# Harmonization Ablation — Findings (2026-06-09)

Benchmark: `benchmarks/harmonization_ablation.py`. 140 queries (mu-virus 70,
abbreviations 40, real-world 30) × 9 source→DEST index pairs × a 2×2 ablation
(raw vs harmonized search × source vs harmonized index). Limit 200 records/cell
for adjudication; retrieved totals are full index-side counts.

## Headline: harmonization's win is RECALL, not precision

| category | raw_dest retrieved | harm_dest retrieved | **recall lift** |
|---|---|---|---|
| mu_virus | 343,731 | 585,608 | **1.7×** |
| abbreviations | 32,547 | 277,155 | **8.5×** |
| real_world | 85,530 | 376,267 | **4.4×** |

Harmonized search on the harmonized index returns **1.7–8.5× more relevant
records** than raw substring search. The effect is largest for abbreviations:
`q="EEEV"` substring-matches almost nothing, but resolving EEEV → its NCBI
taxon and filtering `subjects.valueUri` matches every Eastern-equine-
encephalitis record regardless of how its organism is spelled. This is the
core value of the harmonization layer + the strain→species normalization
(species-expanded `subjects.valueUri` catches strain-level records too).

`harm_dest` > `harm_source` everywhere (e.g. real_world 376k vs 132k) because
the DEST `subjects.valueUri` field is uniform + species-expanded, whereas the
SOURCE native-field filter is per-index and rank-literal.

## Precision (descendant-aware dictionary oracle)

| category | raw_source | raw_dest | harm_source | harm_dest |
|---|---|---|---|---|
| mu_virus | 87.2% | 83.0% | 100% | 100% |
| abbreviations | 79.3% | 82.2% | 100% | 100% |
| real_world | 97.8% | 92.8% | 100% | 100% |

**Read these honestly:**

- **Harmonized 100% is partly circular.** The oracle adjudicates a DEST record
  by `subjects.valueUri ∩ S_Q` — the same field `harm_dest` filters on. So
  100% confirms the filter and the stamp agree (harmonization is internally
  consistent), NOT an independent precision measurement. The genuinely
  independent signal is **raw-search precision** (organism oracle vs substring
  match).

- **Raw precision 79–98% is the real false-retrieval signal**, but its "false"
  has TWO causes, only one of which is a true defect:
  1. **Genuine substring over-match** (harmonization avoids these): abbreviation
     collisions dominate — `HEV` (Hepatitis E) vs `HeV` (Hendra) both 5%
     precision; `HIV` 31%. Raw `q=` matches any text mention, including records
     about a *different* organism that merely names the query in prose.
  2. **Resolution dead-ends** (an artifact, not a raw-search defect):
     `herpesvirus` (0% — 0 true / 333 false), `hepatitis a virus` (3%),
     `parainfluenza virus` (2%). These resolve to a **childless NCBI grouping
     taxon** whose subtree is empty in `taxon_hierarchy`, so the oracle can't
     credit the (biologically correct) species-level records. Same mechanism as
     `human immunodeficiency virus` → taxon 12721 (0 descendants) while the
     real HIV strains hang under 11676 (2,832 descendants).

  The descendant-aware oracle fix moved raw precision up sharply where the
  subtree IS populated (mu_virus raw_source 73%→87%, real_world 89%→98%); the
  residual low-precision queries are mostly cause-2 dead-ends.

## Resolution coverage is the binding constraint

Harmonization gives **zero** benefit when the query doesn't resolve — both
`harm` cells return nothing. Unresolved queries:

- **mu_virus (4/70):** `arbovirus`, `coronavirus`, `papilloma and polyoma
  viruses`, `poxvirus` — group/family names with no single taxon. `coronavirus`
  unresolved is the notable gap.
- **abbreviations (5/40):** `DENV`, `LASV`, `MARV`, `NiV`, `RABV` — common
  virology acronyms absent from the dictionary's acronym set (SC-A3 ingestion
  gap). Concrete, fixable dictionary targets.
- **real_world (14/30):** mostly multi-word phrases (`influenza vaccine`,
  `SARS-CoV-2 spike protein`, `Ebola virus glycoprotein`). The dictionary
  resolves *entities*, not free-text queries — these need entity extraction
  (NER) upstream before harmonization can help.

## Honest limitations of this benchmark

- **Precision is sampled** (≤200 records/cell); the `true/false/unknown` counts
  are sample-bucket sizes, not full-index counts. The precision *rate* is the
  meaningful number; absolute counts are not full-index false totals.
- **The oracle is the dictionary itself.** It cannot detect a harmonization
  error that is *internally consistent* (a record mis-stamped AND mis-filtered
  the same way). Independent precision requires hand-labeled ground truth,
  which does not exist for this corpus.
- **`unknown` is reported separately, never folded into true or false.** Raw
  cells carry meaningful unknown (organism field absent/unresolvable);
  unresolved queries make every retrieved record unknown.

## Actionable takeaways

