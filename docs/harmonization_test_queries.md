# Harmonization test queries — MCP + skill reference

Practical test plan for the apecx-harvesters harmonization layer. Each
query below carries:

- The **surface term** the user types
- What the harmonization **demonstrates** (SC-B7 closure / HITL / parity / etc.)
- The **expected resolution** (path, canonical IRI, candidate count)
- Expected **live-Globus counts** (raw substring vs harmonized) where applicable
- The **MCP primitive call** (any MCP client) AND the **skill CLI** form
- **Pass criterion** — what to assert

All numbers are measured live against the production APECx Globus indices on
2026-06-09 with dictionary version `sc-a4c-2026-06-08`. Re-running may yield
slightly different totals as upstream sources grow; the **relative shape**
(harmonized > raw, ambiguous → 6 candidates, parity, etc.) is what the
test verifies.

## Setup

```bash
# 1. Install reader + MCP from the fork (until upstream PR merges)
pip install 'apecx-harvesters[mcp] @ git+https://github.com/AlexandrNP/apecx-harvesters.git@main'

# 2. Point the bootstrap at the published Globus path
export APECX_DICT_PUBLIC_BASE_URL="https://g-958ce2.fd635.8443.data.globus.org/apecx-ramanathan-anl/public/synonyms_dictionary"

# 3. Bootstrap the local dictionary (~45 MB download, ~250 MB on disk)
apecx-dict-update

# 4. Verify the install
apecx-lookup CHIKV --json
```

**MCP access**: the harmonization layer is reached through the canonical
`apecx-mcp` server in `apecx-mcp-integration` (tools:
`resolve_canonical_entity`, `query_globus_search`,
`list_workflows`/`describe_workflow`, `start_workflow`/`show_diff`/
`execute_workflow`). The previous standalone `apecx-mcp-reader` from
this repo was retired 2026-06-09 because it duplicated
`resolve_canonical_entity` + `query_globus_search`. For installing the
canonical MCP server, see `apecx-mcp-integration/docs/mcp_integration.md`.

For Claude Desktop, add the canonical server to your client config:

```json
{
  "mcpServers": {
    "apecx-mcp": {
      "command": "apecx-mcp",
      "env": {
        "APECX_DICT_PUBLIC_BASE_URL": "https://g-958ce2.fd635.8443.data.globus.org/apecx-ramanathan-anl/public/synonyms_dictionary"
      }
    }
  }
}
```

For programmatic clients, the canonical primitives are documented in
`docs/two_arm_contract.md` and `search_demo/agent-skill-harmonized/SKILL.md`.

---

## Group 1 — SC-B7 strain-prefix mining wins (BVBRC, automatic)

These are the headline closures the corpus mining unlocked. Pre-SC-B7 the
harmonized query returned **zero records** for these acronyms; post-SC-B7
the harmonization layer reaches every record of the species.

### 1.1 CHIKV — Chikungunya virus (biggest absolute win)

| What | Value |
|---|---|
| **Expected resolution** | path=`fast`, iri=`http://purl.obolibrary.org/obo/NCBITaxon_37124`, label=`Chikungunya virus` |
| **Synonyms recorded** | 6,653 surface forms |
| **Raw (BVBRC genome)** | 1,162 records |
| **Harmonized** | 6,684 records |
| **Lift** | **+5,522 records (5.8×)** |
| **Demonstrates** | SC-B7 slash-separator mining (`CHIKV/IRL/2007`-style genome names) |

```bash
# Skill CLI:
python scripts/harmonized_query.py --term CHIKV --resolve-only
python scripts/harmonized_query.py --term CHIKV --index bvbrc_genome --compare --limit 500

# MCP primitive (any client):
run(operation="resolve", params={"term": "CHIKV"})
run(operation="harmonized_search", params={"term": "CHIKV", "index": "bvbrc_genome", "limit": 500})
```

**Pass criterion**: resolve returns `path: fast`, canonical_iri contains `NCBITaxon_37124`. harmonized_search returns `harmonized_total >= 6000` and `harmonized_total > 4 * raw_total`.

---

### 1.2 MAYV — Mayaro virus (largest relative lift)

