"""Analysis engine: fee rollups, earnings-credit math, benchmarking, and anomaly detection.

Run from the project root (after generate_data and map_codes):
    python -m src.analyze

Writes to data/processed/:
  fee_summary.csv       fees by bank, month, AFP family, and service
  compensation.csv      per bank per month: balances, earnings allowance, fees, net charge, excess balance
  price_benchmark.csv   each bank's unit price per AFP code vs. the illustrative benchmark and the other banks
  exceptions.csv        one row per finding, with the calculation written out in plain English
  savings_summary.csv   savings by category and bank, split into one-time / run-rate / income

The detection rules work only from the data (statements, balances, terms, price lists,
benchmarks). They never read the answer key.

HOW SAVINGS AVOID DOUBLE-COUNTING
Findings are worked out in a fixed order, and each later step only counts what is
left after the earlier ones:
  1. One-time refunds (duplicates A4, tier errors A5, math errors A6) are past
     overcharges. They are separate money from any future price change, so they never
     overlap with run-rate savings. A duplicate line is refunded in full by A4, so the
     other rules ignore duplicate rows when counting volume.
  2. Unused services (A3) are cancelled. A cancelled line is removed before the price
     rules run, so we never "roll back the price" of a fee we are also cancelling.
  3. Price increases (A1) are rolled back to the old price.
  4. Above-benchmark pricing (A2) is measured from the rolled-back price down to the
     benchmark p75, so the A1 part of a price is not counted again.
  5. The ECR shortfall (A7) is measured after the bank's fees are reduced by steps 2-4,
     because fixing those fees also shrinks the gap the earnings credit has to cover.
  6. Idle balances (A8) are lost investment income, not a fee, so they are reported
     in their own "income opportunity" column and never added to fee savings.
"""

import json

import numpy as np
import pandas as pd

from src import config

MONTH = "statement_month"
MONEY_COLS = [
    "volume_x_price", "total_charge", "ecr_eligible_fees", "non_offsettable_fees",
    "avg_ledger_balance", "avg_collected_balance", "investable_balance", "earnings_allowance",
    "credit_carried_in", "analyzed_fees", "hard_charges", "earnings_shortfall", "net_service_charge",
    "balance_required", "excess_balance", "opportunity_cost", "monthly_impact", "annualized_impact",
    "one_time_recovery", "dollar_impact", "annual_cost", "annual_cost_above_median",
    "annual_cost_above_p75", "one_time_recovery", "annual_run_rate", "income_opportunity",
]


# ---------------------------------------------------------------------------
# Compensation formulas (CLAUDE.md glossary). Work on single numbers or whole columns.
# ---------------------------------------------------------------------------
def investable_balance(collected, reserve_pct):
    """Collected balance after the bank's reserve haircut."""
    return collected * (1 - reserve_pct)


def earnings_allowance(collected, reserve_pct, ecr, days, day_basis):
    """Earnings credit = investable balance x ECR x (days in month / day basis)."""
    return investable_balance(collected, reserve_pct) * ecr * days / day_basis


def balance_required(analyzed_fees, reserve_pct, ecr, days, day_basis):
    """Collected balance needed for the earnings credit to exactly cover analyzed fees."""
    return analyzed_fees / (ecr * days / day_basis) / (1 - reserve_pct)


def opportunity_cost(excess_balance, days, yield_rate=None):
    """Income the excess balance could earn elsewhere (money market fund) in the month."""
    rate = config.MONEY_MARKET_YIELD if yield_rate is None else yield_rate
    return np.maximum(excess_balance, 0) * rate * days / 365


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_inputs():
    """Everything the analysis needs, as text tables straight from disk."""
    raw = config.RAW_DIR
    return {
        "statements": pd.concat([_read(p) for p in sorted(raw.glob("statements_*.csv"))], ignore_index=True),
        "schedules": pd.concat([_read(p) for p in sorted(raw.glob("price_schedule_*.csv"))], ignore_index=True),
        "balances": _read(raw / "balances.csv"),
        "terms": _read(raw / "bank_terms.csv"),
        "mapping": _read(config.PROCESSED_DIR / "code_mapping.csv"),
        "benchmarks": _read(config.REFERENCE_DIR / "benchmark_prices.csv"),
        "afp_ref": _read(config.REFERENCE_DIR / "afp_codes_sample.csv"),
    }


