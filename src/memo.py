"""Draft a one-page negotiation memo for each bank (SPEC.md section 6).

Run from the project root (after analyze):
    python -m src.memo            # draft memos that are missing or out of date
    python -m src.memo --force    # redraft all three
    python -m src.memo --plan     # show what would be sent and the estimated cost, then stop

How the memo stays honest:
  1. Claude only receives a structured summary of one bank's findings - never raw statement rows.
  2. Every dollar figure in that summary is already formatted, and Claude is told to quote
     figures exactly as written and never to calculate new ones.
  3. After drafting, every dollar figure in the memo is checked against the summary. A figure
     passes if it appears in the summary, or is that same figure rounded (e.g. $5,579.86 -> $5,580
     or $1,145,346 -> $1.1M). Anything else - such as a total Claude added up itself - is flagged.

Memos are cached in data/cache/memos/ (one JSON file plus a readable .md per bank), so the app
shows them without an API key. A memo is redrafted only when asked, or when the findings it was
based on have changed.
"""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone

import pandas as pd

from src import config

COMPANY = "Heartland Fabrication Co. (fictional)"
EST_INPUT_TOKENS, EST_OUTPUT_TOKENS = 4_000, 2_500  # per memo, for the cost estimate
PRICE_PER_M_INPUT, PRICE_PER_M_OUTPUT = 0.10, 0.50   # Claude Haiku 5.5, USD per million tokens


# ---------------------------------------------------------------------------
# Formatting helpers - every figure Claude sees is pre-formatted here
# ---------------------------------------------------------------------------
def dollars(x):
    """Whole dollars: $1,234."""
    return f"${x:,.0f}"


def dollars_cents(x):
    """Exact amount for refunds: $1,234.56."""
    return f"${x:,.2f}"


def bank_slug(bank):
    return bank.split()[0].lower()


def months_label(months):
    """'2026-07;2026-08;2026-09' -> 'Jul 2026 - Sep 2026 (3 months)'."""
    ms = [pd.Timestamp(m + "-01") for m in str(months).split(";")]
    if len(ms) == 1:
        return f"{ms[0]:%b %Y}"
    return f"{ms[0]:%b %Y} - {ms[-1]:%b %Y} ({len(ms)} months)"


# ---------------------------------------------------------------------------
# Step 1: the structured summary sent to Claude (no raw rows)
# ---------------------------------------------------------------------------
def load_results():
    """The processed analysis outputs this module needs."""
    p = config.PROCESSED_DIR
    read = lambda name: pd.read_csv(p / name, dtype={"afp_code": str}, keep_default_na=False, na_values=[""])
    balances = pd.read_csv(config.RAW_DIR / "balances.csv")
    ref = pd.read_csv(config.REFERENCE_DIR / "afp_codes_sample.csv", dtype=str)
    return {
        "afp_names": dict(zip(ref["afp_code"], ref["afp_description"])),
        "exceptions": read("exceptions.csv"),
        "compensation": read("compensation.csv"),
        "accounts": balances.groupby("bank")["account_id"].nunique().to_dict(),
    }


def ecr_description(comp):
    """'2.50%' if the ECR never changed, else '2.25% until Mar 2026, then 1.75%'."""
    comp = comp.sort_values("statement_month")
    changes = comp[comp["ecr"] != comp["ecr"].shift()]
    if len(changes) == 1:
        return f"{changes['ecr'].iloc[0]:.2%}"
    parts = [f"{changes['ecr'].iloc[0]:.2%}"]
    for row in changes.iloc[1:].itertuples():
        parts.append(f"{row.ecr:.2%} from {pd.Timestamp(row.statement_month):%b %Y}")
    return ", then ".join(parts)


def finding_amount(row):
    """The finding's dollar impact, formatted the way the memo should quote it."""
    if row["savings_type"] == "One-time recovery":
        return {"one_time_refund": dollars_cents(row["one_time_recovery"])}
    key = "annual_income_opportunity" if row["savings_type"] == "Income opportunity" else "annual_savings"
    return {key: dollars(row["annualized_impact"]), "monthly": dollars(row["monthly_impact"])}


