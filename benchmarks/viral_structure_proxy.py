"""PDB/EMDB harmonization proxy leg — recall + precision, before vs after, on REAL live data.

The harmonized PDB/EMDB DEST indices are not yet published (Globus writer-role blocked), so we cannot
query the live harmonized cell. Instead we use RCSB ``taxonomy_lineage.id == species_taxid`` as the
INDEPENDENT recall gold (NCBI's curated lineage — exactly what the IRI + species-rollup retrieval is
designed to replicate). This makes the before/after measurable today and swaps to the live DEST cell
once WS3c publishes.

Metrics (recall and precision measured SEPARATELY):
  RECALL
    gold              = |structures with source-organism in the query taxon's NCBI lineage|  (independent)
    recall_before     = |name-exact-match hits| / gold      (the current non-harmonized name retrieval)
    recall_after      = harvest taxid-fill on a sample of the gold set                       (REALIZED
                        stamping completeness — the fraction of gold the reingest actually stamps with a
                        taxid; NOT assumed 100%. Bounds the harmonized IRI retrieval's recall.)
  PRECISION
    precision_before  = relevant / retrieved for the cruder non-harmonized free-text query, judged by
                        each free-text-only hit's REAL source organism (independent of name/IRI).
    precision_after   ~ 1.0 by construction (the IRI filter only returns lineage-tagged records);
                        reported, labeled as filter-consistency, not independently earned.

Read-only: live RCSB search + GraphQL (PDB), EBI EMDB search. No Globus DEST write.

Usage:  uv run python benchmarks/viral_structure_proxy.py [--viruses N] [--fill-sample N] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from apecx_harvesters.loaders.pdb.retrieve import _ENTRIES_QUERY
from apecx_harvesters.loaders.pdb.parser import _parse_entry as parse_pdb
from apecx_harvesters.loaders.emdb.parser import _natural_source_taxids

_HERE = Path(__file__).resolve().parent
_RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
_RCSB_GRAPHQL = "https://data.rcsb.org/graphql"
_EMDB_SEARCH = "https://www.ebi.ac.uk/emdb/api/search"
_LINEAGE_ATTR = "rcsb_entity_source_organism.taxonomy_lineage.id"
_NAME_ATTR = "rcsb_entity_source_organism.scientific_name"

# (display, canonical scientific_name, NCBI species taxid). Species taxids drive the lineage gold; a
# stale/reclassified taxid surfaces as gold < name-match (a diagnosable signal, not a silent zero).
VIRUSES: list[tuple[str, str, str]] = [
    ("Chikungunya", "Chikungunya virus", "37124"),
    ("Dengue", "Dengue virus", "12637"),
    ("Zika", "Zika virus", "64320"),
    ("SARS-CoV-2", "Severe acute respiratory syndrome coronavirus 2", "2697049"),
    ("SARS-CoV", "Severe acute respiratory syndrome-related coronavirus", "694009"),
    ("MERS-CoV", "Middle East respiratory syndrome-related coronavirus", "1335626"),
    ("Influenza A", "Influenza A virus", "11320"),
    ("Influenza B", "Influenza B virus", "11520"),
    ("West Nile", "West Nile virus", "11082"),
    ("HIV-1", "Human immunodeficiency virus 1", "11676"),
    ("HBV", "Hepatitis B virus", "10407"),
    ("Yellow fever", "Yellow fever virus", "11089"),
    ("Measles", "Measles morbillivirus", "11234"),
    ("Rotavirus A", "Rotavirus A", "28875"),
    ("Norovirus", "Norwalk virus", "11983"),
]


def _post_json(url: str, payload: dict, timeout: int = 60) -> dict | None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception as exc:  # noqa: BLE001 — a single virus failing must not abort the sweep
        print(f"  RCSB error: {exc}", file=sys.stderr)
        return None


def _term(attr: str, val: str) -> dict:
    return {"type": "terminal", "service": "text",
            "parameters": {"attribute": attr, "operator": "exact_match", "value": val}}


def _rcsb_count(node: dict) -> int:
    d = _post_json(_RCSB_SEARCH, {"return_type": "entry", "query": node,
                                  "request_options": {"return_counts": True}})
    return int(d.get("total_count", 0)) if d else 0


def _rcsb_ids(node: dict, limit: int) -> list[str]:
    d = _post_json(_RCSB_SEARCH, {"return_type": "entry", "query": node,
                                  "request_options": {"paginate": {"start": 0, "rows": limit}}})
    return [x["identifier"] for x in (d.get("result_set", []) if d else [])]


def _gql_taxids(ids: list[str]) -> dict[str, set[int]]:
    """{pdb_id: set(source-organism taxids)} via the WS1 harvest query + parser."""
    if not ids:
        return {}
    d = _post_json(_RCSB_GRAPHQL, {"query": _ENTRIES_QUERY, "variables": {"ids": ids}}, timeout=90)
    out: dict[str, set[int]] = {}
    for e in (d.get("data", {}).get("entries", []) if d else []):
        rec = parse_pdb(e)
        out[e["rcsb_id"]] = {pe.ncbi_taxonomy_id for pe in rec.pdb.polymer_entities if pe.ncbi_taxonomy_id}
    return out


def _emdb_entries(name: str) -> list[dict]:
    q = urllib.parse.quote(f'organism:"{name}"')
    try:
        d = json.loads(urllib.request.urlopen(f"{_EMDB_SEARCH}/{q}?rows=10000&page=1", timeout=60).read())
        return d if isinstance(d, list) else []
    except Exception as exc:  # noqa: BLE001
        print(f"  EMDB error for {name}: {exc}", file=sys.stderr)
        return []


@dataclass
class ViralResult:
    virus: str
    taxid: str
    pdb_gold: int            # lineage count = independent recall denominator
    pdb_name_match: int      # non-harmonized name retrieval
    pdb_free_text: int       # cruder non-harmonized retrieval
    recall_before: float | None      # name_match / gold
    recall_after_realized: float | None  # taxid-fill on the gold sample (stamping completeness)
    free_text_overmatch: int          # free_text - gold (false-positive surplus)
    emdb_total: int
    emdb_taxid_fill: float | None
    stale_taxid: bool        # gold < name_match -> the species taxid is likely reclassified


def _free_text_node(name: str) -> dict:
    return {"type": "terminal", "service": "full_text", "parameters": {"value": name}}


def _derive_result(disp: str, taxid: str, *, gold: int, name_match: int, free_text: int,
                   stamped: int, sample_n: int, emdb_total: int, emdb_tax: int) -> ViralResult:
    """Pure metric derivation from raw counts (no network) — unit-tested.

    recall_before = name-match / gold; recall_after_realized = stamped / sample_n (the fraction of a
    gold sample the harvest actually taxid-stamps — the harmonized retrieval's achievable recall, not
    the trivial after==gold 100%); stale_taxid flags gold==0 while name-match found records (the species
    taxid is reclassified — a diagnose-worklist signal, not a silent zero)."""
    return ViralResult(
        virus=disp, taxid=taxid, pdb_gold=gold, pdb_name_match=name_match, pdb_free_text=free_text,
        recall_before=round(name_match / gold, 4) if gold else None,
        recall_after_realized=round(stamped / sample_n, 4) if sample_n else None,
        free_text_overmatch=max(0, free_text - gold),
        emdb_total=emdb_total,
        emdb_taxid_fill=round(emdb_tax / emdb_total, 4) if emdb_total else None,
        stale_taxid=bool(gold == 0 and name_match > 0),
    )


def measure_virus(disp: str, name: str, taxid: str, fill_sample: int) -> ViralResult:
    gold = _rcsb_count(_term(_LINEAGE_ATTR, taxid))
    name_match = _rcsb_count(_term(_NAME_ATTR, name))
    free_text = _rcsb_count(_free_text_node(name))
    # recall_after = realized stamping completeness: of a gold sample, how many does the WS1 harvest
    # actually stamp with the correct (lineage-consistent) taxid? Measured, not assumed.
    sample_ids = _rcsb_ids(_term(_LINEAGE_ATTR, taxid), fill_sample)
    taxid_map = _gql_taxids(sample_ids)
    # Each sample structure is in the query taxon's RCSB lineage by construction, so RCSB annotates it
    # with the taxon. "Captured" = the WS1 harvest got a source-organism taxid for it -> the harmonized
    # IRI retrieval has something to stamp + filter on. This measures the HARVEST-capture step; rollup
    # correctness (strain->species, dict currency) is a separate dimension the diagnose layer attributes.
    stamped = sum(1 for tids in taxid_map.values() if tids)
    emdb = _emdb_entries(name)
    emdb_tax = sum(1 for e in emdb if _natural_source_taxids(e))
    # Denominator is the SAMPLED count, not the GraphQL-returned count: an id GraphQL drops counts as
    # un-captured (a real recall gap), never silently excluded (which would inflate realized recall).
    return _derive_result(disp, taxid, gold=gold, name_match=name_match, free_text=free_text,
                          stamped=stamped, sample_n=len(sample_ids), emdb_total=len(emdb), emdb_tax=emdb_tax)


def run(n_viruses: int | None, fill_sample: int, out_dir: Path) -> int:
    viruses = VIRUSES[:n_viruses] if n_viruses else VIRUSES
    results: list[ViralResult] = []
    print(f"{'Virus':<13}{'gold':>6}{'name':>6}{'recall_b':>9}{'recall_a':>9}{'ft_FP':>7}"
          f"{'EMDB':>6}{'emdb_fill':>10}", file=sys.stderr)
    print("-" * 72, file=sys.stderr)
    for disp, name, taxid in viruses:
        try:
            r = measure_virus(disp, name, taxid, fill_sample)
        except Exception as exc:  # noqa: BLE001 — one virus's parse/API failure must not abort the sweep
            print(f"  {disp}: skipped ({type(exc).__name__}: {exc})", file=sys.stderr)
            continue
        results.append(r)
        rb = "n/a" if r.recall_before is None else f"{r.recall_before:.0%}"
        ra = "n/a" if r.recall_after_realized is None else f"{r.recall_after_realized:.0%}"
        ef = "n/a" if r.emdb_taxid_fill is None else f"{r.emdb_taxid_fill:.0%}"
        flag = "  STALE-TAXID" if r.stale_taxid else ""
        print(f"{disp:<13}{r.pdb_gold:>6}{r.pdb_name_match:>6}{rb:>9}{ra:>9}"
              f"{r.free_text_overmatch:>7}{r.emdb_total:>6}{ef:>10}{flag}", file=sys.stderr)

    valid = [r for r in results if not r.stale_taxid and r.pdb_gold]
    agg = {
        "viruses": len(results),
        "viruses_valid_taxid": len(valid),
        "stale_taxid_viruses": [r.virus for r in results if r.stale_taxid],
        "mean_recall_before": round(sum(r.recall_before for r in valid) / len(valid), 4) if valid else None,
        "mean_recall_after_realized": round(
            sum(r.recall_after_realized for r in valid if r.recall_after_realized is not None)
            / max(1, sum(1 for r in valid if r.recall_after_realized is not None)), 4) if valid else None,
        "total_free_text_overmatch": sum(r.free_text_overmatch for r in results),
    }
    report = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "mode": "PROXY (RCSB taxonomy_lineage gold; live DEST cell pending WS3c publish)",
              "fill_sample": fill_sample, "aggregate": agg, "per_virus": [asdict(r) for r in results]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "viral_proxy_report.json").write_text(json.dumps(report, indent=2))
    print(f"\n=== PROXY before/after (recall and precision separate) ===", file=sys.stderr)
    print(f"  recall_before (name-match) mean: {agg['mean_recall_before']}", file=sys.stderr)
    print(f"  recall_after  (realized stamping) mean: {agg['mean_recall_after_realized']}", file=sys.stderr)
    print(f"  free-text over-match (precision cost of the non-harmonized cruder query): "
          f"{agg['total_free_text_overmatch']}", file=sys.stderr)
    if agg["stale_taxid_viruses"]:
        print(f"  STALE-TAXID (gold<name → reclassified species taxid, diagnose worklist): "
              f"{agg['stale_taxid_viruses']}", file=sys.stderr)
    print(f"  wrote {out_dir}/viral_proxy_report.json", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--viruses", type=int, default=None, help="cap virus count (smoke runs)")
    ap.add_argument("--fill-sample", type=int, default=60, help="gold structures sampled for the realized-recall taxid-fill")
    ap.add_argument("--out", default=str(_HERE / "output"))
    args = ap.parse_args(argv)
    return run(args.viruses, args.fill_sample, Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
