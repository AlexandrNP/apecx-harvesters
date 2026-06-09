# Synonym-Completeness Plan

**Status:** Active design (DRAFT — 2026-06-08).
**Authoring decisions reflected:** vernacular = fuzzy = yes (overrides
memory `synonym_strategy_hard.md`); 1.0 confidence always prevails;
cross-source conflict → HITL (no silent merge); fuzzy HITL threshold = 0.70.
**Companion to:** `ONTOLOGY_ENRICHMENT_PLAN.md` (extends it; does not replace).
**Cross-repo touchpoints:** `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/`
(dictionary build + lookup) and `apecx-harvesters-work/src/.../pipeline/`
(parser-lift + corpus-mining seam).

---

## 0. What this plan exists to fix (brutal framing)

The 2026-06-08 `FIELD_COVERAGE_AUDIT_SUMMARY.md` showed `subjects` is 0%
non-empty across all 9 dest indices. The `ONTOLOGY_ENRICHMENT_PLAN.md`
addresses that — it gets records anchored to taxon IRIs. **That is
necessary but not sufficient.** A user typing `EEEV`, `Flu A`, or
`Hepatitus C virus` (typo) against an index of correctly-anchored
records still misses everything if the dictionary doesn't carry those
surface forms.

This plan addresses what the enrichment plan does not: **maximizing the
surface-form → IRI coverage** so user queries resolve regardless of
which variation a user types.

**Three load-bearing findings driving this plan, in order of impact:**

1. **`names.dmp` is not ingested today.** The taxdump fetcher's
   `_WANTED` set is `{"nodes.dmp", "merged.dmp"}`
   (`apecx-mcp-integration/src/apecx_integration/synonym_dictionary/taxdump_fetcher.py:25`).
   Synonyms come only from per-IRI OLS lookups during build, bounded
   by what the corpus happened to resolve. NCBI's 7 name classes
   (scientific name, synonym, equivalent name, common name, genbank
   common name, acronym, blast name) are NOT loaded as a unit. **This
   is the single highest-impact, lowest-risk fix in the plan** — one
   download line + one parser. Expected synonym count jump: 5-10× on
   resolved taxa, plus full coverage of taxa never in the source
   corpus.

2. **The harvester's parser-lift pass is a synonym-discovery
   opportunity that is currently thrown away.** Every record-to-IRI
   mapping the harvester computes during republish IS a `(surface_form,
   taxon_id)` pair. Today these pairs go to `record.subjects` and
   nowhere else. Feeding them back into the dictionary as
   "corpus-mined" entries gives essentially-free synonym growth from
   the data we already have.

3. **HARD-only matching (per memory `synonym_strategy_hard.md`) cannot
   satisfy the "vernacular spelling → closest existing" requirement.**
   The decision is being reopened explicitly in this plan with a new
   `fuzzy` resolution path, confidence-graded, gated by HITL below 0.70.

**What I'm explicitly not promising:** this plan does not make the
resolver smarter. It makes the dictionary more complete and adds a
fuzzy fallback. If a surface form is genuinely not in any source
(corpus, NCBI, ICTV, VIOLIN) AND too dissimilar from anything in
the dictionary, it will still miss. That's the right outcome.

---

## 1. Use case (single sentence, then unpacked)

A scientist types any variation of an entity name — abbreviation,
common name, scientific name, vernacular, misspelling — and the system
returns records carrying that entity across all 9 indices, with the
resolution decision (confidence, path, alternates) visible.

**Concrete acceptance:**

- `lookup_entity("EEEV")` → `NCBITaxon:11036`, path=`fast`, conf=1.0.
- `lookup_entity("Eastern equine encephalitis virus")` → same IRI, path=`fast`, conf=1.0.
- `lookup_entity("Flu A")` → `NCBITaxon:11320` (Influenza A virus), path=`fast`, conf=1.0.
- `lookup_entity("Hepacivirus C (7)")` → `NCBITaxon:11103`, path=`fast`, conf=0.95 (corpus-mined OR after shard-suffix normalization).
- `lookup_entity("Hepatitus C virus")` (typo) → top candidate `NCBITaxon:11103` with conf=0.78, path=`fuzzy`, status=`ambiguous` (HITL because 0.70 ≤ conf < 0.85), alternates=[(`NCBITaxon:11103`, "Hepacivirus C", 0.78), ...].
- `lookup_entity("Eastern equine encephalitis vyrus")` (typo) → top candidate with conf=0.91 (within fuzzy auto-resolve band), path=`fuzzy`.
- `lookup_entity("xkcd123")` → path=`miss`, status=`unresolved`, no fabricated answer.

Once an IRI is in hand, the seamless cross-index query is a single
Globus Search filter (`subjects.valueUri:"<iri>"`) — that part is
solved by `ONTOLOGY_ENRICHMENT_PLAN.md` Phase F.

---

## 2. Architecture — three layers, one direction

```
                    user types arbitrary text
                              │
                              ▼
   ┌────────────────────────────────────────────────────────┐
   │ LAYER 3 — Lookup pipeline (read-time)                   │
   │   fast → ancestor → slow → fuzzy → miss                 │
   │   confidence-graded; HITL gates at 0.70                 │
   └─────────────────────────┬──────────────────────────────┘
                             │ queries
                             ▼
   ┌────────────────────────────────────────────────────────┐
   │ LAYER 2 — Dictionary (SQLite artifact)                 │
   │   inverse_index: surface_form_normalized → canonical_iri│
   │   entries: canonical_iri → label + synonyms + provenance│
   │   ambiguous_surface_forms: conflict queue (HITL)        │
   │   taxon_hierarchy + merged_taxons (from .dmp)           │
   └─────────────────────────┬──────────────────────────────┘
                             │ fed by
                             ▼
   ┌────────────────────────────────────────────────────────┐
   │ LAYER 1 — Build inputs (all of these, unioned)         │
   │   (a) NCBI names.dmp (all 7 name classes) ← NEW         │
   │   (b) NCBI merged.dmp + delnodes.dmp ← extend           │
   │   (c) ICTV Master Species List ← NEW                    │
   │   (d) VIOLIN curated rows ← already in                  │
   │   (e) Corpus-mined from parser-lift pass ← NEW          │
   └────────────────────────────────────────────────────────┘
```

Layer 1 inputs flow ONE direction into Layer 2. Layer 3 reads only.
No circular dependencies. The corpus-mining feedback loop runs at
republish time, not at query time, so user queries see a stable
artifact.

---

## 3. Confidence model — the explicit rules

The model has two purposes: (a) ordering in the inverse index when
multiple sources claim a surface form, (b) downstream consumer
disposition (auto-use vs. surface-as-ambiguous vs. discard).

### 3.1 The tiers (highest to lowest)

| Tier | Source | Confidence | `ResolutionStatus` (proposed) | Path label |
|---|---|---:|---|---|
| Authoritative | NCBI `names.dmp` all 7 name classes | **1.0** | `id_anchored` | `fast` |
| Authoritative | NCBI `merged.dmp` redirect (old→new) | **1.0** | `id_anchored` | `fast` |
| Authoritative | ICTV Master Species List | **1.0** | `id_anchored` | `fast` |
| Authoritative | VIOLIN curated row with NCBI_Taxonomy_ID | **1.0** | `id_anchored` | `fast` |
| Authoritative | OLS exact term lookup for known IRI | **1.0** | `id_anchored` | `fast` |
| Corroborated mined | Corpus-mined, observed in ≥2 sources, all agree | **0.95** | `mined_corroborated` (new) | `fast` |
| Singleton mined | Corpus-mined, observed in 1 source | **0.90** | `mined_observed` (new) | `fast` |
| Ancestor | NCBI hierarchy traversal | parent × 0.9 | `id_anchored` (parent IRI) | `ancestor` |
| Slow | Substring match against database | per-matcher | `ols_fuzzy`-equivalent | `slow` |
| Fuzzy ≥ 0.85 | Trigram similarity, auto-resolve | similarity | `fuzzy_resolved` (new) | `fuzzy` |
| Fuzzy 0.70–0.85 | Trigram similarity, HITL band | similarity | `ambiguous` (new) | `fuzzy` |
| Fuzzy < 0.70 | No match | 0.0 | `unresolved` | `miss` |
| Conflict | Corpus-mined, sources disagree | NOT MINED | n/a (logged to `ambiguous_surface_forms`) | n/a |

### 3.2 Why 0.95 and 0.90 for corpus-mined (the explanation requested)

The principle is "1.0 always prevails on ties." Corpus-mined entries
must be **strictly below 1.0** so any authoritative source wins when
both have the same surface form. The exact number matters less than
the tier; 0.95 and 0.90 are chosen for three reasons:

1. **Distinctly below 1.0** — authoritative wins ties.
2. **Distinctly above the fuzzy ceiling (0.85)** — a corpus-mined
   exact-string hit never surfaces as "did you mean," because the
   surface form was literally observed in real data; that's stronger
   evidence than a similarity-score guess.
3. **0.95 vs 0.90 graduation rewards cross-source agreement** — when
   two sources independently observed `Hepacivirus C → NCBITaxon:11103`,
   that's stronger evidence than one source alone. The 0.05 gap is
   small but lets a downstream consumer prefer corroborated entries.

