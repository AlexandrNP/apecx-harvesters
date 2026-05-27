# Globus-Index Harmonization — Detailed Task Breakdown & Acceptance Criteria

Companion to `GLOBUS_INDEX_HARMONIZATION_PLAN.md`. Per-phase task lists with tests and
**real-data acceptance criteria** (expected outcomes stated with concrete numbers). All
acceptance criteria are validated against the live source indices and the real destination
index — no mock-only sign-off.

Substrate decision (2026-05-26): **apecx-harvesters-native** (NOT nanobrain — the
nanobrain-native requirement was withdrawn after review; nanobrain is ceremony for a
deterministic bulk ETL). Code home: this repo, branch `feature/globus-index-harmonization`.

## Verified gates (2026-05-26)

- **Read access:** all 9 source indices queryable with the confidential client (`nanobrain-globus`).
- **Write access:** CONFIRMED — the confidential client can create + owns indices.
- **Dev destination index:** `4103190a-019d-4c0b-b8e3-b93817908141`
  ("APECx Harmonized Biomedical Index (dev)", trial, `max_size_in_mb: 1`).
- **Constraints (Globus Search trial):** max **3 trial indices** per identity;
  **1 MB** max size; trial indices non-durable (~30-day cleanup). Bigger size / production
  durability = manual `support@globus.org` conversion → **external blocker for Phase 5**.

## Index registry (created indices)

| UUID | Name | Type | Holds | Created |
|---|---|---|---|---|
| `4103190a-019d-4c0b-b8e3-b93817908141` | APECx Harmonized Biomedical Index (dev) | trial, 1 MB | 35 AntiviralDB records (public, verified) | 2026-05-26 |

Newly created indices are appended here. **Hard limit:** Globus trial cap = **3 indices /
identity** (1 used → **2 slots remain**), each **1 MB**. One-index-per-source is therefore
impossible on trial (8 remaining sources, 2 slots), and most sources exceed 1 MB regardless
(see "Real-data anchors"). Full multi-source ingestion is gated on the `support@globus.org`
allocation bump.

## Real-data anchors (measured 2026-05-26)

| Source | UUID (prefix) | docs | shape | max doc | notes |
|---|---|---:|---|---:|---|
| AntiviralDB | e8097a7b | 35 | nested `Protein_and_Drug[]` | 82 KB | smallest; Phase 0 target |
| VIOLIN:Pathogen | a67c7310 | 217 | flat (13 fields) | 5.3 KB | Phase 1/4 target |
| VIOLIN:Gene | 205c1a5b | 4,063 | flat (18 fields) | 0.8 KB | |
| VIOLIN:Vaccine | c5ff64fd | 3,507 | flat (29 fields) | 5.3 KB | |
| ProtaBank | 9e902471 | 1,643 | flat (7 fields) | 3.0 KB | |
| BVBRC:Epitope | f873c7d5 | 442 | nested `Protein_and_Epitope[]` | **4.17 MB** | single doc > 1 MB index! |
| BVBRC:Protein_Structure | 439f2b66 | 4,566 | nested | 876 KB | |
| BVBRC:Protein | 249efe96 | 24,902 | nested `Protein[]` | 7.8 KB | |
| BVBRC:Genome | b676edbe | ~523k–746k | nested `Genome[]` | 4.3 KB | **volatile (mid-reingest)** |

What fits in a 1 MB trial index: AntiviralDB (~210 KB), VIOLIN:Pathogen (~540 KB).
Everything else needs the allocation bump.

---

## Phase 0 — End-to-end spike on AntiviralDB (35 docs)

Goal: prove the whole chain (scrape → harmonize → ingest → public-verify) on the smallest
real index before investing in 9 parsers.

**Tasks**
- T0.1 Scaffold `loaders/globus_search/` (or `pipeline/globus_source.py`): an async
  `globus_index_records(index_uuid, *, client) -> AsyncIterator[dict]` yielding each GMeta
  entry's `content`. Phase-0 may use `post_search` paging (35 docs « offset cap); Phase 1
  replaces with `scroll_query`.
- T0.2 Inspect AntiviralDB's real content schema (dump 5 docs to a fixture file under
  `tests/fixtures/globus/antiviraldb/`).
