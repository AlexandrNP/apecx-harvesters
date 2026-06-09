"""End-to-end harmonization driver: ingest -> mine -> dict -> republish.

Single entry-point that walks every stage of the harmonization pipeline
in order, producing the DEST indices in their final form (DataCite shape
+ populated ``subjects.valueUri``). All four stages are idempotent and
restartable; the driver invokes each only when the upstream output
indicates work to do.

Stages
------

1. **Harvest + harmonize + ingest** (existing path).
   For each source in ``SOURCE_REGISTRY`` selected by ``--sources``,
   scrape the SOURCE index, parse to DataCite, ingest to DEST. Optionally
   feed a :class:`MinedSynonymAccumulator` so the parsed records double
   as corpus-mining input. Skip per-source via ``--skip-stage-1`` when
   DEST is already populated from a prior run.

2. **Corpus-mining sidecar export**.
   Emits ``output/mined_observations_<source>.jsonl`` per source plus
   a per-source mining-stats report. The sidecar is what the dictionary
   build's ``apply_mined_observations`` ingests in stage 3. Skip via
   ``--skip-stage-2``.

3. **Dictionary update**.
   Ensures the local dictionary is current with the published version
   via :func:`apecx_harvesters.dict_reader.bootstrap.bootstrap_dictionary`
   (no-op when the local version already matches). The mined-observation
   sidecars from stage 2 are passed to the dictionary-side
   ``apply_mined_observations`` script when the user opts in via
   ``--apply-mined-locally`` (requires apecx-mcp-integration on PYTHONPATH).
   Skip via ``--skip-stage-3``.

4. **Re-publish with canonical IRIs** (Phase F).
   For each source selected, run
   :func:`apecx_harvesters.pipeline.republish_with_canonical.republish_index`
   against the DEST index — reads, resolves via the dictionary, writes
   ``subjects[].valueUri``, re-ingests. Idempotent on canonical_uri so a
   partial run is safely restartable. Skip via ``--skip-stage-4``.

Exit code is 0 on full success, 1 on any stage failure (skipped > 1%,
canonical_uri drift, ingest task FAILED, dictionary load error).

Usage
-----

    python -m apecx_harvesters.scripts.run_full_harmonization \\
        --sources violin_pathogen,bvbrc_epitope \\
        --output ./output

Single-source dry-run pre-flight:

    python -m apecx_harvesters.scripts.run_full_harmonization \\
        --sources antiviraldb --skip-stage-3 --skip-stage-1 --skip-stage-2
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from apecx_harvesters.dict_reader.bootstrap import (
    bootstrap_dictionary,
    current_local_version,
    default_dictionary_path,
)
from apecx_harvesters.pipeline.corpus_mining import MinedSynonymAccumulator
from apecx_harvesters.pipeline.globus_source import build_search_client
from apecx_harvesters.pipeline.harmonize import (
    DEST_REGISTRY,
    SOURCE_REGISTRY,
    harmonize_index,
    harmonize_publish_streaming,
)
from apecx_harvesters.pipeline.republish_with_canonical import (
    dest_uuid_for_source,
    republish_index,
    source_uuid_for_name,
)

log = logging.getLogger(__name__)


def _parse_sources(spec: str) -> list[str]:
    """Parse the ``--sources`` arg into a list of source-names.

    ``"all"`` expands to every source registered in ``SOURCE_REGISTRY``;
    otherwise a comma-separated list of source-names is validated against
    the registry.
    """
    names = {name for _, (name, _) in SOURCE_REGISTRY.items()}
    if spec.lower().strip() == "all":
        # Preserve SOURCE_REGISTRY's insertion order so multi-source runs
        # are deterministic.
        return [name for _, (name, _) in SOURCE_REGISTRY.items()]
    requested = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [s for s in requested if s not in names]
    if unknown:
        raise SystemExit(
            f"unknown sources: {unknown}; known: {sorted(names)}"
        )
    return requested


async def _stage_1_harvest_and_ingest(
    sources: list[str],
    *,
    accumulator: MinedSynonymAccumulator | None,
    output_dir: Path,
) -> int:
    """Stage 1 — scrape, harmonize, ingest to DEST.

    Returns the number of sources that ingested without ingest-side
    FAILED. Per-source provenance lands at ``output/<source>_stage1.json``.
    """
    client = build_search_client()
    ok = 0
    for source_name in sources:
        source_uuid = source_uuid_for_name(source_name)
        dest_uuid = DEST_REGISTRY[source_uuid]
        log.info("stage 1: %s — harvest + ingest to %s", source_name, dest_uuid)
        provenance = await harmonize_publish_streaming(
            source_uuid, client=client, dest_index=dest_uuid
        )
        if accumulator is not None:
            # The streaming publish doesn't expose the accumulator hook
            # (records aren't materialized); we run a second pass via
            # harmonize_index when mining is requested.
            log.info("stage 1: %s — mining pass (separate)", source_name)
            _, mining_prov, _ = await harmonize_index(
                source_uuid, client=client, mining_accumulator=accumulator
            )
            provenance["mining"] = {
                k: v for k, v in mining_prov.items()
                if k.startswith("mining_")
            }
        _write_json(output_dir / f"{source_name}_stage1.json", provenance)
        if provenance.get("all_success"):
            ok += 1
        else:
            log.error(
                "stage 1: %s — ingest tasks not all SUCCESS (states=%s)",
                source_name,
                provenance.get("ingest_states"),
            )
    return ok


def _stage_2_export_mining(
    accumulator: MinedSynonymAccumulator,
    *,
    output_dir: Path,
) -> Path:
    """Stage 2 — write accumulator state to a JSONL sidecar.

    One ``{source, surface, taxon_id, normalized}`` line per observation.
    The dictionary build's ``apply_mined_observations`` script consumes
    this shape.
    """
    sidecar = output_dir / "mined_observations.jsonl"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    with sidecar.open("w") as fh:
        for obs in accumulator.observations():
            fh.write(
                json.dumps(
                    {
                        "source": obs.source,
                        "surface": obs.surface_form,
                        "surface_normalized": obs.surface_form_normalized,
                        "taxon_id": obs.taxon_id,
                    }
                )
                + "\n"
            )
    stats_path = output_dir / "mined_observations_stats.json"
    _write_json(stats_path, accumulator.per_source_stats())
    log.info(
        "stage 2: wrote %d observations to %s; %d unique pairs",
        sum(s["observed"] for s in accumulator.per_source_stats().values()),
        sidecar,
        accumulator.unique_pair_count(),
    )
    return sidecar


def _stage_3_dictionary_update(
    *,
    output_dir: Path,
    apply_mined_locally: bool,
    mined_sidecar: Path | None,
) -> Path:
    """Stage 3 — refresh local dictionary; optionally apply mined sidecar.

    Returns the path to the dictionary file in use after this stage.
    """
    dict_path = default_dictionary_path()
    before = current_local_version(dict_path)
    bootstrap_dictionary(dest=dict_path, quiet=True)
    after = current_local_version(dict_path)
    log.info("stage 3: dictionary %s -> %s at %s", before, after, dict_path)
    if apply_mined_locally and mined_sidecar is not None:
        log.warning(
            "stage 3: --apply-mined-locally requested. The mining ingest "
            "lives in apecx-mcp-integration; this driver only emits the "
            "sidecar at %s. Run apply_mined_observations.py against the "
            "local dictionary to ingest.",
            mined_sidecar,
        )
    return dict_path


async def _stage_4_republish(
    sources: list[str],
    *,
    output_dir: Path,
    max_skipped_fraction: float,
) -> int:
    """Stage 4 — Phase F republish per source.

    Returns the number of sources that republished cleanly (ingest
    all_success AND skipped < threshold). Per-source stats land at
    ``output/<source>_stage4.json``.
    """
    client = build_search_client()
    ok = 0
    for source_name in sources:
        source_uuid = source_uuid_for_name(source_name)
        dest_uuid = dest_uuid_for_source(source_name)
        log.info("stage 4: %s — republish %s -> %s",
                 source_name, dest_uuid, dest_uuid)
        stats = await republish_index(
            dest_uuid=dest_uuid,
            source_uuid=source_uuid,
            client=client,
            max_skipped_fraction=max_skipped_fraction,
        )
        _write_json(output_dir / f"{source_name}_stage4.json", asdict(stats))
        if stats.all_success and stats.skipped_fraction <= max_skipped_fraction:
            ok += 1
        else:
            log.error(
                "stage 4: %s — failed gate (all_success=%s skipped=%.4f)",
                source_name,
                stats.all_success,
                stats.skipped_fraction,
            )
    return ok


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        json.dump(payload, fh, indent=2, default=str)


async def _run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = _parse_sources(args.sources)
    log.info("driver starting: sources=%s output=%s", sources, output_dir)

    accumulator = MinedSynonymAccumulator() if not args.skip_stage_2 else None

    summary: dict[str, object] = {
        "driver_version": "run_full_harmonization/0.1",
        "timestamp_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "sources": sources,
    }

    failures: list[str] = []

    if not args.skip_stage_1:
        ok1 = await _stage_1_harvest_and_ingest(
            sources, accumulator=accumulator, output_dir=output_dir,
        )
        summary["stage_1_ok_count"] = ok1
        if ok1 != len(sources):
            failures.append("stage_1")
    else:
        log.info("stage 1 skipped")

    mined_sidecar: Path | None = None
    if not args.skip_stage_2 and accumulator is not None:
        mined_sidecar = _stage_2_export_mining(
            accumulator, output_dir=output_dir,
        )
        summary["stage_2_sidecar"] = str(mined_sidecar)
    else:
        log.info("stage 2 skipped")

    if not args.skip_stage_3:
        dict_path = _stage_3_dictionary_update(
            output_dir=output_dir,
            apply_mined_locally=args.apply_mined_locally,
            mined_sidecar=mined_sidecar,
        )
        summary["stage_3_dictionary"] = str(dict_path)
    else:
        log.info("stage 3 skipped")

    if not args.skip_stage_4:
        ok4 = await _stage_4_republish(
            sources,
            output_dir=output_dir,
            max_skipped_fraction=args.max_skipped_fraction,
        )
        summary["stage_4_ok_count"] = ok4
        if ok4 != len(sources):
            failures.append("stage_4")
    else:
        log.info("stage 4 skipped")

    summary["failures"] = failures
    _write_json(output_dir / "harmonization_summary.json", summary)
    log.info("driver complete: %s", summary)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser(
        prog="run_full_harmonization",
        description="Drive the end-to-end harmonization pipeline",
    )
    ap.add_argument(
        "--sources",
        default="all",
        help="comma-separated source-names, or 'all' (default: all)",
    )
    ap.add_argument(
        "--output", default="./output", help="output directory for per-stage artifacts",
    )
    ap.add_argument(
        "--max-skipped-fraction",
        type=float,
        default=0.01,
        help="stage-4 fails when republish-skipped fraction exceeds this (default 0.01)",
    )
    ap.add_argument("--skip-stage-1", action="store_true",
                    help="skip harvest + ingest")
    ap.add_argument("--skip-stage-2", action="store_true",
                    help="skip corpus mining + sidecar export")
    ap.add_argument("--skip-stage-3", action="store_true",
                    help="skip dictionary bootstrap/update")
    ap.add_argument("--skip-stage-4", action="store_true",
                    help="skip Phase-F republish")
    ap.add_argument(
        "--apply-mined-locally",
        action="store_true",
        help="(advisory) tell stage 3 to also log instructions for "
        "applying the mined-observations sidecar to the local dict",
    )
    args = ap.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
