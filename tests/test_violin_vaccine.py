"""VIOLIN:Vaccine parser unit tests against a captured real fixture (120-doc sample)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apecx_harvesters.loaders.violin_vaccine import VIOLINVaccineContainer, parse_violin_vaccine

_FIXTURE = Path(__file__).parent / "fixtures" / "globus" / "violin_vaccine" / "sample.json"
_DOCS = json.loads(_FIXTURE.read_text())


def test_fixture_present():
    assert len(_DOCS) >= 100


@pytest.mark.parametrize("doc", _DOCS, ids=[str(d["content"]["id"]) for d in _DOCS])
def test_each_doc_parses(doc):
    record = parse_violin_vaccine(doc["content"])
    assert isinstance(record, VIOLINVaccineContainer)
    assert record.canonical_uri == f"violin-vaccine:{doc['content']['id']}"
    json.dumps(record.to_dict())


def test_no_failures_and_unique_uris():
    records = [parse_violin_vaccine(d["content"]) for d in _DOCS]
    uris = [r.canonical_uri for r in records]
    assert len(set(uris)) == len(uris), "canonical_uri collisions"


def test_vo_crossref_lifted():
    records = [parse_violin_vaccine(d["content"]) for d in _DOCS]
    with_vo = [r for r in records if any(a.alternateIdentifierType == "VO" for a in r.alternateIdentifiers)]
    assert with_vo, "expected some records to carry a Vaccine Ontology cross-reference"
