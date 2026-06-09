---
name: apecx-discovery-harmonized
description: Query the harmonized APECx biomedical search indices (VIOLIN, BVBRC, ProtaBank, AntiviralDB — pathogens, genomes, vaccines, epitopes, proteins). Records carry NCBI Taxonomy canonical IRIs as DataCite subjects (subjects.valueUri) so a query for "EEEV" reaches every "Eastern equine encephalitis virus" record via the same taxon. Use when a user asks about a virus / pathogen / vaccine / epitope and you want HIGH RECALL across the 9 APECx source indices.
compatibility: Until the harmonization PR is merged upstream, install from the fork — `pip install 'apecx-harvesters[reader] @ git+https://github.com/AlexandrNP/apecx-harvesters.git@main'` (thin reader; pydantic + stdlib only — no nanobrain/LLM/MCP deps). The install gives you the `apecx-lookup` and `apecx-dict-update` CLIs. First run: `APECX_DICT_PUBLIC_BASE_URL="https://g-958ce2.fd635.8443.data.globus.org/apecx-ramanathan-anl/public/synonyms_dictionary" apecx-dict-update` to bootstrap the dictionary from the published Globus path (`/apecx-ramanathan-anl/public/synonyms_dictionary/` on the APECx Data at Argonne LCF collection). Subsequent runs use the local `~/.apecx/dictionary/dictionary.sqlite`. Also requires `jq` and `globus-sdk>=4.0` for the Globus Search query path.
allowed-tools: Bash(uv *) Bash(jq *) Bash(apecx-lookup *) Bash(python *)
---

## Purpose

This skill provides access to nine harmonized biomedical APECx source indices:

| Source | Globus index | Content |
|---|---|---|
| VIOLIN:Pathogen | `a67c7310-5115-446f-bfb6-d889bc4efa06` | Vaccine-relevant pathogens with NCBI taxonomy |
| VIOLIN:Vaccine | `c5ff64fd-5e78-4cf0-848a-2788a78e71cd` | Vaccine records + targeted pathogen IDs |
| VIOLIN:Gene | `205c1a5b-c9bd-4137-8ac6-ca879c9a4f9c` | Gene records with NCBI Gene IDs |
| BVBRC:Genome | `b676edbe-3286-4514-bc13-5cbe891c4bb1` | Viral / bacterial genome assemblies |
| BVBRC:Protein | `249efe96-14d2-443d-ad47-5621ed43a343` | Protein annotations |
| BVBRC:Protein_Structure | `439f2b66-09d4-4141-8c3d-b4dc18ef8a07` | Protein structure entries (PDB / UniProtKB linked) |
| BVBRC:Epitope | `f873c7d5-8652-466d-806b-b5da46f0f786` | B/T-cell epitope assays |
| AntiviralDB | `e8097a7b-a280-4031-9df1-1e837193494f` | Antiviral compounds + virus targets |
| ProtaBank | `9e902471-9c77-49d3-a12c-516cc0808c3b` | Protein engineering data |

Records share the **DataCite 4.7** core schema with source-specific extensions
(`violin_pathogen`, `bvbrc_genome`, etc.). **The harmonization layer** adds
NCBI Taxonomy canonical IRIs to every taxonomy-bearing record via the
`subjects` field with `subjectScheme = "NCBI Taxonomy"`, so a query for
"EEEV" can reach every record that resolves to `NCBITaxon_11021` regardless
of how the source spelled the surface form.

## Why the harmonized layer matters

**Brutal-truth note on the harmonization layer**: the production Globus
indices today do NOT carry the planned `subjects[].valueUri` DataCite
field — that's the SC-D ingest target that hasn't shipped yet. Until it
does, `harmonized_query.py` routes via per-index source-specific fields
(`Species` for BVBRC_genome, `Organism` for BVBRC_epitope, `Pathogen`
for VIOLIN, etc.) and filters by the dictionary's canonical_label PLUS
the full synonyms list. See `HARMONIZED_FILTER` in
`scripts/harmonized_query.py` for the per-index map; when SC-D ingest
ships, every entry's `shape` field can be flipped to `"iri"` and the
harmonization becomes uniformly IRI-anchored.

