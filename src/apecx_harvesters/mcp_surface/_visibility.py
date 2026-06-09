"""Shared visibility primitives.

Every MCP tool emits a ``processing_steps`` array so the caller sees
each decision point — what was normalized, what was looked up, which
synonyms map to the same canonical IRI, which records were filtered,
etc. ``ProcessingTrace`` is the lightweight builder all tools use.

Design rule: visibility entries describe WHAT happened, not WHY in
prose. Each entry is structured so a UI can render it as a step-by-step
panel; prose belongs in ``evidence`` strings on the eventual result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProcessingTrace:
    """Accumulator for per-tool processing steps.

    Each ``add(step, **detail)`` call records one decision point. The
    final ``as_list()`` projects to the wire shape — a list of dicts
    each with ``step`` (str), ``decision`` (str), and arbitrary
    ``detail`` keys.
    """

    steps: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        step: str,
        decision: str,
        **detail: Any,
    ) -> None:
        """Record one processing step.

        Parameters
        ----------
        step:
            Short identifier ("normalize_term", "inverse_index_lookup",
            "ambiguous_branch", "globus_query_raw", etc.).
        decision:
            One-line summary of what was decided here.
        **detail:
            Arbitrary structured detail. Keys appear verbatim in the
            wire shape; values must be JSON-serializable.
        """
        entry: dict[str, Any] = {"step": step, "decision": decision}
        entry.update(detail)
        self.steps.append(entry)

    def as_list(self) -> list[dict[str, Any]]:
        return list(self.steps)


def synonyms_substitution_summary(
    *, surface_form: str, canonical_iri: str | None, synonyms: list[str]
) -> dict[str, Any]:
    """Compact summary of what synonym substitution would have occurred.

    Returned alongside every resolved result so the caller sees the
    OTHER surface forms that would have hit the same canonical IRI.
    This is the core "what got substituted" visibility — without it,
    the user only knows their term mapped to an IRI, not what else
    travels under that same IRI.
    """
    return {
        "user_term": surface_form,
        "canonical_iri": canonical_iri,
        "synonyms_count": len(synonyms),
        # Cap at 20 to keep the wire shape small; the full list is on
        # the canonical entry's ``synonyms`` field if the caller needs it.
        "synonyms_sample": synonyms[:20],
        "explanation": (
            f"The user term {surface_form!r} resolved to "
            f"{canonical_iri or '(no canonical IRI)'}. The dictionary "
            f"records {len(synonyms)} surface forms that all resolve to "
            f"the same canonical entry — any of them would have "
            f"returned the same result."
        ),
    }
