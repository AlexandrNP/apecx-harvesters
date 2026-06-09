"""Unit tests for SC-B1 corpus_mining accumulator."""

from __future__ import annotations

import pytest

from apecx_harvesters.pipeline.corpus_mining import (
    MinedSynonymAccumulator,
    MinedSynonymObservation,
)


def test_accumulator_observes_minimum_signal() -> None:
    acc = MinedSynonymAccumulator()
    accepted = acc.observe("EEEV", 11021, source="violin_pathogen")
    assert accepted
    obs = list(acc.observations())
    assert len(obs) == 1
    o = obs[0]
    assert o.surface_form == "EEEV"
    assert o.surface_form_normalized == "eeev"
    assert o.taxon_id == 11021
    assert o.source == "violin_pathogen"


def test_normalization_collapses_whitespace_and_casefolds() -> None:
    acc = MinedSynonymAccumulator()
    acc.observe("Severe Acute   Respiratory  Syndrome", 2697049, source="bvbrc_genome")
    obs = list(acc.observations())
    assert len(obs) == 1
    assert obs[0].surface_form_normalized == "severe acute respiratory syndrome"
    # Original is preserved for provenance.
    assert obs[0].surface_form == "Severe Acute   Respiratory  Syndrome".strip()


def test_observe_dedupes_within_source() -> None:
    """Calling observe twice with the same (surface, taxon, source) is idempotent."""
    acc = MinedSynonymAccumulator()
    acc.observe("EEEV", 11021, source="violin_pathogen")
    acc.observe("eeev", 11021, source="violin_pathogen")  # different case
    acc.observe("EEEV", 11021, source="violin_pathogen")  # exact dup

    obs = list(acc.observations())
    assert len(obs) == 1
    assert acc.unique_pair_count() == 1
    stats = acc.per_source_stats()["violin_pathogen"]
    # Three observations all accepted; only one unique pair.
    assert stats["observed"] == 3
    assert stats["unique_pairs"] == 1


def test_observe_records_multiple_sources_for_same_pair() -> None:
    """One (surface, taxon) seen by N sources yields N observations
    AND the corroboration count == N."""
    acc = MinedSynonymAccumulator()
    acc.observe("EEEV", 11021, source="violin_pathogen")
    acc.observe("EEEV", 11021, source="bvbrc_genome")
    acc.observe("EEEV", 11021, source="antiviraldb")

    obs = list(acc.observations())
    assert len(obs) == 3
    sources = {o.source for o in obs}
    assert sources == {"violin_pathogen", "bvbrc_genome", "antiviraldb"}

    # unique_pairs yields ONE pair with source_count = 3
    pairs = list(acc.unique_pairs())
    assert len(pairs) == 1
    _, count, source_set = pairs[0]
    assert count == 3
    assert source_set == frozenset(
        {"violin_pathogen", "bvbrc_genome", "antiviraldb"}
    )


def test_observations_corroborated_returns_only_multi_source_pairs() -> None:
    acc = MinedSynonymAccumulator()
    acc.observe("EEEV", 11021, source="violin_pathogen")
    acc.observe("EEEV", 11021, source="bvbrc_genome")
    acc.observe("ZIKV", 64320, source="violin_pathogen")  # single source

    corr = list(acc.observations_corroborated(min_sources=2))
    assert len(corr) == 1
    assert corr[0].surface_form_normalized == "eeev"
    # Single-source pair excluded.

    # min_sources=1 returns ALL unique pairs.
    all_pairs = list(acc.observations_corroborated(min_sources=1))
    assert len(all_pairs) == 2


def test_surface_form_conflicts_detects_multi_taxon_surface() -> None:
    """The SC-B3 conflict signal: same surface, different taxa."""
    acc = MinedSynonymAccumulator()
    acc.observe("RSV", 11250, source="violin_pathogen")  # Human RSV
    acc.observe("RSV", 11246, source="bvbrc_genome")     # Bovine RSV
    acc.observe("EEEV", 11021, source="violin_pathogen")  # no conflict

    conflicts = dict(acc.surface_form_conflicts())
    assert "rsv" in conflicts
    assert conflicts["rsv"] == frozenset({11246, 11250})
    assert "eeev" not in conflicts
    assert acc.conflict_count() == 1


