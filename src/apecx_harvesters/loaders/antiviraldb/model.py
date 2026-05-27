"""AntiviralDB schema extension.

Extends the base DataCite schema with fields from the AntiviralDB Globus Search
index (index e8097a7b-...). The source models antiviral drug/compound assays
grouped by virus: one source document == one virus, with nested proteins and the
drugs assayed against them. We harmonize one DataCite record per virus.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import DataCite
from ..base.registry import SchemaRegistry


class DrugEntry(BaseModel):
    """A single antiviral drug/compound assayed against a viral protein."""
    model_config = ConfigDict(strict=True, extra="forbid")

    DrugName: Optional[str] = None
    DrugType: Optional[str] = None
    IC50: Optional[str] = None
    EC50: Optional[str] = None
    CC50: Optional[str] = None
    CellLine: Optional[str] = None
    VirusStrain: Optional[str] = None
    PDB: Annotated[Optional[str], Field(description="PDB structure accession, when known")] = None
    Status: Optional[str] = None


class ProteinDrugEntry(BaseModel):
    """A viral protein and the drugs/compounds assayed against it."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Protein: Optional[str] = None
    Ref: Annotated[list[int], Field(description="PubMed IDs for the assay references")] = Field(
        default_factory=list
    )
    Drug: list[DrugEntry] = Field(default_factory=list)


class AntiviralDBFields(BaseModel):
    """Domain-specific AntiviralDB content for one virus."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Virus: Annotated[str, Field(description="Virus name (also the source record subject)")]
    Protein_and_Drug: list[ProteinDrugEntry] = Field(default_factory=list)


@SchemaRegistry.register
class AntiviralDBContainer(DataCite):
    """DataCite record for one AntiviralDB virus entry."""

    antiviraldb: AntiviralDBFields

    @property
    def canonical_uri(self) -> str:
        # No source-side accession; the (unique) virus name is the stable key.
        # Use the EXACT name, not a lowercased slug: slugging collapsed two
        # case-only-distinct source entries ("Influenza Virus" vs "Influenza
        # virus") into one URI, which would silently overwrite a record at
        # ingest. The source subjects are unique, so the exact name is too.
        return f"antiviraldb:{self.antiviraldb.Virus.strip()}"
