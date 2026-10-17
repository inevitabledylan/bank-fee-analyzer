# SPEC.md — Bank Fee Analyzer Requirements

## 1. Scenario (all fictional)
**Company:** Heartland Fabrication Co., a $600M-revenue U.S. manufacturer headquartered in the Midwest.
**Period:** 12 months, October 2025 – September 2026.

**Banks:**
| Bank | Role | Accounts | ECR (start) | Reserve haircut | Day basis | Notes |
|---|---|---|---|---|---|---|
| Prairie National Bank | Primary operating bank | 12 (1 ZBA master + 9 ZBA subs + payroll + concentration) | 2.75% | 0% | 365 | Highest volume. Applies an unannounced price increase in month 10 (July 2026). |
| Summit Commercial Bank | Secondary: lockbox & receivables | 8 | 2.50% | 10% | 360 | Uses non-standard fee descriptions, many with no AFP code. Holds large excess balances. |
| Riverbend Trust Company | Credit facility bank, payroll backup | 5 | 2.25% → 1.75% from April 2026 | 0% | 365 | ECR cut causes fees to flip to hard charges. Several dormant services still billed. |

Balances: total average collected balances across all banks roughly $35–60M, varying by month with mild seasonality. Float 2–6% of ledger.

## 2. Synthetic data generation (`src/generate_data.py`)

### 2.1 Output files
- `data/raw/statements_<bank_slug>.csv` — one row per account × month × service line
- `data/raw/balances.csv` — one row per account × month
- `data/raw/bank_terms.csv` — ECR by month, reserve %, day basis, carryover flag per bank
- `reference/benchmark_prices.csv` — illustrative benchmark per AFP code: `afp_code, unit_basis, p25, median, p75`

### 2.2 Statement line schema
| Column | Type | Notes |
|---|---|---|
| bank | str | |
| account_id | str | e.g., `PNB-0001` |
| account_name | str | e.g., "Operating – ZBA Master" |
| statement_month | date | first of month |
| bank_service_code | str | bank's internal code |
| bank_description | str | bank's wording (varies by bank — see 2.4) |
| afp_code | str or blank | populated for Prairie & Riverbend; often blank for Summit |
| volume | float | |
| unit_price | float | |
| total_charge | float | volume × unit_price, except where an anomaly breaks this on purpose |
| ecr_eligible | bool | |
| price_tier | str | for tiered services |

### 2.3 Services
Use ~25–35 services per bank drawn from `reference/afp_codes_sample.csv`. Volumes should be realistic for a $600M manufacturer (e.g., thousands of ACH credits/month at the primary bank, dozens of wires, a few thousand checks paid). Prices should fall in plausible ranges around the illustrative benchmark median, with each bank having its own price list.

### 2.4 Description style per bank (to make mapping non-trivial)
- **Prairie:** clean descriptions with AFP codes ("ACH ORIGINATED CREDIT ITEM").
- **Riverbend:** abbreviated descriptions with AFP codes ("ACH ORIG CR ITM").
- **Summit:** marketing-style product names, ~60% with blank AFP codes ("PayStream Outbound Credit Txn", "SmartVault Item Capture", "InfoLink Prior-Day Rpt Acct").

### 2.5 Planted anomalies (each must be detectable and tested)
| # | Anomaly | Where | Detail |
|---|---|---|---|
| A1 | Unannounced price increase | Prairie, from July 2026 | 6 services increase 12–20% |
| A2 | Above-benchmark pricing | All banks, mostly Summit | ~8 services priced above benchmark p75 (e.g., outgoing domestic wire 2× median) |
| A3 | Billing for unused services | Riverbend (mostly) | Monthly maintenance for ACH, positive pay, and an information-reporting module on accounts with zero related transaction volume for 6+ months |
| A4 | Duplicate charges | Prairie, 2 months | Same account, month, code billed twice |
| A5 | Tier pricing misapplied | Prairie | Volume qualifies for a lower tier price but billed at the tier-1 price for 3 months |
| A6 | Math error | Summit, 1–2 lines | total_charge ≠ volume × unit_price |
| A7 | ECR cut → hard charges | Riverbend, April 2026 | Earnings allowance no longer covers fees; net service charges appear |
| A8 | Excess idle balances | Summit | Collected balances well above the balance required to offset fees |

Keep an internal `data/raw/_answer_key.csv` listing every planted anomaly (for tests only — the app must not read it).