def build_summary(bank, results=None):
    """Everything Claude needs about one bank, as a dict of pre-formatted text."""
    results = load_results() if results is None else results
    comp = results["compensation"][results["compensation"]["bank"] == bank]
    exc = results["exceptions"][results["exceptions"]["bank"] == bank]
    run_rate = exc.loc[exc["savings_type"] == "Annual run-rate", "annualized_impact"].sum()
    income = exc.loc[exc["savings_type"] == "Income opportunity", "annualized_impact"].sum()
    findings = []
    for _, row in exc.sort_values("dollar_impact", ascending=False).iterrows():
        findings.append({
            "id": row["finding_id"],
            "issue": row["category"],
            "service": row["service"],
            "afp_service": results["afp_names"].get(row["afp_code"], ""),  # standard name, e.g. for "POS PAY MAINT"
            "account": "all accounts" if row["account_id"] == "ALL" else row["account_id"],
            "months": months_label(row["months_affected"]),
            "type": row["savings_type"],
            **finding_amount(row),
            "calculation": row["evidence"],
            "suggested_action": row["recommended_action"],
        })
    return {
        "company": COMPANY,
        "bank": bank,
        "period": "Oct 2025 - Sep 2026 (12 months of account analysis statements)",
        "relationship": {
            "accounts": int(results["accounts"].get(bank, 0)),
            "average_collected_balance": dollars(comp["avg_collected_balance"].mean()),
            "average_balance_required_to_offset_fees": dollars(comp["balance_required"].mean()),
            "annual_fees": dollars(comp["analyzed_fees"].sum() + comp["hard_charges"].sum()),
            "annual_earnings_credit": dollars(comp["earnings_allowance"].sum()),
            "annual_net_cash_charges": dollars(comp["net_service_charge"].sum()),
            "ecr": ecr_description(comp),
            "reserve_haircut": f"{comp['reserve_pct'].iloc[0]:.0%}",
            "day_basis": int(comp["day_basis"].iloc[0]),
        },
        "totals": {
            "one_time_refunds": dollars_cents(exc["one_time_recovery"].sum()),
            "annual_fee_savings": dollars(run_rate),
            "annual_income_opportunity_from_idle_cash": dollars(income),
        },
        "findings": findings,
        "notes": [
            "Benchmark prices are illustrative, not market survey data.",
            "Idle-cash income opportunity is investment income, not a fee saving - keep it separate.",
            "One-time refunds are past overcharges; annual savings are going forward.",
        ],
    }


def summary_hash(summary):
    """Fingerprint of the summary, so a memo can tell when its findings have changed."""
    return hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Step 2: the dollar-figure check
# ---------------------------------------------------------------------------
# "$1,234.56", "$0.58", "$1.1M", "$25 thousand" - commas only between digit groups, so "$3,000," stops at the 0.
DOLLAR_RE = re.compile(r"\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:\s?(K|M|B|thousand|million|billion)\b)?",
                       re.IGNORECASE)
SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


def dollar_figures(text):
    """Every dollar figure in a text: (as written, value, decimals shown, scale)."""
    found = []
    for match in DOLLAR_RE.finditer(text):
        number, suffix = match.group(1).replace(",", ""), (match.group(2) or "").lower()
        decimals = len(number.split(".")[1]) if "." in number else 0
        scale = SCALE.get(suffix, 1.0)
        found.append((match.group(0).strip(), float(number) * scale, decimals, scale))
    return found


def check_figures(memo_text, summary):
    """Check every dollar figure in the memo against the summary.

    A figure passes if the summary contains a dollar amount that, rounded the way the memo
    shows it, is the same number. Returns {"checked": n, "mismatches": [...]}.
    """
    allowed = [value for _, value, _, _ in dollar_figures(json.dumps(summary))]
    mismatches = []
    figures = dollar_figures(memo_text)
    for written, value, decimals, scale in figures:
        shown = round(value / scale, decimals)
        if not any(round(a / scale, decimals) == shown for a in allowed):
            mismatches.append(written)
    return {"checked": len(figures), "mismatches": sorted(set(mismatches))}


# ---------------------------------------------------------------------------
# Step 3: drafting with Claude
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a senior corporate treasury analyst. You write a one-page bank negotiation \
memo, in Markdown, addressed to the company's Treasurer, based only on the JSON summary you are given.

Use exactly these sections, as level-2 headings:
## Relationship overview
## Top issues
## Specific asks
## Fallback positions
## Suggested next step and timing

