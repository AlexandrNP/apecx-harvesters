"""Unit tests for the self-refining harmonization loop's pure control logic (no network)."""
from __future__ import annotations

import os

import pytest

from viral_structure_proxy import ViralResult
from harmonization_refine_loop import diagnose, split_held_out, should_terminate


def _vr(virus, *, gold, name_match, recall_after=1.0, free_text_over=0, stale=False) -> ViralResult:
    return ViralResult(
        virus=virus, taxid="1", pdb_gold=gold, pdb_name_match=name_match, pdb_free_text=gold + free_text_over,
        recall_before=(round(name_match / gold, 4) if gold else None),
        recall_after_realized=recall_after, free_text_overmatch=free_text_over,
        emdb_total=0, emdb_taxid_fill=None, stale_taxid=stale)


def test_diagnose_categorizes_stale_harvest_overmatch():
    results = [
        _vr("Lassa", gold=0, name_match=29, stale=True),                       # stale_taxid -> auto_safe
        _vr("RareBug", gold=50, name_match=40, recall_after=0.70),             # harvest_gap -> gated
        _vr("SARS-CoV", gold=400, name_match=300, free_text_over=5000),        # overmatch -> informational
        _vr("Healthy", gold=200, name_match=180, recall_after=1.0),           # nothing
    ]
    by_virus = {fi.virus: fi for fi in diagnose(results)}
    assert by_virus["Lassa"].category == "stale_taxid" and by_virus["Lassa"].gate == "auto_safe"
    assert by_virus["RareBug"].category == "harvest_gap" and by_virus["RareBug"].gate == "gated"
    assert by_virus["SARS-CoV"].category == "overmatch" and by_virus["SARS-CoV"].gate == "informational"
    assert "Healthy" not in by_virus                                          # no failure -> no item


def test_split_held_out_deterministic_disjoint_and_never_empty_train():
    viruses = [(f"V{i:02d}", f"name{i}", str(i)) for i in range(12)]
    train, held = split_held_out(viruses, every=4)
    assert set(t[0] for t in train).isdisjoint(t[0] for t in held)            # disjoint
    assert len(train) + len(held) == 12                                       # partition
    assert split_held_out(viruses, every=4) == (train, held)                  # deterministic (no RNG)
    assert train and held                                                      # neither empty at K=4


def test_should_terminate_cap_converge_plateau():
    # retry cap
    stop, why = should_terminate([{"auto_safe_pending_train": 5, "applied_refinements": 1}] * 4, max_iters=4)
    assert stop and why == "max_iters"
    # converged: nothing left to auto-fix on TRAIN
    stop, why = should_terminate([{"auto_safe_pending_train": 0, "applied_refinements": 1}], max_iters=9)
    assert stop and "converged" in why
    # plateau: last iteration applied no refinement (and pending>0 so not converged)
    hist = [{"auto_safe_pending_train": 2, "applied_refinements": 1},
            {"auto_safe_pending_train": 2, "applied_refinements": 0}]
    stop, why = should_terminate(hist, max_iters=9)
    assert stop and "plateau" in why
    # keep going: refinements still being applied, pending remains, under cap
    stop, why = should_terminate([{"auto_safe_pending_train": 3, "applied_refinements": 2}], max_iters=9)
    assert not stop


@pytest.mark.skipif(not os.environ.get("APECX_RUN_LIVE"),
                    reason="live RCSB; set APECX_RUN_LIVE=1 to exercise the auto-safe taxid re-derivation")
def test_rederive_taxid_lassa_reclassified():
    from harmonization_refine_loop import rederive_taxid
    # Lassa species 11620 was reclassified; RCSB's current annotation is 3052310 (Mammarenavirus lassaense).
    assert rederive_taxid("Lassa mammarenavirus") == "3052310"