Real measurements on the **live APECx Globus indices** (verified
2026-06-04, anonymous read against the public indices) — these are
end-to-end wins, not local-demo extrapolations:

| Query | raw `q=` | harmonized (label) | Δ |
|---|---:|---:|---:|
| `CHIKV` | 1,162 records | **6,684** | **+5,522 (5.7×)** |
| `MAYV`  |     6 records |   **186** | **+180 (31×)**  |
| `WEEV`  |    16 records |   **132** | **+116 (8.3×)** |
| `EEEV`  |   457 records |   **895** | **+438 (2.0×)** |
| `RSV`   |   658 records | 362 records (AMBIGUOUS over 6 candidates) | qualitative HITL win |

Local corpus measurements (16,826 BVBRC genomes + 210 VIOLIN pathogens)
in `references/harmonization-wins.md` carry slightly different totals
because the local enriched CSV is a subset snapshot — the live Globus
index has more strains.

| Query | raw substring | harmonized (IRI) | Δ |
|---|---:|---:|---:|
| `CHIKV` | 1,175 records | **8,411** records | **+7,236 (8.4×)** |
| `EEEV`  |   456 records | **1,426** records | **+970 (3.1×)** |
| `VEEV`  |   109 records |   **647** records | **+538 (5.9×)** |
| `WEEV`  |    30 records |   **206** records | **+176 (6.9×)** |
| `MAYV`  |     6 records |   **239** records | **+233 (39.8×)** |
| `RSV`   |     0 records |     2 records *+ AMBIGUOUS prompt* | qualitative win |

The wins come from four mechanisms:

1. **Acronym → species expansion** — `EEEV` → `NCBITaxon_11021` catches
   every record using the verbose species name.
2. **NCBI taxon rename bridging** — when NCBI moves a species name
   (`Hepatitis C virus` → `Orthohepacivirus hominis`), the
   merged-taxon table walks the rename so old-named records still hit.
3. **Ambiguity surfacing** — surfaces like `RSV` resolve to ≥2 candidate
   taxa; the harmonized response includes a `candidates` list so the
   caller routes to HITL instead of silently mis-attributing.
4. **SC-B7 strain-prefix acronym mining (2026-06-04)** — automatic
   inference of acronyms from BVBRC's strain-isolate notation
   (`Chikungunya virus CHIKV/IRL/2007`, `WEEV-UY-228`,
   `MAYV_BR/MT_CbaAr66/2017`) via frequency-thresholded co-occurrence.
   No hardcoded synonym list — the acronym must fire for the SAME
   species across ≥10 records AND ≥5% of that species' records before
   it's accepted. Closed four previously-missing gaps (`CHIKV`, `WEEV`,
   `MAYV`, `MADV`); bonus species (`GETV`, `MADV`) surfaced as side
   effects.

## References

Read these before constructing a query:

- `references/query-api.md` — Globus Search JSON query construction
- `references/schema.json` — fields available in the harmonized index
  records (core DataCite + extensions + harmonization fields)
- `references/harmonization-wins.md` — concrete before/after examples
  with brutal-truth caveats

## Available over MCP

The canonical MCP surface is **`apecx-mcp`** (from
`apecx-mcp-integration`), which already exposes a workflow-as-object
surface per the §4/§5 design — `WorkflowResult` envelope, `HandleStore`,
`run_workflow_observed`, recursive workflow inspection, decomposition
(KeywordWorkflowMatcher / RunWorkflowDispatcher / LLMTaskDecomposer),
and the scientist-facing `start_workflow / show_diff /
execute_workflow / list_workflows / describe_workflow` tools.

Relevant existing tools that cover this skill's lookup needs:

| Skill capability | Existing apecx-mcp tool |
|---|---|
| Resolve term → canonical IRI | `resolve_canonical_entity(name, entity_type?)` |
| Free-text Globus search | `query_globus_search(query, max_results, offset)` |
| List composed workflows | `list_workflows()` |
| Inspect a workflow | `describe_workflow(name)` |
| Compose + run a workflow | `start_workflow / show_diff / execute_workflow` |
| Approval/HITL gates | `list_pending_approvals / approve / reject / correct` |

