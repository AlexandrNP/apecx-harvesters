# Ontology Enrichment — Task List

Companion to `ONTOLOGY_ENRICHMENT_PLAN.md`. Stable IDs `OE-*` (Ontology
Enrichment). Cite the ID in the commit body or PR description.

**Convention:** every task that produces verifiable output records its
file path; every acceptance criterion is rooted in real-data values
(record counts, index UUIDs, surface-form distributions).

**Repo:** `apecx-harvesters-work` (branch
`feature/globus-index-harmonization`). Resolver code is read-only from
`apecx-mcp-integration/src/apecx_integration/synonym_dictionary/`.

**Numbers anchor (real data, 2026-05-27 measurements):** Source +
dest index UUIDs and per-source record counts are in
`GLOBUS_INDEX_HARMONIZATION_TASKS.md` "Real-data anchors" + "Production
destination indices" tables; all acceptance criteria below dereference
those tables — keep them in sync.

---

## Phase A — Information-drop audit

### OE-A1 — Define field-coverage audit method

**Output:** `scripts/audit_field_coverage.py` (read-only; consumes fixtures
from `tests/fixtures/globus/<source>/`; emits per-source markdown
reports).

**Acceptance:**
- Runs on AntiviralDB fixture; produces `design/field_coverage_audit_antiviraldb.md`
  with rows for every key path in the fixture's source `content` and its
  disposition (preserved / renamed-to / lifted-to / dropped-deliberately /
  dropped-undocumented).
- For each row, source-side nonempty-rate column is computed from the
  fixture (% of fixture docs where this key is non-null).
- Method documented inline (no surprises about which renames "count").

**Depends on:** nothing.

### OE-A2 … OE-A10 — Per-source audit

| ID | Source | Fixture path | Source record count |
|---|---|---|---|
| OE-A2 | AntiviralDB | `tests/fixtures/globus/antiviraldb/` | 35 |
| OE-A3 | VIOLIN:Pathogen | `tests/fixtures/globus/violin_pathogen/` | 217 |
| OE-A4 | VIOLIN:Vaccine | `tests/fixtures/globus/violin_vaccine/` | 3,507 (120-doc fixture) |
| OE-A5 | VIOLIN:Gene | `tests/fixtures/globus/violin_gene/` | 4,063 (120-doc fixture) |
| OE-A6 | ProtaBank | `tests/fixtures/globus/protabank/` | 1,643 (120-doc fixture) |
| OE-A7 | BVBRC:Epitope | `tests/fixtures/globus/bvbrc_epitope/` | 442 |
| OE-A8 | BVBRC:Protein_Structure | `tests/fixtures/globus/bvbrc_protein_structure/` | 4,566 (30-doc fixture) |
| OE-A9 | BVBRC:Protein | `tests/fixtures/globus/bvbrc_protein/` | 24,902 (30-doc fixture) |
| OE-A10 | BVBRC:Genome | `tests/fixtures/globus/bvbrc_genome/` | 745,917 (30-doc fixture) |

**Per-task acceptance:**
- `design/field_coverage_audit_<source>.md` exists.
- Coverage ratio (preserved-source-fields / total-source-fields, weighted
  by nonempty-rate) reported.
- Every "dropped-undocumented" row has either a stated reason or an
  OE-D2 ticket reference.

**Per-task gotcha:** for sources whose fixture is a sample (A4/A5/A6/A8/A9/A10),
flag whether the audit could miss long-tail fields that exist in a
small fraction of the corpus. **Specifically: any source where the
fixture is <5% of the corpus needs an "audit_completeness_caveat"
section noting the unknown.**

**Depends on:** OE-A1.

### OE-A11 — Consolidated value-bearing drops report

**Output:** `design/field_coverage_audit_summary.md`.

**Acceptance:**
- One table aggregating value-bearing drops (any field with source
  nonempty rate ≥30% that is NOT preserved in the harmonized record)
  across all 9 sources.
- Each row classified: needs-parser-fix / acceptable-loss / debatable.
- Sources with coverage_ratio < 0.85 are explicitly flagged.

**Depends on:** OE-A2 … OE-A10 (all 9).

---

## Phase B — Resolution-surface definition

### OE-B1 — Decide canonical surface shape — RESOLVED 2026-06-08

