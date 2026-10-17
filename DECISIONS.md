# DECISIONS.md — Clarifications to SPEC.md

These were agreed during Phase 0 kickoff. Where this file and SPEC.md differ, this file wins.

## Treasury logic
1. **Idle cash is reported separately from fee savings.** Excess-balance opportunity cost (A8) is foregone investment income, not a fee. The headline shows "annual fee savings" and "investable excess balances / income opportunity" as separate figures.
2. **One-time recoveries vs. run-rate savings.** Duplicates (A4), math errors (A6), and tier misbilling (A5) are refunds of past charges, so they are **not** annualized. Pricing findings (A1 price increase, A2 above benchmark, A3 unused services) are annualized as monthly impact × 12, labeled "annualized." The savings summary has two columns: `one_time_recovery` and `annual_run_rate`.
3. **Above-benchmark savings are measured to p75** (conservative). The gap to the median is shown for information only.
4. **No double counting between A1 and A2.** The price-increase rollback is counted first; A2 counts only the part of the rolled-back price that is still above p75.
5. **Compensation is calculated at the bank-relationship level** (all accounts at a bank combined), since ZBA sub-accounts carry zero balances.
6. **Unused services use an explicit "related activity" map**, not "same AFP family":
   - Positive Pay Maintenance (200010) → Checks Paid (150120)
   - ACH Module Maintenance (250000) → ACH originated/received items (2501xx, 2502xx)
   - Intraday Reporting Module (400200) → BAI Prior-Day Detail Items (400011) — information reporting with no reports pulled
   - Wire Module Maintenance (350000) → wire items (3501xx–3504xx)
7. **Terminology on the dashboard:** "non-offsettable fees" (not ECR-eligible) and "net service charge (cash)" (what the bank actually debits). Avoid using "hard charges" for both.
8. **FDIC assessment (000230)** is a non-offsettable hard charge at **Riverbend**, billed per $1,000 of average ledger balance. At the other banks it is ECR-eligible.
9. **ECR carryover is false for all three banks.**
10. **Price-change rule:** flag any month-over-month unit price increase > 5% for the same bank + AFP code + tier. (The "all customers" condition is dropped; we only have one customer.)

## Data and structure
11. **Tier definitions** live in generated bank price schedules: `data/raw/price_schedule_<bank_slug>.csv`. Tiers are based on monthly volume per bank.
12. **Mapping accuracy** is computed by the pipeline (which may read the answer key) and written to `data/processed/mapping_accuracy.json`. The app reads only that file, never the answer key.
13. **Dashboard has the 7 pages listed in SPEC.md §5** (Fee Drivers may be merged into Executive Summary if it is thin).
14. **15–25 services per bank** (not 25–35), so banks look different (e.g., lockbox only at Summit).
15. **Memo validation:** all dollar figures are pre-formatted in the JSON sent to Claude, which is told to quote them verbatim. The validator checks every `$` figure in the memo against those strings.

16. **Mapping truth file:** `data/raw/_mapping_key.csv` holds the true AFP code for every bank description. Like the answer key, only tests and the accuracy step may read it.
17. **A7 is about analyzed fees vs. earnings allowance**, not total net service charge. Riverbend's FDIC fee is a hard charge every month, so its net service charge is never zero; the ECR-cut finding is the month-by-month gap between analyzed fees and earnings allowance.

## Out of scope unless asked
- Parquet (CSV only), Power BI (Phase 9), SPEC.md §8 stretch goals.
