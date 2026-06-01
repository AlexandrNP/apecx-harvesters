# Resolution Surface — per-source entity → ontology mapping

Status: **DRAFT — NEEDS RATIFICATION (OE-B11).** This is my initial read
from the parsers and source schemas; it has NOT been reviewed against
domain expectations. Do not ship Phase E or Phase F against this file
until OE-B11 lands.

## What this file is

The decision artifact for **which fields on each harmonized DataCite
record feed which ontology resolver**, per source. Without this, Phase C
coverage measurement is undefined and Phase F adapter wiring is
under-specified. The `harvester_adapter.adapt_to_harvester_transform`
expects exactly this kind of mapping at its `extension_field` arg.

## Shape decision (OPEN — see ONTOLOGY_ENRICHMENT_PLAN.md "Open decisions")

Per-record `canonical:` field is either:

### Option A — flat single-slot (simpler)

```
canonical: {
  iri: str | None,
  label: str | None,
  ontology: str | None,
  status: ResolutionStatus | None,
  confidence: float,
  dictionary_version: str | None,
}
```

Works for single-entity records (BVBRC:Genome → one organism).
**Breaks for multi-entity records**: AntiviralDB (Virus + Drug),
VIOLIN:Vaccine (Pathogen + Vaccine), VIOLIN:Gene (Pathogen + Gene).

### Option B — role-keyed nested (more honest)

```
canonical: {
  pathogen: { iri, label, ontology, status, confidence } | None,
  vaccine:  { iri, label, ontology, status, confidence } | None,
  gene:     { iri, label, ontology, status, confidence } | None,
  drugs:    [ { iri, label, ontology, status, confidence } ],
  proteins: [ { iri, label, ontology, status, confidence } ],
  dictionary_version: str | None,
}
```

Role keys are the slot names in the tables below. List for slots that
are 1:N per record (e.g., drugs in AntiviralDB).

**Recommendation: Option B.** Three sources (AntiviralDB, VIOLIN:Vaccine,
VIOLIN:Gene) need multiple roles; designing for the simpler case forces
rework. The adapter contract (`RESOLUTION_OUTPUT_KEYS`) needs a thin
wrapper to write into role-keyed slots — small, mechanical change.

## Per-source mapping (DRAFT)

Columns:
- **Slot** — role name in `canonical:` extension.
- **Source field on harmonized record** — where the surface form lives.
  Paths use dot notation into the container's nested structure.
- **Ontology** — target ontology / cross-ref source.
- **Match mode** — expected dominant outcome.
- **Normalization** — pre-resolution string transformation; "none" means
  trim + case-fold only.
