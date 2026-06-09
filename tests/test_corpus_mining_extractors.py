"""SC-B2 + SC-B5 (2026-06-08) — per-source extractors + strain filter.

Tests run against synthetic containers (no Globus / network) so they
verify the extractor → accumulator pipeline shape without external
dependencies.
"""

from __future__ import annotations

import pytest

from apecx_harvesters.loaders.base import Publisher
from apecx_harvesters.loaders.bvbrc_epitope import (
    BVBRCEpitopeContainer,
    BVBRCEpitopeFields,
)
from apecx_harvesters.loaders.bvbrc_genome import (
    BVBRCGenomeContainer,
    BVBRCGenomeFields,
)
from apecx_harvesters.loaders.bvbrc_genome.model import GenomeEntry
from apecx_harvesters.loaders.bvbrc_protein_structure import (
    BVBRCProteinStructureContainer,
    BVBRCProteinStructureFields,
)
from apecx_harvesters.loaders.bvbrc_protein_structure.model import (
    PublicationStructureGroup,
)
from apecx_harvesters.loaders.violin_pathogen import (
    VIOLINPathogenContainer,
    ViolinPathogenFields,
)
from apecx_harvesters.pipeline.corpus_mining import (
    MinedSynonymAccumulator,
    extract_parenthetical_acronyms,
    extract_strain_prefix_acronyms,
    is_strain_level,
)
from apecx_harvesters.pipeline.corpus_mining_extractors import (
    SOURCE_MINING_EXTRACTORS,
    _species_from_bvbrc_taxon,
    extract_bvbrc_epitope,
    extract_bvbrc_genome,
    extract_bvbrc_protein_structure,
    extract_violin_pathogen,
    mine_bvbrc_strain_prefix_acronyms,
    mine_violin_pathogen_acronyms,
)


# ---------------------------------------------------------------------------
# SC-B5 — strain-level filter (unit-level: pure heuristic)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "surface",
    [
        "Influenza A virus (A/common pochard/Shanxi/16B/2015(H5N1))",
        "Influenza A virus (A/whooper swan/Henan/09L/2015(H5N1))",
        "Human alphaherpesvirus 1 strain KOS",
        "Porcine reproductive and respiratory syndrome virus isolate 14LY01-FJ",
        "Streptococcus pneumoniae subsp. pneumoniae",
        "KP715069 Missing Influenza A virus (A/common pochard/...)",
        "A/duck/Hong Kong/8d/2020",  # flu-style at very start
        "Some virus, AB123456",  # comma + accession list
    ],
)
def test_is_strain_level_catches_known_strain_shapes(surface: str) -> None:
    assert is_strain_level(surface), (
        f"{surface!r} should be flagged as strain-level"
    )


@pytest.mark.parametrize(
    "surface",
    [
        "EEEV",
        "Eastern equine encephalitis virus",
        "Severe acute respiratory syndrome coronavirus 2",
        "Zika virus",
        "Marburg marburgvirus",
        "Orthohepacivirus hominis",
        "Hepatitis C virus",
        "Coronaviridae",  # family name
        "Alphavirus",
    ],
)
def test_is_strain_level_does_not_flag_real_names(surface: str) -> None:
    assert not is_strain_level(surface), (
        f"{surface!r} should NOT be flagged as strain-level"
    )


def test_accumulator_drops_strain_observations() -> None:
    """SC-B5 wired into the accumulator: strain surfaces never land."""
    acc = MinedSynonymAccumulator()
    real_accepted = acc.observe("EEEV", 11021, source="violin_pathogen")
    strain_rejected = not acc.observe(
        "Influenza A virus (A/common pochard/Shanxi/16B/2015(H5N1))",
        2697049,
        source="bvbrc_genome",
    )
    assert real_accepted
    assert strain_rejected
    assert acc.unique_pair_count() == 1
    # Rejection counter incremented.
    assert acc.per_source_stats()["bvbrc_genome"]["rejected"] == 1


