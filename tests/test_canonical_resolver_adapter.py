"""Tests for the canonical-resolver adapter (Phase F building block).

Covers:
- Adapter loads + is callable per source.
- A record whose organism slot is populated gains a Subject row with the
  expected schema (when the dictionary singleton resolves the surface).
- Pass-through behavior when the surface is empty or the source has no
  slot map (e.g. ProtaBank).
- Ambiguous resolution surfaces multiple Subjects, one per candidate.
- Idempotency: re-running the resolver against an already-resolved
  record doesn't duplicate Subject rows.

The dictionary singleton is monkeypatched per test so these are pure
unit tests; no SQLite + no Globus required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from apecx_harvesters.dict_reader import LookupCandidate, LookupResult, ResolutionStatus
from apecx_harvesters.loaders.base import AlternateIdentifier, Publisher, Subject
from apecx_harvesters.loaders.violin_pathogen import VIOLINPathogenContainer
from apecx_harvesters.loaders.violin_pathogen.model import ViolinPathogenFields
from apecx_harvesters.pipeline.canonical_resolver_adapter import (
    make_resolver_for_source,
)


def _make_violin_record(pathogen: str = "Influenza A virus", taxon: int = 11320):
    """Build a minimal VIOLIN:Pathogen container for adapter tests."""
    fields = ViolinPathogenFields(
        id=1,
        VIOLIN_c_pathogen_id=1,
        Pathogen=pathogen,
        NCBI_Taxonomy_ID=taxon,
    )
    return VIOLINPathogenContainer.new(
        title=pathogen,
        description=None,
        creators=[],
        publisher=Publisher(name="VIOLIN"),
        violin_pathogen=fields,
    )


def _resolved(iri: str, label: str, ontology: str = "NCBITaxon") -> LookupResult:
    return LookupResult(
        surface_form=label,
        path="fast",
        canonical_iri=iri,
        canonical_label=label,
        canonical_ontology=ontology,
        confidence=1.0,
        resolution_status=ResolutionStatus.ID_ANCHORED,
        synonyms=(),
        evidence="",
        candidates=(),
    )


def _ambiguous(
    surface: str, candidates: list[tuple[str, str, str]]
) -> LookupResult:
    return LookupResult(
        surface_form=surface,
        path="ambiguous",
        canonical_iri=None,
        canonical_label=None,
        canonical_ontology=None,
        confidence=0.0,
        resolution_status=ResolutionStatus.AMBIGUOUS,
        synonyms=(),
        evidence="",
        candidates=tuple(
            LookupCandidate(
                canonical_iri=iri,
                canonical_label=lbl,
                canonical_ontology=ont,
                confidence=1.0,
            )
            for iri, lbl, ont in candidates
        ),
    )


@pytest.fixture(autouse=True)
def _no_species_dictionary(monkeypatch):
    """Keep these pure unit tests: neutralize the strain→species expansion.

    The expansion pass calls ``get_dictionary_index()`` — a PROCESS-WIDE
    singleton that another test file (``test_republish_roundtrip``) configures
    with the real SQLite. Without this, run order would leak the real dict in
    and add unexpected species subjects. Tests that exercise the expansion
    re-patch ``get_dictionary_index`` locally.
    """
    monkeypatch.setattr(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.get_dictionary_index",
        lambda: (None, None),
    )


class _StubIndex:
    """Minimal stand-in for DictionaryIndex.species_iri_for in unit tests."""

    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping

    def species_iri_for(self, iri: str) -> str | None:
        return self._mapping.get(iri)


def test_adapter_constructs_per_source():
    resolver = make_resolver_for_source("violin_pathogen")
    assert callable(resolver)
    assert resolver.__source_name__ == "violin_pathogen"  # type: ignore[attr-defined]


def test_adapter_pass_through_for_source_with_no_slots():
    """A slot-less source whose record has NOTHING to dual-stamp returns unchanged. (The resolver
    no longer early-returns merely on empty slots; a record with no NCBI-Taxonomy alt-id still
    passes through via the any_hit=False path.)"""
    resolver = make_resolver_for_source("protabank")
    record = _make_violin_record()  # no NCBI-Taxonomy alt-id -> nothing to dual-stamp
    out = resolver(record)
    assert out is record
    assert out.subjects == []


def test_adapter_slotless_source_dual_stamps_ncbitaxon_altid():
    """WS3a: a slot-less source (PDB/EMDB) whose record carries an NCBI-Taxonomy alt-id now gains
    the NCBITaxon IRI Subject via the dual-stamp. Before the fix, resolve()'s empty-slots
    early-return skipped the dual-stamp entirely — a silent harmonization failure (no taxon IRI)."""
    resolver = make_resolver_for_source("pdb")  # not in _SOURCE_SLOTS -> slot-less
    record = _make_violin_record().model_copy(update={
        "alternateIdentifiers": [
            AlternateIdentifier(
                alternateIdentifier="2697049", alternateIdentifierType="NCBI-Taxonomy"
            ),
        ],
        "subjects": [],
    })
    out = resolver(record)
    iris = [s.valueUri for s in out.subjects]
    assert "http://purl.obolibrary.org/obo/NCBITaxon_2697049" in iris
    assert out is not record  # the dual-stamp produced a change


def test_adapter_resolved_writes_one_subject():
    record = _make_violin_record("Influenza A virus", 11320)
    resolver = make_resolver_for_source("violin_pathogen")
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=_resolved(
            "http://purl.obolibrary.org/obo/NCBITaxon_11320",
            "Influenza A virus",
        ),
    ):
        out = resolver(record)
    assert len(out.subjects) == 1
    subj = out.subjects[0]
    assert isinstance(subj, Subject)
    assert subj.valueUri == "http://purl.obolibrary.org/obo/NCBITaxon_11320"
    assert subj.subjectScheme == "NCBI Taxonomy"
    assert subj.subject == "Influenza A virus"


def test_adapter_ambiguous_writes_subject_per_candidate():
    record = _make_violin_record("RSV", 11250)
    resolver = make_resolver_for_source("violin_pathogen")
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=_ambiguous(
            "RSV",
            [
                ("http://purl.obolibrary.org/obo/NCBITaxon_11250", "Human RSV", "NCBITaxon"),
                ("http://purl.obolibrary.org/obo/NCBITaxon_11246", "Bovine RSV", "NCBITaxon"),
            ],
        ),
    ):
        out = resolver(record)
    iris = {s.valueUri for s in out.subjects}
    assert iris == {
        "http://purl.obolibrary.org/obo/NCBITaxon_11250",
        "http://purl.obolibrary.org/obo/NCBITaxon_11246",
    }


def test_adapter_idempotent_does_not_duplicate_subjects():
    record = _make_violin_record("Influenza A virus", 11320)
    resolver = make_resolver_for_source("violin_pathogen")
    iri = "http://purl.obolibrary.org/obo/NCBITaxon_11320"
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=_resolved(iri, "Influenza A virus"),
    ):
        first = resolver(record)
        second = resolver(first)
    assert len(second.subjects) == 1
    assert second.subjects[0].valueUri == iri


def test_adapter_skips_empty_surface_form():
    record = _make_violin_record("", 11320)
    resolver = make_resolver_for_source("violin_pathogen")
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
    ) as patched_lookup:
        out = resolver(record)
    patched_lookup.assert_not_called()
    assert out.subjects == []


def test_adapter_real_lowercase_ontology_code_resolves():
    """REGRESSION: lookup_entity returns lowercase 'ncbitaxon' — the adapter
    must map it to a Subject. A prior version keyed only on mixed-case
    'NCBITaxon' and silently produced zero subjects against the real dict."""
    record = _make_violin_record("Chikungunya virus", 37124)
    resolver = make_resolver_for_source("violin_pathogen")
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=_resolved(
            "http://purl.obolibrary.org/obo/NCBITaxon_37124",
            "Chikungunya virus",
            ontology="ncbitaxon",  # the REAL value lookup_entity returns
        ),
    ):
        out = resolver(record)
    assert len(out.subjects) == 1
    assert out.subjects[0].subjectScheme == "NCBI Taxonomy"
    assert out.subjects[0].valueUri.endswith("NCBITaxon_37124")


def test_adapter_unknown_ontology_skipped():
    record = _make_violin_record("Influenza A virus", 11320)
    resolver = make_resolver_for_source("violin_pathogen")
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=_resolved(
            "ex:foo", "Influenza A virus", ontology="NotARealOntology"
        ),
    ):
        out = resolver(record)
    # No scheme mapping → no Subject rather than emitting an
    # ungrounded entry; record passes through unchanged.
    assert out.subjects == []
    assert out.canonical_uri == record.canonical_uri


# ---------------------------------------------------------------------------
# P2-0: dual-stamp — carry the source-stamped taxon id forward as a Subject
# ---------------------------------------------------------------------------


def _make_violin_record_with_altid(pathogen: str, resolved_taxon: int, stamped_taxon: int):
    """A record whose Species resolves to one taxon but is STAMPED with another.

    Mirrors the BVBRC reality: Species "Alphainfluenzavirus influenzae" →
    dict 2955291, but the record carries alt-id NCBI-Taxonomy 11320.
    """
    from apecx_harvesters.loaders.base import AlternateIdentifier

    fields = ViolinPathogenFields(
        id=1, VIOLIN_c_pathogen_id=1, Pathogen=pathogen, NCBI_Taxonomy_ID=resolved_taxon
    )
    return VIOLINPathogenContainer.new(
        title=pathogen,
        description=None,
        creators=[],
        publisher=Publisher(name="VIOLIN"),
        alternateIdentifiers=[
            AlternateIdentifier(
                alternateIdentifier=str(stamped_taxon),
                alternateIdentifierType="NCBI-Taxonomy",
            )
        ],
        violin_pathogen=fields,
    )


def test_dual_stamp_carries_both_resolved_and_stamped_taxon():
    """subjects.valueUri ends up holding BOTH the dict-resolved IRI AND the
    source-stamped alt-id IRI — so either a common-name or a new-binomial
    query (which resolve to different ids) matches after the filter collapse."""
    record = _make_violin_record_with_altid(
        "Alphainfluenzavirus influenzae", resolved_taxon=2955291, stamped_taxon=11320
    )
    resolver = make_resolver_for_source("violin_pathogen")
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=_resolved(
            "http://purl.obolibrary.org/obo/NCBITaxon_2955291",
            "Alphainfluenzavirus influenzae",
        ),
    ):
        out = resolver(record)
    iris = {s.valueUri for s in out.subjects}
    assert "http://purl.obolibrary.org/obo/NCBITaxon_2955291" in iris  # resolved
    assert "http://purl.obolibrary.org/obo/NCBITaxon_11320" in iris  # stamped


def test_dual_stamp_no_duplicate_when_resolved_equals_stamped():
    """When the resolved id == the stamped id, only one Subject is emitted."""
    record = _make_violin_record_with_altid(
        "Influenza A virus", resolved_taxon=11320, stamped_taxon=11320
    )
    resolver = make_resolver_for_source("violin_pathogen")
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=_resolved(
            "http://purl.obolibrary.org/obo/NCBITaxon_11320", "Influenza A virus"
        ),
    ):
        out = resolver(record)
    iris = [s.valueUri for s in out.subjects]
    assert iris == ["http://purl.obolibrary.org/obo/NCBITaxon_11320"]


def test_dual_stamp_vo_alt_id_anchors_vaccine():
    """A VIOLIN:Vaccine-style record whose Vaccine name doesn't resolve as an
    NCBI pathogen still gets a Vaccine Ontology Subject from its VO alt-id."""
    from apecx_harvesters.loaders.base import AlternateIdentifier

    fields = ViolinPathogenFields(
        id=1, VIOLIN_c_pathogen_id=1, Pathogen="RSV vaccine candidate", NCBI_Taxonomy_ID=1
    )
    record = VIOLINPathogenContainer.new(
        title="RSV vaccine candidate",
        description=None,
        creators=[],
        publisher=Publisher(name="VIOLIN"),
        alternateIdentifiers=[
            AlternateIdentifier(alternateIdentifier="VO_0005278", alternateIdentifierType="VO")
        ],
        violin_pathogen=fields,
    )
    from apecx_harvesters.pipeline.canonical_resolver_adapter import _dual_stamp_subjects

    subs = _dual_stamp_subjects(record)
    assert len(subs) == 1
    assert subs[0].subjectScheme == "Vaccine Ontology"
    assert subs[0].valueUri == "http://purl.obolibrary.org/obo/VO_0005278"


def test_dual_stamp_bvbrc_genome_id_extracts_species_taxon():
    """A BVBRC-Genome 'species.strain' id anchors on the species taxon prefix.

    bvbrc_protein's Genome field is a strain-level name that resolves to
    nothing and carries no NCBI-Taxonomy alt-id; the BVBRC-Genome prefix is
    the only reliable anchor.
    """
    from apecx_harvesters.loaders.base import AlternateIdentifier
    from apecx_harvesters.pipeline.canonical_resolver_adapter import _dual_stamp_subjects

    fields = ViolinPathogenFields(
        id=1, VIOLIN_c_pathogen_id=1, Pathogen="x", NCBI_Taxonomy_ID=1
    )
    record = VIOLINPathogenContainer.new(
        title="x",
        description=None,
        creators=[],
        publisher=Publisher(name="BVBRC"),
        alternateIdentifiers=[
            AlternateIdentifier(alternateIdentifier="37124.7598", alternateIdentifierType="BVBRC-Genome")
        ],
        violin_pathogen=fields,
    )
    subs = _dual_stamp_subjects(record)
    assert len(subs) == 1
    assert subs[0].subjectScheme == "NCBI Taxonomy"
    assert subs[0].valueUri == "http://purl.obolibrary.org/obo/NCBITaxon_37124"


def test_dual_stamp_only_when_resolver_misses():
    """Even if the Species doesn't resolve, the stamped alt-id is still carried."""
    record = _make_violin_record_with_altid(
        "Some Unresolvable Name", resolved_taxon=11320, stamped_taxon=11320
    )
    resolver = make_resolver_for_source("violin_pathogen")
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=LookupResult(
            surface_form="Some Unresolvable Name",
            path="miss",
            canonical_iri=None,
            canonical_label=None,
            canonical_ontology=None,
            confidence=0.0,
            resolution_status=ResolutionStatus.UNRESOLVED,
            synonyms=(),
            evidence="",
            candidates=(),
        ),
    ):
        out = resolver(record)
    iris = {s.valueUri for s in out.subjects}
    assert iris == {"http://purl.obolibrary.org/obo/NCBITaxon_11320"}


