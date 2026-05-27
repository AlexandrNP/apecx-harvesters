"""VIOLIN:Vaccine schema extension (index c5ff64fd-...).

Flat record, one source document == one vaccine. Vaccine_Name/Description are
promoted to base DataCite; the full source content is preserved nested.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import DataCite
from ..base.registry import SchemaRegistry


class ViolinVaccineFields(BaseModel):
    """Full VIOLIN:Vaccine source content (validation gate + nested storage)."""
    model_config = ConfigDict(strict=True, extra="forbid")

    id: Annotated[int, Field(description="VIOLIN row id (unique per record)")]
    vaccine_id: int
    Vaccine: str
    Vaccine_Name: str
    Type: str
    Vaccine_Ontology_ID: Optional[str] = None
    Status: Optional[str] = None
    Description: Optional[str] = None
    Antigen: Optional[str] = None
    Allergen: Optional[str] = None
    Preparation: Optional[str] = None
    Preservative: Optional[str] = None
    Storage: Optional[str] = None
    Virulence: Optional[str] = None
    Contraindication: Optional[str] = None
    Manufacturer: Optional[str] = None
    Tradename: Optional[str] = None
    Product_Name: Optional[str] = None
    Immunization_Route: Optional[str] = None
    Location_Licensed: Optional[str] = None
    Approved_Age_for_Licensed_Use: Optional[str] = None
    Host_Species_for_Licensed_Use: Optional[str] = None
    Host_Species_as_Laboratory_Animal_Model: Optional[str] = None
    CDC_CVX_code: Optional[str] = None
    CDC_CVX_description: Optional[str] = None
    pathogen_id: list[int] = Field(default_factory=list)
    vaccine_pathogen_id: list[int] = Field(default_factory=list)
    VIOLIN_c_pathogen_id: list[int] = Field(default_factory=list)
    VIOLIN_c_vaccine_id: list[int] = Field(default_factory=list)


@SchemaRegistry.register
class VIOLINVaccineContainer(DataCite):
    """DataCite record for one VIOLIN:Vaccine entry."""

    violin_vaccine: ViolinVaccineFields

    @property
    def canonical_uri(self) -> str:
        return f"violin-vaccine:{self.violin_vaccine.id}"
