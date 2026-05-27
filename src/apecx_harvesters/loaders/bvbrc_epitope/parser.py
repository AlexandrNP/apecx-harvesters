"""BV-BRC Epitope Globus Search document -> DataCite parser (aggregate, per organism).

The NCBI Taxon ID and per-protein accessions are lifted into alternateIdentifiers
for cross-source linkage. Raises on malformed input.
"""

from __future__ import annotations

from typing import Any

from ..base import AlternateIdentifier, Publisher
from .model import BVBRCEpitopeContainer, BVBRCEpitopeFields


def parse_bvbrc_epitope(content: dict[str, Any]) -> BVBRCEpitopeContainer:
    fields = BVBRCEpitopeFields.model_validate(content)

    alt_ids: list[AlternateIdentifier] = []
    seen: set[tuple[str, str]] = set()

    def add(value: str | None, id_type: str) -> None:
        if value and (value, id_type) not in seen:
            seen.add((value, id_type))
            alt_ids.append(AlternateIdentifier(alternateIdentifier=value, alternateIdentifierType=id_type))

    if fields.Taxon_ID is not None:
        add(str(fields.Taxon_ID), "NCBI-Taxonomy")
    for group in fields.Protein_and_Epitope:
        for acc in group.Protein_Accession:
            add(acc, "GenBank")

    return BVBRCEpitopeContainer.new(
        title=fields.Organism,
        creators=[],
        publisher=Publisher(name="BV-BRC"),
        alternateIdentifiers=alt_ids,
        bvbrc_epitope=fields,
    )
