"""In-process session store for run history + data handles.

Backs the §5 ``data_handle`` field and the ``apecx_context`` primitive.
Scoped to a single MCP server process — the user-facing arm doesn't
need cross-process state (Claude Desktop spawns one server per chat
session). Bounded by a ring-buffer cap so a long session doesn't grow
the resident set without bound.
"""

from __future__ import annotations

import secrets
from collections import OrderedDict
from typing import Any

_MAX_RUNS = 64


class SessionStore:
    """OrderedDict-backed bounded run cache.

    LRU semantics: once full, the oldest run is evicted on insert.
    Single-process, in-memory; no persistence. The wire-shape contract
    (``data_handle`` is a string the caller passes back to retrieve)
    is preserved so a future ProxyStore-backed implementation is a
    drop-in.
    """

    def __init__(self, max_runs: int = _MAX_RUNS) -> None:
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max = max_runs

    def new_run_id(self) -> str:
        return "run_" + secrets.token_hex(8)

    def record(
        self,
        *,
        run_id: str,
        operation: str,
        params: dict[str, Any],
        envelope: dict[str, Any],
        data_payload: Any | None = None,
    ) -> None:
        """Store a run's full record. Evicts the oldest when over cap."""
        self._runs[run_id] = {
            "run_id": run_id,
            "operation": operation,
            "params": params,
            "envelope": envelope,
            "data_payload": data_payload,
        }
        self._runs.move_to_end(run_id)
        while len(self._runs) > self._max:
            self._runs.popitem(last=False)

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self._runs.get(run_id)

    def get_payload(self, handle: str) -> Any | None:
        """Resolve a ``data_handle`` back to its payload.

        ``data_handle`` and ``run_id`` are the same identifier in this
        in-memory implementation. A ProxyStore-backed swap would
        distinguish them; the wire contract stays unchanged.
        """
        rec = self._runs.get(handle)
        return rec.get("data_payload") if rec else None

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Most-recent runs first."""
        items = list(reversed(self._runs.values()))
        return [
            {
                "run_id": r["run_id"],
                "operation": r["operation"],
                "params": r["params"],
                "status": r["envelope"].get("status"),
                "hitl_required": r["envelope"].get("hitl_required", False),
            }
            for r in items[:limit]
        ]


# Process singleton — the FastMCP server is single-process so a module
# global is fine.
_SESSION = SessionStore()


def session() -> SessionStore:
    return _SESSION
