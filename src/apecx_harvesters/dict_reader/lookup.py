"""Runtime lookup API for the apecx synonym dictionary.

Exposes a single entry point — :func:`lookup_entity` — that hits the
in-memory ``DictionaryIndex`` populated by
``apecx_harvesters.dict_reader.loader`` and routes the result through one
of: ``fast`` / ``ambiguous`` / ``ancestor`` / ``fuzzy`` / ``deleted`` /
``miss``.

Ported from ``apecx_integration.synonym_dictionary.lookup`` (2026-06-04)
with one deliberate cut: the database-substring slow-path has been
removed. That fallback pulled in
``apecx_integration.mcp_surface.data.database`` which loads the
VIOLIN/BVBRC CSVs via pandas — a heavyweight dep we explicitly want
absent from the user-facing arm.

The path enum still contains ``"slow"`` for forward-compatibility but
this implementation never emits it.

Visibility guarantee: the result ALWAYS includes which path was taken
and at what confidence. Callers MUST NOT silently route a term to a
canonical IRI without checking the path field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from apecx_harvesters.dict_reader.enums import EntityType, ResolutionStatus
from apecx_harvesters.dict_reader.loader import get_dictionary_index
from apecx_harvesters.dict_reader.schema import DictionaryEntry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LookupCandidate:
    """One candidate when the surface form maps to multiple canonical IRIs."""

    canonical_iri: str
    canonical_label: str
    canonical_ontology: str
    confidence: float


@dataclass(frozen=True)
class LookupResult:
    """The outcome of a single lookup.

    The ``path`` field is load-bearing — it tells the caller HOW the
    canonical IRI (if any) was reached:
      - ``"fast"``      — exact dictionary hit, single unambiguous candidate
      - ``"ambiguous"`` — exact hit but multiple candidates; HITL required
      - ``"ancestor"``  — strain-level IRI matched via NCBITaxon ancestor walk
      - ``"fuzzy"``     — trigram-Jaccard >= 0.85 fuzzy hit, no near-tied runner-up
      - ``"deleted"``   — IRI points to an NCBI-retired taxon
      - ``"slow"``      — reserved; never emitted by this implementation
      - ``"miss"``      — no resolution on any path
    """

    surface_form: str
    path: Literal["fast", "ambiguous", "ancestor", "slow", "fuzzy", "deleted", "miss"]
    canonical_iri: str | None
    canonical_label: str | None
    canonical_ontology: str | None
    confidence: float
    resolution_status: ResolutionStatus
    synonyms: tuple[str, ...] = field(default_factory=tuple)
    evidence: str = ""
    candidates: tuple[LookupCandidate, ...] = field(default_factory=tuple)


def fast_miss(surface_form: str, *, reason: str = "") -> LookupResult:
    return LookupResult(
        surface_form=surface_form,
        path="miss",
        canonical_iri=None,
        canonical_label=None,
        canonical_ontology=None,
        confidence=0.0,
        resolution_status=ResolutionStatus.UNRESOLVED,
        evidence=reason,
    )


def lookup_entity(
    surface_form: str,
    *,
    entity_type: EntityType | None = None,
    enable_fuzzy: bool = True,
) -> LookupResult:
    """Look up a user-supplied term against the synonym dictionary.

    ``enable_fuzzy=False`` skips the trigram-Jaccard fuzzy fallback. The
    republish path uses this: it resolves CONTROLLED organism fields
    (Species / Genome / Organism) that are clean NCBI names — they should
    match exactly or not at all. A fuzzy match there risks stamping a WRONG
    taxon subject, and the trigram index build over a large dictionary is
    expensive. Interactive lookups keep fuzzy on (the default).
    """
    if not surface_form or not surface_form.strip():
        return fast_miss(surface_form, reason="empty input")

    index, load_error = get_dictionary_index()

    # IRI shortcut: caller already supplied a canonical IRI.
    if index is not None and (
        surface_form.startswith("http://") or surface_form.startswith("https://")
    ):
        entry = index.lookup_by_iri(surface_form)
        if entry is not None:
            return _entry_to_result(surface_form, entry, path="fast")
        if index.is_taxon_deleted(surface_form):
            return _deleted_to_result(surface_form)
        ancestor = index.lookup_ancestor(surface_form)
        if ancestor is not None:
            return _ancestor_to_result(surface_form, ancestor)

    # Fast path — surface form lookup.
    if index is not None:
        if entity_type is not None:
            candidates = index.lookup_all(entity_type, surface_form)
            if len(candidates) == 1:
                return _entry_to_result(surface_form, candidates[0], path="fast")
            if len(candidates) >= 2:
                return _ambiguous_to_result(surface_form, candidates)
        else:
            matches = index.lookup_any_type(surface_form)
            if len(matches) == 1:
                return _entry_to_result(surface_form, matches[0], path="fast")
            if len(matches) >= 2:
                return _ambiguous_to_result(surface_form, tuple(matches))

    # Fuzzy fallback (SC-C5). Skipped when the caller disables it.
    if index is not None and enable_fuzzy:
        fuzzy = index.lookup_fuzzy(
            surface_form, entity_type=entity_type, threshold=0.70
        )
        if fuzzy:
            top_entry, top_conf = fuzzy[0]
            runner_up_conf = fuzzy[1][1] if len(fuzzy) > 1 else 0.0
            near_tie = runner_up_conf >= top_conf - 0.05
            if top_conf >= 0.85 and not near_tie:
                return _fuzzy_resolved_to_result(surface_form, top_entry, top_conf)
            return _fuzzy_ambiguous_to_result(surface_form, fuzzy)

    reason = (
        load_error
        if (index is None and load_error)
        else f"no match in dictionary for {surface_form!r}"
    )
    return fast_miss(surface_form, reason=reason)


def _entry_to_result(
    surface_form: str,
    entry: DictionaryEntry,
    *,
    path: Literal["fast", "ancestor", "slow"],
) -> LookupResult:
    return LookupResult(
        surface_form=surface_form,
        path=path,
        canonical_iri=entry.canonical_iri,
        canonical_label=entry.canonical_label,
        canonical_ontology=entry.ontology.value,
        confidence=entry.confidence,
        resolution_status=(
            ResolutionStatus.ID_ANCHORED
            if entry.confidence == 1.0
            else ResolutionStatus.OLS_FUZZY
        ),
        synonyms=entry.synonyms,
        evidence=(
            f"dictionary_version={entry.ontology_version}; "
            f"source_records={len(entry.source_records)}"
        ),
    )


def _fuzzy_resolved_to_result(
    surface_form: str, entry: DictionaryEntry, confidence: float
) -> LookupResult:
    return LookupResult(
        surface_form=surface_form,
        path="fuzzy",
        canonical_iri=entry.canonical_iri,
        canonical_label=entry.canonical_label,
        canonical_ontology=entry.ontology.value,
        confidence=round(confidence, 4),
        resolution_status=ResolutionStatus.FUZZY_RESOLVED,
        synonyms=entry.synonyms,
        evidence=(
            f"trigram-Jaccard fuzzy match; "
            f"dictionary_version={entry.ontology_version}"
        ),
    )


def _fuzzy_ambiguous_to_result(
    surface_form: str,
    fuzzy_hits: tuple[tuple[DictionaryEntry, float], ...],
) -> LookupResult:
    candidate_records = tuple(
        LookupCandidate(
            canonical_iri=entry.canonical_iri,
            canonical_label=entry.canonical_label,
            canonical_ontology=entry.ontology.value,
            confidence=round(conf, 4),
        )
        for entry, conf in fuzzy_hits
    )
    top_conf = fuzzy_hits[0][1] if fuzzy_hits else 0.0
    return LookupResult(
        surface_form=surface_form,
        path="ambiguous",
        canonical_iri=None,
        canonical_label=None,
        canonical_ontology=None,
        confidence=0.0,
        resolution_status=ResolutionStatus.AMBIGUOUS,
        synonyms=(),
        evidence=(
            f"trigram-Jaccard fuzzy hit but ambiguous (top={top_conf:.2f}, "
            f"{len(candidate_records)} candidate(s)); HITL required"
        ),
        candidates=candidate_records,
    )


def _deleted_to_result(iri: str) -> LookupResult:
    return LookupResult(
        surface_form=iri,
        path="deleted",
        canonical_iri=None,
        canonical_label=None,
        canonical_ontology=None,
        confidence=0.0,
        resolution_status=ResolutionStatus.TAXON_DELETED,
        synonyms=(),
        evidence=f"taxon retired by NCBI (delnodes.dmp); pasted IRI: {iri}",
    )


def _ambiguous_to_result(
    surface_form: str, candidates: tuple[DictionaryEntry, ...]
) -> LookupResult:
    candidate_records = tuple(
        LookupCandidate(
            canonical_iri=entry.canonical_iri,
            canonical_label=entry.canonical_label,
            canonical_ontology=entry.ontology.value,
            confidence=entry.confidence,
        )
        for entry in candidates
    )
    return LookupResult(
        surface_form=surface_form,
        path="ambiguous",
        canonical_iri=None,
        canonical_label=None,
        canonical_ontology=None,
        confidence=0.0,
        resolution_status=ResolutionStatus.AMBIGUOUS,
        synonyms=(),
        evidence=(
            f"{len(candidate_records)} candidate IRIs for "
            f"{surface_form!r}; HITL required"
        ),
        candidates=candidate_records,
    )


def _ancestor_to_result(
    surface_form: str, ancestor: DictionaryEntry
) -> LookupResult:
    return LookupResult(
        surface_form=surface_form,
        path="ancestor",
        canonical_iri=ancestor.canonical_iri,
        canonical_label=ancestor.canonical_label,
        canonical_ontology=ancestor.ontology.value,
        confidence=round(ancestor.confidence * 0.9, 4),
        resolution_status=(
            ResolutionStatus.ID_ANCHORED
            if ancestor.confidence == 1.0
            else ResolutionStatus.OLS_FUZZY
        ),
        synonyms=ancestor.synonyms,
        evidence=(
            f"NCBITaxon ancestor match; queried={surface_form!r}; "
            f"ancestor_iri={ancestor.canonical_iri}; "
            f"dictionary_version={ancestor.ontology_version}"
        ),
    )
