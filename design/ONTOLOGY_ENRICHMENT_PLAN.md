# Plan: Ontology Enrichment of the 9 Public Globus Indices

Status: PLAN (DRAFT). Authored 2026-05-28 after discovery that the 9 published
indices (785,292 records across 9 dest UUIDs, Phases 0–5 of
`GLOBUS_INDEX_HARMONIZATION_TASKS.md`) carry **zero ontology resolution**.
Companion to that file; **does not replace it.**

Sibling artifacts (this directory):

- `RESOLUTION_SURFACE.md` — per-source entity → ontology mapping (DRAFT; needs ratification).
- `ONTOLOGY_TASKS.md` — actionable task list (OE-A1 … OE-G10) with acceptance criteria + dependencies.
- `ONTOLOGY_GAPS_CANDIDATES.md` — append-only inventory of unresolved surface forms (populated by Phase C).

## Why this plan exists (honest framing)

The published indices currently deliver shape harmonization to DataCite,
not semantic harmonization. A query for "Influenza A virus" returns hits
in each source index by substring match; there is no canonical IRI on any
record, so cross-source linkage by entity identity is not possible. The
search-value delivery that distinguishes "9 harmonized indices" from
"9 parallel substring-searchable indices" did not ship in Phases 0–5.

The Phase-5 acceptance criteria measured record-count parity and public
visibility; they did NOT measure ontology coverage. So the gap was not
detected at the time of "Phases 0–5 DONE." This plan corrects that and
records the discovery as a distillation candidate for CLAUDE.md (see
the "Lessons" section at the end of this file).

## Integration architecture (decided)

1. **The resolver lives in apecx-mcp-integration**
   (`src/apecx_integration/synonym_dictionary/`): OLS client, NCBI taxdump
   fetcher, SQLite-backed dictionary, nanobrain workflow YAML, AND
   `harvester_adapter.py` that wraps it as a
   `Callable[[DataCite], Awaitable[DataCite]]` per the docstring's
   "Phase 6" plan.
2. **The seam in apecx-harvesters is `pipeline.run.run(transforms=[...])`**
   (`src/apecx_harvesters/pipeline/run.py`). It already exists and applies
   transforms per-record between source and sink.
3. **Phase-5's `harmonize.py` does NOT use that seam.** It has its own
   loop. We will NOT refactor `harmonize.py` (CLAUDE.md "surgical changes"
   rule). Instead, a new code path `pipeline/republish_with_canonical.py`
   reads from the published indices, applies the adapter via
   `pipeline.run.run`, and re-ingests.
4. **Read-from-published, not re-scrape-from-source.** Per directive #2
   from the 2026-05-28 thread. Cheaper, freezes Phase-2 parser variance
   at its tested state, idempotent on `canonical_uri`.
5. **Canonical resolution lands in DataCite's existing `subjects[]` slot.**
   Originally directive #1 said "new `canonical:` field"; on 2026-06-08
   re-examination the project decided to use the DataCite-4.x-spec
   `Subject` instead. The local `Subject` was a documented sub-spec
   (free-text label only); the DataCite spec also defines
   `subjectScheme`, `schemeUri`, `valueUri`. Completing the local `Subject`
   to the full spec ships the canonical IRI without inventing a new
   container. **Shipped 2026-06-08, commit `f722389`** —
   see `src/apecx_harvesters/loaders/base/model.py` (Subject class) +
   `tests/test_base_schema.py::TestSubjectOntologyFields`. No per-container
   change needed: every `*Container(DataCite)` inherits `subjects`.

## Open decisions (block downstream work)

Track each decision's resolution in this section as it happens.

