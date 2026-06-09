# Harmonization user-question reference

Real biomedical-research questions a scientist asks Claude (with the
`apecx-mcp-reader` MCP server connected), and the harmonized search
the server runs in response.

Sister to [`harmonization_test_queries.md`](./harmonization_test_queries.md)
— that one is a mechanical regression checklist; THIS one is the
natural-language surface.

## How a question becomes an MCP tool call

```
User: "How many Chikungunya virus genome records do we have?"
   │
   ▼
Claude (apecx-discovery-harmonized skill loaded) decides:
  1. Resolve the user's biomedical term to a canonical IRI
  2. Run harmonized_search on the appropriate Globus index
  3. Present the answer + harmonization context

Tool calls:
  run(operation="resolve", params={"term": "Chikungunya virus"})
    -> NCBITaxon_37124, path=fast

  run(operation="harmonized_search",
      params={"term": "Chikungunya virus", "index": "bvbrc_genome"})
    -> WorkflowResult{raw_total: 1162, harmonized_total: 6684,
                      hitl_required: true, hitl_reasons: [divergence]}

Claude responds to the user:
  "BV-BRC has 6,684 Chikungunya virus genome records. A raw substring
   search of the index would have returned only 1,162 — the additional
   5,522 records use only the verbose species name. Want the broader
   harmonized set, the literal-mention set, or both?"
```

The skill's instructions
([SKILL.md](../search_demo/agent-skill-harmonized/SKILL.md)) tell Claude
when to invoke which primitive.

---

## Category 1 — "How many X are there?" inventory questions

### 1.1 SC-B7 closure — Chikungunya virus

**User**: "How many Chikungunya genome records can we access?"

**Tool calls**:
```
run("resolve", {"term": "CHIKV"})
  → canonical_iri=NCBITaxon_37124, 6,653 synonyms recorded

run("harmonized_search", {"term": "CHIKV", "index": "bvbrc_genome"})
  → raw=1162, harm=6684, |Δ|=5522, hitl_required=true (divergence)
```

**Claude responds**: "6,684 Chikungunya virus genome records in BV-BRC.
The harmonization layer caught 5,522 records that raw substring search
would have missed — those use the verbose `Chikungunya virus` species
name rather than the `CHIKV` acronym. Would you like the broader
species-anchored set, only the literal `CHIKV` mentions, or both?"

**Demonstrates**: SC-B7 — biggest absolute lift in the system.

---

### 1.2 SC-B7 closure — Mayaro virus

**User**: "Total Mayaro virus records in BV-BRC?"

**Tool calls**:
```
run("harmonized_search", {"term": "MAYV", "index": "bvbrc_genome"})
  → raw=6, harm=186, |Δ|=180
```

**Claude responds**: "186 Mayaro virus genome records. Raw search of
the `MAYV` acronym returns only 6 — 30 of every 31 records use only the
verbose species name. The harmonization layer reaches all 186 via the
canonical NCBI Taxonomy ID."

**Demonstrates**: SC-B7 — biggest *relative* lift (31×).

---

### 1.3 SC-B7 closure — Western equine encephalitis virus

**User**: "Pull a count of WEEV records from BV-BRC for me."

**Tool calls**:
```
run("harmonized_search", {"term": "WEEV", "index": "bvbrc_genome"})
  → raw=16, harm=132, |Δ|=116 (8.2× lift)
```

**Claude responds**: "132 Western equine encephalitis virus records.
The WEEV-prefix hyphen-format strain names (`WEEV-UY-228`-style) made
the acronym visible to the corpus miner; the species-name-only records
otherwise unreachable by raw substring search add 116 to the count."

**Demonstrates**: SC-B7 hyphen-separator mining (8.2× lift).

---

### 1.4 Pre-existing acronym (no SC-B7 needed)

**User**: "How many EEEV records are in BV-BRC?"

**Tool calls**:
```
run("harmonized_search", {"term": "EEEV", "index": "bvbrc_genome"})
  → raw=457, harm=895, |Δ|=438 (2.0× lift)
```

**Claude responds**: "895 Eastern equine encephalitis virus records.
Raw search finds 457; the harmonization layer adds 438 records whose
names use only the verbose `Eastern equine encephalitis virus` species
form, reachable through NCBITaxon_11021."

**Demonstrates**: Pre-existing NCBI names.dmp acronym → species expansion.

---

## Category 2 — HITL / AMBIGUOUS questions (must NOT silently pick)

### 2.1 RSV — 6-way ambiguity

**User**: "Show me what we have on RSV."