**What the rule does NOT say:** corpus-mined entries do not override
authoritative entries even when the corpus is unanimous. If NCBI says
`X → A` and 12 corpus sources say `X → B`, NCBI wins. The corpus
disagreement is recorded as a **gap** (logged to
`ambiguous_surface_forms` for curator review), not as a basis to
override the authoritative source. Rationale: authoritative sources
are the ground truth definition; if they're wrong, that's a curation
issue to be resolved upstream (in NCBI / ICTV), not in our pipeline.
The HITL queue is the surface where this gets flagged.

### 3.3 Conflict policy (the HITL queue)

| Conflict | Disposition | Where logged |
|---|---|---|
| Surface form X → taxon A (authoritative) AND X → taxon B (mined) | Authoritative wins; mined entry rejected; **log to HITL** | `ambiguous_surface_forms` |
| Surface form X → taxon A (mined, source 1) AND X → taxon B (mined, source 2) | **NEITHER mined**; **log to HITL** | `ambiguous_surface_forms` |
| Surface form X → taxon A (mined, ≥2 sources) AND X → taxon A (mined, more sources) | Mined as `mined_corroborated`, conf=0.95 | `entries.synonyms_json` |
| Fuzzy query → top-1 conf < 0.70 | path=miss; no resolution offered | (nothing logged; the request is just unresolved) |
| Fuzzy query → top-1 conf 0.70–0.85 | path=fuzzy, status=ambiguous, alternates returned | `LookupResult.alternates` (response only) |

The existing `ambiguous_surface_forms` SQLite table
(`apecx-mcp-integration/src/apecx_integration/synonym_dictionary/sqlite_writer.py:87`)
already exists for the authoritative-conflict case from the original
build — corpus-mining reuses it with a new `conflict_source` discriminator.

### 3.4 Fuzzy 0.70 threshold — accept-with-caveat

The user-specified 0.70 HITL threshold is reasonable as a starting
point but is opinion, not data-driven. The plan ships at 0.70 and
treats it as **operating-parameter-configurable**, not a fundamental
contract. SC-E5 (calibration probe) measures false-positive /
false-negative tradeoff at 0.60 / 0.65 / 0.70 / 0.75 / 0.80 on a
hand-curated set of 100 queries; the result lands as a recommendation
to keep, raise, or lower 0.70 BEFORE the dictionary ships to scientists.

---

## 4. Decisions ratified in this plan

| # | Decision | Value | Notes |
|---|---|---|---|
| D1 | Fuzzy fallback | **YES** | Overrides memory `synonym_strategy_hard.md`. Memory must be updated. |
| D2 | Fuzzy implementation | **Trigram first** | Deterministic, cheap, handles typos and word-order. Embedding deferred to SC-J (out of scope for v1). |
| D3 | Fuzzy auto-resolve floor | **0.85** | Above → path=fuzzy, returned. |
| D4 | Fuzzy HITL band | **0.70 – 0.85** | path=fuzzy, status=ambiguous, alternates returned. Calibration in SC-E5. |
| D5 | Miss floor | **< 0.70** | path=miss. |
| D6 | Corpus-mined ceiling | **0.95 (≥2 sources) / 0.90 (1 source)** | Strictly < 1.0; strictly > 0.85 fuzzy ceiling. |
| D7 | Authoritative wins ties | **YES** | Even unanimous corpus disagreement does NOT override authoritative; conflict is logged to HITL instead. |
| D8 | Cross-source conflict | **HITL, no silent merge** | Both candidates logged to `ambiguous_surface_forms`; the surface form does not enter the inverse_index until curator resolves. |
| D9 | Corpus mining provenance | **Per-source observation count** | Every mined entry records `observed_in: [sources]` for re-ranking on rebuild. |
| D10 | `names.dmp` ingestion | **All 7 name classes** | scientific name + synonym + equivalent name + common name + genbank common name + acronym + blast name. |

---

## 5. Phases & tasks

Effort buckets: S (≤1 day), M (1–3 days), L (3–7 days). All tasks ship
the workspace-mandatory bundle (reproducible failure → fix → recorded
verification → real-data integration test). Tasks are designed to
land in this order; SC-A and SC-C have no cross-dependencies and can
parallelize.

### Phase SC-A — Authoritative dictionary augmentation

Goal: every NCBI Taxonomy taxon's full name set is in the dictionary,
regardless of whether the corpus ever resolved that taxon. Plus ICTV
for virus families.

| ID | Task | Files | Effort | DoD |
|---|---|---|---:|---|
| SC-A1 | Audit current `names.dmp` handling — confirm absence | `synonym_dictionary/taxdump_fetcher.py`, `synonym_dictionary/build.py` | S | Audit doc: "today's build uses OLS-per-IRI for synonyms; names.dmp is not ingested" — already half-confirmed in §0 finding 1. |
| SC-A2 | Extend `taxdump_fetcher._WANTED` to include `names.dmp` + `delnodes.dmp` | `synonym_dictionary/taxdump_fetcher.py:25` | S | Fetcher produces 4 files; unit test on fixture archive. |
| SC-A3 | Add `read_names()` parser (all 7 name classes); add `read_delnodes()` parser | `synonym_dictionary/hierarchy_loader.py` (extend) | S | Per-name-class breakdown of distinct surface forms in a fixture; unit test. |
| SC-A4 | Wire `names.dmp` into `DictionaryBuildStep`: every NCBI IRI loaded with full name-class union as synonyms (conf=1.0, status=`id_anchored`) | `synonym_dictionary/build.py`, `synonym_dictionary/workflow/dictionary_build_step.py` | M | Build produces an `entries` row per NCBI taxon (~2.7M rows full; ~10k for virus subtree pinpointed); integration test on real taxdump. |
| SC-A5 | Wire `delnodes.dmp`: deleted taxa surface a loud `unresolved` with `evidence="taxon deleted"` (not silent miss) | `synonym_dictionary/lookup.py`, `synonym_dictionary/sqlite_writer.py` | S | Lookup of a deleted-taxon IRI returns explicit "deleted" reason; integration test. |
| SC-A6 | ICTV Master Species List ingest — annual JSON / TSV; map ICTV species name → NCBITaxon IRI via cross-walk | `synonym_dictionary/ictv_ingest.py` (new); `taxdump_fetcher.py` (extend) | M | Build adds ICTV-name synonyms to existing NCBI virus entries (conf=1.0); virus-subtree distinct-surface-form count grows ≥1.5×; integration test. |
| SC-A7 | Decide virus-subtree scope vs. full NCBI Taxonomy ingest | `synonym_dictionary/build.py` (config) | S | Documented decision: virus-subtree default; full-NCBI behind explicit flag. SQLite size delta measured both ways. |
| SC-A4b | Embedded acronym extraction from `equivalent name` / `synonym` rows (terminal-anchor regex `(?:^\|\s)([A-Z][A-Z0-9]{2,7})\s*$`). NCBI does not consistently carry virus acronyms as standalone `acronym`-class rows; they appear embedded at the tail of equivalent-name strings ("eastern equine encephalomyelitis virus EEEV"). | `synonym_dictionary/build.py` (`_EMBEDDED_ACRONYM_RE`, `_synthesize_subtree_entries`) | S | 2026-06-08: lifts 2,962 embedded acronyms (terminal anchor, 5.4× noise reduction vs. unrestricted `\b...\b`); EEEV/ZIKV/WNV/HCV/SARS-CoV-2 all resolve; H1N1 regression caught and fixed (terminal anchor excludes parenthetical strain detail like `...(H1N1)...`). Unit tests pin the regex shape. |
| SC-A5b | Multi-valued in-memory inverse + `ResolutionStatus.AMBIGUOUS` + `LookupCandidate` + `LookupResult.candidates`. Surfaces collisions at the read path that the writer already records in `ambiguous_surface_forms`. Replaces both **writer-side last-write-wins** AND **loader-side first-write-wins** with a single multi-valued inverse — no silent disambiguation at any layer. | `synonym_dictionary/enums.py` (+AMBIGUOUS), `synonym_dictionary/loader.py` (multi-valued `_inverse`, `lookup_all`), `synonym_dictionary/lookup.py` (`LookupCandidate`, `_ambiguous_to_result`) | S | 2026-06-08: 603 ambiguous surface forms in the SC-A4 virus-subtree build surface as AMBIGUOUS with full candidate lists. RSV (6 candidates: Human/Bovine/Ovine orthopneumovirus, Rous sarcoma virus, RSV clade, Tenuivirus oryzaclavatae) no longer silently picks Tenuivirus. 7 unit tests added; existing 154 tests untouched (161 total green). |

**Out-of-scope for SC-A:** other ontologies (VO, ChEBI, PRO) — same
plan can be applied later; defer to a hypothetical SC-X.

**Relationship to SC-C4:** SC-A5b's `LookupResult.candidates` field
also serves SC-C4 (fuzzy `ambiguous` band). The shape is one tuple
of `LookupCandidate`; SC-C4 will populate it from trigram-fuzzy hits
in the 0.70–0.85 confidence band rather than from exact-string
collisions at conf=1.0. Reusing the field keeps the API surface
small.

### Phase SC-B — Corpus mining during parser-lift

Goal: every `(surface_form, taxon_id)` pair the parser computes during
republish becomes a candidate dictionary entry, with conflict-HITL.

