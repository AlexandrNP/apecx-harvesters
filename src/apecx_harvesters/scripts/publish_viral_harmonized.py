"""WS3b: publish harmonized VIRAL PDB + EMDB structures into the DEST indices (owner-writable).

For each virus: harvest its structures (PDB via RCSB taxonomy_lineage search by species taxid; EMDB via
EBI organism search) -> parse (WS1/WS2 harvesters) -> resolve (WS3a make_resolver_for_source: source
taxid alt-ids -> NCBITaxon IRIs + UniProt) -> to_gmetalist -> `globus search ingest` (native owner auth).
PDB/EMDB are REST harvesters (not Globus-index sources), so this is the sibling of the index-republish path.

Idempotent: a structure's subject (pdb:<id> / emdb:<id>) is stable, so re-running overwrites in place.

Usage:  uv run python -m apecx_harvesters.scripts.publish_viral_harmonized [--viruses N] [--cap N] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from apecx_harvesters.dict_reader import configure_dictionary_path, default_dictionary_path
from apecx_harvesters.loaders.pdb.retrieve import PDBHarvester
from apecx_harvesters.loaders.emdb.retrieve import EMDBHarvester
from apecx_harvesters.pipeline.canonical_resolver_adapter import make_resolver_for_source
from apecx_harvesters.pipeline.harmonize import _as_results
from apecx_harvesters.pipeline.sinks import to_gmetalist

PDB_DEST = "857bc08e-5f35-4e8d-8db1-c505419cb5d6"
EMDB_DEST = "79058f1d-3086-4ee4-ad1a-8671b60831a2"
_RCSB = "https://search.rcsb.org/rcsbsearch/v2/query"
_EMDB = "https://www.ebi.ac.uk/emdb/api/search"
_LINEAGE = "rcsb_entity_source_organism.taxonomy_lineage.id"

# (display, PDB species taxid, EMDB organism name). The product's viral focus.
VIRUSES: list[tuple[str, str, str]] = [
    ("Chikungunya", "37124", "Chikungunya virus"),
    ("Dengue", "12637", "Dengue virus"),
    ("Zika", "64320", "Zika virus"),
    ("SARS-CoV-2", "2697049", "Severe acute respiratory syndrome coronavirus 2"),
    ("Influenza A", "11320", "Influenza A virus"),
    ("West Nile", "11082", "West Nile virus"),
    ("HIV-1", "11676", "Human immunodeficiency virus 1"),
    ("MERS-CoV", "1335626", "Middle East respiratory syndrome-related coronavirus"),
    ("Yellow fever", "11089", "Yellow fever virus"),
    ("Measles", "11234", "Measles morbillivirus"),
]


def _pdb_ids(taxid: str, cap: int) -> list[str]:
    node = {"type": "terminal", "service": "text", "parameters": {
        "attribute": _LINEAGE, "operator": "exact_match", "value": taxid}}
    payload = {"return_type": "entry", "query": node, "request_options": {"paginate": {"start": 0, "rows": cap}}}
    req = urllib.request.Request(_RCSB, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        return [x["identifier"] for x in json.loads(urllib.request.urlopen(req, timeout=60).read()).get("result_set", [])]
    except Exception as exc:  # noqa: BLE001
        print(f"  PDB search error taxid {taxid}: {exc}", file=sys.stderr)
        return []


def _emdb_ids(organism: str, cap: int) -> list[str]:
    q = urllib.parse.quote(f'organism:"{organism}"')
    try:
        rows = json.loads(urllib.request.urlopen(f"{_EMDB}/{q}?rows={cap}&page=1", timeout=60).read())
        return [r["emdb_id"] for r in (rows if isinstance(rows, list) else []) if r.get("emdb_id")]
    except Exception as exc:  # noqa: BLE001
        print(f"  EMDB search error {organism!r}: {exc}", file=sys.stderr)
        return []


async def _harvest(harvester, resolver, ids: list[str]) -> list:
    out = []
    async for res in harvester.iter_results(ids):
        if res.record is not None:
            out.append(resolver(res.record))
    return out


async def _ingest(dest: str, records: list, dry_run: bool) -> int:
    """to_gmetalist -> a temp GIngest doc -> `globus search ingest`. Returns records ingested."""
    docs = [doc async for doc in to_gmetalist(_as_results(records))]
    total = 0
    for i, doc in enumerate(docs):
        n = len(doc["ingest_data"]["gmeta"])
        total += n
        if dry_run:
            print(f"  [dry-run] would ingest {n} records into {dest} (doc {i})", file=sys.stderr)
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(doc, fh)
            path = fh.name
        r = subprocess.run(["globus", "search", "ingest", dest, path], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  INGEST FAILED ({dest}): {r.stderr[:200]}", file=sys.stderr)
        else:
            tid = next((ln.split()[-1] for ln in r.stdout.splitlines() if "Task ID" in ln), "?")
            print(f"  ingested {n} records into {dest} (task {tid})", file=sys.stderr)
    return total


async def main_async(n_viruses: int | None, cap: int, dry_run: bool) -> int:
    configure_dictionary_path(default_dictionary_path())
    pdb_resolver = make_resolver_for_source("pdb")
    emdb_resolver = make_resolver_for_source("emdb")
    viruses = VIRUSES[:n_viruses] if n_viruses else VIRUSES
    pdb_records: list = []
    emdb_records: list = []
    for disp, taxid, organism in viruses:
        pids = _pdb_ids(taxid, cap)
        eids = _emdb_ids(organism, cap)
        pdb_records += await _harvest(PDBHarvester(), pdb_resolver, pids)
        emdb_records += await _harvest(EMDBHarvester(), emdb_resolver, eids)
        print(f"  {disp}: PDB {len(pids)} ids, EMDB {len(eids)} ids harvested", file=sys.stderr)
    # de-dup by canonical_uri (a structure can match multiple viruses' searches only rarely)
    pdb_records = list({r.canonical_uri: r for r in pdb_records}.values())
    emdb_records = list({r.canonical_uri: r for r in emdb_records}.values())
    print(f"\nHarvested {len(pdb_records)} distinct PDB + {len(emdb_records)} distinct EMDB records.", file=sys.stderr)
    np = await _ingest(PDB_DEST, pdb_records, dry_run)
    ne = await _ingest(EMDB_DEST, emdb_records, dry_run)
    print(f"{'[dry-run] ' if dry_run else ''}PDB ingested {np}, EMDB ingested {ne}.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--viruses", type=int, default=None, help="cap the virus count (smoke runs)")
    ap.add_argument("--cap", type=int, default=200, help="max structures per virus per source")
    ap.add_argument("--dry-run", action="store_true", help="harvest + build docs but do NOT ingest")
    args = ap.parse_args(argv)
    return asyncio.run(main_async(args.viruses, args.cap, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
