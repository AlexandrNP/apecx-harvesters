"""WorkflowResult envelope — the §5 contract.

Every ``run`` primitive returns this shape. The external LLM reads
``markdown`` for human-readable narration; chains operations via
``data_handle`` (a stable string that re-resolves on disk or in
process memory — never enters the LLM's context); uses
``data_preview`` to reason about next steps.

Ported in spirit from ``external_orchestration_design.md §5``. We
don't have ProxyStore on the user-facing arm (deliberate dep cut), so
``data_handle`` here points at an in-memory session store keyed by
``run_id``. The shape is forward-compatible: a future ProxyStore-backed
deployment can replace the handle resolver without changing the wire.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass
class WorkflowResult:
    """The §5 output envelope. Returned by every ``run(...)`` call."""

    markdown: str
    run_id: str
    status: Literal["ok", "partial", "error", "hitl_required"]
    data_handle: str | None = None
    data_preview: dict[str, Any] | None = None
    hitl_required: bool = False
    hitl_prompt: str | None = None
    hitl_candidates: list[dict[str, Any]] = field(default_factory=list)
    next_actions: list[dict[str, Any]] = field(default_factory=list)
    processing_steps: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
