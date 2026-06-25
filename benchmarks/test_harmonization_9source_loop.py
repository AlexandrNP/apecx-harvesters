"""Unit tests for the 9-source loop's pure control + alias-validation logic (no network/LLM/dict)."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from harmonization_9source_loop import split_queries, _valid_alias_target


def _resolver(resolves: set[str]):
    """Stub resolver: returns iris for names in `resolves`, empty otherwise (stands in for the dict)."""
    return lambda name: SimpleNamespace(iris={"NCBITaxon_1"} if name in resolves else set())


def test_valid_alias_target_gates_on_dict_resolution():
    r = _resolver({"Dengue virus"})
    assert _valid_alias_target("DENV", "Dengue virus", resolver=r) is True       # canonical resolves
    assert _valid_alias_target("DENV", "Foo virus", resolver=r) is False         # hallucination: no resolve
    assert _valid_alias_target("DENV", "denv", resolver=r) is False              # same as term (no-op)
    assert _valid_alias_target("DENV", None, resolver=r) is False                # nothing proposed
    assert _valid_alias_target("DENV", "", resolver=r) is False                  # empty


def test_split_queries_deterministic_disjoint_partition():
    qs = [f"Q{i:02d}" for i in range(12)]
    train, held = split_queries(qs, every=5)
    assert set(train).isdisjoint(held)                  # disjoint
    assert sorted(train + held) == sorted(qs)           # partition (no loss)
    assert split_queries(qs, every=5) == (train, held)  # deterministic (no RNG)
    assert train and held                               # neither empty at K=5


def test_derive_refuses_multi_entity_alias(monkeypatch):
    # GUARD: a multi-entity query must NOT be aliased to its organism alone (discarding the protein).
    # derive_validated_alias refuses BEFORE any dict/LLM call when classify says multi_entity.
    import harmonization_9source_loop as L
    from entity_split import QuerySplit
    monkeypatch.setattr(L, "classify_query_entities",
                        lambda t: QuerySplit(term=t, organism="hiv", organism_iris={"x"},
                                             protein_term="protease", protein_confident=True, multi_entity=True))
    assert L.derive_validated_alias("HIV protease") is None


@pytest.mark.skipif(not os.environ.get("APECX_RUN_LIVE"), reason="live dict+Ollama; set APECX_RUN_LIVE=1")
def test_derive_validated_alias_expands_acronym():
    from apecx_harvesters.dict_reader import configure_dictionary_path, default_dictionary_path
    configure_dictionary_path(default_dictionary_path())
    from harmonization_9source_loop import derive_validated_alias
    d = derive_validated_alias("DENV")            # acronym: fuzzy fails -> LLM-propose + dict-validate
    assert d is not None and "dengue" in d[1].lower()   # canonical is a dengue name that the dict resolves