**Tool calls**:
```
run("resolve", {"term": "RSV"})
  → status=hitl_required, hitl_candidates=[
      NCBITaxon_11246 (Bovine orthopneumovirus),
      NCBITaxon_11250 (Human orthopneumovirus),
      NCBITaxon_11886 (Rous sarcoma virus),
      NCBITaxon_12814 (RSV clade — Tenuivirus),
      NCBITaxon_28869 (Ovine orthopneumovirus),
      NCBITaxon_3052763 (additional Pneumoviridae member),
    ]
```

**Claude responds**: "`RSV` is ambiguous in our dictionary — it maps to
6 different taxa. To run a meaningful search I need to know which one
you mean:
  1. Human orthopneumovirus (the typical clinical RSV)
  2. Bovine orthopneumovirus (cattle RSV)
  3. Ovine orthopneumovirus (sheep RSV)
  4. Rous sarcoma virus (unrelated retrovirus — same acronym)
  5. RSV clade (Tenuivirus)
  6. Other Pneumoviridae member
Which one were you asking about?"

**After the user picks**, e.g., "Human RSV":
```
run("harmonized_search",
    {"term": "http://purl.obolibrary.org/obo/NCBITaxon_11250",
     "index": "bvbrc_genome"})
```

**Demonstrates**: HITL contract — Claude never silently picks for the user.

---

### 2.2 HIV — cross-species acronym collision

**User**: "I want to look at HIV epitope records."

**Tool calls**:
```
run("resolve", {"term": "HIV"})
  → status=hitl_required, hitl_candidates=6
    (Human IV, Feline IV, Simian IV, and 3 others)
```

**Claude responds**: "HIV is ambiguous — the synonym dictionary surfaces
6 candidate taxa. Most clinical research means Human Immunodeficiency
Virus 1 or 2 specifically. Should I narrow to Human IV, or include the
animal IV equivalents (Feline, Simian) too?"

**Demonstrates**: HITL on cross-species acronym collision.

---

### 2.3 HEV — Hepatitis E vs Turkey hemorrhagic enteritis

**User**: "How many HEV records do we have?"

**Tool calls**:
```
run("resolve", {"term": "HEV"})
  → status=hitl_required, 6 candidates including:
    Hepatitis E virus AND Turkey hemorrhagic enteritis virus
    (both legitimately called "HEV" in primary literature)
```

**Claude responds**: "`HEV` resolves to several taxa — most prominently
Hepatitis E virus and Turkey hemorrhagic enteritis virus. Which lineage
are you asking about?"

**Demonstrates**: HITL where both candidates are legitimate biomedical usage.

---

## Category 3 — Cross-source fan-out questions

### 3.1 "Everything we know about X" across all 9 indices

**User**: "Show me everything we have on Chikungunya — genomes, proteins, epitopes, antivirals, all sources."

**Tool calls**:
```
run("resolve", {"term": "Chikungunya virus"})  # canonical_iri established once

# Then per index:
discover("index")  # list all 9 indices
# For each: run("harmonized_search", {"term": "Chikungunya virus", "index": <name>})
```

**Claude responds**: "Across all 9 APECx Globus indices for Chikungunya
virus (NCBITaxon_37124):
  - bvbrc_genome:            6,684 records (harmonized)
  - bvbrc_protein:               1 record
  - bvbrc_protein_structure:     1 record
  - bvbrc_epitope:               1 record
  - violin_pathogen:             1 record
  - violin_vaccine:              0 records (no licensed/candidate vaccine)
  - violin_gene:                 0 records
  - antiviraldb:                 1 record
  - protabank:                   0 records
The largest concentration is in BV-BRC genome data, where the
harmonization layer reaches 5,522 records that raw substring search
of `CHIKV` would miss."

**Demonstrates**: Cross-source orchestration; per-index filter map
(different field per source).

---

### 3.2 Vaccine-specific question (cross-source by intent)

**User**: "Are there any EEEV vaccine candidates in VIOLIN?"

**Tool calls**:
```
run("harmonized_search", {"term": "EEEV", "index": "violin_vaccine"})
  # The violin_vaccine harmonized filter uses VIOLIN_c_pathogen_id (an
  # internal VIOLIN ID), so the resolve step must include the taxon mapping
```

**Claude responds**: "VIOLIN contains <N> vaccine records associated with
Eastern equine encephalitis virus (NCBITaxon_11021). They are linked via
the VIOLIN pathogen_id cross-reference rather than NCBI Taxonomy directly."

**Demonstrates**: Per-index filter shape (`taxon_id` for VIOLIN vaccine
vs `label` for BV-BRC genome) — the harmonization layer abstracts this
from the user.

---

## Category 4 — Visibility / "what got substituted" questions