**Note 2026-06-09**: the standalone `apecx-mcp-reader` shipped under
`apecx_harvesters.mcp_surface` was retired because it duplicated
`resolve_canonical_entity` + `query_globus_search` from the canonical
apecx-mcp surface. The harmonization-search domain logic (per-index
filter map, raw-vs-harmonized divergence, HITL envelope from
ambiguous resolution) is currently in
`scripts/harmonized_query.py` and is the next thing to land as a
proper workflow YAML / MCP tool in apecx-mcp-integration.

## Available scripts

- `scripts/fetch.py` — run a query and emit JSONL (one record per line).
  Same shape as the base skill's fetch.py; the differences are in the
  schema and what filters land hits.

- `scripts/harmonized_query.py` — **the canonical entry point.** Takes
  a user term (`"EEEV"`, `"Hepatitis C"`, `"Marburg virus"`) and:
  1. Resolves it via the synonym dictionary (`lookup_entity`)
  2. Constructs a Globus Search query targeting `subjects.valueUri:"<iri>"`
     for the resolved IRI (or all IRIs when AMBIGUOUS)
  3. Optionally fans out across multiple source indices for cross-source
     discovery
  4. Returns JSONL with the resolution metadata preserved alongside
     each record

  Run `python scripts/harmonized_query.py --help` for the full interface.

## Workflow

### Single-source query, harmonized

```bash
python scripts/harmonized_query.py \
    --index b676edbe-3286-4514-bc13-5cbe891c4bb1 \
    --term "EEEV" \
    --limit 200 \
  | jq -s 'group_by(.canonical_iri // .subjects[]?.valueUri) | map({iri: .[0].canonical_iri, count: length})'
```

### Cross-source fan-out (the seamless discovery use case)

```bash
# Find every record across 9 indices anchored to "Rift Valley fever virus"
python scripts/harmonized_query.py \
    --term "Rift Valley fever virus" \
    --all-indices \
    --limit 200 \
  | jq -s 'group_by(.publisher.name) | map({source: .[0].publisher.name, count: length})'
```

### Manual raw-mode query for comparison

```bash
# How many records does raw substring matching catch?
echo '{"q": "EEEV", "limit": 1000}' \
  | uv run scripts/fetch.py b676edbe-3286-4514-bc13-5cbe891c4bb1 \
  | jq -s 'length'
```

### Handling AMBIGUOUS resolution

If `harmonized_query.py` reports `path=ambiguous`, surface the candidate
list to the user instead of silently picking. Example:

```bash
python scripts/harmonized_query.py --term "RSV" --resolve-only
# {
#   "resolution_path": "ambiguous",
#   "candidates": [
#     {"canonical_iri": ".../NCBITaxon_11246", "label": "Bovine orthopneumovirus", ...},
#     {"canonical_iri": ".../NCBITaxon_11250", "label": "Human orthopneumovirus", ...},
#     ...
#   ]
# }
```

### `--compare` mode: raw vs harmonized divergence + HITL

Harmonization correctly expands `EEEV` from 456 → 1,426 records — that's
a recall win when the user wants all EEEV-related data. But when the
user types something **specific** (a particular strain, a verbose name
with isolate detail), the species-level expansion is the *opposite* of
what they want.

`--compare` runs BOTH the raw substring query (`q=<term>`) and the
harmonized IRI-filter query, then emits a single JSON envelope with:

* `per_index[<short>].overlap_records` — records both modes hit
* `per_index[<short>].raw_only_records` — substring matches harmonization missed
* `per_index[<short>].harmonized_only_records` — IRI matches raw missed
* `per_index[<short>].divergence_fraction` — symmetric divergence
* `hitl_required` — true when divergence ≥ thresholds OR resolution is `ambiguous`
* `hitl_prompt` — a string the caller can show the user

