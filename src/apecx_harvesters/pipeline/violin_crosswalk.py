"""VIOLIN cross-table linking: vaccine → pathogen → NCBI taxon.

VIOLIN is fragmented across three Globus indices (Pathogen, Vaccine, Gene).
Only the Pathogen table carries ``NCBI_Taxonomy_ID``; Vaccine records link
to their target pathogen(s) by an internal ``pathogen_id`` but don't carry
the taxon themselves. Joining them before re-ingest lets a vaccine inherit
its pathogen's taxon — at the cost of duplicating that taxon onto every
vaccine targeting the pathogen (the intended denormalisation: a single-clause
``subjects.valueUri`` read instead of a runtime join).

The cross-walk is ``{pathogen.pathogen_id: NCBI_Taxonomy_ID}`` built once
from the VIOLIN:Pathogen DEST index (217 records). Pathogens with no taxon
(``NCBI_Taxonomy_ID is None`` — VIOLIN's non-infectious / cancer catch-all
pathogen rows) are omitted, so vaccines targeting them get no taxon subject
(correct — they are not organism-anchored).

Genes do NOT participate: VIOLIN:Gene records carry no ``pathogen_id`` link
(only an ``Organism`` name, resolved by the standard slot path).
"""

from __future__ import annotations

import logging

import globus_sdk

from apecx_harvesters.loaders.base import Subject
from apecx_harvesters.pipeline.globus_source import scroll_index_records

log = logging.getLogger(__name__)

_NCBITAXON_IRI_PREFIX = "http://purl.obolibrary.org/obo/NCBITaxon_"
_NCBITAXON_SCHEME = ("NCBI Taxonomy", "http://purl.obolibrary.org/obo/ncbitaxon.owl")

# DEST index UUID for VIOLIN:Pathogen (the table holding the taxon).
VIOLIN_PATHOGEN_DEST = "b4965a61-e6de-4e8b-b312-7ab37c7c39d3"


async def build_violin_pathogen_crosswalk(
    client: globus_sdk.SearchClient,
    *,
    dest_uuid: str = VIOLIN_PATHOGEN_DEST,
    page_size: int = 500,
) -> dict[int, int]:
    """Scroll VIOLIN:Pathogen DEST → ``{pathogen_id: NCBI_Taxonomy_ID}``.

    Omits pathogens with no taxon. Cheap (217 records).
    """
    xwalk: dict[int, int] = {}
    async for rec in scroll_index_records(
        dest_uuid, client=client, query="*", page_size=page_size
    ):
        ext = (rec.get("content") or {}).get("violin_pathogen") or {}
        pid = ext.get("pathogen_id")
        tax = ext.get("NCBI_Taxonomy_ID")
        if isinstance(pid, int) and isinstance(tax, int) and tax > 0:
            xwalk[pid] = tax
    log.info("VIOLIN pathogen cross-walk: %d pathogen_id → taxon entries", len(xwalk))
    return xwalk


def violin_vaccine_crosswalk_subjects(
    record, crosswalk: dict[int, int]
) -> list[Subject]:
    """Subjects for a VIOLIN:Vaccine record's target pathogens via the cross-walk.

    Reads ``record.violin_vaccine.pathogen_id`` (a list), maps each through
    ``crosswalk`` to a taxon, and emits one NCBI-Taxonomy Subject per distinct
    resolved taxon. Multi-pathogen vaccines yield multiple subjects.
    """
    ext = getattr(record, "violin_vaccine", None)
    if ext is None:
        return []
    pathogen_ids = getattr(ext, "pathogen_id", None) or []
    name, scheme_uri = _NCBITAXON_SCHEME
    out: list[Subject] = []
    seen: set[str] = set()
    for pid in pathogen_ids:
        tax = crosswalk.get(pid)
        if tax is None:
            continue
        iri = f"{_NCBITAXON_IRI_PREFIX}{tax}"
        if iri in seen:
            continue
        seen.add(iri)
        out.append(
            Subject(
                subject=str(tax),
                subjectScheme=name,
                schemeUri=scheme_uri,
                valueUri=iri,
            )
        )
    return out


__all__ = [
    "VIOLIN_PATHOGEN_DEST",
    "build_violin_pathogen_crosswalk",
    "violin_vaccine_crosswalk_subjects",
]