## 3. AFP code mapping — AI feature #1 (`src/map_codes.py`)
For every unique (bank, bank_description) where afp_code is blank:
1. **Fuzzy match** against `reference/afp_codes_sample.csv` descriptions using rapidfuzz (token-set ratio). Score ≥ 90 → accept.
2. **LLM fallback** for the rest: send the description, the bank's name, and the candidate code list to Claude. Ask for JSON: `{"afp_code": ..., "afp_description": ..., "confidence": 0–1, "reasoning": "<1 sentence>"}`. Use a low temperature.
3. Confidence < 0.7 → `needs_review = True`.
4. Cache every LLM response in `data/cache/afp_mappings.json` keyed by bank + description.
5. Output `data/processed/code_mapping.csv` with method (`provided | fuzzy | llm`), score/confidence, and needs_review.
6. Report mapping accuracy against the answer key in tests.

## 4. Analysis engine (`src/analyze.py`)
Produce these outputs in `data/processed/`:
- **fee_summary** — fees by bank, month, AFP family, service; totals; ECR-eligible vs hard.
- **compensation** — per bank per month: avg ledger, avg collected, investable balance, ECR, earnings allowance, analyzed fees, hard charges, net service charge, balance required, excess balance, opportunity cost.
- **price_benchmark** — each bank's unit price per AFP code vs. benchmark p25/median/p75; also cross-bank comparison for the same code; annualized cost of paying above median.
- **exceptions** — one row per finding with: `finding_id, category (A1–A8 style labels in plain English), bank, account_id, afp_code, months_affected, monthly_impact, annualized_impact, evidence (plain-English calculation), recommended_action`.
- **savings_summary** — total annualized savings by category and bank. Avoid double-counting (e.g., a service that is both duplicate-billed and above benchmark).

Detection logic must be general (rules based on the data), not hardcoded to the answer key:
- Price change: unit price for same bank+code+tier changes month over month without a matching change across all customers (assume any increase >5% is flagged for review).
- Above benchmark: unit price > p75.
- Unused service: a maintenance/module fee where related transaction volume (same AFP family on that account) is zero for ≥3 consecutive months.
- Duplicate: same bank, account, month, code, volume, price appearing more than once.
- Tier misapplication: volume falls in a lower-price tier but billed at a higher tier price (tiers defined in the price list).
- Math error: |volume × unit_price − total_charge| > $0.01.
- ECR shortfall: net service charge > 0 in a month; flag when caused by an ECR change.
- Excess balance: excess balance > 20% of balance required for 3+ months.

Tests in `tests/` must confirm each planted anomaly A1–A8 is detected.

## 5. Dashboard (`app/streamlit_app.py`)
Global sidebar filters: bank, date range, AFP family. Clean, professional, minimal colors. Every page has a 1–2 sentence "What this shows" note.

1. **Executive Summary** — KPI cards: annual fees, earnings credit, net hard charges, identified annualized savings, excess balances. Monthly fees vs. earnings allowance by bank (line/bar). Top 5 findings by dollar impact.
2. **Fee Drivers** — fees by AFP family; Pareto of top services; volume vs. unit price.
3. **Price Benchmarking** — unit price vs. benchmark range per code (dot plot with p25–p75 band); cross-bank comparison for the same service; table of above-benchmark services with annualized overpayment.
4. **Exceptions** — filterable table of all findings with evidence and recommended action; CSV download.
5. **Balances & ECR** — earnings allowance vs. analyzed fees; balance required vs. actual collected balance; excess balance and opportunity cost; ECR history per bank. Include a what-if slider for ECR and money-market yield.
6. **AI Mapping Review** — table of mapped descriptions with method, confidence, and needs-review flag; mapping accuracy stat.
7. **Negotiation Memo** — the generated memo per bank (see §6), with a regenerate button that is disabled if no API key is set.

Footer on every page: "Synthetic data and illustrative benchmarks for demonstration purposes."

## 6. Negotiation memo — AI feature #2 (`src/memo.py`)
For each bank, send Claude a structured JSON summary of that bank's findings (no raw data) and ask for a 1-page memo in Markdown addressed to the Treasurer:
- Relationship overview (balances, annual fees, earnings credit, net cost)
- Top issues with dollar impact
- Specific asks (refunds for duplicates/errors, price rollbacks, removal of unused services, ECR improvement, sweep of excess balances)
- Fallback positions
- Suggested next step and timing

Rules: the model may only use numbers present in the JSON; validate after generation that every dollar figure in the memo appears in the input (flag mismatches). Cache output in `data/cache/memos/`.

## 7. README requirements
Lead with the business problem and results (headline savings number and 3 key findings), then a screenshot, then how it works (diagram of pipeline), then tech, then how to run it, then limitations (synthetic data, illustrative benchmarks, how real 822/camt.086 or PDF statements would plug in).

## 8. Stretch goals (only if asked)
- Parse a sample ISO 20022 camt.086 XML file into the statement schema.
- Excel export with formatted findings workbook.
- Bank RFP pricing comparison page.