| ID | Task | Files | Effort | DoD |
|---|---|---|---:|---|
| SC-B1 | Add `MinedSynonymObservation` dataclass + accumulator | `apecx-harvesters-work/src/.../pipeline/corpus_mining.py` (new) | S | Unit test: accumulator dedupes, counts sources, surfaces conflicts. |
| SC-B2 | Wire mining hook into the parser-lift pass (OE-A11 from `ONTOLOGY_ENRICHMENT_PLAN.md`) | `apecx-harvesters-work/src/.../pipeline/republish_with_canonical.py` (extend per ENRICHMENT_PLAN Phase F) | M | Republish dry-run emits a `mined_observations.jsonl` sidecar per source; non-trivial entries observed. |
| SC-B3 | Conflict detection + write to `ambiguous_surface_forms` | `apecx-harvesters-work/src/.../pipeline/corpus_mining.py` (extend); `apecx-mcp-integration/src/.../sqlite_writer.py` (extend table with `conflict_source` column) | M | Integration: a deliberately-poisoned fixture produces an entry in `ambiguous_surface_forms`; that surface form is NOT added to `inverse_index`. |
| SC-B4 | Ingest mined entries into dictionary build with proper provenance (`mined_corroborated`/`mined_observed`) | `apecx-mcp-integration/src/.../build.py` (extend) | M | Build manifest reports per-source mined-entry count + corroboration distribution; integration test reads back via `lookup_entity`. |
| SC-B5 | Mining policy: skip strain-level descriptors that map to species (avoid noise from BVBRC `Genome_Name` 745k unique long strings) | `apecx-harvesters-work/src/.../pipeline/corpus_mining.py` (extend with policy) | S | Documented policy + unit test of the rule on BVBRC fixtures; mined-entry count for `Genome_Name` against species is zero (only stored at strain taxon if present). |
| SC-B6 | Per-source mined-entry report | `apecx-harvesters-work/design/MINED_SYNONYM_REPORT.md` (generated) | S | Per-source: number of distinct mined surface forms, corroboration histogram, conflict count. |

### Phase SC-C — Fuzzy fallback (the vernacular-spelling path)

| ID | Task | Files | Effort | DoD |
|---|---|---|---:|---|
| SC-C1 | Add `path="fuzzy"` to `ResolutionPath` Literal; add `mined_corroborated`, `mined_observed`, `fuzzy_resolved`, `ambiguous`, `taxon_deleted` to `ResolutionStatus` | `synonym_dictionary/metrics.py:53`, `synonym_dictionary/enums.py:28` | S | Schema-version bump in `BuildManifest.schema_version`; loader refuses incompatible versions per the existing contract (`schema.py:9-15`). |
| SC-C2 | Build trigram index over `inverse_index.surface_form_normalized` — SQLite `INSTR`-based or a separate trigram table | `synonym_dictionary/sqlite_writer.py` (extend); `synonym_dictionary/trigram_index.py` (new) | M | Index built at end of `DictionaryBuildStep`; size measured (expected ~3× the `inverse_index` size); unit test on synthetic data. |
| SC-C3 | `lookup_entity` calls fuzzy path AFTER `fast → ancestor → slow` and BEFORE returning miss | `synonym_dictionary/lookup.py:151-165` | M | A typo'd query returns `path="fuzzy"` instead of `path="miss"`; integration test on real dictionary. |
| SC-C4 | Extend `LookupResult` with `alternates: tuple[LookupAlternate, ...]` (new dataclass: `surface_form`, `canonical_iri`, `confidence`) — populated only when `status == ambiguous` | `synonym_dictionary/lookup.py:50` | S | Field defaults to empty tuple; backwards-compatible read; unit test. |
| SC-C5 | HITL gate: 0.70 ≤ conf < 0.85 → `status=ambiguous`, alternates populated; ≥0.85 → `status=fuzzy_resolved`, alternates empty | `synonym_dictionary/lookup.py` | S | Three integration tests (one per band) on real dictionary. |
| SC-C6 | Update memory `synonym_strategy_hard.md`: HARD-only-for-first-release is REPLACED by fuzzy-fallback-with-HITL | `/Users/onarykov/.claude/projects/.../memory/synonym_strategy_hard.md` | S | Memory updated in same commit as SC-C5. |

**Recommendation on trigram implementation:** SQLite's FTS5 (full-text
search) supports trigram tokenization since 3.34. Reuse FTS5 rather
than building a custom trigram table — less code, well-tested, fast.
If the workspace's SQLite is older, fall back to a separate trigram
table with explicit `LIKE`-based queries.

### Phase SC-D — Read-time API surfaces resolution decision

Goal: a single MCP tool that returns full resolution decision + known
synonyms + cross-index results. Layered on `EO-03` from the
external-orchestration task graph.

| ID | Task | Files | Effort | DoD |
|---|---|---|---:|---|
| SC-D1 | `query_by_entity(text)` MCP tool returns `{resolution: LookupResult-equivalent, synonyms_known: [...], results_by_index: {...}}` | `apecx-mcp-integration/src/.../mcp_surface/tools/query_by_entity.py` (new) | M | Integration test against the 9 real dest indices (Ollama / live optional). |
| SC-D2 | Reverse-direction "synonyms for IRI" lookup using indexed `canonical_iri` column on `inverse_index` | `synonym_dictionary/lookup.py` (extend with `get_synonyms_for_iri(iri)`); `synonym_dictionary/sqlite_writer.py:60` (verify index exists, add if not) | S | Unit test: round-trip a surface form → IRI → synonym-set including the input. |
| SC-D3 | Globus Search fan-out across 9 dest indices via `subjects.valueUri:"<iri>"` filter, with per-index hit counts in response | `apecx-mcp-integration/src/.../mcp_surface/tools/query_by_entity.py` (extend) | M | Integration test: known taxon returns hits on the indices whose source data carries it. |

### Phase SC-E — Validation + calibration

| ID | Task | Files | Effort | DoD |
|---|---|---|---:|---|
| SC-E1 | Probe set — 100 hand-curated user queries, each labeled with expected IRI(s), path, confidence band | `apecx-mcp-integration/tests/integration/fixtures/synonym_probe_v1.jsonl` (new) | M | Coverage of 5 scenarios: (a) exact scientific name, (b) acronym, (c) common name, (d) vernacular/typo, (e) genuinely-unresolvable. ≥20 of each. |
| SC-E2 | Per-source record-anchoring metric — % records carrying ≥1 taxonomy Subject after Phase A+B | `apecx-harvesters-work/scripts/audit_taxonomy_anchoring.py` (new) | S | Report per source; target ≥99% on 8 of 9 (ProtaBank explicitly excluded). |
| SC-E3 | Per-source ancestor-chain coverage — % records carrying ≥2 taxonomy Subjects (strain + species ancestor) | same script | S | Report per source; target ≥99% on bvbrc_genome (only source with native lineage). |
| SC-E4 | Cross-index seamless-query smoke — for 10 pinned taxa, query each of the 9 indices, confirm hit counts match expected | `apecx-mcp-integration/tests/integration/test_seamless_query_cross_index.py` (new) | M | Each pinned taxon hits the sources whose source-data carries it; deviation = bug. |
| SC-E5 | Fuzzy-threshold calibration — run probe set at fuzzy floors {0.60, 0.65, 0.70, 0.75, 0.80}; report FP / FN | `apecx-mcp-integration/scripts/calibrate_fuzzy_threshold.py` (new) | S | Recommendation doc lands BEFORE the dictionary ships to scientists; the 0.70 default is confirmed or adjusted. |
| SC-E6 | Dictionary growth report — entries / inverse_index rows / disk size before vs after SC-A, B, C | `apecx-mcp-integration/scripts/dictionary_size_report.py` (new) | S | Numbers in a doc; sanity-checks the SC-A7 virus-subtree-vs-full decision. |

### Phase SC-F — Operational rollout

| ID | Task | Files | Effort | DoD |
|---|---|---|---:|---|
| SC-F1 | Dictionary version bump policy — every change in SC-A/B/C bumps `BuildManifest.schema_version` per the existing contract | `synonym_dictionary/schema.py:9-15` (verify rules) | S | Versions land in the artifact manifest; mismatch raises loader exception. |
| SC-F2 | Mined-synonym rebuild cadence — when corpus changes (a republish ships), rebuild dictionary with mining input refreshed | `synonym_dictionary/workflow/dictionary_build_workflow.yml` (extend) | S | Build step accepts mined-observations sidecar path; rebuild is idempotent. |
| SC-F3 | HITL queue export — `ambiguous_surface_forms` rows exported to a curator-readable doc per build | `apecx-mcp-integration/scripts/export_hitl_queue.py` (new) | S | Per-build report; manageable size (triage by source-count weight if > 200 entries). |

---

## 6. Reference code (file:line ground truth)

This section is the citation index for the plan. Anchor edits here
when the underlying files change.