# ---------------------------------------------------------------------------
# Strain→species normalization — stamp the species-rank ancestor alongside
# every NCBITaxon subject so a species-level subjects.valueUri query matches
# strain-stamped records uniformly across sources.
# ---------------------------------------------------------------------------

_PREF = "http://purl.obolibrary.org/obo/NCBITaxon_"


def test_species_expansion_stamps_species_ancestor_of_strain():
    """A record carrying a strain taxon also gains its species ancestor."""
    record = _make_violin_record("Mycobacterium tuberculosis H37Rv", 83332)
    resolver = make_resolver_for_source("violin_pathogen")
    with (
        patch(
            "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
            return_value=_resolved(f"{_PREF}83332", "M.tb H37Rv"),
        ),
        patch(
            "apecx_harvesters.pipeline.canonical_resolver_adapter.get_dictionary_index",
            return_value=(_StubIndex({f"{_PREF}83332": f"{_PREF}1773"}), None),
        ),
    ):
        out = resolver(record)
    iris = {s.valueUri for s in out.subjects}
    assert f"{_PREF}83332" in iris  # the strain itself
    assert f"{_PREF}1773" in iris  # its species ancestor
    species = next(s for s in out.subjects if s.valueUri == f"{_PREF}1773")
    assert species.subjectScheme == "NCBI Taxonomy"
    assert species.subject == "1773"