def prepare_lines(inputs):
    """Statement lines with numbers parsed and the mapped AFP code and family attached."""
    lines = inputs["statements"].copy()
    lines[MONTH] = pd.to_datetime(lines[MONTH])
    for col in ("volume", "unit_price", "total_charge"):
        lines[col] = lines[col].astype(float)
    lines["ecr_eligible"] = lines["ecr_eligible"] == "True"
    mapping = inputs["mapping"][["bank", "bank_service_code", "mapped_afp_code", "method"]]
    lines = lines.merge(mapping, on=["bank", "bank_service_code"], how="left", validate="many_to_one")
    lines = lines.rename(columns={"mapped_afp_code": "afp", "method": "mapping_method"})
    ref = inputs["afp_ref"].set_index("afp_code")
    lines["afp_description"] = lines["afp"].map(ref["afp_description"]).fillna("Unmapped")
    lines["family_code"] = lines["afp"].map(ref["family_code"]).fillna("??")
    lines["family_name"] = lines["afp"].map(ref["family_name"]).fillna("Unmapped")
    lines["line_id"] = np.arange(len(lines))  # stable row number, for tracing findings back
    return lines


def prepare_terms(inputs):
    terms = inputs["terms"].copy()
    terms[MONTH] = pd.to_datetime(terms[MONTH])
    terms["ecr"] = terms["ecr"].astype(float)
    terms["reserve_pct"] = terms["reserve_pct"].astype(float)
    terms["day_basis"] = terms["day_basis"].astype(int)
    terms["ecr_carryover"] = terms["ecr_carryover"] == "True"
    return terms


def prepare_balances(inputs):
    bal = inputs["balances"].copy()
    bal[MONTH] = pd.to_datetime(bal[MONTH])
    for col in ("avg_ledger_balance", "avg_collected_balance"):
        bal[col] = bal[col].astype(float)
    return bal


def without_duplicates(lines):
    """Lines with exact duplicate rows removed (the duplicates are refunded separately by A4)."""
    key = ["bank", "account_id", MONTH, "bank_service_code", "volume", "unit_price", "total_charge"]
    return lines.drop_duplicates(subset=key, keep="first")


# ---------------------------------------------------------------------------
# Output 1: fee summary
# ---------------------------------------------------------------------------
def fee_summary(lines):
    """Fees by bank, month, AFP family, and service, split into ECR-eligible and not."""
    df = lines.assign(
        ecr_eligible_fees=np.where(lines["ecr_eligible"], lines["total_charge"], 0.0),
        non_offsettable_fees=np.where(lines["ecr_eligible"], 0.0, lines["total_charge"]),
    )
    keys = ["bank", MONTH, "family_code", "family_name", "afp", "afp_description", "bank_description"]
    return (df.groupby(keys, as_index=False)
              .agg(volume=("volume", "sum"), total_charge=("total_charge", "sum"),
                   ecr_eligible_fees=("ecr_eligible_fees", "sum"),
                   non_offsettable_fees=("non_offsettable_fees", "sum"))
              .rename(columns={"afp": "afp_code"}))


# ---------------------------------------------------------------------------
# Output 2: compensation (earnings credit vs. fees)
# ---------------------------------------------------------------------------
def apply_earnings_credit(comp):
    """Offset analyzed fees with the earnings allowance, month by month for each bank.

    Unused credit normally expires at month-end. If a bank's carryover flag is on,
    unused credit rolls forward to the next month instead.
    """
    comp = comp.sort_values(["bank", MONTH]).copy()
    carried_in, shortfall = [], []
    for _, bank_rows in comp.groupby("bank", sort=False):
        carry = 0.0
        for row in bank_rows.itertuples():
            available = row.earnings_allowance + carry
            carried_in.append(carry)
            shortfall.append(max(0.0, row.analyzed_fees - available))
            carry = max(0.0, available - row.analyzed_fees) if row.ecr_carryover else 0.0
    comp["credit_carried_in"] = carried_in
    comp["earnings_shortfall"] = shortfall  # analyzed fees NOT covered by earnings credit
    comp["net_service_charge"] = comp["earnings_shortfall"] + comp["hard_charges"]
    return comp


