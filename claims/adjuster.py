"""Human-in-the-loop: the adjuster queue.

Claims needing a human decision land here: high/medium fraud tiers,
amounts over the review threshold, and ALL denials. Low-risk claims
below the threshold are auto-approved and logged as such — the queue
records both paths so the audit trail is complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .settle import Recommendation

DECISIONS = ("approve", "approve_modified", "deny", "escalate")


@dataclass
class QueueItem:
    claim_id: str
    action: str  # recommended action
    payable_amount: float
    rationale: List[str]
    reason_queued: str
    status: str = "pending"  # pending | decided


@dataclass
class AdjusterDecision:
    claim_id: str
    decision: str
    adjuster_id: str
    rationale: str
    decided_at: str
    final_payable: float


class AdjusterQueue:
    """Holds claims awaiting human decision; records decisions taken."""

    def __init__(self) -> None:
        self.pending: Dict[str, QueueItem] = {}
        self.decisions: Dict[str, AdjusterDecision] = {}
        self.auto_approved: List[str] = []

    def submit(self, rec: Recommendation, reason: str) -> QueueItem:
        item = QueueItem(
            claim_id=rec.claim_id, action=rec.action,
            payable_amount=rec.payable_amount, rationale=list(rec.rationale),
            reason_queued=reason,
        )
        self.pending[rec.claim_id] = item
        return item

    def log_auto_approved(self, rec: Recommendation) -> None:
        """Record a low-risk auto-approval (no human involved)."""
        self.auto_approved.append(rec.claim_id)

    def decide(
        self,
        claim_id: str,
        decision: str,
        adjuster_id: str,
        rationale: str,
        final_payable: Optional[float] = None,
    ) -> AdjusterDecision:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        item = self.pending.get(claim_id)
        if item is None:
            raise KeyError(f"claim {claim_id} is not in the adjuster queue")
        if not rationale or len(rationale.strip()) < 10:
            raise ValueError("adjuster rationale must be at least 10 characters")
        amount = (
            final_payable
            if final_payable is not None
            else (item.payable_amount if decision in ("approve", "approve_modified") else 0.0)
        )
        if decision == "deny":
            amount = 0.0
        record = AdjusterDecision(
            claim_id=claim_id, decision=decision, adjuster_id=adjuster_id,
            rationale=rationale.strip(),
            decided_at=datetime.now(timezone.utc).isoformat(),
            final_payable=round(amount, 2),
        )
        self.decisions[claim_id] = record
        item.status = "decided"
        del self.pending[claim_id]
        return record

    def get(self, claim_id: str) -> Optional[QueueItem]:
        return self.pending.get(claim_id)

    def outstanding(self) -> List[QueueItem]:
        return list(self.pending.values())