def test_observe_rejects_invalid_inputs() -> None:
    acc = MinedSynonymAccumulator()
    rejects = [
        (None, 11021),
        ("", 11021),
        (" ", 11021),
        ("a", 11021),        # below MIN_SURFACE_LEN
        ("nan", 11021),      # pandas null sentinel
        ("None", 11021),
        ("12345", 11021),    # accession-like numeric
        ("123.0", 11021),    # pandas float-string
        ("EEEV", None),
        ("EEEV", 0),
        ("EEEV", -5),
        ("EEEV", "not_an_int"),
    ]
    for surface, taxon in rejects:
        assert not acc.observe(surface, taxon, source="violin_pathogen"), (
            f"should have rejected ({surface!r}, {taxon!r})"
        )

    assert acc.unique_pair_count() == 0
    stats = acc.per_source_stats()["violin_pathogen"]
    assert stats["observed"] == 0
    assert stats["rejected"] == len(rejects)


def test_observe_accepts_string_taxon_id() -> None:
    """Source data often carries the taxon as a string ('11021'); accept it."""
    acc = MinedSynonymAccumulator()
    assert acc.observe("EEEV", "11021", source="violin_pathogen")
    obs = list(acc.observations())
    assert obs[0].taxon_id == 11021


def test_per_source_stats_reports_observed_rejected_unique() -> None:
    acc = MinedSynonymAccumulator()
    acc.observe("EEEV", 11021, source="violin_pathogen")
    acc.observe("ZIKV", 64320, source="violin_pathogen")
    acc.observe("", 11021, source="violin_pathogen")  # rejected
    acc.observe("nan", 11021, source="violin_pathogen")  # rejected
    acc.observe("EEEV", 11021, source="bvbrc_genome")  # different source

    stats = acc.per_source_stats()
    assert stats["violin_pathogen"]["observed"] == 2
    assert stats["violin_pathogen"]["rejected"] == 2
    assert stats["violin_pathogen"]["unique_pairs"] == 2
    assert stats["bvbrc_genome"]["observed"] == 1
    assert stats["bvbrc_genome"]["unique_pairs"] == 1


def test_merge_combines_accumulators_without_double_counting() -> None:
    """Per-process accumulation + final merge: corroboration counts must combine correctly."""
    a = MinedSynonymAccumulator()
    a.observe("EEEV", 11021, source="violin_pathogen")
    a.observe("ZIKV", 64320, source="violin_pathogen")

    b = MinedSynonymAccumulator()
    b.observe("EEEV", 11021, source="bvbrc_genome")
    b.observe("HCV", 3052230, source="bvbrc_genome")

    a.merge(b)

    pairs = list(a.unique_pairs())
    by_norm = {o.surface_form_normalized: (count, sources) for o, count, sources in pairs}
    assert by_norm["eeev"][0] == 2  # corroborated across both sources
    assert by_norm["eeev"][1] == frozenset({"violin_pathogen", "bvbrc_genome"})
    assert by_norm["zikv"][0] == 1
    assert by_norm["hcv"][0] == 1


def test_mined_synonym_observation_is_hashable() -> None:
    """Frozen dataclass — observations can be put in sets / dict keys."""
    o1 = MinedSynonymObservation(
        surface_form="EEEV",
        surface_form_normalized="eeev",
        taxon_id=11021,
        source="violin_pathogen",
    )
    o2 = MinedSynonymObservation(
        surface_form="EEEV",
        surface_form_normalized="eeev",
        taxon_id=11021,
        source="violin_pathogen",
    )
    s = {o1, o2}
    assert len(s) == 1


def test_original_form_preserves_first_seen_capitalization() -> None:
    """When the same normalized surface appears in different cases,
    the first-seen original is kept for provenance."""
    acc = MinedSynonymAccumulator()
    acc.observe("EEEV", 11021, source="violin_pathogen")
    acc.observe("eeev", 11021, source="bvbrc_genome")
    acc.observe("Eeev", 11021, source="antiviraldb")

    obs = list(acc.observations())
    originals = {o.surface_form for o in obs}
    assert originals == {"EEEV"}  # first-seen capitalization sticks
