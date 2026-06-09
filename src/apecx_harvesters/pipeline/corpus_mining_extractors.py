"""Per-source extractors that yield ``(surface_form, taxon_id)`` pairs
from parsed DataCite containers.

Lives separately from :mod:`corpus_mining` so the accumulator stays free
of parser-model imports. Extractors import per-source models directly
because they read source-specific fields (``Pathogen``, ``Organism``,
``Genome_Name``, etc.) on the source-specific sub-containers.

v1 coverage — the four sources with clean ``(surface, taxon)`` signal:

- ``violin_pathogen``        — ``Pathogen`` + ``NCBI_Taxonomy_ID``
- ``bvbrc_epitope``          — ``Organism`` + ``Taxon_ID``
- ``bvbrc_genome``           — ``Genome_Name`` + species-level taxon
                               extracted from each nested GenomeEntry
                               (BVBRC's ``species.strain`` convention
                               e.g. ``37124.6497``)
- ``bvbrc_protein_structure`` — ``Organism_Name`` + each ``Taxon_ID``
                                from each PublicationStructureGroup
                                (list-typed: virus-host complexes)

When invoked by ``harmonize_index``, each extractor is fed the parsed
container and yields zero or more ``(surface_form, taxon_id)`` pairs.
The accumulator applies its noise + strain filter on every observation,
so extractors don't pre-filter.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from apecx_harvesters.loaders.base import DataCite
from apecx_harvesters.loaders.bvbrc_epitope import BVBRCEpitopeContainer
from apecx_harvesters.loaders.bvbrc_genome import BVBRCGenomeContainer
from apecx_harvesters.loaders.bvbrc_protein_structure import (
    BVBRCProteinStructureContainer,
)
from apecx_harvesters.loaders.violin_pathogen import VIOLINPathogenContainer
from apecx_harvesters.pipeline.corpus_mining import (
    MinedSynonymAccumulator,
    extract_parenthetical_acronyms,
    extract_strain_prefix_acronyms,
)


def _species_from_bvbrc_taxon(raw: str | int | None) -> int | None:
    """Split BVBRC's ``species.strain`` convention to the species component.

    Genomes carry NCBI_Taxon_ID strings shaped like ``"37124.6497"``
    where ``37124`` is the species and ``6497`` is BVBRC's strain serial.
    Mining keys on the species id — that's what users type. Returns None
    on uncoercible / non-positive input.
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    text = str(raw).strip()
    if not text:
        return None
    head = text.split(".", 1)[0]
    try:
        species = int(head)
    except ValueError:
        return None
    return species if species > 0 else None


def extract_violin_pathogen(
    container: VIOLINPathogenContainer,
) -> Iterable[tuple[str, int]]:
    """``(Pathogen, NCBI_Taxonomy_ID)`` for one VIOLIN pathogen container.

    Cleanest mineable source: exactly one pathogen string + one taxon id
    per container.
    """
    fields = container.violin_pathogen
    if fields.Pathogen and fields.NCBI_Taxonomy_ID is not None:
        yield fields.Pathogen, int(fields.NCBI_Taxonomy_ID)


def extract_bvbrc_epitope(
    container: BVBRCEpitopeContainer,
) -> Iterable[tuple[str, int]]:
    """``(Organism, Taxon_ID)`` for one BVBRC epitope container.

    Container granularity is one organism per record; taxon id is the
    species-level NCBI taxon.
    """
    fields = container.bvbrc_epitope
    if fields.Organism and fields.Taxon_ID is not None:
        yield fields.Organism, int(fields.Taxon_ID)


