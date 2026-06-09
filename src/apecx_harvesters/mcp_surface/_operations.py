"""Runnable operations the ``run`` primitive dispatches to.

Each entry in :data:`OPERATIONS` is a callable that takes a params dict
and returns a :class:`WorkflowResult`. The ``run`` MCP tool itself is
generic — operation-specific richness lives here.

Current catalog:

- ``resolve`` — surface-form → canonical IRI (replaces the ex-tool
  ``resolve_term`` and the IRI shortcut from ``describe_canonical_iri``).
- ``harmonized_search`` — live Globus dual-query with HITL envelope.
- ``update_dictionary`` — bootstrap or refresh the local dict.

Adding a new operation: write a function returning ``WorkflowResult``
and register it in :data:`OPERATIONS`. The MCP surface stays the same
size; the catalog grows.
"""

from __future__ import annotations

from typing import Any, Callable

import globus_sdk

from apecx_harvesters.dict_reader import (
    EntityType,
    configure_dictionary_path,
    default_dictionary_path,
    get_dictionary_index,
    lookup_entity,
)
from apecx_harvesters.dict_reader.bootstrap import (
    bootstrap_dictionary,
    current_local_version,
)
from apecx_harvesters.dict_reader.normalization import normalize_surface_form
from apecx_harvesters.mcp_surface._envelope import WorkflowResult
from apecx_harvesters.mcp_surface._session import session
from apecx_harvesters.mcp_surface._visibility import (
    ProcessingTrace,
    synonyms_substitution_summary,
)


# Per-Globus-index harmonized filter map (same as the agent-skill's
# harmonized_query.py HARMONIZED_FILTER).
_HARMONIZED_FILTER: dict[str, dict[str, str]] = {
    "violin_pathogen":         {"field": "NCBI_Taxonomy_ID", "shape": "taxon_id"},
    "violin_vaccine":          {"field": "VIOLIN_c_pathogen_id", "shape": "taxon_id"},
    "violin_gene":             {"field": "Organism", "shape": "label"},
    "bvbrc_genome":            {"field": "Species", "shape": "label"},
    "bvbrc_protein":           {"field": "Genome", "shape": "label"},
    "bvbrc_protein_structure": {"field": "Organism_Name", "shape": "label"},
    "bvbrc_epitope":           {"field": "Organism", "shape": "label"},
    "antiviraldb":             {"field": "Virus", "shape": "label"},
    "protabank":               {"field": "Title", "shape": "label"},
}

_INDEX_UUIDS: dict[str, str] = {
    "violin_pathogen":         "a67c7310-5115-446f-bfb6-d889bc4efa06",
    "violin_vaccine":          "c5ff64fd-5e78-4cf0-848a-2788a78e71cd",
    "violin_gene":             "205c1a5b-c9bd-4137-8ac6-ca879c9a4f9c",
    "bvbrc_genome":            "b676edbe-3286-4514-bc13-5cbe891c4bb1",
    "bvbrc_protein":           "249efe96-14d2-443d-ad47-5621ed43a343",
    "bvbrc_protein_structure": "439f2b66-09d4-4141-8c3d-b4dc18ef8a07",
    "bvbrc_epitope":           "f873c7d5-8652-466d-806b-b5da46f0f786",
    "antiviraldb":             "e8097a7b-a280-4031-9df1-1e837193494f",
    "protabank":               "9e902471-9c77-49d3-a12c-516cc0808c3b",
}


def _ensure_dict() -> tuple[bool, str | None]:
    path = default_dictionary_path()
    if not path.exists():
        return False, (
            f"Local dictionary not found at {path}. Run "
            f"run(operation='update_dictionary')."
        )
    configure_dictionary_path(path)
    return True, None


def _iris_to_taxon_ids(iris: list[str]) -> list[int]:
    out: list[int] = []
    for iri in iris:
        suffix = iri.rsplit("/", 1)[-1].split("_", 1)[-1]
        try:
            out.append(int(suffix))
        except (TypeError, ValueError):
            continue
    return out


