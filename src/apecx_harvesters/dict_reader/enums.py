"""Enumerations for the apecx synonym dictionary.

Stable strings — they appear in the on-disk SQLite artifact + the lookup
API. Changing a value here is a contract-breaking change requiring a
schema-version bump on ``BuildManifest.schema_version``.

Ported verbatim from ``apecx_integration.synonym_dictionary.enums``
(2026-06-08).
"""

from __future__ import annotations

from enum import StrEnum


class EntityType(StrEnum):
    """The kind of entity a dictionary entry refers to."""

    PATHOGEN = "pathogen"
    VACCINE = "vaccine"
    DISEASE = "disease"
    GENE = "gene"
    GENOME = "genome"


class ResolutionStatus(StrEnum):
    """Provenance tag describing how a row's canonical IRI was determined."""

    ID_ANCHORED = "id_anchored"
    OLS_EXACT = "ols_exact"
    OLS_FUZZY = "ols_fuzzy"
    PROJECT_LOCAL = "project_local"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    FUZZY_RESOLVED = "fuzzy_resolved"
    TAXON_DELETED = "taxon_deleted"
    MINED_CORROBORATED = "mined_corroborated"
    MINED_OBSERVED = "mined_observed"


class OntologyName(StrEnum):
    """Authoritative-source identifier; pinned per dictionary build via
    ``BuildManifest.ontology_versions``."""

    NCBITAXON = "ncbitaxon"
    VO = "vo"
    DOID = "doid"
    GO = "go"
    NCBIGENE = "ncbigene"
    APECX_LOCAL = "apecx_local"
