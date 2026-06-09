# /// script
# dependencies = [
#   "globus-sdk>=4.0",
# ]
# ///
"""Resolve a user term via the synonym dictionary, then run a harmonized
Globus Search query against one or more APECx source indices.

Pipeline:
  1. Call ``apecx-lookup`` (or python module fallback) to resolve the
     user term to one (fast / fuzzy) or more (ambiguous) canonical IRIs.
  2. Construct a Globus Search query filtering on
     ``subjects.valueUri`` for the resolved IRI(s).
  3. POST to the index(es) and emit results as JSONL with the
     resolution metadata preserved per record.

Two flavors of output:
  * **Default** (JSONL) — one harmonized record per line.
  * **``--compare``** (JSON envelope) — runs BOTH the raw substring
    query (``q=<term>``) and the harmonized IRI-filter query, then
    surfaces divergence. When raw and harmonized disagree by more
    than ``--divergence-threshold`` records (default 5) or fraction
    (default 5%%), the envelope sets ``hitl_required: true`` so the
    caller can route to a human-in-the-loop disambiguation step
    instead of silently presenting the broader (or narrower) set.

Usage:
  python harmonized_query.py --term "EEEV" \\
      --index b676edbe-3286-4514-bc13-5cbe891c4bb1 --limit 200

  python harmonized_query.py --term "Rift Valley fever virus" \\
      --all-indices --limit 100

  python harmonized_query.py --term "RSV" --resolve-only

  python harmonized_query.py --term "EEEV" --compare \\
      --index bvbrc_genome --limit 500
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any

import globus_sdk

# The nine APECx source indices, keyed by short name. Update this map
# alongside SOURCE_REGISTRY in apecx-harvesters' harmonize.py.
INDICES: dict[str, str] = {
    "violin_pathogen": "a67c7310-5115-446f-bfb6-d889bc4efa06",
    "violin_vaccine":  "c5ff64fd-5e78-4cf0-848a-2788a78e71cd",
    "violin_gene":     "205c1a5b-c9bd-4137-8ac6-ca879c9a4f9c",
    "bvbrc_genome":            "b676edbe-3286-4514-bc13-5cbe891c4bb1",
    "bvbrc_protein":           "249efe96-14d2-443d-ad47-5621ed43a343",
    "bvbrc_protein_structure": "439f2b66-09d4-4141-8c3d-b4dc18ef8a07",
    "bvbrc_epitope":           "f873c7d5-8652-466d-806b-b5da46f0f786",
    "antiviraldb": "e8097a7b-a280-4031-9df1-1e837193494f",
    "protabank":   "9e902471-9c77-49d3-a12c-516cc0808c3b",
}


def _resolve_term(term: str) -> dict[str, Any]:
    """Call ``apecx-lookup --json`` to resolve the term.

    Returns the LookupResult JSON the CLI emits. Falls back to a
    direct python import when the CLI isn't on PATH.
    """
    cli = shutil.which("apecx-lookup")
    if cli is not None:
        proc = subprocess.run(
            [cli, term, "--json"],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode in (0, 1):  # 1 = miss, still well-formed
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError:
                pass
        print(
            f"apecx-lookup failed (exit={proc.returncode}); falling back to module",
            file=sys.stderr,
        )
    # Python module fallback — uses apecx_harvesters.dict_reader (the
    # thin reader, no nanobrain/LLM deps). On missing local dict, the
    # caller should run `apecx-dict-update` to bootstrap from the
    # published Globus path. We do NOT auto-bootstrap from inside this
    # script: a silent multi-megabyte download during query is hostile UX.
    try:
        from apecx_harvesters.dict_reader import (
            configure_dictionary_path,
            default_dictionary_path,
            lookup_entity,
        )
        dict_path = default_dictionary_path()
        if not dict_path.exists():
            print(
                f"dictionary not found at {dict_path}; run "
                f"`apecx-dict-update` to bootstrap from the published "
                f"Globus path.",
                file=sys.stderr,
            )
            return {
                "surface_form": term,
                "resolution_path": "miss",
                "canonical_iri": None,
                "resolution_status": "unresolved",
                "candidates": [],
                "evidence": "local dictionary absent; run apecx-dict-update",
            }
        configure_dictionary_path(dict_path)
        result = lookup_entity(term)
        return {
            "surface_form": result.surface_form,
            "resolution_path": result.path,
            "canonical_iri": result.canonical_iri,
            "canonical_label": result.canonical_label,
            "canonical_ontology": result.canonical_ontology,
            "confidence": result.confidence,
            "resolution_status": result.resolution_status.value,
            "synonyms": list(result.synonyms),
            "evidence": result.evidence,
            "candidates": [
                {
                    "canonical_iri": c.canonical_iri,
                    "canonical_label": c.canonical_label,
                    "canonical_ontology": c.canonical_ontology,
                    "confidence": c.confidence,
                }
                for c in result.candidates
            ],
        }
    except Exception as exc:
        print(
            f"resolution failed: {exc}; emitting miss",
            file=sys.stderr,
        )
        return {
            "surface_form": term,
            "resolution_path": "miss",
            "canonical_iri": None,
            "resolution_status": "unresolved",
            "candidates": [],
        }


def _iris_for_query(resolution: dict[str, Any]) -> list[str]:
    """Pick which IRIs to filter on (when the index carries them).

    For unambiguous paths use the single canonical IRI; for
    ``ambiguous`` use the full candidate list. Empty list when the
    resolution missed.
    """
    path = resolution.get("resolution_path") or resolution.get("path")
    if path == "ambiguous":
        return [
            c["canonical_iri"]
            for c in resolution.get("candidates", [])
            if c.get("canonical_iri")
        ]
    iri = resolution.get("canonical_iri")
    return [iri] if iri else []


def _labels_for_query(resolution: dict[str, Any]) -> list[str]:
    """Pick which canonical labels (+ synonyms) to filter on.

    Used by indices that lack ``subjects.valueUri`` and instead carry a
    species-name field (``Species`` for BVBRC_genome, ``Organism`` for
    BVBRC_epitope, etc.). The set includes the canonical_label PLUS the
    full ``synonyms`` list from the dictionary, so a record using an
    ICTV-renamed name (e.g., ``Orthoflavivirus encephalitidis`` for
    TBEV) still matches even when the dict's canonical_label is the
    legacy form. For ``ambiguous`` resolutions every candidate's
    canonical_label is included.
    """
    out: list[str] = []
    path = resolution.get("resolution_path") or resolution.get("path")
    if path == "ambiguous":
        for c in resolution.get("candidates", []):
            if c.get("canonical_label"):
                out.append(c["canonical_label"])
        return out
    label = resolution.get("canonical_label")
    if label:
        out.append(label)
    for syn in resolution.get("synonyms", []) or []:
        if isinstance(syn, str) and syn and syn not in out:
            out.append(syn)
    return out


# Per-index harmonization filter map. Each entry says:
#  - field: the index field to filter on
#  - shape: 'iri' (filter by canonical IRI list — production target after
#    SC-D ingest ships) OR 'label' (filter by canonical_label list — what
#    works against current production indices) OR 'taxon_id' (filter by
#    integer NCBI taxon — VIOLIN Pathogen's column).
#
# IMPORTANT (2026-06-08): the SKILL.md design originally targeted
# ``subjects.valueUri`` as the universal harmonized filter, but the
# current production Globus indices have NOT been re-ingested with the
# harmonization layer yet. Until that ships, this map points each index
# at the field that DOES exist today. When SC-D ingest lands, flip every
# row's shape to 'iri' + field to 'subjects.valueUri' and the harmonized
# query becomes uniform again.
HARMONIZED_FILTER: dict[str, dict[str, str]] = {
    "violin_pathogen":        {"field": "NCBI_Taxonomy_ID", "shape": "taxon_id"},
    "violin_vaccine":         {"field": "VIOLIN_c_pathogen_id", "shape": "taxon_id"},
    "violin_gene":            {"field": "Organism", "shape": "label"},
    "bvbrc_genome":           {"field": "Species", "shape": "label"},
    "bvbrc_protein":          {"field": "Genome", "shape": "label"},
    "bvbrc_protein_structure": {"field": "Organism_Name", "shape": "label"},
    "bvbrc_epitope":          {"field": "Organism", "shape": "label"},
    "antiviraldb":            {"field": "Virus", "shape": "label"},
    "protabank":              {"field": "Title", "shape": "label"},
}


def _taxon_id_from_resolution(resolution: dict[str, Any]) -> list[int]:
    """Extract integer NCBI taxon IDs from the canonical IRI(s).

    The NCBI Taxonomy IRI shape is
    ``http://purl.obolibrary.org/obo/NCBITaxon_<INT>``; we parse the
    suffix back to an int for indices that store the taxon as a number.
    """
    iris = _iris_for_query(resolution)
    taxa: list[int] = []
    for iri in iris:
        suffix = iri.rsplit("/", 1)[-1].split("_", 1)[-1]
        try:
            taxa.append(int(suffix))
        except (TypeError, ValueError):
            continue
    return taxa


def _build_query_for_index(
    short: str, resolution: dict[str, Any], limit: int
) -> dict[str, Any] | None:
    """Construct the harmonized Globus payload for one index.

    Returns ``None`` when the resolution doesn't yield any value that
    fits the index's filter shape (e.g., resolution missed and we have
    no label/IRI/taxon to filter on).
    """
    spec = HARMONIZED_FILTER.get(short)
    if spec is None:
        return None
    shape = spec["shape"]
    if shape == "iri":
        values: list[Any] = _iris_for_query(resolution)
    elif shape == "label":
        values = _labels_for_query(resolution)
    elif shape == "taxon_id":
        values = _taxon_id_from_resolution(resolution)
    else:
        return None
    if not values:
        return None
    return {
        "filters": [
            {
                "type": "match_any",
                "field_name": spec["field"],
                "values": values,
            }
        ],
        "limit": limit,
    }


def _build_query(iris: list[str], limit: int) -> dict[str, Any]:
    """Legacy IRI-based query builder. Retained for callers that target
    indices already carrying ``subjects.valueUri``. Most production callers
    now go through ``_build_query_for_index`` instead.
    """
    return {
        "filters": [
            {
                "type": "match_any",
                "field_name": "subjects.valueUri",
                "values": iris,
            }
        ],
        "limit": limit,
    }


def _query_one(
    client: globus_sdk.SearchClient,
    index_id: str,
    payload: dict[str, Any],
    *,
    short_name: str,
    resolution: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    """Run one POST and unwrap each gmeta entry. Returns (total, records)."""
    try:
        response = client.post_search(index_id, payload)
    except globus_sdk.SearchAPIError as e:
        print(
            f"[{short_name}] Globus API error ({e.http_status}): {e.message}",
            file=sys.stderr,
        )
        return 0, []
    except globus_sdk.GlobusAPIError as e:
        print(f"[{short_name}] API error ({e.http_status}): {e.message}", file=sys.stderr)
        return 0, []
    except globus_sdk.NetworkError as e:
        print(f"[{short_name}] network error: {e}", file=sys.stderr)
        return 0, []
    data = response.data
    total = int(data.get("total", 0))
    out: list[dict[str, Any]] = []
    for gmeta in data.get("gmeta", []):
        entries = gmeta.get("entries", [])
        if not entries:
            continue
        content = entries[0].get("content", {})
        # Attach the resolution metadata so the consumer can group/filter
        # without re-running the lookup.
        content["_harmonization"] = {
            "queried_index": short_name,
            "resolution_path": resolution.get("resolution_path"),
            "queried_iris": _iris_for_query(resolution),
            "resolution_label": resolution.get("canonical_label"),
        }
        out.append(content)
    return total, out


def _query_raw_one(
    client: globus_sdk.SearchClient,
    index_id: str,
    term: str,
    limit: int,
    *,
    short_name: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Run a Globus full-text query ``q=<term>`` — the raw substring path.

    Multi-word terms are wrapped in double quotes so Globus treats them
    as a phrase match instead of OR-tokenizing each word — without the
    quotes, ``q="Sindbis virus"`` matches every BVBRC record containing
    the word "virus", returning hundreds of thousands of false positives.

    Returned records carry the source's original surface fields with no
    harmonization metadata attached. Used by ``--compare`` to measure how
    raw substring search diverges from the harmonized filter.
    """
    q_term = term
    # Globus full-text tokenizes on whitespace AND non-alphanumerics —
    # ``q=HSV-2`` becomes ``HSV OR 2``, inflating to 138k records in BVBRC.
    # Quote whenever the term carries any non-alphanumeric character so
    # Globus performs a phrase match against the source surface fields.
    needs_quote = any(not c.isalnum() for c in term) and not (
        term.startswith('"') and term.endswith('"')
    )
    if needs_quote:
        q_term = f'"{term}"'
    payload = {"q": q_term, "limit": limit}
    try:
        response = client.post_search(index_id, payload)
    except (globus_sdk.SearchAPIError, globus_sdk.GlobusAPIError) as e:
        print(
            f"[{short_name}] raw API error ({e.http_status}): {e.message}",
            file=sys.stderr,
        )
        return 0, []
    except globus_sdk.NetworkError as e:
        print(f"[{short_name}] raw network error: {e}", file=sys.stderr)
        return 0, []
    data = response.data
    total = int(data.get("total", 0))
    records: list[dict[str, Any]] = []
    for gmeta in data.get("gmeta", []):
        entries = gmeta.get("entries", [])
        if not entries:
            continue
        records.append(entries[0].get("content", {}))
    return total, records