# ---------------------------------------------------------------------------
# SC-B2 — species-id extraction from BVBRC's species.strain notation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("37124.6497", 37124),
        ("11021", 11021),
        (11021, 11021),
        ("11021.", 11021),
        (".6497", None),
        ("0", None),
        ("0.6497", None),
        (None, None),
        ("", None),
        ("not-an-int", None),
        ("  37124  ", 37124),  # whitespace tolerated
        ("37124.6497.x", 37124),  # multi-dot, first wins
    ],
)
def test_species_from_bvbrc_taxon_extracts_first_component(
    raw, expected
) -> None:
    assert _species_from_bvbrc_taxon(raw) == expected


# ---------------------------------------------------------------------------
# SC-B2 — per-source extractors
# ---------------------------------------------------------------------------


def _make_pathogen(pathogen: str, taxon: int | None = 11021) -> VIOLINPathogenContainer:
    fields = ViolinPathogenFields(
        id=1, VIOLIN_c_pathogen_id=1, Pathogen=pathogen, NCBI_Taxonomy_ID=taxon
    )
    return VIOLINPathogenContainer.new(
        title=pathogen,
        description=None,
        creators=[],
        publisher=Publisher(name="VIOLIN"),
        alternateIdentifiers=[],
        violin_pathogen=fields,
    )


def test_extract_violin_pathogen_yields_one_pair() -> None:
    c = _make_pathogen("EEEV", 11021)
    pairs = list(extract_violin_pathogen(c))
    assert pairs == [("EEEV", 11021)]


def test_extract_violin_pathogen_skips_missing_taxon() -> None:
    c = _make_pathogen("EEEV", None)
    assert list(extract_violin_pathogen(c)) == []


def _make_epitope(organism: str, taxon: int | None = 11021) -> BVBRCEpitopeContainer:
    fields = BVBRCEpitopeFields(
        Organism=organism, Taxon_ID=taxon, Protein_and_Epitope=[]
    )
    return BVBRCEpitopeContainer.new(
        title=organism,
        description=None,
        creators=[],
        publisher=Publisher(name="BV-BRC"),
        alternateIdentifiers=[],
        bvbrc_epitope=fields,
    )


def test_extract_bvbrc_epitope_yields_one_pair() -> None:
    c = _make_epitope("Eastern equine encephalitis virus", 11021)
    assert list(extract_bvbrc_epitope(c)) == [
        ("Eastern equine encephalitis virus", 11021)
    ]


def _make_genome(
    name: str,
    taxa: list[str | None],
    *,
    species: str | None = None,
) -> BVBRCGenomeContainer:
    genomes = [
        GenomeEntry(Genome_ID=f"genome.{i}", NCBI_Taxon_ID=t)
        for i, t in enumerate(taxa)
    ]
    fields = BVBRCGenomeFields(Genome_Name=name, Species=species, Genome=genomes)
    return BVBRCGenomeContainer.new(
        title=name,
        description=None,
        creators=[],
        publisher=Publisher(name="BV-BRC"),
        alternateIdentifiers=[],
        bvbrc_genome=fields,
    )


def test_extract_bvbrc_genome_dedupes_species_across_strain_entries() -> None:
    """Multiple GenomeEntry rows with the same species.strain prefix should
    yield ONE observation per species, not one per strain."""
    c = _make_genome(
        "Severe acute respiratory syndrome coronavirus 2",
        ["2697049.1001", "2697049.1002", "2697049.1003"],
    )
    pairs = list(extract_bvbrc_genome(c))
    assert pairs == [("Severe acute respiratory syndrome coronavirus 2", 2697049)]


def test_extract_bvbrc_genome_handles_multiple_species_in_one_container() -> None:
    """Edge case: container with multiple distinct species ids (rare, but happens)."""
    c = _make_genome(
        "Multi-species container",
        ["11021.5", "64320.5", "11021.6"],
    )
    pairs = list(extract_bvbrc_genome(c))
    # Order matches first-seen; dedup leaves 2.
    species = {p[1] for p in pairs}
    assert species == {11021, 64320}


