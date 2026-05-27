"""BV-BRC Genome Globus Search document -> DataCite parser (aggregate, per organism).

Per-genome NCBI Taxon IDs, GenBank accessions, and BV-BRC Genome IDs are lifted
into alternateIdentifiers for cross-source linkage. Raises on malformed input.
"""

from __future__ import annotations

from typing import Any

from ..base import AlternateIdentifier, Publisher
from .model import BVBRCGenomeContainer, BVBRCGenomeFields


def parse_bvbrc_genome(content: dict[str, Any], subject: str | None = None) -> BVBRCGenomeContainer:
    fields = BVBRCGenomeFields.model_validate(content)

    alt_ids: list[AlternateIdentifier] = []
    seen: set[tuple[str, str]] = set()
    for genome in fields.Genome:
        for value, id_type in (
            (genome.NCBI_Taxon_ID, "NCBI-Taxonomy"),
            (genome.GenBank_Accessions, "GenBank"),
            (genome.Genome_ID, "BVBRC-Genome"),
        ):
            if value and (value, id_type) not in seen:
                seen.add((value, id_type))
                alt_ids.append(AlternateIdentifier(alternateIdentifier=value, alternateIdentifierType=id_type))

    record = BVBRCGenomeContainer.new(
        title=fields.Genome_Name,
        creators=[],
        publisher=Publisher(name="BV-BRC"),
        alternateIdentifiers=alt_ids,
        bvbrc_genome=fields,
    )
    if subject:
        record._source_subject = subject
    return record
