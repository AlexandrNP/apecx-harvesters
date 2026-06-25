"""Self-refining 9-source harmonization loop: full-corpus recall + precision audit + alias refine.

Evaluator->reflect->refine over the 9 published DEST indices, NO --limit (the loop processes the whole
corpus). One iteration:
  EVALUATE  resolve every query (with current aliases); for resolved queries measure full-corpus recall
            (before/after) + precision; unresolved queries are the recall-side gap.
  DIAGNOSE  unresolved_query (AUTO-SAFE: an alias) · false_membership (GATED, from the precision audit) ·
            low_recall_after (GATED, a harvest/stamp gap).
  REFINE    AUTO-SAFE only: derive a DICT-VALIDATED alias for an unresolved TRAIN query and append it to
            a SEPARATE queries/auto_aliases.tsv (NEVER the human-curated curated_aliases.tsv; both are
            read by the _USE_ALIASES seam, curated overriding auto) -> the query resolves next iteration.
            Code/dict/precision fixes stay GATED (worklist + evidence, never auto-applied).
  terminate cap / converge (no TRAIN query left to alias) / plateau.

Alias derivation is SAFE: fuzzy first (deterministic), else the LLM PROPOSES an expansion and the dict
GATES it (the proposed canonical must resolve to a real taxon, or it is rejected — a hallucinated name
never lands). HELD-OUT queries are never aliased (generalization guard). Memory:
output/{refine_history_9src,failure_taxonomy_9src}.json.

Read-only against live Globus/Ollama/dict; the only write is the alias TSV (reversible data). No DEST write.

Usage:  uv run python benchmarks/harmonization_9source_loop.py [--max-iters N] [--indices ...]
                                                              [--max-queries N] [--held-out-every K]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

import harmonization_ablation as HA
from harmonization_ablation import (
    resolve_query, SOURCE_REGISTRY, DEST_REGISTRY, _load_queries, _build_client,
)
from recall_oracle import measure_recall_full
from precision_audit import audit_query_source, _extract_json, _LLM_BASE, _LLM_MODEL
from entity_split import classify_query_entities
from harmonization_refine_loop import should_terminate
from apecx_harvesters.dict_reader import (
    configure_dictionary_path, default_dictionary_path, get_dictionary_index,
)

_HERE = Path(__file__).resolve().parent
# Auto-proposed aliases go to a SEPARATE file (never the human-curated one) so they stay reviewable +
# promotable; _load_aliases reads both, curated overriding auto on conflict.
AUTO_ALIASES_TSV = _HERE / "queries" / "auto_aliases.tsv"
RECALL_AFTER_FLOOR = 0.90  # recall_after below this on a resolved query = a gated harvest/stamp gap


def llm_expand(term: str) -> str | None:
    """LLM PROPOSAL only: map an unresolved query/acronym/phrase to the pathogen organism it refers to.
    The dict GATES the proposal next (a hallucinated or non-resolving name is rejected)."""
    system = ('You map a search query to the single pathogen ORGANISM it is about (virus, bacterium, '
              'etc.), ignoring modifiers like "vaccine"/"genome"/"spike protein". Respond ONLY with JSON '
              '{"name": "<full scientific organism name>"}. If it is not a single organism, use null.')
    user = f"Query: {term}\nWhich pathogen organism is this about? Full scientific name."
    body = json.dumps({"model": _LLM_MODEL, "temperature": 0,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}).encode()
    try:
        r = json.loads(urllib.request.urlopen(
            urllib.request.Request(f"{_LLM_BASE}/chat/completions", data=body,
                                   headers={"Content-Type": "application/json"}), timeout=60).read())
        parsed = _extract_json(r["choices"][0]["message"]["content"])
        name = (parsed or {}).get("name")
        return name.strip() if isinstance(name, str) and name.strip() else None
    except Exception:  # noqa: BLE001
        return None


def derive_validated_alias(term: str) -> tuple[str, str, str] | None:
    """(term, canonical, method) — a DICT-VALIDATED alias whose canonical resolves to a real taxon.
    Fuzzy first (deterministic, high-confidence); else LLM-propose + dict-gate. None if unvalidatable
    (then the term stays on the gated worklist for a human)."""
    # GUARD: never alias a MULTI-ENTITY query (organism + protein/gene, e.g. "HIV protease") to a single
    # taxon — that silently discards the protein. Refuse; the loop gates it for the compound-query path.
    if classify_query_entities(term).multi_entity:
        return None
    idx, _ = get_dictionary_index()
    if idx is not None:
        for entry, score in idx.lookup_fuzzy(term, threshold=0.9, limit=3):
            canon = getattr(entry, "canonical_label", None) or getattr(entry, "surface_form", None)
            if _valid_alias_target(term, canon):
                return (term, canon, f"fuzzy:{round(score, 2)}")
    name = llm_expand(term)
    if _valid_alias_target(term, name):
        return (term, name, "llm-proposed+dict-validated")
    return None


def _valid_alias_target(term: str, canonical: str | None, resolver=resolve_query) -> bool:
    """A proposed alias is valid only if the canonical is non-empty, differs from the term, and the
    canonical RESOLVES to a real taxon (the dict gates a fabricated/hallucinated expansion). resolver is
    injectable for unit tests."""
    return bool(canonical) and canonical.strip().lower() != term.strip().lower() \
        and bool(resolver(canonical).iris)


def append_alias(term: str, canonical: str) -> None:
    """Append to the AUTO alias TSV (separate, reviewable, reversible data) + bust the in-process cache."""
    new = not AUTO_ALIASES_TSV.exists()
    with AUTO_ALIASES_TSV.open("a") as fh:
        if new:
            fh.write("# auto-proposed aliases (loop: LLM-proposed, dict-validated). Review + promote good "
                     "ones to curated_aliases.tsv; delete wrong ones. NOT human-curated.\n")
        fh.write(f"{term}\t{canonical}\n")
    HA._ALIASES = None           # bust the lazy cache so the next resolve_query sees it
    HA._USE_ALIASES = True


def split_queries(queries: list[str], every: int) -> tuple[list[str], list[str]]:
    """Deterministic TRAIN/HELD-OUT query split (every Kth, sorted; no RNG). Held-out is never aliased."""
    ordered = sorted(queries)
    held = [q for i, q in enumerate(ordered) if every and i % every == 0]
    train = [q for q in ordered if q not in held]
    return train, held


@dataclass
class GatedItem:
    query: str
    source: str
    category: str
    evidence: str


def run_loop(max_iters: int, indices, max_queries, held_out_every, out_dir: Path,
             categories=("abbreviations",)) -> int:
    dict_path = default_dictionary_path()
    if not dict_path.exists():
        print(f"dictionary not present at {dict_path}", file=sys.stderr)
        return 2
    configure_dictionary_path(dict_path)
    if get_dictionary_index()[0] is None:
        print("dictionary failed to load", file=sys.stderr)
        return 2
    HA._USE_ALIASES = True  # honor curated + auto-added aliases this run
    client = _build_client()
    pairs = [(name, src, DEST_REGISTRY[src]) for src, (name, _) in SOURCE_REGISTRY.items()
             if indices is None or name in indices]
    seen: set[str] = set()
    queries = [q for cat in categories for q in _load_queries(cat)
               if not (q in seen or seen.add(q))]  # de-dup across categories, preserve order
    if max_queries:
        queries = queries[:max_queries]
    train, held = split_queries(queries, held_out_every)
    print(f"TRAIN={len(train)} HELD-OUT={len(held)} queries; max_iters={max_iters}", file=sys.stderr)

    applied_aliases: list[tuple[str, str, str]] = []
    history: list[dict] = []
    while True:
        it = len(history) + 1
        HA._RESOLVE_CACHE.clear()  # aliases may have changed -> re-resolve fresh
        train_unresolved = [q for q in train if not resolve_query(q).iris]
        held_unresolved = [q for q in held if not resolve_query(q).iris]
        # REFINE auto-safe: derive a dict-validated alias for each TRAIN-unresolved query.
        applied = 0
        for q in train_unresolved:
            d = derive_validated_alias(q)
            if d:
                append_alias(d[0], d[1])
                HA._RESOLVE_CACHE.clear()
                applied_aliases.append(d)
                applied += 1
                print(f"  [iter {it}] AUTO-SAFE alias: {d[0]!r} -> {d[1]!r} ({d[2]})", file=sys.stderr)
        still_unresolved_train = [q for q in train if not resolve_query(q).iris]
        snapshot = {
            "iter": it,
            "train_unresolved_before": len(train_unresolved),
            "held_unresolved": len(held_unresolved),
            "applied_refinements": applied,
            "auto_safe_pending_train": len(still_unresolved_train),
        }
        history.append(snapshot)
        print(f"  [iter {it}] train unresolved {len(train_unresolved)}->{len(still_unresolved_train)} "
              f"applied={applied} held-unresolved={len(held_unresolved)}", file=sys.stderr)
        stop, reason = should_terminate(history, max_iters)
        if stop:
            print(f"  TERMINATE: {reason}", file=sys.stderr)
            break

    # IMPACT + GATED worklist: measure full-corpus recall + precision on the queries we just made resolve,
    # plus surface the still-unresolved. Split them: MULTI-ENTITY queries (organism + protein) were
    # deliberately NOT aliased (a single-taxon alias would discard the protein) -> gated for the
    # compound-query path; the rest need a human alias.
    gated: list[GatedItem] = []
    for q in still_unresolved_train + held_unresolved:
        split = classify_query_entities(q)
        if split.multi_entity:
            gated.append(GatedItem(q, "*", "multi_entity",
                                   f"organism={split.organism!r} + protein={split.protein_term!r} — build a "
                                   f"compound (taxon AND protein) query, do NOT alias to the organism alone"))
        else:
            gated.append(GatedItem(q, "*", "unresolved_query_unvalidatable",
                                   "no fuzzy + no dict-validated LLM expansion — needs a human alias"))
    impact = []
    for term, canon, method in applied_aliases:
        for name, src, dst in pairs:
            rec = measure_recall_full(client, name, dst, term)
            prec = audit_query_source(client, name, dst, term, run_llm=True)
            if rec:
                impact.append({"query": term, "alias": canon, "method": method, "source": name,
                               "gold": rec.gold, "recall_after": rec.recall_after,
                               "advisory_precision": prec.advisory_precision if prec else None})
                if rec.recall_after is not None and rec.recall_after < RECALL_AFTER_FLOOR:
                    gated.append(GatedItem(term, name, "low_recall_after",
                                           f"recall_after {rec.recall_after} < {RECALL_AFTER_FLOOR}"))
                if prec and prec.llm_flagged:
                    gated.append(GatedItem(term, name, "false_membership",
                                           f"{prec.llm_flagged}/{prec.harm_total} flagged: {prec.false_organisms[:1]}"))

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "refine_history_9src.json").write_text(json.dumps(
        {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "terminate_reason": reason,
         "iterations": history, "applied_aliases": applied_aliases, "impact": impact}, indent=2))
    (out_dir / "failure_taxonomy_9src.json").write_text(json.dumps(
        {"gated_worklist": [asdict(g) for g in gated]}, indent=2))
    print(f"\n=== 9-source self-refining loop done ({reason}) ===", file=sys.stderr)
    print(f"  auto-safe aliases applied: {[(a[0], a[1]) for a in applied_aliases]}", file=sys.stderr)
    print(f"  GATED worklist: {len(gated)} items (need human/dict/harvest action)", file=sys.stderr)
    for g in gated[:8]:
        print(f"    - {g.query}/{g.source} [{g.category}]: {g.evidence}", file=sys.stderr)
    print(f"  wrote {out_dir}/{{refine_history_9src,failure_taxonomy_9src}}.json", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-iters", type=int, default=4)
    ap.add_argument("--indices", default=None, help="comma-separated source short names (default: all 9)")
    ap.add_argument("--max-queries", type=int, default=None)
    ap.add_argument("--held-out-every", type=int, default=5, help="every Kth query (sorted) is held out; 0 disables")
    ap.add_argument("--categories", default="abbreviations",
                    help="comma-separated: mu_virus,abbreviations,real_world (default: abbreviations)")
    ap.add_argument("--out", default=str(_HERE / "output"))
    args = ap.parse_args(argv)
    return run_loop(args.max_iters, args.indices.split(",") if args.indices else None,
                    args.max_queries, args.held_out_every, Path(args.out),
                    categories=tuple(args.categories.split(",")))


if __name__ == "__main__":
    raise SystemExit(main())