**Outcome:** the resolver writes to DataCite's existing `subjects[]` slot.
The local `Subject` class was extended with the DataCite-4.x spec fields
`subjectScheme`, `schemeUri`, `valueUri` (commit `f722389`). Multi-entity
records become multiple Subject entries differentiated by `subjectScheme`.
Confidence and dictionary_version are sidecar (`provenance.json`), not
on the record. **No new container; no `canonical:` extension.** See
`ONTOLOGY_ENRICHMENT_PLAN.md` integration architecture #5 and the
Open Decisions table for the production-Globus verification record.

### OE-B2 … OE-B10 — Per-source resolution-surface ratification

| ID | Source | Resolves to (primary) | Secondary slots |
|---|---|---|---|
| OE-B2 | AntiviralDB | pathogen (NCBI Taxonomy) | drugs[] (ChEBI, OPEN), proteins[] (UniProt) |
| OE-B3 | VIOLIN:Pathogen | pathogen (NCBI Taxonomy, cross-ref via `NCBI_Taxonomy_ID`) | — |
| OE-B4 | VIOLIN:Vaccine | pathogen + vaccine (VO) | type categorical (hand-curate) |
| OE-B5 | VIOLIN:Gene | pathogen + gene (NCBI Gene cross-ref) | VO_ID passthrough where present |
| OE-B6 | ProtaBank | proteins[] (UniProt cross-ref) | — |
| OE-B7 | BVBRC:Epitope | pathogen (NCBI Taxonomy) | type categorical (hand-curate) |
| OE-B8 | BVBRC:Protein_Structure | pathogen + proteins[] (UniProt cross-ref) | — |
| OE-B9 | BVBRC:Protein | pathogen | — |
| OE-B10 | BVBRC:Genome | pathogen (NCBI Taxonomy) | — |

**Per-task acceptance:**
- Section in `RESOLUTION_SURFACE.md` for this source has every
  row reviewed; ratified or marked deferred.
- Normalization rules per source are concrete (regex / mapping table).
- For each "Already canonical? YES" entry, the cross-ref short-circuit
  logic is described (resolver SHOULD NOT re-query OLS).

**Depends on:** OE-B1.

### OE-B11 — Ratify `RESOLUTION_SURFACE.md`

**Output:** `RESOLUTION_SURFACE.md` ratification checklist complete; OE-B11
recorded in `ONTOLOGY_ENRICHMENT_PLAN.md` log.

**Acceptance:**
- All checklist boxes ticked.
- Open decisions table in plan file shows resolved status for shape and
  ontology scope.

**Depends on:** OE-B1, OE-B2 … OE-B10.

---

## Phase C — Coverage projection (dry-run)

### OE-C0 — Pin dictionary version + build artifact

**Output:** a single SQLite dictionary artifact (path recorded), with
its `BuildManifest.dictionary_version` recorded in OE-C reports and
OE-F0 pre-flight.

**Acceptance:**
- `apecx-setup` run completed (or equivalent dictionary-build invocation
  from apecx-mcp-integration; route per `synonym_dictionary/workflow/bootstrap.py`).
- Dictionary path + version captured (e.g.,
  `data/apecx_synonym_dict/v1.0.0/dictionary.sqlite`).
- A pin-record (filename + version + sha256 + build date) committed
  alongside in `design/dictionary_pin.md`.

**Depends on:** nothing (resolver-side prereq).

**Brutal note:** the dictionary itself depends on VIOLIN data under
`APECX_DATA_ROOT` per apecx-mcp-integration/CLAUDE.md. The
**harvester scrapes VIOLIN from Globus**; **the dictionary reads
VIOLIN from a local CSV.** These are two different data paths; do not
conflate them. Confirm `APECX_DATA_ROOT` is populated before OE-C0.

### OE-C-RATE — OLS rate-limit + cost estimation

**Output:** `design/ols_cost_estimate.md` with measured latency-per-query
+ projected wall-clock per source.

**Acceptance:**
- Measured: 100 sequential OLS queries against EBI's endpoint, p50/p95 latency.
- Backoff behavior observed on rate-limit (HTTP 429).
- Projected: per-source wall-clock for Phase C dry-run, given distinct
  surface_set size (worst-case, no cache hits).