- **Already canonical?** — true if the source itself carries an ontology
  ID we can short-circuit to (e.g., VIOLIN's `NCBI_Taxonomy_ID`).

### AntiviralDB (35 records, dest `23a7bffd-…`)

| Slot | Field | Ontology | Match mode | Normalization | Already canonical? |
|---|---|---|---|---|---|
| pathogen | `Virus` | NCBI Taxonomy | exact-or-synonym | strip "virus" suffix variants; case-fold | NO |
| drugs[] | `Protein_and_Drug[].Drug[].Drug_Name` | ChEBI (fallback: PubChem cross-ref) | likely synonym-dominant | trim; case-fold | NO |
| proteins[] | `Protein_and_Drug[].Protein_Name` | UniProt cross-ref OR PRO | cross-ref preferred | trim | source has no canonical ID |

**Notes:**
- AntiviralDB carries no NCBI_Taxonomy_ID; pathogen resolution is full-OLS.
- "Influenza Virus" vs. "Influenza virus" case collision was already
  fixed at canonical_uri layer in Phase 0; the pathogen resolver
  must canonicalize these to the same IRI (`NCBITaxon:11320`) or
  surface as a bug.
- Drug ontology choice is OPEN — ChEBI is the OLS-supported drug
  ontology, but apecx-mcp-integration's resolver scope was originally
  pathogen-first. Confirm before Phase 0.

### VIOLIN:Pathogen (217 records, dest `b4965a61-…`)

| Slot | Field | Ontology | Match mode | Normalization | Already canonical? |
|---|---|---|---|---|---|
| pathogen | `Pathogen` (surface) + `NCBI_Taxonomy_ID` (cross-ref) | NCBI Taxonomy | cross-ref via existing ID | none if ID present | **YES — has NCBI_Taxonomy_ID** |

**Notes:**
- Highest expected coverage: ~100% via cross-ref short-circuit.
- For records missing `NCBI_Taxonomy_ID`, fall back to OLS resolution
  of `Pathogen`. Phase C must report the missing-ID rate per source.

### VIOLIN:Vaccine (3,507 records, dest `12dfce07-…`)

| Slot | Field | Ontology | Match mode | Normalization | Already canonical? |
|---|---|---|---|---|---|
| pathogen | `Pathogen` (or container.violin_vaccine.pathogen) | NCBI Taxonomy | cross-ref preferred | none if pathogen ID lifted | sometimes |
| vaccine | `Vaccine_Name` | Vaccine Ontology (VO) | exact-or-synonym | trim; strip vendor suffixes | NO |
| (categorical) `Type` | small enum | VO categorical (or skip) | enum lookup | none | hand-curatable |

**Notes:**
- `Vaccine_Name` had 47/3507 nulls per Phase-5 corpus validation; the
  parser title fallback applies. Slot should be Optional.
- `Type` is small (handful of categories); hand-curate the mapping in
  `RESOLUTION_SURFACE.md` rather than calling OLS per record.

### VIOLIN:Gene (4,063 records, dest `667dc223-…`)

| Slot | Field | Ontology | Match mode | Normalization | Already canonical? |
|---|---|---|---|---|---|
| pathogen | container's pathogen ref | NCBI Taxonomy | cross-ref via lifted ID | none | likely |
| gene | `Gene_Name` | NCBI Gene cross-ref (organism-scoped) | cross-ref | symbol normalization | sometimes via lifted IDs |
| (skip) other VO references | `VO_ID` | VO | already canonical | none | YES |

**Notes:**
- Gene names are organism-scoped (the same `RpoB` exists in many
  pathogens); resolution requires pathogen IRI + gene symbol.
  Cross-product lookup, not single surface form. **This is the most
  complex resolution surface across the 9 sources; consider deferring
  to Phase G2.**
- Records already carrying `VO_ID` should NOT be re-resolved; pass through.

### ProtaBank (1,643 records, dest `be999b57-…`)

| Slot | Field | Ontology | Match mode | Normalization | Already canonical? |
|---|---|---|---|---|---|
| proteins[] | `Protein[].UniProt` (lifted accession) | UniProt cross-ref | cross-ref | none | YES — accession present |
| (skip) PDB_ID, ProtaBank_ID | (already in alternateIdentifiers) | — | — | — | YES |

**Notes:**
- ProtaBank pathogen slot intentionally null — records describe
  proteins, not pathogens.
- Resolution is pure cross-ref; OLS call rate is zero. Cheap.
- Counter-argument: cross-ref-only resolution might not be worth a
  dedicated republish if the accession is already accessible in
  `alternateIdentifiers`. Phase C should report what % of records
  gain anything from this slot vs. what's already in the existing
  alternateIdentifiers.

### BVBRC:Epitope (442 records, dest `4c0b4e3d-…`)

| Slot | Field | Ontology | Match mode | Normalization | Already canonical? |
|---|---|---|---|---|---|
| pathogen | `Organism` (== source subject) | NCBI Taxonomy | exact-or-synonym | sharding-suffix strip | partially via cross-ref |

**Notes:**
- `Protein_and_Epitope[].Epitope[].Type` is a small enum (IEDB types) —
  hand-curate, do not call OLS.
- Sequences are not ontology entities; skip.

### BVBRC:Protein_Structure (4,566 records, dest `96fbabbb-…`)

| Slot | Field | Ontology | Match mode | Normalization | Already canonical? |
|---|---|---|---|---|---|
| pathogen | `Organism` | NCBI Taxonomy | exact-or-synonym | sharding-suffix strip | partially |
| proteins[] | structure publication `UniProtKB` accessions (lifted) | UniProt cross-ref | cross-ref | none | YES |
| (skip) PDB_ID | (already in alternateIdentifiers) | — | — | — | YES |

### BVBRC:Protein (24,902 records, dest `826e5d28-…`)

| Slot | Field | Ontology | Match mode | Normalization | Already canonical? |
|---|---|---|---|---|---|
| pathogen | `Organism` (== source subject) | NCBI Taxonomy | exact-or-synonym | sharding-suffix strip | partially |

**Notes:**
- Protein features per record are heterogeneous property-bags
  (GenBank accessions, protein product strings); not entity-resolvable
  in a normalized way. Skip.

### BVBRC:Genome (745,917 records, dest `dfefcd85-…`)

| Slot | Field | Ontology | Match mode | Normalization | Already canonical? |
|---|---|---|---|---|---|
| pathogen | `Organism` (== source subject; e.g., "Hepacivirus C (7)") | NCBI Taxonomy | exact-or-synonym (after normalization) | **REQUIRED: strip BV-BRC shard suffix " (N)"** | partially |

**Notes:**
- Documented Phase-5 sharding: "Hepacivirus C" through "Hepacivirus C
  (13)" are 14 distinct subjects in the source index but resolve to the
  same NCBI taxon (`NCBITaxon:11103`). The shard-suffix strip is
  **load-bearing** for any meaningful coverage projection; otherwise
  14× the distinct surface forms with no resolution value.
- This is the source where the sample-vs-corpus discipline must apply
  (Phase C: full distinct surface_set, not sample). Genome's long tail
  is where NCBI Taxonomy coverage drops.

## Normalization rules summary (consolidated)

Move to a single helper in Phase E. Per-source rules above; consolidated
here for the implementer:

1. **Trim + case-fold.** Every surface form.
2. **Strip BV-BRC shard suffix.** Regex `\s+\(\d+\)$` → `""`.
   Applies to: BVBRC:Epitope, BVBRC:Protein_Structure, BVBRC:Protein,
   BVBRC:Genome (the `Organism` field).
3. **Strip "virus" suffix variants.** Optional / OPEN. AntiviralDB
   records use "Influenza Virus" but NCBI Taxonomy canonical is
   "Influenza A virus" — there's a real linguistic gap. **Decide
   per ontology, not per source.** Punt until OE-C2 (AntiviralDB
   coverage projection) gives us data on the failure mode.
4. **Vendor-suffix strip on vaccine names.** OPEN. Vaccine ontologies
   typically use generic name; commercial names have vendor variants.
   Add the rule only if OE-C4 surfaces it as a failure mode.

## Ratification checklist (OE-B11 gate)

- [ ] Shape decision: Option A (flat) or Option B (role-keyed).
- [ ] Ontology scope: NCBI-Taxonomy-only Phase 0, or wider.
- [ ] Per-source normalization rules locked or punted to Phase C data.
- [ ] VIOLIN:Gene slot deferred to Phase G2 (recommendation) or in scope.
- [ ] ProtaBank slot in scope (cross-ref only) or skip-with-rationale.
- [ ] All decisions recorded in `ONTOLOGY_ENRICHMENT_PLAN.md` "Open decisions" table.
