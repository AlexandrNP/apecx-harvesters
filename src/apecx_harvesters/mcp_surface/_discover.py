"""Discovery catalog — what ``discover(category, query)`` walks.

Per ``external_orchestration_design.md §4`` the discover primitive is
"RAG over the registry" — a lightweight catalog search. The user-facing
arm doesn't need a FAISS index for this; the catalogs are small and
substring matching is sufficient. The shape stays forward-compatible
with a future RAG upgrade.

Categories surfaced today:

- ``operation`` — runnable verbs (resolve, harmonized_search, ...)
- ``object_type`` — inspectable typed objects (canonical_iri, ...)
- ``index`` — the 9 Globus source indices
- ``skill`` — the agent-skill bundles
- ``synonym`` — synonyms in the local dict that contain the query substring
- ``candidate_iri`` — canonical IRIs whose label contains the query substring
"""

from __future__ import annotations

from typing import Any

from apecx_harvesters.dict_reader import (
    configure_dictionary_path,
    default_dictionary_path,
    get_dictionary_index,
)
from apecx_harvesters.mcp_surface._objects import (
    _INDEX_UUIDS,
    describe_object_types,
)
from apecx_harvesters.mcp_surface._operations import describe_operations


def _matches(needle: str, haystack: str) -> bool:
    if not needle:
        return True
    return needle.casefold() in haystack.casefold()


def _discover_operations(query: str) -> list[dict[str, Any]]:
    return [
        op for op in describe_operations()
        if _matches(query, op["name"]) or _matches(query, op["description"])
    ]


def _discover_object_types(query: str) -> list[dict[str, Any]]:
    return [
        ot for ot in describe_object_types()
        if _matches(query, ot["name"]) or _matches(query, ot["description"])
    ]


def _discover_indices(query: str) -> list[dict[str, Any]]:
    return [
        {"name": name, "globus_uuid": uuid}
        for name, uuid in sorted(_INDEX_UUIDS.items())
        if _matches(query, name)
    ]


def _discover_skills(query: str) -> list[dict[str, Any]]:
    items = [
        {
            "id": "harmonized-discovery",
            "name": "apecx-discovery-harmonized",
            "description": "Query the harmonized APECx biomedical search "
                           "indices (VIOLIN, BVBRC, ProtaBank, AntiviralDB) "
                           "with canonical-IRI substitution and HITL on "
                           "raw-vs-harmonized divergence.",
        },
    ]
    return [
        s for s in items
        if _matches(query, s["id"]) or _matches(query, s["description"])
        or _matches(query, s["name"])
    ]


def _discover_synonyms(query: str) -> list[dict[str, Any]]:
    if not query:
        return []
    path = default_dictionary_path()
    if not path.exists():
        return []
    configure_dictionary_path(path)
    index, _ = get_dictionary_index()
    if index is None:
        return []
    q = query.casefold()
    matches: list[dict[str, Any]] = []
    for (entity_type, normalized), iris in index._inverse.items():
        if q in normalized:
            for iri in iris:
                entry = index._entries.get(iri)
                if entry:
                    matches.append({
                        "surface_form": normalized,
                        "canonical_iri": iri,
                        "canonical_label": entry.canonical_label,
                        "entity_type": entity_type,
                    })
        if len(matches) >= 50:
            break
    return matches[:50]


def _discover_candidate_iris(query: str) -> list[dict[str, Any]]:
    if not query:
        return []
    path = default_dictionary_path()
    if not path.exists():
        return []
    configure_dictionary_path(path)
    index, _ = get_dictionary_index()
    if index is None:
        return []
    q = query.casefold()
    out: list[dict[str, Any]] = []
    for iri, entry in index._entries.items():
        if q in entry.canonical_label.casefold():
            out.append({
                "canonical_iri": iri,
                "canonical_label": entry.canonical_label,
                "ontology": entry.ontology.value,
            })
        if len(out) >= 50:
            break
    return out[:50]


CATEGORIES = {
    "operation": _discover_operations,
    "object_type": _discover_object_types,
    "index": _discover_indices,
    "skill": _discover_skills,
    "synonym": _discover_synonyms,
    "candidate_iri": _discover_candidate_iris,
}


def discover_catalog() -> list[dict[str, Any]]:
    """Catalog of available discover categories."""
    return [
        {"category": "operation",
         "description": "Verbs callable via run() — see params schemas."},
        {"category": "object_type",
         "description": "Typed objects inspect() understands."},
        {"category": "index",
         "description": "Globus indices harmonized_search can query."},
        {"category": "skill",
         "description": "Agent-skill bundles available; inspect them via "
                        "inspect(object_type='skill', id=<name>)."},
        {"category": "synonym",
         "description": "Surface forms in the dict whose normalized form "
                        "contains the query substring."},
        {"category": "candidate_iri",
         "description": "Canonical IRIs whose label contains the query substring."},
    ]
