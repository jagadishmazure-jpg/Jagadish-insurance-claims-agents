"""End-to-end claims pipeline.

process_claim() runs one FNOL through every stage — intake validation,
document extraction, fraud scoring, settlement recommendation, adjuster
routing — recording each stage in the ledger. It returns a ClaimCase
carrying every artifact; human decisions happen afterwards via the
AdjusterQueue (the pipeline never invents a human decision).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

from .adjuster import AdjusterQueue
from .audit import ClaimLedger
from .extract import ExtractedDoc, TotalsCheck, extract_document, validate_totals
from .fraud import FraudAssessment, fraud_score
from .intake import FNOL, ValidationResult, validate_fnol
from .settle import Recommendation, recommend_settlement


@dataclass
class ClaimCase:
    claim_id: str
    fnol: FNOL
    validation: ValidationResult
    docs: List[ExtractedDoc] = field(default_factory=list)
    totals: Optional[TotalsCheck] = None
    fraud: Optional[FraudAssessment] = None
    recommendation: Optional[Recommendation] = None
    queued_reason: Optional[str] = None
    auto_approved: bool = False
    errors: List[str] = field(default_factory=list)


def process_claim(
    fnol: FNOL,
    documents: List[Tuple[str, str]],
    history: Optional[List[FNOL]] = None,
    ledger: Optional[ClaimLedger] = None,
    queue: Optional[AdjusterQueue] = None,
    today: Optional[date] = None,
) -> ClaimCase:
    """Run the full pipeline for one claim.

    documents: list of (doc_text, doc_kind) tuples.
    """
    ledger = ledger or ClaimLedger()
    queue = queue or AdjusterQueue()
    case = ClaimCase(claim_id=fnol.claim_id, fnol=fnol,
                     validation=validate_fnol(fnol, today=today))
    ledger.record("intake", fnol.claim_id, {
        "valid": case.validation.valid, "errors": case.validation.errors,
        "loss_type": fnol.loss_type, "claimed_amount": fnol.claimed_amount,
    })
    if not case.validation.valid:
        case.errors.extend(case.validation.errors)
        return case

    # Extraction.
    for text, kind in documents:
        doc = extract_document(text, kind)
        case.docs.append(doc)
        ledger.record("extraction", fnol.claim_id, {
            "kind": kind,
            "fields": {k: v.value for k, v in doc.fields.items()},
            "line_items": len(doc.line_items), "doc_total": doc.doc_total,
        })

    # Totals cross-check against the first document carrying a total.
    doc_total = next((d.doc_total for d in case.docs if d.doc_total is not None), None)
    if case.docs:
        case.totals = validate_totals(case.docs[0], fnol.claimed_amount)
        ledger.record("totals_check", fnol.claim_id, {
            "consistent": case.totals.consistent, "notes": case.totals.notes,
        })

    # Fraud scoring.
    case.fraud = fraud_score(fnol, history=history, doc_total=doc_total, today=today)
    ledger.record("fraud_scoring", fnol.claim_id, {
        "composite": case.fraud.composite, "tier": case.fraud.tier,
        "fired": [s.name for s in case.fraud.fired_signals()],
    })

    # Settlement recommendation.
    case.recommendation = recommend_settlement(fnol, case.fraud, case.totals)
    ledger.record("recommendation", fnol.claim_id, {
        "action": case.recommendation.action,
        "payable_amount": case.recommendation.payable_amount,
        "needs_human": case.recommendation.needs_human,
        "rationale": case.recommendation.rationale,
    })

    # Adjuster routing.
    rec = case.recommendation
    if rec.needs_human or rec.action in ("investigate", "deny"):
        if rec.action == "deny":
            reason = "denials always require a human adjuster"
        elif rec.action == "investigate":
            reason = f"routed to investigation (fraud tier {case.fraud.tier})"
        else:
            reason = "amount meets human-review threshold"
        queue.submit(rec, reason)
        case.queued_reason = reason
        ledger.record("adjuster_routing", fnol.claim_id,
                      {"queued": True, "reason": reason})
    else:
        queue.log_auto_approved(rec)
        case.auto_approved = True
        ledger.record("adjuster_routing", fnol.claim_id,
                      {"queued": False, "auto_approved": True,
                       "payable_amount": rec.payable_amount})
    return case
