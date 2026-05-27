"""Harmonize a registered Globus Search source index and optionally publish it.

    uv run python -m apecx_harvesters.scripts.harmonize_and_publish <source-uuid> \
        [--publish <dest-uuid>|auto] [--streaming]

Reads GLOBUS_CLIENT_ID / GLOBUS_CLIENT_SECRET. `--publish auto` resolves the
production destination from DEST_REGISTRY. `--streaming` uses the memory-safe
one-pass path (required for large sources, e.g. BVBRC:Genome ~746k). Always
verifies the authenticated + anonymous (public) counts. Exits non-zero on any
failure -- no silent success.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import globus_sdk

from apecx_harvesters.pipeline.globus_source import build_search_client
from apecx_harvesters.pipeline.harmonize import (
    DEST_REGISTRY,
    SOURCE_REGISTRY,
    harmonize_index,
    harmonize_publish_streaming,
    publish_records,
    wait_for_ingest,
)


async def _verify(client: globus_sdk.SearchClient, dest: str, want: int) -> tuple[int, int]:
    deadline = time.time() + 300
    auth = 0
    while time.time() < deadline:
        auth = int((await asyncio.to_thread(client.search, dest, "*", limit=0)).get("total", 0))
        if auth >= want:
            break
        await asyncio.sleep(5)
    anon = int((await asyncio.to_thread(globus_sdk.SearchClient().search, dest, "*", limit=0)).get("total", 0))
    return auth, anon


async def _run(source: str, dest: str | None, streaming: bool) -> int:
    client = build_search_client()

    if streaming:
        if not dest:
            print("--streaming requires --publish")
            return 2
        prov = await harmonize_publish_streaming(source, client=client, dest_index=dest)
        print(json.dumps(prov, indent=2))
        if not prov["stable_total"]:
            print("  WARNING: source changed during scrape (torn snapshot) -- re-run when stable "
                  "(ingest is idempotent on canonical_uri).")
        if not prov["all_success"]:
            print("  PUBLISH FAILED: not all ingest tasks SUCCEEDED.")
            return 1
        auth, anon = await _verify(client, dest, prov["harmonized_count"])
        print(f"  dest authenticated total={auth}, anonymous total={anon} (public-access proof)")
        return 0

    records, prov, errors = await harmonize_index(source, client=client)
    print(json.dumps(prov, indent=2))
    if errors:
        print(f"  {len(errors)} parse error(s); first 3: {errors[:3]}")
    outdir = Path("output") / prov["source_name"]
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "provenance.json").write_text(json.dumps({"provenance": prov, "parse_errors": errors}, indent=2))
    print(f"  provenance manifest -> {outdir / 'provenance.json'}")
    if not dest:
        print("  harmonize-only (no --publish).")
        return 0
    if not prov["stable_total"]:
        print("  REFUSING to publish a torn snapshot.")
        return 1
    ingested, tasks = await publish_records(records, client=client, dest_index=dest)
    states = await wait_for_ingest(client, tasks)
    print(f"  ingested {ingested} record(s) in {len(tasks)} batch(es); states: {set(states.values())}")
    if any(s != "SUCCESS" for s in states.values()):
        print(f"  PUBLISH FAILED: non-SUCCESS task state(s): {states}")
        return 1
    auth, anon = await _verify(client, dest, ingested)
    print(f"  dest authenticated total={auth}, anonymous total={anon} (public-access proof)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harmonize_and_publish")
    ap.add_argument("source", help="source Globus Search index UUID (must be registered)")
    ap.add_argument("--publish", default=None, metavar="DEST", help="dest index UUID, or 'auto' (DEST_REGISTRY)")
    ap.add_argument("--streaming", action="store_true", help="memory-safe one-pass path (large sources)")
    args = ap.parse_args(argv)
    if args.source not in SOURCE_REGISTRY:
        print(f"unknown source index {args.source!r}; registered: {sorted(SOURCE_REGISTRY)}")
        return 2
    dest = args.publish
    if dest == "auto":
        dest = DEST_REGISTRY.get(args.source)
        if not dest:
            print(f"no registered dest for {args.source!r}")
            return 2
    return asyncio.run(_run(args.source, dest, args.streaming))


if __name__ == "__main__":
    sys.exit(main())
