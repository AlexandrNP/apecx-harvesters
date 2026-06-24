"""Unit tests for the 9-source recall oracle's pure logic (no network, no dictionary).

The live Globus search + dictionary judgment are exercised by the script itself; these pin the
independent-judge field extraction + the recall arithmetic over the pooled gold.
"""
from __future__ import annotations

from recall_oracle import _dest_native_organism, _recall_fractions


def test_dest_native_organism_reads_nested_source_container():
    # DEST records nest the source-native fields under a source-name container; the organism judge
    # field is independent of the top-level harmonized subjects.valueUri.
    content = {"subjects": [{"valueUri": "http://purl.obolibrary.org/obo/NCBITaxon_11320"}],
               "bvbrc_genome": {"Species": "Influenza A virus", "Genome": "..."}}
    assert _dest_native_organism(content, "bvbrc_genome") == "Influenza A virus"


def test_dest_native_organism_missing_or_blank_returns_none():
    assert _dest_native_organism({"bvbrc_genome": {}}, "bvbrc_genome") is None      # field absent
    assert _dest_native_organism({"bvbrc_genome": {"Species": "  "}}, "bvbrc_genome") is None  # blank
    assert _dest_native_organism({}, "bvbrc_genome") is None                        # container absent


def test_recall_fractions_before_after_over_independent_gold():
    # gold = {a,b,c,d} (judged relevant by native organism, independent of the filters). raw substring
    # caught {a}; harmonized caught {a,b,c} -> recall_before 0.25, recall_after 0.75 (the harmonization win).
    before, after = _recall_fractions(raw_ids={"a", "x"}, harm_ids={"a", "b", "c"},
                                      gold_ids={"a", "b", "c", "d"})
    assert before == 0.25 and after == 0.75


def test_recall_fractions_empty_gold_is_none_not_zero():
    assert _recall_fractions({"a"}, {"a"}, set()) == (None, None)   # no gold -> undefined, no ZeroDivision
