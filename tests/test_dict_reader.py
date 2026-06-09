"""dict_reader extraction tests.

Two layers:

1. Unit tests against a small synthetic SQLite dict built in-process —
   verify normalization, fast/ambiguous/miss path routing, and
   schema-version rejection.

2. Pin tests against the production
   ``~/.apecx/dictionary/dictionary.sqlite`` AND the upstream
   ``apecx-lookup`` CLI. The extracted reader and apecx-lookup MUST
   return the same ``path`` + ``canonical_iri`` + ``candidates`` count
   for every probe term, or future drift will silently desync the
   user-facing arm from the backend arm. Skips when either dependency
   is absent (CI without the dict file; venv without apecx-lookup).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from apecx_harvesters.dict_reader import (
    EntityType,
    LookupResult,
    ResolutionStatus,
    configure_dictionary_path,
    default_dictionary_path,
    lookup_entity,
)
from apecx_harvesters.dict_reader.normalization import normalize_surface_form
from apecx_harvesters.dict_reader.sqlite_reader import (
    SQLiteDictionaryReader,
    _MANIFEST_ROW_KEY,
)


# ---------------------------------------------------------------------------
# Layer 1 — unit tests against a synthetic in-process dict
# ---------------------------------------------------------------------------


def _build_test_dict(path: Path, schema_version: str = "1.0.0") -> None:
    """Materialize a minimum-viable dict at ``path`` for tests."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE entries (
            entity_type TEXT NOT NULL,
            canonical_iri TEXT NOT NULL,
            canonical_label TEXT NOT NULL,
            ontology TEXT NOT NULL,
            ontology_version TEXT NOT NULL,
            confidence REAL NOT NULL,
            resolved_at TEXT NOT NULL,
            source_records_json TEXT NOT NULL,
            synonyms_json TEXT NOT NULL,
            PRIMARY KEY (entity_type, canonical_iri)
        );
        CREATE TABLE inverse_index (
            entity_type TEXT NOT NULL,
            surface_form_normalized TEXT NOT NULL,
            canonical_iri TEXT NOT NULL,
            PRIMARY KEY (entity_type, surface_form_normalized)
        );
        """
    )
    manifest = {
        "schema_version": schema_version,
        "dictionary_version": "test-2026-06-08",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "harvester_version": None,
        "ontology_versions": {"ncbitaxon": "test"},
        "record_counts_per_entity_type": {},
        "unresolved_count": 0,
        "record_count_total": 0,
    }
    conn.execute(
        "INSERT INTO manifest (key, value) VALUES (?, ?)",
        (_MANIFEST_ROW_KEY, json.dumps(manifest)),
    )
    rows = [
        # (entity_type, iri, label, synonyms, confidence)
        ("pathogen", "http://x/T1", "Test virus one", ["TV1", "TV-1"], 1.0),
        ("pathogen", "http://x/T2", "Test virus two", ["TV2"], 0.95),
        # Ambiguous surface: "amb" maps to two distinct IRIs.
        ("pathogen", "http://x/A1", "Ambiguous virus one", ["AMB"], 1.0),
        ("pathogen", "http://x/A2", "Ambiguous virus two", ["AMB"], 1.0),
    ]
    now = datetime.now(timezone.utc).isoformat()
    for et, iri, label, syns, conf in rows:
        conn.execute(
            "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (et, iri, label, "ncbitaxon", "test", conf, now,
             json.dumps([]), json.dumps(syns)),
        )
        for sf in (label, *syns):
            normalized = normalize_surface_form(sf)
            conn.execute(
                "INSERT OR IGNORE INTO inverse_index VALUES (?, ?, ?)",
                (et, normalized, iri),
            )
    conn.commit()
    conn.close()


@pytest.fixture
def synthetic_dict(tmp_path: Path) -> Path:
    p = tmp_path / "dict.sqlite"
    _build_test_dict(p)
    return p


def test_normalize_idempotent() -> None:
    s = "  Eastern Equine ENCEPHALITIS  Virus "
    once = normalize_surface_form(s)
    twice = normalize_surface_form(once)
    assert once == twice
    assert once == "eastern equine encephalitis virus"


def test_normalize_unicode_nfkc_casefold() -> None:
    # German ß becomes "ss" under casefold.
    assert normalize_surface_form("MASSE") == normalize_surface_form("maße")


def test_lookup_fast_hit(synthetic_dict: Path) -> None:
    configure_dictionary_path(synthetic_dict)
    # Configure resets the singleton; force reload.
    r = lookup_entity("TV1", entity_type=EntityType.PATHOGEN)
    assert isinstance(r, LookupResult)
    assert r.path == "fast"
    assert r.canonical_iri == "http://x/T1"
    assert r.canonical_label == "Test virus one"
    assert r.resolution_status == ResolutionStatus.ID_ANCHORED


def test_lookup_ambiguous(synthetic_dict: Path) -> None:
    configure_dictionary_path(synthetic_dict)
    r = lookup_entity("AMB", entity_type=EntityType.PATHOGEN)
    assert r.path == "ambiguous"
    assert r.canonical_iri is None
    assert len(r.candidates) == 2
    iris = {c.canonical_iri for c in r.candidates}
    assert iris == {"http://x/A1", "http://x/A2"}


def test_lookup_miss(synthetic_dict: Path) -> None:
    configure_dictionary_path(synthetic_dict)
    r = lookup_entity("does-not-exist-anywhere", entity_type=EntityType.PATHOGEN)
    assert r.path == "miss"
    assert r.canonical_iri is None


def test_lookup_empty_input(synthetic_dict: Path) -> None:
    configure_dictionary_path(synthetic_dict)
    assert lookup_entity("").path == "miss"
    assert lookup_entity("   ").path == "miss"


def test_reader_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    """The reader's SUPPORTED_SCHEMA_MAJOR is (1,) — a v2 dict must fail loudly."""
    p = tmp_path / "future.sqlite"
    _build_test_dict(p, schema_version="2.0.0")
    with pytest.raises(ValueError, match="schema major v2 not supported"):
        SQLiteDictionaryReader(p)


