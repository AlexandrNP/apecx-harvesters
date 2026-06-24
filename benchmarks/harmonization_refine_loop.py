"""Self-refining harmonization evaluation loop (PDB/EMDB viral leg).

An evaluator→reflect→refine feedback loop (pattern per AWS reflect-refine / Reflexion / 2026 gold-set
eval guidance) wrapped around the proxy benchmark (viral_structure_proxy). One iteration:

  EVALUATE  measure recall (vs the independent RCSB-lineage gold) + precision, before vs after, per virus
  DIAGNOSE  bucket every failure into a fixed taxonomy (stale_taxid / harvest_gap / overmatch)
  REFINE    AUTO-SAFE: re-derive a stale species taxid from RCSB's own current annotation (a benchmark
            data correction, sourced from the live authority — never fabricated); GATED: emit an
            evidence-backed worklist for the production dictionary rebuild + harvest fixes (human/
            review-gate applies, never auto-edited).
  terminate when no auto-safe refinement remains on the TRAIN split, OR plateau, OR --max-iters.

A HELD-OUT split is never refined on (measures generalization, guards against teaching-to-the-test).
Long-term memory: output/{refine_history,failure_taxonomy,taxid_overrides}.json.

Read-only against live RCSB/EBI; no Globus DEST write. The autonomy boundary (auto-apply DATA only;
gate code + production-dict changes) is the user's decision for this loop.

Usage:  uv run python benchmarks/harmonization_refine_loop.py [--max-iters N] [--fill-sample N]
                                                              [--held-out-every K] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from viral_structure_proxy import (
    VIRUSES, ViralResult, measure_virus, _term, _rcsb_ids, _gql_taxids, _rcsb_count,
    _NAME_ATTR, _LINEAGE_ATTR,
)

_HERE = Path(__file__).resolve().parent
HARVEST_GAP_THRESHOLD = 0.90  # recall_after below this = a real harvest-capture gap worth flagging


@dataclass
class FailureItem:
    virus: str
    category: str       # stale_taxid | harvest_gap | overmatch
    gate: str           # auto_safe | gated | informational
    evidence: str
    proposed_action: str


def diagnose(results: list[ViralResult]) -> list[FailureItem]:
    """Reflect step: bucket each virus's measurement into the fixed failure taxonomy."""
    items: list[FailureItem] = []
    for r in results:
        if r.stale_taxid:
            # AUTO-SAFE in this loop: re-derive the benchmark's gold taxid from RCSB. GATED in
            # production: the real harmonization's species-map (apecx-mcp-integration dict) almost
            # certainly carries the same reclassification and needs a rebuild.
            items.append(FailureItem(
                r.virus, "stale_taxid", "auto_safe",
                f"lineage gold=0 but name-match found {r.pdb_name_match} structures "
                f"(species taxid {r.taxid} reclassified)",
                "re-derive gold taxid from RCSB current annotation; FLAG production dict rebuild"))
        elif r.recall_after_realized is not None and r.recall_after_realized < HARVEST_GAP_THRESHOLD:
            items.append(FailureItem(
                r.virus, "harvest_gap", "gated",
                f"recall_after (harvest-capture) {r.recall_after_realized:.0%} of gold sample",
                "investigate the harvester organism capture for this taxon"))
        elif r.pdb_gold and r.free_text_overmatch > r.pdb_gold:
            # Free-text over-matches by more than the gold itself: large precision cost the taxon-IRI
            # filter avoids. Informational — it demonstrates the precision win, not a defect to fix.
            items.append(FailureItem(
                r.virus, "overmatch", "informational",
                f"free-text over-match {r.free_text_overmatch} > gold {r.pdb_gold}",
                "none — demonstrates the harmonized filter's precision benefit"))
    return items


# Common expression hosts / co-crystallized partners (antibody Fabs, receptors) that contaminate the
# name-matched virus structures. A re-derived "virus" taxid must never be one of these, or an
# antibody-complex-heavy virus could auto-correct to human 9606.
_HOST_TAXIDS = {9606, 10090, 10116, 9544, 9986, 562, 511145, 83333, 4932, 7227, 7108, 7460}


def rederive_taxid(name: str, *, min_lineage: int = 1) -> str | None:
    """AUTO-SAFE refine: the current species taxid from RCSB's own annotation of the name-matched
    structures (the most common ncbi_taxonomy_id whose lineage is non-empty). Sourced from the live
    authority — not fabricated. Excludes common host/expression taxids so a co-crystallized antibody or
    receptor cannot be mistaken for the virus. None if nothing usable (then it stays on the gated list)."""
    ids = _rcsb_ids(_term(_NAME_ATTR, name), 25)
    counts: Counter[int] = Counter()
    for tids in _gql_taxids(ids).values():
        for t in tids:
            if t not in _HOST_TAXIDS:
                counts[t] += 1
    for taxid, _ in counts.most_common():
        if _rcsb_count(_term(_LINEAGE_ATTR, str(taxid))) >= min_lineage:
            return str(taxid)
    return None


