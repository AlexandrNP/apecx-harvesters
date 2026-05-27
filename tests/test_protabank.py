"""ProtaBank parser unit tests against a captured real fixture (120-doc sample)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apecx_harvesters.loaders.protabank import ProtaBankContainer, parse_protabank

_FIXTURE = Path(__file__).parent / "fixtures" / "globus" / "protabank" / "sample.json"
_DOCS = json.loads(_FIXTURE.read_text())


def test_fixture_present():
    assert len(_DOCS) >= 100


@pytest.mark.parametrize("doc", _DOCS, ids=range(len(_DOCS)))
def test_each_doc_parses(doc):
    record = parse_protabank(doc["content"])
    assert isinstance(record, ProtaBankContainer)
    assert record.canonical_uri == f"protabank:{doc['content']['Title'].strip()}"
    json.dumps(record.to_dict())


def test_no_failures_and_unique_uris():
    records = [parse_protabank(d["content"]) for d in _DOCS]
    uris = [r.canonical_uri for r in records]
    assert len(set(uris)) == len(uris), "canonical_uri collisions"


def test_publication_fields_promoted_to_datacite():
    # ProtaBank is publication metadata: authors -> creators, year -> publicationYear.
    records = [parse_protabank(d["content"]) for d in _DOCS]
    with_creators = [r for r in records if r.creators]
    with_year = [r for r in records if r.publicationYear]
    assert with_creators, "expected some records to promote authors to creators"
    assert with_year, "expected some records to promote publication year"
