"""Run three synthetic claims and a seeded batch through FNOL -> payout (offline, deterministic)."""
from collections import Counter
from datetime import date, timedelta

from claims import AdjusterQueue, ClaimLedger, gen_fnol, process_claim
from claims.intake import FNOL

TODAY = date(2026, 6, 20)


def estimate(total, claimant="Ava Reyes"):
    parts = round(total * 0.6, 2)
    return (f"Claimant: {claimant}\nDate: 2026-03-10\nTotal: ${total:,.2f}\n"
            f"- Parts: ${parts:,.2f}\n- Labor: ${total - parts:,.2f}\n")


def fnol(claim_id, amount, start=date(2026, 1, 1), incident=date(2026, 3, 10)):
    return FNOL(claim_id=claim_id, policyholder="Ava Reyes", policy_id="POL-1",
                policy_start=start, incident_date=incident, loss_type="auto",
                description="Rear-end collision at intersection; bumper damage.",
                claimed_amount=amount)


ledger, queue = ClaimLedger(), AdjusterQueue()
prior = fnol("CLM-0", 5_000.00, start=date(2026, 6, 1), incident=date(2026, 6, 3))
scenarios = [
    ("clean, small", fnol("CLM-1", 5_000.00), [(estimate(5_000.00), "repair_estimate")], []),
    ("clean, large", fnol("CLM-2", 30_000.00), [(estimate(30_000.00), "repair_estimate")], []),
    ("duplicate + inflated + new policy",
     fnol("CLM-3", 45_000.00, start=date(2026, 6, 1), incident=date(2026, 6, 3)),
     [(estimate(10_000.00), "repair_estimate")], [prior]),
]
for label, f, docs, history in scenarios:
    case = process_claim(f, docs, history=history, ledger=ledger, queue=queue, today=TODAY)
    fired = [s.name for s in case.fraud.fired_signals()]
    print(f"{case.claim_id} ({label}): fraud {case.fraud.composite:.0f} {case.fraud.tier.upper()}"
          f" fired={fired or '-'} -> {case.recommendation.action}"
          f" payable=${case.recommendation.payable_amount:,.2f}"
          f" {'queued: ' + case.queued_reason if case.queued_reason else 'auto-approved'}")

decision = queue.decide("CLM-3", "deny", "ADJ-7", "Duplicate of CLM-0 with an inflated amount.")
print(f"Adjuster ADJ-7 on CLM-3: {decision.decision}, final payable ${decision.final_payable:,.2f}")
print(f"Ledger entries: {len(ledger.entries)}, chain verifies: {ledger.verify_chain()}")

batch = gen_fnol(40, seed=42)
actions, tiers = Counter(), Counter()
for f in batch:
    c = process_claim(f, [(estimate(f.claimed_amount, f.policyholder), "repair_estimate")],
                      history=batch, today=f.incident_date + timedelta(days=5))  # reported 5 days after the loss
    actions[c.recommendation.action if c.recommendation else "invalid"] += 1
    tiers[c.fraud.tier if c.fraud else "n/a"] += 1
print(f"\nSeeded batch of {len(batch)} claims, matching estimates, each reported 5 days after the loss:")
print("  actions:", dict(sorted(actions.items())))
print("  fraud tiers:", dict(sorted(tiers.items())))