def split_held_out(viruses: list[tuple[str, str, str]], every: int
                   ) -> tuple[list[tuple], list[tuple]]:
    """Deterministic TRAIN/HELD-OUT split (every Kth virus, by sorted name → reproducible, no RNG)."""
    ordered = sorted(viruses, key=lambda v: v[0])
    held = [v for i, v in enumerate(ordered) if every and i % every == 0]
    train = [v for v in ordered if v not in held]
    return train, held


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def should_terminate(history: list[dict], max_iters: int) -> tuple[bool, str]:
    """Stop on: retry cap, no auto-safe refinements left on TRAIN, or plateau (no new fix applied)."""
    if len(history) >= max_iters:
        return True, "max_iters"
    last = history[-1]
    if last["auto_safe_pending_train"] == 0:
        return True, "converged (no auto-safe refinement left on TRAIN)"
    if len(history) >= 2 and history[-1]["applied_refinements"] == 0:
        return True, "plateau (last iteration applied no refinement)"
    return False, ""


def _evaluate(viruses, overrides, fill_sample) -> list[ViralResult]:
    out = []
    for disp, name, default_taxid in viruses:
        taxid = overrides.get(disp, default_taxid)
        try:
            out.append(measure_virus(disp, name, taxid, fill_sample))
        except Exception as exc:  # noqa: BLE001
            print(f"  {disp}: eval skipped ({type(exc).__name__}: {exc})", file=sys.stderr)
    return out


def run_loop(max_iters: int, fill_sample: int, held_out_every: int, out_dir: Path) -> int:
    train, held = split_held_out(VIRUSES, held_out_every)
    print(f"TRAIN={len(train)} HELD-OUT={len(held)} viruses; max_iters={max_iters}", file=sys.stderr)
    overrides: dict[str, str] = {}            # virus -> re-derived current taxid (auto-safe refinements)
    gated_worklist: dict[str, FailureItem] = {}  # deduped across iterations (long-term memory)
    history: list[dict] = []

    while True:
        it = len(history) + 1
        train_res = _evaluate(train, overrides, fill_sample)
        held_res = _evaluate(held, overrides, fill_sample)
        train_diag = diagnose(train_res)
        for fi in diagnose(held_res) + train_diag:
            if fi.gate == "gated":
                gated_worklist[f"{fi.virus}:{fi.category}"] = fi

        # REFINE — auto-safe only, TRAIN only (held-out is never refined on).
        applied = 0
        for fi in train_diag:
            if fi.category == "stale_taxid" and fi.virus not in overrides:
                name = next(n for d, n, _ in train if d == fi.virus)
                new_taxid = rederive_taxid(name)
                if new_taxid:
                    overrides[fi.virus] = new_taxid
                    applied += 1
                    print(f"  [iter {it}] AUTO-SAFE: {fi.virus} taxid -> {new_taxid} "
                          f"(re-derived from RCSB)", file=sys.stderr)
                # The production-dict implication is always recorded as a gated worklist item.
                gated_worklist[f"{fi.virus}:stale_taxid_prod_dict"] = FailureItem(
                    fi.virus, "stale_taxid", "gated", fi.evidence,
                    "rebuild the production synonym/taxon dict (apecx-mcp-integration) on a current "
                    "NCBI taxonomy — the species-rollup will miss this virus until then")

        valid_train = [r for r in train_res if not r.stale_taxid and r.pdb_gold]
        valid_held = [r for r in held_res if not r.stale_taxid and r.pdb_gold]
        # auto-safe still pending AFTER this iteration's refinements applied:
        pending = sum(1 for r in train_res if r.stale_taxid and r.virus not in overrides)
        snapshot = {
            "iter": it,
            "train_recall_before": _mean([r.recall_before for r in valid_train if r.recall_before is not None]),
            "held_recall_before": _mean([r.recall_before for r in valid_held if r.recall_before is not None]),
            "train_valid_taxid": len(valid_train), "train_total": len(train_res),
            "held_valid_taxid": len(valid_held), "held_total": len(held_res),
            "applied_refinements": applied,
            "auto_safe_pending_train": pending,
            "gated_worklist_size": len(gated_worklist),
        }
        history.append(snapshot)
        print(f"  [iter {it}] train valid-taxid {len(valid_train)}/{len(train_res)} "
              f"held {len(valid_held)}/{len(held_res)} applied={applied} pending={pending}",
              file=sys.stderr)
        stop, reason = should_terminate(history, max_iters)
        if stop:
            print(f"  TERMINATE: {reason}", file=sys.stderr)
            break

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "refine_history.json").write_text(json.dumps(
        {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "terminate_reason": reason, "iterations": history, "taxid_overrides": overrides}, indent=2))
    (out_dir / "failure_taxonomy.json").write_text(json.dumps(
        {"gated_worklist": [asdict(fi) for fi in gated_worklist.values()]}, indent=2))
    (out_dir / "taxid_overrides.json").write_text(json.dumps(overrides, indent=2))
    print(f"\n=== self-refining loop done ({reason}) ===", file=sys.stderr)
    print(f"  auto-safe refinements applied: {overrides}", file=sys.stderr)
    print(f"  GATED worklist ({len(gated_worklist)} items, need human/review-gate):", file=sys.stderr)
    for fi in gated_worklist.values():
        print(f"    - {fi.virus} [{fi.category}]: {fi.proposed_action}", file=sys.stderr)
    print(f"  wrote {out_dir}/{{refine_history,failure_taxonomy,taxid_overrides}}.json", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-iters", type=int, default=4, help="retry cap (prevents infinite loops)")
    ap.add_argument("--fill-sample", type=int, default=30, help="gold structures sampled per virus for realized-recall")
    ap.add_argument("--held-out-every", type=int, default=4, help="every Kth virus (sorted) is held out; 0 disables")
    ap.add_argument("--out", default=str(_HERE / "output"))
    args = ap.parse_args(argv)
    return run_loop(args.max_iters, args.fill_sample, args.held_out_every, Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
