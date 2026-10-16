"""Tests for the insurance claims pipeline. Deterministic; no network/keys."""

from datetime import date

import pytest

from claims import (
    AdjusterQueue,
    ClaimLedger,
    extract_document,
    fraud_score,
    gen_fnol,
    process_claim,
    recommend_settlement,
    validate_fnol,
    validate_totals,
)
from claims.intake import FNOL


def make_fnol(**kw):
    base = dict(
        claim_id="CLM-1",
        policyholder="Ava Reyes",
        policy_id="POL-1",
        policy_start=date(2026, 1, 1),
        incident_date=date(2026, 3, 10),
        loss_type="auto",
        description="Rear-end collision at intersection; bumper damage.",
        claimed_amount=5_000.00,
    )
    base.update(kw)
    return FNOL(**base)


def make_doc(total=10000.00, parts=6000.00, labor=4000.00, claimant="Ava Reyes",
             doc_date="2026-03-10"):
    return (
        f"Claimant: {claimant}\n"
        f"Date: {doc_date}\n"
        f"Total: ${total:,.2f}\n"
        f"- Parts: ${parts:,.2f}\n"
        f"- Labor: ${labor:,.2f}\n"
    )


# ---------------------------------------------------------------- intake ---
class TestIntake:
    def test_valid_fnol(self):
        r = validate_fnol(make_fnol(), today=date(2026, 6, 1))
        assert r.valid and r.errors == []

    def test_missing_claim_id(self):
        r = validate_fnol(make_fnol(claim_id=""), today=date(2026, 6, 1))
        assert not r.valid and any("claim_id" in e for e in r.errors)

    def test_bad_loss_type(self):
        r = validate_fnol(make_fnol(loss_type="alien"), today=date(2026, 6, 1))
        assert not r.valid and any("loss_type" in e for e in r.errors)

    def test_future_incident_date(self):
        r = validate_fnol(make_fnol(incident_date=date(2026, 9, 1)),
                          today=date(2026, 6, 1))
        assert not r.valid and any("future" in e for e in r.errors)

    def test_incident_before_policy_start(self):
        r = validate_fnol(make_fnol(incident_date=date(2025, 12, 1)),
                          today=date(2026, 6, 1))
        assert not r.valid and any("predates" in e for e in r.errors)

    def test_negative_amount(self):
        r = validate_fnol(make_fnol(claimed_amount=-50), today=date(2026, 6, 1))
        assert not r.valid and any("positive" in e for e in r.errors)

    def test_short_description(self):
        r = validate_fnol(make_fnol(description="dented"), today=date(2026, 6, 1))
        assert not r.valid

    def test_gen_fnol_deterministic(self):
        a = gen_fnol(5, seed=7)
        b = gen_fnol(5, seed=7)
        assert [x.claim_id for x in a] == [x.claim_id for x in b]
        assert all(validate_fnol(x, today=date(2026, 12, 31)).valid for x in a)


# ------------------------------------------------------------- extraction ---
class TestExtraction:
    def test_fields_with_confidence_and_snippet(self):
        doc = extract_document(make_doc(), "repair_estimate")
        total = doc.get("total_amount")
        assert total is not None and total.value == "10000.00"
        assert 0 < total.confidence <= 1.0 and "$10,000.00" in total.snippet
        assert doc.get("doc_date").value == "2026-03-10"
        assert doc.get("claimant").value == "Ava Reyes"

    def test_line_items_parsed(self):
        doc = extract_document(make_doc(), "repair_estimate")
        assert len(doc.line_items) == 2
        assert doc.line_items[0]["desc"] == "Parts"
        assert doc.line_items[0]["amount"] == 6000.00

    def test_police_report_number(self):
        text = "Officer: J. Doe\nReport #: PR-8821\nDate: 2026-03-10\n"
        doc = extract_document(text, "police_report")
        assert doc.get("report_no").value == "PR-8821"

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            extract_document("hello", "fortune_cookie")

    def test_totals_consistent(self):
        doc = extract_document(make_doc(), "repair_estimate")
        check = validate_totals(doc, 10_000.00)
        assert check.consistent and check.notes == []

    def test_totals_flag_inflation(self):
        doc = extract_document(make_doc(), "repair_estimate")
        check = validate_totals(doc, 45_000.00)
        assert not check.consistent
        assert any("inflation" in n for n in check.notes)

    def test_totals_flag_line_mismatch(self):
        doc = extract_document(make_doc(total=9000.00), "repair_estimate")
        check = validate_totals(doc, 9_000.00)
        assert not check.consistent
        assert any("line-item sum" in n for n in check.notes)


