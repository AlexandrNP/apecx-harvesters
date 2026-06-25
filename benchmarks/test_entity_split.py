"""Unit tests for the multi-entity query detector (no dict — an injected stub resolver)."""
from __future__ import annotations

from types import SimpleNamespace

from entity_split import classify_query_entities


def _resolver(resolves: set[str]):
    """Stub: returns an iri for organism strings in `resolves`, empty otherwise (stands in for the dict)."""
    return lambda name: SimpleNamespace(iris={"NCBITaxon_1"} if name and name.lower() in resolves else set())


def test_protein_query_is_multi_entity_and_keeps_protease():
    # The whole point: "HIV protease" must keep BOTH entities, not collapse to HIV-1.
    s = classify_query_entities("HIV protease", resolver=_resolver({"hiv"}))
    assert s.organism == "hiv" and s.organism_iris            # organism resolves
    assert s.protein_term == "protease" and s.protein_confident is True
    assert s.multi_entity is True


def test_multiword_protein_matches_longest():
    s = classify_query_entities("HIV reverse transcriptase", resolver=_resolver({"hiv"}))
    assert s.protein_term == "reverse transcriptase"          # not just "transcriptase" or a single token
    assert s.multi_entity is True
    s2 = classify_query_entities("SARS-CoV-2 spike protein", resolver=_resolver({"sars-cov-2"}))
    assert s2.protein_term == "spike protein" and s2.multi_entity is True


def test_record_type_is_not_a_protein_loss():
    # "vaccine"/"genome"/"structure" are index-routing hints, NOT discarded entities -> not multi_entity.
    s = classify_query_entities("influenza vaccine", resolver=_resolver({"influenza"}))
    assert s.record_type == "vaccine" and s.protein_term is None and s.multi_entity is False
    s2 = classify_query_entities("zika virus structure", resolver=_resolver({"zika virus"}))
    assert s2.record_type == "structure" and s2.multi_entity is False


def test_single_entity_query_is_not_multi():
    s = classify_query_entities("EEEV", resolver=_resolver({"eeev"}))
    assert s.multi_entity is False and s.protein_term is None and s.residual_unknown is None


def test_unknown_residual_flagged_for_hitl_not_dropped():
    # An organism+unknown term that doesn't resolve -> surfaced as residual_unknown (HITL), never silently aliased.
    s = classify_query_entities("HIV frobnicator", resolver=_resolver(set()))
    assert s.multi_entity is False and s.protein_term is None
    assert s.residual_unknown == "hiv frobnicator"


def test_protein_with_unresolvable_organism_is_not_multi_entity():
    # protein present but organism doesn't resolve -> not multi_entity (no taxon to AND with); the loop
    # will still need to resolve the organism separately.
    s = classify_query_entities("HIV protease", resolver=_resolver(set()))
    assert s.protein_term == "protease" and s.multi_entity is False
