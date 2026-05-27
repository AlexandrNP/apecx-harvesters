"""BV-BRC Protein Structure parser unit tests against a captured real fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apecx_harvesters.loaders.bvbrc_protein_structure import (
    BVBRCProteinStructureContainer,
    parse_bvbrc_protein_structure,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "globus" / "bvbrc_protein_structure" / "sample.json"
_DOCS = json.loads(_FIXTURE.read_text())


def test_fixture_present():
    assert len(_DOCS) >= 1


@pytest.mark.parametrize("doc", _DOCS, ids=range(len(_DOCS)))
def test_each_doc_parses(doc):
    record = parse_bvbrc_protein_structure(doc["content"])
    assert isinstance(record, BVBRCProteinStructureContainer)
    assert record.canonical_uri == f"bvbrc-protein-structure:{doc['content']['Organism_Name']}"
    json.dumps(record.to_dict())


def test_no_failures_and_unique_uris():
    records = [parse_bvbrc_protein_structure(d["content"]) for d in _DOCS]
    uris = [r.canonical_uri for r in records]
    assert len(set(uris)) == len(uris), "canonical_uri collisions"


def test_pdb_and_pmid_crossrefs_lifted():
    records = [parse_bvbrc_protein_structure(d["content"]) for d in _DOCS]
    types = {a.alternateIdentifierType for r in records for a in r.alternateIdentifiers}
    assert {"PDB", "PMID", "UniProt"} & types, f"expected structure cross-refs, got {types}"