| What | Value |
|---|---|
| **Expected resolution** | path=`fast`, iri=`http://purl.obolibrary.org/obo/NCBITaxon_59301`, label=`Mayaro virus` |
| **Synonyms recorded** | 183 surface forms |
| **Raw (BVBRC genome)** | 6 records |
| **Harmonized** | 186 records |
| **Lift** | **+180 records (31×)** |
| **Demonstrates** | SC-B7 underscore-separator mining (`MAYV_BR/MT_...`-style names) |

```bash
run(operation="resolve", params={"term": "MAYV"})
run(operation="harmonized_search", params={"term": "MAYV", "index": "bvbrc_genome"})
```

**Pass criterion**: resolve returns `NCBITaxon_59301`; harmonized_total ≥ 150 and ratio ≥ 20×.

---

### 1.3 WEEV — Western equine encephalitis virus

| What | Value |
|---|---|
| **Expected resolution** | path=`fast`, iri=`http://purl.obolibrary.org/obo/NCBITaxon_11039` |
| **Synonyms recorded** | 134 surface forms |
| **Raw (BVBRC genome)** | 16 records |
| **Harmonized** | 132 records |
| **Lift** | **+116 records (8.2×)** |
| **Demonstrates** | SC-B7 hyphen-separator mining (`WEEV-UY-228`-style names) — hyphen NOT slash |

```bash
run(operation="resolve", params={"term": "WEEV"})
run(operation="harmonized_search", params={"term": "WEEV", "index": "bvbrc_genome"})
```

**Pass criterion**: `NCBITaxon_11039`, harmonized_total ≥ 100, ratio ≥ 5×.

---

### 1.4 GETV — Getah virus (bonus SC-B7 find)

| What | Value |
|---|---|
| **Expected resolution** | path=`fast`, iri=`http://purl.obolibrary.org/obo/NCBITaxon_59300` |
| **Demonstrates** | SC-B7 also picked up Getah virus (frequency-mined, not a hardcoded acronym) |

```bash
run(operation="resolve", params={"term": "GETV"})
```

**Pass criterion**: path=`fast`, canonical_iri contains `NCBITaxon_59300`.

---

### 1.5 MADV — Madariaga virus (bonus SC-B7 find)

| What | Value |
|---|---|
| **Expected resolution** | path=`fast`, iri=`http://purl.obolibrary.org/obo/NCBITaxon_1440170` |
| **Demonstrates** | SC-B7 surfaced Madariaga virus from BVBRC's strain-prefix corpus |

```bash
run(operation="resolve", params={"term": "MADV"})
```

**Pass criterion**: path=`fast`, canonical_iri contains `NCBITaxon_1440170`.

---

## Group 2 — Pre-SC-B7 acronym wins (already in NCBI names.dmp)

These work without the corpus mining, demonstrating the baseline
acronym→species expansion (NCBI `names.dmp` ingest path).

### 2.1 EEEV — Eastern equine encephalitis virus

| What | Value |
|---|---|
| **Expected resolution** | path=`fast`, iri=`NCBITaxon_11021` |
| **Raw (BVBRC genome)** | 457 records |
| **Harmonized** | 895 records |
| **Lift** | **+438 records (2.0×)** |

```bash
run(operation="resolve", params={"term": "EEEV"})
run(operation="harmonized_search", params={"term": "EEEV", "index": "bvbrc_genome"})
```

**Pass criterion**: path=`fast`, `NCBITaxon_11021`, harmonized_total ≥ 800, ratio ≥ 1.5×.

---

### 2.2 VEEV — Venezuelan equine encephalitis virus

| What | Value |
|---|---|
| **Expected resolution** | path=`fast`, iri=`NCBITaxon_11036` |
| **Raw (BVBRC genome)** | 105 records |
| **Harmonized** | 480 records |
| **Lift** | **+375 records (4.6×)** |

```bash
run(operation="harmonized_search", params={"term": "VEEV", "index": "bvbrc_genome"})
```

**Pass criterion**: path=`fast`, `NCBITaxon_11036`, ratio ≥ 4×.

---

## Group 3 — SC-B8 VIOLIN parenthetical-acronym wins