def _quote_raw(term: str) -> tuple[str, bool]:
    needs = any(not c.isalnum() for c in term)
    if needs and not (term.startswith('"') and term.endswith('"')):
        return f'"{term}"', True
    return term, False


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def _op_resolve(params: dict[str, Any]) -> WorkflowResult:
    term = (params.get("term") or "").strip()
    entity_type_str = params.get("entity_type") or None
    run_id = session().new_run_id()
    trace = ProcessingTrace()
    if not term:
        return WorkflowResult(
            markdown="**Error**: `term` parameter is required.",
            run_id=run_id, status="error",
            error="missing required parameter `term`",
            processing_steps=trace.as_list(),
        )
    trace.add("input", f"term={term!r}", term=term, entity_type=entity_type_str)

    normalized = normalize_surface_form(term)
    trace.add("normalize", f"normalized={normalized!r}",
              normalized_form=normalized)

    ok, err = _ensure_dict()
    if not ok:
        return WorkflowResult(
            markdown=f"**Dictionary unavailable**: {err}",
            run_id=run_id, status="error", error=err,
            processing_steps=trace.as_list(),
            next_actions=[{
                "tool": "run", "params": {"operation": "update_dictionary"},
                "purpose": "bootstrap the local dict from the published path",
            }],
        )
    index, _ = get_dictionary_index()
    if index is None:
        return WorkflowResult(
            markdown="**Dictionary failed to load**",
            run_id=run_id, status="error", error="dictionary load error",
            processing_steps=trace.as_list(),
        )
    trace.add(
        "dict_loaded",
        f"loaded dict v{index.manifest.dictionary_version} "
        f"({index.entry_count():,} entries)",
        dictionary_version=index.manifest.dictionary_version,
    )

    et = EntityType(entity_type_str) if entity_type_str else None
    result = lookup_entity(term, entity_type=et)
    trace.add(
        "resolve",
        f"path={result.path!r} iri={result.canonical_iri!r} "
        f"label={result.canonical_label!r}",
        resolution_path=result.path,
        confidence=result.confidence,
    )

    sub = synonyms_substitution_summary(
        surface_form=term,
        canonical_iri=result.canonical_iri,
        synonyms=list(result.synonyms),
    )
    candidates = [
        {
            "canonical_iri": c.canonical_iri,
            "canonical_label": c.canonical_label,
            "canonical_ontology": c.canonical_ontology,
            "confidence": c.confidence,
        }
        for c in result.candidates
    ]
    hitl_required = result.path == "ambiguous"
    md_lines = [
        f"### Resolution of `{term}`",
        f"- **Path**: `{result.path}` (confidence {result.confidence})",
        f"- **Canonical IRI**: `{result.canonical_iri}`"
            if result.canonical_iri else "- **Canonical IRI**: (none — see candidates)",
        f"- **Label**: {result.canonical_label!r}"
            if result.canonical_label else "- **Label**: (none)",
        f"- **Synonyms recorded for this canonical entry**: "
        f"{sub['synonyms_count']} surface form(s)",
    ]
    if hitl_required:
        md_lines += [
            "",
            f"**HITL required** — `{term}` matches {len(candidates)} candidates. "
            f"Present them and re-call with the chosen canonical IRI:",
            "",
        ]
        for c in candidates:
            md_lines.append(
                f"  - `{c['canonical_iri']}` — "
                f"{c['canonical_label']!r}"
            )

    data_preview = {
        "result": {
            "surface_form": result.surface_form,
            "resolution_path": result.path,
            "canonical_iri": result.canonical_iri,
            "canonical_label": result.canonical_label,
            "canonical_ontology": result.canonical_ontology,
            "confidence": result.confidence,
            "resolution_status": result.resolution_status.value,
            "evidence": result.evidence,
        },
        "synonyms_substitution": sub,
        "candidates": candidates,
    }

    next_actions: list[dict[str, Any]] = []
    if hitl_required:
        next_actions.append({
            "tool": "inspect",
            "params": {
                "object_type": "canonical_iri",
                "id": "<one of the candidate canonical_iri values>",
            },
            "purpose": "inspect a candidate's full synonym set before user picks",
        })
        next_actions.append({
            "tool": "run",
            "params": {
                "operation": "resolve",
                "params": {"term": "<chosen canonical_iri>"},
            },
            "purpose": "commit the user's choice (IRI input → path=fast)",
        })
    elif result.path == "miss":
        next_actions.append({
            "tool": "run",
            "params": {
                "operation": "harmonized_search",
                "params": {"term": term, "index": "bvbrc_genome"},
            },
            "purpose": "fall back to raw substring across Globus indices",
        })

    envelope = WorkflowResult(
        markdown="\n".join(md_lines),
        run_id=run_id,
        status="hitl_required" if hitl_required else "ok",
        data_handle=run_id,
        data_preview=data_preview,
        hitl_required=hitl_required,
        hitl_prompt=(
            f"Term {term!r} resolved to {len(candidates)} candidates; "
            f"present them and re-call run() with the chosen IRI."
        ) if hitl_required else None,
        hitl_candidates=candidates,
        next_actions=next_actions,
        processing_steps=trace.as_list(),
    )
    session().record(
        run_id=run_id, operation="resolve", params=params,
        envelope=envelope.to_dict(), data_payload=data_preview,
    )
    return envelope


