# CLAUDE.md — Bank Fee Analyzer

## Project purpose
A portfolio project for a treasury analyst job search. It demonstrates corporate treasury domain knowledge (bank fee analysis, account analysis statements, AFP Service Codes, earnings credits) combined with practical AI use. The audience is a Treasurer or Assistant Treasurer hiring manager, not a software engineer. **Clarity and correct treasury logic matter more than technical sophistication.**

The full requirements are in `SPEC.md`. Read it before starting any phase, along with `DECISIONS.md` (Phase 0 clarifications that override SPEC.md where they differ). If SPEC.md and a prompt conflict, ask before proceeding.

## The owner
The owner is a finance professional, not a developer. When you finish a step:
- Summarize what you built in plain business language.
- Tell them exactly what to run and what they should see.
- Flag any assumption you made about treasury logic so they can verify it.

## Tech stack (keep it simple)
- Python 3.11+, pandas, numpy
- Streamlit for the dashboard, Plotly for charts
- `rapidfuzz` for fuzzy matching
- `anthropic` Python SDK for AI features; model set via env var `ANTHROPIC_MODEL` (default `claude-haiku-5-5`)
- `python-dotenv` for config; pytest for tests
- No databases, no Docker, no web frameworks beyond Streamlit. CSV/Parquet files on disk are fine.

## Project structure
```
bank-fee-analyzer/
├── CLAUDE.md, SPEC.md, DECISIONS.md, PROMPTS.md, BUILD_PLAN.md, README.md
├── .env.example            # ANTHROPIC_API_KEY=, ANTHROPIC_MODEL=
├── requirements.txt
├── reference/
│   ├── afp_codes_sample.csv     # illustrative AFP code subset (provided)
│   └── benchmark_prices.csv     # generated, clearly labeled illustrative
├── data/
│   ├── raw/                # generated synthetic statements (one CSV per bank)
│   ├── processed/          # mapped + analyzed outputs
│   └── cache/              # cached LLM responses (JSON) so the app runs without an API key
├── src/
│   ├── generate_data.py
│   ├── map_codes.py
│   ├── analyze.py
│   ├── memo.py
│   └── config.py
├── app/
│   └── streamlit_app.py
└── tests/
```

## Non-negotiable rules
1. **No real bank names, real companies, or real benchmark claims.** Use the fictional entities in SPEC.md. Label all benchmark prices "illustrative."
2. **Reproducible:** synthetic data uses a fixed random seed. Running the pipeline twice gives identical results.
3. **The app must run without an API key** using cached LLM outputs in `data/cache/`. AI calls are only made when the cache is missing or the user clicks "regenerate."
4. **Never commit secrets.** `.env` is gitignored. Never print API keys.
5. **Every dollar figure must be traceable** to source rows. Anomaly findings include the account, bank, month, AFP code, and calculation.
6. **Money math:** use rounded-to-cents decimals for display; keep full precision in calculations. Annualize monthly findings explicitly and label them.
7. **Tests prove the planted anomalies are detected** (see SPEC.md §4). If a test fails, fix the logic, not the test.
8. Keep functions small and commented in plain English. The owner will read this code to learn from it.

## Treasury glossary and formulas
- **Account analysis statement:** monthly bank invoice. Lists balances, each service used, volume, unit price, total charge, and earnings credit applied.
- **AFP Service Codes:** standard 6-digit codes (2020 domestic version) for bank services. First two digits = family:
  `00` Balance & Compensation Info · `01` General Account Services · `05` Lockbox · `10` Depository · `15` Paper Disbursement · `20` Paper Disbursement Reconciliation · `25` General ACH · `35` Wire & Other Funds Transfer · `40` Information Services · `45` Investment Services. Banks also use their own internal codes and descriptions, which is why mapping is needed.
- **Average ledger balance:** average end-of-day book balance.
- **Average collected balance:** ledger balance minus float (deposited items not yet collected).
- **Reserve requirement / reserve haircut:** the share of collected balance excluded from earning credit. The Fed reserve requirement has been 0% since March 2020, but some banks still apply a haircut. Parameterize per bank (default 0% for two banks, 10% for one).
- **Investable balance** = collected balance × (1 − reserve %)
- **ECR (earnings credit rate):** annual rate the bank applies to investable balances to produce a non-cash credit.
- **Earnings allowance (earnings credit)** = investable balance × ECR × (days in month / 365). Some banks use 360 — parameterize per bank.
- **Analyzed (ECR-eligible) fees:** service fees that earnings credit can offset.
- **Hard charges / fee-based services:** not eligible for offset (e.g., FDIC assessment at some banks, certain third-party pass-throughs). Flag via an `ecr_eligible` field.
- **Net service charge** = max(0, analyzed fees − earnings allowance) + hard charges. Excess earnings credit generally expires monthly (no carryover) unless the bank's `ecr_carryover` flag is true.
- **Balance required to offset fees** = analyzed fees ÷ (ECR × days/basis) ÷ (1 − reserve %)
- **Excess balance** = collected balance − balance required. Excess balances earn nothing beyond offsetting fees; opportunity cost = excess balance × alternative yield (use a configurable money market rate, default 4.0%) × days/365.
- **FDIC assessment fee (000230):** charged per $1,000 of average ledger (or collected) balance.
- **Billing standards:** ANSI X12 822 (U.S. account analysis EDI) and ISO 20022 camt.086 (Bank Services Billing). Not required to implement, but reference in README.