def compensation(lines, balances, terms):
    """Per bank per month: balances, earnings allowance, fees, net charge, and excess balance."""
    bal = balances.groupby(["bank", MONTH], as_index=False)[["avg_ledger_balance", "avg_collected_balance"]].sum()
    fees = (lines.assign(analyzed=np.where(lines["ecr_eligible"], lines["total_charge"], 0.0),
                         hard=np.where(lines["ecr_eligible"], 0.0, lines["total_charge"]))
                 .groupby(["bank", MONTH], as_index=False)
                 .agg(analyzed_fees=("analyzed", "sum"), hard_charges=("hard", "sum")))
    comp = bal.merge(fees, on=["bank", MONTH], how="left").merge(terms, on=["bank", MONTH], how="left")
    comp["days"] = comp[MONTH].dt.days_in_month
    comp["investable_balance"] = investable_balance(comp["avg_collected_balance"], comp["reserve_pct"])
    comp["earnings_allowance"] = earnings_allowance(comp["avg_collected_balance"], comp["reserve_pct"],
                                                    comp["ecr"], comp["days"], comp["day_basis"])
    comp = apply_earnings_credit(comp)
    comp["balance_required"] = balance_required(comp["analyzed_fees"], comp["reserve_pct"],
                                                comp["ecr"], comp["days"], comp["day_basis"])
    comp["excess_balance"] = comp["avg_collected_balance"] - comp["balance_required"]
    comp["opportunity_cost"] = opportunity_cost(comp["excess_balance"], comp["days"])
    cols = ["bank", MONTH, "avg_ledger_balance", "avg_collected_balance", "reserve_pct", "investable_balance",
            "ecr", "day_basis", "days", "ecr_carryover", "earnings_allowance", "credit_carried_in",
            "analyzed_fees", "hard_charges", "earnings_shortfall", "net_service_charge",
            "balance_required", "excess_balance", "opportunity_cost"]
    return comp[cols].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Finding helpers
# ---------------------------------------------------------------------------
CATEGORIES = {
    "A1": "Unannounced price increase",
    "A2": "Above-benchmark pricing",
    "A3": "Billing for unused service",
    "A4": "Duplicate charge",
    "A5": "Tier pricing misapplied",
    "A6": "Billing math error",
    "A7": "ECR cut created cash fees",
    "A8": "Excess idle balances",
}
ONE_TIME, RUN_RATE, INCOME = "One-time recovery", "Annual run-rate", "Income opportunity"


def month_list(months):
    return ";".join(pd.Timestamp(m).strftime("%Y-%m") for m in sorted(set(months)))


def month_name(m):
    return pd.Timestamp(m).strftime("%b %Y")


def finding(rule, bank, account_id, afp_code, service, months, savings_type, monthly_impact,
            evidence, action, one_time=0.0):
    """One exceptions row. Run-rate and income findings are annualized as monthly x 12."""
    annualized = monthly_impact * config.MONTHS_PER_YEAR if savings_type != ONE_TIME else 0.0
    return {
        "rule": rule, "category": CATEGORIES[rule], "bank": bank, "account_id": account_id,
        "afp_code": afp_code, "service": service, "months_affected": month_list(months),
        "savings_type": savings_type, "monthly_impact": monthly_impact,
        "annualized_impact": annualized, "one_time_recovery": one_time,
        "dollar_impact": annualized if savings_type != ONE_TIME else one_time,
        "evidence": evidence, "recommended_action": action,
    }


def consecutive_runs(months, min_length):
    """Months that sit in a run of at least min_length back-to-back calendar months."""
    months = sorted(pd.Timestamp(m) for m in set(months))
    keep, run = [], []
    for m in months:
        if run and m != run[-1] + pd.offsets.MonthBegin(1):
            keep += run if len(run) >= min_length else []
            run = []
        run.append(m)
    keep += run if len(run) >= min_length else []
    return keep


# ---------------------------------------------------------------------------
# A4 duplicates, A5 tier errors, A6 math errors  (one-time refunds)
# ---------------------------------------------------------------------------
def detect_duplicates(lines):
    """A4: the same account, month, service, volume, and price billed more than once."""
    key = ["bank", "account_id", MONTH, "bank_service_code", "volume", "unit_price", "total_charge"]
    counts = lines.groupby(key + ["afp", "bank_description"]).size().reset_index(name="times_billed")
    out = []
    for row in counts[counts["times_billed"] > 1].itertuples():
        refund = (row.times_billed - 1) * row.total_charge
        out.append(finding(
            "A4", row.bank, row.account_id, row.afp, row.bank_description, [row.statement_month], ONE_TIME, 0.0,
            f"{row.bank_description} on {row.account_id} billed {row.times_billed} times in "
            f"{month_name(row.statement_month)}: {row.volume:,.0f} x ${row.unit_price:,.3f} = "
            f"${row.total_charge:,.2f} each. Refund = {row.times_billed - 1} x ${row.total_charge:,.2f} "
            f"= ${refund:,.2f}.",
            "Request a refund of the duplicate charge and ask the bank to confirm the billing fix.",
            one_time=refund))
    return out


