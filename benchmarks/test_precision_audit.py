"""Unit tests for the precision audit's pure logic (no network, no LLM, no dictionary).

The live facet + LLM judge are exercised by the script itself; these pin the JSON parsing the judge
relies on and the bucket-tally routing (belongs / suspect / advisory-flagged).
"""
from __future__ import annotations

import os

import pytest

from precision_audit import _extract_json, _tally_buckets


def test_extract_json_lenient_parses_first_object():
    assert _extract_json('{"belongs": true, "reason": "ok"}')["belongs"] is True
    # tolerate prose around the JSON (small models often add it)
    assert _extract_json('Sure!\n{"belongs": false, "reason": "different species"}\nHope that helps')["belongs"] is False
    assert _extract_json("no json here") is None
    assert _extract_json("{not valid json}") is None


def test_tally_buckets_routes_and_scales_to_counts():
    buckets = [("Dengue virus 2", 100),      # classify True -> belongs (no judge)
               ("Zika virus", 30),            # classify False -> suspect -> judge says not-belong -> flagged
               ("Unknownia sp.", 7)]          # classify None -> suspect -> judge says belong -> not flagged
    classify = lambda org: True if org == "Dengue virus 2" else (False if org == "Zika virus" else None)
    judge = lambda org: {"belongs": False, "reason": "different flavivirus"} if org == "Zika virus" \
        else {"belongs": True, "reason": "ok"}
    belongs, suspect, flagged, unresolved, false_orgs = _tally_buckets(buckets, classify, judge, run_llm=True)
    assert belongs == 100                      # scaled to the bucket count, not 1
    assert suspect == 37                        # 30 + 7
    assert flagged == 30                        # only the LLM-disputed bucket, by its count
    assert unresolved == 0
    assert false_orgs == [("Zika virus", 30, "different flavivirus")]


def test_tally_buckets_judge_error_counts_unresolved_not_flagged():
    # belongs=None (LLM errored) -> unresolved, NOT flagged, NOT clean. This is the guard against an
    # unreachable Ollama silently reporting perfect precision.
    buckets = [("Zika virus", 30)]
    belongs, suspect, flagged, unresolved, false_orgs = _tally_buckets(
        buckets, classify=lambda o: False, judge=lambda o: {"belongs": None, "reason": "LLM error"},
        run_llm=True)
    assert (belongs, suspect, flagged, unresolved, false_orgs) == (0, 30, 0, 30, [])


def test_tally_buckets_no_llm_flags_nothing():
    # run_llm=False -> suspects are counted but never flagged (dict-only mode).
    buckets = [("Zika virus", 30)]
    belongs, suspect, flagged, unresolved, false_orgs = _tally_buckets(
        buckets, classify=lambda o: False, judge=lambda o: {"belongs": False, "reason": "x"}, run_llm=False)
    assert (belongs, suspect, flagged, unresolved, false_orgs) == (0, 30, 0, 0, [])


@pytest.mark.skipif(not os.environ.get("APECX_RUN_LIVE"), reason="live Ollama; set APECX_RUN_LIVE=1")
def test_llm_judge_live_distinguishes_belong_vs_related():
    from precision_audit import llm_judge
    assert llm_judge("Dengue virus 2", "Dengue virus")["belongs"] is True      # serotype belongs
    assert llm_judge("Zika virus", "Dengue virus")["belongs"] is False         # related, different
