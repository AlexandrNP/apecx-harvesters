"""SQLite reader for the apecx synonym dictionary.

Read-only access to the SQLite artifact produced by the build/ingest
arm. Exposes :class:`SQLiteDictionaryReader` — the writer side stays in
``apecx_integration`` (build pipeline), this side travels with the
runtime-lookup arm so consumers don't pull in the writer's deps.

Ported from ``apecx_integration.synonym_dictionary.sqlite_writer``
(2026-06-08) — read methods only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from apecx_harvesters.dict_reader.enums import EntityType, OntologyName
from apecx_harvesters.dict_reader.normalization import normalize_surface_form
from apecx_harvesters.dict_reader.schema import BuildManifest, DictionaryEntry

_MANIFEST_ROW_KEY = "manifest_json"


class SQLiteDictionaryReader:
    """Read-only access to a SQLite dictionary artifact.

    Constructed eagerly: opens the SQLite connection in read-only mode
    and validates the schema version at construction. Callers that
    can't tolerate an incompatible-version dict get a loud
    ``ValueError`` instead of a silent miss at first lookup.
    """

    # Major schema versions this reader supports. Bump in lockstep with
    # the writer when introducing breaking changes.
    SUPPORTED_SCHEMA_MAJOR: tuple[int, ...] = (1,)

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        if not self._path.exists():
            raise FileNotFoundError(f"dictionary artifact not found: {self._path}")
        self._conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row
        manifest = self.read_manifest()
        major = int(manifest.schema_version.split(".", 1)[0])
        if major not in self.SUPPORTED_SCHEMA_MAJOR:
            raise ValueError(
                f"dictionary schema major v{major} not supported by this "
                f"reader (supported: {self.SUPPORTED_SCHEMA_MAJOR})"
            )

    def read_manifest(self) -> BuildManifest:
        row = self._conn.execute(
            "SELECT value FROM manifest WHERE key = ?",
            (_MANIFEST_ROW_KEY,),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"dictionary at {self._path} has no manifest row — "
                f"build was incomplete"
            )
        return BuildManifest.model_validate_json(row["value"])

    def lookup_by_surface_form(
        self, entity_type: EntityType, surface_form: str
    ) -> DictionaryEntry | None:
        normalized = normalize_surface_form(surface_form)
        if not normalized:
            return None
        row = self._conn.execute(
            "SELECT canonical_iri FROM inverse_index "
            "WHERE entity_type = ? AND surface_form_normalized = ?",
            (entity_type.value, normalized),
        ).fetchone()
        if row is None:
            return None
        return self.lookup_by_iri(row["canonical_iri"])

    def lookup_by_iri(self, canonical_iri: str) -> DictionaryEntry | None:
        row = self._conn.execute(
            "SELECT entity_type, canonical_iri, canonical_label, ontology, "
            "ontology_version, confidence, resolved_at, "
            "source_records_json, synonyms_json FROM entries "
            "WHERE canonical_iri = ?",
            (canonical_iri,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def has_taxon_hierarchy(self) -> bool:
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM taxon_hierarchy LIMIT 1"
            ).fetchone()
            return bool(row and row[0] > 0)
        except sqlite3.OperationalError:
            return False

    def all_entries(self) -> Iterator[DictionaryEntry]:
        for row in self._conn.execute(
            "SELECT entity_type, canonical_iri, canonical_label, ontology, "
            "ontology_version, confidence, resolved_at, "
            "source_records_json, synonyms_json FROM entries"
        ):
            yield self._row_to_entry(row)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SQLiteDictionaryReader":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> DictionaryEntry:
        return DictionaryEntry(
            entity_type=EntityType(row["entity_type"]),
            canonical_iri=row["canonical_iri"],
            canonical_label=row["canonical_label"],
            ontology=OntologyName(row["ontology"]),
            ontology_version=row["ontology_version"],
            confidence=row["confidence"],
            resolved_at=datetime.fromisoformat(row["resolved_at"]),
            source_records=tuple(json.loads(row["source_records_json"])),
            synonyms=tuple(json.loads(row["synonyms_json"])),
        )