Mined automatically from VIOLIN Pathogen description prose (e.g.,
`"Herpes simplex virus 1 and 2 (HSV-1 and HSV-2)"`).

### 3.1 HSV-1, HSV-2, HHV-1 (Herpes simplex group)

| What | Value |
|---|---|
| **Expected resolution** | All three → path=`fast`, iri=`NCBITaxon_10298`, label=`Human alphaherpesvirus 1` |
| **Synonyms** | 15 (HSV-2), 15 (HHV-1), more for HSV-1 |
| **Demonstrates** | SC-B8 parenthetical extraction; multiple synonyms point to same species |

```bash
run(operation="resolve", params={"term": "HSV-2"})
run(operation="resolve", params={"term": "HHV-1"})
```

**Pass criterion**: path=`fast`, `NCBITaxon_10298` for both. Note: the
HARMONIZED Globus search on `bvbrc_genome` for HSV-2 returns 0 because
BVBRC stores the modern ICTV name `Orthoherpesvirinae alpha` — known
label-bridge gap (Group 7).

---

### 3.2 CCHF — Crimean-Congo hemorrhagic fever virus (merge-walked)

| What | Value |
|---|---|
| **Expected resolution** | path=`fast`, iri=`NCBITaxon_3052518`, label=`Orthonairovirus haemorrhagiae` |
| **Synonyms** | 8 surface forms |
| **Demonstrates** | SC-B8 mined `CCHF` from VIOLIN prose; NCBI taxon 11593 merged to 3052518 (ICTV rename); the bootstrap walks `merged_taxons` to land on the active entry |

```bash
run(operation="resolve", params={"term": "CCHF"})
```

**Pass criterion**: path=`fast`, canonical_iri contains `NCBITaxon_3052518`. (NOT 11593, even though VIOLIN records carry the old ID — the merge-walk transparently bridges.)

---

### 3.3 TBEV — Tick-borne encephalitis virus

| What | Value |
|---|---|
| **Expected resolution** | path=`fast`, iri=`NCBITaxon_11084` |
| **Synonyms** | 7 surface forms |
| **Demonstrates** | SC-B8 mined `TBEV` from `"Tick-borne Encephalitis Virus (TBEV)"` |

```bash
run(operation="resolve", params={"term": "TBEV"})
```

**Pass criterion**: path=`fast`, `NCBITaxon_11084`. Same label-bridge caveat as HSV-2 for the live Globus query.

---

### 3.4 RVF — Rift Valley fever virus

```bash
run(operation="resolve", params={"term": "RVF"})
```

**Expected**: path=`fast`, `NCBITaxon_11588`. Verbose-name harmonized query for `Rift Valley fever virus` against `bvbrc_genome` returns ~1812 records.

---

### 3.5 Other SC-B8 confirmable closures

Quick smoke tests (path=`fast` expected for all):

```bash
run(operation="resolve", params={"term": "WNV"})        # West Nile, NCBITaxon_11082
run(operation="resolve", params={"term": "VZV"})        # Varicella-zoster, NCBITaxon_10335
run(operation="resolve", params={"term": "ASFV"})       # African swine fever, NCBITaxon_10497
run(operation="resolve", params={"term": "BLV"})        # Bovine leukemia, NCBITaxon_11901
run(operation="resolve", params={"term": "FIV"})        # Feline immunodeficiency, NCBITaxon_11673
```

**Pass criterion**: all return path=`fast` with the expected canonical_iri.

---

## Group 4 — AMBIGUOUS / HITL cases

The harmonization MUST surface ambiguity rather than silently picking a
winner. Each of these terms maps to ≥ 2 distinct canonical IRIs; the
tool must return `status: hitl_required` with the full candidate list.

### 4.1 RSV — Respiratory Syncytial Virus

| What | Value |
|---|---|
| **Expected resolution** | path=`ambiguous`, **6 candidates** |
| **Candidate taxa** | Human / Bovine / Ovine orthopneumovirus + Rous sarcoma virus + Tenuivirus + RSV clade |
| **Demonstrates** | The 6-way ambiguity that raw-text search silently lumps together |