### 4.1 What other terms reach the same records?

**User**: "If I search for EEEV, what other surface forms in the database does that match?"

**Tool calls**:
```
run("resolve", {"term": "EEEV"})
  → canonical_iri=NCBITaxon_11021,
    synonyms_substitution: {synonyms_count: 401, synonyms_sample: [...]}

inspect("canonical_iri",
        "http://purl.obolibrary.org/obo/NCBITaxon_11021", depth=1)
  → full synonyms list
```

**Claude responds**: "EEEV resolves to Eastern equine encephalitis virus
(NCBITaxon_11021). The dictionary records 401 surface forms that all
map to the same canonical entry — every one of them retrieves the same
set of records. Sample: ['EEEV', 'EEE', 'Eastern equine encephalitis
virus', 'eastern equine encephalitis EEE', ...]. Any of these queries
returns the same harmonized result."

**Demonstrates**: Visibility into synonym substitution — the user sees
WHAT got expanded, not just the answer.

---

### 4.2 What did harmonization do to my query?

**User**: "I queried for `CHIKV` and got 6,684 records — what specifically did the harmonization do?"

**Tool calls**:
```
inspect_run(run_id=<the run_id from the prior harmonized_search>)
  → returns processing_steps, full envelope including raw_q vs harmonized
    filter_field and filter_values_sample
```

**Claude responds**: "Here's the processing trace:
  1. Input: `CHIKV`
  2. Normalize: `chikv`
  3. Dictionary lookup: 1 hit → NCBITaxon_37124, label=`Chikungunya virus`
  4. Filter build: harmonized search uses the `Species` field of
     bvbrc_genome with the value `Chikungunya virus` (plus 6,652 other
     synonyms as the match-any set)
  5. Globus query: harmonized hit 6,684; raw `q=CHIKV` hit 1,162
  6. Divergence: 5,522 records (83% of the harmonized total)
The 5,522-record gap is the records whose genome name doesn't contain
the literal `CHIKV` substring."

**Demonstrates**: `processing_steps` audit trail; the user can audit
every decision the resolver took.

---

## Category 5 — Comparison questions (raw vs harmonized)

### 5.1 "Why does harmonization matter here?"

**User**: "Compare raw substring search and harmonized search for WEEV in BV-BRC. Show me the gap."

**Tool calls**:
```
run("harmonized_search", {"term": "WEEV", "index": "bvbrc_genome"})
  → both totals + divergence already in the same envelope
```

**Claude responds**: "Raw `q=WEEV` on BV-BRC returns 16 records — only
genome names that contain the literal `WEEV` substring (the
`WEEV-UY-228`-style isolates). Harmonized search reaches 132 records by
filtering on `Species=Western equine encephalitis virus`. The 116-record
gap is records whose genome names use only the verbose species name
without the acronym."

**Demonstrates**: Side-by-side raw vs harmonized — the answer the user
gets includes the *why*.

---

## Category 6 — Gap-surfacing questions (HITL fires honestly)

### 6.1 Known ICTV-rename gap (HSV-2)

**User**: "Find HSV-2 genome records in BV-BRC."

**Tool calls**:
```
run("resolve", {"term": "HSV-2"})
  → NCBITaxon_10298, path=fast (synonym mined from VIOLIN prose by SC-B8)

run("harmonized_search", {"term": "HSV-2", "index": "bvbrc_genome"})
  → raw=42, harm=0, hitl_required=true
    hitl_reasons: ["raw_total=42 vs harmonized_total=0 (|Δ|=42, 100%)"]
```

**Claude responds**: "I found a discrepancy worth flagging. Raw substring
search of `HSV-2` returns 42 BV-BRC genome records. But the harmonized
search returns 0 — because BV-BRC stores the modern ICTV-renamed name
(`Human alphaherpesvirus`-family taxonomy) rather than the legacy `HSV-2`
label our dictionary's synonym list carries for taxon 10298. This is a
known label-bridge gap.
For accurate results today, raw substring search is the better path for
HSV-2 against BV-BRC. We're tracking the dictionary rebuild needed to
close this."

**Demonstrates**: Gap surfaced via HITL (harm=0, raw>0); honest answer
rather than silent zero.

---

### 6.2 Genus-vernacular gap (alphavirus)

**User**: "How many alphavirus records are in BV-BRC?"

**Tool calls**:
```
run("harmonized_search", {"term": "alphavirus", "index": "bvbrc_genome"})
  → raw=98, harm=14, hitl_required=true
```

**Claude responds**: "`alphavirus` is a genus name (Alphavirus,
NCBITaxon_11019). Harmonized search returns only 14 records that are
explicitly tagged with the genus IRI. Raw substring search returns 98 —
matching genome names from individual alphavirus species (EEEV, VEEV,
WEEV, CHIKV, etc.) that mention `alphavirus` in their name.
If you want every record across the genus subtree, you probably want to
query each species individually. Want me to enumerate the species under
genus Alphavirus and aggregate?"

