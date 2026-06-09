"""Thin runtime reader for the apecx synonym dictionary.

This package is the **user-facing arm**'s only dependency on the
dictionary stack. It carries:

- ``normalization`` — surface-form normalization (must match the
  build-time normalizer; pinned by test)
- ``enums`` — ``EntityType`` / ``ResolutionStatus`` / ``OntologyName``
- ``schema`` — pydantic ``DictionaryEntry`` + ``BuildManifest``
- ``sqlite_reader`` — read-only SQLite access
- ``loader`` — in-memory ``DictionaryIndex`` + process singleton
- ``lookup`` — ``lookup_entity()`` + ``LookupResult``
- ``bootstrap`` — download from published Globus path (CLI: apecx-dict-update)

Deliberately NOT here:
- Build pipeline (``apecx_integration.synonym_dictionary.build`` etc.)
- SQLite writer (build-time only)
- pandas / heavyweight slow-path fallback

Public surface — the only symbols downstream consumers should import:

    from apecx_harvesters.dict_reader import (
        lookup_entity,
        LookupResult,
        LookupCandidate,
        configure_dictionary_path,
        default_dictionary_path,
        EntityType,
        ResolutionStatus,
    )
"""

from __future__ import annotations

from apecx_harvesters.dict_reader.enums import (
    EntityType,
    OntologyName,
    ResolutionStatus,
)
from apecx_harvesters.dict_reader.loader import (
    DictionaryIndex,
    configure_dictionary_path,
    default_dictionary_path,
    get_dictionary_index,
)
from apecx_harvesters.dict_reader.lookup import (
    LookupCandidate,
    LookupResult,
    lookup_entity,
)
from apecx_harvesters.dict_reader.schema import (
    BuildManifest,
    DictionaryEntry,
)

__all__ = [
    "BuildManifest",
    "DictionaryEntry",
    "DictionaryIndex",
    "EntityType",
    "LookupCandidate",
    "LookupResult",
    "OntologyName",
    "ResolutionStatus",
    "configure_dictionary_path",
    "default_dictionary_path",
    "get_dictionary_index",
    "lookup_entity",
]
