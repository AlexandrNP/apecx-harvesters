"""AntiviralDB Globus Search document -> DataCite parser.

One record per virus. PDB structure codes and PubMed reference IDs found in the
nested drug data are lifted into ``alternateIdentifiers`` so harmonized records
carry cross-source linkage (e.g. to BVBRC:Protein_Structure via PDB, or to
publications via PMID) without any fuzzy matching.
"""

from __future__ import annotations

from typing import Any

from ..base import AlternateIdentifier, Publisher
from .model import AntiviralDBContainer, AntiviralDBFields


def parse_antiviraldb(content: dict[str, Any]) -> AntiviralDBContainer:
    """Parse one AntiviralDB index document into an ``AntiviralDBContainer``.

    Raises on malformed input (Pydantic ValidationError) rather than emitting a
    partial record -- callers surface the failure, never silently drop data.
    """
    fields = AntiviralDBFields.model_validate(content)

    alt_ids: list[AlternateIdentifier] = []
    seen: set[tuple[str, str]] = set()
    for pd in fields.Protein_and_Drug:
        for pmid in pd.Ref:
            key = (str(pmid), "PMID")
            if key not in seen:
                seen.add(key)
                alt_ids.append(AlternateIdentifier(alternateIdentifier=str(pmid), alternateIdentifierType="PMID"))
        for drug in pd.Drug:
            if drug.PDB:
                key = (drug.PDB, "PDB")
                if key not in seen:
                    seen.add(key)
                    alt_ids.append(AlternateIdentifier(alternateIdentifier=drug.PDB, alternateIdentifierType="PDB"))

    return AntiviralDBContainer.new(
        title=fields.Virus,
        creators=[],
        publisher=Publisher(name="AntiviralDB"),
        alternateIdentifiers=alt_ids,
        antiviraldb=fields,
    )