- T0.3 Write `AntiviralDBContainer(DataCite)` + `parse_antiviraldb(content) -> AntiviralDBContainer`,
  registered via `@SchemaRegistry.register`. `canonical_uri` = `antiviraldb:{stable id}`
  (derive a stable id; if none, MD5 of the canonical content per the IEDB prior-art pattern).
- T0.4 Wire `globus_index_records → parse → to_gmetalist` and ingest into
  `4103190a` via `globus search ingest` (or `submit_ingest`).
- T0.5 Set `visible_to: ["public"]` on the entries (already the `to_gmetalist` default) and
  confirm the public-access transition.

**Tests**
- Unit (`tests/test_antiviraldb.py`): feed the captured fixture (T0.2) → assert the parsed
  `AntiviralDBContainer` validates and key fields map (Virus → ..., Protein_and_Drug[].Drug[].EC50 preserved).
- Integration (`tests/integration/test_phase0_antiviraldb_e2e.py`, real-network, opt-in):
  scrape live → harmonize → ingest into a **dedicated Phase-0 index** → query back.

**Acceptance criteria (real data)**
1. Scrape yields exactly **35** records (== `search(q=*)` total for AntiviralDB).
2. All 35 parse into valid `AntiviralDBContainer` with non-empty `canonical_uri`; 0 parse failures.
3. After ingest, an **authenticated** `search(q=*)` on the dest index returns **35**.
4. Before setting public ACL, an **anonymous** query returns **0**; after, it returns **35**.
   (This is the public-access proof — the same anon=0/auth=N signature measured on the sources.)
5. Total ingested size < 1 MB (fits the trial cap; expect ~210 KB).

**Expected outcome:** the dev index holds 35 publicly-searchable harmonized AntiviralDB
records; the chain is proven end-to-end on real data.

---

## Phase 1 — Full-extraction scrape reader (`scroll_query`)

Goal: a correct, resumable bulk reader that survives the offset cap and large indices.

**Tasks**
- T1.1 Implement `scroll_query`-based extraction in `globus_index_records` (marker pagination,
  not `post_search` offset). Reuse `loaders/base/rate_limit.py::RateLimiter` +
  `http_retry.http_request` for throttling/backoff.
- T1.2 Record `total` at scrape start and end; raise/flag if they differ (drift guard).
- T1.3 Resumability: persist the last marker; on restart, continue from it.
- T1.4 Per-record emit as the pipeline's `RetrievalResult` so it feeds `pipeline.run()`.

**Tests**
- Integration (real): scrape AntiviralDB (35), VIOLIN:Pathogen (217), VIOLIN:Gene (4,063).
- Resume test: interrupt mid-scroll on VIOLIN:Gene, restart, assert no dupes/gaps.

**Acceptance criteria (real data)**
1. Scraped count **== `search(q=*)` total** for all three (35 / 217 / 4063), exact.
2. Zero duplicate `subject`s across the full scroll.
3. Interrupted-then-resumed run produces the identical record set as an uninterrupted run
   (set equality on `subject`).
4. Start/end `total` drift on a stable index is 0; the guard fires on a simulated drift.

**Expected outcome:** any source index (except mid-reingest Genome) extractable completely
and resumably.

---

## Phase 2 — Schema discovery + harmonization (the bulk of the work)

Goal: a validated `DataCite` subclass + parser per source, with the record-granularity
decision made on real data.

**Tasks (per source × 9)**
- T2.x.a Dump the real content schema (field inventory + sample) to `tests/fixtures/globus/<source>/`.
- T2.x.b Decide **record granularity**: flat sources → 1 record/doc; nested sources
  (BVBRC:*, AntiviralDB) → decide per-document-aggregate vs. explode-to-per-entity.
  Record the decision + rationale in `OPEN_QUESTIONS.md` resolution notes.
- T2.x.c Write `<Source>Container(DataCite)` + `parse_<source>` + `@SchemaRegistry.register`.
  Promote to base DataCite fields where they fit; domain detail in a nested container
  (house convention). Cross-reference IDs (`Taxon_ID`, `NCBI_*`, `PDB_ID`, `vaccine_pathogen_id`)
  preserved in `alternateIdentifiers`/`relatedIdentifiers`.
- T2.x.d Per-source unit test with captured fixture.

