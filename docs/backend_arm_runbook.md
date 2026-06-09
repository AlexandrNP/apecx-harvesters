# Backend arm runbook — mining → ingest → publish

The backend arm produces a new published synonym dictionary. End-to-end
this is **mine → ingest → compress → publish to Globus**. Each step can
be re-run independently.

## Prerequisites

Required:

- The apecx-mcp-integration venv with `apecx_integration` installed
  (carries the SC-B4 ingest + the Globus transfer driver).
- The apecx-harvesters venv with mining code (SC-B7 + SC-B8 in
  `apecx_harvesters.pipeline.corpus_mining_extractors`).
- A pre-existing local dict at `~/.apecx/dictionary/dictionary.sqlite`
  built from the canonical NCBI names.dmp + enriched VIOLIN/BVBRC.
  Building one from scratch is out of scope for this runbook — see
  `apecx_integration.synonym_dictionary.build` for that pipeline.
- The enriched corpora at `~/.apecx/dictionary/enriched/`.

For the publish step (`--upload`):

- A Globus confidential client (client_id + client_secret) with
  consent for BOTH the Transfer scope AND the HTTPS data-access scope
  of the APECx Data at Argonne LCF collection
  (UUID `8d2e71d6-7a29-41d9-94e5-38d8a95fa5db`). Register one at
  https://app.globus.org/settings/developers if you don't already have
  one configured.
- `GLOBUS_COMPUTE_CLIENT_ID` + `GLOBUS_COMPUTE_CLIENT_SECRET` env vars
  set (the same vars `apecx-globus-setup store` uses).
- **No local Globus endpoint is required.** Publish is a direct HTTPS
  PUT to the collection — same URL the user-facing arm GETs from
  anonymously, just with an `Authorization: Bearer <token>` header.
  No Globus Transfer task, no endpoint pairing, no Globus Connect
  Personal install.

## Step 1 — Run the corpus mining pass

SC-B7 (BVBRC strain-prefix acronyms) + SC-B8 (VIOLIN parenthetical
acronyms). Both batch miners accept an `Iterable[BVBRCGenomeContainer]`
or `Iterable[VIOLINPathogenContainer]`; the container construction
goes through the standard loaders.

```python
from apecx_harvesters.pipeline.corpus_mining import MinedSynonymAccumulator
from apecx_harvesters.pipeline.corpus_mining_extractors import (
    mine_bvbrc_strain_prefix_acronyms,
    mine_violin_pathogen_acronyms,
)

acc = MinedSynonymAccumulator()
mine_bvbrc_strain_prefix_acronyms(
    bvbrc_containers, accumulator=acc, min_count=10, min_fraction=0.05,
)
mine_violin_pathogen_acronyms(violin_containers, accumulator=acc)

# Emit JSONL sidecar (input to SC-B4 ingest)
for (norm, tid), sources in acc._buckets.items():
    for source in sources:
        write_json_line({
            "surface_form": acc._original_form[norm],
            "surface_form_normalized": norm,
            "taxon_id": tid,
            "source": source,
            "source_count": 1,
        })
```

For NCBI-merged taxa (e.g., NCBI 11593 -> 3052518 for CCHF), the
sidecar emitter walks `merged_taxons` before writing so the ingest
hits the active entry. See the existing one-off script in the
2026-06-08 session log for the merge-walk pattern.

## Step 2 — Apply the mined observations via SC-B4 ingest

```python
from apecx_integration.synonym_dictionary.mined_ingest import (
    ingest_mined_observations,
)
summary = ingest_mined_observations(
    dict_path=Path.home() / ".apecx" / "dictionary" / "dictionary.sqlite",
    mined_jsonl=Path("/tmp/sc_b8_violin_acronym_observations.jsonl"),
    entity_type="pathogen",
)
print(summary)
# IngestSummary(rows_read=40, entries_touched=15, synonyms_added=15,
#               inverse_writes=34, new_ambiguity_captures=8, mined_conflicts_written=5,
#               missing_entries=0)
```

This mutates the local dict in place. Re-running with the same JSONL
is idempotent at the (surface, taxon, source) tuple level.

## Step 3 — Verify the new closures landed

```bash
.venv/bin/apecx-lookup CHIKV --json | jq '.canonical_iri'
# "http://purl.obolibrary.org/obo/NCBITaxon_37124"
.venv/bin/apecx-lookup HSV-2 --json | jq '.canonical_iri'
# "http://purl.obolibrary.org/obo/NCBITaxon_10298"
```

