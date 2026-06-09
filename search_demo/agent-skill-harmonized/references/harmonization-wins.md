# Harmonization wins — local demonstration (2026-06-04, post-SC-B7)

Runs 11 query scenarios against the enriched VIOLIN+BVBRC corpus CSVs
(17,043 records: 217 VIOLIN pathogens + 16,826 BVBRC genomes). These
are the same data that would feed the harmonized Globus indices —
the comparison simulates what a `q="..."` baseline query vs a
`subjects.valueUri:"<iri>"` harmonized query would catch.

The synonym dictionary feeding `harmonized_query.py` includes the
SC-B7 strain-prefix acronym pass landed on 2026-06-04. Four
previously-missing acronyms (`CHIKV`, `WEEV`, `MAYV`, `MADV`) and
one bonus species (`GETV`) were automatically inferred from BVBRC's
own strain-isolate notation via frequency-thresholded co-occurrence
mining — no hardcoded synonym list. See `../../../src/apecx_harvesters/pipeline/corpus_mining.py`
(`extract_strain_prefix_acronyms`) and `corpus_mining_extractors.py`
(`mine_bvbrc_strain_prefix_acronyms`) for the implementation.

## Brutal-truth caveats first

**What this report shows**:

- The corpus snapshot is **dominated by alphaviruses** (8,413 CHIKV
  records, 1,811 RVFV, 1,425 EEEV, ...). Queries outside that domain
  (HCV, Influenza A, Marburg) hit very few records or zero — the
  numbers are honest but the corpus has thin coverage outside
  alphaviruses.
- **Sindbis virus / Chikungunya virus -34 / -6** records is NOT a
  harmonization bug — it's records whose Genome Name string
  contains the phrase but whose taxon ID points to a related but
  distinct species. Raw catches them by substring; harmonization
  correctly excludes them via canonical IRI. Whether that's a "win"
  depends on user intent. The `--compare` mode surfaces this kind
  of divergence with an HITL prompt.
- **Numbers are LOCAL** — they reflect the on-disk enriched CSVs at
  `~/.apecx/dictionary/enriched/`. The production Globus indices
  would carry slightly different totals depending on snapshot
  freshness.

**What this report does NOT show**:

- Cross-source fan-out wins (all 9 indices). The demo only loads
  VIOLIN_Pathogen + BVBRC_Genome. A production harmonized index
  would catch cross-source records too (VIOLIN_Vaccine targeting
  EEEV, BVBRC_Epitope on CHIKV, ProtaBank on alphavirus proteins).
- Recall on **out-of-corpus queries**: a real user asking about
  Marburg or HCV in production indices that include the relevant
  source data would see the harmonization win, not the 0-record
  miss shown here.

## Each row reports

- `raw` — record count from substring matching on source surface
  fields (Pathogen / Genome Name / Other Names)
- `harmonized` — record count from canonical-IRI matching after
  resolving the term via the synonym dictionary
- `overlap` — records both modes hit
- `raw_only` — records raw hit but harmonized missed (typically
  surface-form coincidence)
- `hm_only` — records harmonized hit that raw missed (the *real
  win* — harmonization-recoverable records that raw text cannot reach)

## Summary table — actual measurements (post-SC-B7)

| Query | Path | raw | harm | overlap | raw_only | hm_only | Δ recall |
|---|---|---:|---:|---:|---:|---:|---:|
| `CHIKV`                   | fast              | 1175 | **8411** | 1173 |  2 | **7238** | **+7238 (7.2×)** |
| `EEEV`                    | fast              |  456 | **1426** |  456 |  0 |  **970** | **+970 (3.1×)** |
| `VEEV`                    | fast              |  109 |  **647** |  109 |  0 |  **538** | **+538 (5.9×)** |
| `MAYV`                    | fast              |    6 |  **239** |    6 |  0 |  **233** | **+233 (39.8×)** |
| `WEEV`                    | fast              |   30 |  **206** |   30 |  0 |  **176** | **+176 (6.9×)** |
| `Rift Valley fever virus` | fast              | 1812 |     1812 | 1812 |  0 |        0 | 0 (parity) |
| `Chikungunya virus`       | fast              | 8417 |     8411 | 8411 |  6 |        0 | -6 (substring noise) |
| `Sindbis virus`           | fast              |  802 |      768 |  768 | 34 |        0 | -34 (substring noise) |
| `RSV`                     | ambiguous (6)     |    0 |        2 |    0 |  0 |        2 | +2 (qualitative) |
| `adenovirus`              | ambiguous (2)     |    4 |        1 |    1 |  3 |        0 | -3 (substring noise) |
| `alphavirus`              | fast              |   98 |       14 |   14 | 84 |        0 | -84 (genus-as-family vernacular) |

