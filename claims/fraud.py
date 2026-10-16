"""Fraud signal scoring for insurance claims.

Four deterministic signals, each citing its evidence. The composite
score (0-100) maps to a risk tier: low (<35), medium (35-69), high (>=70).

All data is synthetic. See docs/fraud-signals.md for the signal catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional

from .intake import FNOL


@dataclass
class FraudSignal:
    name: str
    score: float  # 0-100 contribution weight of this signal
    fired: bool
    evidence: str


@dataclass
class FraudAssessment:
    claim_id: str
    signals: List[FraudSignal] = field(default_factory=list)
    composite: float = 0.0
    tier: str = "low"  # low | medium | high

    def fired_signals(self) -> List[FraudSignal]:
        return [s for s in self.signals if s.fired]


# Estimator bands: plausible documented-cost ranges per loss type.
# Claims far above the band for their loss type look inflated.
ESTIMATOR_BAND = {
    "auto": (500, 25_000),
    "property": (1_000, 80_000),
    "health": (300, 40_000),
}

# Weights sum to 100.
_W_DUPLICATE = 35.0
_W_INFLATED = 25.0
_W_DATE = 20.0
_W_VELOCITY = 20.0


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def fraud_score(
    fnol: FNOL,
    history: Optional[List[FNOL]] = None,
    doc_total: Optional[float] = None,
    today: Optional[date] = None,
) -> FraudAssessment:
    """Score fraud risk for a claim against its history and documents."""
    history = history or []
    today = today or date.today()
    signals: List[FraudSignal] = []

    # 1. Duplicate claim: same policyholder + same incident date + near-identical
    #    description, but a different claim_id -> possible double-dip.
    dup = None
    for h in history:
        if h.claim_id == fnol.claim_id:
            continue
        if (
            h.policyholder == fnol.policyholder
            and h.incident_date == fnol.incident_date
            and _norm(h.description) == _norm(fnol.description)
        ):
            dup = h
            break
    signals.append(
        FraudSignal(
            name="duplicate_claim",
            score=_W_DUPLICATE if dup else 0.0,
            fired=dup is not None,
            evidence=(
                f"matches claim {dup.claim_id} (same policyholder, incident date "
                f"{dup.incident_date}, identical description)"
                if dup
                else "no matching prior claim"
            ),
        )
    )

    # 2. Inflated amount: documented total far below claimed, or claimed far
    #    above the estimator band for the loss type.
    inflated_evidence = "within estimator band"
    inflated = False
    lo, hi = ESTIMATOR_BAND.get(fnol.loss_type, (0, float("inf")))
    if doc_total is not None and fnol.claimed_amount > doc_total * 1.5:
        inflated = True
        inflated_evidence = (
            f"claimed ${fnol.claimed_amount:,.2f} is >150% of documented "
            f"${doc_total:,.2f}"
        )
    elif fnol.claimed_amount > hi * 1.5:
        inflated = True
        inflated_evidence = (
            f"claimed ${fnol.claimed_amount:,.2f} exceeds estimator band "
            f"for {fnol.loss_type} (${lo:,.0f}-${hi:,.0f}) by >50%"
        )
    signals.append(
        FraudSignal(
            name="inflated_amount",
            score=_W_INFLATED if inflated else 0.0,
            fired=inflated,
            evidence=inflated_evidence,
        )
    )

    # 3. Date anomaly: incident very close to policy start (possible
    #    pre-existing damage claimed on a new policy) or filed suspiciously
    #    long after the incident.
    date_anomaly = False
    date_evidence = "incident timing normal"
    days_after_start = (fnol.incident_date - fnol.policy_start).days
    if 0 <= days_after_start <= 7:
        date_anomaly = True
        date_evidence = (
            f"incident {days_after_start} days after policy start "
            f"({fnol.policy_start}) — possible pre-existing loss"
        )
    elif (today - fnol.incident_date).days > 180:
        date_anomaly = True
        date_evidence = (
            f"incident reported {(today - fnol.incident_date).days} days late"
        )
    signals.append(
        FraudSignal(
            name="date_anomaly",
            score=_W_DATE if date_anomaly else 0.0,
            fired=date_anomaly,
            evidence=date_evidence,
        )
    )

    # 4. Claim velocity: 3+ claims by the same policyholder in 180 days.
    recent = [
        h
        for h in history
        if h.policyholder == fnol.policyholder
        and h.claim_id != fnol.claim_id
        and 0 <= (fnol.incident_date - h.incident_date).days <= 180
    ]
    velocity = len(recent) >= 2  # 2 prior + this one = 3 in window
    signals.append(
        FraudSignal(
            name="claim_velocity",
            score=_W_VELOCITY if velocity else 0.0,
            fired=velocity,
            evidence=(
                f"{len(recent) + 1} claims by {fnol.policyholder} within 180 days"
                if velocity
                else f"{len(recent) + 1} claim(s) in 180-day window (normal)"
            ),
        )
    )

    composite = round(sum(s.score for s in signals), 1)
    tier = "high" if composite >= 70 else "medium" if composite >= 35 else "low"
    return FraudAssessment(
        claim_id=fnol.claim_id, signals=signals, composite=composite, tier=tier
    )
