"""Full-corpus PRECISION audit of the harmonized DEST indices — does a record REALLY belong to the
requested pathogen, or a related / similarly-named one?

The harmonized cell filters subjects.valueUri ∈ the query taxon, so every record carries the query
taxon STAMP. Precision asks whether that stamp is CORRECT. A mis-stamp shows up as a record whose
source-native organism resolves to a DIFFERENT (related / similarly-named) taxon than the query.

Tractable over the FULL corpus (no --limit) via Globus FACETS: a query's 100k+ records collapse to a
handful of distinct native-organism NAMES (influenza taxon 11320 -> one name across 114,627 records).
So we judge distinct organism NAMES, scaling each judgment to its record count — never per record.

  facet harm-set on native organism  -> [(organism, count), ...]   (whole corpus, limit:0)
  resolve each organism (dict, AUTHORITATIVE):
     in the query subtree  -> BELONGS (no LLM)
     elsewhere / unresolvable -> SUSPECT -> LLM-naming judge (ADVISORY)
       (a sequence-similarity second judge on flagged suspects is a planned follow-up)
  advisory_precision = 1 - (Σ LLM-flagged counts) / harm_total
  LLM-flagged records -> a GATED worklist for human / sequence confirmation (never auto-applied)

IMPORTANT — the LLM is ADVISORY, not authoritative. It is naming-blind to recent taxonomy: e.g. it
judges "Alphainfluenzavirus influenzae" as NOT Influenza A ("different species"), though it is the 2023
ICTV rename of Influenza A. The DICTIONARY catches such renames it knows (species expansion links
11320<->2955291, so that organism is marked BELONGS and never reaches the LLM). The LLM only ever sees
organisms the dict cannot place — and where the dict is ALSO stale for a rename, the LLM may false-flag
it. So LLM-flagged counts are a CANDIDATE set for human / sequence-similarity confirmation, and the
precision number is an advisory LOWER BOUND, not a confirmed error rate.

Read-only: anonymous Globus + the local dictionary + a local Ollama judge. No DEST write.

Usage:  uv run python benchmarks/precision_audit.py [--categories C] [--max-queries N] [--no-llm]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from harmonization_ablation import (
    _build_client, resolve_query, _taxon_matches, _query_descendants,
    ORGANISM_FIELD, SOURCE_REGISTRY, DEST_REGISTRY, _load_queries,
)
from apecx_harvesters.dict_reader import configure_dictionary_path, default_dictionary_path, get_dictionary_index

_HERE = Path(__file__).resolve().parent
_LLM_BASE = os.environ.get("APECX_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
_LLM_MODEL = os.environ.get("APECX_LLM_MODEL", "mistral-nemo:latest")
_FACET_SIZE = 2000  # distinct organism buckets; protein sources carry thousands of strain-level names.
                    # Any records beyond the returned buckets are reported as unjudged (never assumed OK).


def _facet_native_organism(client, dest: str, source: str, s_q: set[str]
                           ) -> tuple[int, list[tuple[str, int]]]:
    """(harm_total, [(organism, count), ...]) over the WHOLE harmonized corpus (subjects.valueUri ∈ s_q).
    Globus facet type=terms on the nested native organism field; limit:0 = no record fetch, full corpus."""
    field = f"{source}.{ORGANISM_FIELD.get(source, '')}"
    payload = {
        "q": "*",
        "filters": [{"type": "match_any", "field_name": "subjects.valueUri", "values": sorted(s_q)}],
        "facets": [{"name": "org", "type": "terms", "field_name": field, "size": _FACET_SIZE}],
        "limit": 0,
    }
    try:
        data = client.post_search(dest, payload).data
    except Exception as exc:  # noqa: BLE001
        print(f"  facet error on {dest}: {exc}", file=sys.stderr)
        return 0, []
    total = int(data.get("total", 0))
    fr = data.get("facet_results") or []
    buckets = [(b.get("value"), int(b.get("count", 0))) for b in (fr[0].get("buckets", []) if fr else [])
               if isinstance(b.get("value"), str)]
    return total, buckets


def _organism_belongs_by_dict(organism: str, s_q: set[str], desc_ids: set[int]) -> bool | None:
    """Cheap first pass: resolve the organism via the dictionary. True/False if it resolves;
    None if unresolvable (then the LLM decides)."""
    rr = resolve_query(organism, enable_fuzzy=False)
    if not rr.iris:
        return None
    return any(_taxon_matches(ti, s_q, desc_ids) for ti in rr.iris)


def _extract_json(text: str) -> dict | None:
    """Lenient parse: first {...} object in the LLM response."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None


