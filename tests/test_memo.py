"""Tests for the negotiation memo: what is sent to Claude, the dollar-figure check, and caching.

Uses a stand-in for Claude (FakeClient), so these run free and offline.
"""

import json
from types import SimpleNamespace

import pytest

from src import config
from src import memo


class FakeClient:
    def __init__(self, text):
        self.text, self.calls, self.messages = text, 0, self

    def create(self, **kwargs):
        self.calls += 1
        self.last_request = kwargs
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], stop_reason="end_turn")


BANK = "Riverbend Trust Company"


@pytest.fixture(scope="module")
def summary():
    return memo.build_summary(BANK)


# ---------------------------------------------------------------------------
# What Claude receives
# ---------------------------------------------------------------------------
def test_summary_has_findings_but_no_raw_rows(summary):
    assert set(summary) == {"company", "bank", "period", "relationship", "totals", "findings", "notes"}
    assert len(summary["findings"]) == 5
    text = json.dumps(summary)
    assert "statement_month" not in text and "unit_price" not in text  # no raw statement columns
    assert all(f["calculation"] and f["suggested_action"] for f in summary["findings"])


def test_summary_only_covers_one_bank(summary):
    text = json.dumps(summary)
    assert "Prairie National Bank" not in text and "Summit Commercial Bank" not in text


def test_every_amount_is_preformatted(summary):
    for f in summary["findings"]:
        amounts = [v for k, v in f.items() if k in ("annual_savings", "one_time_refund", "annual_income_opportunity")]
        assert len(amounts) == 1 and amounts[0].startswith("$")


# ---------------------------------------------------------------------------
# Dollar-figure check
# ---------------------------------------------------------------------------
def test_figures_from_the_summary_pass(summary):
    fees = summary["relationship"]["annual_fees"]
    text = f"Annual fees were {fees}; cancel the module to save {summary['findings'][0]['annual_savings']}."
    assert memo.check_figures(text, summary)["mismatches"] == []


def test_rounded_figures_pass():
    source = {"x": "$5,579.86 and $1,145,346 and $0.580"}
    result = memo.check_figures("About $5,580, roughly $1.1M, at $0.58 each.", source)
    assert result == {"checked": 3, "mismatches": []}


def test_invented_or_added_up_figures_are_flagged():
    source = {"x": "$1,000 and $2,000"}
    result = memo.check_figures("Together that is $3,000, plus a $9.99 fee.", source)
    assert result["mismatches"] == ["$3,000", "$9.99"]


# ---------------------------------------------------------------------------
# Drafting and caching
# ---------------------------------------------------------------------------
def test_memo_is_drafted_checked_and_cached(summary, tmp_path):
    client = FakeClient(f"# Memo\n\n## Relationship overview\nFees {summary['relationship']['annual_fees']}. "
                        "An invented $123,456 figure.")
    record = memo.generate_memo(BANK, client=client, folder=tmp_path)
    assert client.calls == 1
    assert record["figure_check"]["mismatches"] == ["$123,456"]
    assert (tmp_path / "riverbend.json").exists() and (tmp_path / "riverbend.md").exists()
    # The prompt carries the summary as JSON, not raw rows.
    sent = client.last_request["messages"][0]["content"]
    assert '"findings"' in sent and "statement_month" not in sent

    again = memo.generate_memo(BANK, client=client, folder=tmp_path)
    assert client.calls == 1, "an up-to-date cached memo is reused"
    assert again["memo_markdown"] == record["memo_markdown"]

    memo.generate_memo(BANK, client=client, folder=tmp_path, force=True)
    assert client.calls == 2, "force redrafts"


def test_no_api_key_and_no_cache_returns_none(tmp_path):
    assert memo.generate_memo(BANK, client=None, folder=tmp_path) is None


def test_stale_memo_is_detected(summary, tmp_path):
    record = memo.generate_memo(BANK, client=FakeClient("# Memo"), folder=tmp_path)
    assert not memo.is_stale(record, summary)
    changed = json.loads(json.dumps(summary))
    changed["totals"]["annual_fee_savings"] = "$1"
    assert memo.is_stale(record, changed)


def test_cached_memos_pass_the_figure_check():
    """The memos shipped with the app must have no unverified dollar figures."""
    cached = [memo.load_memo(b) for b in memo.all_banks()]
    if any(r is None for r in cached):
        pytest.skip("Memos not generated yet - run `python -m src.memo`")
    for record in cached:
        check = memo.check_figures(record["memo_markdown"], memo.build_summary(record["bank"]))
        assert check["mismatches"] == [], (record["bank"], check["mismatches"])
