"""Read DEST index, enrich subjects via the dictionary, re-ingest.

End-of-pipeline phase that closes the harmonization loop:

    SOURCE → harmonize → ingest to DEST (existing path)
                                          │
                                          ▼
                              this module ─→ DEST (with subjects.valueUri)

Re-ingest is idempotent on ``canonical_uri`` (Globus Search keys on
``subject`` == our canonical_uri), so partial runs are restartable —
re-running over already-enriched records is a no-op data-wise.

Per-record contract:
- Read record from DEST as DataCite-shaped content.
- Re-parse to the registered DataCite subclass (per
  ``apecx_harvesters.scripts.harmonize_and_publish.SOURCE_REGISTRY``) so
  the typed extension fields are available to the resolver.
- Apply the source's resolver from
  :func:`canonical_resolver_adapter.make_resolver_for_source`.
- Re-emit via ``to_gmetalist`` + ``client.ingest``.

The strict-round-trip risk (DataCite emits enum fields as strings that
strict re-validation refuses to coerce) is handled by parsing the DEST
content with ``strict=False`` on the re-validate step. The original
record's ``canonical_uri`` is asserted stable after the round-trip; a
mismatch fails the entire batch (silent rename would publish duplicate
records — exactly the failure mode the harvester collision guard exists
to prevent).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import globus_sdk

from apecx_harvesters.loaders.base import DataCite
from apecx_harvesters.loaders.base.retrieve import RetrievalResult
from apecx_harvesters.pipeline.canonical_resolver_adapter import (
    make_resolver_for_source,
)
from apecx_harvesters.pipeline.globus_source import scroll_index_records
from apecx_harvesters.pipeline.harmonize import (
    DEST_REGISTRY,
    SOURCE_REGISTRY,
    PIPELINE_VERSION,
)
from apecx_harvesters.pipeline.sinks import to_gmetalist

log = logging.getLogger(__name__)

REPUBLISH_VERSION = "republish-with-canonical/0.1"


class CanonicalUriDriftError(RuntimeError):
    """The round-tripped record's canonical_uri changed.

    Republishing under a different canonical_uri would create a duplicate
    record (orphan'ing the original); refuse rather than silently fork.
    """


@dataclass
class RepublishStats:
    """Aggregated counters for one republish run."""

    source_name: str = ""
    source_index: str = ""
    dest_index: str = ""
    dictionary_version: str = ""
    repository_version: str = REPUBLISH_VERSION
    records_read: int = 0
    records_resolved: int = 0
    records_subjects_added: int = 0
    records_unchanged: int = 0
    records_skipped: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)
    ingest_batches: int = 0
    ingest_states: list[str] = field(default_factory=list)
    timestamp_utc: str = ""

    @property
    def all_success(self) -> bool:
        return bool(self.ingest_states) and all(
            s == "SUCCESS" for s in self.ingest_states
        )

    @property
    def skipped_fraction(self) -> float:
        denom = self.records_read or 1
        return self.records_skipped / denom


async def republish_index(
    *,
    dest_uuid: str,
    source_uuid: str,
    client: globus_sdk.SearchClient,
    visible_to: list[str] | None = None,
    max_skipped_fraction: float = 0.01,
    page_size: int = 1000,
) -> RepublishStats:
    """Resolve + re-ingest every record in ``dest_uuid``.

    Caller supplies BOTH the dest uuid (where records are read from /
    written to) and the source uuid (whose parser/subclass + organism
    slots determine resolution). FAIL-LOUD on skipped > threshold.
    """
    if source_uuid not in SOURCE_REGISTRY:
        raise KeyError(f"no parser registered for source {source_uuid!r}")
    name, parser = SOURCE_REGISTRY[source_uuid]
    _assert_full_lineage_ready(name)
    resolver = make_resolver_for_source(
        name,
        violin_pathogen_crosswalk=await _maybe_violin_crosswalk(name, client),
        uniprot_taxid_map=await _maybe_uniprot_taxid_map(
            name, dest_uuid, client, page_size=page_size
        ),
        full_lineage=(name == "protabank"),
    )

    stats = RepublishStats(
        source_name=name,
        source_index=source_uuid,
        dest_index=dest_uuid,
        dictionary_version=_resolve_dictionary_version(),
        timestamp_utc=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )

    async def _stream() -> AsyncIterator[RetrievalResult[Any]]:
        async for rec in scroll_index_records(
            dest_uuid, client=client, query="*", page_size=page_size
        ):
            stats.records_read += 1
            subject = rec["subject"]
            content = rec["content"] or {}
            # The DEST shape is the DataCite subclass's ``to_dict`` output.
            # Re-validate with ``strict=False`` so enum-as-string round-trips.
            try:
                record = _reparse_dest_content(content, parser, subject)
            except Exception as exc:  # noqa: BLE001
                stats.records_skipped += 1
                stats.skipped.append(
                    {
                        "subject": subject,
                        "stage": "reparse",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            try:
                resolved = resolver(record)
            except Exception as exc:  # noqa: BLE001
                stats.records_skipped += 1
                stats.skipped.append(
                    {
                        "subject": subject,
                        "stage": "resolve",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            if resolved.canonical_uri != record.canonical_uri:
                raise CanonicalUriDriftError(
                    f"canonical_uri drift on {subject!r}: "
                    f"pre={record.canonical_uri!r} post={resolved.canonical_uri!r}"
                )
            before = len(record.subjects or [])
            after = len(resolved.subjects or [])
            if after > before:
                stats.records_subjects_added += 1
            else:
                stats.records_unchanged += 1
            stats.records_resolved += 1
            yield RetrievalResult(id=resolved.canonical_uri, record=resolved)

    task_ids: list[str] = []
    async for doc in to_gmetalist(_stream(), visible_to=visible_to):
        resp = await asyncio.to_thread(client.ingest, dest_uuid, doc)
        task_ids.append(resp["task_id"])
        stats.ingest_batches += 1

    if stats.skipped_fraction > max_skipped_fraction:
        raise RuntimeError(
            f"republish skipped fraction {stats.skipped_fraction:.4f} > "
            f"threshold {max_skipped_fraction:.4f}; "
            f"first skipped: {stats.skipped[:3]}"
        )

    states = await _wait_for_ingest(client, task_ids)
    stats.ingest_states = sorted(set(states.values()))
    return stats


@dataclass
class PreflightStats:
    """Read-only projection of what a republish WOULD do (no ingest)."""

    source_name: str = ""
    dest_index: str = ""
    dictionary_version: str = ""
    records_read: int = 0
    records_would_add_subjects: int = 0
    records_unchanged: int = 0
    records_reparse_failed: int = 0
    canonical_uri_stable: int = 0
    sample_subjects: list[dict[str, Any]] = field(default_factory=list)

    @property
    def canonical_uri_all_stable(self) -> bool:
        return self.records_read > 0 and self.canonical_uri_stable == self.records_read

    @property
    def subjects_fraction(self) -> float:
        return self.records_would_add_subjects / (self.records_read or 1)


async def preflight_index(
    *,
    dest_uuid: str,
    source_uuid: str,
    client: globus_sdk.SearchClient,
    page_size: int = 1000,
    sample_n: int = 5,
    max_records: int | None = None,
) -> PreflightStats:
    """READ-ONLY projection of a republish: resolve records, count what WOULD
    change, but ingest nothing.

    This is the OE-F0 gate. It caught the case-sensitivity zero-subject bug
    before the OE-G1 live write. Callers should refuse to republish a source
    whose pre-flight shows ``records_would_add_subjects == 0`` (either a real
    bug or a genuinely non-taxonomy-anchored source) or
    ``canonical_uri_all_stable is False``.

    ``max_records`` caps how many records are sampled. A few hundred is
    enough to detect a SYSTEMATIC zero-subject failure cheaply; pass ``None``
    to project the whole index (expensive on large sources).
    """
    if source_uuid not in SOURCE_REGISTRY:
        raise KeyError(f"no parser registered for source {source_uuid!r}")
    name, parser = SOURCE_REGISTRY[source_uuid]
    _assert_full_lineage_ready(name)
    resolver = make_resolver_for_source(
        name,
        violin_pathogen_crosswalk=await _maybe_violin_crosswalk(name, client),
        uniprot_taxid_map=await _maybe_uniprot_taxid_map(
            name, dest_uuid, client, max_records=max_records, page_size=page_size
        ),
        full_lineage=(name == "protabank"),
    )
    stats = PreflightStats(
        source_name=name,
        dest_index=dest_uuid,
        dictionary_version=_resolve_dictionary_version(),
    )
    async for rec in scroll_index_records(
        dest_uuid, client=client, query="*", page_size=page_size
    ):
        if max_records is not None and stats.records_read >= max_records:
            break
        stats.records_read += 1
        subject = rec["subject"]
        try:
            record = _reparse_dest_content(rec["content"] or {}, parser, subject)
        except Exception:  # noqa: BLE001
            stats.records_reparse_failed += 1
            continue
        resolved = resolver(record)
        if resolved.canonical_uri == record.canonical_uri:
            stats.canonical_uri_stable += 1
        before = len(record.subjects or [])
        after = len(resolved.subjects or [])
        if after > before:
            stats.records_would_add_subjects += 1
            if len(stats.sample_subjects) < sample_n:
                stats.sample_subjects.append(
                    {
                        "subject": subject,
                        "valueUris": [s.valueUri for s in (resolved.subjects or [])],
                    }
                )
        else:
            stats.records_unchanged += 1
    return stats


def _reparse_dest_content(
    content: dict[str, Any],
    parser: Callable[..., DataCite],
    subject: str,
) -> DataCite:
    """Reconstruct the DataCite subclass from a DEST record's content.

    The DEST content was produced by ``DataCite.to_dict`` (=
    ``model_dump(mode='json', exclude_none=True)``); strict round-trip
    fails because enum fields serialize to strings that the strict
    validator refuses to coerce. Workaround: re-parse via the registered
    source parser when the content shape matches the source-side
    document, OR re-validate the model directly with ``strict=False``.

    The DEST shape carries the parser's output, not the source's input,
    so we re-validate via the model class — the parser would re-derive
    fields from a source-side schema we no longer have.
    """
    # The parser was bound at import time and carries a reference to its
    # registered DataCite subclass via the closure on its return type.
    # We can't get the class without invoking the parser, so we DO invoke
    # it on the source-side shape only when ``content`` looks source-side
    # (has the source-native field names). Otherwise we re-validate the
    # already-DataCite shape with strict=False.
    cls = _datacite_cls_for_parser(parser)
    return cls.model_validate(content, strict=False)


_PARSER_TO_CLS: dict[Callable[..., DataCite], type[DataCite]] = {}


def _datacite_cls_for_parser(
    parser: Callable[..., DataCite],
) -> type[DataCite]:
    """Resolve the DataCite subclass bound to a parser, with caching.

    Each parser in ``SOURCE_REGISTRY`` is a thin function that returns a
    specific ``DataCite`` subclass. We don't invoke the parser to learn
    its return type (a parser may reject DEST-shaped input); instead we
    use the parser's qualified name + module to look up the registered
    subclass in :data:`_PARSER_CLS_REGISTRY` (populated lazily on first
    call).
    """
    cached = _PARSER_TO_CLS.get(parser)
    if cached is not None:
        return cached
    cls = _import_cls_for_parser(parser)
    _PARSER_TO_CLS[parser] = cls
    return cls


def _import_cls_for_parser(
    parser: Callable[..., DataCite],
) -> type[DataCite]:
    """Import the DataCite subclass that ``parser`` constructs.

    Convention: every loader package exports both ``parse_<source>``
    and ``<Source>Container``; the container is the DataCite subclass.
    Walk by parser module path.
    """
    module_name = parser.__module__
    package = module_name.rsplit(".", 1)[0]
    pkg = __import__(package, fromlist=["*"])
    for attr in dir(pkg):
        obj = getattr(pkg, attr)
        if (
            isinstance(obj, type)
            and issubclass(obj, DataCite)
            and obj is not DataCite
        ):
            return obj
    raise RuntimeError(
        f"no DataCite subclass exported by {package!r} for parser {parser!r}"
    )


async def _wait_for_ingest(
    client: globus_sdk.SearchClient,
    task_ids: list[str],
    *,
    timeout: float = 1800.0,
    poll_seconds: float = 2.0,
) -> dict[str, str]:
    """Poll ingest tasks to terminal state. ``{task_id: state}``."""
    states: dict[str, str] = {}
    for task_id in task_ids:
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = await asyncio.to_thread(client.get_task, task_id)
            state = resp.get("state", "PENDING")
            if state in ("SUCCESS", "FAILED"):
                states[task_id] = state
                break
            await asyncio.sleep(poll_seconds)
        else:
            states[task_id] = "TIMEOUT"
    return states


async def _maybe_violin_crosswalk(
    source_name: str, client: globus_sdk.SearchClient
) -> dict[int, int] | None:
    """Build the VIOLIN pathogen→taxon cross-walk, only for violin_vaccine.

    Returns None for every other source (no cross-table join applies).
    """
    if source_name != "violin_vaccine":
        return None
    from apecx_harvesters.pipeline.violin_crosswalk import (
        build_violin_pathogen_crosswalk,
    )

    return await build_violin_pathogen_crosswalk(client)


def _assert_full_lineage_ready(source_name: str) -> None:
    """Fail LOUD (not a silent no-op) when a protabank full-lineage republish is
    requested but the dictionary has no ``taxon_hierarchy`` — otherwise every
    record would get only its strain taxid and the post-ICTV-rename matching the
    whole feature exists for would silently fail while the run reports success."""
    if source_name != "protabank":
        return
    from apecx_harvesters.dict_reader.loader import get_dictionary_index

    index, err = get_dictionary_index()
    if index is None or not index.has_hierarchy:
        raise RuntimeError(
            "protabank full-lineage republish requires a dictionary with a "
            f"taxon_hierarchy table; got: {err or 'hierarchy table absent'}. "
            "Point APECX_SYNONYM_DICT_PATH at a hierarchy-bearing dictionary."
        )


async def _maybe_uniprot_taxid_map(
    source_name: str,
    dest_uuid: str,
    client: globus_sdk.SearchClient,
    *,
    max_records: int | None = None,
    page_size: int = 1000,
) -> dict[str, int] | None:
    """Pre-resolve every UniProt accession on the dest index to its organism
    taxid, in one batched pass — only for protabank.

    ProtaBank carries no organism name or taxid; its UniProt accession is the
    only taxon signal. Returns None for every other source. ``max_records``
    bounds the scroll so a preflight samples the same prefix the resolve loop does
    (``page_size`` matches the resolve loop so the two scrolls return the same prefix).
    """
    if source_name != "protabank":
        return None
    from apecx_harvesters.loaders.base.uniprot_client import (
        resolve_uniprot_taxids,
        split_accession_field,
    )

    accessions: list[str] = []
    seen: set[str] = set()
    read = 0
    async for rec in scroll_index_records(
        dest_uuid, client=client, query="*", page_size=page_size
    ):
        if max_records is not None and read >= max_records:
            break
        read += 1
        for alt in (rec.get("content") or {}).get("alternateIdentifiers") or []:
            if alt.get("alternateIdentifierType") != "UniProt":
                continue
            for acc in split_accession_field(alt.get("alternateIdentifier") or ""):
                if acc not in seen:
                    seen.add(acc)
                    accessions.append(acc)
    return await resolve_uniprot_taxids(accessions)


def _resolve_dictionary_version() -> str:
    """Read the dictionary version the resolver is bound to, for provenance.

    Best-effort: returns ``"unknown"`` if the dictionary can't be loaded
    (the republish still proceeds — version is metadata, not a gate).
    """
    try:
        from apecx_harvesters.dict_reader import get_dictionary_index

        index, err = get_dictionary_index()
        if index is None or err:
            return "unknown"
        return index.manifest.dictionary_version
    except Exception:  # noqa: BLE001
        return "unknown"


def dest_uuid_for_source(source_name: str) -> str:
    """Look up the DEST uuid for a source-name (e.g. ``"violin_pathogen"``)."""
    for source_uuid, (name, _) in SOURCE_REGISTRY.items():
        if name == source_name:
            return DEST_REGISTRY[source_uuid]
    raise KeyError(f"unknown source {source_name!r}")


def source_uuid_for_name(source_name: str) -> str:
    """Look up the SOURCE uuid for a source-name."""
    for source_uuid, (name, _) in SOURCE_REGISTRY.items():
        if name == source_name:
            return source_uuid
    raise KeyError(f"unknown source {source_name!r}")


__all__ = [
    "PreflightStats",
    "preflight_index",
    "REPUBLISH_VERSION",
    "PIPELINE_VERSION",
    "CanonicalUriDriftError",
    "RepublishStats",
    "dest_uuid_for_source",
    "source_uuid_for_name",
    "republish_index",
]
