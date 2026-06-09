"""MCP server for the apecx-harvesters user-facing arm.

Exposes the synonym-dictionary lookup + harmonized-search functionality
as Model Context Protocol tools. Designed for **maximum visibility**:
every tool response carries a ``processing_steps`` array listing each
decision the resolution took, so the user (or the MCP client model
acting on their behalf) can see exactly how a surface form was
substituted with a canonical IRI.

Architectural constraints (matches docs/two_arm_contract.md):

- Zero dependency on apecx_integration, nanobrain, MCP control plane,
  LLM wrappers, or FAISS. Same independence guarantee as the dict_reader.
- Reads the dictionary from the same canonical local path the dict_reader
  uses: ``~/.apecx/dictionary/dictionary.sqlite`` (or
  ``APECX_SYNONYM_DICT_PATH``).
- HITL is not optional: ambiguous resolutions ALWAYS return the full
  candidate list with ``hitl_required: true``; the tool never picks a
  winner silently.

Public entry point: ``apecx_harvesters.mcp_surface.server.main`` —
wired as the ``apecx-mcp-reader`` console script in pyproject.toml.
"""

from __future__ import annotations