Rules for numbers - these matter more than style:
- Use ONLY dollar figures that appear in the JSON, quoted exactly as written there (you may round a \
figure, e.g. $5,579.86 to $5,580). Never add, subtract, or multiply figures to make new totals; if you \
want a total, use one from "totals" or "relationship".
- Do not invent prices, rates, percentages, benchmarks, or dates that are not in the JSON.
- Keep idle-cash income opportunity separate from fee savings; never add them together.
- Distinguish one-time refunds (past overcharges) from annual savings (going forward).

Style: concise and specific, for a busy Treasurer. Start with a title line and a short header \
(To / From / Re). Aim for one page (about 350-500 words). Refer to findings by issue and service, \
not by ID. Specific asks should name the refund, rollback, cancellation, price, or ECR change being \
requested. Fallback positions should be concrete concessions to accept if the bank pushes back. \
Mention once that benchmarks are illustrative."""


def draft_memo(client, summary):
    """Ask Claude for the memo. Returns the Markdown text."""
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=6_000,
        output_config={"effort": "medium"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "Findings summary (JSON):\n```json\n"
                   + json.dumps(summary, indent=2) + "\n```\nWrite the memo."}],
    )
    text = "\n".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise ValueError(f"Empty memo (stop reason: {response.stop_reason})")
    return text


def make_client():
    if not config.has_api_key():
        return None
    import anthropic
    return anthropic.Anthropic()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def cache_path(bank, folder=None):
    return (folder or config.MEMO_CACHE_DIR) / f"{bank_slug(bank)}.json"


def load_memo(bank, folder=None):
    """The cached memo record for a bank, or None."""
    path = cache_path(bank, folder)
    return json.loads(path.read_text()) if path.exists() else None


def save_memo(record, folder=None):
    folder = folder or config.MEMO_CACHE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    cache_path(record["bank"], folder).write_text(json.dumps(record, indent=2) + "\n")
    (folder / f"{bank_slug(record['bank'])}.md").write_text(record["memo_markdown"] + "\n")


def is_stale(record, summary):
    """True if the findings have changed since the memo was drafted."""
    return record is None or record.get("summary_hash") != summary_hash(summary)


def generate_memo(bank, client="auto", force=False, results=None, folder=None):
    """Draft (or reuse) one bank's memo. Returns the cached record, or None with no key and no cache."""
    summary = build_summary(bank, results)
    record = load_memo(bank, folder)
    if record is not None and not force and not is_stale(record, summary):
        return record
    client = make_client() if client == "auto" else client
    if client is None:
        return record  # no API key: keep whatever is cached (possibly None)
    text = draft_memo(client, summary)
    record = {
        "bank": bank,
        "model": config.ANTHROPIC_MODEL,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "summary_hash": summary_hash(summary),
        "summary": summary,
        "memo_markdown": text,
        "figure_check": check_figures(text, summary),
    }
    save_memo(record, folder)
    return record


def all_banks(results=None):
    results = load_results() if results is None else results
    return sorted(results["compensation"]["bank"].unique())


def main():
    parser = argparse.ArgumentParser(description="Draft bank negotiation memos.")
    parser.add_argument("--force", action="store_true", help="redraft every memo")
    parser.add_argument("--plan", action="store_true", help="show what would be drafted and the cost, then stop")
    args = parser.parse_args()
    results = load_results()
    banks = all_banks(results)
    todo = [b for b in banks if args.force or is_stale(load_memo(b), build_summary(b, results))]
    per_memo = (EST_INPUT_TOKENS * PRICE_PER_M_INPUT + EST_OUTPUT_TOKENS * PRICE_PER_M_OUTPUT) / 1e6
    print(f"{len(todo)} memo(s) to draft with {config.ANTHROPIC_MODEL}: {', '.join(todo) or 'none'}. "
          f"Estimated cost ${len(todo) * per_memo:.4f}.")
    if args.plan:
        return
    if todo and not config.has_api_key():
        print("No ANTHROPIC_API_KEY set - keeping cached memos as they are.")
    for bank in banks:
        record = generate_memo(bank, force=args.force, results=results)
        if record is None:
            print(f"  {bank}: no memo (no API key and nothing cached)")
            continue
        check = record["figure_check"]
        status = "all match" if not check["mismatches"] else f"MISMATCHES: {', '.join(check['mismatches'])}"
        print(f"  {bank}: {check['checked']} dollar figures checked - {status} "
              f"-> {cache_path(bank).relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
