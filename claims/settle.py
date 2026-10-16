"""Settlement recommendations.

Deterministic routing: pay, pay-with-deduction, investigate, or deny.
High fraud tier NEVER auto-pays — it always routes to investigation.
Denials always require a human adjuster (see adjuster.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .extract import TotalsCheck
from .fraud import FraudAssessment
from .intake import FNOL

ACTIONS = ("pay", "pay_with_deduction", "investigate", "deny")

# Claims at or above this amount always need a human, regardless of tier.
HUMAN_REVIEW_THRESHOLD = 25_000.0


@dataclass
class Recommendation:
    claim_id: str
    action: str  # pay | pay_with_deduction | investigate | deny
    payable_amount: float
    rationale: List[str] = field(default_factory=list)
    needs_human: bool = True


def recommend_settlement(
    fnol: FNOL,
    fraud: FraudAssessment,
    totals: Optional[TotalsCheck] = None,
) -> Recommendation:
    """Recommend a settlement action with cited rationale."""
    rationale: List[str] = []
    payable = fnol.claimed_amount

    # Hard rule: high fraud never auto-pays.
    if fraud.tier == "high":
        fired = ", ".join(s.name for s in fraud.fired_signals())
        rationale.append(
            f"fraud tier HIGH (score {fraud.composite}); signals: {fired}"
        )
        rationale.append("policy: high fraud tier always routes to investigation")
        return Recommendation(
            claim_id=fnol.claim_id, action="investigate", payable_amount=0.0,
            rationale=rationale, needs_human=True,
        )

    # Deny: no valid loss (e.g. incident predates policy). Kept narrow and
    # always human-reviewed.
    if fnol.incident_date < fnol.policy_start:
        rationale.append(
            f"incident {fnol.incident_date} predates policy start "
            f"{fnol.policy_start}: no coverage"
        )
        return Recommendation(
            claim_id=fnol.claim_id, action="deny", payable_amount=0.0,
            rationale=rationale, needs_human=True,
        )

    # Totals cross-check: documented costs below claimed -> deduct.
    if totals is not None and not totals.consistent and totals.doc_total is not None:
        payable = totals.doc_total
        rationale.append(
            f"documented total ${totals.doc_total:,.2f} < claimed "
            f"${fnol.claimed_amount:,.2f}; paying documented amount"
        )
        for note in totals.notes:
            rationale.append(f"totals check: {note}")
        action = "pay_with_deduction"
    elif fraud.tier == "medium":
        fired = ", ".join(s.name for s in fraud.fired_signals())
        rationale.append(
            f"fraud tier MEDIUM (score {fraud.composite}); signals: {fired}"
        )
        rationale.append("routing to investigation before payment")
        return Recommendation(
            claim_id=fnol.claim_id, action="investigate", payable_amount=0.0,
            rationale=rationale, needs_human=True,
        )
    else:
        rationale.append(
            f"fraud tier LOW (score {fraud.composite}); documentation consistent"
        )
        action = "pay"

    needs_human = payable >= HUMAN_REVIEW_THRESHOLD
    if needs_human:
        rationale.append(
            f"amount ${payable:,.2f} >= ${HUMAN_REVIEW_THRESHOLD:,.0f} "
            "human-review threshold"
        )
    else:
        rationale.append(
            f"amount ${payable:,.2f} below human-review threshold: auto-approved"
        )
    return Recommendation(
        claim_id=fnol.claim_id, action=action, payable_amount=round(payable, 2),
        rationale=rationale, needs_human=needs_human,
    )