- Genome's projection is called out separately — that's the source where
  the wall-clock is non-trivial.

**Depends on:** OE-C0.

### OE-C1 — Construct dry-run probe (no SQLite writes)

**Output:** `scripts/coverage_projection.py`. Reads from a dest index
via `globus_index_records`; extracts distinct surface-form values per
slot defined in `RESOLUTION_SURFACE.md`; runs the resolver in
read-only mode (no writes to the dictionary); emits the
`coverage_projection_<source>.md` report shape.

**Acceptance:**
- Run against AntiviralDB dest index (`23a7bffd-…`) produces
  `design/coverage_projection_antiviraldb.md` with the
  `coverage_pct_by_distinct` + `coverage_pct_by_record` columns AND
  the `top_20_unresolved` section.
- Read-only confirmed: SQLite dictionary file mtime unchanged after run.
- Report header includes `dictionary_version` from OE-C0.

**Depends on:** OE-C0, OE-B11.

### OE-C2 … OE-C10 — Per-source coverage projection

| ID | Source | Dest index | Distinct surface_set size (denominator) |
|---|---|---|---|
| OE-C2 | AntiviralDB | `23a7bffd-…` | full (35 records, ~35 distinct pathogens, ~70 distinct drugs) |
| OE-C3 | VIOLIN:Pathogen | `b4965a61-…` | full (217 records, ≤217 distinct pathogens) |
| OE-C4 | VIOLIN:Vaccine | `12dfce07-…` | full (≤3507 pathogens + vaccine names) |
| OE-C5 | VIOLIN:Gene | `667dc223-…` | full (≤4063 gene-pathogen pairs) |
| OE-C6 | ProtaBank | `be999b57-…` | full (≤1643 UniProt accessions) |
| OE-C7 | BVBRC:Epitope | `4c0b4e3d-…` | full (≤442 organisms) |
| OE-C8 | BVBRC:Protein_Structure | `96fbabbb-…` | full (≤4566 organisms) |
| OE-C9 | BVBRC:Protein | `826e5d28-…` | full (≤24902 organisms, deduped) |
| OE-C10 | BVBRC:Genome | `dfefcd85-…` | **full distinct surface_set, NOT sample** (sample-vs-corpus discipline) |

**Per-task acceptance (every source):**
- `design/coverage_projection_<source>.md` exists.
- Two coverage numbers reported per slot: `coverage_pct_by_distinct`
  AND `coverage_pct_by_record`. Reporting only one is a fail.
- `top_20_unresolved` rows are APPENDED to
  `ONTOLOGY_GAPS_CANDIDATES.md` under this source's section, with the
  required provenance header (date + dictionary_version + endpoint +
  normalization-rules-applied).
- For sources with `Already canonical? YES` in `RESOLUTION_SURFACE.md`,
  the report distinguishes cross-ref-hit vs. OLS-hit coverage.
- Wall-clock + OLS call count logged at the bottom of each report.

**Per-task gotcha:**
- **OE-C10 (Genome) specifically:** report `top_50_unresolved`, not
  top_20, given the corpus size and the long tail. **Run after** the
  shard-suffix normalization rule from OE-B10 is in effect; otherwise
  14× the distinct surface forms with no resolution value, projection
  is meaningless.
- **OE-C4 / OE-C5 (multi-slot):** report coverage independently per
  slot; do not average.

**Depends on:** OE-C1, OE-C-RATE, OE-B11.

### OE-C11 — Consolidated coverage gate report

**Output:** `design/coverage_projection_summary.md`.

**Acceptance:**
- One table summarizing per-source per-slot coverage percentages.
- Recommendation column per source: republish-now / republish-after-fix /
  skip.
- The recommendation cell explicitly references which OE-A finding (if
  any) or OE-B normalization rule blocks the source.

**Depends on:** OE-C2 … OE-C10.

---

## Phase D — Decision gate (user, not code)

### OE-D1 — Per-source republish go/no-go decision

**Output:** `ONTOLOGY_ENRICHMENT_PLAN.md` "Open decisions" updated for
per-source-republish row.

**Acceptance:**
- Each of 9 sources has a decision recorded: republish-now /
  republish-after-fix / skip / defer-to-G2.
