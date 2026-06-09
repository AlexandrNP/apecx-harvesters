"""SC-B2 (2026-06-08) — per-source extractors that yield
``(surface_form, taxon_id)`` pairs from parsed DataCite containers.

Lives separately from :mod:`corpus_mining` so the accumulator
foundation stays free of parser-model imports. Extractors here import
the per-source models directly because they need to read source-specific
fields (``Pathogen``, ``Organism``, ``Genome_Name``, etc.) that live
on the source-specific sub-containers.

Coverage in v1 (the four sources with clean ``(surface, taxon)`` signal):

- ``violin_pathogen``: ``Pathogen`` + ``NCBI_Taxonomy_ID``
- ``bvbrc_epitope``: ``Organism`` + ``Taxon_ID``
- ``bvbrc_genome``: ``Genome_Name`` + species-extracted ``NCBI_Taxon_ID``
  from each nested GenomeEntry. The taxon id may carry a strain
  suffix in BVBRC's ``species.strain`` convention (e.g. ``37124.6497``);
  we split on the first dot to extract the species level. Strain-level
  ``Genome_Name`` surface forms are caught by the SC-B5 strain filter
  in :mod:`corpus_mining` (not here).
- ``bvbrc_protein_structure``: ``Organism_Name`` + each
  ``Taxon_ID`` from each ``PublicationStructureGroup`` (list-typed
  because protein structures may be associated with multiple taxa,
  e.g., a virus-host complex).

Out of v1 scope (lower signal-to-noise; will revisit in SC-B2b):

- ``violin_vaccine``: vaccines link to pathogens by VIOLIN-internal
  ``pathogen_id``, not by NCBI taxon. Mining would require a
  cross-walk through ``violin_pathogen``.
- ``violin_gene``: ``NCBI_Gene_ID`` is a gene id, not a taxon id.
  Cross-walk through NCBI Gene → taxon would add a network dep.
- ``bvbrc_protein``: surface form is ``Genome`` (organism name),
  but the model only carries strain-level Genome_IDs nested below
  with no clean species-level taxon. Same strain-suffix split would
  work but I want to ship v1 against the four cleanest sources first.
- ``protabank``, ``pdb``, ``antiviraldb``: less clear surface_form /
  taxon_id mapping; left for v2.

When invoked by ``harmonize_index``, each extractor is fed the parsed
container (a ``DataCite`` subclass) and yields zero or more
``(surface_form, taxon_id)`` pairs. The accumulator applies its noise
filter (SC-B5) on every observation, so extractors don't need to
pre-filter.
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
    """Split BVBRC's species.strain convention to the species component.

    BVBRC genomes carry NCBI_Taxon_ID strings shaped like ``"37124.6497"``
    where ``37124`` is the species and ``6497`` is BVBRC's strain serial.
    For mining, we want the species id — that's what users type. Strain-
    level ids would pollute the surface→taxon mapping.

    Accepts int (already-clean), str with or without dot, or None.
    Returns the species int or None if uncoercible.
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    text = str(raw).strip()
    if not text:
        return None
    # First dot-separated component, then int-coerce.
    head = text.split(".", 1)[0]
    try:
        species = int(head)
    except ValueError:
        return None
    return species if species > 0 else None


def extract_violin_pathogen(
    container: VIOLINPathogenContainer,
) -> Iterable[tuple[str, int]]:
    """Yield ``(Pathogen, NCBI_Taxonomy_ID)`` for one VIOLIN pathogen container.

    The cleanest mineable source: each container has exactly one
    pathogen string + one taxon id. ~3000 records in the production
    VIOLIN snapshot.
    """
    fields = container.violin_pathogen
    if fields.Pathogen and fields.NCBI_Taxonomy_ID is not None:
        yield fields.Pathogen, int(fields.NCBI_Taxonomy_ID)


