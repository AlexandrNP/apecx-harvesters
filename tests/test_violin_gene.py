"""VIOLIN:Gene parser unit tests against a captured real fixture (120-doc sample)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apecx_harvesters.loaders.violin_gene import VIOLINGeneContainer, parse_violin_gene

_FIXTURE = Path(__file__).parent / "fixtures" / "globus" / "violin_gene" / "sample.json"
_DOCS = json.loads(_FIXTURE.read_text())


def test_fixture_present():
    assert len(_DOCS) >= 100


@pytest.mark.parametrize("doc", _DOCS, ids=[str(d["content"]["id"]) for d in _DOCS])
def test_each_doc_parses(doc):
    record = parse_violin_gene(doc["content"])
    assert isinstance(record, VIOLINGeneContainer)
    assert record.canonical_uri == f"violin-gene:{doc['content']['id']}"
    json.dumps(record.to_dict())


def test_no_failures_and_unique_uris():
    records = [parse_violin_gene(d["content"]) for d in _DOCS]
    uris = [r.canonical_uri for r in records]
    assert len(set(uris)) == len(uris), "canonical_uri collisions"


def test_accession_crossrefs_lifted():
    records = [parse_violin_gene(d["content"]) for d in _DOCS]
    types = {a.alternateIdentifierType for r in records for a in r.alternateIdentifiers}
    # Genes carry external accessions; expect at least NCBI-Gene or GenBank to appear.
    assert {"NCBI-Gene", "GenBank", "PDB"} & types, f"expected accession cross-refs, got {types}"