def _record_subject(record: dict[str, Any]) -> str:
    """Pick a stable per-record identifier for set arithmetic.

    Prefers DataCite ``identifier.identifier`` then source-specific
    identity fields. Falls back to the title string when nothing else
    is present (worst case: equal titles dedupe, which matches user
    intent for "show me unique records").
    """
    ident = record.get("identifier") or {}
    if isinstance(ident, dict) and ident.get("identifier"):
        return str(ident["identifier"])
    # source-specific identity fallbacks
    for key in (
        "bvbrc_genome",
        "bvbrc_protein",
        "bvbrc_epitope",
        "bvbrc_protein_structure",
        "violin_pathogen",
        "violin_vaccine",
        "violin_gene",
        "antiviraldb",
        "protabank",
    ):
        sub = record.get(key)
        if isinstance(sub, dict):
            for field in ("Genome_ID", "Genome_Name", "PDB_ID",
                          "VIOLIN_c_pathogen_id", "Vaccine_Name",
                          "Gene_Name", "Epitope_ID", "Title", "Pathogen"):
                if sub.get(field):
                    return f"{key}:{sub[field]}"
    titles = record.get("titles") or []
    if titles and isinstance(titles, list):
        first = titles[0]
        if isinstance(first, dict) and first.get("title"):
            return f"title:{first['title']}"
    return f"anon:{id(record)}"