def test_extract_bvbrc_genome_skips_when_no_taxon() -> None:
    c = _make_genome("Lab strain virus", [None, None])
    assert list(extract_bvbrc_genome(c)) == []


def _make_protein_structure(
    organism: str, taxa: list[int]
) -> BVBRCProteinStructureContainer:
    groups = [
        PublicationStructureGroup(
            Title="Some publication", Taxon_ID=taxa, PMID=[], Protein_Structure=[]
        )
    ]
    fields = BVBRCProteinStructureFields(
        Organism_Name=organism, Publication_and_Protein_Structure=groups
    )
    return BVBRCProteinStructureContainer.new(
        title=organism,
        description=None,
        creators=[],
        publisher=Publisher(name="BV-BRC"),
        alternateIdentifiers=[],
        bvbrc_protein_structure=fields,
    )


def test_extract_bvbrc_protein_structure_yields_one_per_taxon() -> None:
    c = _make_protein_structure("Hepatitis C virus", [11103, 3052230])
    pairs = list(extract_bvbrc_protein_structure(c))
    assert {p[1] for p in pairs} == {11103, 3052230}
    for surface, _ in pairs:
        assert surface == "Hepatitis C virus"


# ---------------------------------------------------------------------------
# Registry sanity check
# ---------------------------------------------------------------------------


def test_extractor_registry_includes_four_sources() -> None:
    """The v1 SC-B2 registry must cover the four high-value sources."""
    assert set(SOURCE_MINING_EXTRACTORS) == {
        "violin_pathogen",
        "bvbrc_epitope",
        "bvbrc_genome",
        "bvbrc_protein_structure",
    }


# ---------------------------------------------------------------------------
# End-to-end extractor + accumulator
# ---------------------------------------------------------------------------


def test_extractor_accumulator_e2e_filters_strain_pollution() -> None:
    """SC-B2+B5 combined: extractor yields a strain-looking name from a
    real BVBRC genome container; the accumulator's SC-B5 filter drops it.
    No silent pollution."""
    acc = MinedSynonymAccumulator()
    clean_container = _make_genome(
        "Severe acute respiratory syndrome coronavirus 2", ["2697049.1"]
    )
    polluted_container = _make_genome(
        "Influenza A virus (A/common pochard/Shanxi/16B/2015(H5N1))",
        ["11320.42"],
    )
    extractor = SOURCE_MINING_EXTRACTORS["bvbrc_genome"]
    for c in (clean_container, polluted_container):
        for surface, taxon in extractor(c):
            acc.observe(surface, taxon, source="bvbrc_genome")
    # Only the clean container's surface lands.
    obs = list(acc.observations())
    assert len(obs) == 1
    assert obs[0].surface_form == (
        "Severe acute respiratory syndrome coronavirus 2"
    )
    assert obs[0].taxon_id == 2697049


# ---------------------------------------------------------------------------
# SC-B7 — strain-prefix acronym extraction (per-record + batch)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,species,expected",
    [
        # The five known-gap targets, one example each.
        ("Chikungunya virus CHIKV/IRL/2007", "Chikungunya virus", ["CHIKV"]),
        (
            "Eastern equine encephalitis virus EEEV/Equus/USA/RAB12/2012",
            "Eastern equine encephalitis virus",
            ["EEEV"],
        ),
        (
            "Venezuelan equine encephalitis virus VEEV/Anopheles/VEN/12-1/1995/IC",
            "Venezuelan equine encephalitis virus",
            ["VEEV"],
        ),
        (
            "Western equine encephalitis virus WEEV-UY-228",
            "Western equine encephalitis virus",
            ["WEEV"],
        ),
        ("Mayaro virus MAYV_BR/MT_CbaAr66/2017", "Mayaro virus", ["MAYV"]),
        # Multiple separators in one string — pick the first slash-separated.
        ("Getah virus GETV/horse/JP/2014", "Getah virus", ["GETV"]),
    ],
)
def test_extract_strain_prefix_acronyms_catches_known_gaps(
    name: str, species: str, expected: list[str]
) -> None:
    assert extract_strain_prefix_acronyms(name, species=species) == expected


