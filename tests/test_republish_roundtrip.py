"""Phase F de-risk: strict-round-trip + canonical_uri stability per source.

These are the safety pins the plan (P2-2, P2-3) requires BEFORE the
745k-record OE-G9 genome republish. They prove, against one real DEST
record per source, that:

  1. A DEST record (DataCite.to_dict output) re-parses through
     `_reparse_dest_content` (strict=False) without raising — the known
     "DataCite emits enum-as-string that strict re-validation refuses"
     failure mode.
  2. The resolver preserves `canonical_uri` exactly (a drift would create
     a duplicate record at ingest — the silent-fork failure the harvester
     collision guard exists to prevent).

Live-gated on Globus reachability (anonymous read). One record per source
keeps it fast; the per-source coverage is what matters (each DataCite
subclass has its own round-trip risk).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from unittest.mock import patch

from apecx_harvesters.dict_reader import configure_dictionary_path
from apecx_harvesters.pipeline.canonical_resolver_adapter import make_resolver_for_source
from apecx_harvesters.pipeline.harmonize import DEST_REGISTRY, SOURCE_REGISTRY
from apecx_harvesters.pipeline.republish_with_canonical import (
    _assert_full_lineage_ready,
    _reparse_dest_content,
)

_DICT_PATH = Path(
    os.environ.get(
        "APECX_SYNONYM_DICT_PATH",
        str(Path.home() / ".apecx" / "dictionary" / "dictionary.sqlite"),
    )
)


def _globus_reachable() -> bool:
    try:
        import globus_sdk

        globus_sdk.SearchClient().post_search(
            DEST_REGISTRY["b676edbe-3286-4514-bc13-5cbe891c4bb1"], {"q": "*", "limit": 0}
        )
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _globus_reachable(), reason="Globus Search unreachable"),
]


# (source_uuid, source_name) for all 9 registered sources.
_SOURCES = [(uuid, name) for uuid, (name, _) in SOURCE_REGISTRY.items()]


def _fetch_sample(dest_uuid: str, n: int = 3) -> list[dict]:
    import globus_sdk

    c = globus_sdk.SearchClient()
    r = c.post_search(dest_uuid, {"q": "*", "limit": n})
    return [g["entries"][0]["content"] for g in r.data.get("gmeta", []) if g.get("entries")]


@pytest.mark.parametrize("source_uuid,source_name", _SOURCES, ids=[n for _, n in _SOURCES])
def test_strict_false_roundtrip_per_source(source_uuid: str, source_name: str) -> None:
    """A real DEST record re-parses under strict=False without raising."""
    dest_uuid = DEST_REGISTRY[source_uuid]
    _, parser = SOURCE_REGISTRY[source_uuid]
    samples = _fetch_sample(dest_uuid)
    assert samples, f"{source_name}: no DEST records to round-trip"
    for content in samples:
        record = _reparse_dest_content(content, parser, content.get("id", "?"))
        # Re-serialising must also not raise (the ingest path calls to_dict).
        round_tripped = record.to_dict()
        assert isinstance(round_tripped, dict)


@pytest.mark.parametrize("source_uuid,source_name", _SOURCES, ids=[n for _, n in _SOURCES])
def test_canonical_uri_stable_through_resolver(source_uuid: str, source_name: str) -> None:
    """The resolver must preserve canonical_uri exactly (no silent fork)."""
    if not _DICT_PATH.exists():
        pytest.skip(f"dictionary not present at {_DICT_PATH}")
    configure_dictionary_path(_DICT_PATH)
    dest_uuid = DEST_REGISTRY[source_uuid]
    name, parser = SOURCE_REGISTRY[source_uuid]
    resolver = make_resolver_for_source(name)
    samples = _fetch_sample(dest_uuid, n=5)
    assert samples, f"{source_name}: no DEST records"
    for content in samples:
        record = _reparse_dest_content(content, parser, content.get("id", "?"))
        resolved = resolver(record)
        assert resolved.canonical_uri == record.canonical_uri, (
            f"{source_name}: canonical_uri drifted "
            f"{record.canonical_uri!r} → {resolved.canonical_uri!r}"
        )


@pytest.mark.parametrize("source_uuid,source_name", _SOURCES, ids=[n for _, n in _SOURCES])
def test_resolver_idempotent_on_rerun(source_uuid: str, source_name: str) -> None:
    """Re-applying the resolver to an already-resolved record adds nothing.

    This is what makes per-source republish safe to retry: the dedup-by-
    valueUri keeps a second pass a no-op.
    """
    if not _DICT_PATH.exists():
        pytest.skip(f"dictionary not present at {_DICT_PATH}")
    configure_dictionary_path(_DICT_PATH)
    dest_uuid = DEST_REGISTRY[source_uuid]
    name, parser = SOURCE_REGISTRY[source_uuid]
    resolver = make_resolver_for_source(name)
    for content in _fetch_sample(dest_uuid, n=5):
        record = _reparse_dest_content(content, parser, content.get("id", "?"))
        once = resolver(record)
        twice = resolver(once)
        assert {s.valueUri for s in (once.subjects or [])} == {
            s.valueUri for s in (twice.subjects or [])
        }, f"{source_name}: re-resolve changed the subject set (not idempotent)"


# ---------------------------------------------------------------------------
# FIX 3: protabank full-lineage republish must FAIL LOUD (not silently under-
# stamp) when the dictionary lacks a taxon_hierarchy. The assert is a no-op for
# every other source. The function does a local import of get_dictionary_index
# from dict_reader.loader, so we patch it at that module.
# ---------------------------------------------------------------------------


class _StubHierarchyIndex:
    """Minimal stand-in exposing only has_hierarchy."""

    def __init__(self, has_hierarchy: bool):
        self._has_hierarchy = has_hierarchy

    @property
    def has_hierarchy(self) -> bool:
        return self._has_hierarchy


def test_assert_full_lineage_ready_raises_for_protabank_without_hierarchy():
    with patch(
        "apecx_harvesters.dict_reader.loader.get_dictionary_index",
        return_value=(None, "not set"),
    ):
        with pytest.raises(RuntimeError, match="taxon_hierarchy"):
            _assert_full_lineage_ready("protabank")


def test_assert_full_lineage_ready_noop_for_other_sources():
    """Even with a missing/hierarchy-less dictionary, non-protabank sources are
    untouched — the guard must not raise."""
    with patch(
        "apecx_harvesters.dict_reader.loader.get_dictionary_index",
        return_value=(None, "not set"),
    ):
        _assert_full_lineage_ready("bvbrc_protein")  # must not raise


def test_assert_full_lineage_ready_passes_when_hierarchy_present():
    with patch(
        "apecx_harvesters.dict_reader.loader.get_dictionary_index",
        return_value=(_StubHierarchyIndex(has_hierarchy=True), None),
    ):
        _assert_full_lineage_ready("protabank")  # must not raise
