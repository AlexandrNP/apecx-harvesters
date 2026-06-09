"""Tests for the VIOLIN vaccine→pathogen→taxon cross-walk."""

from __future__ import annotations

from unittest.mock import patch

from apecx_harvesters.loaders.base import Publisher
from apecx_harvesters.loaders.violin_vaccine import VIOLINVaccineContainer
from apecx_harvesters.loaders.violin_vaccine.model import ViolinVaccineFields
from apecx_harvesters.pipeline.canonical_resolver_adapter import make_resolver_for_source
from apecx_harvesters.pipeline.violin_crosswalk import (
    violin_vaccine_crosswalk_subjects,
)


def _vaccine(pathogen_ids: list[int], name: str = "Test vaccine"):
    fields = ViolinVaccineFields(
        id=1, vaccine_id=1, Vaccine=name, pathogen_id=pathogen_ids
    )
    return VIOLINVaccineContainer.new(
        title=name,
        description=None,
        creators=[],
        publisher=Publisher(name="VIOLIN"),
        violin_vaccine=fields,
    )


def test_crosswalk_maps_pathogen_ids_to_taxa():
    rec = _vaccine([105, 172])
    subs = violin_vaccine_crosswalk_subjects(rec, {105: 11250, 172: 10566})
    iris = {s.valueUri for s in subs}
    assert iris == {
        "http://purl.obolibrary.org/obo/NCBITaxon_11250",
        "http://purl.obolibrary.org/obo/NCBITaxon_10566",
    }
    assert all(s.subjectScheme == "NCBI Taxonomy" for s in subs)


def test_crosswalk_skips_unmapped_and_dedupes():
    # 217 not in the cross-walk (no taxon); 105 appears twice → one Subject.
    rec = _vaccine([105, 105, 217])
    subs = violin_vaccine_crosswalk_subjects(rec, {105: 11250})
    assert [s.valueUri for s in subs] == [
        "http://purl.obolibrary.org/obo/NCBITaxon_11250"
    ]


def test_crosswalk_empty_when_no_pathogen_link():
    rec = _vaccine([])
    assert violin_vaccine_crosswalk_subjects(rec, {105: 11250}) == []


def test_resolver_uses_crosswalk_for_violin_vaccine():
    """make_resolver_for_source wires the cross-walk into the vaccine resolver,
    so a vaccine whose NAME doesn't resolve still gets its pathogen's taxon."""
    rec = _vaccine([105], name="some unresolvable vaccine name")
    resolver = make_resolver_for_source(
        "violin_vaccine", violin_pathogen_crosswalk={105: 11250}
    )
    # The vaccine-name slot lookup misses; the cross-walk supplies the taxon.
    from apecx_harvesters.dict_reader import ResolutionStatus
    from apecx_harvesters.dict_reader.lookup import LookupResult

    miss = LookupResult(
        surface_form="x",
        path="miss",
        canonical_iri=None,
        canonical_label=None,
        canonical_ontology=None,
        confidence=0.0,
        resolution_status=ResolutionStatus.UNRESOLVED,
        synonyms=(),
        evidence="",
        candidates=(),
    )
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=miss,
    ):
        out = resolver(rec)
    iris = {s.valueUri for s in (out.subjects or [])}
    assert "http://purl.obolibrary.org/obo/NCBITaxon_11250" in iris


def test_resolver_without_crosswalk_is_unchanged():
    """No cross-walk passed → vaccine resolver behaves as before (no taxon
    from the link)."""
    rec = _vaccine([105], name="x")
    resolver = make_resolver_for_source("violin_vaccine")
    from apecx_harvesters.dict_reader import ResolutionStatus
    from apecx_harvesters.dict_reader.lookup import LookupResult

    miss = LookupResult(
        surface_form="x", path="miss", canonical_iri=None, canonical_label=None,
        canonical_ontology=None, confidence=0.0,
        resolution_status=ResolutionStatus.UNRESOLVED, synonyms=(), evidence="",
        candidates=(),
    )
    with patch(
        "apecx_harvesters.pipeline.canonical_resolver_adapter.lookup_entity",
        return_value=miss,
    ):
        out = resolver(rec)
    # No NCBITaxon subject from a link (the record carries no alt-id either).
    assert not any(
        s.valueUri.startswith("http://purl.obolibrary.org/obo/NCBITaxon_")
        for s in (out.subjects or [])
    )