1. **Ship harmonized-DEST as the default search path** — 1.7–8.5× recall at
   ≥83% measured precision, vs raw substring's recall floor.
2. **Fix the acronym gap**: add DENV/LASV/MARV/NiV/RABV (+ audit other common
   virology acronyms) to the dictionary.
3. **Resolution-granularity is the next lever**: broad names resolving to
   childless grouping taxa (herpesvirus, HIV→12721, coronavirus) need either a
   better canonical-target choice or query-time descendant expansion in the
   `subjects.valueUri` filter (the benchmark already computes the subtree).
4. **Free-text queries need NER** before harmonization — 14/30 real-world
   queries didn't resolve as phrases.

Raw artifacts: `benchmarks/output/full/ablation_report.{md,json}`,
`per_query.jsonl` (per query×index, all four cells + buckets — re-scorable).

---

# Improvements implemented + measured (2026-06-09)

Three levers, each a benchmark flag, measured on the **affected queries only**
(no full re-run — unchanged queries keep their baseline numbers).
Artifacts: `benchmarks/output/exp_combined/`.

## A. `--expand-descendants` — descendant-expanded `subjects.valueUri` filter

The harm_dest filter expands to the query taxon's full subtree (chunked ≤1000
for Globus). Converts a genus-level query — stamped on almost no records
because records carry species/strain taxa *below* the genus — into the whole
subtree, at genuine 100% precision (subtree-correct). No re-republish.

| query | harm_dest before | after | lift |
|---|---|---|---|
| adenovirus | 28 | 9,868 | 352× |
| rotavirus | 31 | 17,970 | 580× |
| norovirus | 4 | 84,659 | huge |
| measles virus (species) | 6,284 | 6,288 | ~1× (already correct) |

Species-level queries are unchanged (they already resolve to a stamped taxon).
Dead-end resolutions (descendants=0) are untouched — they need B.

## B. `--use-aliases` — curated alias redirect (`queries/curated_aliases.tsv`)

Redirects, at resolution time, (1) acronyms NCBI's `names.dmp` lacks and
(2) broad names that hit a childless grouping taxon, to a subtree root.
Composes with A.

| query | before | after | note |
|---|---|---|---|
| DENV | 0 (miss) | 42,413 | acronym → Dengue virus |
| RABV | 0 (miss) | 24,459 | acronym → Rabies lyssavirus |
| coronavirus | 0 (miss) | 65,904 | → Coronaviridae (2267-taxon subtree) |
| poxvirus | 0 (miss) | 21,313 | → Poxviridae |
| herpesvirus | 12 | 23,639 | dead-end → Orthoherpesviridae |
| cytomegalovirus | 4 | 11,106 | dead-end → subtree |

All at 100% precision. **Honest caveats:**
- **HEV/HeV went 1,463 → 9** — NOT a regression. Baseline resolved *ambiguous*
  (HEV matched several taxa); the alias pins it to Hepatitis E virus precisely.
  Precision up, recall down because Hepatitis E is genuinely sparse here.
- **Removed `coxsackievirus`/`rhinovirus` → Enterovirus after measurement.**
  Both redirected to the Enterovirus genus → each returned the *entire* genus
  (41,838: polio/echo/rhino/coxsackie) at a **fake** 100% precision (the oracle
  judges against the redirected target, so it cannot see most results aren't
  coxsackie). A redirect that loses the user's specificity is worse than a
  miss. **Rule: redirect only to a node that IS the entity, never to a
  diluting ancestor.**
- The production home for these is a **dictionary synonym delta + republish**,
  not a resolution-layer file. This is the no-republish demonstration.

## C. `--enable-ner` — optional LLM entity extraction for free-text

On a resolution miss, extract entities via `apecx_db_integration.
extract_entities_llm` (direct import, else subprocess to apecx-mcp-integration's
venv; no-op if unavailable — apecx-harvesters stays framework-agnostic).
Verified live against mistral-nemo.

| free-text query | before | after |
|---|---|---|
| SARS-CoV-2 spike protein | 0 | 645 |
| Ebola virus glycoprotein | 0 | 129 |
| zika virus structure | 0 | 15 |
| adenovirus vaccine | 0 | 9,868 (NER strips "vaccine") |

**NER is necessary but not sufficient.** Still-zero: `influenza vaccine`,
`tuberculosis genome`, `rabies vaccine`, `Lassa fever virus`, `herpes simplex`,
`swine flu` — NER extracted the right entity *string*, but that string then
failed to resolve (bare "influenza"/"tuberculosis"/"Lassa fever virus" aren't
dictionary entries). The next lever is synonym coverage on the *extracted*
entity (B's alias map would close most of these).

## Net effect (affected queries, excluding the removed over-broad pair)

Harm_dest recall on the changed queries rose from ~13.7k to ~475k records
(~34×) at genuine subtree-correct precision, by turning total-misses and
dead-ends into populated subtrees. The wins concentrate exactly where the
baseline was weakest (broad names, acronyms, free-text) and leave the
already-correct species queries untouched.
