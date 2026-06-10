#!/usr/bin/env python3
"""Republish named sources with a read-only pre-flight gate per source.

For each source: pre-flight (read-only, resolve every record, count what
WOULD change). Republish ONLY if the pre-flight shows subjects would be
added AND every canonical_uri is stable. A source that resolves 0 subjects
is SKIPPED (either non-taxonomy-anchored like ProtaBank, or a bug to
investigate) rather than re-ingested as a no-op.

Writes a per-source JSON report + a summary. FAIL-LOUD on ingest failure
or canonical_uri drift.

Usage:
    GLOBUS_CLIENT_ID=... GLOBUS_CLIENT_SECRET=... \\
    PYTHONPATH=src python scripts/republish_sources.py \\
        --sources violin_pathogen,bvbrc_epitope,... [--out output] [--dry-run]

`--dry-run` runs ONLY the pre-flight (no writes) for every source.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from apecx_harvesters.dict_reader import configure_dictionary_path
from apecx_harvesters.pipeline.globus_source import build_search_client
from apecx_harvesters.pipeline.republish_with_canonical import (
    dest_uuid_for_source,
    preflight_index,
    republish_index,
    source_uuid_for_name,
)

# Minimum fraction of records that must gain a subject for a write to proceed.
# The republish is idempotent + additive, so a PARTIAL-coverage source (e.g.
# violin_gene at 22% — virus genes resolve, out-of-subtree bacterial genes
# don't) is still worth writing: it harmonizes the records that resolve and
# leaves the rest untouched. So the floor is low — it exists only to hard-skip
# a GENUINE 0% (a non-anchored source like ProtaBank, or a systematic bug the
# pre-flight should surface), not to demand majority coverage.
_MIN_SUBJECTS_FRACTION = 0.02

# Sample size for the pre-flight gate. A few hundred records is enough to
# distinguish a systematic zero-subject failure (0%) from a healthy source
# (~90%+); resolving the full index just to gate would double the work.
_PREFLIGHT_SAMPLE = 300


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str))


async def _run(sources: list[str], out_dir: Path, dry_run: bool) -> int:
    dict_path = Path(
        os.environ.get(
            "APECX_SYNONYM_DICT_PATH",
            str(Path.home() / ".apecx" / "dictionary" / "dictionary.sqlite"),
        )
    )
    if not dict_path.exists():
        print(f"dictionary not present at {dict_path}", file=sys.stderr)
        return 2
    configure_dictionary_path(dict_path)
    client = build_search_client()

    summary: list[dict] = []
    failures: list[str] = []

    for name in sources:
        src = source_uuid_for_name(name)
        dst = dest_uuid_for_source(name)
        print(f"\n=== {name}  (dst={dst[:8]}..) ===")

        # 1. Read-only pre-flight on a SAMPLE (the gate).
        pf = await preflight_index(
            dest_uuid=dst, source_uuid=src, client=client, max_records=_PREFLIGHT_SAMPLE
        )
        _write_json(out_dir / f"{name}_preflight.json", asdict(pf))
        print(
            f"  pre-flight: read={pf.records_read} would_add={pf.records_would_add_subjects} "
            f"({pf.subjects_fraction:.0%}) canonical_uri_stable={pf.canonical_uri_stable}/{pf.records_read} "
            f"reparse_failed={pf.records_reparse_failed}"
        )

        decision = ""
        if not pf.canonical_uri_all_stable:
            decision = "SKIP (canonical_uri drift in pre-flight — investigate)"
            failures.append(name)
        elif pf.subjects_fraction < _MIN_SUBJECTS_FRACTION:
            decision = (
                f"SKIP (only {pf.subjects_fraction:.0%} would gain subjects; "
                f"likely non-taxonomy-anchored or a resolver gap — not re-ingesting)"
            )
        elif dry_run:
            decision = "DRY-RUN (pre-flight only; no write)"
        else:
            # 2. Live republish.
            stats = await republish_index(
                dest_uuid=dst, source_uuid=src, client=client, max_skipped_fraction=0.05
            )
            _write_json(out_dir / f"{name}_republish.json", asdict(stats))
            if not stats.all_success:
                decision = f"FAILED ingest states={stats.ingest_states}"
                failures.append(name)
            else:
                decision = (
                    f"WROTE subjects_added={stats.records_subjects_added} "
                    f"unchanged={stats.records_unchanged} skipped={stats.records_skipped} "
                    f"ingest={stats.ingest_states}"
                )
        print(f"  → {decision}")
        summary.append(
            {
                "source": name,
                "records": pf.records_read,
                "would_add_subjects": pf.records_would_add_subjects,
                "decision": decision,
            }
        )

    _write_json(out_dir / "republish_summary.json", {"sources": summary, "failures": failures})
    print(f"\n=== summary ({len(sources)} sources, {len(failures)} failures) ===")
    for row in summary:
        print(f"  {row['source']:26s} {row['decision']}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="republish_sources")
    ap.add_argument("--sources", required=True, help="comma-separated source names")
    ap.add_argument("--out", default="output/republish")
    ap.add_argument("--dry-run", action="store_true", help="pre-flight only, no writes")
    args = ap.parse_args(argv)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    return asyncio.run(_run(sources, Path(args.out), args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
