"""Resolve organism slots on a parsed DataCite record to canonical IRIs.

Wraps :func:`apecx_harvesters.dict_reader.lookup_entity` and writes
zero-or-more :class:`Subject` entries into ``record.subjects`` so the
re-ingest path produces records with populated ``subjects.valueUri`` —
the uniform cross-source query facet Globus auto-indexes.

The adapter is per-source: each registered source declares which sub-
container fields carry organism surface forms, and how multi-entity
records map to multiple Subjects.

Honors ambiguity per the dictionary contract: when a surface form
resolves to >= 2 candidate IRIs the adapter writes ONE Subject per
candidate (downstream consumers see the full set in the index); when
the resolution path is ``miss`` the record passes through unchanged.

I/O-free at construction. The first ``resolve_one`` call materializes
the dictionary singleton via the dict_reader bootstrap.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from apecx_harvesters.dict_reader import (
    EntityType,
    LookupCandidate,
    LookupResult,
    lookup_entity,
)
from apecx_harvesters.loaders.base import DataCite, Subject

log = logging.getLogger(__name__)

# Maps the dict_reader's ontology short-code to a (scheme_name, scheme_uri)
# pair for the DataCite Subject. Kept in this adapter rather than the
# dictionary so the scheme labels can drift without rebuilding the dict.
_ONTOLOGY_SCHEME: dict[str, tuple[str, str]] = {
    "NCBITaxon": (
        "NCBI Taxonomy",
        "http://purl.obolibrary.org/obo/ncbitaxon.owl",
    ),
    "VO": (
        "Vaccine Ontology",
        "http://purl.obolibrary.org/obo/vo.owl",
    ),
    "DOID": (
        "Disease Ontology",
        "http://purl.obolibrary.org/obo/doid.owl",
    ),
    "NCBIGene": (
        "NCBI Gene",
        "https://www.ncbi.nlm.nih.gov/gene",
    ),
}


# DataCite alternateIdentifierType → ontology code for the dual-stamp pass.
# A harmonized record carries the source-stamped taxon id here (e.g. BVBRC
# stamps NCBITaxon 11320 on Influenza A genomes even though their Species
# was renamed to "Alphainfluenzavirus influenzae", which the dict resolves
# to 2955291). Stamping BOTH ids as subjects.valueUri keeps the eventual
# single-clause subjects.valueUri filter as broad as the Pass 1 union.
_ALTID_TYPE_TO_ONTOLOGY: dict[str, str] = {
    "NCBI-Taxonomy": "NCBITaxon",
}

_NCBITAXON_IRI_PREFIX = "http://purl.obolibrary.org/obo/NCBITaxon_"


def _altid_to_iri(ontology: str, value: str) -> str | None:
    """Build the canonical IRI for a source-stamped alternate identifier."""
    if ontology == "NCBITaxon" and value.isdigit():
        return f"{_NCBITAXON_IRI_PREFIX}{value}"
    return None


@dataclass(frozen=True)
class OrganismSlot:
    """One organism slot on a source's DataCite container.

    Attributes
    ----------
    ext_field:
        Name of the sub-container attribute (``"violin_pathogen"``,
        ``"bvbrc_genome"``, …) on the DataCite record.
    surface_attr:
        Attribute on the sub-container carrying the surface form (e.g.
        ``"Pathogen"`` on VIOLIN, ``"Organism"`` on BVBRC epitope).
    entity_type:
        What to look up against; usually PATHOGEN.
    """

    ext_field: str
    surface_attr: str
    entity_type: EntityType = EntityType.PATHOGEN


# Per-source organism slot map. Sources absent here pass through with
# subjects unchanged (e.g. ProtaBank's UniProt-primary slot is out of
# scope until a UniProt resolver lands).
_SOURCE_SLOTS: dict[str, tuple[OrganismSlot, ...]] = {
    "violin_pathogen": (
        OrganismSlot("violin_pathogen", "Pathogen"),
    ),
    "violin_vaccine": (
        # Vaccines link to pathogens by VIOLIN-internal pathogen_id, not
        # by NCBI taxon, so the surface form here is the vaccine name
        # itself — only the VO slot gets resolved cleanly at the dict
        # level. Pathogen-side resolution can be added once the
        # VIOLIN-internal cross-walk is loaded.
        OrganismSlot("violin_vaccine", "Vaccine"),
    ),
    "violin_gene": (
        OrganismSlot("violin_gene", "Organism"),
    ),
    "bvbrc_epitope": (
        OrganismSlot("bvbrc_epitope", "Organism"),
    ),
    "bvbrc_genome": (
        OrganismSlot("bvbrc_genome", "Species"),
    ),
    "bvbrc_protein_structure": (
        OrganismSlot("bvbrc_protein_structure", "Organism_Name"),
    ),
    "bvbrc_protein": (
        OrganismSlot("bvbrc_protein", "Genome"),
    ),
    "antiviraldb": (
        # AntiviralDB's Virus field is the pathogen surface form. Drug
        # resolution (ChEBI) is deferred to a later phase.
        OrganismSlot("antiviraldb", "Virus"),
    ),
}


def _candidate_to_subject(
    surface: str, cand: LookupCandidate
) -> Subject | None:
    """Project one :class:`LookupCandidate` into a :class:`Subject`."""
    scheme = _ONTOLOGY_SCHEME.get(cand.canonical_ontology)
    if scheme is None:
        log.debug(
            "no scheme map for ontology %r — skipping",
            cand.canonical_ontology,
        )
        return None
    name, scheme_uri = scheme
    return Subject(
        subject=cand.canonical_label or surface,
        subjectScheme=name,
        schemeUri=scheme_uri,
        valueUri=cand.canonical_iri,
    )


def _result_to_subjects(
    surface: str, result: LookupResult
) -> list[Subject]:
    """Project a :class:`LookupResult` into 0..N Subject entries.

    - ``miss`` / no canonical → empty list (record passes through).
    - ``fast`` / ``fuzzy_resolved`` / ``ancestor`` → one Subject.
    - ``ambiguous`` → one Subject per candidate so all canonical IRIs
      reach the index. Downstream advanced-filter queries on
      ``subjects.valueUri`` then match any of the candidates.
    """
    if result.canonical_iri and not result.candidates:
        scheme = _ONTOLOGY_SCHEME.get(result.canonical_ontology or "")
        if scheme is None:
            return []
        name, scheme_uri = scheme
        return [
            Subject(
                subject=result.canonical_label or surface,
                subjectScheme=name,
                schemeUri=scheme_uri,
                valueUri=result.canonical_iri,
            )
        ]
    subjects: list[Subject] = []
    for cand in result.candidates:
        s = _candidate_to_subject(surface, cand)
        if s is not None:
            subjects.append(s)
    return subjects


def _read_ext_attr(record: DataCite, ext_field: str, attr: str) -> Any:
    """Extract ``record.<ext_field>.<attr>`` if both exist; else None."""
    ext = getattr(record, ext_field, None)
    if ext is None:
        return None
    return getattr(ext, attr, None)


@dataclass
class ResolveStats:
    """Per-record resolve counters. Aggregated by the caller across a run."""

    records_seen: int = 0
    surfaces_attempted: int = 0
    subjects_emitted: int = 0
    ambiguous_records: int = 0
    miss_records: int = 0


def make_resolver_for_source(
    source_name: str,
) -> Callable[[DataCite], DataCite]:
    """Return a resolver callable for ``source_name``.

    The returned callable is fed one parsed DataCite record at a time;
    it returns a new record (same type, ``model_copy``) with
    ``record.subjects`` extended by zero-or-more Subject entries per
    organism slot configured for the source. Records for sources with
    no registered slot pass through unchanged.

    Construction is fast (no SQLite hit); the dictionary singleton is
    lazily materialized on the first ``lookup_entity`` call.
    """
    slots = _SOURCE_SLOTS.get(source_name, ())
    if not slots:
        log.info(
            "no organism slots registered for source %r — pass-through",
            source_name,
        )

    def resolve(record: DataCite) -> DataCite:
        if not slots:
            return record
        new_subjects: list[Subject] = list(record.subjects or [])
        any_hit = False
        for slot in slots:
            surface = _read_ext_attr(record, slot.ext_field, slot.surface_attr)
            if not isinstance(surface, str) or not surface.strip():
                continue
            result = lookup_entity(surface.strip(), entity_type=slot.entity_type)
            projected = _result_to_subjects(surface.strip(), result)
            if not projected:
                continue
            any_hit = True
            for s in projected:
                if not _subject_already_present(new_subjects, s.valueUri):
                    new_subjects.append(s)

        # P2-0 DUAL-STAMP: also carry the record's source-stamped taxon ids
        # forward as subjects. This keeps the eventual single-clause
        # subjects.valueUri filter as broad as the Pass 1 alt-id ∪ label
        # union — a record stamped 11320 but whose Species resolves to
        # 2955291 ends up with BOTH IRIs, so either query matches.
        dual = _dual_stamp_subjects(record)
        for s in dual:
            if not _subject_already_present(new_subjects, s.valueUri):
                new_subjects.append(s)
                any_hit = True

        if not any_hit:
            # No subject resolved on any slot; pass record through to keep
            # caller-side counters accurate. The record still re-ingests.
            return record
        # ``model_copy`` preserves the record's private subject-keyed attrs
        # (BVBRC:Genome relies on these for canonical_uri stability).
        return record.model_copy(update={"subjects": new_subjects})

    resolve.__source_name__ = source_name  # type: ignore[attr-defined]
    return resolve


def _dual_stamp_subjects(record: DataCite) -> list[Subject]:
    """Project the record's source-stamped alternate identifiers into Subjects.

    Reads ``record.alternateIdentifiers`` for types in
    :data:`_ALTID_TYPE_TO_ONTOLOGY` (currently NCBI-Taxonomy) and emits a
    Subject per recognised id. The source-stamped id is authoritative for
    the records the source actually carries, independent of how the
    dictionary re-resolves the current Species name.
    """
    out: list[Subject] = []
    seen: set[str] = set()
    for alt in record.alternateIdentifiers or []:
        ontology = _ALTID_TYPE_TO_ONTOLOGY.get(alt.alternateIdentifierType or "")
        if ontology is None:
            continue
        value = (alt.alternateIdentifier or "").strip()
        iri = _altid_to_iri(ontology, value)
        if iri is None or iri in seen:
            continue
        scheme = _ONTOLOGY_SCHEME.get(ontology)
        if scheme is None:
            continue
        seen.add(iri)
        name, scheme_uri = scheme
        out.append(
            Subject(
                subject=value,
                subjectScheme=name,
                schemeUri=scheme_uri,
                valueUri=iri,
            )
        )
    return out


def _subject_already_present(
    subjects: Iterable[Subject], iri: str | None
) -> bool:
    """Avoid duplicate Subject rows on idempotent re-runs.

    Two Subjects are considered identical when they share ``valueUri``.
    Keeps re-runs of the republish pass deterministic: a record already
    carrying ``NCBITaxon:11320`` won't gain a second copy.
    """
    if iri is None:
        return False
    return any(s.valueUri == iri for s in subjects)


__all__ = [
    "OrganismSlot",
    "ResolveStats",
    "make_resolver_for_source",
]
