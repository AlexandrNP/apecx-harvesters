"""VIOLIN:Gene schema extension (index 205c1a5b-...).

Flat record, one source document == one gene. Carries rich external accessions
(NCBI Gene/Protein/Nucleotide, Genbank, PDB) that the parser lifts into
``alternateIdentifiers`` for cross-source linkage.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..base import DataCite
from ..base.registry import SchemaRegistry


class ViolinGeneFields(BaseModel):
    """Full VIOLIN:Gene source content (validation gate + nested storage)."""
    model_config = ConfigDict(strict=True, extra="forbid")

    id: Annotated[int, Field(description="VIOLIN row id (unique per record)")]
    VIOLIN_c_gene_id: int
    # Gene_Name is nullable in the full corpus (4/4063 docs); parser falls back.
    Gene_Name: Optional[str] = None
    Organism: Optional[str] = None
    Protein_Name: Optional[str] = None
    Molecule_Role: Optional[str] = None
    Locus_Tag: Optional[str] = None
    NCBI_Gene_ID: Optional[int] = None
    NCBI_Protein_GI: Optional[str] = None
    NCBI_Nucleotide_GI: Optional[str] = None
    Genbank_Accession: Optional[str] = None
    Protein_Accession: Optional[str] = None
    PDB_ID: Optional[str] = None
    Other_Database_IDs: Optional[str] = None
    Vaccine_Ontology_ID: Optional[str] = None
    gene_id: Optional[int] = None
    VIOLIN_c_vaccine_id: Optional[int] = None
    vaccine_pathogen_id: Optional[int] = None


@SchemaRegistry.register
class VIOLINGeneContainer(DataCite):
    """DataCite record for one VIOLIN:Gene entry."""

    violin_gene: ViolinGeneFields

    @property
    def canonical_uri(self) -> str:
        return f"violin-gene:{self.violin_gene.id}"
