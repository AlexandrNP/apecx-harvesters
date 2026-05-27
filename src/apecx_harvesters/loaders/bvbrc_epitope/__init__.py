"""BV-BRC Epitope loader: Globus Search index -> DataCite harmonization."""

from .model import (
    BVBRCEpitopeContainer,
    BVBRCEpitopeFields,
    EpitopeEntry,
    ProteinEpitopeGroup,
)
from .parser import parse_bvbrc_epitope

__all__ = [
    "BVBRCEpitopeContainer",
    "BVBRCEpitopeFields",
    "EpitopeEntry",
    "ProteinEpitopeGroup",
    "parse_bvbrc_epitope",
]