If the new closures don't resolve, the ingest fired but the
`inverse_index` row isn't there. Check the JSONL contents AND check
the `entries` table — `missing_entries` >0 in the summary means the
mined taxon ID isn't in the canonical NCBI subset of the dict; either
walk `merged_taxons` (taxon was renamed) or accept the gap.

## Step 4 — Publish

```bash
# From the apecx-mcp-integration repo root.
cd apecx-mcp-integration

# Dry-run (no upload — just stage):
.venv/bin/python scripts/publish_dictionary.py --staging-dir /tmp/dict_publish

# Inspect staged files:
ls -lh /tmp/dict_publish/
# dictionary-sc-a4c-2026-06-08.sqlite.gz   45 MB
# MANIFEST.json                            ~400 B

# Direct HTTPS PUT to the collection (requires confidential-client creds):
export GLOBUS_COMPUTE_CLIENT_ID=<your client_id>
export GLOBUS_COMPUTE_CLIENT_SECRET=<your client_secret>
.venv/bin/python scripts/publish_dictionary.py \
    --staging-dir /tmp/dict_publish \
    --upload
```

The `--upload` path does three things, in order:

1. Calls Globus Auth's `client_credentials` grant to acquire ONE token
   pair: a Transfer-API token AND an HTTPS-data-access token for the
   collection (single round trip).
2. Calls Transfer API `GET /endpoint/<uuid>` to discover the
   collection's `https_server` URL (so we don't have to hardcode it
   in the script — it travels with the collection's GCS config).
3. Issues two HTTPS PUTs to `${https_server}/<dest_path>/` — one for
   the gz and one for the MANIFEST.json — each with the
   `Authorization: Bearer <https_token>` header.

What's NOT involved: no local Globus Connect Personal endpoint, no
endpoint pairing, no Transfer task, no Globus Connect Server
filesystem mount on your machine. The 45 MB upload completes in
roughly the time it takes your network to push that much data to ANL
(typically <60 s on a residential connection, <10 s on Argonne's
campus network).

## Step 5 — Verify the publish

```bash
# From any user-facing arm install (or your own).
# URL verified live 2026-06-08 against APECx Data at Argonne LCF
# collection 8d2e71d6-7a29-41d9-94e5-38d8a95fa5db:
export APECX_DICT_PUBLIC_BASE_URL=https://g-958ce2.fd635.8443.data.globus.org/apecx-ramanathan-anl/public/synonyms_dictionary

apecx-dict-update --check-only
# local:     sc-a4c-2026-06-08
# published: sc-a4c-2026-06-08
# schema:    1.0.0 (supported major: (1,))
# built_at:  2026-06-08T19:59:31.263876Z
# file:      dictionary-sc-a4c-2026-06-08.sqlite.gz (46,955,332 bytes, compression=gzip)
# status: up-to-date
```

If the manifest is reachable but `status: update available` shows
stale, the publish silently failed — re-run step 4 and check the
Globus task log.

## Failure modes

| Symptom | Diagnosis | Fix |
|---|---|---|
| `missing_entries > 0` in step 2 | Mined taxon was renamed by NCBI | Walk `merged_taxons` in your JSONL emitter |
| `sha256 mismatch` at bootstrap | Stale manifest, partial upload, or corrupted file | Re-run step 4 |
| `apecx-dict-update` 404 | `APECX_DICT_PUBLIC_BASE_URL` is wrong | Confirm the HTTPS host the Argonne collection exposes |
| Upload returns HTTP 401/403 | Confidential client lacks consent for the collection's HTTPS scope | Grant consent on https://app.globus.org/settings/developers (Transfer scope + per-collection HTTPS scope) |
| Upload returns HTTP 404 on PUT | Destination path doesn't exist and collection doesn't auto-mkdir | Create `/apecx-ramanathan-anl/public/synonyms_dictionary/` once via Globus web app (or `globus mkdir`) |
| Schema version mismatch error | Reader doesn't support the major version | Upgrade `apecx-harvesters` on the user-facing side BEFORE re-publishing |

## What the runbook deliberately omits

- **Mining from scratch on a fresh checkout.** The runbook assumes the
  enriched CSVs exist. Generating them is the harvester chain
  (`apecx_harvesters.scripts.aggregate_gsearch`).
- **Rebuilding the dict from NCBI names.dmp.** That's
  `apecx_integration.synonym_dictionary.workflow.bootstrap`. Only
  needed once; subsequent updates are delta-mined via the pipeline
  above.
- **HTTPS ACLs on the Globus public path.** Assumed pre-configured by
  the Ramanathan group's Globus admin. If you can't `curl` MANIFEST.json
  anonymously, the ACL is wrong; talk to whoever administers the
  collection.