**Tests**
- Unit per source (captured payload → validated container).
- Integration per source: harmonize a real ≥20-record subset (full set for the small indices).

**Acceptance criteria (real data)**
1. Each source: a real ≥20-record subset (or the full set if smaller) harmonizes with **0
   validation errors** and **0 dropped records** (every input doc → ≥1 output record).
2. Every output record has a non-empty, unique `canonical_uri`.
3. Schema round-trips: `Container.model_validate(record.to_dict())` succeeds for every record.
4. For nested sources, the explode/aggregate decision is documented and the record count
   matches the chosen granularity (e.g. if exploding epitopes, output count == sum of
   `Protein_and_Epitope[].Epitope[]` lengths).

**Expected outcome:** 9 source parsers, each proven on real data; harmonized records carry
cross-source linkage IDs.

---

## Phase 3 — Pipeline wiring + provenance

Goal: one command turns a source index UUID into harmonized, provenance-stamped GMetaList
chunks.

**Tasks**
- T3.1 `aggregate_globus.py` (mirror `aggregate_gsearch.py`): for a given index UUID,
  scrape → parse → `to_gmetalist` → write gzip chunks under `output/<ts>/<source>/`.
- T3.2 Stamp provenance into each record (source index UUID, scrape timestamp, scraped
  `total`, pipeline version) — a `_provenance` block in the harmonized content.
- T3.3 CLI entry point (pyproject `[project.scripts]`).

**Tests**
- Integration: run the command on AntiviralDB + VIOLIN:Pathogen end to end.

**Acceptance criteria (real data)**
1. Output chunk record count == scraped record count == source `total` (no loss across the wire).
2. Every chunk is valid `GMetaList` JSON, each entry < 10 MB (per-entry guard).
3. Every record carries a complete `_provenance` block with the real source UUID + timestamp.

**Expected outcome:** reproducible harmonized output with auditable lineage.

---

## Phase 4 — Publish layer

Goal: idempotent ingest into the public destination index, verified by an unauthenticated
client.

**Tasks**
- T4.1 `create_index` helper (idempotent: reuse a UUID if provided, else create + record it).
- T4.2 Ingest driver: POST each `GMetaList` chunk via `submit_ingest` (or `ingest_gsearch.sh`),
  poll task status to completion, fail loud on any rejected entry.
- T4.3 Idempotent re-ingest keyed on `canonical_uri` (re-running replaces, not duplicates).
- T4.4 Respect 10 MB per-entry / batch guard for nested aggregates.

**Tests**
- Integration: publish AntiviralDB (35) + VIOLIN:Pathogen (217) — 252 records, < 1 MB total.
- Re-ingest test: run twice, assert the index total is stable (no duplication).

**Acceptance criteria (real data)**
1. After publish, **anonymous** `search(q=*)` on the dest index returns **252** (35 + 217).
2. Every Globus ingest task reaches `SUCCEEDED`; 0 rejected entries (fail loud otherwise).
3. Re-running the publish leaves the total at 252 (idempotent — proven, not assumed).
4. A spot-checked harmonized record is retrievable by `subject` and matches the source data.

**Expected outcome:** 252 harmonized records publicly searchable on the dev index.

---

## Phase 5 — Scale to full + verify (BLOCKED on allocation)

Goal: full corpus ingested into a production (non-trial) index.

**Hard blockers (external):**
- Trial 1 MB cap → needs `support@globus.org` conversion to non-trial + size bump.
- BVBRC:Genome mid-reingest → defer scrape until its `total` stabilizes.

**Tasks**
- T5.1 Request index conversion/allocation (human step).
- T5.2 Granularity at scale for nested sources; batch tuning + ingest backoff.
- T5.3 Genome stability gate: poll `total` over a window; only scrape when flat.
- T5.4 Full scrape → harmonize → publish for all 9.

**Acceptance criteria (real data)**
1. Per-source ingested count matches the `total` recorded at scrape time (within the
   reconciliation window for any still-changing source).
2. Anonymous `search(q=*)` on the production index returns the summed per-source totals.
3. Throughput (records/min for scrape + ingest) documented.

**Expected outcome:** the full harmonized corpus publicly searchable.

---

## Implementation log (append-only)

