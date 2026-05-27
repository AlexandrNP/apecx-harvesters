"""BV-BRC Genome parser unit tests against a captured real fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apecx_harvesters.loaders.bvbrc_genome import BVBRCGenomeContainer, parse_bvbrc_genome

_FIXTURE = Path(__file__).parent / "fixtures" / "globus" / "bvbrc_genome" / "sample.json"
_DOCS = json.loads(_FIXTURE.read_text())


def test_fixture_present():
    assert len(_DOCS) >= 20


@pytest.mark.parametrize("doc", _DOCS, ids=range(len(_DOCS)))
def test_each_doc_parses(doc):
    record = parse_bvbrc_genome(doc["content"])
    assert isinstance(record, BVBRCGenomeContainer)
    assert record.canonical_uri == f"bvbrc-genome:{doc['content']['Genome_Name']}"
    json.dumps(record.to_dict())


def test_no_failures_and_unique_uris():
    records = [parse_bvbrc_genome(d["content"]) for d in _DOCS]
    uris = [r.canonical_uri for r in records]
    assert len(set(uris)) == len(uris), "canonical_uri collisions"


def test_taxon_crossrefs_lifted():
    records = [parse_bvbrc_genome(d["content"]) for d in _DOCS]
    types = {a.alternateIdentifierType for r in records for a in r.alternateIdentifiers}
    assert {"NCBI-Taxonomy", "GenBank", "BVBRC-Genome"} & types, f"expected genome cross-refs, got {types}"