- For "republish-after-fix": references the OE-A* or OE-B* item that's
  the prerequisite.

**Depends on:** OE-A11, OE-B11, OE-C11.

### OE-D2 — Parser-fix tickets

**Output:** GitHub issues or new tasks (OE-A-FIX-* IDs) opened for each
source flagged in OE-A11 as needing a parser amendment.

**Acceptance:**
- One issue per affected source.
- Each issue cites the dropped field + source nonempty-rate + value-bearing-justification.

**Depends on:** OE-A11. Optional — skipped if OE-A11 surfaces nothing.

### OE-D3 — Ontology scope freeze

**Output:** plan file "Open decisions" row resolved.

**Acceptance:**
- Phase 0 ontology scope explicitly: NCBI-Taxonomy-only OR (NCBI + listed
  additions). Anything not listed is deferred.

**Depends on:** OE-C11.

---

## Phase E — Schema change — SHIPPED 2026-06-08 (commit `f722389`)

The original 5 subtasks (OE-E1 .. OE-E5) collapsed when the surface
flipped to "complete `Subject`" instead of "add `CanonicalExtension`."
What landed:

**OE-E1 (Subject extension).** `loaders/base/model.py::Subject`
gains three `Optional[str] = None` fields (`subjectScheme`, `schemeUri`,
`valueUri`). `extra='forbid'` preserved. All 5 existing
`Subject(subject=...)` construction sites unchanged.

**OE-E2 (per-container additions).** **OBVIATED.** Every
`*Container(DataCite)` already has `subjects: list[Subject]` via
inheritance. Zero per-container edits.

**OE-E3 (fixtures).** **OBVIATED.** Existing fixtures use
`Subject(subject=...)` which serialize byte-for-byte identically under
`to_dict(exclude_none=True)`.

**OE-E4 (round-trip tests).** Replaced by 5 unit tests in
`tests/test_base_schema.py::TestSubjectOntologyFields` and 2 end-to-end
shape tests in `tests/test_pipeline.py::TestSubjectOntologyPublishShape`.

**OE-E5 (full suite + headroom).** Suite: 1,280 passed, 1 pre-existing
skip. Headroom: ~80–150 bytes per record per resolved Subject; full
9-source republish stays well within dest allocations.

**Production smoke verification (2026-06-08).** One synthetic record
ingested into the AntiviralDB dest (`23a7bffd-...`) with all four
Subject fields populated. Three retrievals succeeded:
1. `globus search subject show` returned all 4 fields verbatim.
2. `globus search query -q 'subjects.subjectScheme:"NCBI Taxonomy"' --advanced` returned 1 hit.
3. `globus search query -q 'subjects.valueUri:"https*"' --advanced` returned 1 hit.

Record then deleted; index restored to baseline 35 entries.
**Globus Search auto-indexes the new fields as facets — no separate
mapping/schema step needed at the Search-service layer.**

---

## Phase F — Republish pipeline

### OE-F0 — Adapter integration pre-flight

**Output:** Python-shell or notebook session log proving the adapter is
importable, constructs, and returns a `Callable[[DataCite], Awaitable[DataCite]]`
when invoked with a built dictionary from OE-C0.

**Acceptance:**
- Adapter constructed against AntiviralDBContainer; round-trips a
  single sample record whose `record.subjects` now contains at least
  one Subject with `subjectScheme="NCBI Taxonomy"` and a non-None
  `valueUri`.
- The session log records `dictionary_version` and confirms it matches
  OE-C0's pin.

**Depends on:** OE-C0. (Was OE-E2; obviated — see Phase E header.)

### OE-F1 — Author `pipeline/republish_with_canonical.py`

**Output:** new file under
`src/apecx_harvesters/pipeline/republish_with_canonical.py`. Wires
`globus_index_records(dest_index_uuid, ...)` as source, the adapter as
the transform, and `to_gmetalist` + `client.ingest` as the sink.

