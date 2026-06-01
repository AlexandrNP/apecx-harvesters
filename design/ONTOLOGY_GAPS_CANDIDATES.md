# Ontology Gap-Filling Candidates (append-only inventory)

Status: **template / format spec.** Populated by Phase C
(OE-C2..C10) `top_20_unresolved` outputs. Do not edit historical
entries; append new ones with their date + dictionary_version.

## Purpose

When the resolver cannot map a surface form to a canonical IRI, that's
either (a) a real coverage gap in the ontology — candidate for an
upstream submission, (b) a normalization rule we should add, (c) a
data-quality issue in the source, or (d) intentionally out-of-scope.
This file is the inventory. **The output of this inventory drives
Phase H (production gap-filling), which is a separate curation
workstream, NOT in scope for the engineering plan.**

## Format

Per-row schema (markdown table per source):

| Surface form (verbatim) | Source records affected | Suggested action | Notes |
|---|---|---|---|

- **Surface form (verbatim)** — what the source actually contained,
  before normalization. Exact match needed for traceability.
- **Source records affected** — count of records in the source that
  carry this surface form. Frequency matters more than uniqueness
  here: a single high-frequency miss outranks 50 long-tail misses.
- **Suggested action** — enum:
  - `ontology-submission` — file an issue/submission with the upstream
    ontology (NCBI Taxonomy, VO, ChEBI, ...). Surface form is a real
    entity that should be there.
  - `normalization-rule` — add a regex / mapping to
    `RESOLUTION_SURFACE.md`. Surface form IS in the ontology under a
    different spelling we should normalize to.
  - `curation-rule` — manual mapping; surface form is a real entity
    but the rule isn't general enough for normalization.
  - `data-quality` — source has a typo / corruption; reach out to source
    owners or filter.
  - `out-of-scope` — not an entity for this ontology (e.g., synthetic
    constructs in NCBI Taxonomy context).
- **Notes** — anything relevant: alternate name in the ontology,
  source-doc context, prior resolution attempts.

## Provenance header (set per Phase C run)

Each append batch MUST include this header before its rows:

```
### Phase C run: <YYYY-MM-DD>
- dictionary_version: <pinned value, e.g. v1.0.0>
- OLS endpoint: <https://www.ebi.ac.uk/ols4/api/ or self-hosted>
- Normalization applied: <list of rules from RESOLUTION_SURFACE.md applied before resolution>
- Coverage projection report: <path to OE-C* report>
```

Without this header, top-20-unresolved is unfalsifiable downstream — a
later "fixed" entry can't be confirmed against the same dictionary.

## Per-source inventories

Sections initialized empty; populated by OE-C2..C10.

### AntiviralDB (35 records)

_To be populated by OE-C2._

### VIOLIN:Pathogen (217 records)

_To be populated by OE-C3._

### VIOLIN:Vaccine (3,507 records)

_To be populated by OE-C4._

### VIOLIN:Gene (4,063 records)

_To be populated by OE-C5._

### ProtaBank (1,643 records)

_To be populated by OE-C6._

### BVBRC:Epitope (442 records)

_To be populated by OE-C7._

### BVBRC:Protein_Structure (4,566 records)

_To be populated by OE-C8._

### BVBRC:Protein (24,902 records)

_To be populated by OE-C9._

### BVBRC:Genome (745,917 records)

_To be populated by OE-C10. The long tail of organism names will dominate
this section — expect O(1000+) entries. Phase C should produce
top-50-unresolved here, not top-20, given the corpus size._

## Phase H pointer

The act of triaging these rows (deciding action, drafting submissions,
adding normalization rules) is Phase H. Tasks for it go in a
`PHASE_H_GAP_FILLING.md` (NOT YET WRITTEN — author when the first
Phase C report lands and the actual volume is known).