# ------------------------------------------------------------------ fraud ---
class TestFraud:
    def test_clean_claim_low(self):
        a = fraud_score(make_fnol(), history=[], today=date(2026, 6, 1))
        assert a.tier == "low" and a.composite == 0.0
        assert a.fired_signals() == []

    def test_duplicate_fires(self):
        prior = make_fnol(claim_id="CLM-0")
        a = fraud_score(make_fnol(claim_id="CLM-1"), history=[prior],
                        today=date(2026, 6, 1))
        sig = next(s for s in a.signals if s.name == "duplicate_claim")
        assert sig.fired and "CLM-0" in sig.evidence

    def test_duplicate_quiet_on_different_description(self):
        prior = make_fnol(claim_id="CLM-0", description="Hailstorm dented the hood.")
        a = fraud_score(make_fnol(claim_id="CLM-1"), history=[prior],
                        today=date(2026, 6, 1))
        sig = next(s for s in a.signals if s.name == "duplicate_claim")
        assert not sig.fired

    def test_inflated_vs_doc_total(self):
        a = fraud_score(make_fnol(claimed_amount=45_000.00), doc_total=10_000.00,
                        today=date(2026, 6, 1))
        sig = next(s for s in a.signals if s.name == "inflated_amount")
        assert sig.fired and "150%" in sig.evidence

    def test_inflated_vs_estimator_band(self):
        a = fraud_score(make_fnol(loss_type="auto", claimed_amount=60_000.00),
                        today=date(2026, 6, 1))
        sig = next(s for s in a.signals if s.name == "inflated_amount")
        assert sig.fired

    def test_date_anomaly_new_policy(self):
        f = make_fnol(policy_start=date(2026, 6, 1), incident_date=date(2026, 6, 3))
        a = fraud_score(f, today=date(2026, 6, 20))
        sig = next(s for s in a.signals if s.name == "date_anomaly")
        assert sig.fired and "pre-existing" in sig.evidence

    def test_velocity_fires(self):
        h1 = make_fnol(claim_id="H1", incident_date=date(2026, 1, 10))
        h2 = make_fnol(claim_id="H2", incident_date=date(2026, 2, 15))
        f = make_fnol(claim_id="CLM-9", incident_date=date(2026, 3, 1))
        a = fraud_score(f, history=[h1, h2], today=date(2026, 6, 1))
        sig = next(s for s in a.signals if s.name == "claim_velocity")
        assert sig.fired and "3 claims" in sig.evidence

    def test_composite_tier_high(self):
        prior = make_fnol(claim_id="CLM-0", policy_start=date(2026, 6, 1),
                          incident_date=date(2026, 6, 3))
        f = make_fnol(claim_id="CLM-1", claimed_amount=45_000.00,
                      policy_start=date(2026, 6, 1), incident_date=date(2026, 6, 3))
        a = fraud_score(f, history=[prior], doc_total=10_000.00,
                        today=date(2026, 6, 20))
        assert a.composite == 80.0 and a.tier == "high"


