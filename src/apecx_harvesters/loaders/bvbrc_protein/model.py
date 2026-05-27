"""BV-BRC Protein schema extension (index 249efe96-...).

Source granularity: one document == one organism/genome, with a nested list of
its annotated protein features. We harmonize at that same granularity (one
record per organism) rather than exploding to per-protein: Globus Search indexes
the nested fields, so a per-protein query still matches the organism record, and
exploding would multiply ~25k docs into ~370k records for little discovery gain.
Per-feature accessions are lifted to alternateIdentifiers for cross-linking.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import DataCite
from ..base.registry import SchemaRegistry


class ProteinFeature(BaseModel):
    """One annotated protein feature within an organism's proteome."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Feature_ID: str
    BRC_ID: Optional[str] = None
    Genome_ID: Optional[str] = None
    Accession: Optional[str] = None
    Annotation: Optional[str] = None
    Product: Optional[str] = None
    Gene_Symbol: Optional[str] = None
    Feature_Type: Optional[str] = None
    AA_Length: Optional[str] = None
    Length: Optional[str] = None
    Start: Optional[str] = None
    End: Optional[str] = None
    Strand: Optional[str] = None
    Protein_ID: Optional[str] = None
    Alt_Locus_Tag: Optional[str] = None
    RefSeq_Locus_Tag: Optional[str] = None
    FIGfam_ID: Optional[str] = None
    GO: Optional[str] = None
    PATRIC_cross_genus_families: Optional[str] = None
    PATRIC_genus_specific_families: Optional[str] = None


class BVBRCProteinFields(BaseModel):
    """Full BV-BRC Protein source content for one organism."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Genome: str
    Protein: list[ProteinFeature] = Field(default_factory=list)


@SchemaRegistry.register
class BVBRCProteinContainer(DataCite):
    """DataCite record for one BV-BRC organism's protein set."""

    bvbrc_protein: BVBRCProteinFields

    @property
    def canonical_uri(self) -> str:
        # Genome (organism name) == the source subject (Globus-unique per index).
        return f"bvbrc-protein:{self.bvbrc_protein.Genome}"
