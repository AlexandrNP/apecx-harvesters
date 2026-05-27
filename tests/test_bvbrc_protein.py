"""BV-BRC Protein parser unit tests against a captured real fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apecx_harvesters.loaders.bvbrc_protein import BVBRCProteinContainer, parse_bvbrc_protein

_FIXTURE = Path(__file__).parent / "fixtures" / "globus" / "bvbrc_protein" / "sample.json"
_DOCS = json.loads(_FIXTURE.read_text())


def test_fixture_present():
    assert len(_DOCS) >= 20


@pytest.mark.parametrize("doc", _DOCS, ids=range(len(_DOCS)))
def test_each_doc_parses(doc):
    record = parse_bvbrc_protein(doc["content"])
    assert isinstance(record, BVBRCProteinContainer)
    assert record.canonical_uri == f"bvbrc-protein:{doc['content']['Genome']}"
    json.dumps(record.to_dict())


def test_no_failures_and_unique_uris():
    records = [parse_bvbrc_protein(d["content"]) for d in _DOCS]
    uris = [r.canonical_uri for r in records]
    assert len(set(uris)) == len(uris), "canonical_uri collisions"


def test_feature_crossrefs_lifted():
    records = [parse_bvbrc_protein(d["content"]) for d in _DOCS]
    types = {a.alternateIdentifierType for r in records for a in r.alternateIdentifiers}
    assert {"BVBRC-Genome", "GenBank"} & types, f"expected feature cross-refs, got {types}"