# --------------------------------------------------------------- settle ---
class TestSettle:
    def test_high_fraud_never_auto_pays(self):
        prior = make_fnol(claim_id="CLM-0")
        f = make_fnol(claim_id="CLM-1", claimed_amount=45_000.00,
                      policy_start=date(2026, 6, 1), incident_date=date(2026, 6, 3))
        fraud = fraud_score(f, history=[prior], doc_total=10_000.00,
                            today=date(2026, 6, 20))
        rec = recommend_settlement(f, fraud)
        assert rec.action == "investigate"
        assert rec.payable_amount == 0.0 and rec.needs_human

    def test_medium_fraud_investigates(self):
        f = make_fnol(claimed_amount=45_000.00, policy_start=date(2026, 6, 1),
                      incident_date=date(2026, 6, 3))
        fraud = fraud_score(f, doc_total=10_000.00, today=date(2026, 6, 20))
        assert fraud.tier == "medium"
        rec = recommend_settlement(f, fraud)
        assert rec.action == "investigate" and rec.needs_human

    def test_deny_always_needs_human(self):
        f = make_fnol(incident_date=date(2025, 12, 15))
        fraud = fraud_score(f, today=date(2026, 6, 1))
        rec = recommend_settlement(f, fraud)
        assert rec.action == "deny" and rec.needs_human
        assert rec.payable_amount == 0.0

    def test_low_risk_small_claim_auto_pays(self):
        f = make_fnol(claimed_amount=5_000.00)
        fraud = fraud_score(f, today=date(2026, 6, 1))
        doc = extract_document(make_doc(total=5000.00, parts=3000.00, labor=2000.00),
                               "repair_estimate")
        totals = validate_totals(doc, 5_000.00)
        rec = recommend_settlement(f, fraud, totals)
        assert rec.action == "pay" and not rec.needs_human
        assert rec.payable_amount == 5_000.00

    def test_large_clean_claim_needs_human(self):
        f = make_fnol(claimed_amount=30_000.00, loss_type="property",
                      description="Windstorm tore shingles; attic leak damage.")
        fraud = fraud_score(f, today=date(2026, 6, 1))
        assert fraud.tier == "low"
        rec = recommend_settlement(f, fraud)
        assert rec.action == "pay" and rec.needs_human

    def test_pay_with_deduction(self):
        f = make_fnol(claimed_amount=20_000.00)
        fraud = fraud_score(f, doc_total=10_000.00, today=date(2026, 6, 1))
        assert fraud.tier == "low"  # inflated signal alone = 25 < 35
        doc = extract_document(make_doc(), "repair_estimate")
        totals = validate_totals(doc, 20_000.00)
        rec = recommend_settlement(f, fraud, totals)
        assert rec.action == "pay_with_deduction"
        assert rec.payable_amount == 10_000.00


# -------------------------------------------------------------- adjuster ---
class TestAdjuster:
    def _rec(self, action="investigate", payable=0.0):
        f = make_fnol()
        fraud = fraud_score(f, today=date(2026, 6, 1))
        rec = recommend_settlement(f, fraud)
        rec.action, rec.payable_amount = action, payable
        return rec

    def test_submit_and_decide_approve(self):
        q = AdjusterQueue()
        q.submit(self._rec("pay", 5_000.00), "amount meets threshold")
        d = q.decide("CLM-1", "approve", "ADJ-7", "Documentation complete and verified.")
        assert d.decision == "approve" and d.final_payable == 5_000.00
        assert q.get("CLM-1") is None

    def test_deny_zeroes_payable(self):
        q = AdjusterQueue()
        q.submit(self._rec("deny"), "denials always require a human adjuster")
        d = q.decide("CLM-1", "deny", "ADJ-7", "No coverage: incident predates policy.")
        assert d.decision == "deny" and d.final_payable == 0.0

    def test_invalid_decision_raises(self):
        q = AdjusterQueue()
        q.submit(self._rec(), "test")
        with pytest.raises(ValueError):
            q.decide("CLM-1", "maybe", "ADJ-7", "A sufficiently long rationale.")

    def test_short_rationale_raises(self):
        q = AdjusterQueue()
        q.submit(self._rec(), "test")
        with pytest.raises(ValueError):
            q.decide("CLM-1", "approve", "ADJ-7", "ok")

    def test_unknown_claim_raises(self):
        q = AdjusterQueue()
        with pytest.raises(KeyError):
            q.decide("CLM-404", "approve", "ADJ-7", "A sufficiently long rationale.")

    def test_auto_approved_logged(self):
        q = AdjusterQueue()
        f = make_fnol()
        fraud = fraud_score(f, today=date(2026, 6, 1))
        rec = recommend_settlement(f, fraud)
        q.log_auto_approved(rec)
        assert "CLM-1" in q.auto_approved


