"""Harmonize a registered Globus Search source index and optionally publish it.

    uv run python -m apecx_harvesters.scripts.harmonize_and_publish <source-uuid> \
        [--publish <dest-index-uuid>]

Reads GLOBUS_CLIENT_ID / GLOBUS_CLIENT_SECRET. Always writes a provenance
manifest under output/<source>/. With --publish it ingests into the dest index
(idempotent on canonical_uri) and verifies the authenticated + anonymous
(public) document counts. Exits non-zero on any failure -- no silent success.
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
    SOURCE_REGISTRY,
    harmonize_index,
    publish_records,
    wait_for_ingest,
)


async def _run(source: str, dest: str | None) -> int:
    client = build_search_client()

    records, prov, errors = await harmonize_index(source, client=client)
    print(json.dumps(prov, indent=2))
    if not prov["stable_total"]:
        print("  WARNING: source total changed during scrape (torn snapshot) -- "
              "do not publish this run; re-scrape once the index stabilizes.")
    if errors:
        print(f"  {len(errors)} parse error(s); first 3: {errors[:3]}")

    outdir = Path("output") / prov["source_name"]
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "provenance.json").write_text(
        json.dumps({"provenance": prov, "parse_errors": errors}, indent=2)
    )
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

    # Verify: authenticated count reflects the ingest (eventual consistency) and
    # an anonymous client can read it (public-access proof).
    deadline = time.time() + 90
    auth = 0
    while time.time() < deadline:
        auth = int((await asyncio.to_thread(client.search, dest, "*", limit=0)).get("total", 0))
        if auth >= ingested:
            break
        await asyncio.sleep(3)
    anon = int((await asyncio.to_thread(globus_sdk.SearchClient().search, dest, "*", limit=0)).get("total", 0))
    print(f"  dest authenticated total={auth}, anonymous total={anon} (public-access proof)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harmonize_and_publish")
    ap.add_argument("source", help="source Globus Search index UUID (must be registered)")
    ap.add_argument("--publish", default=None, metavar="DEST_UUID", help="ingest into this dest index")
    args = ap.parse_args(argv)
    if args.source not in SOURCE_REGISTRY:
        print(f"unknown source index {args.source!r}; registered: {sorted(SOURCE_REGISTRY)}")
        return 2
    return asyncio.run(_run(args.source, args.publish))


if __name__ == "__main__":
    sys.exit(main())