# ---------------------------------------------------------------------------
# Layer 2 — pin parity with the production dict + apecx-lookup CLI
# ---------------------------------------------------------------------------


PROD_DICT = default_dictionary_path()
APECX_LOOKUP = shutil.which("apecx-lookup") or shutil.which(
    "/Users/onarykov/Downloads/apecx-cowork/apecx-mcp-integration/.venv/bin/apecx-lookup"
)


PIN_TERMS = ["CHIKV", "EEEV", "TBEV", "MAYV", "WEEV", "RSV", "HIV", "Sindbis virus"]


@pytest.mark.skipif(not PROD_DICT.exists(), reason="production dict not available")
def test_production_dict_loads() -> None:
    """The 247 MB production dict loads cleanly into the extracted reader."""
    configure_dictionary_path(PROD_DICT)
    r = lookup_entity("CHIKV")
    assert r.path == "fast"
    assert r.canonical_iri is not None and "37124" in r.canonical_iri


@pytest.mark.skipif(
    not (PROD_DICT.exists() and APECX_LOOKUP),
    reason="apecx-lookup CLI or production dict unavailable",
)
@pytest.mark.parametrize("term", PIN_TERMS)
def test_parity_with_apecx_lookup(term: str) -> None:
    """Extracted reader's path + canonical_iri + candidate count MUST match
    the upstream apecx-lookup CLI verbatim. Drift here means the user-facing
    arm silently desynced from the build arm."""
    configure_dictionary_path(PROD_DICT)
    ours = lookup_entity(term)
    proc = subprocess.run(
        [APECX_LOOKUP, term, "--json"], capture_output=True, text=True, check=False
    )
    upstream = json.loads(proc.stdout)
    assert ours.path == upstream["path"], (
        f"path mismatch for {term!r}: ours={ours.path} upstream={upstream['path']}"
    )
    assert ours.canonical_iri == upstream.get("canonical_iri"), (
        f"IRI mismatch for {term!r}"
    )
    assert len(ours.candidates) == len(upstream.get("candidates") or []), (
        f"candidate count mismatch for {term!r}"
    )
