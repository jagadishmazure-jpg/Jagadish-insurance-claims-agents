"""Deterministic document field extraction for claim documents.

Handles three synthetic document kinds: repair estimates, police
reports, and medical bills. Extraction is rule-based (regex over the
document text) so it is fully deterministic and testable; each field
carries a confidence score and the source snippet it came from.

The production path swaps the regex engine for a vision/LLM extractor
behind the same interface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

DOC_KINDS = ("repair_estimate", "police_report", "medical_bill")


@dataclass
class ExtractedField:
    name: str
    value: str
    confidence: float  # 0.0 - 1.0
    snippet: str


@dataclass
class ExtractedDoc:
    kind: str
    fields: Dict[str, ExtractedField] = field(default_factory=dict)
    line_items: List[Dict] = field(default_factory=list)  # {"desc": str, "amount": float}
    doc_total: Optional[float] = None

    def get(self, name: str) -> Optional[ExtractedField]:
        return self.fields.get(name)


_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_LINE_ITEM = re.compile(r"^[-*]\s*(.+?):\s*\$\s*([\d,]+(?:\.\d{2})?)\s*$", re.MULTILINE)


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def extract_document(text: str, kind: str) -> ExtractedDoc:
    """Extract structured fields from a synthetic claim document."""
    if kind not in DOC_KINDS:
        raise ValueError(f"unknown doc kind {kind!r}; expected one of {DOC_KINDS}")
    doc = ExtractedDoc(kind=kind)

    # Amounts: first money mention is the headline total; keep all for cross-check.
    monies = [(m.group(0), _money(m.group(1))) for m in _MONEY.finditer(text)]
    if monies:
        snippet, value = monies[0]
        doc.fields["total_amount"] = ExtractedField(
            name="total_amount", value=f"{value:.2f}", confidence=0.95, snippet=snippet
        )
        doc.doc_total = value

    # Dates: first ISO date is treated as the document/incident date.
    dates = [m.group(1) for m in _DATE.finditer(text)]
    if dates:
        doc.fields["doc_date"] = ExtractedField(
            name="doc_date", value=dates[0], confidence=0.95, snippet=dates[0]
        )

    # Parties: "Claimant: <name>" / "Provider: <name>" / "Officer: <name>" lines.
    for label in ("Claimant", "Provider", "Officer", "Insured"):
        m = re.search(rf"^{label}:\s*(.+?)\s*$", text, re.MULTILINE)
        if m:
            name = m.group(1).strip()
            doc.fields[label.lower()] = ExtractedField(
                name=label.lower(), value=name, confidence=0.9, snippet=m.group(0)
            )

    # Line items: "- <desc>: $X" bullets (repair estimates, medical bills).
    for m in _LINE_ITEM.finditer(text):
        desc, amount = m.group(1).strip(), _money(m.group(2))
        doc.line_items.append({"desc": desc, "amount": amount, "snippet": m.group(0)})

    # Kind-specific markers.
    if kind == "police_report":
        m = re.search(r"Report\s*#:\s*(\S+)", text)
        if m:
            doc.fields["report_no"] = ExtractedField(
                name="report_no", value=m.group(1), confidence=0.95, snippet=m.group(0)
            )
    if kind == "medical_bill":
        m = re.search(r"CPT:\s*([\d,\s]+)", text)
        if m:
            doc.fields["cpt_codes"] = ExtractedField(
                name="cpt_codes", value=m.group(1).strip(), confidence=0.85,
                snippet=m.group(0),
            )
    return doc


@dataclass
class TotalsCheck:
    consistent: bool
    line_sum: float
    doc_total: Optional[float]
    claimed_amount: float
    notes: List[str] = field(default_factory=list)


def validate_totals(doc: ExtractedDoc, claimed_amount: float,
                    tolerance: float = 0.01) -> TotalsCheck:
    """Cross-check line-item sum vs document total vs claimed amount.

    Flags inflated claims where the claimed amount materially exceeds the
    documented costs — a classic fraud signal input.
    """
    notes: List[str] = []
    line_sum = round(sum(i["amount"] for i in doc.line_items), 2)
    consistent = True
    if doc.line_items and doc.doc_total is not None:
        if abs(line_sum - doc.doc_total) > tolerance:
            consistent = False
            notes.append(
                f"line-item sum ${line_sum:,.2f} != doc total ${doc.doc_total:,.2f}"
            )
    if doc.doc_total is not None and claimed_amount > doc.doc_total * 1.25:
        consistent = False
        notes.append(
            f"claimed ${claimed_amount:,.2f} exceeds documented "
            f"${doc.doc_total:,.2f} by >25% (possible inflation)"
        )
    if not doc.line_items and doc.doc_total is None:
        notes.append("no amounts found in document")
    return TotalsCheck(
        consistent=consistent, line_sum=line_sum, doc_total=doc.doc_total,
        claimed_amount=claimed_amount, notes=notes,
    )


def parse_iso(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()
