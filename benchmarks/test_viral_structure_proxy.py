"""Unit tests for the PDB/EMDB harmonization proxy leg's pure metric derivation.

The live RCSB/EBI fetches are exercised by the script itself (real-data smoke); these pin the
recall/precision arithmetic + the stale-taxid diagnose signal without the network.
"""
from __future__ import annotations

from viral_structure_proxy import _derive_result


def test_recall_before_low_after_realized_high():
    # Dengue-shaped: serotype-named structures invisible to a single-name query (recall_before low),
    # but the harvest stamps the gold sample fully (recall_after high). Separable recall + precision.
    r = _derive_result("Dengue", "12637", gold=349, name_match=22, free_text=380,
                       stamped=60, sample_n=60, emdb_total=100, emdb_tax=93)
    assert r.recall_before == round(22 / 349, 4)      # ~0.063 — name-match recall is terrible
    assert r.recall_after_realized == 1.0              # harvest stamped all 60 sampled gold structures
    assert r.free_text_overmatch == 380 - 349          # 31 precision-cost over-matches
    assert r.emdb_taxid_fill == round(93 / 100, 4)
    assert r.stale_taxid is False


def test_stale_taxid_flagged_when_gold_zero_but_name_matches():
    # Lassa/HCV-shaped: a reclassified species taxid -> lineage gold 0 while name-match still finds
    # records. Must surface as a diagnose signal, never a silent zero.
    r = _derive_result("Lassa", "11620", gold=0, name_match=29, free_text=103,
                       stamped=0, sample_n=0, emdb_total=28, emdb_tax=24)
    assert r.stale_taxid is True
    assert r.recall_before is None                     # no gold denominator -> undefined, not 0
    assert r.recall_after_realized is None             # no sample -> undefined


def test_narrow_taxid_flagged_when_name_match_exceeds_gold():
    # Ebola-shaped: a too-narrow sub-species taxid -> name-match (38) > lineage gold (19), an impossible
    # ordering (name is a subset of lineage) that signals the taxid is wrong. Must flag stale, not emit
    # a nonsensical recall_before > 1.0 into the aggregate.
    r = _derive_result("Ebola", "186538", gold=19, name_match=38, free_text=113,
                       stamped=8, sample_n=8, emdb_total=16, emdb_tax=15)
    assert r.stale_taxid is True


def test_no_emdb_entries_yields_none_fill():
    r = _derive_result("Rift Valley", "11588", gold=32, name_match=0, free_text=42,
                       stamped=10, sample_n=10, emdb_total=0, emdb_tax=0)
    assert r.emdb_taxid_fill is None                   # no EMDB entries -> undefined, no ZeroDivision
    assert r.recall_before == 0.0                      # gold>0, name_match 0 -> genuine 0 recall
    assert r.stale_taxid is False                      # gold>0, so not a stale-taxid case