def tier_table(schedules):
    """Tiered prices from the banks' price lists: one row per bank, service code, and tier."""
    tiers = schedules[schedules["price_tier"] != ""].copy()
    tiers["tier_min_volume"] = tiers["tier_min_volume"].astype(float)
    tiers["tier_max_volume"] = pd.to_numeric(tiers["tier_max_volume"], errors="coerce")  # blank = no cap
    tiers["unit_price"] = tiers["unit_price"].astype(float)
    return tiers


def correct_tier(tiers_for_code, volume):
    """The tier whose volume range contains the bank-wide monthly volume."""
    for t in tiers_for_code.itertuples():
        if volume >= t.tier_min_volume and (pd.isna(t.tier_max_volume) or volume <= t.tier_max_volume):
            return t
    return None


def detect_tier_errors(lines, schedules):
    """A5: volume qualified for a cheaper tier, but the line was billed at another tier's price."""
    tiers, out = tier_table(schedules), []
    clean = without_duplicates(lines)
    for (bank, code), tiers_for_code in tiers.groupby(["bank", "bank_service_code"]):
        svc = clean[(clean["bank"] == bank) & (clean["bank_service_code"] == code)]
        other_prices = set(tiers_for_code["unit_price"])
        months, overcharge, examples = [], 0.0, []
        for month, month_lines in svc.groupby(MONTH):
            bank_volume = month_lines["volume"].sum()
            right = correct_tier(tiers_for_code, bank_volume)
            wrong = month_lines[(month_lines["unit_price"] > right.unit_price + 1e-9)
                                & month_lines["unit_price"].isin(other_prices)]
            if len(wrong):
                extra = round((wrong["volume"] * (wrong["unit_price"] - right.unit_price)).sum(), 2)  # refund in cents
                months.append(month)
                overcharge += extra
                examples.append(f"{month_name(month)}: {bank_volume:,.0f} items qualify for {right.price_tier} "
                                f"at ${right.unit_price:.3f} but billed ${wrong['unit_price'].iloc[0]:.3f} "
                                f"(${extra:,.2f} over)")
        if months:
            first = svc.iloc[0]
            out.append(finding(
                "A5", bank, "ALL", first["afp"], first["bank_description"], months, ONE_TIME, 0.0,
                "; ".join(examples) + f". Total overbilled ${overcharge:,.2f}.",
                "Request a refund of the tier difference and ask the bank to correct its tier setup.",
                one_time=overcharge))
    return out


def detect_math_errors(lines):
    """A6: billed total does not equal volume x unit price (more than a cent apart)."""
    df = without_duplicates(lines).copy()
    df["volume_x_price"] = df["volume"] * df["unit_price"]
    df["difference"] = df["total_charge"] - df["volume_x_price"]
    out = []
    for row in df[df["difference"].abs() > config.MATH_ERROR_TOLERANCE].itertuples():
        over = max(row.difference, 0.0)
        direction = "overbilled" if row.difference > 0 else "underbilled"
        out.append(finding(
            "A6", row.bank, row.account_id, row.afp, row.bank_description, [row.statement_month], ONE_TIME, 0.0,
            f"{month_name(row.statement_month)}: {row.volume:,.0f} x ${row.unit_price:,.3f} = "
            f"${row.volume_x_price:,.2f}, but the statement shows ${row.total_charge:,.2f} "
            f"({direction} by ${abs(row.difference):,.2f}).",
            "Request a refund of the overbilled amount." if over else "No refund due; note the error to the bank.",
            one_time=over))
    return out


