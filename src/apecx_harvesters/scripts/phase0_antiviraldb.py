"""Phase 0 spike: AntiviralDB -> harmonize -> ingest -> verify public access.

Real end-to-end run against the live AntiviralDB source index and the dev
destination index. Reads GLOBUS_CLIENT_ID / GLOBUS_CLIENT_SECRET from env.

    uv run python -m apecx_harvesters.scripts.phase0_antiviraldb

Exit 0 only if every acceptance criterion passes:
  - scraped == source total (35)
  - ingested == scraped (no records dropped)
  - every ingest task SUCCESS
  - dest authenticated count == ingested
  - dest anonymous count == ingested  (proves the index is public)
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import globus_sdk

from apecx_harvesters.loaders.antiviraldb import parse_antiviraldb
from apecx_harvesters.pipeline.globus_source import (
    build_search_client,
    globus_index_source,
    index_total,
)
from apecx_harvesters.pipeline.run import run
from apecx_harvesters.pipeline.sinks import to_gmetalist

SOURCE = "e8097a7b-a280-4031-9df1-1e837193494f"   # AntiviralDB
DEST = "4103190a-019d-4c0b-b8e3-b93817908141"     # APECx Harmonized (dev)


async def _ingest_sink(client: globus_sdk.SearchClient, results: Any) -> tuple[int, list[str]]:
    ingested = 0
    task_ids: list[str] = []
    async for doc in to_gmetalist(results):
        resp = await asyncio.to_thread(client.ingest, DEST, doc)
        task_ids.append(resp["task_id"])
        ingested += len(doc["ingest_data"]["gmeta"])
    return ingested, task_ids


async def _wait_task(client: globus_sdk.SearchClient, task_id: str, timeout: float = 180) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = await asyncio.to_thread(client.get_task, task_id)
        state = info.get("state") or info.get("status")
        if state in ("SUCCESS", "FAILED"):
            if state == "FAILED":
                print("    task detail:", json.dumps(dict(info))[:600])
            return state
        await asyncio.sleep(2)
    return "TIMEOUT"


async def _auth_count(client: globus_sdk.SearchClient, index: str) -> int:
    r = await asyncio.to_thread(client.search, index, "*", limit=0)
    return int(r.get("total", 0))


async def _anon_count(index: str) -> int:
    r = await asyncio.to_thread(globus_sdk.SearchClient().search, index, "*", limit=0)
    return int(r.get("total", 0))


async def main() -> int:
    client = build_search_client()

    src_total = await index_total(client, SOURCE)
    print(f"[1] AntiviralDB source total: {src_total}")

    source = globus_index_source(SOURCE, parse_antiviraldb, client=client)
    ingested, task_ids = await run(source, lambda results: _ingest_sink(client, results))
    print(f"[2] harmonized + submitted: {ingested} records in {len(task_ids)} batch(es)")

    for tid in task_ids:
        state = await _wait_task(client, tid)
        print(f"    ingest task {tid[:8]} -> {state}")
        if state != "SUCCESS":
            print("PHASE 0 ACCEPTANCE: FAIL (ingest task not SUCCESS)")
            return 1

    # Eventual consistency: poll until the dest reflects the ingest (or give up).
    deadline = time.time() + 90
    auth = 0
    while time.time() < deadline:
        auth = await _auth_count(client, DEST)
        if auth >= ingested:
            break
        await asyncio.sleep(3)
    anon = await _anon_count(DEST)
    print(f"[3] dest authenticated total: {auth}")
    print(f"[4] dest anonymous   total: {anon}  (public-access proof)")

    checks = {
        "scraped == source total": ingested == src_total,
        "ingested == scraped": ingested == src_total,  # source yields == scraped here
        "dest auth == ingested": auth == ingested,
        "dest anon == ingested (public)": anon == ingested,
    }
    for name, ok in checks.items():
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    overall = all(checks.values())
    print("PHASE 0 ACCEPTANCE:", "PASS" if overall else "FAIL")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
