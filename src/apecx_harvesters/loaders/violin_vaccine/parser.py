"""VIOLIN:Vaccine Globus Search document -> DataCite parser.

One record per vaccine. The Vaccine Ontology ID (VO) is lifted into
``alternateIdentifiers`` for cross-source linkage. Raises on malformed input.
"""

from __future__ import annotations

from typing import Any

from ..base import AlternateIdentifier, Publisher
from .model import VIOLINVaccineContainer, ViolinVaccineFields


def parse_violin_vaccine(content: dict[str, Any]) -> VIOLINVaccineContainer:
    fields = ViolinVaccineFields.model_validate(content)

    alt_ids: list[AlternateIdentifier] = []
    if fields.Vaccine_Ontology_ID:
        alt_ids.append(
            AlternateIdentifier(
                alternateIdentifier=fields.Vaccine_Ontology_ID,
                alternateIdentifierType="VO",
            )
        )

    return VIOLINVaccineContainer.new(
        title=fields.Vaccine_Name,
        description=fields.Description,
        creators=[],
        publisher=Publisher(name="VIOLIN"),
        alternateIdentifiers=alt_ids,
        violin_vaccine=fields,
    )
