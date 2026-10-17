"""Map each bank's fee descriptions to AFP Service Codes (SPEC.md section 3).

Run from the project root:
    python -m src.map_codes            # map everything (calls Claude only for uncached items)
    python -m src.map_codes --plan     # show how many items would go to Claude and the cost, then stop

How each (bank, description) is mapped, in order:
  1. provided - the bank printed an AFP code on the statement; use it.
  2. fuzzy    - the description closely matches an AFP description (score >= 90).
  3. llm      - ask Claude to pick the best code from the reference list.
  4. unmapped - no API key and nothing cached; flagged for human review.

Every Claude answer is saved in data/cache/afp_mappings.json, so the app and tests
run without an API key and the same answer is reused on every run.
"""

import argparse
import json

import pandas as pd
from rapidfuzz import fuzz, process, utils

from src import config

CACHE_FILE = config.CACHE_DIR / "afp_mappings.json"
OUTPUT_FILE = config.PROCESSED_DIR / "code_mapping.csv"
ACCURACY_FILE = config.PROCESSED_DIR / "mapping_accuracy.json"
NO_MATCH = "NONE"  # what Claude answers when nothing in the list fits
# Bump this when the prompt changes so old cached answers are re-asked.
# v1: bank + description only. v2: adds account names, unit price, and monthly volume.
PROMPT_VERSION = 2

# Rough per-call token use, for the cost estimate shown before calling the API.
EST_INPUT_TOKENS_PER_CALL = 1_600
EST_OUTPUT_TOKENS_PER_CALL = 400   # answer plus the model's brief reasoning
PRICE_PER_M_INPUT = 0.10           # Claude Haiku 5.5, USD per million tokens
PRICE_PER_M_OUTPUT = 0.50


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def load_reference():
    """The illustrative AFP code list."""
    return pd.read_csv(config.REFERENCE_DIR / "afp_codes_sample.csv", dtype=str)


def load_descriptions():
    """Every distinct (bank, service code, description) on the statements, with any AFP code shown,
    plus context an analyst would look at: which accounts it is billed on, its usual unit price,
    and its usual bank-wide monthly volume."""
    frames = [pd.read_csv(path, dtype=str, keep_default_na=False)
              for path in sorted(config.RAW_DIR.glob("statements_*.csv"))]
    lines = pd.concat(frames, ignore_index=True)
    lines["volume"] = lines["volume"].astype(float)
    lines["unit_price"] = lines["unit_price"].astype(float)
    group = ["bank", "bank_service_code", "bank_description", "afp_code"]
    monthly_volume = (lines.groupby(group + ["statement_month"])["volume"].sum()
                      .groupby(level=group).median().rename("monthly_volume"))
    context = lines.groupby(group).agg(
        accounts=("account_name", lambda names: "; ".join(sorted(set(names)))),
        unit_price=("unit_price", "median"),
    ).join(monthly_volume).reset_index()
    return (context.rename(columns={"afp_code": "afp_code_on_statement"})
            .sort_values(["bank", "bank_service_code"]).reset_index(drop=True))


def describe_context(row):
    """The statement facts sent to Claude alongside the description (blank if not available)."""
    accounts = getattr(row, "accounts", "")
    if not accounts:
        return ""
    return (f"Billed on account(s): {accounts}\n"
            f"Typical unit price: ${row.unit_price:,.3f}\n"
            f"Typical bank-wide monthly volume: {row.monthly_volume:,.0f}")


# ---------------------------------------------------------------------------
# Step 2: fuzzy matching
# ---------------------------------------------------------------------------
def fuzzy_match(description, reference):
    """Best AFP description match by token-set similarity. Returns (code, score 0-100)."""
    choices = dict(zip(reference["afp_code"], reference["afp_description"]))
    _description, score, code = process.extractOne(
        description, choices, scorer=fuzz.token_set_ratio, processor=utils.default_process)
    return code, float(score)


# ---------------------------------------------------------------------------
# Step 3: Claude fallback
# ---------------------------------------------------------------------------
def cache_key(bank, description):
    return f"{bank} || {description}"


def cached_answer(cache, key):
    """A cached answer, only if it came from the current prompt version."""
    answer = cache.get(key)
    return answer if answer and answer.get("prompt_version") == PROMPT_VERSION else None


def load_cache(path=CACHE_FILE):
    return json.loads(path.read_text()) if path.exists() else {}


