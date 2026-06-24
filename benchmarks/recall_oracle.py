"""Independent recall oracle for the live 9-source harmonized DEST indices — recall, before vs after.

The existing ablation (harmonization_ablation.py) measures PRECISION but its FINDINGS.md flags that the
harmonized-cell precision is "partly circular": it adjudicates a DEST record by subjects.valueUri, the
SAME field the harmonized cell filters on. This module computes RECALL with an INDEPENDENT judge.

For a DEST record the judge is its NESTED NATIVE ORGANISM field (e.g. content["bvbrc_genome"]["Species"]) —
the source's own organism annotation, independent of the harmonized subjects.valueUri stamp. So:

  pool        = raw_dest hits  ∪  harm_dest hits   (deduped by gmeta subject id, per query+source)
  gold        = pooled records whose NATIVE ORGANISM resolves into the query taxon's subtree (independent)
  recall_before = |raw_dest ∩ gold| / |gold|     (substring search — the non-harmonized retrieval)
  recall_after  = |harm_dest ∩ gold| / |gold|    (subjects.valueUri filter — the harmonized retrieval)

This is recall@limit (pooled over the sampled records, the standard TREC-pooling estimate when the
complete relevant set is unknowable) with a non-circular judge: the judge field (native organism) is
never the field either cell filtered on (substring / valueUri).

Two honesty caveats. (a) The numbers are POOL-RELATIVE: gold ⊆ raw∪harm, so a relevant record retrieved
by NEITHER cell is never counted — these are an A/B of the two retrievals over what they jointly find,
not absolute index coverage. (b) The judge re-runs the SAME resolver the harmonization pipeline used to
STAMP subjects.valueUri from the native organism, so recall_after is partly a resolver self-consistency
check, not fully exogenous. recall_before (substring) is fully independent of resolver AND stamp; that
recall_after is 0.81 not 1.0 shows the stamp and the re-resolution genuinely diverge (s_q value cap,
descendant asymmetry, dictionary-version drift) — so it is informative, not tautological.

Read-only anonymous Globus search + the local dictionary. No DEST write.

Usage:  uv run python benchmarks/recall_oracle.py [--categories C] [--max-queries N] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from harmonization_ablation import (
    _build_client, resolve_query, _taxon_matches, _query_descendants, _raw_payload,
    ORGANISM_FIELD, SOURCE_REGISTRY, DEST_REGISTRY, _load_queries,
)
from apecx_harvesters.dict_reader import configure_dictionary_path, default_dictionary_path, get_dictionary_index

_HERE = Path(__file__).resolve().parent
_MAX_FILTER_VALUES = 1000


def _fetch_with_ids(client, index_id: str, payload: dict) -> list[tuple[str, dict]]:
    """Like the ablation's _post but KEEPS the gmeta subject id (needed to pool raw vs harm)."""
    try:
        resp = client.post_search(index_id, payload)
    except Exception as exc:  # noqa: BLE001
        print(f"  search error on {index_id}: {exc}", file=sys.stderr)
        return []
    out = []
    for g in resp.data.get("gmeta", []):
        ents = g.get("entries", [])
        if ents:
            out.append((g.get("subject"), ents[0].get("content", {})))
    return out


def _harm_dest_payload(s_q: set[str], limit: int) -> dict:
    values = sorted(s_q)[:_MAX_FILTER_VALUES]
    return {"filters": [{"type": "match_any", "field_name": "subjects.valueUri", "values": values}],
            "limit": limit}


def _dest_native_organism(content: dict, source: str) -> str | None:
    """The DEST record's source-native organism text (nested under the source-name container) — the
    judge field, independent of subjects.valueUri."""
    nested = content.get(source)
    if isinstance(nested, dict):
        val = nested.get(ORGANISM_FIELD.get(source, ""))
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _is_relevant(content: dict, source: str, s_q: set[str], desc_ids: set[int]) -> bool | None:
    """Independent relevance judgment: resolve the native organism, is it in the query subtree?
    None = unjudgeable (no organism / unresolvable) — excluded from the gold, never guessed."""
    organism = _dest_native_organism(content, source)
    if not organism:
        return None
    rr = resolve_query(organism, enable_fuzzy=False)
    if not rr.iris:
        return None
    return any(_taxon_matches(ti, s_q, desc_ids) for ti in rr.iris)


@dataclass
class RecallRow:
    source: str
    query: str
    gold: int            # pooled records judged relevant by the independent native-organism oracle
    recall_before: float | None   # raw_dest (substring)  ∩ gold / gold
    recall_after: float | None    # harm_dest (valueUri)  ∩ gold / gold
    unjudged: int        # pooled records with no resolvable native organism (excluded from gold)
    saturated: bool      # a cell hit the fetch limit -> the pool undersamples; recall is a @limit estimate


