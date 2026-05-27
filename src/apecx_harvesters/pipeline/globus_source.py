"""Globus Search index as a pipeline *source*.

Reads records out of a Globus Search index and emits ``RetrievalResult`` so the
existing ``pipeline.run()`` / sinks can consume index data exactly as they
consume API-harvested data. This is the inverse of ``sinks.to_gmetalist``
(which writes to an index).

Phase 0 uses offset paging (``post_search``); Phase 1 will switch to the
marker-based ``scroll_query`` for full extraction beyond the ~10k offset cap.
The synchronous Globus SDK calls are off-loaded via ``asyncio.to_thread`` so the
async pipeline is not blocked.
"""

from __future__ import annotations

import asyncio
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


async def globus_index_source(
    index_uuid: str,
    parser: Callable[[dict[str, Any]], DataCite],
    *,
    client: globus_sdk.SearchClient,
    query: str = "*",
    page_size: int = 100,
) -> AsyncIterator[RetrievalResult[Any]]:
    """Yield one ``RetrievalResult`` per document in *index_uuid*, parsed by *parser*.

    A parse failure becomes a ``RetrievalResult`` carrying the error string (never
    a silently dropped record); downstream sinks log and skip failed results.
    """
    offset = 0
    while True:
        resp = await asyncio.to_thread(
            client.post_search, index_uuid, {"q": query, "limit": page_size, "offset": offset}
        )
        gmeta = resp.get("gmeta", []) or []
        if not gmeta:
            break
        for g in gmeta:
            subject = g.get("subject") or ""
            for entry in (g.get("entries") or []):
                content = entry.get("content") or {}
                try:
                    record = parser(content)
                    yield RetrievalResult(id=subject, record=record)
                except Exception as exc:  # noqa: BLE001 - report, never drop
                    yield RetrievalResult(id=subject, error=f"{type(exc).__name__}: {exc}")
        offset += page_size
        if offset >= int(resp.get("total", 0)):
            break


async def index_total(client: globus_sdk.SearchClient, index_uuid: str, query: str = "*") -> int:
    """Return the index's document count for *query* (the scrape-completeness anchor)."""
    resp = await asyncio.to_thread(client.search, index_uuid, query, limit=0)
    return int(resp.get("total", 0))