# ---------------------------------------------------------------------------
# harmonized_search
# ---------------------------------------------------------------------------


def _op_harmonized_search(params: dict[str, Any]) -> WorkflowResult:
    term = (params.get("term") or "").strip()
    index = params.get("index") or "bvbrc_genome"
    limit = int(params.get("limit") or 200)
    sample_size = int(params.get("sample_size") or 3)
    div_records = int(params.get("divergence_records_threshold") or 5)
    div_fraction = float(params.get("divergence_fraction_threshold") or 0.05)

    run_id = session().new_run_id()
    trace = ProcessingTrace()

    if not term:
        return WorkflowResult(
            markdown="**Error**: `term` is required.",
            run_id=run_id, status="error", error="missing `term`",
            processing_steps=trace.as_list(),
        )
    if index not in _INDEX_UUIDS:
        return WorkflowResult(
            markdown=f"**Error**: unknown index {index!r}; "
                     f"see `discover(category='index')`.",
            run_id=run_id, status="error",
            error=f"unknown index {index!r}",
            processing_steps=trace.as_list(),
        )
    trace.add("input", f"term={term!r} index={index!r} limit={limit}",
              term=term, index=index, limit=limit)
    index_uuid = _INDEX_UUIDS[index]
    spec = _HARMONIZED_FILTER[index]
    trace.add(
        "index_resolve",
        f"index uuid={index_uuid}; harmonized filter "
        f"field={spec['field']!r} shape={spec['shape']!r}",
        index_uuid=index_uuid, filter_field=spec["field"],
        filter_shape=spec["shape"],
    )

    ok, err = _ensure_dict()
    if not ok:
        return WorkflowResult(
            markdown=f"**Dictionary unavailable**: {err}",
            run_id=run_id, status="error", error=err,
            processing_steps=trace.as_list(),
        )
    result = lookup_entity(term)
    trace.add(
        "dict_resolve",
        f"path={result.path!r} iri={result.canonical_iri!r} "
        f"candidates={len(result.candidates)}",
        resolution_path=result.path,
        candidates_count=len(result.candidates),
    )

    iris: list[str] = []
    labels: list[str] = []
    if result.path == "ambiguous":
        iris = [c.canonical_iri for c in result.candidates if c.canonical_iri]
        labels = [c.canonical_label for c in result.candidates if c.canonical_label]
    else:
        if result.canonical_iri:
            iris = [result.canonical_iri]
        if result.canonical_label:
            labels = [result.canonical_label] + [
                s for s in result.synonyms if s and s != result.canonical_label
            ]
    if spec["shape"] == "label":
        filter_values: list[Any] = labels
    elif spec["shape"] == "taxon_id":
        filter_values = _iris_to_taxon_ids(iris)
    else:
        filter_values = iris
    trace.add(
        "filter_build",
        f"built {len(filter_values)} filter value(s)",
        filter_values_count=len(filter_values),
        filter_values_sample=list(filter_values)[:5],
    )

    raw_q, was_quoted = _quote_raw(term)
    trace.add("raw_q_build", f"q={raw_q!r} was_quoted={was_quoted}",
              raw_q=raw_q, was_quoted=was_quoted)

    client = globus_sdk.SearchClient()
    try:
        raw_resp = client.post_search(index_uuid, {"q": raw_q, "limit": limit})
        raw_total = int(raw_resp.data.get("total", 0))
        raw_records = [
            g["entries"][0]["content"]
            for g in raw_resp.data.get("gmeta", []) if g.get("entries")
        ]
        trace.add(
            "raw_executed",
            f"raw_total={raw_total} returned={len(raw_records)}",
            raw_total=raw_total, raw_returned=len(raw_records),
        )
    except (globus_sdk.GlobusAPIError, globus_sdk.NetworkError) as exc:
        trace.add("raw_error", str(exc), error=str(exc))
        raw_total, raw_records = 0, []

    harm_total, harm_records = 0, []
    if filter_values:
        try:
            harm_resp = client.post_search(index_uuid, {
                "filters": [{
                    "type": "match_any",
                    "field_name": spec["field"],
                    "values": list(filter_values),
                }],
                "limit": limit,
            })
            harm_total = int(harm_resp.data.get("total", 0))
            harm_records = [
                g["entries"][0]["content"]
                for g in harm_resp.data.get("gmeta", []) if g.get("entries")
            ]
            trace.add(
                "harm_executed",
                f"harm_total={harm_total} returned={len(harm_records)}",
                harm_total=harm_total, harm_returned=len(harm_records),
            )
        except (globus_sdk.GlobusAPIError, globus_sdk.NetworkError) as exc:
            trace.add("harm_error", str(exc), error=str(exc))
    else:
        trace.add("harm_skipped",
                  "no filter values built; harmonized query skipped")

    abs_diff = abs(raw_total - harm_total)
    larger = max(raw_total, harm_total) or 1
    div_fraction_actual = abs_diff / larger
    diverges = (
        abs_diff >= div_records or div_fraction_actual >= div_fraction
    )
    trace.add(
        "divergence",
        f"|Δ|={abs_diff} fraction={div_fraction_actual:.1%} diverges={diverges}",
        absolute_diff=abs_diff, divergence_fraction=round(div_fraction_actual, 4),
        diverges=diverges,
    )

    hitl_required = result.path == "ambiguous" or diverges
    reasons: list[str] = []
    if result.path == "ambiguous":
        reasons.append(
            f"ambiguous resolution ({len(result.candidates)} candidates)"
        )
    if diverges:
        reasons.append(
            f"raw_total={raw_total} vs harmonized_total={harm_total} "
            f"(|Δ|={abs_diff})"
        )

    md_lines = [
        f"### Harmonized search: `{term}` on `{index}`",
        f"- **raw** (`q={raw_q}`): `{raw_total}` records",
        f"- **harmonized** ({spec['field']}, {len(filter_values)} value(s)): "
        f"`{harm_total}` records",
        f"- **divergence**: |Δ|={abs_diff} ({div_fraction_actual:.0%} of {larger})",
    ]
    if hitl_required:
        md_lines += [
            "",
            "**HITL required.** Reasons: " + "; ".join(reasons),
        ]

    data_preview = {
        "resolution": {
            "path": result.path,
            "canonical_iri": result.canonical_iri,
            "canonical_label": result.canonical_label,
            "candidates": [
                {
                    "canonical_iri": c.canonical_iri,
                    "canonical_label": c.canonical_label,
                    "confidence": c.confidence,
                }
                for c in result.candidates
            ],
        },
        "raw_query": {
            "q": raw_q, "was_quoted": was_quoted,
            "total": raw_total, "returned": len(raw_records),
            "sample": raw_records[:sample_size],
        },
        "harmonized_query": {
            "filter_field": spec["field"],
            "filter_shape": spec["shape"],
            "filter_values_count": len(filter_values),
            "filter_values_sample": list(filter_values)[:5],
            "total": harm_total, "returned": len(harm_records),
            "sample": harm_records[:sample_size],
        },
        "divergence": {
            "absolute_diff": abs_diff,
            "fraction_of_larger_total": round(div_fraction_actual, 4),
        },
    }

    next_actions: list[dict[str, Any]] = []
    if result.path == "ambiguous":
        next_actions.append({
            "tool": "inspect",
            "params": {"object_type": "canonical_iri", "id": "<chosen IRI>"},
            "purpose": "inspect a candidate's synonyms before user picks",
        })
        next_actions.append({
            "tool": "run",
            "params": {
                "operation": "harmonized_search",
                "params": {"term": "<chosen IRI>", "index": index,
                           "limit": limit},
            },
            "purpose": "re-run on the chosen IRI directly",
        })

    envelope = WorkflowResult(
        markdown="\n".join(md_lines),
        run_id=run_id,
        status="hitl_required" if hitl_required else "ok",
        data_handle=run_id,
        data_preview=data_preview,
        hitl_required=hitl_required,
        hitl_prompt=(
            f"Term {term!r} produced HITL-flagged results on {index}. "
            f"Reasons: {'; '.join(reasons)}."
        ) if hitl_required else None,
        hitl_candidates=[
            {
                "canonical_iri": c.canonical_iri,
                "canonical_label": c.canonical_label,
                "confidence": c.confidence,
            }
            for c in result.candidates
        ],
        next_actions=next_actions,
        processing_steps=trace.as_list(),
    )
    session().record(
        run_id=run_id, operation="harmonized_search", params=params,
        envelope=envelope.to_dict(), data_payload=data_preview,
    )
    return envelope


