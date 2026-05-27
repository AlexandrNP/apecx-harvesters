"""VIOLIN:Pathogen parser unit tests against a captured real fixture (217 docs).

Fixture captured live 2026-05-26 (full index). Real data, not synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apecx_harvesters.loaders.violin_pathogen import (
    VIOLINPathogenContainer,
    parse_violin_pathogen,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "globus" / "violin_pathogen" / "sample.json"
_DOCS = json.loads(_FIXTURE.read_text())


def test_fixture_is_the_full_index():
    assert len(_DOCS) == 217


@pytest.mark.parametrize("doc", _DOCS, ids=[str(d["content"]["id"]) for d in _DOCS])
def test_each_doc_parses_and_roundtrips(doc):
    record = parse_violin_pathogen(doc["content"])
    assert isinstance(record, VIOLINPathogenContainer)
    assert record.canonical_uri == f"violin-pathogen:{doc['content']['id']}"
    # to_dict() is the GMetaEntry content sent to Globus; it must be JSON-serializable.
    # (Not a strict model round-trip: DataCite is strict=True, so model_dump(mode="json")
    # serializes enums to strings that strict re-validation refuses to coerce -- that path
    # is never exercised at ingest.)
    json.dumps(record.to_dict())


def test_no_failures_and_unique_uris():
    records = [parse_violin_pathogen(d["content"]) for d in _DOCS]
    assert len(records) == 217
    uris = [r.canonical_uri for r in records]
    assert len(set(uris)) == len(uris), "canonical_uri collisions"


def test_taxonomy_crossref_lifted():
    records = [parse_violin_pathogen(d["content"]) for d in _DOCS]
    with_tax = [
        r for r in records
        if any(a.alternateIdentifierType == "NCBI-Taxonomy" for a in r.alternateIdentifiers)
    ]
    # ~210 of 217 carry an NCBI Taxonomy ID.
    assert len(with_tax) >= 200, f"expected >=200 taxonomy cross-refs, got {len(with_tax)}"
