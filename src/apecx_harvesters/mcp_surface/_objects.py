"""Inspectable object types — what ``inspect`` operates on.

Each entry in :data:`OBJECT_INSPECTORS` is a callable that takes
``(identifier: str, depth: int)`` and returns a dict describing the
object. The MCP ``inspect`` primitive is generic; per-object richness
lives here.

Current catalog:

- ``canonical_iri`` — describe one entry (full synonyms + source records).
- ``dictionary`` — describe the local dictionary (version, manifest,
  entry counts); ``id="published"`` describes the published version.
- ``index`` — describe one of the 9 Globus indices (UUID + filter spec).
- ``skill`` — return the agent-skill SKILL.md + references.
- ``operation`` — describe one operation (its params schema).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from apecx_harvesters.dict_reader import (
    configure_dictionary_path,
    default_dictionary_path,
    get_dictionary_index,
)
from apecx_harvesters.dict_reader.bootstrap import (
    SUPPORTED_SCHEMA_MAJOR,
    fetch_manifest,
)
from apecx_harvesters.dict_reader.bootstrap import (
    current_local_version as _local_version,
)
from apecx_harvesters.mcp_surface._visibility import ProcessingTrace


# Same maps as _operations.py — kept duplicated to avoid a cyclic import.
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


def _ensure_dict() -> tuple[bool, str | None]:
    p = default_dictionary_path()
    if not p.exists():
        return False, f"local dictionary not present at {p}"
    configure_dictionary_path(p)
    return True, None


def _inspect_canonical_iri(identifier: str, depth: int) -> dict[str, Any]:
    trace = ProcessingTrace()
    trace.add("input", f"canonical_iri={identifier!r} depth={depth}",
              canonical_iri=identifier, depth=depth)
    ok, err = _ensure_dict()
    if not ok:
        return {"processing_steps": trace.as_list(), "object": None,
                "error": err}
    index, _ = get_dictionary_index()
    if index is None:
        return {"processing_steps": trace.as_list(), "object": None,
                "error": "dictionary load failed"}
    entry = index.lookup_by_iri(identifier)
    if entry is None:
        trace.add("lookup_by_iri",
                  f"no entry for {identifier!r}",
                  canonical_iri=identifier)
        return {"processing_steps": trace.as_list(), "object": None,
                "error": f"no entry for {identifier!r}"}
    trace.add("lookup_by_iri",
              f"found {entry.canonical_label!r}",
              canonical_label=entry.canonical_label)

    out: dict[str, Any] = {
        "object_type": "canonical_iri",
        "canonical_iri": entry.canonical_iri,
        "canonical_label": entry.canonical_label,
        "canonical_ontology": entry.ontology.value,
        "ontology_version": entry.ontology_version,
        "confidence": entry.confidence,
        "entity_type": entry.entity_type.value,
        "synonyms_count": len(entry.synonyms),
    }
    if depth >= 1:
        out["synonyms"] = list(entry.synonyms)
        out["source_records"] = list(entry.source_records)
    return {"processing_steps": trace.as_list(), "object": out}


def _inspect_dictionary(identifier: str, depth: int) -> dict[str, Any]:
    """``id="local"`` or ``id="published"``."""
    trace = ProcessingTrace()
    identifier = identifier or "local"
    trace.add("input", f"dictionary={identifier!r}", id=identifier, depth=depth)

    if identifier == "local":
        target = default_dictionary_path()
        v = _local_version(target)
        if v is None:
            return {
                "processing_steps": trace.as_list(),
                "object": {"object_type": "dictionary", "id": "local",
                           "exists": False, "path": str(target)},
            }
        index, _ = get_dictionary_index()
        if index is None:
            configure_dictionary_path(target)
            index, _ = get_dictionary_index()
        info = {
            "object_type": "dictionary", "id": "local",
            "exists": True, "path": str(target),
            "dictionary_version": v,
            "supported_schema_major": list(SUPPORTED_SCHEMA_MAJOR),
        }
        if index is not None:
            info["entry_count"] = index.entry_count()
            info["inverse_index_size"] = index.index_entry_count()
            info["schema_version"] = index.manifest.schema_version
            info["built_at"] = index.manifest.built_at.isoformat()
        return {"processing_steps": trace.as_list(), "object": info}

    if identifier == "published":
        try:
            m = fetch_manifest()
        except Exception as exc:  # noqa: BLE001
            trace.add("manifest_fetch", f"failed: {exc}", error=str(exc))
            return {
                "processing_steps": trace.as_list(),
                "object": None,
                "error": f"could not fetch published manifest: {exc}",
            }
        trace.add("manifest_fetch", f"version={m.dictionary_version}",
                  dictionary_version=m.dictionary_version)
        return {
            "processing_steps": trace.as_list(),
            "object": {
                "object_type": "dictionary", "id": "published",
                "dictionary_version": m.dictionary_version,
                "schema_version": m.schema_version,
                "built_at": m.built_at,
                "published_filename": m.dictionary_filename,
                "compressed_bytes": m.dictionary_size_bytes,
                "sha256": m.dictionary_sha256,
                "compression": m.compression,
            },
        }

    return {
        "processing_steps": trace.as_list(),
        "object": None,
        "error": f"unknown dictionary id {identifier!r} "
                 f"(use 'local' or 'published')",
    }


def _inspect_index(identifier: str, depth: int) -> dict[str, Any]:
    trace = ProcessingTrace()
    trace.add("input", f"index={identifier!r}", id=identifier)
    if identifier not in _INDEX_UUIDS:
        return {
            "processing_steps": trace.as_list(),
            "object": None,
            "error": f"unknown index {identifier!r}; "
                     f"valid: {sorted(_INDEX_UUIDS)}",
        }
    spec = _HARMONIZED_FILTER[identifier]
    return {
        "processing_steps": trace.as_list(),
        "object": {
            "object_type": "index",
            "name": identifier,
            "globus_uuid": _INDEX_UUIDS[identifier],
            "harmonized_filter_field": spec["field"],
            "harmonized_filter_shape": spec["shape"],
        },
    }


def _inspect_skill(identifier: str, depth: int) -> dict[str, Any]:
    """Return the agent-skill SKILL.md + references."""
    trace = ProcessingTrace()
    identifier = identifier or "harmonized-discovery"
    trace.add("input", f"skill={identifier!r}", id=identifier)
    if identifier not in (
        "harmonized-discovery", "apecx-discovery-harmonized",
    ):
        return {
            "processing_steps": trace.as_list(),
            "object": None,
            "error": f"unknown skill {identifier!r}; "
                     f"only 'harmonized-discovery' is shipped here",
        }
    # Resolve the skill root. Two layouts to support:
    # 1. Installed wheel: skill assets live at
    #    apecx_harvesters/_skill_assets/agent-skill-harmonized/ via the
    #    hatchling force-include in pyproject.toml.
    # 2. Source checkout: skill lives at the repo's
    #    search_demo/agent-skill-harmonized/ relative to project root.
    here = Path(__file__).resolve().parent
    bundled = here.parent / "_skill_assets" / "agent-skill-harmonized"
    root: Path | None = None
    if bundled.is_dir():
        root = bundled
    else:
        for p in (here, *here.parents):
            cand = p / "search_demo" / "agent-skill-harmonized"
            if cand.is_dir():
                root = cand
                break
            cand2 = p.parent / "search_demo" / "agent-skill-harmonized"
            if cand2.is_dir():
                root = cand2
                break
    if root is None:
        return {
            "processing_steps": trace.as_list(),
            "object": None,
            "error": "skill directory not discoverable from this install",
        }
    trace.add("locate_skill", f"root={root}", root=str(root))

    skill_md = (root / "SKILL.md").read_text(encoding="utf-8") \
        if (root / "SKILL.md").is_file() else None
    refs: dict[str, str] = {}
    if depth >= 1 and (root / "references").is_dir():
        for f in sorted((root / "references").iterdir()):
            if f.is_file():
                try:
                    refs[f.name] = f.read_text(encoding="utf-8")
                except Exception:
                    pass
    return {
        "processing_steps": trace.as_list(),
        "object": {
            "object_type": "skill",
            "id": "harmonized-discovery",
            "skill_root": str(root),
            "skill_md": skill_md,
            "references": refs,
            "primitive_equivalents": {
                "resolve-only lookup": "run(operation='resolve', params={'term': ...})",
                "--compare divergence query":
                    "run(operation='harmonized_search', params={'term': ..., 'index': ...})",
                "describe canonical IRI's synonyms":
                    "inspect(object_type='canonical_iri', id=<IRI>)",
                "check dict version":
                    "inspect(object_type='dictionary', id='local') "
                    "+ inspect(id='published')",
                "bootstrap dict":
                    "run(operation='update_dictionary')",
            },
        },
    }


def _inspect_operation(identifier: str, depth: int) -> dict[str, Any]:
    from apecx_harvesters.mcp_surface._operations import describe_operations
    trace = ProcessingTrace()
    trace.add("input", f"operation={identifier!r}", id=identifier)
    matching = [op for op in describe_operations() if op["name"] == identifier]
    if not matching:
        return {
            "processing_steps": trace.as_list(),
            "object": None,
            "error": f"unknown operation {identifier!r}",
        }
    return {
        "processing_steps": trace.as_list(),
        "object": {"object_type": "operation", **matching[0]},
    }


OBJECT_INSPECTORS: dict[str, Callable[[str, int], dict[str, Any]]] = {
    "canonical_iri": _inspect_canonical_iri,
    "dictionary": _inspect_dictionary,
    "index": _inspect_index,
    "skill": _inspect_skill,
    "operation": _inspect_operation,
}


def describe_object_types() -> list[dict[str, Any]]:
    return [
        {"name": "canonical_iri",
         "description": "Full record for one canonical IRI (entry's "
                        "label/ontology/synonyms/source_records)."},
        {"name": "dictionary",
         "description": "Either the local dict (id='local') or the "
                        "published manifest (id='published')."},
        {"name": "index",
         "description": "One of the 9 APECx Globus indices (UUID + "
                        "harmonized filter field)."},
        {"name": "skill",
         "description": "The agent-skill SKILL.md + references."},
        {"name": "operation",
         "description": "An operation registered in the run() catalog."},
    ]
