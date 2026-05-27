"""BV-BRC Genome parser unit tests against a captured real fixture.

Genome's canonical_uri keys on the SOURCE SUBJECT, not Genome_Name: BV-BRC shards
high-volume organisms across docs that share a name but have distinct subjects
(e.g. 14 docs all named "Hepacivirus C", subjects "Hepacivirus C (2)".."(13)").
"""

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
    record = parse_bvbrc_genome(doc["content"], doc["subject"])
    assert isinstance(record, BVBRCGenomeContainer)
    assert record.canonical_uri == f"bvbrc-genome:{doc['subject']}"
    json.dumps(record.to_dict())


def test_no_failures_and_unique_uris():
    records = [parse_bvbrc_genome(d["content"], d["subject"]) for d in _DOCS]
    uris = [r.canonical_uri for r in records]
    assert len(set(uris)) == len(uris), "canonical_uri collisions"


def test_subject_drives_canonical_not_genome_name():
    # Regression for the real collision: BV-BRC shards "Hepacivirus C" into 14 docs that
    # share Genome_Name but have distinct subjects ("Hepacivirus C (4)", "(9)", ...). The
    # canonical_uri MUST follow the subject, or one shard would silently overwrite another.
    content = _DOCS[0]["content"]  # real content; vary only the (real-shaped) subject
    a = parse_bvbrc_genome(content, "Hepacivirus C (4)")
    b = parse_bvbrc_genome(content, "Hepacivirus C (9)")
    assert a.canonical_uri == "bvbrc-genome:Hepacivirus C (4)"
    assert b.canonical_uri == "bvbrc-genome:Hepacivirus C (9)"
    assert a.canonical_uri != b.canonical_uri  # same Genome_Name would have collided


def test_taxon_crossrefs_lifted():
    records = [parse_bvbrc_genome(d["content"], d["subject"]) for d in _DOCS]
    types = {a.alternateIdentifierType for r in records for a in r.alternateIdentifiers}
    assert {"NCBI-Taxonomy", "GenBank", "BVBRC-Genome"} & types, f"expected genome cross-refs, got {types}"
