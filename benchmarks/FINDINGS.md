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