**Demonstrates**: Honest gap — genus-subtree expansion isn't in the
harmonization layer yet; Claude suggests the workaround.

---

## Category 7 — Multi-step workflow questions

### 7.1 Resolve → present candidates → re-query with chosen IRI

**User**: "I'm working on RSV-related antivirals. What do you have?"

**Step 1**: Claude resolves RSV → 6 candidates → asks user which one.

**Step 2**: User says "Human RSV".

**Step 3 — Claude looks up the right candidate IRI**:
```
discover("candidate_iri", "Human orthopneumovirus")
  → NCBITaxon_11250

inspect("canonical_iri",
        "http://purl.obolibrary.org/obo/NCBITaxon_11250", depth=1)
  → all synonyms for Human RSV, source records
```

**Step 4 — Claude runs the harmonized search**:
```
run("harmonized_search",
    {"term": "http://purl.obolibrary.org/obo/NCBITaxon_11250",
     "index": "antiviraldb"})
```

**Claude responds**: "Antiviraldb has <N> compound records targeting
Human orthopneumovirus (Human RSV, NCBITaxon_11250). The harmonized
filter uses the `Virus` field on each AntiviralDB record."

**Demonstrates**: HITL recovery path; Claude takes the user's choice
and re-queries cleanly.

---

### 7.2 Cross-reference workflow

**User**: "We have CHIKV genome data — do we also have epitope and protein structure data for it?"

**Step 1**:
```
run("resolve", {"term": "CHIKV"}) -> NCBITaxon_37124
```

**Step 2 — query three indices in parallel**:
```
run("harmonized_search", {"term": "CHIKV", "index": "bvbrc_genome"})
run("harmonized_search", {"term": "CHIKV", "index": "bvbrc_epitope"})
run("harmonized_search",
    {"term": "CHIKV", "index": "bvbrc_protein_structure"})
```

**Claude responds**: "For Chikungunya virus (NCBITaxon_37124):
  - bvbrc_genome:            6,684 records
  - bvbrc_epitope:               1 record
  - bvbrc_protein_structure:     1 record
You have dense genome coverage; epitope and protein-structure data is
sparse. Want me to inspect what's there?"

**Demonstrates**: Multi-index orchestration on a single canonical entity.

---

## Quick-reference table — what question triggers what

| User question pattern | MCP primitive(s) | Demonstrates |
|---|---|---|
| "How many X are there?" / "Count of X" | `run(resolve)` then `run(harmonized_search)` | Single-index inventory |
| "What X do you have across all sources?" | `run(resolve)` + `discover(index)` + `run(harmonized_search)` × N | Cross-source fan-out |
| "Show me X" where X has multiple meanings | `run(resolve)` → HITL with candidates | AMBIGUOUS handling |
| "What does X stand for?" / "What is X?" | `run(resolve)` only | Acronym → species |
| "What other names match X?" | `inspect(canonical_iri)` | Synonym substitution visibility |
| "Why did the search return so many more / fewer?" | `inspect_run(run_id)` for processing_steps | Decision audit trail |
| "Compare raw vs harmonized for X" | `run(harmonized_search)` returns both | Side-by-side divergence |
| "Find X but X is a recent rename" | `run(harmonized_search)` returns harm=0, raw>0 | Honest gap surfacing |
| "Pick the Human / Bovine / etc. variant of X" | `inspect(canonical_iri, <picked>)` then re-query | HITL recovery |

## What this doc does NOT cover

- **The skill's own SKILL.md instructions** — those teach Claude when to
  pick `resolve` vs `harmonized_search` vs `inspect`. They're separate
  documentation: [SKILL.md](../search_demo/agent-skill-harmonized/SKILL.md).
- **Authentication for backend publish** — the user-facing arm is
  anonymous read; publish-side credentials are in
  [`docs/backend_arm_runbook.md`](./backend_arm_runbook.md).
- **Performance** — none of these questions assert latency. First
  resolve loads the 247 MB dict (~5 s); subsequent calls in the same
  session are sub-millisecond. Globus queries take 100–500 ms each.
- **Failure modes** — questions assume the dict is bootstrapped and
  Globus indices are reachable. Both are verified by
  [`harmonization_test_queries.md`](./harmonization_test_queries.md)
  Group 8 (visibility) and Group 9 (concordance).
