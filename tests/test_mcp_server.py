"""End-to-end MCP server tests — tiered primitive surface.

Drives JSON-RPC tool calls against a freshly-spawned apecx-mcp-reader
subprocess. Verifies:

- The surface is exactly 5 generic primitives (discover, inspect, run,
  inspect_run, apecx_context) per external_orchestration_design.md §4.
- run() returns a WorkflowResult envelope per §5 (markdown + run_id +
  status + data_handle + data_preview + processing_steps).
- HITL is preserved end-to-end: ambiguous resolutions set
  status="hitl_required" and populate hitl_candidates + next_actions.
- Visibility: every run carries processing_steps.
- inspect_run lets the LLM retrieve a prior run's payload by run_id.
- Adding capabilities does NOT add tools — operations/object types
  are internal registries.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from apecx_harvesters.dict_reader.loader import default_dictionary_path

pytestmark = pytest.mark.skipif(
    not default_dictionary_path().exists(),
    reason="production dict not bootstrapped; run apecx-dict-update first",
)


async def _call_tool(tool_name: str, arguments: dict) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=str(Path(".venv/bin/apecx-mcp-reader").resolve()),
        env={**os.environ},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
    payload = result.content[0]
    return json.loads(getattr(payload, "text"))


async def _call_sequence(calls):
    """Drive multiple tool calls within a SINGLE MCP session.

    The session store is in-process — run_ids only persist for the
    server process's lifetime. Real MCP clients (Claude Desktop) keep
    one server alive per chat; this helper models that.

    Each entry in ``calls`` is either:
      - ``(tool_name, arguments)`` — fixed call
      - a callable ``f(prior_results: list[dict]) -> (tool_name, arguments)``
        — lets the next call see prior responses (e.g., for run_id interpolation)
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=str(Path(".venv/bin/apecx-mcp-reader").resolve()),
        env={**os.environ},
    )
    out: list[dict] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for entry in calls:
                if callable(entry):
                    tool_name, arguments = entry(out)
                else:
                    tool_name, arguments = entry
                result = await session.call_tool(tool_name, arguments)
                out.append(json.loads(getattr(result.content[0], "text")))
    return out


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Surface shape — exactly 5 generic primitives
# ---------------------------------------------------------------------------


def test_mcp_surface_is_five_primitives() -> None:
    """The MCP wire surface is fixed at 5 primitives, regardless of how
    many operations or object types exist behind them."""
    from apecx_harvesters.mcp_surface.server import build_server
    server = build_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "discover", "inspect", "run", "inspect_run", "apecx_context",
    }


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def test_discover_no_args_returns_catalog() -> None:
    payload = _run(_call_tool("discover", {}))
    assert payload["category"] == ""
    cats = payload["available_categories"]
    assert "operation" in cats
    assert "object_type" in cats
    assert "skill" in cats
    assert "synonym" in cats


def test_discover_operations_lists_resolve_and_harmonized_search() -> None:
    payload = _run(_call_tool("discover", {"category": "operation"}))
    names = {op["name"] for op in payload["results"]}
    assert "resolve" in names
    assert "harmonized_search" in names
    assert "update_dictionary" in names


def test_discover_synonyms_substring_match() -> None:
    """Query 'chikv' substring → finds CHIKV entries in the dict."""
    payload = _run(_call_tool("discover", {
        "category": "synonym", "query": "chikv",
    }))
    assert payload["result_count"] > 0
    # Every result should carry a canonical_iri.
    assert all(r["canonical_iri"] for r in payload["results"])


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def test_inspect_dictionary_local() -> None:
    payload = _run(_call_tool("inspect", {
        "object_type": "dictionary", "id": "local",
    }))
    obj = payload["object"]
    assert obj["exists"] is True
    assert obj["dictionary_version"]
    assert obj["entry_count"] > 100_000


def test_inspect_index_bvbrc_genome() -> None:
    payload = _run(_call_tool("inspect", {
        "object_type": "index", "id": "bvbrc_genome",
    }))
    obj = payload["object"]
    assert obj["harmonized_filter_field"] == "Species"
    assert obj["globus_uuid"] == "b676edbe-3286-4514-bc13-5cbe891c4bb1"


def test_inspect_canonical_iri_round_trip() -> None:
    iri = "http://purl.obolibrary.org/obo/NCBITaxon_37124"  # Chikungunya
    payload = _run(_call_tool("inspect", {
        "object_type": "canonical_iri", "id": iri, "depth": 1,
    }))
    obj = payload["object"]
    assert obj["canonical_iri"] == iri
    assert obj["canonical_label"] == "Chikungunya virus"
    assert isinstance(obj["synonyms"], list)


