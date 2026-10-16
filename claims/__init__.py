"""claims: end-to-end insurance claims processing.

FNOL intake -> document extraction -> fraud scoring -> settlement
recommendation -> adjuster (human-in-the-loop) approval -> payout,
with every stage recorded in a hash-chained audit ledger.

All data is synthetic. Vendor-neutral: the pattern, not any product.
"""

from .adjuster import AdjusterQueue
from .audit import ClaimLedger
from .extract import ExtractedDoc, extract_document, validate_totals
from .fraud import FraudSignal, fraud_score
from .intake import FNOL, gen_fnol, validate_fnol
from .pipeline import ClaimCase, process_claim
from .settle import Recommendation, recommend_settlement

__all__ = [
    "FNOL",
    "gen_fnol",
    "validate_fnol",
    "ExtractedDoc",
    "extract_document",
    "validate_totals",
    "FraudSignal",
    "fraud_score",
    "Recommendation",
    "recommend_settlement",
    "AdjusterQueue",
    "ClaimLedger",
    "ClaimCase",
    "process_claim",
]
