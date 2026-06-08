#!/usr/bin/env python
"""Per-field coverage auditor for Globus Search indices (OE-A1).

Walks every record in one or more Globus Search indices and reports, per
nested JSON path, how often the path is present + non-null + non-empty.
Output is a JSON report sufficient to drive the per-source Phase A
"information-drop" assessments (OE-A2 .. OE-A10).

The auditor is intentionally source-agnostic: it does NOT use the parsers
under ``apecx_harvesters/loaders/``. Whatever shape is in the index is
what gets counted. Source vs dest interpretation is the caller's job
(invoke twice, once per side).

Usage::

    uv run python scripts/audit_field_coverage.py --side dest --output design/FIELD_COVERAGE_AUDIT_dest.json
    uv run python scripts/audit_field_coverage.py --side source --output design/FIELD_COVERAGE_AUDIT_source.json
    uv run python scripts/audit_field_coverage.py --side dest --only antiviraldb,violin_pathogen --output /tmp/quick.json
    uv run python scripts/audit_field_coverage.py --side dest --full-genome --output design/FIELD_COVERAGE_AUDIT_dest_full.json

Authentication: requires ``GLOBUS_CLIENT_ID`` / ``GLOBUS_CLIENT_SECRET``
in the environment (the same confidential client used by the rest of the
publish path). See ``pipeline/globus_source.build_search_client``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import os

import globus_sdk

from apecx_harvesters.pipeline.globus_source import build_search_client, scroll_index_records
from apecx_harvesters.pipeline.harmonize import DEST_REGISTRY, SOURCE_REGISTRY


def _build_client() -> globus_sdk.SearchClient:
    """Build a SearchClient.

    Two auth modes, in priority order:
    1. ``GLOBUS_SEARCH_ACCESS_TOKEN`` — raw access token (e.g. extracted from the
       ``globus`` CLI's storage). Useful for one-off operator-driven runs where
       the confidential client isn't available.
    2. ``GLOBUS_CLIENT_ID`` / ``GLOBUS_CLIENT_SECRET`` (the publish path's default).
    """
    token = os.environ.get("GLOBUS_SEARCH_ACCESS_TOKEN")
    if token:
        authorizer = globus_sdk.AccessTokenAuthorizer(token)
        return globus_sdk.SearchClient(authorizer=authorizer)
    return build_search_client()

logger = logging.getLogger("audit_field_coverage")

# Per-record progress emitted every N records during a walk.
PROGRESS_INTERVAL = 25_000

# Distinct-value tracking ceiling per path. Above this many distinct values,
# we stop tracking the distinct set and just record the count + null-rate.
DISTINCT_VALUES_CEILING = 1000

# Default sample for BVBRC:Genome (745,917 records full-scan ~ 12 minutes).
GENOME_SAMPLE_DEFAULT = 5000


def _walk(obj: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    """Yield ``(jsonpath, value)`` for every leaf-or-container in *obj*.

    For dicts: yields the dict itself at its path AND descends. For lists:
    yields the list at its path AND descends with ``[]`` suffix (list
    items share a single coalesced path — sufficient for coverage stats).
    """
    yield prefix, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{prefix}.{k}" if prefix else k
            yield from _walk(v, sub)
    elif isinstance(obj, list):
        sub = f"{prefix}[]"
        for v in obj:
            yield from _walk(v, sub)


def _classify(v: Any) -> tuple[str, bool, bool]:
    """Return ``(type_name, is_non_null, is_non_empty)`` for value *v*."""
    if v is None:
        return ("null", False, False)
    if isinstance(v, bool):
        return ("bool", True, True)
    if isinstance(v, (int, float)):
        return ("number", True, True)
    if isinstance(v, str):
        return ("string", True, len(v) > 0)
    if isinstance(v, list):
        return ("list", True, len(v) > 0)
    if isinstance(v, dict):
        return ("dict", True, len(v) > 0)
    return (type(v).__name__, True, True)


class FieldStats:
    """Coverage stats for one JSON path across a record stream."""

    __slots__ = ("present", "non_null", "non_empty", "types", "distinct_values", "distinct_overflow")

    def __init__(self) -> None:
        self.present = 0
        self.non_null = 0
        self.non_empty = 0
        self.types: Counter[str] = Counter()
        self.distinct_values: set[str] = set()
        self.distinct_overflow = False

    def observe(self, v: Any) -> None:
        self.present += 1
        type_name, non_null, non_empty = _classify(v)
        self.types[type_name] += 1
        if non_null:
            self.non_null += 1
        if non_empty:
            self.non_empty += 1
        # Only track distinct values for scalar leaves; containers explode cardinality.
        if not self.distinct_overflow and type_name in ("string", "number", "bool"):
            self.distinct_values.add(str(v))
            if len(self.distinct_values) > DISTINCT_VALUES_CEILING:
                self.distinct_overflow = True
                self.distinct_values.clear()

    def to_json(self, records_walked: int) -> dict[str, Any]:
        return {
            "present": self.present,
            "non_null": self.non_null,
            "non_empty": self.non_empty,
            "non_null_pct": round(100 * self.non_null / records_walked, 2) if records_walked else 0.0,
            "non_empty_pct": round(100 * self.non_empty / records_walked, 2) if records_walked else 0.0,
            "types": dict(self.types),
            "distinct_value_count": (
                "> " + str(DISTINCT_VALUES_CEILING) if self.distinct_overflow else len(self.distinct_values)
            ),
            "distinct_values_sample": (
                None if self.distinct_overflow else sorted(self.distinct_values)
            ),
        }


async def audit_index(
    index_uuid: str,
    *,
    name: str,
    side: str,
    client: Any,
    sample_limit: int | None,
) -> dict[str, Any]:
    """Walk every (or sampled) document in *index_uuid* and report per-path coverage."""
    logger.info("auditing %s (%s) %s ...", name, side, index_uuid)
    stats: dict[str, FieldStats] = defaultdict(FieldStats)
    records_walked = 0
    t0 = time.monotonic()
    async for rec in scroll_index_records(index_uuid, client=client):
        content = rec.get("content") or {}
        seen_paths: set[str] = set()
        for path, value in _walk(content):
            if path in seen_paths:
                # A nested list yields the same path multiple times; we count
                # the path's presence ONCE per record (presence = "this record
                # had at least one occurrence"). The non_empty count for list-
                # typed paths still reflects whether the list itself is non-empty.
                continue
            seen_paths.add(path)
            stats[path].observe(value)
        records_walked += 1
        if records_walked % PROGRESS_INTERVAL == 0:
            elapsed = time.monotonic() - t0
            logger.info("  %s: %d records walked (%.1fs elapsed)", name, records_walked, elapsed)
        if sample_limit is not None and records_walked >= sample_limit:
            logger.info("  %s: hit sample limit %d, stopping", name, sample_limit)
            break
    elapsed = time.monotonic() - t0
    logger.info("  %s: done. %d records, %d distinct paths, %.1fs", name, records_walked, len(stats), elapsed)

    return {
        "source_name": name,
        "index_uuid": index_uuid,
        "side": side,
        "records_walked": records_walked,
        "sample_limit_hit": sample_limit is not None and records_walked == sample_limit,
        "wall_seconds": round(elapsed, 1),
        "fields": {path: s.to_json(records_walked) for path, s in sorted(stats.items())},
    }


async def main_async(args: argparse.Namespace) -> int:
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    client = _build_client()

    # Pick the registry to walk.
    if args.side == "source":
        index_map = {name: uuid for uuid, (name, _parser) in SOURCE_REGISTRY.items()}
    elif args.side == "dest":
        index_map = {SOURCE_REGISTRY[src_uuid][0]: dest_uuid for src_uuid, dest_uuid in DEST_REGISTRY.items()}
    else:
        raise SystemExit(f"unknown --side {args.side!r} (must be source|dest)")

    if only:
        unknown = only - set(index_map)
        if unknown:
            raise SystemExit(f"--only contained unknown source name(s): {sorted(unknown)}")
        index_map = {k: v for k, v in index_map.items() if k in only}

    reports: list[dict[str, Any]] = []
    for name, uuid in index_map.items():
        sample = (
            args.genome_sample if (name == "bvbrc_genome" and not args.full_genome)
            else None
        )
        try:
            rep = await audit_index(uuid, name=name, side=args.side, client=client, sample_limit=sample)
        except Exception as exc:  # noqa: BLE001 - audit a best-effort across many indices
            logger.exception("FAILED audit %s: %s", name, exc)
            rep = {"source_name": name, "index_uuid": uuid, "side": args.side, "error": str(exc)}
        reports.append(rep)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"side": args.side, "reports": reports}, indent=2))
    logger.info("wrote %s (%d bytes)", out_path, len(out_path.read_bytes()))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--side", required=True, choices=["source", "dest"],
                        help="walk the 9 SOURCE_REGISTRY indices or the 9 DEST_REGISTRY indices")
    parser.add_argument("--output", required=True, type=Path,
                        help="path to write the JSON report")
    parser.add_argument("--only", default=None,
                        help="comma-separated subset of source NAMES (e.g. 'antiviraldb,violin_pathogen')")
    parser.add_argument("--genome-sample", type=int, default=GENOME_SAMPLE_DEFAULT,
                        help=f"sample size for BVBRC:Genome (default {GENOME_SAMPLE_DEFAULT}); ignored if --full-genome")
    parser.add_argument("--full-genome", action="store_true",
                        help="walk the full 745k BVBRC:Genome index (slow; ~12 min)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