```bash
run(operation="resolve", params={"term": "RSV"})
# Then to inspect a candidate before user picks:
inspect(object_type="canonical_iri", id="<picked candidate IRI>", depth=1)
# Then re-call with the chosen IRI to "commit":
run(operation="resolve", params={"term": "<chosen IRI>"})
```

**Pass criterion**: status=`hitl_required`, `len(hitl_candidates) == 6`, all candidates have non-empty `canonical_iri` and `canonical_label`, `next_actions` array suggests both `inspect` and a follow-up `run`.

---

### 4.2 HIV — Human/Feline/Simian Immunodeficiency Virus

| What | Value |
|---|---|
| **Expected resolution** | path=`ambiguous`, **6 candidates** |
| **Demonstrates** | Same acronym surfaced from multiple VIOLIN records (Human, Feline, etc.) — SC-A5b ambiguity capture preserves all candidates |

```bash
run(operation="resolve", params={"term": "HIV"})
```

**Pass criterion**: status=`hitl_required`, 6 candidates. Confirms HITL on cross-species acronym collision.

---

### 4.3 HEV — Hepatitis E vs Turkey hemorrhagic enteritis

| What | Value |
|---|---|
| **Expected resolution** | path=`ambiguous`, **6 candidates** |
| **Demonstrates** | HEV is real for both Hepatitis E virus AND for Turkey hemorrhagic enteritis virus; SC-B8 mined both and SC-A5b correctly routes to HITL |

```bash
run(operation="resolve", params={"term": "HEV"})
```

**Pass criterion**: status=`hitl_required`, ≥ 2 candidates.

---

### 4.4 HRSV — Human RSV (with cross-contamination caveat)

| What | Value |
|---|---|
| **Expected resolution** | path=`ambiguous`, ~3 candidates |
| **Demonstrates** | HRSV was mined from Bovine RSV's VIOLIN record (which mentions HRSV) AND from Human RSV's record; AMBIGUOUS correctly routes the user to pick |

```bash
run(operation="resolve", params={"term": "HRSV"})
```

**Pass criterion**: status=`hitl_required`. The fact that the resolver fires AMBIGUOUS here is the safety net working — without it, the wrong IRI would be picked silently.

---

## Group 5 — Parity cases (no harmonization needed)

Verbose species names where raw substring search and harmonized search
return the same record count. Confirms harmonization doesn't over-expand.

### 5.1 Sindbis virus

```bash
run(operation="harmonized_search", params={"term": "Sindbis virus", "index": "bvbrc_genome"})
```

**Expected**: raw=612, harmonized=612, divergence=0, hitl_required=`false`. Pass criterion: |raw − harm| ≤ 5 (i.e., parity, no HITL flag).

---

### 5.2 Chikungunya virus (verbose form, not the acronym)

Different from `CHIKV` because the user typed the full species name; both
raw and harmonized hit the species' records.

```bash
run(operation="harmonized_search", params={"term": "Chikungunya virus", "index": "bvbrc_genome"})
```

**Expected**: raw ≈ harm (with small noise from substring false positives in raw).

---

## Group 6 — Cross-source fan-out

The harmonization works across all 9 APECx Globus indices, not just
BVBRC genome. These queries demonstrate the fan-out pattern.

### 6.1 CHIKV across all indices

```bash
# Discover the catalog:
discover(category="index")

# Then run harmonized_search per index (the MCP server doesn't yet have
# a single fan-out primitive; the skill CLI has --all-indices):
for idx in violin_pathogen violin_vaccine violin_gene bvbrc_genome \
          bvbrc_protein bvbrc_protein_structure bvbrc_epitope \
          antiviraldb protabank; do
  run(operation="harmonized_search", params={"term": "CHIKV", "index": $idx})
done
```

Or with the skill CLI:

```bash
python scripts/harmonized_query.py --term CHIKV --all-indices --compare --limit 200
```

**Pass criterion**: all 9 indices return without error; bvbrc_genome shows the headline 5.8× lift; smaller corpora (bvbrc_epitope, antiviraldb, etc.) return small but non-zero records when the species is covered.

---

## Group 7 — Known gaps (HITL surfaces them honestly)

