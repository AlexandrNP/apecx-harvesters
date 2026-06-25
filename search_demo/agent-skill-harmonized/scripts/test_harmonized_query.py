"""Unit tests for the compound (taxon AND protein) query builder (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harmonized_query as H  # noqa: E402

_RES = {"resolution_path": "fast", "canonical_label": "HIV-1", "synonyms": []}


def test_compound_payload_ands_taxon_and_protein():
    # "HIV protease" on a protein index -> AND(taxon, like protein) — both entities honored, not collapsed.
    q = H._build_query_for_index("bvbrc_protein", _RES, 200, protein_term="protease")
    top = q["filters"][0]
    assert top["type"] == "and"
    subs = top["filters"]
    assert any(s["type"] == "match_any" and s["field_name"] == "Genome" for s in subs)            # taxon
    assert any(s["type"] == "like" and s["field_name"] == "Protein.Product"
               and "protease" in s["value"] for s in subs)                                         # protein


def test_index_without_protein_field_stays_taxon_only():
    # bvbrc_genome has no protein field (a genome isn't a single protein) -> taxon-only even with a protein term.
    q = H._build_query_for_index("bvbrc_genome", _RES, 200, protein_term="protease")
    assert q["filters"][0]["type"] == "match_any"


def test_no_protein_term_stays_single_filter():
    q = H._build_query_for_index("bvbrc_protein", _RES, 200)
    assert q["filters"][0]["type"] == "match_any"


import os  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.skipif(not os.environ.get("APECX_RUN_LIVE"), reason="live Globus; set APECX_RUN_LIVE=1")
def test_compound_narrows_vs_taxon_only_live():
    # On the real bvbrc_protein index, the compound (organism AND protease) must be a SUBSET of taxon-only
    # — it honors the protein without silently dropping it. Uses a Genome value known to carry protease.
    import globus_sdk
    c = globus_sdk.SearchClient()
    idx = "249efe96-14d2-443d-ad47-5621ed43a343"
    facet = {"filters": [{"type": "like", "field_name": "Protein.Product", "value": "*protease*"}],
             "facets": [{"name": "g", "type": "terms", "field_name": "Genome", "size": 1}], "limit": 0}
    g = (c.post_search(idx, facet).data.get("facet_results") or [{}])[0]["buckets"][0]["value"]
    res = {"resolution_path": "fast", "canonical_label": g, "synonyms": []}
    taxon = c.post_search(idx, {**H._build_query_for_index("bvbrc_protein", res, 0), "limit": 0}).data["total"]
    comp = c.post_search(idx, {**H._build_query_for_index("bvbrc_protein", res, 0, protein_term="protease"),
                               "limit": 0}).data["total"]
    assert 0 < comp <= taxon