_JUDGE_CACHE: dict[tuple[str, str], dict] = {}


def llm_judge(organism: str, pathogen: str, *, model: str = _LLM_MODEL, timeout: int = 60) -> dict:
    """Explainable LLM-naming judge: does a record whose source organism is `organism` belong to the
    requested `pathogen`, or a different/similarly-named/related one? Returns
    {"belongs": bool|None, "reason": str}. belongs=None on an LLM/parse failure (treated conservatively
    as still-suspect, never silently dropped). Cached per (organism, pathogen) — the same strain name
    recurs across queries+sources, so a full-corpus run judges each distinct pair once."""
    key = (organism, pathogen)
    if key in _JUDGE_CACHE:
        return _JUDGE_CACHE[key]
    system = ("You are a virologist adjudicating database taxonomy. Decide whether a record whose source "
              "organism is the GIVEN organism truly belongs to the REQUESTED pathogen, as opposed to a "
              "different but similarly-named or merely related organism (e.g. a different species in the "
              "same family, a chimera, or a name collision). Judge by naming and taxonomy only. Respond "
              'ONLY with JSON: {"belongs": true or false, "reason": "<one short sentence, naming-based>"}.')
    user = (f"REQUESTED pathogen: {pathogen}\nrecord source organism: {organism}\n"
            "Does the record belong to the requested pathogen?")
    body = json.dumps({"model": model, "temperature": 0,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(f"{_LLM_BASE}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        content = resp["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        return {"belongs": None, "reason": f"LLM error: {type(exc).__name__}"}  # transient: not cached
    parsed = _extract_json(content)
    if not parsed or "belongs" not in parsed:
        return {"belongs": None, "reason": f"unparseable: {content[:80]}"}  # not cached -> may retry
    result = {"belongs": bool(parsed["belongs"]), "reason": str(parsed.get("reason", ""))[:200]}
    _JUDGE_CACHE[key] = result  # cache only definitive verdicts
    return result


@dataclass
class PrecisionRow:
    source: str
    query: str
    harm_total: int                 # full-corpus harmonized record count (no limit)
    distinct_organisms: int
    belongs_dict: int               # records whose organism resolved into the subtree (no LLM needed)
    suspect_records: int            # records on suspect organism names (resolved elsewhere / unresolved)
    llm_flagged: int            # records on organisms the ADVISORY LLM judged NOT-belonging (candidates)
    judge_unresolved: int           # records on suspect organisms the LLM could not decide (error/unparse)
    unjudged_records: int           # records beyond the facet buckets / with no organism field — NOT
                                    # judged, NOT assumed OK; disclosed as a coverage gap
    advisory_precision: float | None  # 1 - llm_flagged/harm_total. A LOWER bound w.r.t. LLM false-flagging
                                      # (the LLM may dispute renames the dict missed); but an UPPER bound
                                      # for the unjudged/unresolved slice (counted as not-flagged). None on
                                      # total judge failure. Confirm flags via human / sequence check.
    false_organisms: list           # [(organism, count, reason), ...] for the gated worklist


def _tally_buckets(buckets, classify, judge, run_llm):
    """Pure tally (no network): for each (organism, count) bucket, classify (->True belongs / False or
    None suspect). For a suspect, run the judge if enabled; belongs=False -> flagged, belongs=None
    (LLM error/unparseable) -> unresolved (NOT clean, NOT flagged — the judge simply couldn't decide).
    classify/judge are injected so this is unit-testable with stubs.
    Returns (belongs, suspect, flagged, unresolved, false_orgs)."""
    belongs = suspect = flagged = unresolved = 0
    false_orgs: list = []
    for organism, count in buckets:
        if classify(organism) is True:
            belongs += count
            continue
        suspect += count
        if run_llm:
            j = judge(organism)
            if j["belongs"] is False:
                flagged += count
                false_orgs.append((organism, count, j["reason"]))
            elif j["belongs"] is None:
                unresolved += count
    return belongs, suspect, flagged, unresolved, false_orgs


def audit_query_source(client, source: str, dest: str, term: str, *, run_llm: bool) -> PrecisionRow | None:
    res = resolve_query(term)
    if not res.iris:
        return None  # query unresolved -> recall-side diagnose, not a precision cell
    desc_ids = _query_descendants(res.iris)
    harm_total, buckets = _facet_native_organism(client, dest, source, res.s_q)
    if harm_total == 0:
        return None
    belongs_dict, suspect_records, llm_flagged, judge_unresolved, false_orgs = _tally_buckets(
        buckets,
        classify=lambda org: _organism_belongs_by_dict(org, res.s_q, desc_ids),
        judge=lambda org: llm_judge(org, term),
        run_llm=run_llm)
    # FAIL-LOUD on total judge failure: if the LLM was asked to judge suspects but resolved NONE of them
    # (e.g. Ollama unreachable), precision is unknowable — report None, never a fake 1.0.
    judge_failed = run_llm and suspect_records > 0 and judge_unresolved == suspect_records and llm_flagged == 0
    return PrecisionRow(
        source=source, query=term, harm_total=harm_total, distinct_organisms=len(buckets),
        belongs_dict=belongs_dict, suspect_records=suspect_records, llm_flagged=llm_flagged,
        judge_unresolved=judge_unresolved,
        unjudged_records=max(0, harm_total - belongs_dict - suspect_records),
        advisory_precision=(None if (judge_failed or not harm_total)
                            else round(1 - llm_flagged / harm_total, 4)),
        false_organisms=false_orgs)


def run(categories: list[str], max_queries: int | None, indices: list[str] | None,
        run_llm: bool, out_dir: Path) -> int:
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

    rows: list[PrecisionRow] = []
    for category in categories:
        queries = _load_queries(category)
        if max_queries:
            queries = queries[:max_queries]
        for term in queries:
            for name, src, dst in pairs:
                row = audit_query_source(client, name, dst, term, run_llm=run_llm)
                if row:
                    rows.append(row)
                    if row.llm_flagged:
                        print(f"  ADVISORY FLAG {name}/{term!r}: {row.llm_flagged}/{row.harm_total} "
                              f"records on organisms the LLM disputes -> {row.false_organisms[:2]}",
                              file=sys.stderr)

    flagged = [r for r in rows if r.llm_flagged]
    scored = [r.advisory_precision for r in rows if r.advisory_precision is not None]
    agg = {
        "cells": len(rows),
        "cells_precision_unknown": len(rows) - len(scored),  # judge-failed cells (precision is None)
        "total_harm_records": sum(r.harm_total for r in rows),
        "total_suspect_records": sum(r.suspect_records for r in rows),
        "total_judge_unresolved": sum(r.judge_unresolved for r in rows),
        "total_unjudged_records": sum(r.unjudged_records for r in rows),
        "total_llm_flagged": sum(r.llm_flagged for r in rows),
        "cells_with_flag": len(flagged),
        "mean_advisory_precision": round(sum(scored) / len(scored), 4) if scored else None,
    }
    report = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "judge": f"dict resolve + LLM-naming ({_LLM_MODEL})" if run_llm else "dict resolve only",
              "full_corpus": True, "aggregate": agg, "rows": [asdict(r) for r in rows]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "precision_audit_report.json").write_text(json.dumps(report, indent=2))
    print(f"\n=== full-corpus precision audit, {len(rows)} cells ===", file=sys.stderr)
    print(f"  total harmonized records audited: {agg['total_harm_records']:,} (NO --limit)", file=sys.stderr)
    print(f"  suspect records (organism resolves elsewhere/unresolved): {agg['total_suspect_records']:,}", file=sys.stderr)
    print(f"  unjudged records (no organism field / facet tail — coverage gap, NOT assumed OK): "
          f"{agg['total_unjudged_records']:,}", file=sys.stderr)
    print(f"  judge-unresolved records (LLM errored/unparseable — not flagged, not clean): "
          f"{agg['total_judge_unresolved']:,}", file=sys.stderr)
    print(f"  LLM-flagged (ADVISORY) candidate records: {agg['total_llm_flagged']:,} "
          f"in {agg['cells_with_flag']} cells -> gated worklist for human/sequence confirmation", file=sys.stderr)
    print(f"  mean advisory precision: {agg['mean_advisory_precision']} "
          f"({agg['cells_precision_unknown']} cells unknown — judge failed)", file=sys.stderr)
    print(f"  wrote {out_dir}/precision_audit_report.json", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--categories", default="abbreviations")
    ap.add_argument("--max-queries", type=int, default=None)
    ap.add_argument("--indices", default=None, help="comma-separated source short names (default: all 9)")
    ap.add_argument("--no-llm", action="store_true", help="dict-resolve only (skip the LLM judge)")
    ap.add_argument("--out", default=str(_HERE / "output"))
    args = ap.parse_args(argv)
    return run(args.categories.split(","), args.max_queries,
               args.indices.split(",") if args.indices else None, not args.no_llm, Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