```bash
python scripts/harmonized_query.py \
    --term "EEEV-strain-X" --index bvbrc_genome \
    --compare --limit 500
# {
#   "term": "EEEV-strain-X",
#   "resolution": {"resolution_path": "fast", "canonical_iri": ".../NCBITaxon_11021", ...},
#   "per_index": {
#     "bvbrc_genome": {
#       "raw_total": 2,
#       "harmonized_total": 1426,
#       "overlap_records": 2,
#       "raw_only_records": 0,
#       "harmonized_only_records": 1424,
#       "divergence_fraction": 0.998
#     }
#   },
#   "hitl_required": true,
#   "hitl_prompt": "Search term 'EEEV-strain-X' returns DIFFERENT results
#                  under raw substring vs harmonized IRI-filter modes ...
#                  Choose: (a) the harmonized superset ..., (b) the raw
#                  substring set ..., or (c) intersection only ..."
# }
```

The HITL prompt presents the three legitimate options instead of silently
picking one. Tune `--divergence-records` (default 5) and
`--divergence-fraction` (default 0.05) to control sensitivity.

## Gotchas

- **`subjects.valueUri` is the harmonized filter target**, not the
  free-text `subject` field. Construct filters as
  `{"type": "match_any", "field_name": "subjects.valueUri", "values": [iri]}`.

- **Source-specific surface fields** still exist and still match raw
  text: `violin_pathogen.Pathogen`, `bvbrc_genome.Genome_Name`,
  `bvbrc_epitope.Organism`, etc. Use these when you need source-format
  records (e.g., the exact strain name).

- **AMBIGUOUS is a feature, not a failure.** When `harmonized_query.py`
  reports ambiguity, present the candidates rather than picking the
  first one. Silent picking is exactly the failure SC-A5b ships to
  prevent.

- **Resolution can still miss some acronyms.** SC-B7 (2026-06-04) closed
  the major BVBRC strain-prefix gaps (`CHIKV`, `WEEV`, `MAYV`, `MADV`
  now all `fast`). SC-B8 (same date) added VIOLIN parenthetical mining:
  `HSV-1`, `HSV-2`, `HHV-1`, `TBEV`, `CCHF`, `RVF`, `BVDV`, `RHDV`,
  `ASFV`, `WNV`, `VZV`, `BLV`, `FIV`, `FCV`, `EAV` etc. all `fast` now,
  with multi-species cases (HEV/HIV/BVDV) correctly routing to
  AMBIGUOUS. Three-character acronyms (`HSV`, `DENV`) are intentionally
  not mined — too noisy. For uncaught cases, `--compare` will surface
  high `raw_only` counts so you can see the gap; report them for the
  next mining pass. See `references/harmonization-wins.md` for the full
  inventory.

- **Label-bridge gap on ICTV renames.** When NCBI renames a species
  (e.g., NCBI 11084 `Tick-borne encephalitis virus` is referenced by
  BVBRC as the ICTV-modern `Orthoflavivirus encephalitidis`), the
  dict's TBEV entry doesn't carry the modern label in its synonyms
  list. The harmonized query (filtering on canonical_label + synonyms)
  misses the renamed records. `--compare` correctly surfaces this with
  `harm=0 raw>0` and `hitl_required=true`. Fixing it requires either
  walking `merged_taxons` at query time OR rebuilding the dict with
  the modern NCBI names.dmp + ICTV cross-reference. Tracked as an
  apecx-mcp-integration dict-build follow-up.

- **Fine-grained queries get HITL.** When a user types a SPECIFIC
  strain name (e.g., `EEEV-strain-X1`) and harmonization expands to
  the whole species, the user's intent might be the narrow set, not
  the broad one. `--compare` mode catches this case and emits an
  HITL prompt presenting three options (broader / narrower /
  intersection) instead of silently picking. See the `--compare`
  section above.

- **The dictionary path matters.** `apecx-lookup` requires
  `APECX_SYNONYM_DICT_PATH` to point at a built dictionary SQLite, or
  falls back to `~/.apecx/dictionary/dictionary.sqlite`. Missing
  dictionary → harmonization disabled → script degrades to raw mode.

- **Field names match `schema.json` exactly** — dot-notation for
  nested fields, source-specific names as listed in the per-source
  schema sections.
