# User-facing arm install guide

This document is for someone who wants to **query** the apecx synonym
dictionary without ever running a harvester, miner, or ingest. If you
are building the dictionary, read `backend_arm_runbook.md` instead.

## What you're installing

A ~1,000-line Python library plus two CLIs. No nanobrain, no FAISS,
no LLM client, no MCP server, no pandas. Runtime dependencies: pydantic
and the Python standard library. The shipped artifact (the dictionary
SQLite) is ~250 MB on disk and ~45 MB compressed in transit.

## Step 1 — Install the reader

Until the upstream PR to `abought/apecx-harvesters` is merged, install
from the fork that carries the harmonization work:

```bash
# Recommended for users (read-only):
pip install 'apecx-harvesters[reader] @ git+https://github.com/AlexandrNP/apecx-harvesters.git@main'

# Or pin to the feature branch for reproducibility:
pip install 'apecx-harvesters[reader] @ git+https://github.com/AlexandrNP/apecx-harvesters.git@feature/globus-index-harmonization'
```

Once the upstream PR is merged, this simplifies to:

```bash
pip install 'apecx-harvesters[reader]'
```

This installs:

- The `apecx_harvesters.dict_reader` library (importable from your code).
- The `apecx-lookup` CLI for ad-hoc term resolution.
- The `apecx-dict-update` CLI for fetching/updating the dictionary
  from the published Globus path.

## Step 2 — Point the bootstrap at the published Globus path

The bootstrap fetches over anonymous HTTPS from the
"APECx Data at Argonne LCF" Globus collection. There is **no hardcoded
URL** — you must set the base URL once.

```bash
export APECX_DICT_PUBLIC_BASE_URL=https://g-958ce2.fd635.8443.data.globus.org/apecx-ramanathan-anl/public/synonyms_dictionary
```

Persist this in your shell rc (`~/.zshrc`, `~/.bashrc`, etc.) so you
don't have to re-export it.

This URL is the HTTPS-server hostname assigned by Globus Connect Server
to the "APECx Data at Argonne LCF" collection
(UUID `8d2e71d6-7a29-41d9-94e5-38d8a95fa5db`), suffixed with the
publish path. The host may change if the collection is redeployed —
re-discover with `globus collection show 8d2e71d6-7a29-41d9-94e5-38d8a95fa5db`
if the bootstrap returns DNS errors.

## Step 3 — Bootstrap the local dictionary

```bash
apecx-dict-update
# downloading https://.../dictionary-sc-a4c-2026-06-04.sqlite.gz
#   2,359,296 / 46,955,332 bytes (5%)
#   ...
#   46,955,332 / 46,955,332 bytes (100%)
# decompressing...
# dictionary updated to version sc-a4c-2026-06-04 at ~/.apecx/dictionary/dictionary.sqlite
```

This places the file at `~/.apecx/dictionary/dictionary.sqlite`.
Future runs of the CLI or library calls find it there automatically.

## Step 4 — Verify by resolving a known term

```bash
apecx-lookup CHIKV --json
# {
#   "surface_form": "CHIKV",
#   "path": "fast",
#   "canonical_iri": "http://purl.obolibrary.org/obo/NCBITaxon_37124",
#   "canonical_label": "Chikungunya virus",
#   ...
# }
```

If you see `path: "miss"` for a known acronym, the dictionary you
downloaded predates the SC-B7/B8 mining. Re-run `apecx-dict-update`.

## Using the library from Python

```python
from apecx_harvesters.dict_reader import (
    configure_dictionary_path,
    default_dictionary_path,
    lookup_entity,
)

# One-time setup (process-singleton):
configure_dictionary_path(default_dictionary_path())

# All later calls hit the in-memory index:
result = lookup_entity("EEEV")
print(result.path)              # "fast"
print(result.canonical_iri)     # "http://purl.obolibrary.org/obo/NCBITaxon_11021"
print(result.canonical_label)   # "Eastern equine encephalitis virus"

# Ambiguous handling:
result = lookup_entity("RSV")
if result.path == "ambiguous":
    for cand in result.candidates:
        print(cand.canonical_label, cand.canonical_iri)
```

The full result schema is `LookupResult` from
`apecx_harvesters.dict_reader.lookup`.

## Updating

Updates are **manual**. Reasons documented in
`two_arm_contract.md#update-policy`.

```bash
apecx-dict-update --check-only
# local:     sc-a4c-2026-06-04
# published: sc-a4c-2026-06-04
# status: up-to-date

# When an update is available:
apecx-dict-update
# (downloads the new gz, sha-verifies, atomically replaces local)

# Force re-download even when local matches:
apecx-dict-update --force
```

## Using the agent-skill instead

If you're using Claude Code, the
`search_demo/agent-skill-harmonized/` skill in the apecx-harvesters
repo wraps the reader in a Globus-Search-aware query layer. It
inherits the same install: as long as `apecx-harvesters[reader]` is
on PATH and the dict is bootstrapped, the skill works.

## What you don't need

Common confusions worth pre-empting:

- **`apecx-mcp-integration`** — only required for the backend ingest +
  publish. The reader doesn't import any of its code.
- **Enriched CSVs at `~/.apecx/dictionary/enriched/`** — backend-only.
  Lookup works without them.
- **The 9 raw VIOLIN CSVs** — backend-only.
- **Globus credentials** — the bootstrap uses anonymous HTTPS, no
  auth needed. Globus credentials are required only for the backend
  publish step.

## When it doesn't work

| Symptom | Diagnosis | Fix |
|---|---|---|
| `apecx-dict-update` says "APECX_DICT_PUBLIC_BASE_URL not set" | Step 2 was skipped | `export APECX_DICT_PUBLIC_BASE_URL=...` |
| Download fails with 404 | Wrong base URL or the collection moved | Confirm the path with the admin |
| Download fails with 403 | The Globus collection's HTTPS ACL requires auth | Contact the collection admin; the public path should be anonymous-readable |
| sha256 mismatch | Publish-side corruption or stale manifest | `apecx-dict-update --force` |
| `path: "miss"` for known terms | Stale local dict | `apecx-dict-update --force` |
| Schema version error at install | Reader is older than the published dict | `pip install --upgrade 'apecx-harvesters[reader]'` |

## Footprint

After install + bootstrap:

- ~5 MB Python (the reader library + pydantic).
- ~250 MB disk (`~/.apecx/dictionary/dictionary.sqlite`).
- Network: one ~45 MB download per dictionary version you decide to
  install. No background traffic; the reader never phones home.
