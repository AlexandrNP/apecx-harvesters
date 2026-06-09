"""Pydantic schemas for the apecx synonym dictionary artifact.

These models define the **on-disk and over-wire contract** between the
build/mining arm (apecx-harvesters) and the runtime arm (the
``apecx_harvesters.dict_reader`` lookup library used by the agent-skill
and any other consumer).

Every model sets ``extra="forbid"`` per the workspace pydantic_extra_forbid
rule — a typo in YAML or JSON that would silently use a default is
preferable to fail loudly.

Schema versioning:
- The literal version string is on :class:`BuildManifest.schema_version`.
- Bumping it is a coordinated cross-repo change.
- Reader compatibility: the reader refuses to load a dict whose major
  version is not in its ``SUPPORTED_SCHEMA_MAJOR`` set.

Ported from ``apecx_integration.synonym_dictionary.schema`` (2026-06-08).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from apecx_harvesters.dict_reader.enums import (
    EntityType,
    OntologyName,
    ResolutionStatus,
)

_FROZEN_FORBID = ConfigDict(extra="forbid", frozen=True)


class ResolutionResult(BaseModel):
    """Per-record output emitted alongside enriched harvester CSVs."""

    model_config = _FROZEN_FORBID

    canonical_iri: str | None
    canonical_label: str | None
    canonical_ontology: OntologyName | None
    synonyms: tuple[str, ...] = ()
    resolution_status: ResolutionStatus
    resolution_confidence: float = Field(ge=0.0, le=1.0)
    dictionary_version: str


class DictionaryEntry(BaseModel):
    """One entry per ``(entity_type, canonical_iri)`` tuple."""

    model_config = _FROZEN_FORBID

    entity_type: EntityType
    canonical_iri: str
    canonical_label: str
    synonyms: tuple[str, ...] = ()
    ontology: OntologyName
    ontology_version: str
    source_records: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    resolved_at: datetime


class BuildManifest(BaseModel):
    """Metadata describing one published dictionary build."""

    model_config = _FROZEN_FORBID

    schema_version: str = Field(default="1.0.0")
    dictionary_version: str
    built_at: datetime
    harvester_version: str | None = None
    ontology_versions: dict[str, str]
    record_counts_per_entity_type: dict[EntityType, int]
    unresolved_count: int = Field(ge=0)
    record_count_total: int = Field(ge=0)