def _build_hitl_envelope(
    term: str,
    resolution: dict[str, Any],
    per_index_results: dict[str, dict[str, Any]],
    *,
    record_threshold: int,
    fraction_threshold: float,
    sample_size: int,
) -> dict[str, Any]:
    """Compose the structured comparison + HITL envelope.

    HITL is required when ANY of these hold for ANY queried index:
      * Resolution path is ``ambiguous`` (multiple candidate taxa).
      * ``raw_only`` count + ``harmonized_only`` count >= record_threshold.
      * Symmetric divergence fraction >= fraction_threshold of the
        larger of the two totals.

    Each index in ``per_index_results`` carries:
      raw_total, harmonized_total, raw_records, harmonized_records.
    """
    per_index: dict[str, dict[str, Any]] = {}
    hitl_required = resolution.get("resolution_path") == "ambiguous"
    hitl_reasons: list[str] = []
    if hitl_required:
        hitl_reasons.append(
            f"ambiguous resolution: {len(resolution.get('candidates', []))} candidate taxa"
        )

    for short, payload in per_index_results.items():
        raw_records: list[dict[str, Any]] = payload["raw_records"]
        harm_records: list[dict[str, Any]] = payload["harmonized_records"]
        raw_set = {_record_subject(r): r for r in raw_records}
        harm_set = {_record_subject(r): r for r in harm_records}
        overlap_keys = raw_set.keys() & harm_set.keys()
        raw_only_keys = raw_set.keys() - harm_set.keys()
        harm_only_keys = harm_set.keys() - raw_set.keys()
        raw_total = payload["raw_total"]
        harm_total = payload["harmonized_total"]
        # Compare TOTALS (index-side) for the divergence floor — comparing
        # the returned-sample-set against itself overstates divergence
        # whenever the limit truncates. Symmetric difference uses the
        # smaller of the two totals as the share-of-total denominator;
        # a 5% difference of a tiny set is noisier than 5% of a big one.
        absolute_diff = abs(raw_total - harm_total)
        larger_total = max(raw_total, harm_total) or 1
        divergence_fraction = absolute_diff / larger_total
        diverges = (
            absolute_diff >= record_threshold
            or divergence_fraction >= fraction_threshold
        )
        if diverges:
            hitl_required = True
            hitl_reasons.append(
                f"{short}: raw_total={raw_total} vs harmonized_total={harm_total} "
                f"(|Δ|={absolute_diff}, {divergence_fraction:.0%} of {larger_total})"
            )
        per_index[short] = {
            "raw_total": payload["raw_total"],
            "harmonized_total": payload["harmonized_total"],
            "raw_returned": len(raw_records),
            "harmonized_returned": len(harm_records),
            "overlap_records": len(overlap_keys),
            "raw_only_records": len(raw_only_keys),
            "harmonized_only_records": len(harm_only_keys),
            "divergence_fraction": round(divergence_fraction, 4),
            "raw_only_samples": [
                raw_set[k] for k in list(raw_only_keys)[:sample_size]
            ],
            "harmonized_only_samples": [
                harm_set[k] for k in list(harm_only_keys)[:sample_size]
            ],
        }

    hitl_prompt = None
    if hitl_required:
        hitl_prompt = (
            f"Search term {term!r} returns DIFFERENT results under raw "
            f"substring vs harmonized IRI-filter modes. "
            f"Reasons: {'; '.join(hitl_reasons)}. "
            f"Choose: (a) the harmonized superset (every record sharing "
            f"the canonical taxon), (b) the raw substring set (literal "
            f"text mentions only), or (c) intersection only (records "
            f"both modes agree on)."
        )

    return {
        "term": term,
        "resolution": resolution,
        "per_index": per_index,
        "hitl_required": hitl_required,
        "hitl_reasons": hitl_reasons,
        "hitl_prompt": hitl_prompt,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--term", required=True, help="User search term to resolve")
    parser.add_argument(
        "--index",
        action="append",
        default=[],
        help="Globus index UUID or short name (violin_pathogen / bvbrc_genome / ...). Repeatable.",
    )
    parser.add_argument(
        "--all-indices",
        action="store_true",
        help="Fan out across every index in the INDICES map.",
    )
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="Print the resolution JSON and exit (no Globus query).",
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        help="Output JSONL file (default: stdout)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Run BOTH raw substring (q=<term>) and harmonized IRI-filter "
             "queries; emit a structured comparison + HITL prompt when they "
             "diverge. Output is a single JSON envelope (NOT JSONL).",
    )
    parser.add_argument(
        "--divergence-records",
        type=int, default=5,
        help="In --compare mode, HITL is required when raw_only + "
             "harmonized_only meets or exceeds this count (default: 5).",
    )
    parser.add_argument(
        "--divergence-fraction",
        type=float, default=0.05,
        help="In --compare mode, HITL is required when the symmetric "
             "divergence fraction meets or exceeds this value (default: 0.05).",
    )
    parser.add_argument(
        "--sample-size",
        type=int, default=3,
        help="In --compare mode, number of raw-only / harmonized-only "
             "sample records to include in the envelope (default: 3).",
    )
    args = parser.parse_args()

    resolution = _resolve_term(args.term)
    if args.resolve_only:
        json.dump(resolution, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    iris = _iris_for_query(resolution)
    labels = _labels_for_query(resolution)
    path = resolution.get("resolution_path") or resolution.get("path")
    if not iris and not labels:
        print(
            f"resolution path = {path!r}; "
            f"no IRI or label to query — resolution missed. consider falling "
            f"back to raw text search.",
            file=sys.stderr,
        )
        sys.stdout.write(json.dumps({"_harmonization": resolution}) + "\n")
        return 1

    print(
        f"resolved {args.term!r} → {len(iris)} IRI(s) / {len(labels)} label(s) "
        f"via path={path}",
        file=sys.stderr,
    )

    # Pick indices: explicit --index entries OR --all-indices OR error.
    target_short_names: list[str] = []
    if args.all_indices:
        target_short_names = list(INDICES)
    else:
        for idx in args.index:
            if idx in INDICES:
                target_short_names.append(idx)
            elif len(idx) == 36 and idx.count("-") == 4:
                # Looks like a raw UUID; pass through.
                target_short_names.append(idx)
            else:
                print(
                    f"unknown index {idx!r}; valid: {', '.join(INDICES)}",
                    file=sys.stderr,
                )
                return 2
    if not target_short_names:
        print("--index or --all-indices required", file=sys.stderr)
        return 2

    client = globus_sdk.SearchClient()

    if args.compare:
        per_index_results: dict[str, dict[str, Any]] = {}
        for short in target_short_names:
            index_id = INDICES.get(short, short)
            raw_total, raw_records = _query_raw_one(
                client, index_id, args.term, args.limit, short_name=short,
            )
            payload = _build_query_for_index(short, resolution, args.limit)
            if payload is None:
                harm_total, harm_records = 0, []
                print(
                    f"[{short:25s}] no harmonized filter for index — "
                    f"skipping harmonized query",
                    file=sys.stderr,
                )
            else:
                harm_total, harm_records = _query_one(
                    client, index_id, payload,
                    short_name=short, resolution=resolution,
                )
            per_index_results[short] = {
                "raw_total": raw_total,
                "harmonized_total": harm_total,
                "raw_records": raw_records,
                "harmonized_records": harm_records,
            }
            print(
                f"[{short:25s}] raw_total={raw_total:>6d} "
                f"harm_total={harm_total:>6d}",
                file=sys.stderr,
            )
        envelope = _build_hitl_envelope(
            args.term, resolution, per_index_results,
            record_threshold=args.divergence_records,
            fraction_threshold=args.divergence_fraction,
            sample_size=args.sample_size,
        )
        out_fp = open(args.output, "w") if args.output else sys.stdout
        try:
            json.dump(envelope, out_fp, indent=2)
            out_fp.write("\n")
        finally:
            if args.output:
                out_fp.close()
        return 0

    out = open(args.output, "w") if args.output else sys.stdout
    try:
        for short in target_short_names:
            index_id = INDICES.get(short, short)
            payload = _build_query_for_index(short, resolution, args.limit)
            if payload is None:
                print(
                    f"[{short:25s}] no harmonized filter; skipping",
                    file=sys.stderr,
                )
                continue
            total, records = _query_one(
                client, index_id, payload,
                short_name=short, resolution=resolution,
            )
            print(
                f"[{short:25s}] total={total:>6d} returned={len(records):>4d}",
                file=sys.stderr,
            )
            for r in records:
                out.write(json.dumps(r) + "\n")
    finally:
        if args.output:
            out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