def extract_bvbrc_genome(
    container: BVBRCGenomeContainer,
) -> Iterable[tuple[str, int]]:
    """``(Genome_Name, species_taxon)`` per nested genome entry.

    Per-genome NCBI_Taxon_ID is at the strain level (``"37124.6497"``);
    the species component is what we mine. 1..N genome entries per
    container; we dedupe species-level taxa within the container so the
    accumulator sees one observation per unique species, not one per
    strain assembly. Strain-level surface forms are caught by the
    strain filter at the accumulator level.
    """
    fields = container.bvbrc_genome
    name = fields.Genome_Name
    if not name:
        return
    seen_species: set[int] = set()
    for genome in fields.Genome:
        species = _species_from_bvbrc_taxon(genome.NCBI_Taxon_ID)
        if species is None or species in seen_species:
            continue
        seen_species.add(species)
        yield name, species


def extract_bvbrc_protein_structure(
    container: BVBRCProteinStructureContainer,
) -> Iterable[tuple[str, int]]:
    """``(Organism_Name, taxon_id)`` for each taxon in each group.

    Protein-structure entries may be associated with multiple taxa
    (virus-host complexes); emit one observation per (name, taxon) pair,
    deduped within the container.
    """
    fields = container.bvbrc_protein_structure
    name = fields.Organism_Name
    if not name:
        return
    seen: set[int] = set()
    for group in fields.Publication_and_Protein_Structure:
        for tid in group.Taxon_ID:
            tid_int = int(tid) if tid else 0
            if tid_int <= 0 or tid_int in seen:
                continue
            seen.add(tid_int)
            yield name, tid_int


_ACRONYM_SOURCE = "bvbrc_genome_acronym"
_VIOLIN_ACRONYM_SOURCE = "violin_pathogen_acronym"

# VIOLIN Pathogen prose fields that may carry introduced acronyms.
_VIOLIN_PROSE_FIELDS: tuple[str, ...] = (
    "Pathogen_Description",
    "Microbial_Pathogenesis",
    "Host_Ranges_and_Animal_Models",
    "Host_Protective_Immunity",
)


def mine_violin_pathogen_acronyms(
    containers: Iterable[VIOLINPathogenContainer],
    *,
    accumulator: MinedSynonymAccumulator,
    source: str = _VIOLIN_ACRONYM_SOURCE,
) -> dict[str, int]:
    """Mine parenthetically-introduced acronyms from VIOLIN prose fields.

    Walk every prose field per container; extract acronyms satisfying
    both precision guards in :func:`extract_parenthetical_acronyms`
    (phrase-overlaps-pathogen AND initialism-of-phrase). Each accepted
    acronym is observed against the record's NCBI Taxonomy ID under
    ``source`` (default distinguishes prose-mined acronyms from canonical
    surface-form observations).

    No frequency floor: VIOLIN Pathogen has one record per species, so a
    count floor would discard everything. The initialism guard is the
    precision floor.

    Returns: ``records_scanned``, ``records_with_taxon``,
    ``acronyms_observed``, ``observe_rejected``.
    """
    stats = {
        "records_scanned": 0,
        "records_with_taxon": 0,
        "acronyms_observed": 0,
        "observe_rejected": 0,
    }
    for container in containers:
        stats["records_scanned"] += 1
        fields = container.violin_pathogen
        pathogen = (fields.Pathogen or "").strip()
        taxon = fields.NCBI_Taxonomy_ID
        if not pathogen or taxon is None:
            continue
        try:
            taxon_int = int(taxon)
        except (TypeError, ValueError):
            continue
        if taxon_int <= 0:
            continue
        stats["records_with_taxon"] += 1

        seen_in_record: set[str] = set()
        for field_name in _VIOLIN_PROSE_FIELDS:
            text = getattr(fields, field_name, None)
            if not text:
                continue
            for acr in extract_parenthetical_acronyms(text, pathogen=pathogen):
                if acr in seen_in_record:
                    continue
                seen_in_record.add(acr)
                accepted = accumulator.observe(
                    acr, taxon_int, source=source
                )
                if accepted:
                    stats["acronyms_observed"] += 1
                else:
                    stats["observe_rejected"] += 1

    return stats


