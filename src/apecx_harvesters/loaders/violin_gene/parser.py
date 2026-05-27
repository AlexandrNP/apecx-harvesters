"""VIOLIN:Gene Globus Search document -> DataCite parser.

One record per gene. External accessions (NCBI Gene/Protein/Nucleotide, Genbank,
Protein accession, PDB, VO) are lifted into ``alternateIdentifiers`` so genes
link to the protein/structure/genome sources by shared accession. Raises on
malformed input.
"""

from __future__ import annotations

from typing import Any

from ..base import AlternateIdentifier, Publisher
from .model import VIOLINGeneContainer, ViolinGeneFields

# Source field -> alternateIdentifier type. str fields only.
_ALT_ID_FIELDS = {
    "Genbank_Accession": "GenBank",
    "NCBI_Protein_GI": "NCBI-Protein-GI",
    "NCBI_Nucleotide_GI": "NCBI-Nucleotide-GI",
    "Protein_Accession": "RefSeq-Protein",
    "PDB_ID": "PDB",
    "Vaccine_Ontology_ID": "VO",
}


def parse_violin_gene(content: dict[str, Any]) -> VIOLINGeneContainer:
    fields = ViolinGeneFields.model_validate(content)

    alt_ids: list[AlternateIdentifier] = []
    if fields.NCBI_Gene_ID is not None:
        alt_ids.append(
            AlternateIdentifier(alternateIdentifier=str(fields.NCBI_Gene_ID), alternateIdentifierType="NCBI-Gene")
        )
    for field_name, id_type in _ALT_ID_FIELDS.items():
        value = getattr(fields, field_name)
        if value:
            alt_ids.append(AlternateIdentifier(alternateIdentifier=value, alternateIdentifierType=id_type))

    title = fields.Gene_Name or fields.Protein_Name or f"VIOLIN gene {fields.id}"
    return VIOLINGeneContainer.new(
        title=title,
        creators=[],
        publisher=Publisher(name="VIOLIN"),
        alternateIdentifiers=alt_ids,
        violin_gene=fields,
    )