def test_species_expansion_no_duplicate_when_taxon_is_a_species():
    """A species-rank taxon maps to itself — no duplicate Subject is added."""
    record = _make_violin_record("Homo sapiens", 9606)
    resolver = make_resolver_for_source("violin_pathogen")
    with (
        patch(
            "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
            return_value=_resolved(f"{_PREF}9606", "Homo sapiens"),
        ),
        patch(
            "apecx_harvesters.pipeline.canonical_resolver_adapter.get_dictionary_index",
            return_value=(_StubIndex({f"{_PREF}9606": f"{_PREF}9606"}), None),
        ),
    ):
        out = resolver(record)
    iris = [s.valueUri for s in out.subjects]
    assert iris == [f"{_PREF}9606"]


def test_species_expansion_idempotent_on_rerun():
    """Re-running over an already-species-stamped record adds nothing."""
    record = _make_violin_record("Mycobacterium tuberculosis H37Rv", 83332)
    resolver = make_resolver_for_source("violin_pathogen")
    with (
        patch(
            "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
            return_value=_resolved(f"{_PREF}83332", "M.tb H37Rv"),
        ),
        patch(
            "apecx_harvesters.pipeline.canonical_resolver_adapter.get_dictionary_index",
            return_value=(_StubIndex({f"{_PREF}83332": f"{_PREF}1773"}), None),
        ),
    ):
        once = resolver(record)
        twice = resolver(once)
    assert {s.valueUri for s in once.subjects} == {s.valueUri for s in twice.subjects}
    assert len([s for s in twice.subjects if s.valueUri == f"{_PREF}1773"]) == 1


def test_species_expansion_noop_without_dictionary():
    """Pre-normalization dictionary (index None) → expansion is a silent no-op."""
    record = _make_violin_record("Mycobacterium tuberculosis H37Rv", 83332)
    resolver = make_resolver_for_source("violin_pathogen")
    # get_dictionary_index already neutralized to (None, None) by the autouse
    # fixture — the expansion must not raise and must add no species subject.
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=_resolved(f"{_PREF}83332", "M.tb H37Rv"),
    ):
        out = resolver(record)
    assert {s.valueUri for s in out.subjects} == {f"{_PREF}83332"}