# ---------------------------------------------------------------------------
# A3 unused services  (cancel - annual run-rate)
# ---------------------------------------------------------------------------
def detect_unused_services(lines):
    """A3: a monthly module fee billed while the account shows no related activity for 3+ months.

    Returns (findings, cancelled) where cancelled is the set of (bank, account, AFP code)
    lines we recommend cancelling - later price rules skip these.
    """
    clean = without_duplicates(lines)
    latest = clean[MONTH].max()
    out, cancelled = [], set()
    for module, related in config.RELATED_ACTIVITY.items():
        for (bank, acct), fees in clean[clean["afp"] == module].groupby(["bank", "account_id"]):
            acct_lines = clean[(clean["bank"] == bank) & (clean["account_id"] == acct)]
            active = set(acct_lines.loc[acct_lines["afp"].isin(related) & (acct_lines["volume"] > 0), MONTH])
            idle = [m for m in fees[MONTH].unique() if m not in active]
            flagged = consecutive_runs(idle, config.UNUSED_SERVICE_MIN_MONTHS)
            if not flagged:
                continue
            charges = fees[fees[MONTH].isin(flagged)].groupby(MONTH)["total_charge"].sum()
            still_idle = latest in flagged
            current = charges.iloc[-1] if still_idle else 0.0
            if still_idle:
                cancelled.add((bank, acct, module))
            out.append(finding(
                "A3", bank, acct, module, fees["bank_description"].iloc[0], flagged, RUN_RATE, current,
                f"{fees['bank_description'].iloc[0]} billed on {acct} for {len(flagged)} months "
                f"({month_name(flagged[0])} - {month_name(flagged[-1])}) with zero related activity "
                f"(AFP {', '.join(related)}). ${charges.sum():,.2f} billed while unused; "
                f"current charge ${current:,.2f}/month x 12 = ${current * 12:,.2f}/year.",
                "Cancel the module (or close the account if it is no longer needed) and ask for a credit "
                "of fees billed while it was unused."))
    return out, cancelled


def exclude_cancelled(lines, cancelled):
    """Drop lines for services we already recommend cancelling (so savings are not counted twice)."""
    keys = list(zip(lines["bank"], lines["account_id"], lines["afp"]))
    return lines[[k not in cancelled for k in keys]]


# ---------------------------------------------------------------------------
# A1 price increases  (roll back - annual run-rate)
# ---------------------------------------------------------------------------
def detect_price_increases(lines, schedules, cancelled):
    """A1: the unit price for a bank + AFP code + tier jumps more than 5% from one billing to the next.

    Returns (findings, rollback_prices) where rollback_prices maps (bank, AFP code) to the
    pre-increase price, which the benchmark rule then uses as its starting point.
    """
    clean = exclude_cancelled(without_duplicates(lines), cancelled)
    contract = schedules.assign(unit_price=schedules["unit_price"].astype(float)).set_index(
        ["bank", "bank_service_code", "price_tier"])["unit_price"]
    prices = (without_duplicates(lines).groupby(["bank", "afp", "bank_service_code", "price_tier", MONTH])
              ["unit_price"].median().reset_index().sort_values(MONTH))
    out, rollback = [], {}
    for (bank, afp, code, tier), series in prices.groupby(["bank", "afp", "bank_service_code", "price_tier"]):
        previous = series["unit_price"].shift()
        jumps = series[series["unit_price"] > previous * (1 + config.PRICE_INCREASE_THRESHOLD)]
        for idx in jumps.index:
            old, new, start = previous[idx], series.at[idx, "unit_price"], series.at[idx, MONTH]
            affected = list(series.loc[(series[MONTH] >= start) & (series["unit_price"] >= new - 1e-9), MONTH])
            svc = clean[(clean["bank"] == bank) & (clean["bank_service_code"] == code)
                        & (clean["price_tier"] == tier) & (clean[MONTH].isin(affected))]
            monthly_volume = svc.groupby(MONTH)["volume"].sum().reindex(affected, fill_value=0).mean()
            monthly = (new - old) * monthly_volume
            rollback[(bank, afp)] = old
            contracted = contract.get((bank, code, tier))
            contract_note = (f" The bank's price schedule still shows ${contracted:,.3f}."
                             if contracted is not None and abs(contracted - old) < 1e-9 else "")
            out.append(finding(
                "A1", bank, "ALL", afp, series_label(svc, code), affected, RUN_RATE, monthly,
                f"Unit price rose from ${old:,.3f} to ${new:,.3f} (+{new / old - 1:.1%}) in "
                f"{month_name(start)}.{contract_note} {monthly_volume:,.0f} items/month x "
                f"${new - old:,.3f} = ${monthly:,.2f}/month; x 12 = ${monthly * 12:,.2f}/year.",
                "Ask the bank for the price-change notice; request rollback to the contracted price and a "
                "credit for months billed at the higher price."))
    return out, rollback


