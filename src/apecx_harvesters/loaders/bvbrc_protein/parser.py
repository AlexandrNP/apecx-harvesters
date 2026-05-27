"""BV-BRC Protein Globus Search document -> DataCite parser (aggregate, per organism).

Per-feature BV-BRC Genome IDs and protein accessions are lifted into
alternateIdentifiers for cross-source linkage. Raises on malformed input.
"""

from __future__ import annotations

from typing import Any

from ..base import AlternateIdentifier, Publisher
from .model import BVBRCProteinContainer, BVBRCProteinFields


def parse_bvbrc_protein(content: dict[str, Any]) -> BVBRCProteinContainer:
    fields = BVBRCProteinFields.model_validate(content)

    alt_ids: list[AlternateIdentifier] = []
    seen: set[tuple[str, str]] = set()
    for feat in fields.Protein:
        for value, id_type in ((feat.Genome_ID, "BVBRC-Genome"), (feat.Accession, "GenBank")):
            if value and (value, id_type) not in seen:
                seen.add((value, id_type))
                alt_ids.append(AlternateIdentifier(alternateIdentifier=value, alternateIdentifierType=id_type))

    return BVBRCProteinContainer.new(
        title=fields.Genome,
        creators=[],
        publisher=Publisher(name="BV-BRC"),
        alternateIdentifiers=alt_ids,
        bvbrc_protein=fields,
    )
