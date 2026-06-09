"""apecx-mcp-reader — tiered MCP surface (5 generic primitives).

Implements the §4 ``workflow-as-object`` MCP pattern from
``apecx-mcp-integration/docs/external_orchestration_design.md``:

> The external LLM is the orchestrator, so it needs primitives, not super-tools.

Five primitives, each operating over a typed object space:

| Primitive    | Operates over           | Purpose |
|--------------|-------------------------|---------|
| ``discover`` | catalogs                | RAG/substring catalog search |
| ``inspect``  | object_type + id        | Show structure of one object |
| ``run``      | operation + params      | Execute and return WorkflowResult |
| ``inspect_run`` | run_id                | Provenance for a prior run |
| ``apecx_context`` | (none)             | Session re-orientation |

Adding a new capability does NOT mean adding an MCP tool — it means
adding an entry in ``_operations.OPERATIONS`` (a new runnable verb) or
``_objects.OBJECT_INSPECTORS`` (a new inspectable object type). The
MCP surface area stays at 5 tools forever.

Architectural contract:
- Zero apecx_integration / nanobrain / LLM-stack imports.
- Every ``run`` returns the §5 ``WorkflowResult`` envelope.
- HITL is NEVER bypassed: ambiguous resolutions set
  ``status="hitl_required"``, populate ``hitl_candidates``, and put
  the recommended follow-up calls in ``next_actions``.
- Every result includes ``processing_steps`` — full visibility.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from apecx_harvesters.mcp_surface._discover import (
    CATEGORIES as DISCOVER_CATEGORIES,
)
from apecx_harvesters.mcp_surface._discover import discover_catalog
from apecx_harvesters.mcp_surface._objects import (
    OBJECT_INSPECTORS,
    describe_object_types,
)
from apecx_harvesters.mcp_surface._operations import (
    OPERATIONS,
    describe_operations,
)
from apecx_harvesters.mcp_surface._session import session

log = logging.getLogger("apecx-mcp-reader")


def discover(category: str = "", query: str = "") -> dict[str, Any]:
    """Catalog-search primitive — RAG over registries.

    Parameters
    ----------
    category:
        One of: ``"operation"``, ``"object_type"``, ``"index"``,
        ``"skill"``, ``"synonym"``, ``"candidate_iri"``. Empty string
        returns the catalog of available categories.
    query:
        Substring filter applied within the category. Empty string
        returns everything in the category.

    Returns dict with ``category``, ``query``, ``results`` (list of
    catalog entries), and ``available_categories`` for re-orientation.
    """
    if not category:
        return {
            "category": "",
            "query": query,
            "results": discover_catalog(),
            "available_categories": list(DISCOVER_CATEGORIES),
            "explanation": (
                "No category specified. Pick one from "
                "`available_categories` and re-call."
            ),
        }
    if category not in DISCOVER_CATEGORIES:
        return {
            "error": f"unknown category {category!r}",
            "available_categories": list(DISCOVER_CATEGORIES),
        }
    results = DISCOVER_CATEGORIES[category](query)
    return {
        "category": category,
        "query": query,
        "results": results,
        "result_count": len(results),
        "available_categories": list(DISCOVER_CATEGORIES),
    }


def inspect(
    object_type: str = "",
    id: str = "",
    depth: int = 0,
) -> dict[str, Any]:
    """Typed inspection primitive.

    Parameters
    ----------
    object_type:
        ``"canonical_iri"``, ``"dictionary"``, ``"index"``, ``"skill"``,
        ``"operation"``. Empty string returns the catalog of types.
    id:
        Object identifier — interpretation depends on object_type
        (an IRI string for ``canonical_iri``; ``"local"`` or
        ``"published"`` for ``dictionary``; an index short name for
        ``index``; etc.).
    depth:
        0 returns metadata only; 1+ expands lists (synonyms,
        references, source records).
    """
    if not object_type:
        return {
            "available_object_types": [
                ot["name"] for ot in describe_object_types()
            ],
            "explanation": (
                "No object_type specified. Pick one from "
                "`available_object_types` and re-call."
            ),
        }
    if object_type not in OBJECT_INSPECTORS:
        return {
            "error": f"unknown object_type {object_type!r}",
            "available_object_types": list(OBJECT_INSPECTORS),
        }
    if not id:
        return {
            "error": f"object_type {object_type!r} requires an `id`",
            "hint": "Use discover() to find ids matching a substring.",
        }
    return OBJECT_INSPECTORS[object_type](id, int(depth))


def run(
    operation: str = "",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a registered operation and return a WorkflowResult envelope.

    Parameters
    ----------
    operation:
        One of the names in ``discover(category="operation")``. Empty
        string returns the available operations.
    params:
        Operation-specific parameters dict. The params schema for each
        operation is in ``discover(category="operation")``.

    The returned envelope carries:

    - ``markdown`` — human-readable narration of the result
    - ``run_id`` + ``data_handle`` — pointers into the session store
      so a follow-up call can retrieve the structured payload
    - ``data_preview`` — small structured peek
    - ``status`` — ``"ok"``, ``"hitl_required"``, ``"partial"``, or ``"error"``
    - ``hitl_required`` + ``hitl_candidates`` + ``hitl_prompt`` when
      the operation needs disambiguation
    - ``next_actions`` — concrete follow-up tool calls
    - ``processing_steps`` — full audit trail
    """
    if not operation:
        return {
            "available_operations": [
                op["name"] for op in describe_operations()
            ],
            "explanation": (
                "No operation specified. Use "
                "`discover(category='operation')` to see params schemas."
            ),
        }
    if operation not in OPERATIONS:
        return {
            "error": f"unknown operation {operation!r}",
            "available_operations": list(OPERATIONS),
        }
    envelope = OPERATIONS[operation](params or {})
    return envelope.to_dict()