def extract_bvbrc_epitope(
    container: BVBRCEpitopeContainer,
) -> Iterable[tuple[str, int]]:
    """Yield ``(Organism, Taxon_ID)`` for one BVBRC epitope container.

    Container granularity is one organism per record. Surface form is
    typically the curated species name; taxon id is the species-level
    NCBI taxon.
    """
    fields = container.bvbrc_epitope
    if fields.Organism and fields.Taxon_ID is not None:
        yield fields.Organism, int(fields.Taxon_ID)


def extract_bvbrc_genome(
    container: BVBRCGenomeContainer,
) -> Iterable[tuple[str, int]]:
    """Yield ``(Genome_Name, species_taxon)`` per nested genome entry.

    BVBRC stores per-genome NCBI_Taxon_ID at the strain level
    (e.g., ``"37124.6497"``). The species component is what we mine;
    the surface form (``Genome_Name``) is the organism name at the
    container level. Each container has 1..N genome entries; we dedupe
    species-level taxa within the container so the accumulator sees one
    observation per unique species id, not one per strain assembly.

    Strain-level surface forms (e.g., "Influenza A virus (A/common
    pochard/.../H5N1)") will be caught by the SC-B5 strain filter at
    the accumulator level; this extractor doesn't pre-filter.
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
    """Yield ``(Organism_Name, taxon_id)`` for each taxon in each group.

    Protein-structure entries can be associated with multiple taxa
    (virus-host complexes, multi-organism crystals). We emit one
    observation per (organism_name, taxon) pair, deduped within the
    container.
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

# Prose fields on VIOLIN Pathogen records that may carry introduced
# acronyms. Order doesn't matter — the miner walks each in turn.
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
    """SC-B8: mine parenthetically-introduced acronyms from VIOLIN prose.

    For each VIOLIN Pathogen container, walk every prose field listed in
    :data:`_VIOLIN_PROSE_FIELDS` and extract acronym tokens that satisfy
    both precision guards (phrase-overlaps-pathogen AND initialism-of-
    phrase — see :func:`extract_parenthetical_acronyms`). Each accepted
    acronym is observed against the record's NCBI Taxonomy ID under
    ``source`` (default ``"violin_pathogen_acronym"``) so SC-B3's
    conflict-surfacing layer can distinguish prose-mined acronyms from
    canonical surface-form observations.

    Returns counters:

    - ``records_scanned`` — VIOLIN containers processed
    - ``records_with_taxon`` — containers with a usable NCBI Taxonomy ID
    - ``acronyms_observed`` — total (acronym, taxon) pairs emitted
    - ``observe_rejected`` — rejected by the accumulator's filter

    There is NO frequency floor here: VIOLIN Pathogen carries one record
    per species, so a count-based floor would discard every observation.
    The initialism-match guard is the precision floor for this miner.
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
    """SC-B7: frequency-mine strain-prefix acronyms from BVBRC genomes.

    Two-pass over ``containers``: pass 1 counts ``(acronym, species_taxon)``
    co-occurrences and per-species record totals; pass 2 emits each
    qualifying acronym as an ``accumulator.observe`` call against the
    species taxon. An acronym qualifies when its per-species record count
    is at least ``min_count`` AND at least ``min_fraction`` of that species'
    record count — the all-automatic precision filter (no hardcoded list).

    Returns counters:

    - ``acronyms_proposed`` — distinct ``(acronym, species)`` pairs seen
    - ``acronyms_accepted`` — pairs that passed both thresholds
    - ``acronyms_rejected_by_count`` — failed the absolute floor
    - ``acronyms_rejected_by_fraction`` — failed the per-species fraction
    - ``species_observed`` — number of distinct species seen

    Source default ``"bvbrc_genome_acronym"`` differs from the standard
    ``"bvbrc_genome"`` so the SC-B3 conflict layer can distinguish
    acronym-mined synonyms from full-name observations.
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


# Source-name → extractor function. ``harmonize_index`` looks up by the
# source-name from ``SOURCE_REGISTRY`` (e.g., ``"violin_pathogen"``).
# Sources not registered here are skipped silently — mining is opt-in
# per source, and adding a new extractor is purely additive.
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
