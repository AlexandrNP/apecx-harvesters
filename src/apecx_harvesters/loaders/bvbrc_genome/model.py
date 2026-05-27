"""BV-BRC Genome schema extension (index b676edbe-...).

Source granularity: one document == one organism (Genome_Name) + taxonomy, with
a nested list of its sequenced genomes. Harmonized at that granularity (one
record per organism); per-genome accessions/taxon-ids lifted to
alternateIdentifiers. NOTE: this index was observed mid-reingest (volatile) on
2026-05-26 -- scrape only once its total stabilizes (see plan, Phase 5 gate).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import DataCite
from ..base.registry import SchemaRegistry


class GenomeEntry(BaseModel):
    """One sequenced genome assembly within an organism group."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Genome_ID: str
    Genome_Status: Optional[str] = None
    Strain: Optional[str] = None
    NCBI_Taxon_ID: Optional[str] = None
    GenBank_Accessions: Optional[str] = None
    Collection_Year: Optional[int] = None
    Contig_L50: Optional[int] = None
    Contig_N50: Optional[int] = None
    Contigs: Optional[int] = None
    GC_Content: Optional[float] = None
    Size: Optional[int] = None
    Segment: Optional[int] = None
    Publication: Optional[int] = None
    Geographic_Group: Optional[str] = None
    Geographic_Location: Optional[str] = None
    Isolation_Country: Optional[str] = None
    Isolation_Source: Optional[str] = None
    Host_Common_Name: Optional[str] = None
    Host_Group: Optional[str] = None
    Host_Name: Optional[str] = None
    Other_Names: Optional[str] = None
    Sequencing_Platform: Optional[str] = None
    Taxon_Lineage_IDs: list[str] = Field(default_factory=list)
    Taxon_Lineage_Names: list[str] = Field(default_factory=list)


class BVBRCGenomeFields(BaseModel):
    """Full BV-BRC Genome source content for one organism group."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Genome_Name: str
    Superkingdom: Optional[str] = None
    Kingdom: Optional[str] = None
    Phylum: Optional[str] = None
    Class: Optional[str] = None
    Order: Optional[str] = None
    Family: Optional[str] = None
    Genus: Optional[str] = None
    Species: Optional[str] = None
    Genome: list[GenomeEntry] = Field(default_factory=list)


@SchemaRegistry.register
class BVBRCGenomeContainer(DataCite):
    """DataCite record for one BV-BRC organism's genome set."""

    bvbrc_genome: BVBRCGenomeFields

    @property
    def canonical_uri(self) -> str:
        # Genome_Name == the source subject (Globus-unique per index).
        return f"bvbrc-genome:{self.bvbrc_genome.Genome_Name}"