These are real gaps the harmonization layer has today. The test verifies
that the system **surfaces the gap as HITL** rather than silently returning
zero. A future dict-rebuild closes them.

### 7.1 HSV-2 against BVBRC Globus index (ICTV label-bridge gap)

```bash
run(operation="harmonized_search", params={"term": "HSV-2", "index": "bvbrc_genome"})
```

**Expected**:
- raw_total ≈ 42 records (literal `HSV-2` mentions)
- harmonized_total = **0** (BVBRC stores `Orthoherpesvirinae`-renamed labels, our dict's synonyms list for taxon 10298 lacks them)
- hitl_required=`true` with reason `raw_total=42 vs harmonized_total=0 (|Δ|=42, 100%)`

**Pass criterion**: hitl_required=`true`. The MCP envelope surfaces the gap; the user sees harm=0 and knows to fall back to raw or inspect candidates.

---

### 7.2 TBEV against BVBRC Globus index

```bash
run(operation="harmonized_search", params={"term": "TBEV", "index": "bvbrc_genome"})
```

Same shape as 7.1 — BVBRC stores `Orthoflavivirus encephalitidis` for tick-borne records; dict's TBEV entry (taxon 11084) doesn't carry that synonym yet.

**Pass criterion**: hitl_required=`true`, harmonized_total=0 surfaces the gap.

---

### 7.3 alphavirus (genus-vernacular gap)

```bash
run(operation="harmonized_search", params={"term": "alphavirus", "index": "bvbrc_genome"})
```

**Expected**: harmonized_total very small (~14 — only records explicitly tagged with `Alphavirus` genus); raw catches 98 substring matches across alphavirus-family species. Neither mode is "right" — the user probably wants the genus subtree, which requires SC-A5b ancestor walking.

**Pass criterion**: hitl_required=`true` (large divergence); demonstrates the genus-subtree limitation.

---

## Group 8 — Visibility + HITL flow integration

These test that the MCP envelope's visibility guarantees hold (processing_steps,
synonyms_substitution, hitl_prompt, next_actions) — not the resolution itself.

### 8.1 Every resolve carries a processing_steps audit trail

```bash
result = run(operation="resolve", params={"term": "CHIKV"})
assert "processing_steps" in result
assert any(s["step"] == "normalize" for s in result["processing_steps"])
assert any(s["step"] == "resolve" for s in result["processing_steps"])
```

**Pass criterion**: at least 4 steps recorded (input, normalize, dict_loaded, resolve).

---

### 8.2 Every resolve carries synonym_substitution visibility

```bash
result = run(operation="resolve", params={"term": "CHIKV"})
sub = result["data_preview"]["synonyms_substitution"]
assert sub["user_term"] == "CHIKV"
assert sub["synonyms_count"] > 100  # CHIKV has 6,653 synonyms recorded
assert len(sub["synonyms_sample"]) > 0
```

**Pass criterion**: synonyms_substitution surfaces what other surface forms travel under the same canonical IRI — the user can see what their query is "expanding to."

---

### 8.3 HITL response carries next_actions guidance

```bash
result = run(operation="resolve", params={"term": "RSV"})
assert result["hitl_required"] is True
assert len(result["hitl_candidates"]) == 6
assert len(result["next_actions"]) >= 2
tools = {a["tool"] for a in result["next_actions"]}
assert "inspect" in tools  # so user can examine each candidate
assert "run" in tools      # so user can re-call with chosen IRI
```

**Pass criterion**: HITL responses guide the model toward concrete follow-up tool calls; they don't just say "ambiguous, figure it out."

---

### 8.4 inspect_run retrieves prior payloads from session

```bash
r1 = run(operation="resolve", params={"term": "EEEV"})
saved_run_id = r1["run_id"]
# ... later in the same session ...
r2 = inspect_run(run_id=saved_run_id)
assert r2["operation"] == "resolve"
assert r2["envelope"]["data_preview"]["result"]["canonical_iri"] == \
       r1["data_preview"]["result"]["canonical_iri"]
```

**Pass criterion**: data_handle / run_id retrieve the original envelope without re-running the lookup. Verifies the §5 WorkflowResult contract.