def inspect_run(run_id: str = "") -> dict[str, Any]:
    """Retrieve a prior run's full record by run_id.

    Returns the original parameters, the envelope that was returned,
    and the data payload (the same one ``data_handle`` resolves to).
    Use this to retrieve structured data from an earlier ``run`` call
    without re-executing.
    """
    if not run_id:
        return {
            "error": "missing `run_id`",
            "hint": "Use apecx_context() to see recent runs.",
        }
    rec = session().get(run_id)
    if rec is None:
        return {
            "error": f"unknown run_id {run_id!r}",
            "hint": "Run may have been evicted from the session ring buffer.",
        }
    return {
        "run_id": run_id,
        "operation": rec["operation"],
        "params": rec["params"],
        "envelope": rec["envelope"],
        "data_payload": rec["data_payload"],
    }


def apecx_context(limit: int = 10) -> dict[str, Any]:
    """Session re-orientation: recent runs + summary state.

    Returns the N most-recent runs (operation + params + status +
    run_id), the local dictionary version, and a compact summary
    of what the session has done. Useful after a context drop or
    when an LLM client wants to find a prior ``data_handle``.
    """
    recent = session().recent(int(limit))
    # Cheap local-dict version lookup (no full load).
    from apecx_harvesters.dict_reader.bootstrap import current_local_version
    from apecx_harvesters.dict_reader.loader import default_dictionary_path
    local_version = current_local_version(default_dictionary_path())
    pending_hitl = [r for r in recent if r["hitl_required"]]
    return {
        "local_dictionary_version": local_version,
        "recent_runs": recent,
        "pending_hitl_runs": pending_hitl,
        "session_summary": {
            "recent_run_count": len(recent),
            "pending_hitl_count": len(pending_hitl),
        },
        "primitives_available": [
            "discover", "inspect", "run", "inspect_run", "apecx_context",
        ],
    }


def build_server() -> FastMCP:
    """Construct the FastMCP server with the 5 primitives registered."""
    server: FastMCP = FastMCP("apecx-mcp-reader")
    server.tool()(discover)
    server.tool()(inspect)
    server.tool()(run)
    server.tool()(inspect_run)
    server.tool()(apecx_context)
    return server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    log.info(
        "apecx-mcp-reader starting (FastMCP stdio); 5 tiered primitives"
    )
    build_server().run()


if __name__ == "__main__":
    main()