@pytest.mark.parametrize(
    "name,species",
    [
        # Three-char acronyms are intentionally rejected (RSV, HIV, HSV are
        # ambiguous in the dict and shouldn't be mined from a single source).
        ("Some virus RSV/strain/2020", "Some virus"),
        # Strain ID looking shape (one letter + digits) — rejected.
        ("Salmon pancreas disease virus F02/strain", "Salmon pancreas disease virus"),
        # Strain ID with two letters + digits — rejected (need >=3 letters).
        ("Foo virus AB123/strain", "Foo virus"),
        # No species prefix to strip — verbose name without acronym, no hits.
        ("Sindbis virus", "Sindbis virus"),
    ],
)
def test_extract_strain_prefix_acronyms_rejects_noise(
    name: str, species: str
) -> None:
    assert extract_strain_prefix_acronyms(name, species=species) == []


def test_extract_strain_prefix_acronyms_handles_empty_or_missing_inputs() -> None:
    assert extract_strain_prefix_acronyms("") == []
    assert extract_strain_prefix_acronyms("Foo virus FOOV/bar") == ["FOOV"]
    # Species missing — still works, just no prefix strip.
    assert extract_strain_prefix_acronyms("Foo virus FOOV/bar", species=None) == [
        "FOOV"
    ]


def test_mine_bvbrc_strain_prefix_acronyms_threshold_filters() -> None:
    """Acronym must fire >= min_count records AND >= min_fraction of species
    records to be accepted."""
    species = "Chikungunya virus"
    species_taxon = "37124.1"
    containers = []
    # 12 CHIKV records with the acronym in the prefix.
    for i in range(12):
        containers.append(
            _make_genome(
                f"{species} CHIKV/IRL/200{i}",
                [species_taxon],
                species=species,
            )
        )
    # 50 CHIKV records without the acronym (verbose-only name).
    for i in range(50):
        containers.append(
            _make_genome(
                f"{species} isolate-strain-{i}",
                [species_taxon],
                species=species,
            )
        )
    # And 4 noise records carrying a 1-off FOOBAR acronym.
    for i in range(4):
        containers.append(
            _make_genome(
                f"{species} FOOBAR/strain/{i}",
                [species_taxon],
                species=species,
            )
        )

    acc = MinedSynonymAccumulator()
    stats = mine_bvbrc_strain_prefix_acronyms(
        containers, accumulator=acc, min_count=10, min_fraction=0.10
    )
    # CHIKV: 12/66 records ≈ 18% — accepted.
    # FOOBAR: 4/66 records ≈ 6% — fails min_count (4 < 10) -> rejected.
    assert stats["acronyms_accepted"] == 1
    assert stats["acronyms_rejected_by_count"] == 1
    obs = list(acc.observations())
    assert len(obs) == 1
    assert obs[0].surface_form == "CHIKV"
    assert obs[0].taxon_id == 37124


def test_mine_bvbrc_strain_prefix_acronyms_rejects_below_fraction_floor() -> None:
    """High absolute count but tiny species fraction → rejected."""
    species = "Big Species virus"
    species_taxon = "99999"
    containers = []
    # 20 records with the acronym (above min_count=10).
    for i in range(20):
        containers.append(
            _make_genome(
                f"{species} ZZZZ/strain/{i}",
                [species_taxon],
                species=species,
            )
        )
    # 1000 records without — denominator inflates so fraction = 2%.
    for i in range(1000):
        containers.append(
            _make_genome(
                f"{species} isolate-{i}",
                [species_taxon],
                species=species,
            )
        )

    acc = MinedSynonymAccumulator()
    stats = mine_bvbrc_strain_prefix_acronyms(
        containers, accumulator=acc, min_count=10, min_fraction=0.10
    )
    assert stats["acronyms_proposed"] == 1
    assert stats["acronyms_accepted"] == 0
    assert stats["acronyms_rejected_by_fraction"] == 1
    assert list(acc.observations()) == []


