"""Harmonize a Globus Search source index into DataCite records.

Ties the per-source parsers to the scroll reader, and adds the two reliability
guards the plan requires before any ingest:

* **Canonical-collision guard** (FAIL LOUD): Globus Search keys entries on
  ``subject`` == our ``canonical_uri``. Two records sharing one would silently
  overwrite each other at ingest. We refuse to proceed if any collide.
* **Drift guard**: the source ``total`` is read before and after the scrape;
  a change means the index mutated mid-scrape (a torn snapshot, e.g. the
  mid-reingest BVBRC:Genome). Surfaced in provenance, not swallowed.

Provenance is returned as a sidecar record (not stamped into the strict DataCite
documents), so harmonized content stays pure DataCite while lineage is auditable.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import globus_sdk

from apecx_harvesters.loaders.antiviraldb import parse_antiviraldb
from apecx_harvesters.loaders.base import DataCite
from apecx_harvesters.loaders.base.retrieve import RetrievalResult
from apecx_harvesters.loaders.bvbrc_epitope import parse_bvbrc_epitope
from apecx_harvesters.loaders.bvbrc_genome import parse_bvbrc_genome
from apecx_harvesters.loaders.bvbrc_protein import parse_bvbrc_protein
from apecx_harvesters.loaders.bvbrc_protein_structure import parse_bvbrc_protein_structure
from apecx_harvesters.loaders.protabank import parse_protabank
from apecx_harvesters.loaders.violin_gene import parse_violin_gene
from apecx_harvesters.loaders.violin_pathogen import parse_violin_pathogen
from apecx_harvesters.loaders.violin_vaccine import parse_violin_vaccine
from apecx_harvesters.pipeline.globus_source import globus_index_source, index_total
from apecx_harvesters.pipeline.sinks import to_gmetalist

PIPELINE_VERSION = "globus-harmonization/0.1"

# Globus Search index UUID -> (source name, parser). The nine APECx sources.
SOURCE_REGISTRY: dict[str, tuple[str, Callable[[dict[str, Any]], DataCite]]] = {
    "9e902471-9c77-49d3-a12c-516cc0808c3b": ("protabank", parse_protabank),
    "e8097a7b-a280-4031-9df1-1e837193494f": ("antiviraldb", parse_antiviraldb),
    "a67c7310-5115-446f-bfb6-d889bc4efa06": ("violin_pathogen", parse_violin_pathogen),
    "c5ff64fd-5e78-4cf0-848a-2788a78e71cd": ("violin_vaccine", parse_violin_vaccine),
    "205c1a5b-c9bd-4137-8ac6-ca879c9a4f9c": ("violin_gene", parse_violin_gene),
    "f873c7d5-8652-466d-806b-b5da46f0f786": ("bvbrc_epitope", parse_bvbrc_epitope),
    "439f2b66-09d4-4141-8c3d-b4dc18ef8a07": ("bvbrc_protein_structure", parse_bvbrc_protein_structure),
    "249efe96-14d2-443d-ad47-5621ed43a343": ("bvbrc_protein", parse_bvbrc_protein),
    "b676edbe-3286-4514-bc13-5cbe891c4bb1": ("bvbrc_genome", parse_bvbrc_genome),
}


class CanonicalCollisionError(RuntimeError):
    """Two harmonized records share a canonical_uri (would overwrite at ingest)."""


def assert_unique_canonical(records: list[DataCite]) -> None:
    """Raise ``CanonicalCollisionError`` if any two records share a canonical_uri.

    This is the full-set safety net for the {organism}==subject assumption: even
    if it held on every sampled doc, a single collision here means silent data
    loss at ingest, so we refuse rather than overwrite.
    """
    seen: set[str] = set()
    for record in records:
        uri = record.canonical_uri
        if uri in seen:
            raise CanonicalCollisionError(
                f"duplicate canonical_uri {uri!r}: ingest would silently overwrite the "
                f"earlier record (Globus Search keys entries on subject)."
            )
        seen.add(uri)


async def harmonize_index(
    index_uuid: str,
    *,
    client: globus_sdk.SearchClient,
) -> tuple[list[DataCite], dict[str, Any], list[dict[str, str]]]:
    """Scrape + harmonize an entire source index.

    Returns ``(records, provenance, parse_errors)``. Raises ``KeyError`` for an
    unregistered index and ``CanonicalCollisionError`` on a canonical collision
    (before any caller can ingest). Parse failures are collected (surfaced in
    provenance), never silently dropped.
    """
    if index_uuid not in SOURCE_REGISTRY:
        raise KeyError(f"no parser registered for Globus Search index {index_uuid!r}")
    name, parser = SOURCE_REGISTRY[index_uuid]

    total_before = await index_total(client, index_uuid)
    records: list[DataCite] = []
    errors: list[dict[str, str]] = []
    async for result in globus_index_source(index_uuid, parser, client=client):
        if result.ok:
            assert result.record is not None
            records.append(result.record)
        else:
            errors.append({"subject": result.id, "error": result.error or ""})
    total_after = await index_total(client, index_uuid)

    assert_unique_canonical(records)  # FAIL LOUD before any ingest

    provenance = {
        "source_index": index_uuid,
        "source_name": name,
        "pipeline_version": PIPELINE_VERSION,
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "scraped_total_before": total_before,
        "scraped_total_after": total_after,
        "stable_total": total_before == total_after,
        "harmonized_count": len(records),
        "parse_error_count": len(errors),
    }
    return records, provenance, errors


async def _as_results(records: list[DataCite]) -> AsyncIterator[RetrievalResult[Any]]:
    for record in records:
        yield RetrievalResult(id=record.canonical_uri, record=record)


async def publish_records(
    records: list[DataCite],
    *,
    client: globus_sdk.SearchClient,
    dest_index: str,
    visible_to: list[str] | None = None,
) -> tuple[int, list[str]]:
    """Ingest harmonized *records* into *dest_index* as GMetaList batches.

    Run ``harmonize_index`` first -- it applies the canonical-collision guard, so
    no record silently overwrites another at ingest. ``to_gmetalist`` defaults
    ``visible_to`` to ``["public"]`` and keeps each batch/entry within the 10 MB
    Globus limits. Returns ``(ingested_count, task_ids)``; the caller must poll
    the tasks (see ``wait_for_ingest``) and verify success.
    """
    ingested = 0
    task_ids: list[str] = []
    async for doc in to_gmetalist(_as_results(records), visible_to=visible_to):
        resp = await asyncio.to_thread(client.ingest, dest_index, doc)
        task_ids.append(resp["task_id"])
        ingested += len(doc["ingest_data"]["gmeta"])
    return ingested, task_ids


async def wait_for_ingest(
    client: globus_sdk.SearchClient,
    task_ids: list[str],
    *,
    timeout: float = 600.0,
    poll_seconds: float = 2.0,
) -> dict[str, str]:
    """Poll ingest task IDs to a terminal state. Returns ``{task_id: state}``.

    FAIL-LOUD contract: the caller MUST verify every state is ``"SUCCESS"``. A
    ``"FAILED"`` / ``"TIMEOUT"`` must never be read as success -- that would
    publish a partial index while reporting OK (the silent failure to avoid).
    """
    states: dict[str, str] = {}
    for task_id in task_ids:
        deadline = time.time() + timeout
        state = "TIMEOUT"
        while time.time() < deadline:
            info = await asyncio.to_thread(client.get_task, task_id)
            state = info.get("state") or info.get("status") or "UNKNOWN"
            if state in ("SUCCESS", "FAILED"):
                break
            await asyncio.sleep(poll_seconds)
        states[task_id] = state
    return states