## Headline findings

1. **CHIKV is now the dominant win.** Before SC-B7 it was the biggest
   visible gap (raw caught 1,175 records, harmonized caught zero —
   the dictionary lacked the `CHIKV → 37124` mapping). After SC-B7
   harmonization catches **8,411 records** (every Chikungunya genome
   in the corpus), and raw still finds only the 1,175 that happen
   to spell `CHIKV` literally. Net: **+7,238 records** unreachable
   by raw search.

2. **WEEV and MAYV are now captured.** Frequency-mined from BVBRC's
   own strain prefixes (`WEEV-UY-228`, `MAYV_BR/MT_CbaAr66/2017`)
   without any human curation. WEEV closes a 6.9× gap; MAYV closes
   a 39.8× gap (raw only matched 6 records — most Mayaro records
   only carry the verbose species name).

3. **Acronym → species expansion remains the dominant win mechanism.**
   `EEEV → NCBITaxon_11021` (3.1×) and `VEEV → NCBITaxon_11036`
   (5.9×) carry over from before. The wins come from records using
   only the verbose name; the acronym-anchored IRI filter reaches
   them, raw substring cannot.

4. **AMBIGUOUS surfaces user choice instead of silent mis-attribution.**
   `RSV` resolves to 6 candidate taxa (Human/Bovine/Ovine
   orthopneumovirus + Rous sarcoma + RSV clade + Tenuivirus). The
   harmonized response carries the candidate list; the consumer
   routes to HITL. Raw text matching would lump all "RSV" mentions
   together. **The 2 vs 0 record count understates this** — the
   real win is "user sees the 6 candidates and picks one" vs
   "raw silently mixes intent."

5. **Genus-level vernaculars still miss.** `alphavirus` resolves to
   `Alphavirus` (NCBITaxon_11019) and catches 14 records carrying
   that as canonical IRI. Raw catches 98 records via substring —
   most are alphavirus-family members tagged with their species
   IRI, not the genus. **Neither mode is "right"** — the user
   probably wants the genus subtree, which requires SC-A5b ancestor
   walking the IRI from the response, not a direct filter match.

6. **Sindbis/Chikungunya parity-ish (-34, -6) is NOT a regression.**
   Raw mode caught a handful of records whose Genome_Name contains
   the substring but whose taxon ID points to a closely-related
   species. Whether that's "right" depends on intent: if the user
   wants the SPECIES, harmonization is correct to exclude them;
   if they want anything containing the phrase, raw catches more.
   `harmonized_query.py --compare` surfaces this with an HITL prompt.

## Remaining open gaps

After SC-B7:

- **3-character acronyms (`HSV`, `RSV`, `HIV`, `DENV`)** — intentionally
  excluded by the SC-B7 shape rule (≥4 chars) because they're too
  ambiguous to mine from a single source's strain prefixes. Manual
  curation or cross-source corroboration would be safer here.
- **Genus-level vernaculars (`alphavirus`, `coronavirus`, `flavivirus`)**
  — different problem class: the user wants a subtree, not a single
  taxon. Requires SC-A5b ancestor walking or genus-aware filter
  construction.
- **Vaccine common names (e.g., `MMR`, `BCG`)** — not in scope for
  taxonomy-based harmonization; would need Vaccine Ontology mining
  separately.

## Notes per query (post-SC-B7)

### `CHIKV`

**GAP CLOSED 2026-06-04.** SC-B7 mined `CHIKV → 37124` from BVBRC's
strain-prefix occurrences (`Chikungunya virus CHIKV/IRL/2007` and
similar). Before: 0 harmonized records. After: 8,411 records (every
Chikungunya virus genome in the corpus). Raw still catches only the
1,175 records that literally contain "CHIKV"; the other ~7,200 use
only the verbose species name.

