"""FNOL (first notice of loss) intake and validation.

All policyholders, incidents, and amounts are synthetic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional

LOSS_TYPES = ("auto", "property", "health")


@dataclass
class FNOL:
    """First notice of loss submitted by (or for) a policyholder."""

    claim_id: str
    policyholder: str
    policy_id: str
    policy_start: date
    incident_date: date
    loss_type: str  # auto | property | health
    description: str
    claimed_amount: float


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)


def validate_fnol(fnol: FNOL, today: Optional[date] = None) -> ValidationResult:
    """Check required fields, sane dates, and sane amounts."""
    today = today or date.today()
    errors: List[str] = []
    if not fnol.claim_id:
        errors.append("claim_id is required")
    if not fnol.policyholder:
        errors.append("policyholder is required")
    if not fnol.policy_id:
        errors.append("policy_id is required")
    if fnol.loss_type not in LOSS_TYPES:
        errors.append(f"loss_type must be one of {LOSS_TYPES}, got {fnol.loss_type!r}")
    if not fnol.description or len(fnol.description.strip()) < 10:
        errors.append("description must be at least 10 characters")
    if fnol.claimed_amount <= 0:
        errors.append("claimed_amount must be positive")
    if fnol.claimed_amount > 5_000_000:
        errors.append("claimed_amount exceeds plausible maximum ($5M)")
    if fnol.incident_date > today:
        errors.append("incident_date cannot be in the future")
    if fnol.incident_date < fnol.policy_start:
        errors.append("incident_date predates policy_start")
    if (today - fnol.incident_date).days > 365 * 5:
        errors.append("incident_date is more than 5 years old")
    return ValidationResult(valid=not errors, errors=errors)


# ---------------------------------------------------------------------------
# Synthetic generator
# ---------------------------------------------------------------------------

_FIRST = ["Ava", "Liam", "Maya", "Noah", "Priya", "Omar", "Sofia", "Ethan"]
_LAST = ["Reyes", "Khan", "Okafor", "Nguyen", "Patel", "Garcia", "Kim", "Ali"]

_DESC = {
    "auto": [
        "Rear-end collision at intersection; bumper and trunk damage.",
        "Hailstorm dented hood and roof panels while parked overnight.",
        "Side mirror torn off in parking garage; paint scratched on door.",
    ],
    "property": [
        "Kitchen pipe burst; water damage to cabinets and flooring.",
        "Windstorm tore shingles; attic leak damaged ceiling drywall.",
        "Basement sump pump failed during heavy rain; carpet ruined.",
    ],
    "health": [
        "Emergency room visit for fractured wrist after a fall.",
        "Outpatient surgery for torn meniscus; physical therapy required.",
        "Urgent care visit and X-ray for suspected ankle sprain.",
    ],
}

_AMOUNT_BAND = {
    "auto": (800, 18_000),
    "property": (2_000, 60_000),
    "health": (500, 25_000),
}


def gen_fnol(n: int, seed: int = 42, base_date: Optional[date] = None) -> List[FNOL]:
    """Deterministic synthetic FNOL generator (seeded)."""
    rng = random.Random(seed)
    base = base_date or date(2026, 1, 1)
    out: List[FNOL] = []
    for i in range(n):
        loss_type = rng.choice(list(LOSS_TYPES))
        lo, hi = _AMOUNT_BAND[loss_type]
        policy_start = base - timedelta(days=rng.randint(200, 900))
        incident = base + timedelta(days=rng.randint(0, 280))
        out.append(
            FNOL(
                claim_id=f"CLM-{100000 + i}",
                policyholder=f"{rng.choice(_FIRST)} {rng.choice(_LAST)}",
                policy_id=f"POL-{50000 + rng.randint(0, 9999)}",
                policy_start=policy_start,
                incident_date=incident,
                loss_type=loss_type,
                description=rng.choice(_DESC[loss_type]),
                claimed_amount=round(rng.uniform(lo, hi), 2),
            )
        )
    return out