def series_label(svc_lines, code):
    return svc_lines["bank_description"].iloc[0] if len(svc_lines) else code


# ---------------------------------------------------------------------------
# Output 3 + A2: price benchmarking
# ---------------------------------------------------------------------------
def price_benchmark(lines, benchmarks, rollback):
    """Each bank's current unit price per AFP code vs. the benchmark and the other banks.

    Current price = volume-weighted price in the latest month (duplicates removed).
    Annual volume = the last 12 months of volume.
    """
    clean = without_duplicates(lines)
    latest = clean[MONTH].max()
    last = clean[clean[MONTH] == latest]
    current = (last.assign(cost=last["volume"] * last["unit_price"])
               .groupby(["bank", "afp"]).agg(cost=("cost", "sum"), vol=("volume", "sum")))
    current["unit_price"] = current["cost"] / current["vol"]
    annual = clean.groupby(["bank", "afp"]).agg(annual_volume=("volume", "sum"),
                                                bank_description=("bank_description", "first"),
                                                afp_description=("afp_description", "first"))
    bench = benchmarks.assign(**{c: benchmarks[c].astype(float) for c in ("p25", "median", "p75")})
    table = (annual.join(current["unit_price"]).reset_index()
             .merge(bench[["afp_code", "unit_basis", "p25", "median", "p75"]],
                    left_on="afp", right_on="afp_code", how="left").drop(columns="afp"))
    table["price_before_increase"] = [rollback.get((b, a), p) for b, a, p in
                                      zip(table["bank"], table["afp_code"], table["unit_price"])]
    table["position"] = np.select(
        [table["unit_price"] > table["p75"], table["unit_price"] > table["median"],
         table["unit_price"] >= table["p25"]],
        ["Above p75", "Median to p75", "p25 to median"], default="Below p25")
    table["annual_cost"] = table["unit_price"] * table["annual_volume"]
    table["annual_cost_above_median"] = np.maximum(table["unit_price"] - table["median"], 0) * table["annual_volume"]
    table["annual_cost_above_p75"] = np.maximum(table["unit_price"] - table["p75"], 0) * table["annual_volume"]
    cheapest = table.loc[table.groupby("afp_code")["unit_price"].idxmin(), ["afp_code", "bank", "unit_price"]]
    cheapest.columns = ["afp_code", "lowest_price_bank", "lowest_price_any_bank"]
    table = table.merge(cheapest, on="afp_code", how="left")
    table["banks_offering"] = table.groupby("afp_code")["bank"].transform("count")
    cols = ["bank", "afp_code", "afp_description", "bank_description", "unit_basis", "unit_price",
            "price_before_increase", "p25", "median", "p75", "position", "annual_volume", "annual_cost",
            "annual_cost_above_median", "annual_cost_above_p75", "banks_offering",
            "lowest_price_any_bank", "lowest_price_bank"]
    return table[cols].sort_values(["afp_code", "bank"]).reset_index(drop=True)


def detect_above_benchmark(lines, benchmark_table, cancelled):
    """A2: price (after rolling back any A1 increase) is above the benchmark p75.

    Savings = (rolled-back price - p75) x annual volume, excluding lines being cancelled under A3.
    """
    clean = exclude_cancelled(without_duplicates(lines), cancelled)
    out = []
    for row in benchmark_table.itertuples():
        base = row.price_before_increase
        if pd.isna(row.p75) or base <= row.p75:
            continue
        annual_volume = clean.loc[(clean["bank"] == row.bank) & (clean["afp"] == row.afp_code), "volume"].sum()
        annual = (base - row.p75) * annual_volume
        out.append(finding(
            "A2", row.bank, "ALL", row.afp_code, row.bank_description, list(clean.loc[
                (clean["bank"] == row.bank) & (clean["afp"] == row.afp_code), MONTH]), RUN_RATE, annual / 12,
            f"Unit price ${base:,.3f} vs illustrative p75 ${row.p75:,.3f} (median ${row.median:,.3f}; "
            f"{base / row.median:.1f}x median). {annual_volume:,.0f} items/year x ${base - row.p75:,.3f} "
            f"= ${annual:,.2f}/year to reach p75 (${(base - row.median) * annual_volume:,.2f} to reach median).",
            f"Negotiate the price to at most ${row.p75:,.2f} (benchmark p75); use the median "
            f"${row.median:,.2f} as the target."))
    return out


