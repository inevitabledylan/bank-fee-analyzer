# Bank Fee Analysis Dashboard — Build Plan

**What you're building:** An AI-assisted tool that takes 12 months of bank account analysis statements from three (fictional) banks, normalizes every fee to AFP Service Codes, benchmarks unit prices, catches billing errors, analyzes earnings credits vs. fees, and produces an interactive dashboard plus an AI-drafted bank negotiation memo.

**The story you'll tell in interviews:** "I built a tool that found $X of annual savings in a mid-size company's bank fees — mispriced services, unannounced price increases, duplicate charges, fees for services nobody used, and idle balances that should be invested."

---

## 1. Budget (target: under $30)

| Item | Cost | Notes |
|---|---|---|
| Claude Code | ~$20 (one month of Claude Pro) | Or pay-as-you-go API credits. Cancel after the build if you like. |
| Anthropic API credits (for the app's AI features) | $5 minimum top-up | The app uses a small, cheap model; the whole project should use well under $1. |
| Python, VS Code, Git, GitHub | Free | |
| Streamlit Community Cloud hosting | Free | Gives you a live link for your resume. |
| Power BI Desktop (optional) | Free | Windows only. Optional second version of the dashboard. |
| **Total** | **~$25** | |

## 2. Time estimate

About **12–18 hours** spread over 1–2 weekends. Most of that is reviewing what Claude Code builds and learning the treasury logic well enough to explain it — that part matters more than the code.

## 3. One-time setup (≈1 hour)

1. Install **Python 3.11+**, **Git**, and **VS Code**.
2. Create a free **GitHub** account if you don't have one.
3. Install **Claude Code** and sign in (follow Anthropic's current install instructions at docs.claude.com).
4. Create an Anthropic API key at console.anthropic.com, add $5 of credit, and keep the key handy. You'll put it in a `.env` file — never commit it to GitHub.
5. Make a project folder, e.g. `bank-fee-analyzer/`, and copy these kit files into it:
   ```
   bank-fee-analyzer/
   ├── CLAUDE.md                  ← Claude Code reads this automatically
   ├── SPEC.md                    ← the detailed requirements
   ├── PROMPTS.md                 ← the prompts you paste, phase by phase
   ├── BUILD_PLAN.md              ← this file (for you)
   └── reference/
       └── afp_codes_sample.csv   ← illustrative AFP code list
   ```
6. Open a terminal in that folder and run `claude`.

## 4. Build phases

Each phase has a matching prompt in **PROMPTS.md**. Paste one prompt, let Claude Code finish, review, then move on. Commit to Git after every phase.

| Phase | What gets built | Your review checklist | Time |
|---|---|---|---|
| **0. Kickoff** | Claude reads the docs and proposes a plan (no code yet) | Does the plan match SPEC.md? Push back on anything over-engineered. | 15 min |
| **1. Scaffold** | Folder structure, virtual env, dependencies, `.env` template, Git | `.env` is in `.gitignore`. | 30 min |
| **2. Synthetic data** | Realistic account analysis statements for 3 banks × 12 months, with planted problems | Open the CSVs in Excel. Do the numbers look like a real statement? Does each planted anomaly exist? | 2–3 hrs |
| **3. AFP code mapping (AI #1)** | Maps messy bank fee descriptions to AFP codes using fuzzy matching, with Claude as a fallback | Check 10–15 mappings by hand. Are low-confidence ones flagged for review? | 1–2 hrs |
| **4. Analysis engine** | Fee rollups, price benchmarking, anomaly detection, earnings credit math, savings estimate | Recalculate 2–3 numbers yourself in Excel (especially earnings allowance). | 2–3 hrs |
| **5. Dashboard** | Streamlit app with 6 pages | Click every page and filter. Would a Treasurer understand it in 30 seconds? | 2–3 hrs |
| **6. Negotiation memo (AI #2)** | Claude drafts a bank-by-bank negotiation brief from the findings | Every number in the memo must match the dashboard. | 1 hr |
| **7. Polish & README** | Tests, README with screenshots, cleanup | README tells the business story first, tech second. | 1–2 hrs |
| **8. Deploy** | Live app on Streamlit Community Cloud | Link works in an incognito window. | 30 min |
| **9. (Optional) Power BI** | Same analysis rebuilt in Power BI from the exported CSVs | Shows you can work in the tools treasury teams actually use. | 2–3 hrs |

## 5. How to work with Claude Code effectively

- **One phase at a time.** Don't paste all prompts at once.
- **Ask it to explain.** After each phase, use the "teach me" prompt at the bottom of PROMPTS.md. You need to be able to defend every calculation in an interview.
- **Make it prove things.** Ask it to run the tests and show you output, not just tell you it works.
- **Commit often.** If a phase goes sideways, `git checkout .` and re-prompt.
- **If it drifts**, say: "Re-read CLAUDE.md and SPEC.md. You're deviating from section X."

## 6. Treasury concepts to know cold (interview prep)

Study these while Claude Code builds. The CLAUDE.md glossary has formulas.

- **Account analysis statement** — the monthly bank invoice showing balances, services used, volumes, unit prices, and how earnings credits offset fees.
- **AFP Service Codes** — the industry-standard 6-digit codes (U.S. domestic) that let you compare fees across banks. Families include General Account Services (01), Depository (10), Paper Disbursement (15), Disbursement Reconciliation (20), ACH (25), Wire (35), and Information Services (40). The full list is sold by AFP; this project uses an illustrative subset.
- **ECR (earnings credit rate)** and **earnings allowance** — banks give a non-cash credit on balances that offsets fees. If the ECR is cut, fees that were "free" become hard charges.
- **Balance required to offset fees** — and why keeping excess balances beyond that is an opportunity cost vs. a money market fund or sweep.
- **Analyzed fees vs. hard charges** — not all fees are eligible for earnings credit offset.
- **FDIC assessment fee** (AFP code 000230) — passed through per $1,000 of balances.
- **Billing file standards** — ANSI X12 **822** (U.S.) and ISO 20022 **camt.086** (global). Mentioning these signals real fluency.

## 7. Resume bullets (fill in your real numbers)

- Built an AI-assisted bank fee analytics tool in Python that normalized 12 months of account analysis data across 3 banks to AFP Service Codes, identifying **$XX,XXX (X%) in annualized savings** from mispriced services, duplicate billing, and unused products.
- Used an LLM to map non-standard bank fee descriptions to AFP codes with **XX% accuracy**, flagging low-confidence matches for human review.
- Modeled earnings credit vs. fee coverage to quantify **$X.XM of excess idle balances** available for investment, and generated AI-drafted negotiation briefs for each bank relationship.
- Deployed an interactive Streamlit dashboard (link) used to present findings in a Treasurer-ready format.

## 8. Interview talking points

- **Why it matters:** Bank fees are often a top-10 controllable cost in treasury, and statements are hard to read. Most companies don't review them line by line.
- **What you'd do on day one at their company:** Request 12 months of 822 files or PDF statements from each bank, run them through this process, and bring findings to the next bank relationship review.
- **Your judgment, not just the AI:** Explain why the AI mapping has a human-review step, and how you'd validate a savings number before taking it to a bank.
- **Limitations, honestly stated:** Synthetic data, illustrative benchmarks, and real statements would need a parser for PDFs or 822 files. Interviewers respect this.

## 9. Share it

- Pin the GitHub repo to your profile.
- Add the live link and repo to the Projects section of your resume and LinkedIn.
- Write a short LinkedIn post: the problem, 3 findings with dollar amounts, one screenshot, and the link.
