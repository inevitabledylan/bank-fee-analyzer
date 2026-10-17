"""Checks on the synthetic data: shape, sanity, and that every planted anomaly is really there.

These tests check the data itself with independent calculations. They do not
test the analysis engine (that comes in Phase 4).
"""

import pandas as pd
import pytest

from src import config
from src import generate_data as gd


@pytest.fixture(scope="module")
def data():
    return gd.build_dataset()


@pytest.fixture(scope="module")
def statements(data):
    return pd.concat(data["statements"].values(), ignore_index=True)


@pytest.fixture(scope="module")
def mapping(data):
    """True AFP code for every (bank, bank_service_code)."""
    m = data["mapping_key"]
    return dict(zip(zip(m["bank"], m["bank_service_code"]), m["true_afp_code"]))


@pytest.fixture(scope="module")
def lines(statements, mapping):
    """Statement lines with the true AFP code attached (Summit leaves many blank)."""
    df = statements.copy()
    df["true_afp"] = [mapping[(b, c)] for b, c in zip(df["bank"], df["bank_service_code"])]
    return df


def key_rows(data, anomaly_id):
    key = data["answer_key"]
    rows = key[key["anomaly_id"] == anomaly_id]
    assert len(rows) > 0, f"{anomaly_id} missing from answer key"
    return rows


def months_of(row):
    """The months listed on an answer-key row (works for both row styles pandas hands back)."""
    months = row.months if hasattr(row, "months") else row["months"]
    return [pd.Timestamp(m) for m in months.split(";")]


# ---------------------------------------------------------------------------
# Structure and sanity
# ---------------------------------------------------------------------------
def test_reproducible():
    first, second = gd.build_dataset(), gd.build_dataset()
    for slug in gd.BANKS:
        pd.testing.assert_frame_equal(first["statements"][slug], second["statements"][slug])
    pd.testing.assert_frame_equal(first["balances"], second["balances"])
    pd.testing.assert_frame_equal(first["answer_key"], second["answer_key"])


def test_statement_schema(data):
    for df in data["statements"].values():
        assert list(df.columns) == gd.STATEMENT_COLUMNS


def test_row_counts(data, statements):
    assert statements["bank"].nunique() == 3
    assert statements["statement_month"].nunique() == 12
    accounts = statements.groupby("bank")["account_id"].nunique().to_dict()
    assert accounts == {"Prairie National Bank": 12, "Summit Commercial Bank": 8, "Riverbend Trust Company": 5}
    assert len(data["balances"]) == 25 * 12
    services = statements.groupby("bank")["bank_service_code"].nunique()
    assert services.between(15, 27).all(), services.to_dict()


def test_no_negative_values(statements, data):
    assert (statements[["volume", "unit_price", "total_charge"]] >= 0).all().all()
    assert (data["balances"][["avg_ledger_balance", "avg_collected_balance"]] >= 0).all().all()


def test_totals_reconcile_except_planted_math_errors(data, statements):
    diff = (statements["volume"] * statements["unit_price"] - statements["total_charge"]).abs()
    broken = statements[diff > config.MATH_ERROR_TOLERANCE]
    found = set(zip(broken["account_id"], broken["statement_month"]))
    a6 = key_rows(data, "A6")
    planted = {(r.account_id, months_of(r)[0]) for r in a6.itertuples()}
    assert found == planted


def test_balances_are_realistic(data):
    bal = data["balances"]
    total = bal.groupby("statement_month")["avg_collected_balance"].sum()
    assert total.between(35e6, 60e6).all(), total.to_dict()
    funded = bal[bal["avg_ledger_balance"] > 0]
    float_pct = 1 - funded["avg_collected_balance"] / funded["avg_ledger_balance"]
    assert float_pct.between(0.0199, 0.0601).all()


def test_summit_leaves_most_afp_codes_blank(data):
    schedule = data["price_schedules"]["summit"]
    blank_share = (schedule["afp_code"] == "").mean()
    assert 0.5 <= blank_share <= 0.7


def test_benchmarks_are_labeled_illustrative(data):
    assert data["benchmarks"]["label"].str.contains("Illustrative").all()


def test_every_anomaly_is_in_the_answer_key(data):
    assert set(data["answer_key"]["anomaly_id"]) == {f"A{i}" for i in range(1, 9)}


# ---------------------------------------------------------------------------
# Each planted anomaly exists in the data
# ---------------------------------------------------------------------------
def test_a1_price_increase(data, lines):
    rows = key_rows(data, "A1")
    assert len(rows) == 6
    june, july = pd.Timestamp("2026-06-01"), pd.Timestamp("2026-07-01")
    for row in rows.itertuples():
        svc = lines[(lines["bank"] == row.bank) & (lines["true_afp"] == row.afp_code)]
        before = svc.loc[svc["statement_month"] == june, "unit_price"].unique()
        after = svc.loc[svc["statement_month"] >= july, "unit_price"].unique()
        assert len(before) == 1 and len(after) == 1
        assert 1.12 <= after[0] / before[0] <= 1.20, (row.afp_code, before, after)