| Reference | Location | Used in plan for |
|---|---|---|
| Taxdump fetcher's `_WANTED` set | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/taxdump_fetcher.py:25` | SC-A2 — must add `names.dmp` + `delnodes.dmp` |
| Hierarchy loader (current) | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/hierarchy_loader.py` (lines 24+, 49+) | SC-A3 — extend with `read_names()`, `read_delnodes()` |
| `DictionaryBuildStep` aggregator | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/build.py:81, 163, 235-249` | SC-A4, SC-B4 — extend aggregator to merge name-class union and mined synonyms |
| SQLite tables | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/sqlite_writer.py:46, 60, 87, 106, 117` | SC-A4, SC-B3, SC-C2 — extend schema (and version-bump) |
| `LookupResult` dataclass | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/lookup.py:50` | SC-C4 — add `alternates`; SC-C1 — extend `path` Literal |
| `lookup_entity` pipeline | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/lookup.py:93-165` | SC-C3 — insert fuzzy before miss |
| `ResolutionStatus` enum | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/enums.py:28` | SC-C1 — add new statuses |
| `ResolutionResult` schema | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/schema.py:44` | SC-C4 — extend with `alternates` field |
| Visibility contract | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/lookup.py:21-25` | SC-C5 — fuzzy-with-HITL preserves the "MUST NOT silently route" invariant |
| OLS-based synonym fetcher | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/resolvers.py:115-126, 161-175` | SC-A4 — coexists with names.dmp; OLS still used per IRI, names.dmp adds the union |
| Ambiguous-surface-form table | `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/sqlite_writer.py:87-96` | SC-B3 — extend with `conflict_source` discriminator (`authoritative_vs_mined`, `mined_vs_mined`) |
| Republish pipeline seam (planned) | `apecx-harvesters-work/src/apecx_harvesters/pipeline/republish_with_canonical.py` (per `ONTOLOGY_ENRICHMENT_PLAN.md` Phase F) | SC-B2 — corpus-mining hook lives here |
| 9-source audit | `apecx-harvesters-work/design/FIELD_COVERAGE_AUDIT_dest.json` + `_source.json` + `_SUMMARY.md` | SC-E2/E3 — denominators for anchoring metrics |
| RESOLUTION_SURFACE.md normalization rules | `apecx-harvesters-work/design/RESOLUTION_SURFACE.md:193-210` | SC-B5 — mining policy applies these (e.g., shard-suffix strip) |

---

## 7. Automated tests

Per-phase tests, in addition to the per-task DoD items in §5.

### Unit tests (fast, deterministic, fixture-driven)

| ID | Module under test | Test file | Asserts |
|---|---|---|---|
| UT-SC-A2-fetcher | `taxdump_fetcher` | `tests/unit/synonym_dictionary/test_taxdump_fetcher_names.py` | `_WANTED` includes 4 files; fixture archive extracts all 4. |
| UT-SC-A3-parser | `hierarchy_loader.read_names` | `tests/unit/synonym_dictionary/test_names_parser.py` | All 7 name classes parsed; counts match expected; case-fold normalization correct. |
| UT-SC-A4-build | `DictionaryBuildStep` with names.dmp | `tests/unit/synonym_dictionary/test_build_with_names.py` | A fixture taxon has all its name-class names in `entries.synonyms_json`; `inverse_index` has rows for each. |
| UT-SC-A5-delnodes | `lookup_entity` on deleted IRI | `tests/unit/synonym_dictionary/test_deleted_taxon_lookup.py` | Returns `unresolved` with `evidence="taxon deleted"`; never returns a candidate. |
| UT-SC-B1-mining | `corpus_mining.MinedSynonymAccumulator` | `apecx-harvesters-work/tests/test_corpus_mining_accumulator.py` | Dedup, count, conflict-surface; mined entries serialize. |
| UT-SC-B3-conflict | conflict handling | `tests/unit/synonym_dictionary/test_mining_conflict.py` | Conflicting `(surface, taxon)` pairs log to `ambiguous_surface_forms`; `inverse_index` does NOT contain the surface form. |
| UT-SC-B5-strain-policy | strain-suffix policy | `apecx-harvesters-work/tests/test_corpus_mining_strain_policy.py` | `Genome_Name="Influenza A virus (A/duck/Memphis/546/74(H11N3))"` does NOT mine as a synonym for species taxon. |
| UT-SC-C2-trigram | trigram index build | `tests/unit/synonym_dictionary/test_trigram_index.py` | Index built; top-k retrieval is sorted by similarity. |
| UT-SC-C3-fuzzy | `lookup_entity` fuzzy path | `tests/unit/synonym_dictionary/test_lookup_fuzzy.py` | Typo'd query returns `path="fuzzy"`; vernacular returns same. |
| UT-SC-C5-thresholds | fuzzy threshold gating | `tests/unit/synonym_dictionary/test_fuzzy_thresholds.py` | 0.65 → miss; 0.75 → ambiguous; 0.90 → fuzzy_resolved. |

### Integration tests (real artifacts, real data)

| ID | Module | Test file | Asserts |
|---|---|---|---|
| IT-SC-A4-real | Real taxdump + real build | `tests/integration/test_dictionary_build_with_real_names_dmp.py` | Resolves `EEEV` to `NCBITaxon:11036` even with no VIOLIN row carrying that acronym. Skip-gated on real taxdump availability. |
| IT-SC-A6-ictv | ICTV ingest end-to-end | `tests/integration/test_ictv_ingest_real.py` | Recent ICTV virus name resolves correctly. |
| IT-SC-B4-mining-e2e | Real corpus mining over a single source | `apecx-harvesters-work/tests/test_corpus_mining_e2e.py` | After a republish pass on antiviraldb (35 records), the mined surface forms are queryable via `lookup_entity` with `conf < 1.0`. |
| IT-SC-C3-fuzzy-real | Real dictionary + fuzzy queries | `tests/integration/test_fuzzy_lookup_real_dictionary.py` | All 10 typo'd queries from the probe set resolve to expected IRIs. |
| IT-SC-D1-mcp | `query_by_entity` MCP tool against real indices | `tests/integration/test_query_by_entity_against_globus.py` | Query for `EEEV` returns hits from each of the 8 taxonomy-anchored indices (or the subset whose source data carries the taxon). |
| IT-SC-E4-seamless | Cross-index seamless query for 10 pinned taxa | `tests/integration/test_seamless_query_cross_index.py` | Per-taxon hit-count matches expected (set from source-side `NCBI_Taxon_ID` distribution). |

### Probe / calibration "tests" (advisory, not gating)

| ID | Module | Test file | Output |
|---|---|---|---|
| PROBE-SC-E1 | Probe set against current dictionary (baseline) | `tests/integration/test_synonym_probe_baseline.py` | Per-scenario pass rate, JSON output, written to `_workspace_notes/synonym_probe_baseline.json` |
| PROBE-SC-E5 | Probe set at varying fuzzy thresholds | `tests/integration/test_synonym_probe_threshold_sweep.py` | FP/FN curve, recommendation. |

---

## 8. Pass criteria (per phase, numeric, enforceable)

| Phase | Metric | Target | How measured | Failure disposition |
|---|---|---:|---|---|
| SC-A | `names.dmp` parsed into dictionary | ≥99% of taxa in NCBI virus subtree have ≥3 name-class entries | `tests/integration/test_dictionary_build_with_real_names_dmp.py` + SC-E6 size report | Block SC-D1 / scientist rollout |
| SC-A6 | ICTV ingest | ≥99% of ICTV species names resolve via fast path | IT-SC-A6-ictv | Defer ICTV; ship SC-A4 alone |
| SC-B | Corpus mining yields growth | Mined dictionary entries ≥ 30% of pre-mining inverse_index size | SC-E6 | Investigate parser-lift coverage |
| SC-B | Conflict count manageable | Total `ambiguous_surface_forms` rows after mining < 1,000 | Build manifest | Triage policy: surface top-N by record-weight |
| SC-C | Fuzzy fallback handles probe vernacular | ≥80% of vernacular/typo probes resolve at conf ≥ 0.70 | PROBE-SC-E1 | Tune trigram tokenization; consider embedding fallback |
| SC-C | No silent fabrication | 100% of `path != miss` results have a non-None canonical_iri AND a defensible `evidence` string | UT-SC-C5-thresholds | Block release; this is the visibility contract |
| SC-E2 | Per-source anchoring | ≥99% of records on 8 of 9 dest indices carry ≥1 NCBI Taxonomy Subject | SC-E2 audit | Per-source parser fix |
| SC-E3 | Ancestor-chain coverage | ≥99% on bvbrc_genome (source carries `Taxon_Lineage_Names` at 100%) | SC-E3 audit | Parser fix |
| SC-E4 | Cross-index seamless query | For each of 10 pinned taxa, hit-count parity with source-side `NCBI_Taxon_ID` distribution (±5%) | IT-SC-E4-seamless | Per-source parser bug |
| SC-E5 | Fuzzy threshold calibration | FP ≤ 5% at chosen threshold OR documented justification for the chosen tradeoff | SC-E5 sweep | Adjust default threshold before scientist rollout |

**Hard release gate:** SC-A1..A5 + SC-C1..C6 + SC-E1 + SC-E5 must
pass before scientist-facing rollout. SC-A6 (ICTV) is optional for v1
but recommended.

**Soft release gate:** SC-B and SC-D are quality improvements; ship
when ready, do not block v1 on them. SC-B in particular benefits from
the parser-lift work landing first.

---

## 9. Honest opinion — what could still go wrong (be brutal here)

I'm being skeptical about my own design. These are the failure modes I
think are real, ordered by likelihood × impact.

### 9.1 Likely to bite

1. **`names.dmp` is ~250 MB unpacked.** Loading all NCBI Taxonomy
   names into the dictionary inflates SQLite substantially (rough
   estimate: 10-50× current size depending on virus-subtree vs
   full-tree). The artifact stops being trivially shippable. **Mitigation:**
   the SC-A7 decision (virus subtree by default) is load-bearing.
   Without it, the artifact balloons and the lazy-bootstrap path
   (`synonym_dictionary/workflow/bootstrap.py:ensure_dictionary` per
   CLAUDE.md) gets very slow on first run. Measure and decide.

2. **Trigram fuzzy false positives at 0.70.** Trigram similarity over
   short surface forms is noisy. A query like `Flu` (3 chars) might
   trigram-match a dozen unrelated terms with score > 0.70. **Mitigation:**
   SC-E5 calibration is mandatory; expect to either (a) raise the
   floor to 0.75-0.80, (b) require a minimum surface-form length (e.g.,
   ≥5 chars) before fuzzy is attempted, or (c) both. The plan ships
   at 0.70 because the user asked, but I'd bet the calibration result
   nudges it up.

3. **Cross-source conflict count is huge.** "Influenza A virus" vs.
   "Influenza A" vs. "Influenza A Virus" might map to different
   taxa across BVBRC, VIOLIN, and AntiviralDB just because they're
   tagging different things. Conflict-HITL was sized assuming a few
   dozen real conflicts; it could be hundreds or thousands.
   **Mitigation:** SC-F3 export with triage-by-record-weight; consider
   collapsing case + whitespace before conflict detection (this is
   normalization, not silent merging — but it does reduce noise).

4. **The `mined_corroborated` / `mined_observed` distinction is too
   subtle to be load-bearing in downstream consumers.** Most consumers
   will treat any `conf < 1.0` the same way. The 0.05 gap between
   0.95 and 0.90 might never matter. **Mitigation:** that's fine — keep
   the gradation for observability even if downstream collapses it.
   Don't over-engineer consumer policy around it.

### 9.2 Could bite if we're unlucky

5. **NCBI Taxonomy's name classes contain quality noise.** `equivalent
   name` and `common name` sometimes contain non-canonical variants
   that are wrong-but-historical. Loading them with conf=1.0 means a
   user query that hits one of those finds the right taxon — but also
   means a curator might object to "wrong" synonyms being in the
   dictionary. **Mitigation:** make name-class loading
   per-class-configurable (e.g., `--include-name-class
   scientific,synonym,acronym,common`). Default to all 7; let
   operators trim if they object.

6. **ICTV ingest cross-walk has gaps.** NCBI Taxonomy and ICTV use
   different naming conventions; the cross-walk isn't 1:1.
   **Mitigation:** SC-A6's DoD says "≥99% map" — if it falls below,
   defer ICTV (it's a soft gate) and revisit the cross-walk strategy.

7. **The `RESOLUTION_SURFACE.md` normalization rules are documented
   but not implemented in one place.** SC-B5 mentions them; SC-C3
   uses them implicitly. If they live in two places, they'll drift.
   **Mitigation:** SC-B5 must produce a single
   `synonym_dictionary/normalization.py` module that BOTH the mining
   pass AND `lookup_entity` import. The existing
   `synonym_dictionary/normalization.py:16` is the starting point; it
   already exists, just incomplete.

### 9.3 What I'm probably wrong about

8. **The 100-query probe set in SC-E1 is too small for statistical
   confidence on the 0.70 threshold.** 100 queries × 5 scenarios = 20
   per scenario; the FP/FN bands at p=0.05 are roughly ±10%. Honest
   answer: 100 is the right size for a tractable hand-curated probe;
   the threshold sweep result should be read as directional, not
   precise. If precision matters, grow the probe set to 500 with
   programmatic generation + human verification of a sample.

9. **I'm proposing trigram first and embedding deferred.** I think
   that's right for cost reasons, but I'm not certain trigram catches
   the "Flu A" → "Influenza A virus" mapping (different lengths,
   little char overlap). If SC-E5 shows trigram alone underperforms,
   the right move is sentence-transformer embeddings, which the
   workspace already has plumbed
   (`nanobrain/lightweight/component_index.py`). This would be a
   Phase SC-J follow-up. Don't kick off SC-J without measured need.

### 9.4 Critique of the user's requests where warranted

10. **0.70 is opinion, not measured.** Accept for v1 but treat as
    operating-parameter; SC-E5 measures it. Don't carve it in code as
    a constant — put it in the build manifest.

11. **"1.0 always prevails" is correct in principle but means the
    pipeline can never learn from data.** A virus taxonomy revision
    that makes NCBI wrong relative to current literature gets stuck
    at the wrong IRI forever. This is acceptable for v1 (we don't
    want to silently override authoritative sources) but warrants
    documentation as a known limitation. **Recommendation:** log
    authoritative-vs-mined conflicts to a separate `gaps_candidates`
    list per the existing `ONTOLOGY_GAPS_CANDIDATES.md` pattern, so a
    human can periodically review and decide whether to push back
    upstream to NCBI/ICTV.

12. **"HITL on conflicts" assumes a HITL exists.** Today there is no
    human in the loop for the dictionary build — the build runs
    lazily at MCP startup
    (`synonym_dictionary/workflow/bootstrap.py:ensure_dictionary`).
    HITL means a periodic curator pass over the exported queue
    (SC-F3); this needs to be staffed or the queue grows unbounded.
    **Recommendation:** the doc explicitly notes this is HITL-batch,
    not HITL-real-time, and we ship the export tool, NOT a runtime
    blocking gate.

### 9.5 Critique of my own design

13. **I'm proposing to overload the existing `ambiguous_surface_forms`
    table for two different conflict types.** That might be the wrong
    call — `authoritative_vs_mined` and `mined_vs_mined` have different
    curator workflows. **Mitigation:** the new `conflict_source`
    column (SC-B3) discriminates; a small schema cost for a clearer
    operational story.

14. **The dictionary build is a serial bottleneck.** Adding
    `names.dmp` + ICTV + mining widens the SC-A→SC-B→SC-C ordering
    only slightly (mining depends on the parser-lift pass which
    depends on the harvester republish — but parser-lift IS the
    republish). The longer dependency chain means total elapsed time
    grows. **Mitigation:** SC-A is independent of SC-B; parallelize.

15. **I'm proposing a new MCP tool (SC-D1) that overlaps the
    existing `canonical_entity` tool
    (`apecx-mcp-integration/src/apecx_integration/mcp_surface/tools/canonical_entity.py`).**
    Worth checking whether SC-D1 should extend that tool's response
    shape instead of being a new tool. Defer the decision to
    implementation time; the surface choice is small.

---

## 10. Out of scope

- **Other ontologies** (VO term lookup for vaccines, ChEBI for drugs,
  PRO for proteins). Same design applies — apply later via a sibling
  plan. NCBI Taxonomy is the focus because it's the spine for 8 of 9
  indices.
- **Embedding-based semantic fuzzy** (Phase SC-J). Defer until
  trigram is measured insufficient by SC-E5.
- **Real-time HITL** (e.g., a UI for curators). SC-F3 ships an export;
  the UI is a separate workstream.
- **Cross-source de-duplication of records** (same paper indexed in
  PubMed + CrossRef). Not in scope here — that's a record-identity
  problem, not a synonym problem.
- **Refactoring the dictionary build to be incremental.** Today it
  rebuilds from scratch every time. SC-F2 keeps that model; if rebuild
  time exceeds 30 min after SC-A lands, revisit.
- **Per-user / per-session synonym preferences.** Out of scope per
  `lookup.py:29` ("Per-user or per-session caching — unnecessary at
  current scale").

---

## 11. Open questions (need user input before SC-A2 starts)

| # | Question | Why it blocks | Default if not answered |
|---|---|---|---|
| Q1 | Virus subtree vs. full NCBI Taxonomy ingest? | SC-A4 build time + artifact size | Virus subtree (SC-A7 default) |
| Q2 | ICTV scope — viruses only or all of ICTV? | SC-A6 size | Viruses only |
| Q3 | Trigram or embedding for fuzzy? | SC-C2 | Trigram (D2) |
| Q4 | Where does the HITL curator workflow live? | SC-F3 usability | Export to a Markdown file under `_workspace_notes/`; curator commits resolutions back |
| Q5 | Does ProtaBank participate? | SC-E2 metric denominator | No — explicit exclusion; ProtaBank queryable by UniProt accession only |

---

## 12. Cross-repo coordination

Most of the code lives in `apecx-mcp-integration`. Mining lives in
`apecx-harvesters-work` (the parser-lift seam). Two coordination
points:

1. **Schema version sync.** `BuildManifest.schema_version` in
   `apecx-mcp-integration/src/apecx_integration/synonym_dictionary/schema.py`
   gets bumped per SC-F1. The harvester reads this version when
   feeding mined entries; mismatched versions → loader exception per
   the existing contract. Ship coordinated commits across both repos.

2. **Mined-observation sidecar format.** SC-B2 emits
   `mined_observations.jsonl`; SC-B4 reads it. The format is the
   contract — both sides must agree. Pin format in a dataclass shared
   via Pydantic schema; alternative is a JSON Schema doc in
   `apecx-harvesters-work/design/MINED_OBSERVATIONS_FORMAT.md`.

---

## 13. Implementation log (append-only)

- 2026-06-08 — Plan drafted. Authoritative names.dmp absence
  identified (`taxdump_fetcher.py:25`). Three load-bearing decisions
  ratified (D1 fuzzy reopened, D7 authoritative-wins-ties, D8
  conflict-HITL-no-silent-merge). Open questions Q1-Q5 sent to the
  user. No code yet.
- 2026-06-08 — Q1 ratified (virus subtree, NCBITaxon:10239).
  SC-A2 + SC-A3 + SC-A4 shipped:
  - `taxdump_fetcher._WANTED` extended to all 4 NCBI dump files.
  - `hierarchy_loader.py` gains `parse_names_dmp`, `parse_delnodes_dmp`,
    `compute_subtree_descendants`, `NCBI_NAME_CLASSES_INGESTED` (7
    classes).
  - `build.py` synthesizes one entry per virus-subtree taxon
    (`_synthesize_subtree_entries`) with corpus-mined IRI merge.
  - `sqlite_writer.py` writes `deleted_taxons` table.
  - Dictionary built: 281,736 virus-subtree taxa, 243.9 MB
    (`build/dictionary_sc_a4/dictionary.sqlite`).
  - 12 new unit tests for the 4-file fetcher + parsers + BFS.
- 2026-06-08 — SC-A4b in-stream addition: NCBI does NOT carry virus
  acronyms (EEEV/ZIKV/WNV/HCV) as standalone `acronym`-class rows
  contrary to the original design assumption (§ 3 was overconfident).
  Real shape is embedded at row tail: `"eastern equine encephalomyelitis virus EEEV"`
  in `equivalent name`/`synonym` rows. Added terminal-anchor regex
  `(?:^|\s)([A-Z][A-Z0-9]{2,7})\s*$`. First-pass unrestricted
  `\b[A-Z][A-Z0-9]{2,7}\b` lifted "H1N1" from 36,201 strain isolate
  names and made the H1N1 inverse_index entry point at a random
  isolate (NCBITaxon:2071762) instead of the H1N1 subtype
  (NCBITaxon:114727) — the user's "RSV ambiguity" example surfaced
  before the same failure mode hit H1N1 in production. Terminal
  anchor restored H1N1 → 114727 and reduced lift count from 15,904
  to 2,962 (5.4× noise reduction).
- 2026-06-08 — SC-A5b shipped: multi-valued inverse + AMBIGUOUS
  resolution path. Root cause: the writer already captured 603
  ambiguous surface forms in `ambiguous_surface_forms`, but the
  reader silently picked the inverse_index winner (last-write-wins
  in SQLite, first-write-wins in the in-memory loader — two layers,
  two wrong policies). The fix is single-layer: `DictionaryIndex._inverse`
  changed from `dict[k, str]` to `dict[k, tuple[str, ...]]`;
  `lookup_all` returns the full candidate tuple; `lookup_entity`
  branches on 0/1/N candidates and routes ≥2 to
  `ResolutionStatus.AMBIGUOUS` with full `LookupCandidate` list.
  RSV verified: pre-fix → silently picked Tenuivirus oryzaclavatae
  (3052763, the worst possible default for human-facing queries);
  post-fix → AMBIGUOUS with 6 candidates including both Human and
  Bovine orthopneumovirus, surfaced for HITL. 7 unit tests added.
  No SQLite rebuild needed — fix is reader-only.
- 2026-06-08 — Dictionary promoted to prod (see SC-A8 row TBD in
  next revision); pre-promotion backup at
  `~/.apecx/dictionary/dictionary.sqlite.pre-sc-a4.bak`.
- 2026-06-08 — `apecx-lookup` operator CLI shipped
  (`apecx_integration/cli/lookup.py`, console script entry in
  `pyproject.toml`). Thin wrapper over `lookup_entity`; no new
  resolution logic. Exit codes 0/1/2/3 for hit/miss/argerror/dict-load.
  Self-bootstrap on `sys.path` so file-path invocation also works under
  any interpreter that has the runtime deps installed (existing CLI
  files don't carry this; deliberate surgical scope). 5 unit tests
  (smoke level — full lookup contract tested in `test_loader_lookup.py`).
- 2026-06-08 — SC-A5 shipped: NCBI delnodes wiring at lookup time.
  Added `ResolutionStatus.TAXON_DELETED` + `LookupResult.path="deleted"`.
  `DictionaryIndex.is_taxon_deleted(iri)` queries the `deleted_taxons`
  SQLite table (populated by SC-A4 build); `lookup_entity` short-circuits
  on a deleted NCBITaxon IRI BEFORE the ancestor walk so the user sees
  "taxon retired by NCBI" instead of being silently redirected to the
  parent species. 4 unit tests; closes the SC-A5 row's DoD.
- 2026-06-08 — SC-C shipped (C1+C2+C3+C5; C4 already covered by SC-A5b's
  shared `LookupCandidate` field; C6 = memory update done): trigram-Jaccard
  fuzzy fallback.
  - **C1**: `ResolutionStatus.FUZZY_RESOLVED` + `LookupResult.path="fuzzy"`.
  - **C2**: Lazy in-memory inverted trigram index built on first
    `lookup_fuzzy` call (`DictionaryIndex._ensure_trigram_index`);
    cached for process lifetime. No SQLite schema change; no disk-size
    bloat (would have been ~150-200 MB if persisted). One-time build
    cost ~5s on the 281k-taxon prod dictionary; subsequent fuzzy
    queries sub-millisecond.
  - **C3**: Fuzzy path fires AFTER fast/ancestor/slow, BEFORE returning
    miss. Cannot clobber verbatim hits (regression test pinned).
  - **C5**: Banding per §3.4 — `≥0.85 single hit (no near-tied runner-up,
    margin ≤0.05)` → `FUZZY_RESOLVED`; `0.70 ≤ top < 0.85` OR near-tied
    top → `AMBIGUOUS` + `LookupCandidate` alternates; below 0.70 → miss.
  - **C6**: User memory `synonym_strategy_hard.md` updated with the
    layer-distinction note (workflow-level Step 3b still HARD-deferred;
    dictionary-level lookup now fuzzy-enabled).
  - 10 unit tests added (fuzzy primitives, band-gate, regression guards).
  - Probe outcomes against promoted prod dict:
    * `Severe acute respiratory syndrom coronavirus 2` (1-char drop on
      45-char canonical) → **fuzzy 0.90 → NCBITaxon_2697049** (NEW win).
    * `Hepatitus C virus` (17-char typo) → **AMBIGUOUS 0.76**, top =
      Orthohepacivirus hominis = HCV's renamed canonical (NEW HITL).
    * `CHIKV`, `Flu A` → still miss. **Brutal-truth limitation**:
      acronyms / vernacular abbreviations have Jaccard ≈ 0.08-0.27
      against their full forms — fuzzy CANNOT lift them. Requires SC-B
      corpus mining or explicit acronym rows. Not a SC-C bug.
  - All 179/179 synonym_dictionary unit tests pass (was 161 at SC-A5b);
    broader unit suite untouched.
- 2026-06-08 — SC-E shipped (E1+E5+E6; E2/E3 deferred until SC-B lands,
  E4 deferred until Globus dest indices populated):
  - **E1**: 100-query probe set at
    `apecx-mcp-integration/tests/integration/fixtures/synonym_probe_v1.jsonl`
    with 20 entries each of scientific_name / acronym / common_name /
    typo / unresolvable. Built by
    `scripts/build_synonym_probe.py` against prod dictionary;
    behavior-pinned in `tests/integration/test_synonym_probe_v1.py`
    (102 tests, 100 parametrized + 2 structural). 20/100 candidates
    showed belief-vs-behavior mismatches at build time: **3 real
    coverage gaps** (CHIKV, HSV-2, DENV — NCBI doesn't carry these
    as standalone acronym rows; SC-B / SC-A6 ICTV targets); **5
    stale-training-knowledge corrections** (NCBI Taxonomy was
    reorganized post-2022: HCV → Orthohepacivirus hominis 3052230,
    Lassa virus → 3052310, mumps virus → 2560602, CMV → 12305,
    SARS-CoV-1 → 2901879 distinct from 694009 SARS-related); **12
    fuzzy-band edge cases** that landed on different paths than my
    belief but still consistent with the SC-C contract (the calibration
    data).
  - **E5**: `scripts/calibrate_fuzzy_threshold.py` replays the probe
    set at floors {0.60, 0.65, 0.70, 0.75, 0.80, 0.85} and reports
    typo recall vs unresolvable FPR. **Result against the v1 probe set:**

    | T    | typo recall | unresolvable FPR | F1    |
    |------|-------------|------------------|-------|
    | 0.60 | 0.900       | 0.000            | 0.947 |
    | 0.65 | 0.900       | 0.000            | 0.947 |
    | 0.70 | 0.750       | 0.000            | 0.857 |
    | 0.75 | 0.700       | 0.000            | 0.824 |
    | 0.80 | 0.500       | 0.000            | 0.667 |
    | 0.85 | 0.350       | 0.000            | 0.519 |

    **Headline finding**: zero false positives at every tested floor
    over 20 unresolvable noise probes — trigram-Jaccard is highly
    discriminative against random garbage. The user-ratified **0.70
    default leaves 15% of typo recall on the table** vs 0.65 (3 extra
    typo cases lift, all routed to AMBIGUOUS/HITL).
    **Recommendation (advisory, not yet applied)**: lower the floor
    to 0.65. Caveats: (1) the 20-probe unresolvable set is small;
    biology-adjacent noise (real but unrelated gene/species names
    that share trigrams) could surface FPR not visible here.
    (2) A SC-E5b follow-up with 100+ adversarial-noise probes is
    advised before flipping the constant in `lookup.py`. The user
    must explicitly ratify the change before the code constant moves.
  - **E6**: `scripts/dictionary_size_report.py`. SC-A delta vs
    pre-SC-A4 baseline:

    | metric | pre-SC-A4 (2026-05-12) | post-SC-A4 (2026-06-08) | Δ |
    |---|---|---|---|
    | size | 75 MB | 244 MB | +169 MB (3.25×) |
    | `entries` | 6.4k | 281.7k | +275k (44×) |
    | `inverse_index` | 8.5k | 316.0k | +307k (37×) |
    | `ambiguous_surface_forms` | 279 | 603 | +324 (2.2×) |
    | `deleted_taxons` | (absent) | 780.5k | new |
    | `unresolved_count` | 17,976 | 0 | corpus-bounded → comprehensive |

    The `unresolved_count` collapse to zero is the single clearest
    receipt for SC-A's value: every virus-subtree taxon now has a
    dictionary entry directly, vs. relying on whatever the corpus
    happened to resolve. SC-A7 virus-subtree decision validated:
    a full-NCBI build would scale ~10× to ~2.4 GB, tractable but
    not justified by current use cases.
  - Deferred (out of scope this turn):
    * SC-E2 / SC-E3 per-source anchoring metrics — depend on SC-B
      corpus mining shipping in apecx-harvesters-work AND on a
      republish pass against real source data.
    * SC-E4 cross-index Globus seamless-query smoke — depends on
      the 9 dest indices being populated and Globus Search
      credentials being available in the integration environment.
- 2026-06-08 — Dictionary expansion + SC-E5b + SC-D1 ship:
  - **Dictionary expansion** (NCBI ``includes`` class):
    ``NCBI_NAME_CLASSES_INGESTED`` extended from 7 → 8 classes.
    Applied to prod via ``scripts/apply_includes_delta.py`` (one-shot,
    surgical — avoids a full ~30 min rebuild for a +0.14 MB delta).
    **Result**: +214 synonyms across 169 entries (+1 ambiguity capture).
    Most valuable additions: NCBITaxon_12059 gains "common cold viruses",
    "Rhinovirus", "Rhinoviruses"; NCBITaxon_11096 gains
    "hog cholera virus (HCV) strains" (no collision with HCV acronym —
    different normalized surface form). Brutal-truth scope assessment:
    the dictionary at SC-A4 already ingested 95%+ of the meaningful
    NCBI name space — the remaining NCBI classes (``authority``,
    ``type material``, ``in-part``) are NOT synonyms. The next real
    coverage lever is **ICTV (SC-A6)**, not further NCBI classes.
    Existing test ``test_ingested_name_classes_match_design_doc``
    updated to pin the new 8-class set.
  - **SC-E5b** adversarial-noise probe set:
    50 biology-adjacent unresolvable probes added to
    ``tests/integration/fixtures/synonym_probe_v1.jsonl`` under new
    ``adversarial_noise`` scenario label. Total fixture now 152 probes
    (20 each SC-E1 + 52 SC-E5b). Pure-noise invariant
    (``test_unresolvable_probes_actually_miss``) remains strict on
    the original 20; new test
    ``test_adversarial_noise_fpr_bounded`` adds a 30% FPR ceiling on
    the adversarial population.
    **Revised calibration table (replaces the previous SC-E5 result):**

    | T    | typo recall | noise (pure) | noise (adv) | noise (all) FPR | F1    |
    |------|-------------|--------------|-------------|-----------------|-------|
    | 0.60 | 0.900       | 0/20         | 19/52       | **0.264**       | 0.810 |
    | 0.65 | 0.900       | 0/20         | 17/52       | **0.236**       | 0.826 |
    | 0.70 | 0.750       | 0/20         | 11/52       | **0.153**       | 0.796 |
    | 0.75 | 0.700       | 0/20         | 7/52        | **0.097**       | 0.789 |
    | 0.80 | 0.500       | 0/20         | 5/52        | **0.069**       | 0.650 |
    | 0.85 | 0.350       | 0/20         | 5/52        | **0.069**       | 0.509 |

    **Honest recommendation REVERSAL**: the original SC-E5 finding
    suggested lowering to 0.65 — but that was on the easy pure-noise
    set where FPR was 0% at every threshold. With realistic
    adversarial noise, lowering to 0.65 trades +15% typo recall for
    +8 percentage points of false-positive HITL queue noise — a
    worse-than-1:1 ratio. **Confirm 0.70 as the production default**;
    the math F1 picks 0.65 because it weighs precision and recall
    equally, but operator experience says HITL queue pollution is more
    costly than a typo that fails to auto-lift. ``test_adversarial_noise_fpr_bounded``
    now pins this constant at 30%; any future change loosening
    the fuzzy floor must justify the FPR delta.
  - **SC-D1** MCP tool surface:
    ``apecx_integration/mcp_surface/tools/canonical_entity.py`` extended
    to emit ``candidates: [{canonical_iri, canonical_label,
    canonical_ontology, confidence}, ...]`` whenever
    ``resolution_path == "ambiguous"``. Empty list for every other path
    so downstream consumers can use ``len(candidates) > 0`` as a
    uniform ambiguity signal. Docstring updated to surface the full
    SC-A/C path taxonomy (fast / ambiguous / ancestor / slow / fuzzy /
    deleted / miss) and the new resolution statuses
    (``ambiguous`` / ``fuzzy_resolved`` / ``taxon_deleted``) so the
    model-facing tool catalogue exposes them. **Wire contract: additive
    only — existing keys unchanged.** 2 new unit tests
    (``test_resolve_canonical_entity_ambiguous_returns_candidate_list``,
    ``test_resolve_canonical_entity_candidates_empty_on_non_ambiguous``).
    8/8 canonical_entity tests pass.
  - Test scoreboard: **342/342 passing** (179 unit + 5 CLI + 8
    canonical_entity + 102 probe integration + 48 other unit) — no
    regression.
  - Still NOT shipped this turn (correctly out of scope):
    * SC-A6 ICTV — annual JSON ingest, M-effort, would land
      virus-specific names not in NCBI Taxonomy. **The next-biggest
      coverage lever** if the user wants more synonyms.
    * SC-B corpus mining — sibling-repo work in apecx-harvesters-work.
      The path to fix CHIKV/DENV/HSV-2 acronym gaps.
    * SC-D2/D3 — reverse synonym lookup + Globus Search fan-out.
      Need MCP tool surface area design separately.
- 2026-06-08 — mu-virus-list harmonization smoke + regression pin:
  - Tested all 70 vernacular-style virology terms from
    ``apecx-harvesters/search_demo/data/mu-virus-list.txt``
    against the prod dictionary.
  - **Baseline harmonization rate**: **61/70 = 87.1% fast resolution**.
  - 5/70 ambiguous — correctly flagged for HITL:
    * ``herpesvirus`` (1 candidate)
    * ``hepatitis virus`` (10 candidates — A/B/C/D/E + GB virus C +
      Heron HBV + Torque teno + Bat HBV + Hep delta)
    * ``hemorrhagic fever virus`` (4 candidates)
    * ``marburg virus`` (2 candidates — Marburg virus + Orthomarburgvirus
      marburgense)
    * ``parainfluenza virus`` (2 candidates)
  - 4/70 miss — direct SC-B corpus-mining targets:
    * ``coronavirus`` (Coronaviridae family exists at NCBITaxon_11118
      but has no "coronavirus" synonym)
    * ``poxvirus`` (Poxviridae NCBITaxon_10240, same shape)
    * ``papilloma and polyoma viruses`` (multi-family conjunction —
      SC-B unlikely to help)
    * ``arbovirus`` (ecological category — NOT a NCBI taxon; only
      corpus mining over papers using it as shorthand could help)
  - **Quality concern surfaced (NOT a regression — NCBI-inherited):**
    ``avian influenza virus`` → NCBITaxon_11309 "unidentified influenza
    virus" (a placeholder taxon). Should resolve to Influenza A virus
    (NCBITaxon_11320). The mapping comes from NCBI's own
    ``names.dmp`` synonym row attaching "avian influenza virus" to
    11309 — SC-B corpus co-occurrence frequency could shift the
    canonical winner to 11320.
  - Baseline pinned at ``tests/integration/fixtures/mu_virus_list.txt``
    + ``mu_virus_list_baseline.jsonl``; regression suite
    ``test_mu_virus_list_harmonization.py`` (72 tests: 70 per-query
    pins + headline rate floor at 0.85 + silent-improvement guard).
- 2026-06-08 — SC-B1 shipped (corpus_mining foundation, cross-repo):
  - **Location**: ``apecx-harvesters-work/src/apecx_harvesters/pipeline/
    corpus_mining.py`` (new).
  - **Surface**: ``MinedSynonymObservation`` frozen dataclass +
    ``MinedSynonymAccumulator`` class. I/O-free pure module — no
    SQLite, no Globus, no parser imports.
  - **Hook signature**: ``accumulator.observe(surface_form, taxon_id,
    source=...)``. Designed to be called from any parser-lift step
    that already has both fields (per inspection of
    ``violin_pathogen/parser.py``: ``fields.Pathogen`` +
    ``fields.NCBI_Taxonomy_ID`` are right there).
  - **Defensive noise filter**: rejects empty/short/numeric/null-sentinel
    surface forms; rejects non-positive taxon ids. Per-source
    ``observed`` + ``rejected`` counters surface for SC-B6 reports.
  - **Corroboration semantics**: ``unique_pairs()`` yields ``(obs,
    source_count, source_set)`` so SC-B4 can apply confidence tiers
    (≥2 sources → 0.95 ``mined_corroborated``; 1 source → 0.90
    ``mined_observed``).
  - **Conflict surfacing (SC-B3 precursor)**:
    ``surface_form_conflicts()`` yields ``(normalized, {taxon_ids})``
    where the same surface maps to ≥2 distinct taxa. The accumulator
    records faithfully; conflict POLICY (HITL routing vs. ingest with
    alternatives) lives in SC-B4's ingest, not here.
  - 12 unit tests; 1292/1292 + 1 skipped — no regression in the
    broader harvesters suite.
  - **Still NOT shipped**:
    * SC-B2 — wire mining into actual parser-lift (~9 parsers × small
      edit each, but each parser's surface-form availability needs
      audit before edits land; bvbrc_genome's ``Genome_Name`` carries
      ~745k unique strain strings that SC-B5 policy must filter).
    * SC-B3 — conflict detection write-through to dictionary build.
    * SC-B4 — ingest mined entries with provenance.
    * SC-B5 — strain-level descriptor filter policy.
    * SC-B6 — per-source mined-entry report.
- 2026-06-08 — SC-B2 + SC-B3 + SC-B4 + SC-B5 shipped end-to-end:
  - **SC-B2 (parser-extractor framework)** —
    ``apecx_harvesters/pipeline/corpus_mining_extractors.py`` provides
    a per-source extractor registry that maps a parsed DataCite
    container to ``(surface_form, taxon_id)`` pairs. v1 covers four
    high-signal sources: ``violin_pathogen``, ``bvbrc_epitope``,
    ``bvbrc_genome``, ``bvbrc_protein_structure``. Wired into
    ``harmonize_index`` via an opt-in ``mining_accumulator`` keyword
    argument — existing callers don't change. Provenance dict gains
    ``mining_extractor`` and ``mining_observations_accepted`` when
    enabled. 15 unit tests; bvbrc_genome dedupes species-level taxa
    across strain-level ``species.strain`` entries (one obs per
    species per container).
  - **SC-B5 (strain-level filter)** — heuristic
    ``is_strain_level()`` in ``corpus_mining``:
    - flu-style ``A/<host>/<location>/<isolate>/<year>`` notation,
    - accession-prefixed strings,
    - ``strain``/``isolate``/``subsp.``/``subspecies``/``clone``/
      ``sub-type``/``variant`` keyword + descriptor,
    - long (>60 chars) strings with parens+slash+digits.
    Wired into the accumulator's ``observe`` reject path. Tested with
    real strain examples (e.g.,
    ``"Influenza A virus (A/common pochard/Shanxi/16B/2015(H5N1))"``)
    and 9 known-good real virus names that must NOT be flagged.
  - **SC-B3 (conflict surfacing)** — new ``mined_conflicts`` SQLite
    table in ``apecx-mcp-integration/synonym_dictionary/sqlite_writer.py``
    (in ``_SCHEMA_DDL`` so it's created on writer init). Composite PK
    on ``(surface_form_normalized, candidate_taxon_id,
    conflict_source)`` preserves per-source provenance. Does NOT
    extend existing ``ambiguous_surface_forms`` — schema-stable for
    older readers. ``write_mined_conflicts(...)`` method on the
    writer; auto-applied during SC-B4 ingest.
  - **SC-B4 (mined-observations ingest)** — new module
    ``apecx_integration/synonym_dictionary/mined_ingest.py``:
    ``ingest_mined_observations(dict_path, mined_jsonl, entity_type)``
    reads the SC-B sidecar JSONL and applies it to an existing
    dictionary. Honors the SC-A5b multi-IRI inverse path (writes to
    ``ambiguous_surface_forms`` on collision). Schema migration:
    creates ``mined_conflicts`` table if absent (handles older
    pre-SC-B3 dictionaries gracefully). PRAGMA WAL +
    synchronous=NORMAL + 64MiB cache_size for perf. New
    ``ResolutionStatus.MINED_CORROBORATED`` (≥2 sources, conf 0.95)
    and ``MINED_OBSERVED`` (1 source, conf 0.90). 8 unit tests.
    CLI: ``scripts/apply_mined_observations.py``.
  - **End-to-end demo via CSV pipeline** —
    ``scripts/mine_corpus_csv.py`` reads ``~/.apecx/dictionary/enriched/``
    CSVs (the local corpus the SC-A4 build used) and emits
    ``mined_observations.jsonl`` via the harvesters accumulator —
    same SC-B5 filter, same dedup semantics. Avoids Globus
    credentials for the demo.

    **Real mining output against local corpus:**
    | source | observed | rejected | unique pairs |
    |---|---:|---:|---:|
    | violin_pathogen | 210 | 7 | 210 |
    | bvbrc_genome | 16,169 | 728 | 11,329 |
    | **total** | **16,379** | **735** | **11,535** |

    SC-B5 rejected 735 observations (4.3% of total) — primarily
    flu-style strain isolates from bvbrc_genome.
  - **Dictionary delta after applying the sidecar:**
    - 11,322 entries gained ≥1 new synonym (97% of in-subtree pairs).
    - 11,322 new synonyms added overall.
    - 5 new SC-A5b ambiguity captures (surfaces that previously
      resolved unambiguously now correctly route to AMBIGUOUS HITL).
    - 114 mined pairs skipped as ``missing_entries`` — VIOLIN's
      bacterial pathogens (Bordetella, Vibrio, Yersinia, etc.) are
      out-of-subtree; this is correct behavior, not a bug.
    - 0 ``mined_conflicts`` rows — no surface mapped to ≥2 taxa
      within the mining run (the 5 ambiguity captures came from
      mining-vs-prior-dictionary conflicts, not mining-vs-mining).
  - **mu-virus-list harmonization delta:**
    | metric | pre-SC-B | post-SC-B |
    |---|---:|---:|
    | fast | 61 (87.1%) | 60 (85.7%) |
    | ambiguous | 5 (7.1%) | 6 (8.6%) |
    | miss | 4 (5.7%) | 4 (5.7%) |

    Three per-term changes:
    * ``adenovirus``: fast (NCBITaxon_10535 "unidentified adenovirus")
      → **AMBIGUOUS** with 2 candidates (10535 + NCBITaxon_10508
      "Adenoviridae"). Mining added "Adenovirus" as a synonym of
      the Adenoviridae family taxon, correctly surfacing the
      family-vs-placeholder ambiguity that was previously silently
      resolved to the placeholder.
    * ``sars-cov``: fast (NCBITaxon_2901879 "Severe acute respiratory
      syndrome coronavirus") → **AMBIGUOUS** with 2 candidates (2901879
      + NCBITaxon_694009 broader species). Same pattern.
    * ``parainfluenza virus``: ambiguous → fast (NCBITaxon_31605
      Human parainfluenza virus 1). Mining tipped the dedup.

    **The "regression" is correct behavior**: the dictionary now
    flags genuine ambiguity instead of silently picking. The
    AMBIGUOUS HITL routing is the SC-A5b contract — see SC-A5b
    implementation log for the underlying rationale.
  - **Brutal-truth limitations confirmed:**
    * ``coronavirus``, ``poxvirus``, ``arbovirus``,
      ``papilloma and polyoma viruses`` still miss. The local
      VIOLIN+BVBRC corpus doesn't contain the bare family-level
      vernaculars. Fixing requires a different corpus (PubMed
      abstracts) or manual curation — NOT a SC-B implementation gap.
    * Ingest perf: ~7 min for 11,539 rows on a 256 MB dict. PRAGMA
      WAL helped but UPDATE-on-large-TEXT-column dominates. Acceptable
      for one-shot; if SC-B becomes a recurring step, batched UPDATE
      rewrites would cut this to ~1 min.
  - **Test scoreboard** (across both repos, no regression):
    * apecx-mcp-integration: **422/422** (179 synonym + 5 CLI + 8
      canonical_entity + 102 SC-E probe + 72 mu-virus-list + 8 mined
      ingest + 48 other unit).
    * apecx-harvesters-work: **1331/1331** + 1 skipped (12 corpus_mining
      + 39 corpus_mining_extractors + 1280 existing).
  - **Still NOT shipped**:
    * SC-B6 — per-source mined-entry report (the existing per-source
      stats in the accumulator + the mining CLI output cover this
      operationally; a polished Markdown report would be SC-B6 v1).
    * Globus-fed mining demo — needs Globus credentials. The CSV-fed
      end-to-end demo provides equivalent semantics.
    * Higher-corpus mining (PubMed / abstracts) — would address the
      coronavirus/poxvirus/arbovirus gaps but is a substantially
      different ingest pipeline. Out of SC-B scope.
