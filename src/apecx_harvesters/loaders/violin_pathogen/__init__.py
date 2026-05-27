"""VIOLIN:Pathogen loader: Globus Search index -> DataCite harmonization.

Importing this package registers ``VIOLINPathogenContainer`` with the
``SchemaRegistry``. No BaseHarvester: records are read out of a Globus Search
index by ``apecx_harvesters.pipeline.globus_source``, not fetched by ID.
"""

from .model import VIOLINPathogenContainer, ViolinPathogenFields
from .parser import parse_violin_pathogen

__all__ = ["VIOLINPathogenContainer", "ViolinPathogenFields", "parse_violin_pathogen"]
