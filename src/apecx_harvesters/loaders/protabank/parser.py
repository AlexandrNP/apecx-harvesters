"""ProtaBank Globus Search document -> DataCite parser.

One record per publication. Publication fields are promoted to base DataCite;
per-protein ProtaBank/PDB/UniProt accessions are lifted into alternateIdentifiers
for cross-source linkage. Raises on malformed input rather than dropping.
"""

from __future__ import annotations

from typing import Any

from ..base import AlternateIdentifier, Creator, Publisher
from .model import ProtaBankContainer, ProtaBankFields


def parse_protabank(content: dict[str, Any]) -> ProtaBankContainer:
    fields = ProtaBankFields.model_validate(content)

    creators = [Creator(name=a) for a in fields.Publication_Authors if a]

    alt_ids: list[AlternateIdentifier] = []
    seen: set[tuple[str, str]] = set()
    for p in fields.Protein:
        for value, id_type in ((p.ProtaBank_ID, "ProtaBank"), (p.PDB_ID, "PDB"), (p.Accession, "UniProt")):
            if value and (value, id_type) not in seen:
                seen.add((value, id_type))
                alt_ids.append(AlternateIdentifier(alternateIdentifier=value, alternateIdentifierType=id_type))

    return ProtaBankContainer.new(
        title=fields.Title,
        description=fields.Abstract,
        creators=creators,
        publisher=Publisher(name="ProtaBank"),
        publicationYear=fields.Publication_Year,
        alternateIdentifiers=alt_ids,
        protabank=fields,
    )