# ----------------------------------------------------------------- audit ---
class TestLedger:
    def test_chain_verifies(self):
        ledger = ClaimLedger()
        ledger.record("intake", "CLM-1", {"valid": True})
        ledger.record("fraud_scoring", "CLM-1", {"tier": "low"})
        assert ledger.verify_chain()
        assert len(ledger.for_claim("CLM-1")) == 2

    def test_tamper_detected(self):
        ledger = ClaimLedger()
        ledger.record("intake", "CLM-1", {"valid": True})
        ledger.record("fraud_scoring", "CLM-1", {"tier": "low"})
        ledger.entries[0].payload["valid"] = False
        assert not ledger.verify_chain()

    def test_linkage_tamper_detected(self):
        ledger = ClaimLedger()
        ledger.record("intake", "CLM-1", {})
        ledger.record("intake", "CLM-1", {})
        ledger.entries[1].prev_hash = "0" * 64
        assert not ledger.verify_chain()


# -------------------------------------------------------------- pipeline ---
class TestPipeline:
    def test_clean_claim_end_to_end(self):
        fnol = make_fnol(claimed_amount=5_000.00)
        doc = make_doc(total=5000.00, parts=3000.00, labor=2000.00)
        ledger, queue = ClaimLedger(), AdjusterQueue()
        case = process_claim(fnol, [(doc, "repair_estimate")], ledger=ledger,
                             queue=queue, today=date(2026, 6, 1))
        assert case.validation.valid
        assert case.fraud.tier == "low"
        assert case.recommendation.action == "pay"
        assert case.auto_approved and case.queued_reason is None
        assert ledger.verify_chain()
        stages = [e.stage for e in ledger.for_claim("CLM-1")]
        assert stages == ["intake", "extraction", "totals_check", "fraud_scoring",
                          "recommendation", "adjuster_routing"]

    def test_fraudulent_claim_detected_investigated_denied(self):
        prior = make_fnol(claim_id="CLM-0", policy_start=date(2026, 6, 1),
                          incident_date=date(2026, 6, 3))
        fnol = make_fnol(
            claim_id="CLM-1", claimed_amount=45_000.00,
            policy_start=date(2026, 6, 1), incident_date=date(2026, 6, 3),
        )
        doc = make_doc()
        ledger, queue = ClaimLedger(), AdjusterQueue()
        case = process_claim(fnol, [(doc, "repair_estimate")], history=[prior],
                             ledger=ledger, queue=queue, today=date(2026, 6, 20))
        assert case.fraud.tier == "high"
        assert case.recommendation.action == "investigate"
        assert case.queued_reason is not None
        assert queue.get("CLM-1") is not None
        # Human adjuster denies after investigation.
        decision = queue.decide(
            "CLM-1", "deny", "ADJ-7",
            "Duplicate of CLM-0 with inflated amount; fraud confirmed on review.",
        )
        assert decision.final_payable == 0.0
        ledger.record("adjuster_decision", "CLM-1", {
            "decision": decision.decision, "adjuster_id": decision.adjuster_id,
            "final_payable": decision.final_payable,
        })
        ledger.record("payout", "CLM-1", {"paid": 0.0, "note": "denied"})
        assert ledger.verify_chain()
        assert any("CLM-0" in s.evidence
                   for s in case.fraud.fired_signals()
                   if s.name == "duplicate_claim")

    def test_invalid_fnol_short_circuits(self):
        fnol = make_fnol(claimed_amount=-10)
        ledger = ClaimLedger()
        case = process_claim(fnol, [], ledger=ledger, today=date(2026, 6, 1))
        assert not case.validation.valid
        assert case.recommendation is None and case.fraud is None
        assert [e.stage for e in ledger.for_claim("CLM-1")] == ["intake"]
