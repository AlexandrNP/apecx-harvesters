# Open Questions & Follow-up Items

Items that need a decision or further investigation before they can be closed.

## PDB: polymer entity harmonization

`PDBFields.polymer_entities` records the source organism and polymer type per entity, preserving the entity-organism association that the prior art lost through flattening. However, the correct harmonization strategy for multi-organism complexes (e.g. a viral antigen bound to a human antibody) is a **domain expert decision**:

- How should a structure with two organisms be represented in search? One record or two?
- Should the organism list be promoted to `DataCite.subjects` or a custom field?
- What is the canonical "organism" of a complex — the target, the host, all of them?

See `TODO` comment in `PDBFields.polymer_entities`.

## PDB: DNA-containing structure fixture

`polymer_type` is tested for `Protein` and `RNA` (via 4ZT0) but not `DNA` or `NA-hybrid`. A nucleosome (e.g. 3LZ0) or Cas9+DNA structure would cover this branch if it becomes relevant.

## IEDB harvester

The prior art (`prior_art/tools/iedb_basic_data_collector/`) harvests epitope data from `https://query-api.iedb.org/epitope_export`. The IEDB schema (epitope identity, source molecule, position, related object) does not map naturally to DataCite. Needs a design discussion before implementation:

- What goes in base `DataCite` fields vs. a nested `IEDBFields`?
- What is the canonical identifier? (Prior art used MD5 hash; IEDB IRI may be more appropriate.)


## PubMed: search richness

PubMed supports many search options beyond what the current `PubMedHarvester` exposes. Expand search before unifying the search interface across harvesters. Defer interface unification until search operations are richer.

## Globus Search index harmonization (9 indices → 1 public index)

See `design/GLOBUS_INDEX_HARMONIZATION_PLAN.md` for the full plan (and its Codebase baseline: remote HEAD `b47bc86`, 2026-05-04). Open items to decide during Phase 2 (schema discovery on real data):

- Record granularity for the 5 nested sources (BVBRC:*, AntiviralDB, e.g. `Protein_and_Epitope[]`): one harmonized record per source-document (an organism aggregate) vs. exploding the nested arrays to one record per entity (per epitope / per protein). Determines the shape + count of the public index. (Replaces the earlier 32 KB-field question, now retired.)
- Per-document size, not 32 KB fields: profiling (2026-05-26) found the largest leaf field across all 9 indices is ~2.5 KB — these indices are metadata catalogs (no inline sequences), already Globus-ingested so all fields are ≤32 KB by construction. The real constraint is per-document size: nested aggregates reach multi-MB (BVBRC:Epitope up to 4.17 MB), so the publish path must respect the 10 MB per-entry / `GMetaList` batch guard (`pipeline/sinks.py:84-88`).
- Source volatility: BVBRC:Genome is mid-reingest (count 745,917 → ~523k then climbing ~500 docs/s on 2026-05-26). A scroll started mid-rebuild captures a torn snapshot — record `total` at scrape start+end, abort/reconcile on drift, and defer BVBRC:Genome until it stabilizes. The other 8 indices are stable.
- Per-source DataCite fit for ProtaBank, AntiviralDB, VIOLIN:{Pathogen,Vaccine,Gene}, BVBRC:{Epitope,Protein_Structure,Protein,Genome} — what promotes to base `DataCite` vs. a nested container. Epitope fit overlaps the IEDB harvester question above.
- Entity linking across sources (genome ↔ protein ↔ epitope ↔ vaccine/pathogen): the pipeline does no entity resolution today — is it in scope? Sources carry cross-reference IDs (`Taxon_ID`, `NCBI_*`, `PDB_ID`, `vaccine_pathogen_id`), so linking by shared accession is feasible without fuzzy matching.
- New connector direction: these sources are read out of a Globus Search index, not fetched by ID — needs a `GlobusIndexSource` feeding `pipeline.run()` directly, bypassing `BaseHarvester`.
- Globus Search offset cap (~10k) → full extraction must use `scroll_query`, not `post_search` offsets (same shape as the PubMed eSearch 9,999-record cap).