# ---------------------------------------------------------------------------
# A7 ECR shortfall and A8 idle balances (from the compensation table)
# ---------------------------------------------------------------------------
def detect_ecr_shortfall(comp, fee_fixes_per_month):
    """A7: in months after a bank cuts its ECR, analyzed fees exceed the earnings allowance.

    Only the part of the shortfall caused by the cut counts: at most the allowance lost
    to the lower rate. Fees already being removed by A1-A3 are taken off first.
    """
    out = []
    for bank, g in comp.sort_values(MONTH).groupby("bank"):
        starting_ecr = g["ecr"].iloc[0]
        cut = g[g["ecr"] < starting_ecr - 1e-12]
        if cut.empty:
            continue
        fixes = fee_fixes_per_month.get(bank, 0.0)
        fees_after_fixes = np.maximum(cut["analyzed_fees"] - fixes, 0)
        allowance_lost = cut["earnings_allowance"] * (starting_ecr / cut["ecr"] - 1)
        caused_by_cut = np.minimum(np.maximum(fees_after_fixes - cut["earnings_allowance"], 0), allowance_lost)
        hit = cut[caused_by_cut > 0.005]
        if hit.empty:
            continue
        impacts = caused_by_cut[caused_by_cut > 0.005]
        still_cut = g[MONTH].max() in set(hit[MONTH])
        monthly = impacts.mean() if still_cut else 0.0
        out.append(finding(
            "A7", bank, "ALL", "", "Earnings credit", list(hit[MONTH]), RUN_RATE, monthly,
            f"ECR fell from {starting_ecr:.2%} to {hit['ecr'].iloc[0]:.2%} in {month_name(hit[MONTH].iloc[0])}. "
            f"Average month since: analyzed fees ${hit['analyzed_fees'].mean():,.2f} "
            f"(${fees_after_fixes[hit.index].mean():,.2f} after removing ${fixes:,.2f}/month counted in other "
            f"findings) vs earnings allowance ${hit['earnings_allowance'].mean():,.2f}. Cash fees caused by "
            f"the cut: ${monthly:,.2f}/month (capped at the ${allowance_lost[hit.index].mean():,.2f}/month "
            f"of credit lost to the lower rate).",
            f"Ask the bank to restore the ECR to {starting_ecr:.2%} or set an ECR floor; otherwise cut "
            f"services or move enough balance to cover fees."))
    return out


def detect_excess_balances(comp):
    """A8: collected balance more than 20% above the balance required, for 3+ months in a row."""
    out = []
    for bank, g in comp.sort_values(MONTH).groupby("bank"):
        over = g[g["excess_balance"] > config.EXCESS_BALANCE_THRESHOLD * g["balance_required"]]
        flagged = consecutive_runs(over[MONTH], config.EXCESS_BALANCE_MIN_MONTHS)
        if not flagged:
            continue
        rows = g[g[MONTH].isin(flagged)]
        monthly = rows["opportunity_cost"].mean()
        avg_excess = rows["excess_balance"].mean()
        out.append(finding(
            "A8", bank, "ALL", "", "Balances", flagged, INCOME, monthly,
            f"{len(flagged)} months with excess balances. Average collected balance "
            f"${rows['avg_collected_balance'].mean():,.0f} vs ${rows['balance_required'].mean():,.0f} "
            f"required to offset fees = ${avg_excess:,.0f} excess "
            f"({(rows['excess_balance'] / rows['balance_required']).mean():.0%} over). Excess earns nothing "
            f"beyond offsetting fees. ${avg_excess:,.0f} x {config.MONEY_MARKET_YIELD:.1%} money market "
            f"yield = ${monthly:,.2f}/month; x 12 = ${monthly * 12:,.2f}/year.",
            f"Move about ${avg_excess:,.0f} (keeping a cushion) to a money market fund or investment sweep, "
            f"and keep balances near the ${rows['balance_required'].mean():,.0f} needed to offset fees."))
    return out


# ---------------------------------------------------------------------------
# Outputs 4 and 5: exceptions and savings summary
# ---------------------------------------------------------------------------
def build_exceptions(findings):
    df = pd.DataFrame(findings)
    df = df.sort_values(["dollar_impact", "rule"], ascending=[False, True]).reset_index(drop=True)
    df.insert(0, "finding_id", [f"F{i:03d}" for i in range(1, len(df) + 1)])
    return df


