# PROMPTS.md — Paste these into Claude Code, one phase at a time

Start Claude Code in your project folder (`claude`). Wait for each phase to finish, review it, commit to Git, then continue.

---

## Phase 0 — Kickoff (plan only)
```
Read CLAUDE.md, SPEC.md, and reference/afp_codes_sample.csv. Don't write any code yet.

Give me:
1. A short plan for building this in the phases listed in BUILD_PLAN.md.
2. Any questions or ambiguities in the spec, especially in the treasury logic.
3. Anything you think is over-engineered for a portfolio project.

Keep it concise. Wait for my approval before starting Phase 1.
```

## Phase 1 — Scaffold
```
Phase 1: set up the project scaffold exactly as described in CLAUDE.md.
- Create the folder structure, requirements.txt, .env.example, .gitignore (must include .env, __pycache__, .venv), and src/config.py that loads settings from .env with sensible defaults.
- Create a virtual environment and install requirements.
- Initialize git and make the first commit.
Tell me the exact commands to activate the environment next time I open a terminal.
```

## Phase 2 — Synthetic data
```
Phase 2: build src/generate_data.py per SPEC.md §1 and §2.

Requirements:
- Fixed random seed; reproducible output.
- Realistic volumes and prices for a $600M manufacturer. Create reference/benchmark_prices.csv first (labeled illustrative), then build each bank's price list around it.
- Bank-specific description styles per §2.4.
- Plant every anomaly A1–A8 from §2.5 and write data/raw/_answer_key.csv.
- Add tests/test_generate_data.py checking: schema, row counts, no negative values, totals reconcile except for intentional A6 errors, and every anomaly exists in the data.

When done, run the generator and the tests, then show me:
- A sample of 10 statement lines from each bank
- A one-month summary for each bank (total fees, avg collected balance, ECR, earnings allowance)
- The answer key
Flag any numbers you think a treasury professional might find unrealistic.
```

## Phase 3 — AFP code mapping (AI feature #1)
```
Phase 3: build src/map_codes.py per SPEC.md §3.

- Fuzzy match first (rapidfuzz), LLM fallback second, using the anthropic SDK and the model from config.
- Force JSON output and validate it; retry once on malformed JSON.
- Cache all LLM responses in data/cache/afp_mappings.json. If no API key is set and the cache is missing an item, mark it needs_review instead of crashing.
- Add tests comparing mappings to the answer key and print overall accuracy plus accuracy by method.

Before calling the API, tell me how many descriptions will go to the LLM and the estimated cost. Then run it and show me every mapping with confidence below 0.8.
```

## Phase 4 — Analysis engine
```
Phase 4: build src/analyze.py per SPEC.md §4.

- Implement the compensation math exactly as in the CLAUDE.md glossary, respecting each bank's reserve %, day basis, ECR by month, and carryover flag.
- Detection rules must be general, not hardcoded to the answer key.
- Avoid double-counting savings across categories, and explain your de-duplication approach in a code comment.
- Add tests proving every planted anomaly A1–A8 is detected, plus a unit test of the earnings allowance and balance-required formulas using a hand-calculated example.

Then show me:
- The full exceptions table sorted by annualized impact
- The savings summary by category and bank
- One fully worked example of the earnings allowance calculation for Riverbend in March 2026 vs. April 2026 (before and after the ECR cut), step by step, so I can check it in Excel.
```

## Phase 5 — Dashboard
```
Phase 5: build app/streamlit_app.py per SPEC.md §5.

- Professional, minimal design suitable for showing a Treasurer. Use a consistent color per bank.
- Format currency properly ($1,234 and $1.2M where appropriate).
- Every page gets a short "What this shows" note and the synthetic-data footer.
- The app must load fully without an API key, using cached outputs.
- Add a single command (e.g., a Makefile target or run.py) that regenerates data, runs mapping and analysis, and launches the app.

Launch the app and tell me what to check on each page.
```

## Phase 6 — Negotiation memo (AI feature #2)
```
Phase 6: build src/memo.py per SPEC.md §6 and wire it into the Negotiation Memo page.

- Send only a structured JSON summary of findings for each bank, never raw rows.
- Instruct the model to use only numbers present in the JSON. After generation, extract every dollar figure from the memo and verify it appears in the input; show a warning in the app for any mismatch.
- Cache memos in data/cache/memos/. The regenerate button is disabled with a tooltip if no API key is set.

Generate memos for all three banks and show me the Riverbend one.
```

## Phase 7 — Polish & README
```
Phase 7: polish for a portfolio.

1. Run all tests and fix any failures.
2. Clean up code: remove dead code, add plain-English docstrings, make sure nothing hardcodes the answer key.
3. Write README.md per SPEC.md §7. Lead with the business problem and the headline results using the actual numbers from the analysis. Include a simple pipeline diagram (Mermaid) and placeholders for screenshots: docs/screenshot_exec.png, docs/screenshot_exceptions.png.
4. Add a short "Treasury concepts" section explaining ECR, earnings allowance, AFP codes, and 822/camt.086 for a non-treasury reader.
5. Confirm .env is not tracked by git and no API key appears anywhere in the repo.
```

## Phase 8 — Deploy
```
Phase 8: help me deploy to Streamlit Community Cloud.
- Make sure requirements.txt is complete and the processed data and caches are committed so the app runs without an API key.
- Give me step-by-step instructions to push to a new public GitHub repo and deploy on Streamlit Community Cloud.
- Tell me how to add my API key as a Streamlit secret only if I want regeneration to work in the live app (I may leave it off to avoid costs).
```

## Phase 9 (optional) — Power BI version
```
Export the processed outputs as clean, Power BI–friendly CSVs in exports/powerbi/ (a fact table of fee lines plus dimension tables for bank, account, AFP code, and month). Then write docs/POWERBI_GUIDE.md with step-by-step instructions to build the Executive Summary, Price Benchmarking, and Exceptions pages in Power BI Desktop, including the DAX measures I'll need for total fees, earnings allowance, net service charge, and annualized savings.
```

---

## Useful prompts anytime

**Teach me (use after every phase — this is your interview prep):**
```
Explain what you just built as if I'm a treasury analyst who'll be asked about it in an interview. Cover: the business problem it solves, how the logic works, the key formulas, assumptions you made, and 3 questions a Treasurer might ask me about it with good answers.
```

**Sanity check the treasury logic:**
```
Act as a skeptical Assistant Treasurer reviewing this analysis. What numbers or assumptions would you challenge? What's missing that a real bank fee review would include?
```

**Get back on track:**
```
Stop. Re-read CLAUDE.md and SPEC.md. List where the current code deviates from the spec, then fix only those issues.
```

**Something broke:**
```
Here's the error: <paste>. Explain the cause in plain English, fix it, and run the tests to confirm.
```

**Resume bullets at the end:**
```
Using the actual numbers from data/processed/, write 4 resume bullets and a 3-sentence LinkedIn project summary. Quantify savings, mapping accuracy, and data volume. Keep the tone professional, not hype.
```