- 2026-05-26 — Gates verified. Created dev dest index `4103190a-019d-4c0b-b8e3-b93817908141`
  (trial, 1 MB). Confirmed confidential-client write access. Began Phase 0.
- 2026-05-26 — **Phase 0 COMPLETE (PASS) on real data.** Built `loaders/antiviraldb/`
  (model + parser, registered), `pipeline/globus_source.py` (index→RetrievalResult source +
  env-based auth), and `scripts/phase0_antiviraldb.py` (spike driver). `tests/test_antiviraldb.py`
  — 38 tests green against the full 35-record real fixture; ruff clean. Live run: scraped
  35 == source total; ingested 35 in 1 batch (task `083ec711` SUCCESS); dest auth total 35;
  **dest anonymous total 35 → public access proven** (sources return 0 anon). All acceptance
  criteria PASS.
  - Finding: the uniqueness test caught a real overwrite-on-ingest bug — "Influenza Virus" vs
    "Influenza virus" (case-only-distinct source records) collided under a lowercasing slug;
    Globus keys entries on `subject`, so one would have silently overwritten the other.
    Fixed by keying `canonical_uri` on the exact (unique) source name. Logged as a data-quality
    open question.
  - Next: Phase 1 (`scroll_query` reader for indices beyond the 35-doc/offset range) + Phase 2
    (remaining 8 parsers, each with a captured real fixture + tests).
- 2026-05-26 — Phase 2 parser #2: **VIOLIN:Pathogen** (`loaders/violin_pathogen/`). Flat schema,
  1 record/pathogen, `canonical_uri = violin-pathogen:{id}` (real unique id); NCBI Taxonomy ID
  lifted to `alternateIdentifiers` for cross-linking. `tests/test_violin_pathogen.py` green on
  the full 217-record real fixture; whole suite 258 green, ruff clean. Ingest deferred (awaits
  allocation bump). 7 sources remain (ProtaBank, VIOLIN:{Vaccine,Gene}, BVBRC:{Epitope,
  Protein_Structure,Protein,Genome}).
  - Finding: DataCite is `strict=True`, so `to_dict()` (`model_dump(mode="json")`) emits enum
    fields (e.g. `descriptionType`) as strings that strict re-validation won't coerce back. The
    ingest path never re-validates (the JSON dict goes straight to Globus), so it's benign for
    ingestion -- but a consumer reloading records into the DataCite model strictly would need
    `strict=False`. Affects every parser that sets a description (pdb/pubmed too). Tests now
    assert JSON-serializability of `to_dict()` rather than a strict round-trip.
- 2026-05-26 — Phase 2 parsers #3-5: **ProtaBank, VIOLIN:Vaccine, VIOLIN:Gene** (flat tier
  complete). Each: `<Source>Container(DataCite)` + parser + full real fixture (120-doc samples)
  + tests. Full suite **1181 green**, ruff clean. Ingest deferred (awaits allocation bump).
  5 of 9 sources done; 4 remain (nested BVBRC quartet).
  - ProtaBank maps cleanly to base DataCite (publication metadata: Title/Abstract/Authors/Year
    promoted; protein studies nested). Finding: ProtaBank `Protein` is a list of OBJECTS
    (ProtaBank_ID / PDB_ID / UniProt accession / Number_of_Data_Points / ...), NOT name strings;
    my initial `list[str]` failed strict validation -- the test caught it before ingest. Now
    modeled as `ProtaBankProteinEntry`; accessions lifted to `alternateIdentifiers`.
  - VIOLIN:Gene carries rich external accessions (NCBI Gene/Protein/Nucleotide, GenBank, PDB,
    VO) -- all lifted to `alternateIdentifiers` for cross-source linkage by shared accession.
  - canonical_uri keys: ProtaBank = Title (source subject, unique); VIOLIN:{Vaccine,Gene} = int id.
  - Next: the 4 nested BVBRC sources need the record-granularity decision (per-organism
    aggregate vs. explode to per-entity) -- to be made on real data per source.
