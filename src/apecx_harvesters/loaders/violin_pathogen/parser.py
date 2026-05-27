"""VIOLIN:Pathogen Globus Search document -> DataCite parser.

One record per pathogen. The NCBI Taxonomy ID is lifted into
``alternateIdentifiers`` for cross-source linkage (e.g. to BVBRC:Genome or
VIOLIN:Gene by shared taxon). Raises on malformed input rather than dropping.
"""

from __future__ import annotations

from typing import Any

from ..base import AlternateIdentifier, Publisher
from .model import VIOLINPathogenContainer, ViolinPathogenFields


def parse_violin_pathogen(content: dict[str, Any]) -> VIOLINPathogenContainer:
    fields = ViolinPathogenFields.model_validate(content)

    alt_ids: list[AlternateIdentifier] = []
    if fields.NCBI_Taxonomy_ID is not None:
        alt_ids.append(
            AlternateIdentifier(
                alternateIdentifier=str(fields.NCBI_Taxonomy_ID),
                alternateIdentifierType="NCBI-Taxonomy",
            )
        )

    return VIOLINPathogenContainer.new(
        title=fields.Pathogen,
        description=fields.Pathogen_Description,
        creators=[],
        publisher=Publisher(name="VIOLIN"),
        alternateIdentifiers=alt_ids,
        violin_pathogen=fields,
    )