| Decision | Blocks | Status | Notes |
|---|---|---|---|
| **Canonical surface shape** (originally CanonicalExtension flat-vs-nested) | OE-E1 schema add | **RESOLVED 2026-06-08** (commit `f722389`) | DataCite Subject.subjectScheme/schemeUri/valueUri populated per role. Multi-entity records (AntiviralDB virus+drug, VIOLIN:Vaccine pathogen+vaccine) become multiple Subject entries distinguished by subjectScheme. No new container. |
| **Subject extension required vs. optional** | OE-E1 | **RESOLVED 2026-06-08** | Optional. Three new fields default `None`, excluded by `to_dict(exclude_none=True)`. Existing 785k records serialize byte-for-byte identically. |
| **Globus Search auto-indexes the new fields as facets** | OE-F1, OE-G1..G9 | **VERIFIED 2026-06-08** in production AntiviralDB dest | Smoke ingest + advanced filter `subjects.subjectScheme:"NCBI Taxonomy"` and `subjects.valueUri:"https*"` both returned the test record. No index-side schema work needed. Record was deleted after the probe; index restored to 35 entries. |
| **Ontology scope for Phase 0 republish** — NCBI Taxonomy only vs. wider (VO, ChEBI, PRO) | OE-B11, OE-G1 | OPEN | NCBI Taxonomy only for Phase 0; covers primary slot on 8 of 9 sources; defer wider to a Phase G2 after measured experience |
| **Per-source republish go/no-go** — based on Phase C coverage projection | OE-G1..G9 | OPEN — pending Phase C | decide per source at the OE-D1 gate |
| **Parser fixes before republish** — based on Phase A information-drop audit | OE-G1..G9 | OPEN — pending Phase A | decide at OE-D2 gate |
| **Phase A scope** — committed fixtures only vs. wider sample (re-pull from source) | OE-A2..A10 | OPEN | committed fixtures first; widen only if Phase A surfaces specific suspicion |
| **Resolver dictionary version pinning** — single version across Phase C + F + G, or evolve | OE-C0, OE-F1 | OPEN | single version pinned across all three phases; bump is a separate planned activity |

## Phases (compact reference; OE-* task IDs in `ONTOLOGY_TASKS.md`)

### Phase A — Information-drop audit (per source × 9)

Verify the existing Phase-2 parsers preserve source field content. The
Phase-2 acceptance criteria measured record-count parity, not field-content
parity. A parser that ships "every input doc → ≥1 output record" can
silently drop high-value fields. Method: enumerate source field set per
source (from committed fixtures), enumerate harmonized destination field
set, compute disposition (preserved / renamed / lifted / dropped) per
source field. Output: 9 per-source reports + one consolidated
value-bearing-drops table.

**Decision driven by output:** which sources need parser amendments
before republish. Lock at OE-D2.

### Phase B — Resolution-surface definition (per source × 9)

Decide, in writing, which fields per source feed which ontology resolver.
This artifact does not currently exist. Output: `RESOLUTION_SURFACE.md`,
ratified. Includes per-source normalization rules (e.g., strip BV-BRC
Genome shard suffixes like " (7)" from organism names before resolution).

**Decision driven by output:** ontology scope freeze (NCBI Taxonomy only
or wider). The shape decision was settled 2026-06-08 (DataCite Subject;
see Open Decisions table) and no longer blocks Phase B.

### Phase C — Coverage projection (dry-run, per source × 9)

Run the resolver in dry-run mode against the distinct surface-form values
extracted from each published index. Two denominators per report:
distinct-surface-form coverage (caching cost driver) and record-weighted
coverage (search value driver). Long tail: Genome organism names go
through the full distinct surface_set, not a sample, because of the
documented Phase-2 sample-vs-corpus lesson (30-doc canonical assumption
failed at 746k).

**Decision driven by output:** per-source republish go/no-go at OE-D1.
**Side effect:** the top-20-unresolved per source feeds
`ONTOLOGY_GAPS_CANDIDATES.md`.

### Phase D — Decision gate (user, not code)

Three decisions: per-source republish, parser-fix tickets,
ontology-scope freeze.

### Phase E — Schema change

**Shipped 2026-06-08 (commit `f722389`).** Three Optional fields
(`subjectScheme`, `schemeUri`, `valueUri`) added to
`loaders/base/model.py::Subject`. Backwards compatible: existing
parsers (`biorxiv`, `datacite`, `pdb`, `emdb`, `base/parser.deduplicate_subjects`)
construct `Subject(subject=...)` and serialize identically.
No per-container change: all 9 `*Container(DataCite)` inherit `subjects`.
5 unit tests in `tests/test_base_schema.py::TestSubjectOntologyFields`;
2 end-to-end shape tests in
`tests/test_pipeline.py::TestSubjectOntologyPublishShape`. Full suite
1,280 passed, 1 pre-existing skip. Production Globus Search ingest
verified on AntiviralDB (see "Open decisions" table row).

