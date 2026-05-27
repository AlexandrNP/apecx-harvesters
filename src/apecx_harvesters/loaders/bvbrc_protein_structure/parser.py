"""BV-BRC Protein Structure document -> DataCite parser (aggregate, per organism).

PDB IDs, UniProtKB accessions, BV-BRC Genome IDs, and PMIDs are lifted into
alternateIdentifiers for cross-source linkage. Raises on malformed input.
"""

from __future__ import annotations

from typing import Any

from ..base import AlternateIdentifier, Publisher
from .model import BVBRCProteinStructureContainer, BVBRCProteinStructureFields


def parse_bvbrc_protein_structure(content: dict[str, Any]) -> BVBRCProteinStructureContainer:
    fields = BVBRCProteinStructureFields.model_validate(content)

    alt_ids: list[AlternateIdentifier] = []
    seen: set[tuple[str, str]] = set()

    def add(value: str | None, id_type: str) -> None:
        if value and (value, id_type) not in seen:
            seen.add((value, id_type))
            alt_ids.append(AlternateIdentifier(alternateIdentifier=value, alternateIdentifierType=id_type))

    for group in fields.Publication_and_Protein_Structure:
        for pmid in group.PMID:
            add(str(pmid), "PMID")
        for tax in group.Taxon_ID:
            add(str(tax), "NCBI-Taxonomy")
        for s in group.Protein_Structure:
            add(s.PDB_ID, "PDB")
            add(s.UniProtKB_Accession, "UniProt")
            add(s.Genome_ID, "BVBRC-Genome")

    return BVBRCProteinStructureContainer.new(
        title=fields.Organism_Name,
        creators=[],
        publisher=Publisher(name="BV-BRC"),
        alternateIdentifiers=alt_ids,
        bvbrc_protein_structure=fields,
    )
