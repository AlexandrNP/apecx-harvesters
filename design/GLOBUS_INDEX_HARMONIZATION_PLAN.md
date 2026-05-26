# Plan: Harmonize 9 Globus Search Indices into One Public Index

Status: PLAN (no code yet). Authored 2026-05-26; updated 2026-05-26 after verifying
against the live GitHub remote and empirically profiling the 9 source indices.

Goal: scrape every record from 9 existing Globus Search indices, harmonize via this
pipeline, and publish to a **new public Globus Search index**. Current destination
allocation is 1 MB (to be expanded to full size later); the plan is sequenced so only
the final scale phase depends on that expansion.

## Codebase baseline (IMPORTANT — verified 2026-05-26)

Canonical repo: **github.com/abought/apecx-harvesters**, HEAD `b47bc86` (2026-05-04).
This plan now lives IN a working checkout of that remote, on branch
`feature/globus-index-harmonization`. (A stale pre-2026-04-14 copy still exists at
`/Users/onarykov/Downloads/apecx-cowork/apecx-harvesters/` — it predates the `workers/`
deletion and is NOT a git checkout; do not build against it.) All file:line references
below are against the remote HEAD.

## Source indices

| Source | Index UUID | ~docs (q=*) |
|---|---|---|
| ProtaBank | `9e902471-9c77-49d3-a12c-516cc0808c3b` | 1,643 |
| AntiviralDB | `e8097a7b-a280-4031-9df1-1e837193494f` | 35 |
| VIOLIN:Pathogen | `a67c7310-5115-446f-bfb6-d889bc4efa06` | 217 |
| VIOLIN:Vaccine | `c5ff64fd-5e78-4cf0-848a-2788a78e71cd` | 3,507 |
| VIOLIN:Gene | `205c1a5b-c9bd-4137-8ac6-ca879c9a4f9c` | 4,063 |
| BVBRC:Epitope | `f873c7d5-8652-466d-806b-b5da46f0f786` | 442 |
| BVBRC:Protein_Structure | `439f2b66-09d4-4141-8c3d-b4dc18ef8a07` | 4,566 |
| BVBRC:Protein | `249efe96-14d2-443d-ad47-5621ed43a343` | 24,902 |
| BVBRC:Genome | `b676edbe-3286-4514-bc13-5cbe891c4bb1` | 745,917 (volatile*) |

Total was ≈785k at first read; ≈570k and changing as of 2026-05-26 — *BVBRC:Genome is
mid-reingest (see Source index profile). It dominates scale (~90%+).* Read access verified
for all 9 indices on 2026-05-26 using the stored confidential client (`nanobrain-globus`).
Documents are document-level access-controlled (anonymous query returns total=0); the
new index must set `visible_to: ["public"]` for public access.

## Source index profile (empirical sample, 2026-05-26)

Sampled up to 50 docs/index via `search(q=*)` with the confidential client.

- **Metadata catalogs, not data stores.** No raw sequences or structure files are stored
  inline anywhere — the single largest leaf field across all 9 indices is 2,487 B
  (VIOLIN:Pathogen `Pathogen_Description`); BVBRC:Genome's largest field is 33 B,
  BVBRC:Protein's 52 B. Bulk payloads are *referenced* by accession/ID (`PDB_ID`,
  `Genbank_Accession`, `NCBI_*_GI`, `Taxon_ID`).
- **The 32 KB per-field cap does NOT apply to this content.** These indices were themselves
  populated via Globus Search ingest, so every field is already ≤32 KB by construction;
  re-ingesting their content cannot trip the per-field cap. 0/sample docs had a field
  ≥30 KB. (This RETIRES the earlier "32 KB vs. sequences" open question.)
- **The binding limit is per-DOCUMENT size, via nested aggregates.** Five sources are
  deeply nested (one doc = an organism/grouping with arrays of sub-entities): BVBRC:Epitope
  (`Protein_and_Epitope[]`, docs up to **4.17 MB**), BVBRC:Protein_Structure (up to 876 KB),
  BVBRC:Protein, BVBRC:Genome (`Genome[]`), AntiviralDB (`Protein_and_Drug[].Drug[]`, up to
  82 KB). The other four are flat, one-entity-per-doc (ProtaBank, VIOLIN:Pathogen/Vaccine/
  Gene). The 10 MB `GMetaList` per-entry guard (`sinks.py:84-88`) — not 32 KB truncation —
  is the relevant ingest constraint.
- **Field completeness is high** in the sample (nearly all top-level fields present in 100%
  of docs; ProtaBank `Publication_Year` at 92% is the only sparse one). Caveat: 50-doc
  first-page sample; inner fields of the nested arrays were not presence-profiled.
