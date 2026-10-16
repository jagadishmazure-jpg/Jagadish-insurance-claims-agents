"""Hash-chained audit ledger for the claims pipeline.

Every stage — intake, extraction, fraud scoring, recommendation,
adjuster decision, payout — is appended as a hash-chained entry, so
tampering with any historical record breaks the chain. Same design
family as the ledgers in the sibling portfolio repos.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LedgerEntry:
    seq: int
    stage: str
    claim_id: str
    payload: Dict[str, Any]
    timestamp: str
    prev_hash: str
    entry_hash: str = ""

    def compute_hash(self) -> str:
        body = json.dumps(
            {
                "seq": self.seq,
                "stage": self.stage,
                "claim_id": self.claim_id,
                "payload": self.payload,
                "timestamp": self.timestamp,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class ClaimLedger:
    """Append-only, hash-chained record of every pipeline stage."""

    GENESIS = "0" * 64

    def __init__(self) -> None:
        self.entries: List[LedgerEntry] = []

    def record(self, stage: str, claim_id: str,
               payload: Dict[str, Any]) -> LedgerEntry:
        prev = self.entries[-1].entry_hash if self.entries else self.GENESIS
        entry = LedgerEntry(
            seq=len(self.entries),
            stage=stage,
            claim_id=claim_id,
            payload=payload,
            timestamp=_utcnow(),
            prev_hash=prev,
        )
        entry.entry_hash = entry.compute_hash()
        self.entries.append(entry)
        return entry

    def verify_chain(self) -> bool:
        """Recompute every hash and linkage; False on any tampering."""
        prev = self.GENESIS
        for i, e in enumerate(self.entries):
            if e.seq != i or e.prev_hash != prev:
                return False
            if e.entry_hash != e.compute_hash():
                return False
            prev = e.entry_hash
        return True

    def for_claim(self, claim_id: str) -> List[LedgerEntry]:
        return [e for e in self.entries if e.claim_id == claim_id]

    def export_jsonl(self) -> str:
        lines = []
        for e in self.entries:
            lines.append(
                json.dumps(
                    {
                        "seq": e.seq, "stage": e.stage, "claim_id": e.claim_id,
                        "payload": e.payload, "timestamp": e.timestamp,
                        "prev_hash": e.prev_hash, "entry_hash": e.entry_hash,
                    },
                    sort_keys=True, default=str,
                )
            )
        return "\n".join(lines)