def test_mine_bvbrc_strain_prefix_acronyms_separate_source_name() -> None:
    """Acronym observations land under bvbrc_genome_acronym, not bvbrc_genome,
    so SC-B3 conflict detection can distinguish them."""
    species = "Mayaro virus"
    containers = [
        _make_genome(
            f"{species} MAYV_BR/MT_{i}",
            ["59301.1"],
            species=species,
        )
        for i in range(15)
    ]
    acc = MinedSynonymAccumulator()
    mine_bvbrc_strain_prefix_acronyms(
        containers, accumulator=acc, min_count=10, min_fraction=0.10
    )
    obs = list(acc.observations())
    assert len(obs) == 1
    assert obs[0].source == "bvbrc_genome_acronym"


# ---------------------------------------------------------------------------
# SC-B8 — parenthetical-acronym extraction from VIOLIN prose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,pathogen,expected",
    [
        # Real VIOLIN-style introductions.
        (
            "Herpes simplex virus 1 and 2 (HSV-1 and HSV-2), also known as "
            "Human herpes virus 1 and 2 (HHV-1 and -2), are two members of "
            "the herpes virus family Herpesviridae.",
            "Herpes simplex virus type 1 and 2",
            ["HSV-1", "HSV-2", "HHV-1"],
        ),
        (
            "Tick-borne Encephalitis Virus (TBEV) is the causative agent.",
            "Tick-borne Encephalitis Virus (TBEV)",
            ["TBEV"],
        ),
        (
            "Western Equine Encephalitis (WEE) caused by Western equine "
            "encephalomyelitis virus.",
            "Western equine encephalomyelitis virus",
            ["WEE"],
        ),
    ],
)
def test_extract_parenthetical_acronyms_catches_introductions(
    text: str, pathogen: str, expected: list[str]
) -> None:
    """Mines acronyms introduced inside <verbose phrase> (ACR) patterns."""
    assert extract_parenthetical_acronyms(text, pathogen=pathogen) == expected


def test_extract_parenthetical_acronyms_rejects_citation_marker() -> None:
    """A citation like (CDC: Hendra virus) is NOT an acronym introduction
    for Hendra virus — CDC's letters are not the initials of the phrase
    that precedes the parens.

    The phrase 'patients infected with Hendra virus died' has initials
    p/i/w/H/v/d; CDC = C/D/C doesn't appear in that sequence."""
    text = (
        "...two of the three human patients infected with Hendra virus "
        "died (CDC: Hendra virus)."
    )
    assert extract_parenthetical_acronyms(text, pathogen="Hendra virus") == []


def test_extract_parenthetical_acronyms_rejects_unrelated_introduction() -> None:
    """An acronym introduced in a phrase that has NO content overlap with
    the record's pathogen is rejected — even if it's a valid initialism."""
    text = "The European Union (EU) funded the Hendra virus surveillance."
    # 'European Union' shares no >=2-word window with 'Hendra virus'.
    assert extract_parenthetical_acronyms(text, pathogen="Hendra virus") == []


def test_extract_parenthetical_acronyms_handles_empty_inputs() -> None:
    assert extract_parenthetical_acronyms("", pathogen="Foo") == []
    assert extract_parenthetical_acronyms("Some prose", pathogen="") == []
    assert extract_parenthetical_acronyms(None, pathogen="Foo") == []  # type: ignore[arg-type]


