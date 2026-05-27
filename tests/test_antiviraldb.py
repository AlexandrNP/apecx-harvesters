"""AntiviralDB parser unit tests against a captured real fixture (35 docs).

The fixture (``tests/fixtures/globus/antiviraldb/sample.json``) is the full live
AntiviralDB index content captured 2026-05-26. This is real data, not synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apecx_harvesters.loaders.antiviraldb import AntiviralDBContainer, parse_antiviraldb

_FIXTURE = Path(__file__).parent / "fixtures" / "globus" / "antiviraldb" / "sample.json"
_DOCS = json.loads(_FIXTURE.read_text())


def test_fixture_is_the_full_index():
    assert len(_DOCS) == 35, "expected the full 35-record AntiviralDB fixture"


@pytest.mark.parametrize("doc", _DOCS, ids=[d["subject"] for d in _DOCS])
def test_each_doc_parses_and_roundtrips(doc):
    record = parse_antiviraldb(doc["content"])
    assert isinstance(record, AntiviralDBContainer)
    assert record.canonical_uri.startswith("antiviraldb:")
    assert record.antiviraldb.Virus == doc["content"]["Virus"]
    # to_dict() is the GMetaEntry content sent to Globus; it must be JSON-serializable.
    json.dumps(record.to_dict())


def test_no_parse_failures_and_unique_uris():
    records = [parse_antiviraldb(d["content"]) for d in _DOCS]
    assert len(records) == 35
    uris = [r.canonical_uri for r in records]
    assert len(set(uris)) == len(uris), "canonical_uri collisions across the 35 records"


def test_cross_reference_ids_are_lifted():
    # At least one record should carry PDB and/or PMID alternateIdentifiers
    # (the cross-linking signal). The Chikungunya record has PDB codes + Refs.
    by_uri = {parse_antiviraldb(d["content"]).canonical_uri: parse_antiviraldb(d["content"]) for d in _DOCS}
    total_alt = sum(len(r.alternateIdentifiers) for r in by_uri.values())
    assert total_alt > 0, "expected PDB/PMID cross-reference identifiers to be lifted"
    types = {a.alternateIdentifierType for r in by_uri.values() for a in r.alternateIdentifiers}
    assert {"PDB", "PMID"} & types, f"expected PDB or PMID alt-id types, got {types}"