def save_cache(cache, path=CACHE_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def build_system_prompt(reference):
    """Instructions plus the full candidate code list (the same for every call)."""
    lines = [f"{r.afp_code} | {r.family_name} | {r.afp_description} | {r.unit_basis}"
             for r in reference.itertuples()]
    return (
        "You are a corporate treasury analyst mapping bank account analysis fee descriptions to "
        "AFP Service Codes (2020 U.S. domestic version). Banks often use their own product names "
        "and abbreviations, so reason about what service the fee is actually for. Use the statement "
        "context too: the account type, whether the price looks per-item or monthly, and whether "
        "the volume looks like a count of items or a single monthly charge.\n\n"
        "Pick the single best code from this list (code | family | description | unit basis):\n"
        + "\n".join(lines)
        + f"\n\nIf no code fits, answer \"{NO_MATCH}\". Confidence is your probability (0 to 1) "
        "that the code is correct; use below 0.7 when the description is ambiguous. "
        "Keep the reasoning to one sentence."
    )


def answer_schema(reference):
    """JSON schema that limits the answer to codes in the reference list."""
    return {
        "type": "object",
        "properties": {
            "afp_code": {"type": "string", "enum": list(reference["afp_code"]) + [NO_MATCH]},
            "afp_description": {"type": "string"},
            "confidence": {"type": "number"},
            "reasoning": {"type": "string"},
        },
        "required": ["afp_code", "afp_description", "confidence", "reasoning"],
        "additionalProperties": False,
    }


def parse_answer(text, reference):
    """Check Claude's JSON answer. Returns a clean dict, or raises ValueError if it is malformed."""
    data = json.loads(text)  # raises ValueError (JSONDecodeError) on bad JSON
    code = str(data.get("afp_code", ""))
    if code != NO_MATCH and code not in set(reference["afp_code"]):
        raise ValueError(f"Unknown AFP code {code!r}")
    confidence = float(data.get("confidence"))
    if not 0 <= confidence <= 1:
        raise ValueError(f"Confidence {confidence} is outside 0-1")
    return {"afp_code": code, "afp_description": str(data.get("afp_description", "")),
            "confidence": confidence, "reasoning": str(data.get("reasoning", "")).strip()}


def ask_claude(client, bank, description, reference, context=""):
    """One Claude call for one description, with one retry if the answer is malformed."""
    question = f"Bank: {bank}\nFee description: {description}"
    if context:
        question += f"\n{context}"
    request = dict(
        model=config.ANTHROPIC_MODEL,
        max_tokens=2_000,
        # Haiku 5.5 does not accept a temperature setting; low effort plus a fixed
        # answer schema keeps answers short and consistent instead.
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": answer_schema(reference)}},
        system=build_system_prompt(reference),
        messages=[{"role": "user", "content": question}],
    )
    last_error = None
    for _attempt in range(2):
        response = client.messages.create(**request)
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            answer = parse_answer(text, reference)
            answer["model"] = config.ANTHROPIC_MODEL
            answer["prompt_version"] = PROMPT_VERSION
            return answer
        except (ValueError, TypeError) as error:
            last_error = error
    raise ValueError(f"Malformed answer twice for {description!r}: {last_error}")


def make_client():
    """Anthropic client, or None when no API key is configured."""
    if not config.has_api_key():
        return None
    import anthropic  # imported here so the app never needs the SDK just to read the cache
    return anthropic.Anthropic()


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------
def map_one(row, reference, ref_desc, cache, client):
    """Map one description. Returns the output row and whether the cache changed."""
    out = {"bank": row.bank, "bank_service_code": row.bank_service_code,
           "bank_description": row.bank_description, "afp_code_on_statement": row.afp_code_on_statement,
           "mapped_afp_code": "", "mapped_afp_description": "", "method": "unmapped",
           "fuzzy_score": None, "confidence": None, "reasoning": "", "needs_review": True}
    # 1. Code printed on the statement.
    if row.afp_code_on_statement:
        code = row.afp_code_on_statement
        out.update(mapped_afp_code=code, method="provided", confidence=1.0, needs_review=False,
                   reasoning="AFP code provided by the bank")
        out["mapped_afp_description"] = ref_desc.get(code, "")
        return out, False
    # 2. Close text match.
    code, score = fuzzy_match(row.bank_description, reference)
    out["fuzzy_score"] = score
    if score >= config.FUZZY_ACCEPT_SCORE:
        out.update(mapped_afp_code=code, mapped_afp_description=ref_desc[code], method="fuzzy",
                   confidence=round(score / 100, 2), needs_review=False,
                   reasoning=f"Text match to '{ref_desc[code]}' (score {score:.0f})")
        return out, False
    # 3. Claude (cached answer first).
    key, changed = cache_key(row.bank, row.bank_description), False
    if cached_answer(cache, key) is None and client is not None:
        try:
            cache[key] = ask_claude(client, row.bank, row.bank_description, reference, describe_context(row))
            changed = True
        except Exception as error:  # an API problem should flag the item, not stop the run
            out["reasoning"] = f"AI mapping failed: {type(error).__name__}"
            return out, False
    answer = cached_answer(cache, key)
    if answer is not None:
        code = "" if answer["afp_code"] == NO_MATCH else answer["afp_code"]
        out.update(mapped_afp_code=code, mapped_afp_description=ref_desc.get(code, ""), method="llm",
                   confidence=answer["confidence"], reasoning=answer["reasoning"],
                   needs_review=(answer["confidence"] < config.REVIEW_CONFIDENCE or not code))
    elif not out["reasoning"]:
        out["reasoning"] = "No API key and no cached AI answer - needs manual mapping"
    return out, changed


def map_all(descriptions=None, reference=None, cache_path=CACHE_FILE, client="auto"):
    """Map every description. Returns the mapping table (one row per bank description)."""
    reference = load_reference() if reference is None else reference
    descriptions = load_descriptions() if descriptions is None else descriptions
    ref_desc = dict(zip(reference["afp_code"], reference["afp_description"]))
    client = make_client() if client == "auto" else client
    cache = load_cache(cache_path)
    rows = []
    for row in descriptions.itertuples():
        out, changed = map_one(row, reference, ref_desc, cache, client)
        rows.append(out)
        if changed:
            save_cache(cache, cache_path)  # save as we go so a crash loses nothing
    return pd.DataFrame(rows)


def pending_llm_items(descriptions=None, reference=None, cache_path=CACHE_FILE):
    """Descriptions that would need a new Claude call (blank code, no fuzzy match, not cached)."""
    reference = load_reference() if reference is None else reference
    descriptions = load_descriptions() if descriptions is None else descriptions
    cache = load_cache(cache_path)
    pending = []
    for row in descriptions.itertuples():
        if row.afp_code_on_statement:
            continue
        if fuzzy_match(row.bank_description, reference)[1] >= config.FUZZY_ACCEPT_SCORE:
            continue
        if cached_answer(cache, cache_key(row.bank, row.bank_description)) is None:
            pending.append((row.bank, row.bank_description))
    return pending


def estimated_cost(n_calls):
    """Rough USD cost of n Claude calls (worst case: every call retried once)."""
    per_call = (EST_INPUT_TOKENS_PER_CALL * PRICE_PER_M_INPUT
                + EST_OUTPUT_TOKENS_PER_CALL * PRICE_PER_M_OUTPUT) / 1_000_000
    return n_calls * per_call, 2 * n_calls * per_call


# ---------------------------------------------------------------------------
# Accuracy (pipeline only - reads the answer key, which the app never does)
# ---------------------------------------------------------------------------
def accuracy_report(mapping):
    """Share of descriptions mapped to the true code, overall and by method."""
    key = pd.read_csv(config.RAW_DIR / "_mapping_key.csv", dtype=str, keep_default_na=False)
    merged = mapping.merge(key[["bank", "bank_service_code", "true_afp_code"]],
                           on=["bank", "bank_service_code"], how="left")
    merged["correct"] = merged["mapped_afp_code"] == merged["true_afp_code"]
    needed = merged[merged["method"] != "provided"]  # descriptions that actually needed mapping
    report = {
        "overall_accuracy": round(merged["correct"].mean(), 4),
        "descriptions": int(len(merged)),
        "accuracy_where_code_was_blank": round(needed["correct"].mean(), 4) if len(needed) else None,
        "blank_code_descriptions": int(len(needed)),
        "needs_review": int(merged["needs_review"].sum()),
        "by_method": {
            method: {"count": int(len(g)), "accuracy": round(g["correct"].mean(), 4)}
            for method, g in merged.groupby("method")
        },
    }
    return report, merged


def write_outputs(mapping, report):
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    mapping.to_csv(OUTPUT_FILE, index=False)
    ACCURACY_FILE.write_text(json.dumps(report, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Map bank fee descriptions to AFP codes.")
    parser.add_argument("--plan", action="store_true", help="show pending Claude calls and cost, then stop")
    args = parser.parse_args()

    pending = pending_llm_items()
    low, high = estimated_cost(len(pending))
    print(f"{len(pending)} description(s) need a new Claude call ({config.ANTHROPIC_MODEL}). "
          f"Estimated cost ${low:.4f} (worst case ${high:.4f}).")
    for bank, description in pending:
        print(f"  - {bank}: {description}")
    if args.plan:
        return
    if pending and not config.has_api_key():
        print("No ANTHROPIC_API_KEY set - these will be flagged needs_review.")

    mapping = map_all()
    report, _ = accuracy_report(mapping)
    write_outputs(mapping, report)
    print(f"Wrote {OUTPUT_FILE.relative_to(config.PROJECT_ROOT)} ({len(mapping)} descriptions)")
    print(f"Accuracy: {report['overall_accuracy']:.1%} overall, "
          f"{report['accuracy_where_code_was_blank']:.1%} where the bank left the code blank")
    for method, stats in report["by_method"].items():
        print(f"  {method:<9} {stats['count']:>3} descriptions, {stats['accuracy']:.1%} correct")
    print(f"Flagged for review: {report['needs_review']}")


if __name__ == "__main__":
    main()
