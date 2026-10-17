"""Tests for the analysis engine.

1. Hand-calculated checks of the earnings-credit formulas.
2. Every planted anomaly A1-A8 is detected - and nothing else is (no false alarms).
3. Savings are not double-counted.

The answer key is read here only to grade the engine; the engine itself never reads it.
"""

import pandas as pd
import pytest

from src import analyze as an
from src import config


# ---------------------------------------------------------------------------
# 1. Formulas, checked against hand calculations
# ---------------------------------------------------------------------------
def test_earnings_allowance_360_day_basis_with_reserve():
    # $1,000,000 collected, 10% reserve -> $900,000 investable.
    # 900,000 x 2.50% x 30/360 = 900,000 x 0.0020833... = $1,875.00
    assert an.earnings_allowance(1_000_000, 0.10, 0.025, 30, 360) == pytest.approx(1_875.00)


def test_earnings_allowance_365_day_basis_no_reserve():
    # 2,000,000 x 2.75% = 55,000 a year; x 31/365 = $4,671.23
    assert an.earnings_allowance(2_000_000, 0.0, 0.0275, 31, 365) == pytest.approx(4_671.2329, abs=0.001)


def test_balance_required_is_the_inverse_of_earnings_allowance():
    # Fees of $1,875 at 2.50%, 30/360, 10% reserve need exactly $1,000,000 collected.
    assert an.balance_required(1_875.00, 0.10, 0.025, 30, 360) == pytest.approx(1_000_000)
    # $4,671.23 of fees at 2.75%, 31/365, no reserve need ~$2,000,000.
    assert an.balance_required(4_671.2329, 0.0, 0.0275, 31, 365) == pytest.approx(2_000_000, abs=1)


def test_opportunity_cost_ignores_shortfalls():
    # $10M excess x 4% x 30/365 = $32,876.71; a negative excess earns nothing.
    assert an.opportunity_cost(10_000_000, 30, 0.04) == pytest.approx(32_876.71, abs=0.01)
    assert an.opportunity_cost(-500_000, 30, 0.04) == 0


def _two_months(carryover):
    return pd.DataFrame({
        "bank": ["B", "B"], "statement_month": pd.to_datetime(["2026-01-01", "2026-02-01"]),
        "earnings_allowance": [1_000.0, 1_000.0], "analyzed_fees": [600.0, 1_300.0],
        "hard_charges": [50.0, 50.0], "ecr_carryover": [carryover, carryover],
    })


def test_net_service_charge_without_carryover():
    # Jan: fees 600 < credit 1,000 -> 0 + 50 hard = 50. Unused 400 expires.
    # Feb: fees 1,300 - credit 1,000 = 300 + 50 hard = 350.
    out = an.apply_earnings_credit(_two_months(carryover=False))
    assert list(out["net_service_charge"]) == pytest.approx([50.0, 350.0])


def test_net_service_charge_with_carryover():
    # Jan's unused 400 rolls into Feb: 1,300 - (1,000 + 400) = 0 shortfall -> 50 hard only.
    out = an.apply_earnings_credit(_two_months(carryover=True))
    assert list(out["credit_carried_in"]) == pytest.approx([0.0, 400.0])
    assert list(out["net_service_charge"]) == pytest.approx([50.0, 50.0])


# ---------------------------------------------------------------------------
# 2. Planted anomalies are detected, with no false alarms
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def results():
    return an.run_analysis()


@pytest.fixture(scope="module")
def exceptions(results):
    return results["exceptions"]


@pytest.fixture(scope="module")
def answer_key():
    key = pd.read_csv(config.RAW_DIR / "_answer_key.csv", dtype=str, keep_default_na=False)
    key["months"] = key["months"].str.replace(r"(\d{4}-\d{2})-01", r"\1", regex=True)
    return key


def matching_findings(exceptions, key_row):
    found = exceptions[(exceptions["rule"] == key_row.anomaly_id) & (exceptions["bank"] == key_row.bank)
                       & (exceptions["afp_code"].fillna("") == key_row.afp_code)]
    if key_row.account_id != "ALL":
        found = found[found["account_id"] == key_row.account_id]
    return found


