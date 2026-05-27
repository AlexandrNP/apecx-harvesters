"""VIOLIN:Pathogen schema extension.

Extends the base DataCite schema with fields from the VIOLIN:Pathogen Globus
Search index (index a67c7310-...). One source document == one pathogen record
(carrying its VIOLIN vaccine associations). Flat schema -- contrast AntiviralDB,
which is nested.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import DataCite
from ..base.registry import SchemaRegistry


class ViolinPathogenFields(BaseModel):
    """Domain-specific VIOLIN:Pathogen content for one pathogen."""
    model_config = ConfigDict(strict=True, extra="forbid")

    id: Annotated[int, Field(description="VIOLIN row id (unique per record)")]
    VIOLIN_c_pathogen_id: int
    Pathogen: str
    NCBI_Taxonomy_ID: Optional[int] = None
    Disease: Optional[str] = None
    Pathogen_Description: Optional[str] = None
    Microbial_Pathogenesis: Optional[str] = None
    Host_Ranges_and_Animal_Models: Optional[str] = None
    Host_Protective_Immunity: Optional[str] = None
    pathogen_id: Optional[int] = None
    vaccine_id: list[int] = Field(default_factory=list)
    vaccine_pathogen_id: list[int] = Field(default_factory=list)
    VIOLIN_c_vaccine_id: list[int] = Field(default_factory=list)


@SchemaRegistry.register
class VIOLINPathogenContainer(DataCite):
    """DataCite record for one VIOLIN:Pathogen entry."""

    violin_pathogen: ViolinPathogenFields

    @property
    def canonical_uri(self) -> str:
        return f"violin-pathogen:{self.violin_pathogen.id}"