### Phase F — Republish pipeline

New file `pipeline/republish_with_canonical.py`. Reads from a dest index
via `globus_index_records`, deserializes to the registered DataCite
subclass, applies the adapter as a `pipeline.run.run` transform,
re-ingests with `to_gmetalist`. The adapter writes one `Subject` per
resolved entity into `record.subjects` (multi-entity records get
multiple Subject entries differentiated by `subjectScheme`).
Integration-spike validated on AntiviralDB (35 records).

**End-to-end shape contract already proven** by
`tests/test_pipeline.py::TestSubjectOntologyPublishShape` (the test
runs the real `pipeline.run.run(transforms=[stand-in-resolver]) -> to_gmetalist`
chain) and by the 2026-06-08 production smoke probe (real ingest +
advanced-filter query + delete; see "Open decisions" table).

### Phase G — Per-source republish + verification

Serial in cost order through the first few sources to validate the
pipeline; **parallelizable from OE-G6 onward** (after the pipeline is
proven on 3 sources). Capstone: anonymous query by a known canonical IRI
(e.g., `NCBITaxon:11320` for Influenza A virus) returns expected counts
across the eligible sources.

### Out of scope (for this plan)

- **Phase A2 — semantic drops.** Optional. Verifies preserved-field
  meaning matches source documentation. Only scoped if Phase A surfaces
  specific suspicion.
- **Phase H — ontology gap-filling production work.** `ONTOLOGY_GAPS_CANDIDATES.md`
  is the input. Triage to ontology submissions (NCBI Taxonomy / VO / etc.)
  + curation rules + normalization rules. A separate curation workstream,
  not engineering.
- **Cross-source de-duplication and the Globus query layer changes** that
  let "Influenza A" search hit all 9 indices by canonical IRI. Search-side
  payoff, separate piece of work.
- **Refactoring `harmonize.py` to converge on a single publish path.**
  Defensible cleanup; not load-bearing for this work.
- **A nanobrain-orchestrated republish version** via
  `adapt_workflow_to_harvester_transform`. Plain adapter is the Phase 0
  path; revisit only if it underperforms.

## Will the system run smoothly after these updates? (BRUTAL ANSWER)

**No, not on first run. Schema change is low risk; republish pipeline
has 6 known risk areas plus unknowns.**

### Eliminated (was Low risk, Phase E)

- ~~Optional field with `None` default. Existing records validate
  unchanged.~~ **Shipped 2026-06-08, commit `f722389`.** 1280/1280
  tests green (was 1273; +5 unit + 2 end-to-end). Production AntiviralDB
  smoke probe confirmed Globus Search auto-indexes the new fields as
  query facets. No risk remaining on Phase E.

### Medium risk (Phase F — republish pipeline)

1. **DataCite strict round-trip on read-back from published indices.**
   The Phase 2 #2 commit found DataCite is `strict=True` and emits
   enum fields (e.g. `descriptionType`) as strings that strict
   re-validation won't coerce back. The current Phase-5 publish path
   never re-validates (the JSON dict goes straight to Globus). The
   republish path DOES re-validate (it reconstructs DataCite from
   published-index content). **Likely failure point at scale.**
   Mitigation: explicit round-trip test in OE-F1 acceptance; possibly
   read with `strict=False` for republish-only deserialization.

2. **Pydantic round-trip cost at 745k scale.** The adapter calls
   `model_dump()` then `model_validate()` per record. Pydantic v2 is
   fast but not free; on Genome this is 1.5M+ Pydantic operations on
   the hot path. Mitigation: OE-F2 explicit benchmark before OE-G9.

3. **canonical_uri stability.** Republish must preserve `canonical_uri`
   verbatim, including the Genome subject-keyed fix (the
   `PrivateAttr` the parser sets from the source subject). The
   read-from-published flow does NOT carry the source subject —
   it reads `subject` from the GMeta wrapper. Mitigation: explicit
   assertion in OE-F1 + an integration regression that proves
   canonical_uri is bit-identical to the original publish for
   ≥1 known record per source.