# ---------------------------------------------------------------------------
# update_dictionary
# ---------------------------------------------------------------------------


def _op_update_dictionary(params: dict[str, Any]) -> WorkflowResult:
    run_id = session().new_run_id()
    force = bool(params.get("force", False))
    trace = ProcessingTrace()
    target = default_dictionary_path()
    pre = current_local_version(target)
    trace.add("pre_check", f"local_version={pre!r}", pre_version=pre)
    try:
        path = bootstrap_dictionary(dest=target, force=force, quiet=True)
    except Exception as exc:  # noqa: BLE001
        trace.add("bootstrap_error", str(exc), error=str(exc))
        envelope = WorkflowResult(
            markdown=f"**Bootstrap failed**: {exc}",
            run_id=run_id, status="error", error=str(exc),
            processing_steps=trace.as_list(),
        )
        session().record(run_id=run_id, operation="update_dictionary",
                         params=params, envelope=envelope.to_dict())
        return envelope
    post = current_local_version(path)
    changed = pre != post
    trace.add("post_check",
              f"local_version={post!r} changed={changed}",
              post_version=post, changed=changed)
    envelope = WorkflowResult(
        markdown=(
            f"### Dictionary update\n"
            f"- **before**: `{pre or '(absent)'}`\n"
            f"- **after**: `{post}`\n"
            f"- **changed**: {changed}\n"
            f"- **path**: `{path}`"
        ),
        run_id=run_id, status="ok",
        data_handle=run_id,
        data_preview={
            "pre_version": pre, "post_version": post, "changed": changed,
            "path": str(path),
        },
        processing_steps=trace.as_list(),
    )
    session().record(
        run_id=run_id, operation="update_dictionary",
        params=params, envelope=envelope.to_dict(),
    )
    return envelope


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


