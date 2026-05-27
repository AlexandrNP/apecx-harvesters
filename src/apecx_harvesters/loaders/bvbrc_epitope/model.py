"""BV-BRC Epitope schema extension (index f873c7d5-...).

Three-level nesting: one document == one organism, with proteins, each carrying
its mapped epitopes. Harmonized at the organism granularity (aggregate; see the
plan's granularity decision). Documents are large (multi-MB) -- the per-entry
10 MB GMetaList guard in sinks.py is the relevant ingest constraint here.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import DataCite
from ..base.registry import SchemaRegistry


class EpitopeEntry(BaseModel):
    """One mapped epitope on a protein."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Epitope_ID: str
    Epitope_Sequence: str
    Epitope_Type: str
    Total_Assays: Optional[int] = None
    Start: Optional[int] = None
    End: Optional[int] = None
    Bcell_Assays: Optional[str] = None
    Tcell_Assays: Optional[str] = None
    MCH_Assays: Optional[str] = None
    Comments: Optional[str] = None


class ProteinEpitopeGroup(BaseModel):
    """A protein and the epitopes mapped onto it."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Protein_Name: Optional[str] = None
    Protein_ID: list[str] = Field(default_factory=list)
    Protein_Accession: list[str] = Field(default_factory=list)
    Epitope: list[EpitopeEntry] = Field(default_factory=list)


class BVBRCEpitopeFields(BaseModel):
    """Full BV-BRC Epitope source content for one organism."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Organism: str
    Taxon_ID: Optional[int] = None
    Protein_and_Epitope: list[ProteinEpitopeGroup] = Field(default_factory=list)


@SchemaRegistry.register
class BVBRCEpitopeContainer(DataCite):
    """DataCite record for one BV-BRC organism's epitope set."""

    bvbrc_epitope: BVBRCEpitopeFields

    @property
    def canonical_uri(self) -> str:
        # Organism == the source subject (Globus-unique per index).
        return f"bvbrc-epitope:{self.bvbrc_epitope.Organism}"
