# Implementation guide: from offline build to Azure

> Status: **planned**. Nothing here has been deployed. The offline build in this
> repository is what exists today; this guide is how it would be taken to a
> real Azure environment.

## Azure service mapping

| Offline component | Azure service (planned) | Notes |
|---|---|---|
| FNOL intake | Azure API Management + Azure Functions | Entra ID for callers |
| `extract_document` | Azure AI Document Intelligence (custom or prebuilt model) | Keep confidence and snippets |
| Claims history for fraud signals | Azure SQL Database or Fabric Lakehouse | Read-only managed identity |
| Adjuster queue | Logic Apps approval or an internal web app | Adjusters sign in through Entra ID |
| `ClaimLedger` | Azure immutable blob storage or Azure SQL ledger tables | Tamper evidence |

## Steps

1. Put FNOL intake behind API Management and run `process_claim` in Azure Functions or Container Apps.
2. Swap the regex extractor for Document Intelligence behind the same `extract_document` interface.
3. Read claim history from the claims warehouse for duplicate and velocity signals.
4. Route queued claims to a Logic Apps approval or adjuster app; payouts only after a recorded decision.
5. Write the ledger to immutable storage and verify the chain on a schedule.
6. Calibrate weights and tiers on labelled historical claims before turning on auto-pay.

## Guardrails to keep when going live

- High fraud risk never auto-pays; it always goes to investigation.
- Every denial needs a human adjuster and a written rationale.
- Claims of $25,000 or more need a human even when the risk is low.
- Each fraud signal cites its evidence (the matching claim ID, the ratio, the day count).
- Every stage, decision and payout is on a hash-chained ledger that `verify_chain()` checks.

## What to measure before calling it production-ready

- Straight-through-processing rate and its error rate on audited samples.
- Precision and recall of the HIGH tier on labelled historical fraud.
- Time from FNOL to payout for clean claims.