def mine_bvbrc_strain_prefix_acronyms(
    containers: Iterable[BVBRCGenomeContainer],
    *,
    accumulator: MinedSynonymAccumulator,
    source: str = _ACRONYM_SOURCE,
    min_count: int = 10,
    min_fraction: float = 0.05,
) -> dict[str, int]:
    """Frequency-mine strain-prefix acronyms from BVBRC genomes.

    Two-pass: count ``(acronym, species_taxon)`` co-occurrences and
    per-species record totals; emit each qualifying acronym as an
    ``observe`` call. An acronym qualifies when per-species record
    count is >= ``min_count`` AND >= ``min_fraction`` of that species'
    record count — all-automatic precision filter (no hardcoded list).

    Default source name distinguishes acronym-mined synonyms from
    full-name observations for downstream conflict surfacing.

    Returns: ``acronyms_proposed``, ``acronyms_accepted``,
    ``acronyms_rejected_by_count``, ``acronyms_rejected_by_fraction``,
    ``species_observed``.
    """
    acronym_per_species: dict[tuple[str, int], int] = defaultdict(int)
    species_record_counts: dict[int, int] = defaultdict(int)
    species_name_by_taxon: dict[int, str] = {}

    materialized: list[BVBRCGenomeContainer] = list(containers)

    for container in materialized:
        fields = container.bvbrc_genome
        name = fields.Genome_Name
        species_name = fields.Species
        if not name:
            continue

        species_taxon: int | None = None
        for genome in fields.Genome:
            t = _species_from_bvbrc_taxon(genome.NCBI_Taxon_ID)
            if t is not None:
                species_taxon = t
                break
        if species_taxon is None:
            continue
        species_record_counts[species_taxon] += 1
        if species_name and species_taxon not in species_name_by_taxon:
            species_name_by_taxon[species_taxon] = species_name

        acronyms = extract_strain_prefix_acronyms(name, species=species_name)
        for acr in set(acronyms):
            acronym_per_species[(acr, species_taxon)] += 1

    stats = {
        "acronyms_proposed": len(acronym_per_species),
        "acronyms_accepted": 0,
        "acronyms_rejected_by_count": 0,
        "acronyms_rejected_by_fraction": 0,
        "species_observed": len(species_record_counts),
    }

    for (acr, species_taxon), count in acronym_per_species.items():
        total = species_record_counts.get(species_taxon, 0)
        if total <= 0:
            continue
        if count < min_count:
            stats["acronyms_rejected_by_count"] += 1
            continue
        if count / total < min_fraction:
            stats["acronyms_rejected_by_fraction"] += 1
            continue
        species_name = species_name_by_taxon.get(species_taxon, "")
        if acr.lower() in species_name.lower():
            continue
        accumulator.observe(acr, species_taxon, source=source)
        stats["acronyms_accepted"] += 1

    return stats


# Source-name → extractor. ``harmonize_index`` looks up by source-name
# from ``SOURCE_REGISTRY``. Sources not registered here are skipped
# silently — mining is opt-in per source.
SOURCE_MINING_EXTRACTORS: dict[
    str, Callable[[DataCite], Iterable[tuple[str, int]]]
] = {
    "violin_pathogen": extract_violin_pathogen,  # type: ignore[dict-item]
    "bvbrc_epitope": extract_bvbrc_epitope,  # type: ignore[dict-item]
    "bvbrc_genome": extract_bvbrc_genome,  # type: ignore[dict-item]
    "bvbrc_protein_structure": extract_bvbrc_protein_structure,  # type: ignore[dict-item]
}


__all__ = [
    "SOURCE_MINING_EXTRACTORS",
    "extract_violin_pathogen",
    "extract_bvbrc_epitope",
    "extract_bvbrc_genome",
    "extract_bvbrc_protein_structure",
    "mine_bvbrc_strain_prefix_acronyms",
    "mine_violin_pathogen_acronyms",
]