def test_mine_violin_pathogen_acronyms_e2e() -> None:
    """Walks two synthetic VIOLIN containers and emits acronyms tied to
    the record's NCBI Taxonomy ID under the violin_pathogen_acronym source."""
    fields_hsv = ViolinPathogenFields(
        id=1, VIOLIN_c_pathogen_id=100,
        Pathogen="Herpes simplex virus type 1 and 2",
        NCBI_Taxonomy_ID=10298,
        Pathogen_Description=(
            "Herpes simplex virus 1 and 2 (HSV-1 and HSV-2), also known as "
            "Human herpes virus 1 and 2 (HHV-1 and -2), are two members."
        ),
    )
    c_hsv = VIOLINPathogenContainer.new(
        title="HSV", description=None, creators=[],
        publisher=Publisher(name="VIOLIN"),
        alternateIdentifiers=[], violin_pathogen=fields_hsv,
    )
    fields_tbev = ViolinPathogenFields(
        id=2, VIOLIN_c_pathogen_id=200,
        Pathogen="Tick-borne Encephalitis Virus",
        NCBI_Taxonomy_ID=11084,
        Pathogen_Description=(
            "Tick-borne Encephalitis Virus (TBEV) is the causative agent."
        ),
    )
    c_tbev = VIOLINPathogenContainer.new(
        title="TBEV", description=None, creators=[],
        publisher=Publisher(name="VIOLIN"),
        alternateIdentifiers=[], violin_pathogen=fields_tbev,
    )

    acc = MinedSynonymAccumulator()
    stats = mine_violin_pathogen_acronyms([c_hsv, c_tbev], accumulator=acc)
    assert stats["records_scanned"] == 2
    assert stats["records_with_taxon"] == 2
    assert stats["acronyms_observed"] == 4  # HSV-1, HSV-2, HHV-1, TBEV

    obs_by_pair = {(o.surface_form, o.taxon_id) for o in acc.observations()}
    assert ("HSV-1", 10298) in obs_by_pair
    assert ("HSV-2", 10298) in obs_by_pair
    assert ("HHV-1", 10298) in obs_by_pair
    assert ("TBEV", 11084) in obs_by_pair
    # All observations land under violin_pathogen_acronym, not violin_pathogen.
    sources = {o.source for o in acc.observations()}
    assert sources == {"violin_pathogen_acronym"}


def test_mine_violin_pathogen_acronyms_skips_missing_taxon() -> None:
    """Records without NCBI_Taxonomy_ID are scanned but not observed."""
    fields = ViolinPathogenFields(
        id=1, VIOLIN_c_pathogen_id=100,
        Pathogen="Some virus", NCBI_Taxonomy_ID=None,
        Pathogen_Description="Some virus (SV) is a thing.",
    )
    c = VIOLINPathogenContainer.new(
        title="SV", description=None, creators=[],
        publisher=Publisher(name="VIOLIN"),
        alternateIdentifiers=[], violin_pathogen=fields,
    )
    acc = MinedSynonymAccumulator()
    stats = mine_violin_pathogen_acronyms([c], accumulator=acc)
    assert stats["records_scanned"] == 1
    assert stats["records_with_taxon"] == 0
    assert stats["acronyms_observed"] == 0


def test_mine_violin_pathogen_acronyms_separate_source_name() -> None:
    """SC-B8 observations carry a distinct source name so SC-B3's conflict
    table can attribute prose-mined acronyms separately from canonical
    surface-form observations."""
    fields = ViolinPathogenFields(
        id=1, VIOLIN_c_pathogen_id=100,
        Pathogen="Hepatitis C virus", NCBI_Taxonomy_ID=11103,
        Pathogen_Description="Hepatitis C Virus (HCV) is a member of...",
    )
    c = VIOLINPathogenContainer.new(
        title="HCV", description=None, creators=[],
        publisher=Publisher(name="VIOLIN"),
        alternateIdentifiers=[], violin_pathogen=fields,
    )
    acc = MinedSynonymAccumulator()
    mine_violin_pathogen_acronyms([c], accumulator=acc)
    sources = {o.source for o in acc.observations()}
    assert sources == {"violin_pathogen_acronym"}
