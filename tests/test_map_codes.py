"""Tests for AFP code mapping.

Most tests use a stand-in for Claude (FakeClient) so they run free and offline.
The accuracy test uses the real cached answers in data/cache/afp_mappings.json.
Run with `pytest -s` to see the accuracy printout.
"""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from src import config
from src import map_codes as mc


class FakeClient:
    """Pretends to be the Anthropic client: returns canned text answers in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        text = self.answers.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def good_answer(code="050100", confidence=0.92):
    return json.dumps({"afp_code": code, "afp_description": "x", "confidence": confidence,
                       "reasoning": "Lockbox item."})


@pytest.fixture(scope="module")
def reference():
    return mc.load_reference()


@pytest.fixture(scope="module")
def descriptions():
    return mc.load_descriptions()


def one_description(bank, description, shown=""):
    return pd.DataFrame([{"bank": bank, "bank_service_code": "X1", "bank_description": description,
                          "afp_code_on_statement": shown}])


# ---------------------------------------------------------------------------
# Rules that need no AI
# ---------------------------------------------------------------------------
def test_provided_codes_are_used_as_is(descriptions, reference, tmp_path):
    provided = descriptions[descriptions["afp_code_on_statement"] != ""]
    mapping = mc.map_all(provided, reference, cache_path=tmp_path / "c.json", client=None)
    assert (mapping["method"] == "provided").all()
    assert (mapping["mapped_afp_code"] == mapping["afp_code_on_statement"]).all()
    assert not mapping["needs_review"].any()


def test_fuzzy_accepts_close_wording(reference, tmp_path):
    mapping = mc.map_all(one_description("Test Bank", "ACH Originated Credit"), reference,
                         cache_path=tmp_path / "c.json", client=None)
    row = mapping.iloc[0]
    assert row["method"] == "fuzzy" and row["mapped_afp_code"] == "250101"


def test_fuzzy_rejects_weak_matches(reference):
    # Scores ~81 against "Deposited Item Transit" - wrong code, correctly below the 90 cut-off.
    assert mc.fuzzy_match("Deposit Item - Summit Drawn", reference)[1] < config.FUZZY_ACCEPT_SCORE


def test_no_key_and_no_cache_flags_for_review(reference, tmp_path):
    mapping = mc.map_all(one_description("Summit Commercial Bank", "LockPoint Remittance Item"),
                         reference, cache_path=tmp_path / "c.json", client=None)
    row = mapping.iloc[0]
    assert row["method"] == "unmapped" and row["needs_review"] and row["mapped_afp_code"] == ""


# ---------------------------------------------------------------------------
# Claude answer handling (using the stand-in client)
# ---------------------------------------------------------------------------
def test_answer_is_cached_and_reused(reference, tmp_path):
    cache = tmp_path / "c.json"
    client = FakeClient([good_answer()])
    desc = one_description("Summit Commercial Bank", "LockPoint Remittance Item")
    first = mc.map_all(desc, reference, cache_path=cache, client=client)
    second = mc.map_all(desc, reference, cache_path=cache, client=client)
    assert client.calls == 1, "second run must use the cache"
    assert first.iloc[0]["method"] == second.iloc[0]["method"] == "llm"
    assert second.iloc[0]["mapped_afp_code"] == "050100"


def test_malformed_json_is_retried_once(reference, tmp_path):
    client = FakeClient(["not json at all", good_answer()])
    mapping = mc.map_all(one_description("Summit Commercial Bank", "LockPoint Remittance Item"),
                         reference, cache_path=tmp_path / "c.json", client=client)
    assert client.calls == 2
    assert mapping.iloc[0]["mapped_afp_code"] == "050100"


def test_two_bad_answers_flag_for_review_without_crashing(reference, tmp_path):
    client = FakeClient(["{bad", json.dumps({"afp_code": "999999", "afp_description": "", "confidence": 1,
                                              "reasoning": ""})])
    mapping = mc.map_all(one_description("Summit Commercial Bank", "LockPoint Remittance Item"),
                         reference, cache_path=tmp_path / "c.json", client=client)
    row = mapping.iloc[0]
    assert row["needs_review"] and row["mapped_afp_code"] == ""
    assert not (tmp_path / "c.json").exists(), "a failed answer must not be cached"


def test_low_confidence_is_flagged(reference, tmp_path):
    client = FakeClient([good_answer(confidence=0.55)])
    mapping = mc.map_all(one_description("Summit Commercial Bank", "LockPoint Remittance Item"),
                         reference, cache_path=tmp_path / "c.json", client=client)
    assert mapping.iloc[0]["needs_review"]


def test_parse_answer_rejects_out_of_range_confidence(reference):
    with pytest.raises(ValueError):
        mc.parse_answer(good_answer(confidence=1.4), reference)


# ---------------------------------------------------------------------------
# Accuracy against the answer key (uses the real cached Claude answers)
# ---------------------------------------------------------------------------
def test_mapping_accuracy_against_answer_key(descriptions):
    if mc.pending_llm_items(descriptions):
        pytest.skip("Some descriptions have no cached Claude answer yet - run `python -m src.map_codes`")
    mapping = mc.map_all(descriptions, client=None)  # cache only, no API calls
    report, merged = mc.accuracy_report(mapping)
    print(f"\nMapping accuracy: {report['overall_accuracy']:.1%} overall, "
          f"{report['accuracy_where_code_was_blank']:.1%} where the code was blank")
    for method, stats in report["by_method"].items():
        print(f"  {method:<9} {stats['count']:>3} descriptions, {stats['accuracy']:.1%} correct")
    wrong = merged[~merged["correct"]]
    for row in wrong.itertuples():
        print(f"  WRONG: {row.bank_description!r} -> {row.mapped_afp_code or '(none)'} "
              f"(true {row.true_afp_code}, confidence {row.confidence})")
    assert report["by_method"]["provided"]["accuracy"] == 1.0
    assert report["overall_accuracy"] >= 0.90
