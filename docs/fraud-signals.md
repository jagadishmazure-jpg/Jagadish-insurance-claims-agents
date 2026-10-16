# Fraud signals catalog

Four deterministic signals feed the composite fraud score (0–100).
Weights sum to 100. Tiers: low (<35), medium (35–69), high (≥70).
High tier **never auto-pays** — it always routes to investigation.

## 1. `duplicate_claim` — weight 35

**Detects:** double-dip filing — the same incident submitted as two
claims under different claim IDs.

**Evidence cited:** the matching prior claim ID, plus the three matched
attributes (policyholder, incident date, normalized description).

**Fires when:** a history record shares policyholder + incident date +
identical normalized description with a different `claim_id`.

**Stays quiet when:** descriptions differ, dates differ, or it is the
claimant's first filing.

## 2. `inflated_amount` — weight 25

**Detects:** padding — claimed amounts far above documented or plausible
costs.

**Evidence cited:** claimed vs documented totals ("450% of documented"),
or the estimator band for the loss type that was exceeded.

**Fires when:** claimed > 150% of the extracted document total, or
claimed > 150% of the estimator band ceiling for the loss type.

**Stays quiet when:** claimed amount sits within the documented costs
and the estimator band.

Estimator bands (synthetic, per loss type): auto $500–$25k, property
$1k–$80k, health $300–$40k.

## 3. `date_anomaly` — weight 20

**Detects:** timing games — pre-existing damage claimed on a brand-new
policy, or suspiciously late reporting.

**Evidence cited:** days between policy start and incident, or days
between incident and filing.

**Fires when:** incident falls within 7 days after policy start, or the
incident is reported more than 180 days late.

**Stays quiet when:** incident timing is ordinary relative to the policy
and the filing date.

## 4. `claim_velocity` — weight 20

**Detects:** serial claiming — unusually frequent filings by one
policyholder, a classic soft-fraud pattern.

**Evidence cited:** the count of claims by the policyholder inside the
180-day window.

**Fires when:** 3+ claims (2 prior + current) by the same policyholder
within 180 days.

**Stays quiet when:** the claimant's filing history is sparse.

## Tuning guidance

Weights and thresholds are deliberately exposed as module constants
(`_W_*` in `claims/fraud.py`, bands in `ESTIMATOR_BAND`). In
production, calibrate per line of business against labeled outcomes;
the signal *structure* (fired flag + cited evidence) stays the same so
adjusters and auditors always see the why, not just a number.