def measure_recall(client, source: str, dest: str, term: str, limit: int) -> RecallRow | None:
    res = resolve_query(term)
    if not res.iris:
        return None  # query didn't resolve -> no harmonized retrieval to measure (a diagnose signal)
    desc_ids = _query_descendants(res.iris)
    raw = _fetch_with_ids(client, dest, _raw_payload(term, limit))
    harm = _fetch_with_ids(client, dest, _harm_dest_payload(res.s_q, limit))
    saturated = len(raw) >= limit or len(harm) >= limit
    raw_ids = {sid for sid, _ in raw if sid}
    harm_ids = {sid for sid, _ in harm if sid}
    pool: dict[str, dict] = {}
    for sid, content in raw + harm:
        if sid:
            pool[sid] = content
    gold_ids, unjudged = set(), 0
    for sid, content in pool.items():
        rel = _is_relevant(content, source, res.s_q, desc_ids)
        if rel is None:
            unjudged += 1
        elif rel:
            gold_ids.add(sid)
    before, after = _recall_fractions(raw_ids, harm_ids, gold_ids)
    return RecallRow(source=source, query=term, gold=len(gold_ids),
                     recall_before=before, recall_after=after, unjudged=unjudged, saturated=saturated)


def _recall_fractions(raw_ids: set[str], harm_ids: set[str], gold_ids: set[str]
                      ) -> tuple[float | None, float | None]:
    """Pure recall arithmetic: each cell's relevant hits over the independent pooled gold."""
    g = len(gold_ids)
    if not g:
        return None, None
    return round(len(raw_ids & gold_ids) / g, 4), round(len(harm_ids & gold_ids) / g, 4)


def run(categories: list[str], max_queries: int | None, limit: int, indices: list[str] | None,
        out_dir: Path) -> int:
    dict_path = Path(os.environ.get("APECX_SYNONYM_DICT_PATH", str(default_dictionary_path())))
    if not dict_path.exists():
        print(f"dictionary not present at {dict_path}", file=sys.stderr)
        return 2
    configure_dictionary_path(dict_path)
    if get_dictionary_index()[0] is None:
        print("dictionary failed to load", file=sys.stderr)
        return 2
    client = _build_client()
    pairs = [(name, src, DEST_REGISTRY[src]) for src, (name, _) in SOURCE_REGISTRY.items()
             if indices is None or name in indices]

    rows: list[RecallRow] = []
    for category in categories:
        queries = _load_queries(category)
        if max_queries:
            queries = queries[:max_queries]
        for term in queries:
            for name, src, dst in pairs:
                row = measure_recall(client, name, dst, term, limit)
                if row and row.gold:
                    rows.append(row)

    # Aggregate: micro-mean recall before/after. Saturated cells (a fetch hit the limit -> the pool
    # undersamples the true relevant set) are reported separately so capped numbers don't distort the
    # headline; the clean mean is over UNSATURATED cells only.
    valid = [r for r in rows if r.recall_before is not None]
    clean = [r for r in valid if not r.saturated]
    agg = {
        "cells_with_gold": len(valid),
        "unsaturated_cells": len(clean),
        "saturated_cells": len(valid) - len(clean),
        "mean_recall_before": round(sum(r.recall_before for r in clean) / len(clean), 4) if clean else None,
        "mean_recall_after": round(sum(r.recall_after for r in clean) / len(clean), 4) if clean else None,
        "total_gold": sum(r.gold for r in valid),
    }
    report = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "judge": "independent native-organism oracle (non-circular vs subjects.valueUri)",
              "limit_per_cell": limit, "aggregate": agg, "rows": [asdict(r) for r in rows]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recall_oracle_report.json").write_text(json.dumps(report, indent=2))
    print(f"\n=== independent recall (before vs after harmonization), {len(clean)}/{len(valid)} "
          f"unsaturated cells (judge: native organism, non-circular) ===", file=sys.stderr)
    print(f"  mean recall_before (substring): {agg['mean_recall_before']}", file=sys.stderr)
    print(f"  mean recall_after  (taxon-IRI): {agg['mean_recall_after']}", file=sys.stderr)
    print(f"  ({agg['saturated_cells']} saturated cells excluded — gold exceeded --limit; raise --limit "
          f"to measure them)", file=sys.stderr)
    print(f"  wrote {out_dir}/recall_oracle_report.json", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--categories", default="abbreviations", help="comma-separated: mu_virus,abbreviations,real_world")
    ap.add_argument("--max-queries", type=int, default=None)
    ap.add_argument("--limit", type=int, default=200, help="records/cell pooled for the recall sample")
    ap.add_argument("--indices", default=None, help="comma-separated source short names (default: all 9)")
    ap.add_argument("--out", default=str(_HERE / "output"))
    args = ap.parse_args(argv)
    return run(args.categories.split(","), args.max_queries, args.limit,
               args.indices.split(",") if args.indices else None, Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
