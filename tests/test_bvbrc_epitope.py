"""BV-BRC Epitope parser unit tests against a captured real fixture (deep 3-level nesting)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apecx_harvesters.loaders.bvbrc_epitope import BVBRCEpitopeContainer, parse_bvbrc_epitope

_FIXTURE = Path(__file__).parent / "fixtures" / "globus" / "bvbrc_epitope" / "sample.json"
_DOCS = json.loads(_FIXTURE.read_text())


def test_fixture_present():
    assert len(_DOCS) >= 1


@pytest.mark.parametrize("doc", _DOCS, ids=range(len(_DOCS)))
def test_each_doc_parses(doc):
    record = parse_bvbrc_epitope(doc["content"])
    assert isinstance(record, BVBRCEpitopeContainer)
    assert record.canonical_uri == f"bvbrc-epitope:{doc['content']['Organism']}"
    json.dumps(record.to_dict())


def test_no_failures_and_unique_uris():
    records = [parse_bvbrc_epitope(d["content"]) for d in _DOCS]
    uris = [r.canonical_uri for r in records]
    assert len(set(uris)) == len(uris), "canonical_uri collisions"


def test_deep_nesting_preserved_and_crossrefs():
    # Epitopes survive the round-trip into the nested container, and taxon/accession
    # cross-refs are lifted.
    records = [parse_bvbrc_epitope(d["content"]) for d in _DOCS]
    total_epitopes = sum(
        len(g.Epitope) for r in records for g in r.bvbrc_epitope.Protein_and_Epitope
    )
    assert total_epitopes > 0, "expected epitopes preserved in the nested container"
    types = {a.alternateIdentifierType for r in records for a in r.alternateIdentifiers}
    assert "NCBI-Taxonomy" in types, f"expected taxonomy cross-ref, got {types}"
