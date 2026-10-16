# Insurance claims agents

End-to-end insurance claims processing: FNOL intake → document
extraction → fraud scoring → settlement recommendation → adjuster
(human-in-the-loop) approval → payout, with every stage recorded in a
hash-chained audit ledger.

## The problem

Claims triage is slow and leaky. Human adjusters drown in first notices
of loss while two failure modes compound: legitimate claims wait weeks
for simple payouts, and fraud — duplicate filings, inflated repair
bills, staged incidents — slips through manual review. Both cost money;
only one makes headlines.

## Architecture

```
FNOL ──▶ intake.validate ──▶ extract documents ──▶ fraud.score ──▶ settle.recommend ──▶ adjuster queue ──▶ payout
              │                    │                    │                  │                    │
              └────────────────────┴────────────────────┴──────────────────┴────────────────────┘
                                                       ClaimLedger (hash-chained)
```

- `claims/intake.py` — `FNOL` dataclass, field/date/amount validation,
  seeded synthetic generator.
- `claims/extract.py` — deterministic field extraction (amounts, dates,
  parties, line items) with confidence scores and source snippets;
  totals cross-check flags inflated claims.
- `claims/fraud.py` — four evidence-cited signals (duplicate claim,
  inflated amount, date anomaly, claim velocity) → composite 0–100 →
  low/medium/high tier.
- `claims/settle.py` — `pay` / `pay_with_deduction` / `investigate` /
  `deny` with cited rationale. **High fraud tier never auto-pays.**
  Denials always require a human.
- `claims/adjuster.py` — `AdjusterQueue`: human decisions with recorded
  rationale; low-risk claims auto-approved and logged as such.
- `claims/audit.py` — `ClaimLedger`: SHA-256 hash-chained record of
  every stage; tampering breaks the chain.
- `claims/pipeline.py` — `process_claim()`: the full run, returning a
  `ClaimCase` with every artifact.

## Example claim walkthrough

A policyholder files a $45,000 auto claim three days after the policy
starts, attaching a $10,000 repair estimate — and an identical claim
already exists under a different claim ID.

1. **Intake** — FNOL validates (fields, dates, amounts sane).
2. **Extraction** — repair estimate yields $10,000 total; line items sum
   correctly, but the claimed $45,000 exceeds documented costs by >25%.
3. **Fraud scoring** — three signals fire: `duplicate_claim` (matches
   CLM-0, identical description), `inflated_amount` (450% of documented),
   `date_anomaly` (incident 2 days after policy start). Composite 80 →
   **HIGH**.
4. **Recommendation** — `investigate`, payable $0. Policy: high fraud
   never auto-pays.
5. **Adjuster** — queued for human review; the adjuster denies with
   rationale citing the duplicate and inflation. Payout $0.
6. **Ledger** — all six stages plus the decision and payout are
   hash-chained and verifiable.

A clean $5,000 claim with matching documentation scores LOW and
auto-pays below the $25,000 human-review threshold — straight-through
processing where it is safe.

## Fraud signals

See [docs/fraud-signals.md](docs/fraud-signals.md) for the signal
catalog: what each detects, its evidence, and its weight.

## Production path

- Swap the regex extractor for a vision/LLM document model behind the
  same `extract_document` interface; keep confidence scores and snippets
  — adjusters need to see *why*.
- Feed real policy/claims history into `fraud_score`; calibrate bands
  and weights per line of business.
- Put a real adjuster UI behind `AdjusterQueue.decide`; the queue,
  rationale requirement, and ledger recording stay unchanged.
- Replace the in-process ledger store with append-only storage
  (WORM bucket / ledger database); the hash chain design is unchanged.

## Synthetic data notice

Every policyholder, incident, amount, and document in this repo is
synthetic and generated deterministically from seeds. This project is
independent and unaffiliated with any insurer.

## Tests

```bash
pytest -q
```

41 tests, deterministic, no network access or API keys required.

## CI

`.github/workflows/ci.yml` is prepared locally; add it via the GitHub
web UI (the API cannot push workflow files).
