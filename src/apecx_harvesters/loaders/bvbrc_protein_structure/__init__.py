"""BV-BRC Protein Structure loader: Globus Search index -> DataCite harmonization."""

from .model import (
    BVBRCProteinStructureContainer,
    BVBRCProteinStructureFields,
    PublicationStructureGroup,
    StructureEntry,
)
from .parser import parse_bvbrc_protein_structure

__all__ = [
    "BVBRCProteinStructureContainer",
    "BVBRCProteinStructureFields",
    "PublicationStructureGroup",
    "StructureEntry",
    "parse_bvbrc_protein_structure",
]
