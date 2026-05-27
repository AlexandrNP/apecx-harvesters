"""BV-BRC Protein Structure schema extension (index 439f2b66-...).

Three-level nesting: one document == one organism (or organism pair, e.g.
"SARS-CoV-2;Homo sapiens"), grouping publications, each with their solved protein
structures. Harmonized at the organism granularity (aggregate). PDB / UniProtKB
accessions and PMIDs are lifted to alternateIdentifiers for cross-linking.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import DataCite
from ..base.registry import SchemaRegistry


class StructureEntry(BaseModel):
    """One solved protein structure."""
    model_config = ConfigDict(strict=True, extra="forbid")

    PDB_ID: str
    Method: Optional[str] = None
    Product: Optional[str] = None
    Authors: Optional[str] = None
    Release_Date: Optional[str] = None
    Resolution: Optional[str] = None
    Gene: Optional[str] = None
    Genome_ID: Optional[str] = None
    BRC_ID: Optional[str] = None
    Institution: Optional[str] = None
    UniProtKB_Accession: Optional[str] = None
    # Alignments is null in ~38% of the full corpus (verified by full-scale
    # harmonize: 1740/4566 docs) -- Optional, not a defaulted empty list, because
    # the source sends an explicit null that strict list[str] would reject.
    Alignments: Optional[list[str]] = None


class PublicationStructureGroup(BaseModel):
    """A publication and the protein structures it reports."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Title: Optional[str] = None
    Taxon_ID: list[int] = Field(default_factory=list)
    PMID: list[int] = Field(default_factory=list)
    Protein_Structure: list[StructureEntry] = Field(default_factory=list)


class BVBRCProteinStructureFields(BaseModel):
    """Full BV-BRC Protein Structure source content for one organism."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Organism_Name: str
    Publication_and_Protein_Structure: list[PublicationStructureGroup] = Field(default_factory=list)


@SchemaRegistry.register
class BVBRCProteinStructureContainer(DataCite):
    """DataCite record for one BV-BRC organism's protein-structure set."""

    bvbrc_protein_structure: BVBRCProteinStructureFields

    @property
    def canonical_uri(self) -> str:
        # Organism_Name == the source subject (Globus-unique per index).
        return f"bvbrc-protein-structure:{self.bvbrc_protein_structure.Organism_Name}"