def savings_summary(exceptions):
    """Savings by category and bank, split into one-time / run-rate / income, plus totals."""
    df = exceptions.assign(
        one_time_recovery=np.where(exceptions["savings_type"] == ONE_TIME, exceptions["one_time_recovery"], 0.0),
        annual_run_rate=np.where(exceptions["savings_type"] == RUN_RATE, exceptions["annualized_impact"], 0.0),
        income_opportunity=np.where(exceptions["savings_type"] == INCOME, exceptions["annualized_impact"], 0.0),
    )
    cols = ["one_time_recovery", "annual_run_rate", "income_opportunity"]
    summary = df.groupby(["rule", "category", "bank"], as_index=False).agg(
        findings=("finding_id", "count"), **{c: (c, "sum") for c in cols})
    total = {"rule": "", "category": "TOTAL", "bank": "All banks", "findings": summary["findings"].sum(),
             **{c: summary[c].sum() for c in cols}}
    return pd.concat([summary, pd.DataFrame([total])], ignore_index=True)


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------
def run_analysis(inputs=None):
    """Run every step in the de-duplication order described at the top of this file."""
    inputs = load_inputs() if inputs is None else inputs
    lines = prepare_lines(inputs)
    comp = compensation(lines, prepare_balances(inputs), prepare_terms(inputs))
    schedules = inputs["schedules"]

    # 1. One-time refunds
    findings = detect_duplicates(lines) + detect_tier_errors(lines, schedules) + detect_math_errors(lines)
    # 2. Cancel unused services
    unused, cancelled = detect_unused_services(lines)
    # 3. Roll back price increases (skipping cancelled lines)
    increases, rollback = detect_price_increases(lines, schedules, cancelled)
    # 4. Above benchmark, measured from the rolled-back price
    bench = price_benchmark(lines, inputs["benchmarks"], rollback)
    above = detect_above_benchmark(lines, bench, cancelled)
    findings += unused + increases + above
    # 5. ECR shortfall after the fee fixes above
    fixes = {}
    for f in unused + increases + above:
        fixes[f["bank"]] = fixes.get(f["bank"], 0.0) + f["monthly_impact"]
    findings += detect_ecr_shortfall(comp, fixes)
    # 6. Idle balances (income, kept separate)
    findings += detect_excess_balances(comp)

    exceptions = build_exceptions(findings)
    return {
        "fee_summary": fee_summary(lines),
        "compensation": comp,
        "price_benchmark": bench,
        "exceptions": exceptions,
        "savings_summary": savings_summary(exceptions),
    }


def write_outputs(results):
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in results.items():
        out = df.copy()
        if MONTH in out:
            out[MONTH] = out[MONTH].dt.strftime("%Y-%m-%d")
        money = [c for c in out.columns if c in MONEY_COLS]
        out[money] = out[money].round(2)
        out.to_csv(config.PROCESSED_DIR / f"{name}.csv", index=False)


def headline(results):
    """Key totals, for the console and the dashboard."""
    total = results["savings_summary"].iloc[-1]
    comp = results["compensation"]
    return {
        "annual_fees": float(comp["analyzed_fees"].sum() + comp["hard_charges"].sum()),
        "annual_earnings_allowance": float(comp["earnings_allowance"].sum()),
        "annual_net_service_charge": float(comp["net_service_charge"].sum()),
        "one_time_recovery": float(total["one_time_recovery"]),
        "annual_run_rate_savings": float(total["annual_run_rate"]),
        "annual_income_opportunity": float(total["income_opportunity"]),
        "findings": int(total["findings"]),
    }


def main():
    results = run_analysis()
    write_outputs(results)
    head = headline(results)
    (config.PROCESSED_DIR / "headline.json").write_text(json.dumps(head, indent=2) + "\n")
    print(f"Annual fees ${head['annual_fees']:,.2f} | earnings allowance ${head['annual_earnings_allowance']:,.2f} "
          f"| net service charges ${head['annual_net_service_charge']:,.2f}")
    print(f"{head['findings']} findings: one-time recovery ${head['one_time_recovery']:,.2f}, "
          f"annual run-rate fee savings ${head['annual_run_rate_savings']:,.2f}, "
          f"idle-cash income opportunity ${head['annual_income_opportunity']:,.2f}/year")
    print(f"Wrote {len(results)} tables to {config.PROCESSED_DIR.relative_to(config.PROJECT_ROOT)}/")


if __name__ == "__main__":
    main()
