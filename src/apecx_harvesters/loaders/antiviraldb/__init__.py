"""AntiviralDB loader: Globus Search index -> DataCite harmonization.

Importing this package registers ``AntiviralDBContainer`` with the
``SchemaRegistry`` (via the decorator in ``model``). No BaseHarvester is
defined: AntiviralDB records are read out of a Globus Search index by
``apecx_harvesters.pipeline.globus_source``, not fetched by ID from an API.
"""

from .model import AntiviralDBContainer, AntiviralDBFields, DrugEntry, ProteinDrugEntry
from .parser import parse_antiviraldb

__all__ = [
    "AntiviralDBContainer",
    "AntiviralDBFields",
    "DrugEntry",
    "ProteinDrugEntry",
    "parse_antiviraldb",
]