@pytest.mark.parametrize("rule", [f"A{i}" for i in range(1, 9)])
def test_every_planted_anomaly_is_detected(rule, exceptions, answer_key):
    rows = answer_key[answer_key["anomaly_id"] == rule]
    assert len(rows) > 0
    for row in rows.itertuples():
        found = matching_findings(exceptions, row)
        assert len(found) == 1, f"{rule} not detected: {row.bank} {row.account_id} {row.afp_code}"
        planted, detected = set(row.months.split(";")), set(found.iloc[0]["months_affected"].split(";"))
        if rule in ("A1", "A3", "A4", "A5", "A6"):
            assert planted == detected, (rule, row.afp_code, planted, detected)
        else:
            assert planted & detected, (rule, planted, detected)
        assert found.iloc[0]["dollar_impact"] > 0


def test_no_false_alarms(exceptions, answer_key):
    detected = exceptions["rule"].value_counts().to_dict()
    planted = answer_key["anomaly_id"].value_counts().to_dict()
    assert detected == planted


def test_every_finding_has_evidence_and_action(exceptions):
    assert (exceptions["evidence"].str.len() > 20).all()
    assert (exceptions["recommended_action"].str.len() > 10).all()


def test_ecr_shortfall_only_flagged_when_the_ecr_changed(results, exceptions):
    comp = results["compensation"]
    prairie = comp[comp["bank"] == "Prairie National Bank"]
    assert (prairie["earnings_shortfall"] > 0).any(), "Prairie has some uncovered months..."
    assert not ((exceptions["rule"] == "A7") & (exceptions["bank"] == "Prairie National Bank")).any(), \
        "...but its ECR never changed, so they are not an A7 finding"


# ---------------------------------------------------------------------------
# 3. No double-counting
# ---------------------------------------------------------------------------
def test_price_increase_skips_services_being_cancelled(results, exceptions):
    # Positive Pay (200010) was both repriced (A1) and is unused on PNB-0007 (A3).
    # The A1 rollback must only count the 5 accounts that keep the service.
    lines = an.prepare_lines(an.load_inputs())
    pp = lines[(lines["bank"] == "Prairie National Bank") & (lines["afp"] == "200010")]
    old = pp.loc[pp["statement_month"] == "2026-06-01", "unit_price"].iloc[0]
    new = pp.loc[pp["statement_month"] == "2026-07-01", "unit_price"].iloc[0]
    kept = pp[(pp["statement_month"] >= "2026-07-01") & (pp["account_id"] != "PNB-0007")]
    expected_annual = (new - old) * kept["volume"].sum() / 3 * 12
    a1 = exceptions[(exceptions["rule"] == "A1") & (exceptions["afp_code"] == "200010")].iloc[0]
    assert a1["annualized_impact"] == pytest.approx(expected_annual)


def test_repriced_services_are_not_also_counted_as_above_benchmark(exceptions):
    a1_codes = set(exceptions.loc[exceptions["rule"] == "A1", "afp_code"])
    a2_prairie = exceptions[(exceptions["rule"] == "A2") & (exceptions["bank"] == "Prairie National Bank")]
    assert not (a1_codes & set(a2_prairie["afp_code"]))


def test_idle_cash_is_kept_out_of_fee_savings(results):
    summary = results["savings_summary"]
    a8 = summary[summary["rule"] == "A8"]
    assert (a8["annual_run_rate"] == 0).all() and (a8["income_opportunity"] > 0).all()


def test_savings_summary_ties_to_exceptions(results):
    exc, summary = results["exceptions"], results["savings_summary"]
    total = summary[summary["category"] == "TOTAL"].iloc[0]
    run_rate = exc.loc[exc["savings_type"] == an.RUN_RATE, "annualized_impact"].sum()
    assert total["annual_run_rate"] == pytest.approx(run_rate)
    assert total["one_time_recovery"] == pytest.approx(exc["one_time_recovery"].sum())
    assert total["findings"] == len(exc)