OPERATIONS: dict[str, Callable[[dict[str, Any]], WorkflowResult]] = {
    "resolve": _op_resolve,
    "harmonized_search": _op_harmonized_search,
    "update_dictionary": _op_update_dictionary,
}


def describe_operations() -> list[dict[str, Any]]:
    """Catalog entry for ``discover(category='operation')``."""
    return [
        {
            "name": "resolve",
            "description": "Resolve a surface form to its canonical IRI; "
                           "returns path + candidates + synonym substitution.",
            "params_schema": {
                "term": "str — user surface form (required)",
                "entity_type": "str? — pathogen/vaccine/disease/gene/genome",
            },
        },
        {
            "name": "harmonized_search",
            "description": "Live Globus dual-query (raw substring + harmonized "
                           "label/taxon filter) with divergence + HITL envelope.",
            "params_schema": {
                "term": "str — required",
                "index": "str — short index name (default 'bvbrc_genome')",
                "limit": "int — per-mode record cap (default 200)",
                "sample_size": "int — sample records per mode (default 3)",
                "divergence_records_threshold": "int (default 5)",
                "divergence_fraction_threshold": "float (default 0.05)",
            },
        },
        {
            "name": "update_dictionary",
            "description": "Bootstrap or refresh the local dictionary from the "
                           "published Globus path.",
            "params_schema": {
                "force": "bool — re-download even when version matches (default False)",
            },
        },
    ]
