"""ProtaBank schema extension.

ProtaBank records (index 9e902471-...) are publication-centric: Title, Abstract,
Authors, Year + a list of protein-study objects (each with ProtaBank/PDB/UniProt
accessions). Publication fields map onto base DataCite; the protein studies are
preserved nested and their accessions lifted to alternateIdentifiers.
One source document == one ProtaBank publication.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import DataCite
from ..base.registry import SchemaRegistry


class ProtaBankProteinEntry(BaseModel):
    """A protein-study entry within a ProtaBank publication."""
    model_config = ConfigDict(strict=True, extra="forbid")

    ProtaBank_ID: str
    Proteins: Optional[str] = None
    Accession: Optional[str] = None
    PDB_ID: Optional[str] = None
    Status: Optional[str] = None
    Number_of_Data_Points: Optional[int] = None
    Additional_Information: Optional[str] = None


class ProtaBankFields(BaseModel):
    """Full ProtaBank source content (validation gate + nested storage)."""
    model_config = ConfigDict(strict=True, extra="forbid")

    Title: str
    Abstract: Optional[str] = None
    Publication_Authors: list[str] = Field(default_factory=list)
    Publication_Year: Optional[str] = None
    Submission_Date: Optional[str] = None
    Submitter: Optional[str] = None
    Protein: list[ProtaBankProteinEntry] = Field(default_factory=list)


@SchemaRegistry.register
class ProtaBankContainer(DataCite):
    """DataCite record for one ProtaBank publication."""

    protabank: ProtaBankFields

    @property
    def canonical_uri(self) -> str:
        # ProtaBank has no top-level accession; the title is the source's unique subject.
        return f"protabank:{self.protabank.Title.strip()}"
