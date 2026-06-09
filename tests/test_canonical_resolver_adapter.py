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
from apecx_harvesters.loaders.base import Publisher, Subject
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


def test_adapter_constructs_per_source():
    resolver = make_resolver_for_source("violin_pathogen")
    assert callable(resolver)
    assert resolver.__source_name__ == "violin_pathogen"  # type: ignore[attr-defined]


def test_adapter_pass_through_for_source_with_no_slots():
    """ProtaBank has no organism-slot map yet — record returns unchanged."""
    resolver = make_resolver_for_source("protabank")
    record = _make_violin_record()  # type doesn't matter; ext_field won't match
    out = resolver(record)
    assert out is record
    assert out.subjects == []


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