def test_inspect_skill_returns_skill_md() -> None:
    payload = _run(_call_tool("inspect", {
        "object_type": "skill", "id": "harmonized-discovery", "depth": 1,
    }))
    obj = payload["object"]
    assert obj["skill_md"]
    assert "apecx-discovery-harmonized" in obj["skill_md"]
    assert "schema.json" in obj["references"]
    # The primitive_equivalents map shows what to call instead of the
    # ex-capability tools.
    eq = obj["primitive_equivalents"]
    assert any("run(operation='resolve'" in v for v in eq.values())


# ---------------------------------------------------------------------------
# run + WorkflowResult envelope
# ---------------------------------------------------------------------------


def test_run_resolve_chikv_returns_workflow_result() -> None:
    payload = _run(_call_tool("run", {
        "operation": "resolve", "params": {"term": "CHIKV"},
    }))
    assert payload["status"] == "ok"
    assert payload["run_id"].startswith("run_")
    assert payload["data_handle"] == payload["run_id"]
    assert payload["hitl_required"] is False
    assert "Chikungunya" in payload["markdown"]
    # Visibility: processing_steps traced every decision.
    step_names = {s["step"] for s in payload["processing_steps"]}
    assert "input" in step_names
    assert "normalize" in step_names
    assert "dict_loaded" in step_names
    assert "resolve" in step_names
    # data_preview carries the structured payload + synonym substitution.
    dp = payload["data_preview"]
    assert dp["result"]["canonical_iri"]
    assert dp["synonyms_substitution"]["synonyms_count"] > 1


def test_run_resolve_rsv_triggers_hitl() -> None:
    payload = _run(_call_tool("run", {
        "operation": "resolve", "params": {"term": "RSV"},
    }))
    assert payload["status"] == "hitl_required"
    assert payload["hitl_required"] is True
    assert len(payload["hitl_candidates"]) == 6
    # next_actions guide the model toward disambiguation.
    actions = payload["next_actions"]
    assert any(a["tool"] == "inspect" for a in actions)
    assert any(a["tool"] == "run" for a in actions)
    # Markdown surfaces the candidates so a human can read it directly.
    assert "HITL required" in payload["markdown"]


# ---------------------------------------------------------------------------
# inspect_run + apecx_context
# ---------------------------------------------------------------------------


def test_inspect_run_retrieves_prior_run() -> None:
    """run() then inspect_run(run_id) in the same session returns the
    same payload. Session is in-process; this models the one-server-per-chat
    pattern Claude Desktop uses."""
    results = _run(_call_sequence([
        ("run", {"operation": "resolve",
                 "params": {"term": "EEEV"}}),
        # Use the run_id from the previous call.
        lambda prior: (
            "inspect_run",
            {"run_id": prior[0]["run_id"]},
        ),
    ]))
    run_envelope = results[0]
    retrieved = results[1]
    assert retrieved["run_id"] == run_envelope["run_id"]
    assert retrieved["operation"] == "resolve"
    assert retrieved["envelope"]["status"] == "ok"
    # Re-fetched data_payload matches the original.
    assert (
        retrieved["data_payload"]["result"]["canonical_iri"]
        == run_envelope["data_preview"]["result"]["canonical_iri"]
    )


def test_apecx_context_lists_recent_runs() -> None:
    """Run two operations + apecx_context within ONE session."""
    results = _run(_call_sequence([
        ("run", {"operation": "resolve",
                 "params": {"term": "MAYV"}}),
        ("run", {"operation": "resolve",
                 "params": {"term": "WEEV"}}),
        ("apecx_context", {}),
    ]))
    ctx = results[2]
    assert set(ctx["primitives_available"]) == {
        "discover", "inspect", "run", "inspect_run", "apecx_context",
    }
    # We should see exactly 2 runs from this session.
    assert len(ctx["recent_runs"]) == 2
    operations = {r["operation"] for r in ctx["recent_runs"]}
    assert operations == {"resolve"}


# ---------------------------------------------------------------------------
# Independence
# ---------------------------------------------------------------------------


def test_zero_apecx_integration_imports() -> None:
    import subprocess
    r = subprocess.run(
        [".venv/bin/python", "-c",
         "from apecx_harvesters.mcp_surface.server import build_server; "
         "build_server(); "
         "import sys; "
         "bad = [m for m in sys.modules if 'apecx_integration' in m]; "
         "assert not bad, f'leak: {bad}'; "
         "print('clean')"],
        capture_output=True, text=True,
    )
    assert "clean" in r.stdout, f"stdout: {r.stdout}\nstderr: {r.stderr}"