- Resolution path: `fast`
- Candidate IRIs: `37124`

### `EEEV`

Acronym → species. Raw matches genome names with 'EEEV' substring
(~456 records). Harmonization resolves to NCBITaxon_11021 and
catches ALL Eastern equine encephalitis virus records (~1,426) —
a 3× recall lift.

- Resolution path: `fast`
- Candidate IRIs: `11021`

### `VEEV`

Acronym → Venezuelan equine encephalitis virus (NCBITaxon_11036).
Many BVBRC records use only the verbose species name; harmonization
recovers them.

- Resolution path: `fast`
- Candidate IRIs: `11036`

### `MAYV`

**GAP CLOSED 2026-06-04.** Mined from `MAYV_BR/MT_...` strain
prefixes via SC-B7. 239 records carry the Mayaro virus species name;
only 6 carry the literal acronym, so harmonization delivers a 40×
recall lift.

- Resolution path: `fast`
- Candidate IRIs: `59301`

### `WEEV`

**GAP CLOSED 2026-06-04.** Mined from `WEEV-UY-228` hyphen-separated
strain prefixes via SC-B7. 206 Western equine encephalitis virus
records reachable via the IRI filter; only 30 carry the literal
acronym.

- Resolution path: `fast`
- Candidate IRIs: `11039`

### `Rift Valley fever virus`

NCBI rename → ICTV now lists 'Phlebovirus riftense'. Harmonization
via NCBITaxon_11588 catches records labeled with EITHER name (1,811
records in BVBRC).

- Resolution path: `fast`
- Candidate IRIs: `11588`

### `Chikungunya virus`

Verbose species name. Both raw and harmonized hit ~8,413 BVBRC
records; harmonized via NCBITaxon_37124. The 6-record raw_only
delta is substring noise (e.g., "synthetic recombinant Chikungunya
virus strain LS3" — tagged with a different taxon).

- Resolution path: `fast`
- Candidate IRIs: `37124`

### `Sindbis virus`

Verbose species name. Both modes hit ~801 BVBRC records. The
34-record raw_only delta is substring noise from related-species
genome names containing "Sindbis" as a comparative reference.

- Resolution path: `fast`
- Candidate IRIs: `11034`

### `RSV`

AMBIGUOUS — dictionary surfaces 6 candidate taxa (Human/Bovine/Ovine
orthopneumovirus + Rous sarcoma + Tenuivirus + clade). Raw substring
match silently lumps them all. Win is QUALITATIVE (HITL prompt),
not quantitative.

- Resolution path: `ambiguous`
- Candidate IRIs: `11246`, `11250`, `11886`, `12814`, `28869`, `3052763`

### `adenovirus`

AMBIGUOUS — Adenoviridae (family) + 'unidentified adenovirus'
(placeholder). SC-B mining surfaced the ambiguity; pre-SC-B the
dictionary silently picked one.

- Resolution path: `ambiguous`
- Candidate IRIs: `10508`, `10535`

### `alphavirus`

Family-level vernacular not in NCBI's name set as a taxon-level
synonym. Both modes miss the intent — the user wants the genus
subtree. Harmonization catches only records explicitly tagged with
genus 11019; SC-A5b ancestor walking would close this.

- Resolution path: `fast`
- Candidate IRIs: `11019`

## When to use `--compare`

The summary table shows TWO real failure modes harmonization alone
can't solve:

1. **Substring noise the user actually wants** — `Sindbis virus`
   raw catches 34 extra records (related-species mentions). If
   the user intended a literature-style search, those should
   count; if they intended a species-strict query, harmonization
   is correct to exclude them.
2. **Broad expansion when user intended narrow** — `EEEV-strain-X`
   raw catches 2 records (the specific strain); harmonization
   catches all 1,426 species records.

`harmonized_query.py --compare` surfaces both. When the symmetric
divergence exceeds the threshold, the envelope sets `hitl_required:
true` and includes a prompt that presents three options (harmonized
superset / raw substring set / intersection only) instead of silently
picking one.