---

## Group 9 — Concordance across surfaces

This is the meta-test: skill CLI, dict_reader CLI, and MCP primitive must
all return the same answers for the same inputs.

```bash
# Path A — skill CLI:
python scripts/harmonized_query.py --term CHIKV --resolve-only

# Path B — dict_reader CLI:
apecx-lookup CHIKV --json

# Path C — MCP primitive (via any MCP client):
run(operation="resolve", params={"term": "CHIKV"})
```

**Pass criterion**: all three return path=`fast`, canonical_iri contains `NCBITaxon_37124`, label=`Chikungunya virus`. Same for all 8 SC-B7/SC-B8 test terms (CHIKV, EEEV, TBEV, MAYV, WEEV, RSV, HSV-2, CCHF).

The strong concordance proof: every field of the envelope (except run_id and
timestamps) is byte-equal. Last verified 2026-06-09: 10/10 resolve terms and
5/5 live-Globus harmonized_search queries byte-equal across local source
checkout and git+-installed venv.

---

## Quick reference — pass/fail summary table

Use this as a regression checklist. Run each row through the test infrastructure
of your choice; expected values are the resolution + measured Globus counts.

| Term | Group | path | Canonical | harm/raw (bvbrc_genome) | HITL |
|---|---|---|---|---|:---:|
| CHIKV | SC-B7 closure | fast | NCBITaxon_37124 | 6684/1162 (5.8×) | no |
| EEEV | acronym | fast | NCBITaxon_11021 | 895/457 (2.0×) | no |
| VEEV | acronym | fast | NCBITaxon_11036 | 480/105 (4.6×) | no |
| WEEV | SC-B7 closure | fast | NCBITaxon_11039 | 132/16 (8.2×) | no |
| MAYV | SC-B7 closure | fast | NCBITaxon_59301 | 186/6 (31×) | no |
| GETV | SC-B7 bonus | fast | NCBITaxon_59300 | (corpus small) | no |
| MADV | SC-B7 bonus | fast | NCBITaxon_1440170 | (corpus small) | no |
| HSV-2 | SC-B8 closure | fast | NCBITaxon_10298 | 0/42 (gap) | **yes** |
| HHV-1 | SC-B8 closure | fast | NCBITaxon_10298 | (same as HSV-2) | (gap) |
| CCHF | SC-B8 + merge-walk | fast | NCBITaxon_3052518 | (small corpus) | no |
| TBEV | SC-B8 closure | fast | NCBITaxon_11084 | 0/44 (gap) | **yes** |
| RVF | SC-B8 closure | fast | NCBITaxon_11588 | (verbose name → ~1812) | no |
| RSV | AMBIGUOUS | ambiguous | (6 candidates) | (deferred) | **yes** |
| HIV | AMBIGUOUS | ambiguous | (6 candidates) | (deferred) | **yes** |
| HEV | AMBIGUOUS | ambiguous | (6 candidates) | (deferred) | **yes** |
| HRSV | AMBIGUOUS | ambiguous | (3 candidates) | (deferred) | **yes** |
| Sindbis virus | parity | fast | NCBITaxon_11034 | 612/612 (1.0×) | no |
| alphavirus | gap (genus) | fast | NCBITaxon_11019 | 14/98 (gap) | **yes** |

## What this test plan does NOT cover

Listed for honesty:

- **Performance / throughput**: no latency or concurrency assertions.
- **The SC-A4 dict-build pipeline itself**: the dict is treated as a sealed
  input. Rebuilding it from NCBI names.dmp + the enriched VIOLIN/BVBRC CSVs
  is `apecx-mcp-integration`'s synonym_dictionary build flow, out of scope.
- **Authentication paths** (for the publish side): the bootstrap path is
  anonymous HTTPS; publish requires Globus client_credentials. Not tested here.
- **Local dict mutation**: SC-B4 ingest of new mined observations is exercised
  by the unit tests in `tests/test_corpus_mining_extractors.py`, not here.
- **Production data churn**: numbers were measured against the BVBRC index on
  2026-06-09. Re-run will see slight drift as upstream sources grow.
