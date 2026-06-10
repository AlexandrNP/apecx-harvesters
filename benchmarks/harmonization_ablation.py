"""Harmonization ablation benchmark — raw vs harmonized search × source vs harmonized indices.

A single-command benchmark over a 2×2 ablation:

                    SOURCE index            DEST (harmonized) index
  raw search        q=<term> substring      q=<term> substring
  harmonized        resolve→native field    resolve→subjects.valueUri

across three query categories (mu-virus list, abbreviations, real-world).
For every cell it reports the number of retrieved records AND a
false-retrieval estimate adjudicated by the synonym dictionary itself.

Adjudication oracle (dictionary-as-truth):
  Each retrieved record is bucketed true / false / unknown for the query Q,
  whose resolved canonical IRI set (plus species-rank ancestors) is S_Q:
    * DEST record  → TRUE iff its ``subjects.valueUri`` (∪ species ancestors)
      intersects S_Q; FALSE if it carries NCBITaxon subjects that miss S_Q;
      UNKNOWN if it carries no NCBITaxon subject.
    * SOURCE record → resolve the record's organism text field through the
      dictionary; TRUE iff that IRI (∪ species ancestor) ∈ S_Q, FALSE if it
      resolves elsewhere, UNKNOWN if absent/unresolvable.
  Precision = true / (true + false). UNKNOWN is never folded into either.

The raw bucketed records are saved per cell so a different adjudication can
be re-scored without re-querying.

Honest limitation: for the harmonized-DEST cell the oracle reads the same
field the query filtered on, so its precision measures *harmonization
stamping correctness*, not search precision. The independent signal is raw
search precision (the organism oracle catches substring over-matching).

Run:
  uv run python benchmarks/harmonization_ablation.py            # full (multi-hour)
  uv run python benchmarks/harmonization_ablation.py --limit 100 --max-queries 5  # smoke
  uv run python benchmarks/harmonization_ablation.py --categories abbreviations
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import globus_sdk

from apecx_harvesters.dict_reader import (
    configure_dictionary_path,
    default_dictionary_path,
    get_dictionary_index,
    lookup_entity,
)
from apecx_harvesters.pipeline.harmonize import DEST_REGISTRY, SOURCE_REGISTRY

_PREF = "http://purl.obolibrary.org/obo/NCBITaxon_"
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent

# Per-source native field for the harmonized-SOURCE cell. SOURCE indices use
# FLAT field names (verified 2026-06-09: bvbrc_genome source record has
# top-level "Species", not "bvbrc_genome.Species"). 'label' filters on the
# organism name string(s); 'taxon_id' on the integer NCBI taxon column.
HARMONIZED_FILTER: dict[str, dict[str, str]] = {
    "violin_pathogen": {"field": "NCBI_Taxonomy_ID", "shape": "taxon_id"},
    "violin_vaccine": {"field": "VIOLIN_c_pathogen_id", "shape": "taxon_id"},
    "violin_gene": {"field": "Organism", "shape": "label"},
    "bvbrc_genome": {"field": "Species", "shape": "label"},
    "bvbrc_protein": {"field": "Genome", "shape": "label"},
    "bvbrc_protein_structure": {"field": "Organism_Name", "shape": "label"},
    "bvbrc_epitope": {"field": "Organism", "shape": "label"},
    "antiviraldb": {"field": "Virus", "shape": "label"},
    "protabank": {"field": "Title", "shape": "label"},
}

# Per-source organism text field for SOURCE-record adjudication. SOURCE records
# are FLAT (top-level field), unlike DEST records which nest under a container.
ORGANISM_FIELD: dict[str, str] = {
    "violin_pathogen": "Pathogen",
    "violin_vaccine": "Vaccine_Name",
    "violin_gene": "Organism",
    "bvbrc_genome": "Species",
    "bvbrc_protein": "Genome",
    "bvbrc_protein_structure": "Organism_Name",
    "bvbrc_epitope": "Organism",
    "antiviraldb": "Virus",
    "protabank": "Title",
}


# ---------------------------------------------------------------------------
# Globus client (authenticated when creds available; else anonymous)
# ---------------------------------------------------------------------------


def _build_client() -> globus_sdk.SearchClient:
    cid = os.environ.get("GLOBUS_CLIENT_ID", "").strip()
    secret = os.environ.get("GLOBUS_CLIENT_SECRET", "").strip()
    if not (cid and secret):
        try:
            import keyring  # noqa: PLC0415

            cid = keyring.get_password("nanobrain-globus", "client_id") or ""
            secret = keyring.get_password("nanobrain-globus", "client_secret") or ""
        except Exception:
            cid = secret = ""
    if cid and secret:
        conf = globus_sdk.ConfidentialAppAuthClient(cid, secret)
        authz = globus_sdk.ClientCredentialsAuthorizer(conf, globus_sdk.SearchClient.scopes.all)
        print("globus: authenticated confidential client", file=sys.stderr)
        return globus_sdk.SearchClient(authorizer=authz)
    print("globus: anonymous client (no creds found)", file=sys.stderr)
    return globus_sdk.SearchClient()


# ---------------------------------------------------------------------------
# Resolution + S_Q (query canonical IRI set, species-expanded)
# ---------------------------------------------------------------------------


# IRI → species-ancestor IRI memo. species_iri_for opens a fresh read-only
# SQLite connection to the 771MB dict on every call; adjudication hits the
# same handful of taxa across thousands of records, so without this the
# benchmark is connection-open-bound (CPU pegged at ~98%).
_SPECIES_CACHE: dict[str, str | None] = {}


def _species_expand(iris: set[str]) -> set[str]:
    """Add the species-rank ancestor of every NCBITaxon IRI in the set."""
    index, _ = get_dictionary_index()
    out = set(iris)
    if index is None:
        return out
    for iri in iris:
        if not iri.startswith(_PREF):
            continue
        if iri not in _SPECIES_CACHE:
            _SPECIES_CACHE[iri] = index.species_iri_for(iri)
        sp = _SPECIES_CACHE[iri]
        if sp:
            out.add(sp)
    return out


# Query IRI set → descendant taxon-id set (the subtree under the query taxon).
# Memoized: lookup_descendant_taxon_ids runs a recursive CTE + opens a
# connection per call. Used by the adjudication oracle so a broad query
# ("adenovirus", a genus) CREDITS a specific-species record ("Human
# adenovirus C") as a true hit instead of a false positive — the oracle does
# upward species-normalization AND downward descendant matching.
_DESC_CACHE: dict[frozenset[str], set[int]] = {}


def _query_descendants(iris: set[str]) -> set[int]:
    key = frozenset(iris)
    if key in _DESC_CACHE:
        return _DESC_CACHE[key]
    index, _ = get_dictionary_index()
    out: set[int] = set()
    if index is not None:
        for iri in iris:
            out.update(index.lookup_descendant_taxon_ids(iri))
    _DESC_CACHE[key] = out
    return out


def _taxon_matches(taxon_iri: str, s_q: set[str], desc_ids: set[int]) -> bool:
    """Is a record's taxon within the query entity's subtree (any rank)?

    True when the record taxon equals the query taxon, its species ancestor
    is in S_Q (strain→species upward), or its id is a descendant of the query
    taxon (downward — a specific species under a broad query).
    """
    if taxon_iri in s_q:
        return True
    if _species_expand({taxon_iri}) & s_q:
        return True
    try:
        tid = int(taxon_iri[len(_PREF):])
    except (ValueError, TypeError):
        return False
    return tid in desc_ids


@dataclass
class Resolution:
    term: str
    path: str
    iris: set[str]  # resolved canonical IRIs (NCBITaxon only, for filtering)
    labels: list[str]  # canonical label + synonyms
    taxa: list[int]  # integer taxon ids
    s_q: set[str] = field(default_factory=set)  # iris ∪ species ancestors


_RESOLVE_CACHE: dict[tuple[str, bool], Resolution] = {}

# Curated alias map (lazy-loaded). alias(normalized) → canonical surface form.
_ALIASES: dict[str, str] | None = None
_USE_ALIASES = False

# Optional NER (Change C). LLM entity extraction from free-text queries. The
# implementation lives in apecx_db_integration (a sibling repo, LLM-backed);
# it is NOT a dependency of apecx-harvesters by design. We try a direct import
# first; if absent we shell out to apecx-mcp-integration's venv (which has it
# editable-installed); if neither works, NER is disabled and the benchmark
# runs as-is.
_USE_NER = False
_NER_VENV_PY = (
    _REPO.parent / "apecx-mcp-integration" / ".venv" / "bin" / "python"
)
_NER_MODEL = os.environ.get("APECX_LLM_MODEL", "mistral-nemo:latest")
_NER_BASE_URL = os.environ.get("APECX_LLM_BASE_URL", "http://localhost:11434/v1")
_NER_CACHE: dict[str, list[str]] = {}


def _ner_extract(query: str) -> list[str]:
    """Extract candidate entity names from a free-text query (LLM). [] on miss."""
    if query in _NER_CACHE:
        return _NER_CACHE[query]
    names: list[str] = []
    try:  # direct import (only if apecx_db_integration installed in this env)
        from apecx_db_integration import extract_entities_llm  # noqa: PLC0415

        names = [e.get("name") for e in extract_entities_llm(query) if e.get("name")]
    except Exception:
        # Subprocess fallback to the sibling venv that has the package.
        if _NER_VENV_PY.exists():
            import subprocess  # noqa: PLC0415

            snippet = (
                "import json,sys; from apecx_db_integration import extract_entities_llm; "
                "print(json.dumps([e.get('name') for e in extract_entities_llm(sys.argv[1]) "
                "if e.get('name')]))"
            )
            env = {**os.environ, "APECX_LLM_MODEL": _NER_MODEL, "APECX_LLM_BASE_URL": _NER_BASE_URL}
            try:
                out = subprocess.run(
                    [str(_NER_VENV_PY), "-c", snippet, query],
                    capture_output=True, text=True, timeout=120, env=env,
                )
                if out.returncode == 0 and out.stdout.strip():
                    names = json.loads(out.stdout.strip().splitlines()[-1])
            except Exception as exc:
                print(f"  NER subprocess failed: {exc}", file=sys.stderr)
    _NER_CACHE[query] = names
    return names


def _lookup_to_iris(term: str, enable_fuzzy: bool) -> tuple[str, set[str], list[str]]:
    """Alias-redirect + dictionary lookup → (path, ncbitaxon IRIs, labels)."""
    lookup_term = term
    if _USE_ALIASES and enable_fuzzy:
        lookup_term = _load_aliases().get(term.strip().lower(), term)
    r = lookup_entity(lookup_term, enable_fuzzy=enable_fuzzy)
    iris: set[str] = set()
    labels: list[str] = []
    if r.path == "ambiguous":
        for c in r.candidates:
            if c.canonical_iri:
                iris.add(c.canonical_iri)
            if c.canonical_label:
                labels.append(c.canonical_label)
    else:
        if r.canonical_iri:
            iris.add(r.canonical_iri)
        if r.canonical_label:
            labels.append(r.canonical_label)
    for syn in r.synonyms or ():
        if isinstance(syn, str) and syn and syn not in labels:
            labels.append(syn)
    return r.path, {i for i in iris if i.startswith(_PREF)}, labels


def _load_aliases() -> dict[str, str]:
    global _ALIASES
    if _ALIASES is None:
        _ALIASES = {}
        path = _HERE / "queries" / "curated_aliases.tsv"
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    _ALIASES[parts[0].strip().lower()] = parts[1].strip()
    return _ALIASES


def resolve_query(term: str, *, enable_fuzzy: bool = True) -> Resolution:
    """Resolve a term to its canonical IRI set.

    ``enable_fuzzy`` defaults True for USER queries (people type approximate
    terms). Record-organism adjudication passes False: a record's organism is
    a CONTROLLED field that should match exactly — a fuzzy match there would
    loosely bucket a different organism as the query entity and inflate the
    'true' count (the same reason the republish resolver uses fuzzy off).
    """
    key = (term, enable_fuzzy)
    if key in _RESOLVE_CACHE:
        return _RESOLVE_CACHE[key]
    # Alias redirect happens inside _lookup_to_iris (query-side only; never on
    # record-organism adjudication, which must judge the record's real organism).
    path, iris, labels = _lookup_to_iris(term, enable_fuzzy)
    # NER fallback: free-text query that didn't resolve → extract entities and
    # resolve each. Query-side only (enable_fuzzy). Never recurses into NER.
    if not iris and enable_fuzzy and _USE_NER:
        for ent in _ner_extract(term):
            _, e_iris, e_labels = _lookup_to_iris(ent, enable_fuzzy=True)
            if e_iris:
                iris |= e_iris
                labels.extend(label for label in e_labels if label not in labels)
        if iris:
            path = "ner"
    taxa = [int(i[len(_PREF):]) for i in iris]
    res = Resolution(
        term=term,
        path=path,
        iris=iris,
        labels=labels,
        taxa=taxa,
        s_q=_species_expand(iris),
    )
    _RESOLVE_CACHE[key] = res
    return res


# ---------------------------------------------------------------------------
# The four query cells
# ---------------------------------------------------------------------------


def _post(client, index_id: str, payload: dict, label: str) -> tuple[int, list[dict]]:
    try:
        resp = client.post_search(index_id, payload)
    except (globus_sdk.GlobusAPIError, globus_sdk.NetworkError) as e:
        print(f"  [{label}] error: {e}", file=sys.stderr)
        return -1, []
    data = resp.data
    total = int(data.get("total", 0))
    out = []
    for g in data.get("gmeta", []):
        ents = g.get("entries", [])
        if ents:
            out.append(ents[0].get("content", {}))
    return total, out


def _raw_payload(term: str, limit: int) -> dict:
    # Quote when the term carries non-alphanumerics so Globus does a phrase
    # match instead of OR-tokenizing (q=HSV-2 → "HSV OR 2", 138k false hits).
    q = term
    if any(not c.isalnum() and not c.isspace() for c in term) or " " in term:
        if not (term.startswith('"') and term.endswith('"')):
            q = f'"{term}"'
    return {"q": q, "limit": limit}


def cell_raw(client, index_id: str, term: str, limit: int, label: str) -> tuple[int, list[dict]]:
    return _post(client, index_id, _raw_payload(term, limit), label)


def cell_harmonized_source(
    client, index_id: str, source: str, res: Resolution, limit: int, label: str
) -> tuple[int, list[dict]]:
    spec = HARMONIZED_FILTER.get(source)
    if spec is None:
        return 0, []
    if spec["shape"] == "taxon_id":
        values: list[Any] = res.taxa
    else:
        values = res.labels
    if not values:
        return 0, []
    payload = {
        "filters": [{"type": "match_any", "field_name": spec["field"], "values": values}],
        "limit": limit,
    }
    return _post(client, index_id, payload, label)


# Globus match_any tolerates ~1300 filter values (verified). Chunk below that
# so a broad query's full descendant subtree (e.g. HIV-1 ~2832) can be filtered
# without a single oversized payload.
_MAX_FILTER_VALUES = 1000


def cell_harmonized_dest(
    client,
    index_id: str,
    res: Resolution,
    limit: int,
    label: str,
    expand_descendants: set[int] | None = None,
) -> tuple[int, list[dict]]:
    values = set(res.s_q)
    if expand_descendants:
        values |= {f"{_PREF}{d}" for d in expand_descendants}
    values = sorted(values)
    if not values:
        return 0, []
    if len(values) <= _MAX_FILTER_VALUES:
        payload = {
            "filters": [{"type": "match_any", "field_name": "subjects.valueUri", "values": values}],
            "limit": limit,
        }
        return _post(client, index_id, payload, label)
    # Chunked: the descendant taxa are disjoint, so a record matches at most a
    # few chunks. Sum totals (a near-exact upper bound — a record with taxa in
    # two chunks double-counts, rare) and concatenate fetched records up to
    # ``limit`` for the adjudication sample.
    total = 0
    records: list[dict] = []
    for i in range(0, len(values), _MAX_FILTER_VALUES):
        chunk = values[i : i + _MAX_FILTER_VALUES]
        payload = {
            "filters": [{"type": "match_any", "field_name": "subjects.valueUri", "values": chunk}],
            "limit": limit,
        }
        t, recs = _post(client, index_id, payload, f"{label}#chunk{i // _MAX_FILTER_VALUES}")
        if t > 0:
            total += t
        records.extend(recs)
    return total, records[:limit]


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------


def _record_taxon_iris(record: dict) -> set[str]:
    """NCBITaxon subjects.valueUri carried by a DEST (harmonized) record."""
    out: set[str] = set()
    for s in record.get("subjects") or []:
        if isinstance(s, dict):
            v = s.get("valueUri")
            if isinstance(v, str) and v.startswith(_PREF):
                out.add(v)
    return out


def _record_organism_text(record: dict, source: str) -> str | None:
    fieldname = ORGANISM_FIELD.get(source)
    if fieldname is None:
        return None
    val = record.get(fieldname)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def adjudicate_dest(records: list[dict], s_q: set[str], desc_ids: set[int]) -> dict[str, int]:
    """Bucket DEST records by subjects.valueUri within the query subtree."""
    # No resolved target → we cannot judge correctness; everything is unknown.
    if not s_q and not desc_ids:
        return {"true": 0, "false": 0, "unknown": len(records)}
    t = f_ = u = 0
    for r in records:
        taxa = _record_taxon_iris(r)
        if not taxa:
            u += 1
            continue
        if any(_taxon_matches(ti, s_q, desc_ids) for ti in taxa):
            t += 1
        else:
            f_ += 1
    return {"true": t, "false": f_, "unknown": u}


def adjudicate_source(
    records: list[dict], source: str, s_q: set[str], desc_ids: set[int]
) -> dict[str, int]:
    """Bucket SOURCE records by resolving the organism text field."""
    # No resolved target → unadjudicable (see adjudicate_dest).
    if not s_q and not desc_ids:
        return {"true": 0, "false": 0, "unknown": len(records)}
    t = f_ = u = 0
    for r in records:
        organism = _record_organism_text(r, source)
        if not organism:
            u += 1
            continue
        rr = resolve_query(organism, enable_fuzzy=False)
        if not rr.iris:
            u += 1
        elif any(_taxon_matches(ti, s_q, desc_ids) for ti in rr.iris):
            t += 1
        else:
            f_ += 1
    return {"true": t, "false": f_, "unknown": u}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _load_queries(category: str) -> list[str]:
    if category == "mu_virus":
        path = _REPO / "search_demo" / "data" / "mu-virus-list.txt"
    else:
        path = _HERE / "queries" / f"{category}.txt"
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]


@dataclass
class CellAgg:
    retrieved_total: int = 0  # sum of index-side totals
    adj_records: int = 0  # records actually adjudicated (sampled)
    true: int = 0
    false: int = 0
    unknown: int = 0
    queries_with_hits: int = 0

    def precision(self) -> float | None:
        denom = self.true + self.false
        return round(self.true / denom, 4) if denom else None


def run(
    categories: list[str],
    limit: int,
    max_queries: int | None,
    indices: list[str] | None,
    out_dir: Path,
    expand_descendants: bool = False,
    only_queries: set[str] | None = None,
) -> int:
    dict_path = Path(
        os.environ.get("APECX_SYNONYM_DICT_PATH", str(default_dictionary_path()))
    )
    if not dict_path.exists():
        print(f"dictionary not present at {dict_path}", file=sys.stderr)
        return 2
    configure_dictionary_path(dict_path)
    if get_dictionary_index()[0] is None:
        print("dictionary failed to load", file=sys.stderr)
        return 2
    client = _build_client()

    pairs = [
        (name, src, DEST_REGISTRY[src])
        for src, (name, _) in SOURCE_REGISTRY.items()
        if indices is None or name in indices
    ]
    cells = ("raw_source", "harm_source", "raw_dest", "harm_dest")

    report: dict[str, Any] = {"generated_utc": None, "limit": limit, "categories": {}}
    per_query_rows: list[dict] = []

    for category in categories:
        queries = _load_queries(category)
        if only_queries is not None:
            queries = [q for q in queries if q in only_queries]
        if max_queries:
            queries = queries[:max_queries]
        print(f"\n### category={category}  ({len(queries)} queries)", file=sys.stderr)
        agg = {c: CellAgg() for c in cells}
        resolved_count = 0

        for qi, term in enumerate(queries, 1):
            res = resolve_query(term)
            if res.iris:
                resolved_count += 1
            desc_ids = _query_descendants(res.iris)
            print(
                f"  [{qi}/{len(queries)}] {term!r} path={res.path} "
                f"iris={len(res.iris)} s_q={len(res.s_q)} descendants={len(desc_ids)}",
                file=sys.stderr,
            )
            for name, src, dst in pairs:
                lbl = f"{category}:{term}:{name}"
                rs_t, rs_r = cell_raw(client, src, term, limit, lbl + ":raw_src")
                hs_t, hs_r = cell_harmonized_source(client, src, name, res, limit, lbl + ":harm_src")
                rd_t, rd_r = cell_raw(client, dst, term, limit, lbl + ":raw_dst")
                hd_t, hd_r = cell_harmonized_dest(
                    client, dst, res, limit, lbl + ":harm_dst",
                    expand_descendants=desc_ids if expand_descendants else None,
                )

                judged = {
                    "raw_source": (rs_t, adjudicate_source(rs_r, name, res.s_q, desc_ids)),
                    "harm_source": (hs_t, adjudicate_source(hs_r, name, res.s_q, desc_ids)),
                    "raw_dest": (rd_t, adjudicate_dest(rd_r, res.s_q, desc_ids)),
                    "harm_dest": (hd_t, adjudicate_dest(hd_r, res.s_q, desc_ids)),
                }
                row = {"category": category, "term": term, "index": name, "path": res.path}
                for cell, (total, adj) in judged.items():
                    a = agg[cell]
                    if total > 0:
                        a.retrieved_total += total
                        a.queries_with_hits += 1
                    a.adj_records += adj["true"] + adj["false"] + adj["unknown"]
                    a.true += adj["true"]
                    a.false += adj["false"]
                    a.unknown += adj["unknown"]
                    row[cell] = {"total": total, **adj}
                per_query_rows.append(row)

        report["categories"][category] = {
            "n_queries": len(queries),
            "n_resolved": resolved_count,
            "cells": {
                c: {
                    "retrieved_total": agg[c].retrieved_total,
                    "queries_with_hits": agg[c].queries_with_hits,
                    "adjudicated": agg[c].adj_records,
                    "true": agg[c].true,
                    "false": agg[c].false,
                    "unknown": agg[c].unknown,
                    "precision": agg[c].precision(),
                }
                for c in cells
            },
        }

    report["generated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ablation_report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "per_query.jsonl").write_text(
        "\n".join(json.dumps(r) for r in per_query_rows) + "\n"
    )
    _write_markdown(report, out_dir / "ablation_report.md")
    print(f"\nwrote {out_dir}/ablation_report.{{json,md}} + per_query.jsonl", file=sys.stderr)
    _print_summary(report)
    return 0


def _write_markdown(report: dict, path: Path) -> None:
    lines = ["# Harmonization Ablation Benchmark", ""]
    lines.append(f"Generated: {report['generated_utc']}  ·  limit/cell: {report['limit']}")
    lines.append("")
    lines.append("Cells: **raw_source** (q= on source) · **harm_source** (resolve→native "
                 "field) · **raw_dest** (q= on harmonized index) · **harm_dest** "
                 "(resolve→subjects.valueUri). Precision = true/(true+false) by the "
                 "dictionary-oracle adjudication; UNKNOWN excluded.")
    lines.append("")
    for cat, c in report["categories"].items():
        lines.append(f"## {cat}  ({c['n_resolved']}/{c['n_queries']} queries resolved)")
        lines.append("")
        lines.append("| cell | retrieved | true | false | unknown | precision |")
        lines.append("|---|---|---|---|---|---|")
        for cell, d in c["cells"].items():
            prec = "—" if d["precision"] is None else f"{d['precision']:.1%}"
            lines.append(
                f"| {cell} | {d['retrieved_total']:,} | {d['true']:,} | "
                f"{d['false']:,} | {d['unknown']:,} | {prec} |"
            )
        lines.append("")
    path.write_text("\n".join(lines))


def _print_summary(report: dict) -> None:
    print("\n=== SUMMARY ===")
    for cat, c in report["categories"].items():
        print(f"\n{cat} ({c['n_resolved']}/{c['n_queries']} resolved):")
        for cell, d in c["cells"].items():
            prec = "n/a" if d["precision"] is None else f"{d['precision']:.1%}"
            print(
                f"  {cell:12s} retrieved={d['retrieved_total']:>8,} "
                f"true={d['true']:>5} false={d['false']:>5} unknown={d['unknown']:>5} "
                f"precision={prec}"
            )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--categories",
        default="mu_virus,abbreviations,real_world",
        help="comma-separated: mu_virus,abbreviations,real_world",
    )
    ap.add_argument("--limit", type=int, default=200, help="records fetched per cell for adjudication")
    ap.add_argument("--max-queries", type=int, default=None, help="cap queries/category (smoke runs)")
    ap.add_argument("--indices", default=None, help="comma-separated source short names (default: all 9)")
    ap.add_argument("--out", default=str(_HERE / "output"))
    ap.add_argument(
        "--expand-descendants",
        action="store_true",
        help="harm_dest filters subjects.valueUri on the query taxon's full "
        "descendant subtree (improvement experiment for broad queries).",
    )
    ap.add_argument(
        "--queries",
        default=None,
        help="comma-separated explicit query subset (targeted re-runs; avoids "
        "re-running unchanged queries).",
    )
    ap.add_argument(
        "--use-aliases",
        action="store_true",
        help="apply benchmarks/queries/curated_aliases.tsv before resolution "
        "(acronyms + dead-end redirects experiment).",
    )
    ap.add_argument(
        "--enable-ner",
        action="store_true",
        help="on a resolution miss, extract entities via the apecx_db_integration "
        "LLM NER (direct import or sibling-venv subprocess) and resolve those. "
        "No-op if the package + an LLM backend are unavailable.",
    )
    args = ap.parse_args(argv)

    global _USE_ALIASES, _USE_NER
    _USE_ALIASES = args.use_aliases
    _USE_NER = args.enable_ner

    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    idx = [i.strip() for i in args.indices.split(",")] if args.indices else None
    only = {q.strip() for q in args.queries.split(",")} if args.queries else None
    return run(
        cats, args.limit, args.max_queries, idx, Path(args.out),
        expand_descendants=args.expand_descendants, only_queries=only,
    )


if __name__ == "__main__":
    sys.exit(main())