**Acceptance:**
- Routes through `pipeline.run.run(source=..., sink=..., transforms=[adapter])`
  (the seam directive #2 specified).
- Does NOT modify `harmonize.py`.
- Per-record canonical_uri stability: an integration test verifies the
  republished record carries an IDENTICAL `canonical_uri` to the
  source-of-truth's `subject`. **Especially for BVBRC:Genome**, where
  the `PrivateAttr` subject-keyed fix was load-bearing.

**Depends on:** OE-F0, OE-E5.

### OE-F2 — Resolver lifecycle benchmark

**Output:** `design/oe_f2_lifecycle_benchmark.md`.

**Acceptance:**
- Measured wall-clock for republishing N=1000 records of a known source
  (e.g., synthetic BVBRC:Protein subset).
- Adapter constructed ONCE, reused across all 1000 records.
- Pydantic `model_dump` / `model_validate` overhead measured separately
  from OLS call latency.
- Projected wall-clock for BVBRC:Genome republish (745,917 records),
  reported with both cache-cold and cache-warm assumptions.

**Depends on:** OE-F0.

### OE-F3 — Resilient mid-batch policy + skipped-record log format

**Output:** policy section in `pipeline/republish_with_canonical.py`
docstring; format spec in `design/oe_f3_skipped_record_log.md`.

**Acceptance:**
- Policy: per-record transform exceptions captured (per
  `pipeline.run.run` existing behavior), recorded in
  `output/<source>/republish_skipped.jsonl`, counted toward a per-run
  threshold.
- Threshold: 1% of corpus skipped → publish aborts; configurable via
  env var.
- Skipped-record log carries: source subject, canonical_uri, exception
  type + message, surface forms attempted.

**Depends on:** OE-F1.

### OE-F4 — End-to-end on AntiviralDB (integration spike)

**Output:** `design/oe_f4_antiviraldb_spike.md` + dest index proof.

**Acceptance (real data, AntiviralDB dest `23a7bffd-…`, 35 records):**
- Republish completes with 0 abort + 0 ingest task FAILED states.
- Anonymous query for `subjects.subjectScheme:"NCBI Taxonomy"` exists on
  ≥ 1 record: returns ≥ 1 hit. **(Already proven shape-wise by the
  2026-06-08 smoke probe on this same dest index; OE-F4 must prove it
  end-to-end with the real resolver, not a stand-in.)**
- Anonymous total = 35 (no record loss).
- Record subjects + canonical_uris are bit-identical to pre-republish
  state (idempotency proof).
- `coverage_pct_by_record` for pathogen slot matches OE-C2 projection
  within ±2pp.

**Depends on:** OE-F1, OE-F2, OE-F3, OE-D1 (AntiviralDB go-decision).

---

## Phase G — Per-source republish + verification

### OE-G1 … OE-G9 — Per-source republish (in cost-ramp order)

| ID | Source | Records | Parallelizable with previous? |
|---|---|---|---|
| OE-G1 | AntiviralDB | 35 | NO — calibration |
| OE-G2 | VIOLIN:Pathogen | 217 | NO — validates cross-ref short-circuit |
| OE-G3 | BVBRC:Epitope | 442 | NO — validates BVBRC parser-path |
| OE-G4 | ProtaBank | 1,643 | NO — validates non-pathogen slot |
| OE-G5 | VIOLIN:Vaccine | 3,507 | NO — validates multi-slot |
| OE-G6 | VIOLIN:Gene | 4,063 | **YES (with OE-G5)** if VIOLIN:Gene is in scope per OE-B11 |
| OE-G7 | BVBRC:Protein_Structure | 4,566 | **YES (with OE-G6)** |
| OE-G8 | BVBRC:Protein | 24,902 | **YES (with OE-G7)** |
| OE-G9 | BVBRC:Genome | 745,917 | NO — run alone, cost-bound |

**Per-task acceptance (every source):**
- Republish completes with 0 ingest tasks in non-SUCCESS state.
- Pre/post anonymous record count is identical (idempotent).
- `canonical_uri` stability assertion green (sample of ≥10 records
  spot-checked).
- `coverage_pct_by_record` on the primary slot ≥ OE-C projection minus 2pp.
- Skipped-record log < 1% of corpus.
- Spot-check 5 records: every `subjects[].valueUri` on the resolved
  Subjects resolves to a live OLS term.

**Stop conditions (any source):**
- Actual coverage < OE-C projection minus 5pp → STOP, audit dictionary
  version + normalization rules before proceeding to next source.
- Genome only: wall-clock exceeds OE-F2 projection by 2x → STOP,
  re-evaluate.

**Depends on:**
- OE-G1: OE-F4 (already covers AntiviralDB end-to-end).
- OE-G2 … OE-G9: previous OE-G in serial chain through OE-G5; OE-G6 / G7 / G8
  may run concurrently after OE-G5 lands.
- OE-G9 (Genome): all earlier OE-G complete + OE-F2 benchmark confirmed
  with cache-warm projection.

### OE-G10 — Capstone verification

**Output:** `design/oe_g10_capstone.md`.

**Acceptance (real data, cross-source query proof):**
- Pick 3 well-known canonical IRIs (recommendation: `NCBITaxon:11320`
  Influenza A virus, `NCBITaxon:11103` Hepacivirus C, `NCBITaxon:2697049`
  SARS-CoV-2).
- For each, query each of 9 dest indices anonymously with
  `--advanced 'subjects.valueUri:"<IRI-URL>" AND subjects.subjectScheme:"NCBI Taxonomy"'`.
- Report per-source hit counts. **Expectation:** non-zero in sources
  with this organism present per the OE-C projection.
- The cross-source value-delivery claim is "for each IRI, ≥ 2 sources
  return non-zero hits." This is the deliverable that distinguishes
  semantic harmonization from shape harmonization.

**Depends on:** OE-G9 (or last-in-the-chain depending on which OE-D1
decisions kept which sources in scope).

---

## Dependency graph (ASCII)

Solid arrows = strict prerequisite. Phase numbers in brackets indicate
which tasks within a phase are independent and parallelizable.

```
                         OE-A1
                           │
              ┌────────────┴────────────┐
              │ OE-A2..A10 (parallel × 9) │
              └────────────┬────────────┘
                           │
                         OE-A11 ─────────┐
                                         │
              OE-B1 ──────┐               │
                ├────────► OE-E1          │
                │                         │
                └─► OE-B2..B10 (parallel × 9)
                           │              │
                         OE-B11 ──────────┤
                           │              │
            OE-C0 ────► OE-C-RATE         │
              │            │              │
              ├──► OE-C1 ──┤              │
              │            │              │
              │   OE-C2..C10 (parallel × 9)
              │            │              │
              │         OE-C11 ───────────┤
              │            │              │
              │            ▼              ▼
              │         OE-D1 (uses A11+B11+C11)
              │            │
              │            ├─► OE-D2 (optional, if A11 surfaced fixes)
              │            └─► OE-D3
              │                  │
              │                  ▼
              │     OE-E1 ─► OE-E2 ─► OE-E3 ─► OE-E4 ─► OE-E5
              │                                          │
              │     OE-F0 ◄──────────────────────────────┘
              │      ├─► OE-F1 ─► OE-F3
              │      │    │           │
              │      └─► OE-F2        │
              │           └──► OE-F4 ◄┘
              │                  │
              └──────────────────▼
                              OE-G1
                                │
                              OE-G2 ─► OE-G3 ─► OE-G4 ─► OE-G5
                                                          │
                                  ┌──── OE-G6 ────┐       │
                                  │               │       │
                                  ├──── OE-G7 ────┤◄──────┘
                                  │               │
                                  └──── OE-G8 ────┘
                                          │
                                       OE-G9
                                          │
                                       OE-G10
```

**Critical path** (longest dep chain): OE-A1 → OE-A2..A10 → OE-A11 →
OE-D1 → OE-E1 → OE-E2 → OE-E3 → OE-E4 → OE-E5 → OE-F0 → OE-F1 → OE-F4 →
OE-G1 → OE-G2 → OE-G3 → OE-G4 → OE-G5 → OE-G8 → OE-G9 → OE-G10.
**Length: 20 tasks.** Anything in Phase B (after OE-B1) and Phase C (after
OE-C0) is off the critical path.

**Concurrency notes:**
- Phase A and Phase B (after OE-B1) are fully independent — run in parallel.
- Phase C cannot start until OE-B11 (needs surface definition) AND OE-C0
  (needs dictionary).
- Phase E can START immediately after OE-B1 lands; the rest of Phase B
  runs alongside.
- OE-G6/G7/G8 can fan out once OE-G5 validates the multi-slot path.

---

## Sizing estimates (informational only — refine after OE-F2)

| Phase | Wall-clock estimate | Confidence | Bottleneck |
|---|---|---|---|
| A | 1–2 days | high | fixture audit is local + bounded |
| B | 1 day (assuming decisions are not contested) | medium | depends on review cadence |
| C | 1 day for all sources except Genome; Genome 0.5–1 day OLS-bound | low for Genome | OE-C-RATE measurement gates this |
| D | hours (decision only) | high if A+B+C clean | weeks if A11 surfaces parser amendments |
| E | 1 day | high | optional field, low risk |
| F | 1–2 days | medium | OE-F2 benchmark may surface unknowns |
| G1..G5 | 1 day total | medium | small per-source corpora |
| G6..G8 | 1 day | medium | parallelizable |
| G9 (Genome) | 1–2 days wall-clock | low | OLS rate-limit bound, cache-cold |
| G10 | hours | high | query-only |

**Total critical-path estimate: ~10 working days** (excluding decision
delays at OE-D1 if Phase C surfaces problems).

---

## Gaps in this task list (self-review)

I went through this list a second time before declaring done. Gaps I
either filled above or am explicitly leaving open:

1. **No task for retiring `harmonize.py`'s outputs.** Deliberate — surgical
   change rule. If we ever want to converge on one publish path, that's
   a separate plan.
2. **No rollback plan for Phase G failures.** Republish is idempotent on
   canonical_uri; a failed republish leaves the index in a mixed state
   (some records with `canonical`, some without). This is acceptable in
   practice — re-running the republish converges — but a hard "revert to
   pre-republish" requires either snapshotting the dest index before OE-G
   starts OR accepting the mixed state until a successful re-run. **Open
   for OE-D3 decision: snapshot dest indices before OE-G1?**
3. **No task for documenting the new search-side query patterns** (how a
   downstream consumer queries by canonical IRI across the 9 indices).
   That's the user-facing payoff. Adding stub: a follow-up
   `docs/SEARCH_BY_CANONICAL_IRI.md` should be authored after OE-G10
   succeeds. Not blocking, but the value isn't delivered until it
   exists. **Recommendation: add as OE-G11 — author after capstone.**
4. **No task for re-pulling source fixtures if Phase A11 surfaces that
   the committed fixtures missed long-tail fields.** Add as OE-A12
   ONLY if OE-A11 raises this concern. Otherwise YAGNI.
5. **No task for measuring OLS coverage degradation over time.** The
   ontology is a moving target — NCBI Taxonomy adds species; some may
   be deprecated. A six-month-later re-projection task is worth knowing
   about but is firmly out of scope here. **Recommendation: file as a
   recurring Phase H concern, not added here.**
6. **No task for handling adversarial / malformed surface forms in
   published records** (e.g., a record's `Organism` field is empty
   string). Phase F skipped-record policy (OE-F3) covers it operationally
   but doesn't lock in the contract. **Recommendation: include in OE-F3
   acceptance: "empty / null surface form → skip-with-log, not exception."**
7. **No task for confirming that re-ingest preserves `_provenance`
   (sidecar manifest)** if Phase 3 stamped it into the harmonized
   records. Re-check the harmonize.py flow — the sidecar is NOT stamped
   into records per the existing code, so this is not a concern. Noted.

---

## "Will the system run smoothly after updates?" (re-checked)

Per `ONTOLOGY_ENRICHMENT_PLAN.md` "Will the system run smoothly" — yes,
the answer remains **no, not on first run**. The 6 known risk areas
(DataCite strict round-trip, Pydantic round-trip cost, canonical_uri
stability, OLS rate limits, long-tail organism normalization, dictionary
version drift) are tasked: OE-F1 covers round-trip + canonical_uri,
OE-F2 covers cost, OE-C-RATE covers OLS limits, OE-B10 covers Genome
normalization, OE-C0 + OE-F0 pin dictionary version. The remaining
unknowns are: (a) actual OLS coverage on Genome's long tail, surfacing
at OE-C10, (b) Pydantic re-validation behavior on existing published
records, surfacing at OE-F0/F1.

**The system will run smoothly after OE-G10 if every Phase F-G acceptance
criterion holds.** The plan's job is to make the risks visible and
recoverable, not to pretend they aren't there.
