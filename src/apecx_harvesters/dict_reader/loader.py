"""In-memory loader for the apecx synonym dictionary.

Loads the SQLite artifact produced by the build/ingest arm into a Python
data structure that supports O(1) lookup. Held as a process singleton so
loading happens once per process and concurrent reads are lock-free
(plain dict reads hold the GIL in CPython).

Ported from ``apecx_integration.synonym_dictionary.loader`` (2026-06-08)
with one deliberate cut: the slow-path fallback that hit
``apecx_integration.mcp_surface.data.database`` has been removed.
Consumers using this reader receive ``path: "miss"`` when the fast +
ancestor + fuzzy paths all miss; they are expected to handle that
explicitly (e.g., by surfacing it to a user) rather than chaining
into a heavyweight pandas-loaded text search.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

from apecx_harvesters.dict_reader.enums import EntityType
from apecx_harvesters.dict_reader.normalization import normalize_surface_form
from apecx_harvesters.dict_reader.schema import BuildManifest, DictionaryEntry
from apecx_harvesters.dict_reader.sqlite_reader import SQLiteDictionaryReader

log = logging.getLogger(__name__)

_NOT_LOADED = object()
_NCBITAXON_OBO_PREFIX = "http://purl.obolibrary.org/obo/NCBITaxon_"

_ANCESTOR_CTE = """
WITH RECURSIVE anc(id, depth) AS (
    SELECT h.parent_taxon_id, 1
    FROM   taxon_hierarchy h
    WHERE  h.child_taxon_id = :taxon_id
    UNION ALL
    SELECT h.parent_taxon_id, anc.depth + 1
    FROM   taxon_hierarchy h
    JOIN   anc ON h.child_taxon_id = anc.id
    WHERE  anc.id != 1
)
SELECT id FROM anc ORDER BY depth
"""

_DESCENDANT_CTE = """
WITH RECURSIVE desc_tree(id, depth) AS (
    SELECT h.child_taxon_id, 1
    FROM   taxon_hierarchy h
    WHERE  h.parent_taxon_id = :taxon_id
    UNION ALL
    SELECT h.child_taxon_id, desc_tree.depth + 1
    FROM   taxon_hierarchy h
    JOIN   desc_tree ON h.parent_taxon_id = desc_tree.id
)
SELECT id FROM desc_tree ORDER BY depth
"""


class DictionaryIndex:
    """In-memory index loaded from a SQLite dictionary artifact."""

    def __init__(
        self,
        *,
        inverse: dict[tuple[str, str], tuple[str, ...]],
        entries: dict[str, DictionaryEntry],
        manifest: BuildManifest,
        db_path: Path | None = None,
        has_hierarchy: bool = False,
    ) -> None:
        self._inverse = inverse
        self._entries = entries
        self._manifest = manifest
        self._db_path = db_path
        self._has_hierarchy = has_hierarchy
        self._trigram_index: dict[str, set[tuple[str, str]]] | None = None
        self._sf_trigrams: dict[tuple[str, str], frozenset[str]] | None = None
        self._fuzzy_lock = threading.Lock()

    @property
    def manifest(self) -> BuildManifest:
        return self._manifest

    @property
    def has_hierarchy(self) -> bool:
        return self._has_hierarchy

    def lookup(
        self, entity_type: EntityType, surface_form: str
    ) -> DictionaryEntry | None:
        """Single-result fast-path. Returns None for both miss and ambiguous."""
        normalized = normalize_surface_form(surface_form)
        if not normalized:
            return None
        candidates = self._inverse.get((entity_type.value, normalized), ())
        if len(candidates) != 1:
            return None
        return self._entries.get(candidates[0])

    def lookup_all(
        self, entity_type: EntityType, surface_form: str
    ) -> tuple[DictionaryEntry, ...]:
        """Return ALL candidate entries (SC-A4b ambiguity-aware)."""
        normalized = normalize_surface_form(surface_form)
        if not normalized:
            return ()
        iris = self._inverse.get((entity_type.value, normalized), ())
        results: list[DictionaryEntry] = []
        for iri in iris:
            entry = self._entries.get(iri)
            if entry is not None:
                results.append(entry)
        results.sort(key=lambda e: e.confidence, reverse=True)
        return tuple(results)

    def lookup_any_type(self, surface_form: str) -> list[DictionaryEntry]:
        """Search across all entity types, ordered by confidence."""
        normalized = normalize_surface_form(surface_form)
        if not normalized:
            return []
        results: list[DictionaryEntry] = []
        seen_iris: set[str] = set()
        for (_, norm_form), iris in self._inverse.items():
            if norm_form != normalized:
                continue
            for iri in iris:
                if iri in seen_iris:
                    continue
                entry = self._entries.get(iri)
                if entry is not None:
                    results.append(entry)
                    seen_iris.add(iri)
        results.sort(key=lambda e: e.confidence, reverse=True)
        return results

    def lookup_by_iri(self, canonical_iri: str) -> DictionaryEntry | None:
        return self._entries.get(canonical_iri)

    def is_taxon_deleted(self, iri: str) -> bool:
        """SC-A5: True if ``iri`` points to a taxon in delnodes.dmp."""
        if self._db_path is None or not iri.startswith(_NCBITAXON_OBO_PREFIX):
            return False
        try:
            taxon_id = int(iri[len(_NCBITAXON_OBO_PREFIX):])
        except ValueError:
            return False
        try:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT 1 FROM deleted_taxons WHERE taxon_id = ? LIMIT 1",
                    (taxon_id,),
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            return False
        return row is not None

    def lookup_ancestor(self, iri: str) -> DictionaryEntry | None:
        """Walk the NCBITaxon hierarchy upward to the nearest in-dict ancestor."""
        if not self._has_hierarchy or self._db_path is None:
            return None
        if not iri.startswith(_NCBITAXON_OBO_PREFIX):
            return None
        try:
            taxon_id = int(iri[len(_NCBITAXON_OBO_PREFIX):])
        except ValueError:
            return None
        try:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                merge_row = conn.execute(
                    "SELECT new_taxon_id FROM merged_taxons WHERE old_taxon_id = ?",
                    (taxon_id,),
                ).fetchone()
                if merge_row:
                    taxon_id = merge_row[0]
                rows = conn.execute(
                    _ANCESTOR_CTE, {"taxon_id": taxon_id}
                ).fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.debug("ancestor traversal error for %s: %s", iri, exc)
            return None
        for (ancestor_id,) in rows:
            ancestor_iri = f"{_NCBITAXON_OBO_PREFIX}{ancestor_id}"
            entry = self._entries.get(ancestor_iri)
            if entry is not None:
                return entry
        return None

    def lookup_descendant_taxon_ids(self, iri: str) -> list[int]:
        """Return all descendant NCBITaxon integer IDs for ``iri``."""
        if not self._has_hierarchy or self._db_path is None:
            return []
        if not iri.startswith(_NCBITAXON_OBO_PREFIX):
            return []
        try:
            taxon_id = int(iri[len(_NCBITAXON_OBO_PREFIX):])
        except ValueError:
            return []
        try:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                merge_row = conn.execute(
                    "SELECT new_taxon_id FROM merged_taxons WHERE old_taxon_id = ?",
                    (taxon_id,),
                ).fetchone()
                if merge_row:
                    taxon_id = merge_row[0]
                rows = conn.execute(
                    _DESCENDANT_CTE, {"taxon_id": taxon_id}
                ).fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.debug("descendant traversal error for %s: %s", iri, exc)
            return []
        return [row[0] for row in rows]

    def entry_count(self) -> int:
        return len(self._entries)

    def index_entry_count(self) -> int:
        return len(self._inverse)

    @staticmethod
    def _trigrams_of(text: str) -> frozenset[str]:
        if not text:
            return frozenset()
        padded = "  " + text + "  "
        return frozenset(padded[i:i + 3] for i in range(len(padded) - 2))

    def _ensure_trigram_index(self) -> None:
        if self._trigram_index is not None:
            return
        with self._fuzzy_lock:
            if self._trigram_index is not None:
                return
            start = time.monotonic()
            inverted: dict[str, set[tuple[str, str]]] = {}
            per_sf: dict[tuple[str, str], frozenset[str]] = {}
            for key in self._inverse:
                tris = self._trigrams_of(key[1])
                if not tris:
                    continue
                per_sf[key] = tris
                for tri in tris:
                    inverted.setdefault(tri, set()).add(key)
            self._trigram_index = inverted
            self._sf_trigrams = per_sf
            elapsed = time.monotonic() - start
            log.info(
                "trigram index built: %d trigrams over %d surface forms in %.2fs",
                len(inverted), len(per_sf), elapsed,
            )

    def lookup_fuzzy(
        self,
        surface_form: str,
        *,
        entity_type: EntityType | None = None,
        threshold: float = 0.70,
        limit: int = 20,
    ) -> tuple[tuple[DictionaryEntry, float], ...]:
        """SC-C5: trigram-Jaccard fuzzy fallback."""
        normalized = normalize_surface_form(surface_form)
        if not normalized:
            return ()
        self._ensure_trigram_index()
        assert self._trigram_index is not None and self._sf_trigrams is not None
        q_trigrams = self._trigrams_of(normalized)
        if not q_trigrams:
            return ()
        candidate_counts: Counter[tuple[str, str]] = Counter()
        for tri in q_trigrams:
            for key in self._trigram_index.get(tri, ()):
                if entity_type is None or key[0] == entity_type.value:
                    candidate_counts[key] += 1
        min_shared = max(1, int(threshold * len(q_trigrams)))
        scored: list[tuple[DictionaryEntry, float]] = []
        for key, shared in candidate_counts.items():
            if shared < min_shared:
                continue
            cand_trigrams = self._sf_trigrams[key]
            union_size = len(q_trigrams) + len(cand_trigrams) - shared
            if union_size == 0:
                continue
            jaccard = shared / union_size
            if jaccard < threshold:
                continue
            for iri in self._inverse[key]:
                entry = self._entries.get(iri)
                if entry is None:
                    continue
                scored.append((entry, jaccard))
        best_by_iri: dict[str, tuple[DictionaryEntry, float]] = {}
        for entry, conf in scored:
            cur = best_by_iri.get(entry.canonical_iri)
            if cur is None or conf > cur[1]:
                best_by_iri[entry.canonical_iri] = (entry, conf)
        ordered = sorted(best_by_iri.values(), key=lambda x: x[1], reverse=True)
        return tuple(ordered[:limit])

    @classmethod
    def load(cls, path: Path | str) -> "DictionaryIndex":
        """Load a SQLite dictionary artifact into memory."""
        path = Path(path)
        reader = SQLiteDictionaryReader(path)
        manifest = reader.read_manifest()

        inverse_acc: dict[tuple[str, str], list[str]] = {}
        entries: dict[str, DictionaryEntry] = {}
        for entry in reader.all_entries():
            entries[entry.canonical_iri] = entry
            for surface in (entry.canonical_label, *entry.synonyms):
                normalized = normalize_surface_form(surface)
                if not normalized:
                    continue
                key = (entry.entity_type.value, normalized)
                bucket = inverse_acc.setdefault(key, [])
                if entry.canonical_iri not in bucket:
                    bucket.append(entry.canonical_iri)
        inverse: dict[tuple[str, str], tuple[str, ...]] = {
            k: tuple(v) for k, v in inverse_acc.items()
        }
        ambiguous_count = sum(1 for v in inverse.values() if len(v) > 1)
        has_hierarchy = reader.has_taxon_hierarchy()
        log.info(
            "loaded dictionary %s: %d entries, %d index rows, "
            "%d ambiguous surface forms (version %s, hierarchy=%s)",
            path, len(entries), len(inverse), ambiguous_count,
            manifest.dictionary_version, has_hierarchy,
        )
        return cls(
            inverse=inverse,
            entries=entries,
            manifest=manifest,
            db_path=path,
            has_hierarchy=has_hierarchy,
        )


class _ProcessSingleton:
    """Lazy process-singleton holder for the DictionaryIndex."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._index: DictionaryIndex | object = _NOT_LOADED
        self._path: Path | None = None
        self._error: str | None = None

    def configure(self, path: Path | str) -> None:
        with self._lock:
            new_path = Path(path)
            if new_path != self._path:
                self._path = new_path
                self._index = _NOT_LOADED
                self._error = None

    def get(self) -> tuple[DictionaryIndex | None, str | None]:
        if self._index is not _NOT_LOADED:
            if isinstance(self._index, DictionaryIndex):
                return self._index, None
            return None, self._error
        with self._lock:
            if self._index is _NOT_LOADED:
                if self._path is None:
                    self._error = (
                        "APECX_SYNONYM_DICT_PATH not set; lookup disabled. "
                        "Run `apecx-dict-update` to bootstrap the dictionary "
                        "from the published Globus path, then re-try."
                    )
                    return None, self._error
                try:
                    self._index = DictionaryIndex.load(self._path)
                except Exception as exc:
                    self._error = (
                        f"Failed to load dictionary from {self._path}: {exc}"
                    )
                    log.warning(self._error)
                    return None, self._error
            if isinstance(self._index, DictionaryIndex):
                return self._index, None
            return None, self._error


_singleton = _ProcessSingleton()


def configure_dictionary_path(path: Path | str) -> None:
    """Set the process-wide dictionary artifact path. Idempotent."""
    _singleton.configure(path)


def get_dictionary_index() -> tuple[DictionaryIndex | None, str | None]:
    """Return ``(index, None)`` on success or ``(None, error_message)``."""
    return _singleton.get()


def default_dictionary_path() -> Path:
    """The canonical local path: ``~/.apecx/dictionary/dictionary.sqlite``.

    Both the bootstrap (downloads here) and the lookup (loads from here)
    agree on this single location — there is no second canonical path.
    """
    import os
    env = os.environ.get("APECX_SYNONYM_DICT_PATH")
    if env:
        return Path(env)
    return Path.home() / ".apecx" / "dictionary" / "dictionary.sqlite"
