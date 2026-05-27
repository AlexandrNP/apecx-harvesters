# Adding a Globus-Index Source (harmonization pattern)

How to add a new source that is read OUT of a Globus Search index (not fetched
by ID from an API). Companion to `GLOBUS_INDEX_HARMONIZATION_PLAN.md`. Keep this
terse; it is a recipe, not an overview.

## Architecture (already built)

```
scroll_index_records(idx)            # pipeline/globus_source.py -- full extraction (marker, no offset cap)
   -> globus_index_source(idx, parser)   # -> RetrievalResult stream (parse errors surfaced, never dropped)
   -> harmonize_index(idx)               # pipeline/harmonize.py -- + collision guard + drift guard + provenance
   -> publish_records(records, dest)     # -> GMetaList ingest (idempotent on canonical_uri)
```

CLI: `uv run python -m apecx_harvesters.scripts.harmonize_and_publish <src-uuid> [--publish <dest-uuid>]`.

## To add a source (≈ one loader package + one test)

1. **Capture a real fixture.** Scroll/query the index, save N real docs to
   `tests/fixtures/globus/<source>/sample.json` as `[{"subject", "content"}, ...]`.
   Survey field types + nested-array inner keys before modeling. Real data only.
2. **Model** `loaders/<source>/model.py`: a `<Source>Fields(BaseModel)` mirroring the
   FULL source content with `ConfigDict(strict=True, extra="forbid")` (the gate that
   catches unmodeled/renamed source fields -- do NOT relax it), plus the
   `@SchemaRegistry.register`-ed `<Source>Container(DataCite)` holding it under one
   nested field. Override `canonical_uri`.
3. **Parser** `loaders/<source>/parser.py`: `parse_<source>(content) -> <Source>Container`.
   `model_validate(content)` (raises on malformed -- never partial), promote what maps
   to base DataCite (title/description/creators/year), and lift external accessions
   (NCBI Taxonomy, PDB, UniProt, GenBank, PMID, VO, ...) into `alternateIdentifiers`.
4. **`__init__.py`**: import the model + parser so `@register` fires on package import
   (loaders are auto-imported by `loaders/__init__.py`).
5. **Register** in `pipeline/harmonize.py::SOURCE_REGISTRY`: `index_uuid -> (name, parser)`.
6. **Test** `tests/test_<source>.py`: parametrize over the real fixture; assert every doc
   parses, `canonical_uri` is as expected + unique, and `json.dumps(record.to_dict())`
   succeeds (the real ingest payload). Run `uv run pytest`.

## Invariants (non-negotiable -- reliability)

- **canonical_uri must be unique per source.** Key it on the source `subject` (Globus
  guarantees subject uniqueness within an index) or a verified-unique content id. A
  lowercasing slug already collapsed two records once ("Influenza Virus" vs "Influenza
  virus") -- DO NOT normalize away distinctions. The collision guard in
  `harmonize_index` is the full-set backstop and FAILS LOUD.
- **Strict + `extra="forbid"`** on every model. It has caught real schema drift twice
  (ProtaBank `Protein` objects; a `Taxon_ID` in 9/667 structure groups). A parse failure
  surfaces as a logged `RetrievalResult` error, never a silent drop.
- **`to_dict()` is the ingest payload**, not a strict round-trip target (DataCite's
  `strict=True` rejects its own enum-as-string `model_dump`). Test JSON-serializability.
- **Granularity: aggregate** (one record per source document), not explode-to-per-entity
  -- Globus indexes nested fields, so nested entities stay searchable. Revisit per source
  only if per-entity discovery is required.
- **Never treat a non-`SUCCESS` ingest task as success**; never publish a torn snapshot
  (`stable_total` False).
