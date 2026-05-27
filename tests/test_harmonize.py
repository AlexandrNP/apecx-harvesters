"""Tests for the harmonize layer: source registry + the fail-loud collision guard.

The collision-guard tests use real fixture data (no network). The end-to-end
scroll+harmonize test needs live Globus credentials and is skipped without them.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from apecx_harvesters.loaders.antiviraldb import parse_antiviraldb
from apecx_harvesters.pipeline.harmonize import (
    SOURCE_REGISTRY,
    CanonicalCollisionError,
    assert_unique_canonical,
    harmonize_index,
)

_AVDB = json.loads(
    (Path(__file__).parent / "fixtures" / "globus" / "antiviraldb" / "sample.json").read_text()
)


def test_registry_covers_all_nine_sources():
    assert len(SOURCE_REGISTRY) == 9
    names = {name for name, _ in SOURCE_REGISTRY.values()}
    assert names == {
        "protabank", "antiviraldb", "violin_pathogen", "violin_vaccine", "violin_gene",
        "bvbrc_epitope", "bvbrc_protein_structure", "bvbrc_protein", "bvbrc_genome",
    }


def test_collision_guard_passes_on_unique():
    records = [parse_antiviraldb(d["content"]) for d in _AVDB]
    assert_unique_canonical(records)  # all 35 distinct -> no raise


def test_collision_guard_fails_loud_on_duplicate():
    # Same source doc parsed twice -> identical canonical_uri -> must FAIL LOUD,
    # because ingest would silently overwrite one with the other.
    dup = parse_antiviraldb(_AVDB[0]["content"])
    dup2 = parse_antiviraldb(_AVDB[0]["content"])
    assert dup.canonical_uri == dup2.canonical_uri
    with pytest.raises(CanonicalCollisionError):
        assert_unique_canonical([dup, dup2])


def test_harmonize_rejects_unregistered_index():
    class _Boom:
        def search(self, *a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("should not query an unregistered index")

    with pytest.raises(KeyError):
        asyncio.run(harmonize_index("00000000-0000-0000-0000-000000000000", client=_Boom()))


@pytest.mark.skipif(
    not (os.environ.get("GLOBUS_CLIENT_ID") and os.environ.get("GLOBUS_CLIENT_SECRET")),
    reason="needs live Globus credentials in the environment",
)
def test_harmonize_violin_pathogen_end_to_end():
    from apecx_harvesters.pipeline.globus_source import build_search_client

    client = build_search_client()
    records, prov, errors = asyncio.run(
        harmonize_index("a67c7310-5115-446f-bfb6-d889bc4efa06", client=client)
    )
    assert prov["harmonized_count"] == 217
    assert prov["parse_error_count"] == 0
    assert prov["stable_total"] is True
    assert len(records) == 217
