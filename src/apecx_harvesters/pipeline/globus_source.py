"""Globus Search index as a pipeline *source*.

Reads records out of a Globus Search index and emits ``RetrievalResult`` so the
existing ``pipeline.run()`` / sinks can consume index data exactly as they
consume API-harvested data. This is the inverse of ``sinks.to_gmetalist``
(which writes to an index).

Full extraction uses the marker-based ``scroll`` API, NOT ``post_search``
offsets: offset paging is capped (~10k) by Globus Search, so an offset reader
would be unable to extract the larger indices (BVBRC:Protein ~25k, Genome
~750k). Scroll walks the whole index page by page via an opaque marker.
The synchronous Globus SDK calls are off-loaded via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import AsyncIterator, Callable
from typing import Any

import globus_sdk

from apecx_harvesters.loaders.base import DataCite
from apecx_harvesters.loaders.base.retrieve import RetrievalResult


def build_search_client() -> globus_sdk.SearchClient:
    """Build a Globus ``SearchClient`` from a confidential client in the environment.

    Reads ``GLOBUS_CLIENT_ID`` / ``GLOBUS_CLIENT_SECRET``. FAIL-LOUD if either is
    absent -- we never fall back to an unauthenticated client, which would
    silently return zero documents for access-controlled indices.
    """
    cid = os.environ.get("GLOBUS_CLIENT_ID")
    secret = os.environ.get("GLOBUS_CLIENT_SECRET")
    if not (cid and secret):
        raise RuntimeError(
            "GLOBUS_CLIENT_ID / GLOBUS_CLIENT_SECRET not set; cannot authenticate "
            "to Globus Search. (Access-controlled indices return 0 docs unauthenticated.)"
        )
    confidential = globus_sdk.ConfidentialAppAuthClient(cid, secret)
    authorizer = globus_sdk.ClientCredentialsAuthorizer(confidential, globus_sdk.SearchClient.scopes.all)
    return globus_sdk.SearchClient(authorizer=authorizer)


async def index_total(client: globus_sdk.SearchClient, index_uuid: str, query: str = "*") -> int:
    """Return the index's document count for *query* (the scrape-completeness anchor)."""
    resp = await asyncio.to_thread(client.search, index_uuid, query, limit=0)
    return int(resp.get("total", 0))


async def scroll_index_records(
    index_uuid: str,
    *,
    client: globus_sdk.SearchClient,
    query: str = "*",
    page_size: int = 1000,
) -> AsyncIterator[dict[str, Any]]:
    """Yield ``{"subject", "content"}`` for EVERY document in *index_uuid*.

    Marker-paginated (no offset cap), so this extracts indices of any size.
    """
    data = {"q": query, "limit": page_size}
    marker: Any = globus_sdk.MISSING
    while True:
        resp = await asyncio.to_thread(client.scroll, index_uuid, data, marker=marker)
        for g in resp.get("gmeta", []) or []:
            subject = g.get("subject") or ""
            for entry in (g.get("entries") or []):
                yield {"subject": subject, "content": entry.get("content") or {}}
        if not resp.get("has_next_page"):
            break
        marker = resp["marker"]


async def globus_index_source(
    index_uuid: str,
    parser: Callable[[dict[str, Any]], DataCite],
    *,
    client: globus_sdk.SearchClient,
    query: str = "*",
    page_size: int = 1000,
) -> AsyncIterator[RetrievalResult[Any]]:
    """Yield one ``RetrievalResult`` per document in *index_uuid*, parsed by *parser*.

    Full extraction via scroll. A parse failure becomes a ``RetrievalResult``
    carrying the error string (never a silently dropped record); downstream sinks
    log and skip failed results.
    """
    # Parsers are normally `parse(content)`; sources whose unique key is the Globus
    # subject (not a content field) accept `parse(content, subject)`. Detect by arity so
    # adding the subject never disturbs the single-arg parsers.
    takes_subject = len(inspect.signature(parser).parameters) >= 2
    async for rec in scroll_index_records(index_uuid, client=client, query=query, page_size=page_size):
        subject = rec["subject"]
        try:
            record = parser(rec["content"], subject) if takes_subject else parser(rec["content"])
            yield RetrievalResult(id=subject, record=record)
        except Exception as exc:  # noqa: BLE001 - report, never drop
            yield RetrievalResult(id=subject, error=f"{type(exc).__name__}: {exc}")