- **BVBRC:Genome is volatile — mid-reingest.** Its `total` read 745,917 earlier on
  2026-05-26, then ~522,914, then climbed 533,595 → 534,659 → 536,232 across 6 s
  (~500 docs/s) — a clear-and-rebuild in progress. The other 8 indices' totals are stable.

## Decisions locked

- **Output**: a new public Globus Search index (symmetric with the sources; matches this
  pipeline's `GMetaList` output).
- **Code home**: this repo (apecx-harvesters), authorized writable for this task — work on
  a branch of the github.com/abought/apecx-harvesters remote (see Codebase baseline).
- **Harmonization target**: the established house pattern — `DataCite` core + nested
  per-source container (see CLAUDE.md "Architecture"). Not a new schema family.

## Current-state findings (what's built vs. greenfield, @ remote HEAD)

- **Scrape**: `agents/globus_search/client.py` (in apecx-mcp-integration) and
  `search_demo/agent-skill/scripts/fetch.py` (here) both query via
  `SearchClient.post_search()` (read-only). Offset is capped (~10k) by Globus Search → it
  **cannot** fully extract BVBRC:Genome. Full extraction needs the marker-based
  **`scroll_query`** API — not yet written. `fetch.py` is a reusable auth+query pattern.
- **Pipeline direction mismatch (linchpin intact)**: `BaseHarvester`
  (`loaders/base/retrieve.py`) is **ID → fetch from remote API**; our records already
  live in Globus Search. Globus Search is **output-only** here. Clean seam: `run()`'s
  `source` is `AsyncIterator[RetrievalResult[T]]` (`pipeline/run.py:32-36`) — a
  Globus-reading async generator drops in and bypasses `BaseHarvester` caching entirely.
- **None of the 9 sources are implemented.** Existing loaders: biorxiv, crossref,
  datacite, doi, emdb, openalex, pdb, pubmed. ProtaBank/AntiviralDB/VIOLIN/BVBRC are
  parked in `design/API_REFERENCE.md`; epitope/IEDB fit is flagged open in
  `design/OPEN_QUESTIONS.md`.
- **Publish**: `sinks.to_gmetalist` (`pipeline/sinks.py:92`) emits correct `GMetaList`
  batches (`visible_to or ["public"]`, <10 MB; truncates >32 KB string fields,
  `sinks.py:52-67` — not exercised by this content, see profile). **Ingest is still
  CLI-only** — `globus search ingest` (`scripts/ingest_gsearch.sh:20`). No programmatic
  `submit_ingest`/`create_index` anywhere (the "bulk ingest" commit only activated EMDB).
  Publish can REUSE the CLI path; the gap is creating a public destination index +
  orchestrating ingest.
- **No real-data integration test** exists (fixtures/mocks only). Per workspace rules,
  every component we add ships with a real-subset integration test.

## Architecture

```
9 source indices ──scroll_query scrape──▶ raw GMeta records (subject + content)
        │
        ▼  (this repo, new code)
 GlobusIndexSource (async generator, emits RetrievalResult)
        │
        ▼
 pipeline.run(source=...) ──▶ 9 per-source parsers ──▶ DataCite + nested container
        │
        ▼
 sinks.to_gmetalist ──[globus search ingest]──▶ 1 NEW public Globus Search index
                                                 (visible_to: ["public"])
```

Reused as-is (verified present @ remote HEAD):
- `pipeline.run` (streaming, backpressure), `SchemaRegistry`, `sinks.to_gmetalist`
  (32 KB-field-safe, 10 MB-batched), the `RetrievalResult`/`iter_results` contract,
  `DataCite` base schema, `build_globus_app` auth (nanobrain).
- **`loaders/base/rate_limit.py::RateLimiter` + `loaders/base/http_retry.py::http_request`**
  — drop into the new scroll scraper for rate limiting + retry/backoff.
- **`search_demo/agent-skill/scripts/fetch.py`** — copy-able `SearchClient.post_search`
  query pattern; `--dry-run`/`count()` (`scripts/search_topic.py:42-64`) — count pattern.
- Existing CLI ingest `scripts/ingest_gsearch.sh` — reuse for the publish step.

Built new: scroll scraper (`scroll_query`), `GlobusIndexSource`, 9 parsers + 9 `DataCite`
subclasses + 9 schema registrations, and the create-public-index + ingest orchestration.

## Phases (risk-ordered; each has a hard success criterion)

**Phase 0 — End-to-end spike on AntiviralDB (35 docs).**
Scroll-scrape all 35 → inspect real content schema → harmonize one record → ingest to a
throwaway index via the existing CLI path. (Note: AntiviralDB is a nested aggregate,
`Protein_and_Drug[]`, docs up to 82 KB — exercises the nested shape, not the flat one.)
Success: throwaway index returns 35 records to an authenticated query, 0 to anonymous
until `visible_to: public` is set, then 35. (Real-data integration test at trivial scale.)

**Phase 1 — Full-extraction scrape reader (`scroll_query`).**
Marker-paginated, resumable; parameterized by index UUID; emits `RetrievalResult`s; reuses
`RateLimiter` + `http_request`. Records each index's `total` at start + end and flags drift.
Success: scraped count == `search(q=*)` total for indices of 35 / 217 / 4063, with a clean
resume after an interrupt.

**Phase 2 — Schema discovery + harmonization (the bulk of the effort).**
Per source: dump content schema → decide record granularity (per-document aggregate vs.
explode nested arrays to per-entity) → write + register a `DataCite` subclass + parser →
per-source real-subset test. Each source's DataCite fit is decided here on real data
(deferred items → OPEN_QUESTIONS.md).
Success: all 9 sources have a parser whose output validates against its schema on a real
≥20-record subset, with a recorded run.

**Phase 3 — Pipeline wiring + provenance.**
`GlobusIndexSource` → `pipeline.run()` → harmonized `GMetaList`; thread G4
`ProvenanceContext` for source→record lineage.
Success: one command turns a source index UUID into harmonized GMetaList chunks with
provenance.

**Phase 4 — Publish layer.**
Create a public destination index (`SearchClient.create_index` or the Globus web UI) +
drive ingest (reuse `ingest_gsearch.sh` or wrap `submit_ingest`) with public ACL;
idempotent re-ingest keyed on `canonical_uri`. Respect the 10 MB per-entry / batch guard
for the multi-MB nested-aggregate docs.
Success: harmonized records from the two smallest indices (35 + 217, well within 1 MB) are
queryable on the new **public** index by an **unauthenticated** client.

**Phase 5 — Scale to full + verify.**
Ramp to the BVBRC:Genome volume (~523k–746k, currently volatile): batch tuning, ingest-rate
backoff, allocation expansion. **Gate: only scrape BVBRC:Genome once its `total` has
stabilized** (it was mid-reingest on 2026-05-26 — a scroll started mid-rebuild captures a
torn snapshot).
Success: all records ingested; anonymous `search(q=*)` totals on the new index match the
per-source totals recorded at scrape time; throughput documented.

## Cross-cutting

- **Real-data integration tests, not mocks** — one per phase against a real subset.
- **Scale — two landmines are already solved upstream** (no longer plan work): the
  worker-pool in-memory accumulation (the `workers/` package is deleted; `run.py` is
  streaming) and the mega-folder cache (now 256-way MD5-prefix sharding,
  `retrieve.py:72-80`). Remaining scale work is batch tuning + ingest backoff.
- **Source volatility** — BVBRC:Genome is being actively re-ingested (`total` dropped
  745,917 → ~523k then climbed ~500 docs/s on 2026-05-26). A scroll started mid-rebuild
  captures a torn snapshot. Mitigation: record each index's `total` at scrape start + end
  and abort/reconcile on drift; **defer BVBRC:Genome scraping until its total stabilizes.**
  The other 8 are stable.
- **1 MB → full allocation** — Phases 0/4 fit inside 1 MB using the 35- and 217-doc
  indices; only Phase 5 waits on the expanded allocation.

## Open questions (decide during Phase 2 schema discovery)

- **Record granularity** for the 5 nested sources (BVBRC:*, AntiviralDB): one harmonized
  record per source-document (an organism aggregate) vs. exploding the nested arrays to one
  record per entity (per epitope / per protein). Determines the shape + count of the public
  index. *(Replaces the retired 32 KB-field question — see Source index profile.)*
- **Per-document size**: nested aggregates reach multi-MB (Epitope up to 4.17 MB); the
  publish path must respect the 10 MB per-entry / `GMetaList` batch guard. Field-level
  32 KB truncation is moot here (no field exceeds ~2.5 KB).
- Per-source DataCite fit for the 9 sources — what promotes to base `DataCite` vs. a nested
  container. Epitope fit overlaps the IEDB harvester question in OPEN_QUESTIONS.md.
- **Entity linking** across sources (genome ↔ protein ↔ epitope ↔ vaccine/pathogen): the
  pipeline does **zero** entity resolution today. In scope? If yes, additional greenfield.
  (Note: the sources already carry cross-reference IDs — `Taxon_ID`, `NCBI_*`, `PDB_ID`,
  `vaccine_pathogen_id` — so linking by shared accessions is feasible without fuzzy matching.)
- `Subject.subjectScheme` / `valueUri` cross-database ontology — already deferred in
  OPEN_QUESTIONS.md; same blocker applies here.
- Destination: new index vs. augmenting the existing harvester index
  (`e74bf12a-d0dd-4d19-a965-03f4936db851`).
