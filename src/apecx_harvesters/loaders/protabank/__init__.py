"""ProtaBank loader: Globus Search index -> DataCite harmonization.

Importing registers ``ProtaBankContainer`` with the ``SchemaRegistry``. Records
are read from a Globus Search index by ``apecx_harvesters.pipeline.globus_source``,
not fetched by ID.
"""

from .model import ProtaBankContainer, ProtaBankFields
from .parser import parse_protabank

__all__ = ["ProtaBankContainer", "ProtaBankFields", "parse_protabank"]