- 2026-05-26 — **Granularity decision (BVBRC nested sources): AGGREGATE** -- one harmonized
  record per source document (the source's own organism grouping), NOT exploded to per-entity.
  Rationale: Globus Search indexes nested fields (a per-protein/epitope query still matches
  the aggregate record), exploding would multiply ~25k/746k docs into ~370k/millions of
  records for marginal discovery gain, and aggregate keeps the parser contract uniform.
  canonical_uri = {source}:{organism field}, which == the Globus-unique source subject
  (verified == subject across all 4 BVBRC samples). Explode is a documented DEFERRED
  enhancement if per-entity discovery proves necessary.
  - Publish-layer guard (Phase 4 requirement): ingest MUST assert canonical_uri uniqueness
    and FAIL LOUD on collision (never silently overwrite). This is the full-set safety net,
    since {organism}==subject is sample-verified, not proven for every doc.
- 2026-05-26 — Phase 2 parsers #6-7: **BVBRC:Protein, BVBRC:Genome** (aggregate). Nested
  models (`ProteinFeature` / `GenomeEntry`, strict + extra=forbid); per-feature/per-genome
  accessions (BV-BRC Genome ID, GenBank, NCBI Taxonomy) lifted to alternateIdentifiers. Real
  fixtures (30-doc samples); full suite **1247 green**, ruff clean. Ingest deferred. 7/9 done;
  remaining: BVBRC:Epitope + BVBRC:Protein_Structure (3-level nesting -- deeper inspection next).
- 2026-05-26 — Phase 2 parsers #8-9: **BVBRC:Epitope, BVBRC:Protein_Structure** (aggregate,
  3-level nesting). **ALL 9 SOURCE PARSERS NOW DONE + registered** (verified via SchemaRegistry).
  Epitope: Organism -> proteins -> epitopes (Epitope_ID/Sequence/Type/assays preserved).
  Structure: Organism -> publications -> structures (PDB/UniProtKB/Method/Resolution).
  Taxon/PDB/UniProt/PMID/GenBank cross-refs lifted. Real fixtures (352K / 728K). Full suite
  **1264 green**, ruff clean. Ingest deferred (allocation blocker).
  - Finding: `extra=forbid` caught a `Taxon_ID` field in some Protein_Structure publication
    groups (9/667) that single-doc inspection missed -- added to the model. Exactly the
    silent schema-drift the strict gate exists to prevent.
- 2026-05-26 — **Phase 2 COMPLETE: 9/9 sources harmonized + real-data tested.** Remaining
  work is Phases 3-5 (provenance wiring, the publish/ingest layer with the fail-loud collision
  guard, and the full-scale ingest) -- the latter two gated on the `support@globus.org`
  allocation bump. Only AntiviralDB is currently live in the dev index (Phase 0).
- 2026-05-27 — **Phases 1, 3, 4 (code) DONE + verified on real data.**
  - Phase 1: scroll-based full extraction (`globus_source.scroll_index_records`) replaces
    offset paging (~10k cap). PROVEN: scrolled BVBRC:Protein **24,902/24,902** (95s) and
    VIOLIN:Pathogen 217/217 -- exact, beyond the offset cap.
  - Phase 3: `pipeline/harmonize.py` -- `SOURCE_REGISTRY` (all 9), `harmonize_index()` with
    a provenance record (source/timestamp/counts + `stable_total` drift guard for torn
    snapshots) and the FAIL-LOUD `assert_unique_canonical` collision guard (runs before any
    ingest). Provenance is a sidecar manifest; DataCite records stay pure.
  - Phase 4 (code): `publish_records` + `wait_for_ingest` (fail-loud on non-SUCCESS state);
    generalized CLI `scripts/harmonize_and_publish.py` (any registered source; harmonize-only
    or `--publish` + public verification; refuses torn snapshots). PROVEN end-to-end:
    AntiviralDB harmonize->publish->dev index, **idempotent** (re-ingest stayed 35 not 70),
    auth=35 / anon=35 (public).
  - `tests/test_harmonize.py`: collision-guard fail-loud + registry + unregistered-index units
    (4 pass; 1 network-gated skip). Full suite **1268 green**, ruff clean.
  - `design/GLOBUS_SOURCE_PATTERN.md`: concise add-a-source recipe (LLM-guiding).
  - REMAINING: only **Phase 5** (full multi-source ingest) -- hard-blocked on the
    `support@globus.org` allocation (1 MB cap) + BVBRC:Genome volatility. All code paths built + tested.