def test_a2_above_benchmark(data, lines):
    rows = key_rows(data, "A2")
    assert len(rows) == 8
    p75 = data["benchmarks"].set_index("afp_code")["p75"]
    for row in rows.itertuples():
        prices = lines.loc[(lines["bank"] == row.bank) & (lines["true_afp"] == row.afp_code), "unit_price"]
        assert (prices > p75[row.afp_code]).all()
    # Most of them are at Summit.
    assert (rows["bank"] == "Summit Commercial Bank").sum() >= 4


def test_a3_unused_services(data, lines):
    rows = key_rows(data, "A3")
    assert (rows["bank"] == "Riverbend Trust Company").sum() >= 3
    for row in rows.itertuples():
        months = months_of(row)
        assert len(months) >= 6
        acct = lines[(lines["bank"] == row.bank) & (lines["account_id"] == row.account_id)]
        billed = acct[acct["true_afp"] == row.afp_code]
        assert set(months) <= set(billed["statement_month"]), "module fee should be billed every idle month"
        related = acct[acct["true_afp"].isin(config.RELATED_ACTIVITY[row.afp_code])
                       & acct["statement_month"].isin(months)]
        assert related["volume"].sum() == 0


def test_a4_duplicates(data, statements):
    rows = key_rows(data, "A4")
    cols = ["bank", "account_id", "statement_month", "bank_service_code", "volume", "unit_price"]
    dupes = statements[statements.duplicated(cols, keep="first")]
    found = set(zip(dupes["account_id"], dupes["bank_service_code"], dupes["statement_month"]))
    planted = {(r.account_id, r.bank_service_code, months_of(r)[0]) for r in rows.itertuples()}
    assert found == planted
    assert dupes["statement_month"].nunique() == 2


def test_a5_tier_misapplied(data, lines):
    row = key_rows(data, "A5").iloc[0]
    schedule = data["price_schedules"]["prairie"]
    tiers = schedule[schedule["bank_service_code"] == row["bank_service_code"]].set_index("price_tier")
    tier2_min, tier1_price = tiers.loc["Tier 2", "tier_min_volume"], tiers.loc["Tier 1", "unit_price"]
    svc = lines[(lines["bank"] == row["bank"]) & (lines["bank_service_code"] == row["bank_service_code"])]
    for month in months_of(row):
        m = svc[svc["statement_month"] == month]
        assert m["volume"].sum() >= tier2_min, "volume qualifies for Tier 2"
        assert (m["unit_price"] == tier1_price).all(), "but billed at the Tier 1 price"
    assert len(months_of(row)) == 3


def test_a6_math_errors(data, statements):
    rows = key_rows(data, "A6")
    assert 1 <= len(rows) <= 2
    assert (rows["bank"] == "Summit Commercial Bank").all()


def monthly_compensation(data, statements, bank):
    """Independent re-calculation of earnings allowance vs analyzed fees for one bank."""
    terms = data["bank_terms"].query("bank == @bank").set_index("statement_month")
    collected = data["balances"].query("bank == @bank").groupby("statement_month")["avg_collected_balance"].sum()
    fees = statements.query("bank == @bank and ecr_eligible").groupby("statement_month")["total_charge"].sum()
    out = pd.DataFrame({"collected": collected, "fees": fees}).join(terms)
    days = out.index.days_in_month
    out["allowance"] = out["collected"] * (1 - out["reserve_pct"]) * out["ecr"] * days / out["day_basis"]
    out["required"] = out["fees"] / (out["ecr"] * days / out["day_basis"]) / (1 - out["reserve_pct"])
    return out


def test_a7_ecr_cut_causes_shortfall(data, statements):
    row = key_rows(data, "A7").iloc[0]
    comp = monthly_compensation(data, statements, row["bank"])
    cut_months = months_of(row)
    before = comp[~comp.index.isin(cut_months)]
    after = comp[comp.index.isin(cut_months)]
    assert (before["allowance"] >= before["fees"]).all(), "fees covered before the cut"
    assert (after["allowance"] < after["fees"]).all(), "fees not covered after the cut"
    assert before["ecr"].iloc[-1] > after["ecr"].iloc[0]


def test_a8_excess_balances(data, statements):
    row = key_rows(data, "A8").iloc[0]
    comp = monthly_compensation(data, statements, row["bank"])
    excess_share = (comp["collected"] - comp["required"]) / comp["required"]
    assert (excess_share > config.EXCESS_BALANCE_THRESHOLD).all()


def test_no_accidental_excess_or_shortfall_elsewhere(data, statements):
    """Prairie and Riverbend should not trip the excess-balance rule (only Summit is planted)."""
    for bank in ["Prairie National Bank", "Riverbend Trust Company"]:
        comp = monthly_compensation(data, statements, bank)
        excess_share = (comp["collected"] - comp["required"]) / comp["required"]
        assert (excess_share <= config.EXCESS_BALANCE_THRESHOLD).all(), bank