### High risk (Phase C + Phase G — anything touching OLS)

4. **OLS rate limits.** EBI OLS has unpublished rate limits; first
   Genome run with ~10k–50k distinct organism queries will hit them.
   Mitigation: OE-C-RATE estimation task before OE-C9 (Genome);
   `loaders/base/rate_limit.py` reuse; backoff + retry queue;
   single-pass with SQLite-backed cache so the second run is cheap.

5. **Long-tail organism names that aren't in NCBI Taxonomy as-typed.**
   Phase-5 Genome surfaced "Hepacivirus C (7)" sharding; that exact
   string will not resolve in NCBI Taxonomy without normalization.
   Mitigation: per-source normalization rules in `RESOLUTION_SURFACE.md`;
   OE-B2..B10 must enumerate normalization rules; OE-C runs WITH
   those rules applied (so the projection reflects realistic coverage).

6. **Dictionary version drift.** A `dictionary_version` bump between
   Phase C (projection) and Phase F (republish) means projection
   numbers no longer predict actual coverage. Mitigation: OE-C0 pins
   `dictionary_version`; Phase F + G assert the same version is in
   use; any republish under a different version is a separate Phase
   G2 plan.

### Unknowns (need data, not opinion)

- **OE-G size per source after canonical extension lands.** Each record
  gains ~80–200 bytes of `canonical` payload. Total per-source growth
  is small (Genome ~75 MB on a 7 GB allocation, ProtaBank trivial),
  but each dest index has a hard size cap; OE-E5 should verify headroom.
- **Failure rate of resolver under load.** Skipped-record threshold
  needs a policy: skip-with-log up to 1%, abort + revert above 1%.
  OE-F3 owns this. The 1% is a guess; the actual threshold should be
  re-set after the first Genome run produces real numbers.

## Lessons distilled for CLAUDE.md (proposed)

Two candidates per the workspace-end-of-session policy. **Both proposed
here — to be ratified during the actual session-end distillation step,
not silently added by this plan file.**

1. **"Every input doc → ≥1 output record" is not a completeness gate;
   it's a coverage gate.** Field-content parity (Phase A here) needs
   its own gate alongside record-count parity. Detection signal: a
   pipeline declares "X records harmonized" without a per-source
   source-field-preserved ratio. Source: 2026-05-28 discovery that the
   9 published indices have no ontology fields despite "Phases 0-5
   DONE."

2. **"Shape harmonization is not semantic harmonization."** Detection
   signal: a doc says "harmonization complete" but no records carry
   canonical IRIs / cross-ontology identifiers. The shape change
   (DataCite envelope) is the precondition; the semantic change
   (canonical identity) is the user-facing deliverable. Source: same
   discovery, same date.

## Implementation log (append-only)

- 2026-05-28 — Plan drafted in this thread. No code yet. Open decisions
  enumerated. Companion artifacts `RESOLUTION_SURFACE.md`,
  `ONTOLOGY_TASKS.md`, `ONTOLOGY_GAPS_CANDIDATES.md` written
  alongside. **Phase 6 of the original
  `GLOBUS_INDEX_HARMONIZATION_TASKS.md` is the umbrella; this plan
  decomposes it.**
- 2026-06-08 — Surface decision flipped from `canonical: CanonicalExtension`
  to "complete `Subject` with the DataCite-4.x-spec
  `subjectScheme`/`schemeUri`/`valueUri` fields." Phase E shipped
  (commit `f722389`). Production smoke ingest on AntiviralDB confirmed
  Globus Search auto-indexes the new fields as facets and that
  `--advanced 'subjects.subjectScheme:"NCBI Taxonomy"'`/
  `'subjects.valueUri:"https*"'` filters work. Test record cleanly
  removed (index restored to 35 records). Companion docs
  `RESOLUTION_SURFACE.md` and `ONTOLOGY_TASKS.md` re-edited to drop
  the canonical-extension framing in the same session.
