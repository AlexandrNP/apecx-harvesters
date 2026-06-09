# Two-arm contract — synonym dictionary publish + consume

**Status**: shipped 2026-06-08. Owns the source-of-truth model
(decision (c) from the 2026-06-08 discussion: published dict is a
release artifact with its own version; local dict is dev sandbox;
tests + applications use (a)-semantics — treat local as canonical,
published as a snapshot to pull).

## Architectural split

The synonym dictionary lifecycle is split into **two independent
arms** that communicate through a shared on-disk artifact:

```
                +---------------------------+
                |    BACKEND ARM             |
                |  (apecx-harvesters)        |
                |                            |
                |  enriched CSVs             |
                |    -> SC-B7/B8 mining      |
                |    -> JSONL sidecar        |
                |    -> SC-B4 ingest         |
                |    -> ~/.apecx/...sqlite  |
                |    -> publish_dictionary.py
                |    -> Globus Transfer      |
                +-------------+--------------+
                              |
                              v
           +----------------------------------------+
           |  /apecx-ramanathan-anl/public/         |
           |    synonyms_dictionary/                |
           |      MANIFEST.json                     |
           |      dictionary-<version>.sqlite.gz    |
           +----------------+-----------------------+
                            |
                            v
                +---------------------------+
                |    USER-FACING ARM         |
                |  (apecx-harvesters[reader])|
                |                            |
                |  apecx-dict-update         |
                |    -> HTTPS GET MANIFEST   |
                |    -> HTTPS GET .gz        |
                |    -> sha256 verify        |
                |    -> decompress           |
                |    -> ~/.apecx/...sqlite  |
                |                            |
                |  apecx-lookup TERM         |
                |  harmonized_query.py       |
                +---------------------------+
```

**The two arms share nothing except the contract documented here.**
The user-facing arm has zero dependency on nanobrain, FAISS, LLM
wrappers, MCP server, or pandas. Its install footprint is
`apecx-harvesters[reader]` which is pydantic + stdlib only.

## File contract at the public path

The published directory `/apecx-ramanathan-anl/public/synonyms_dictionary/`
contains exactly two files:

| File | Purpose | Size |
|---|---|---|
| `MANIFEST.json` | Discovery sidecar — points at the dict file + sha256 + size + version | ~400 bytes |
| `dictionary-<version>.sqlite.gz` | gzip-compressed dict, content-addressed by version string | ~45 MB |

The MANIFEST.json schema is **stable** — adding optional fields is
allowed; renaming or removing fields is a major-version bump on
`schema_version`.

```json
{
  "schema_version": "1.0.0",
  "dictionary_version": "sc-a4c-2026-06-08",
  "built_at": "2026-06-08T19:59:31.263876Z",
  "dictionary_filename": "dictionary-sc-a4c-2026-06-08.sqlite.gz",
  "dictionary_sha256": "dd65d202df79aedbdf3644e284d3c245bfcdf1567c71652d11717f4103400cb6",
  "dictionary_size_bytes": 46955332,
  "compression": "gzip",
  "published_at": "2026-06-09T01:22:00.061846+00:00"
}
```

| Field | Required | Notes |
|---|---|---|
| `schema_version` | yes | Major version: the reader's `SUPPORTED_SCHEMA_MAJOR` must contain it. |
| `dictionary_version` | yes | Backend's `built_at`-derived ID. Used by the user-facing arm to detect "already current". |
| `built_at` | yes | When the backend ingest finished. Informational. |
| `dictionary_filename` | yes | The companion file at the same public path. |
| `dictionary_sha256` | yes | Reader refuses to install a download whose sha differs. |
| `dictionary_size_bytes` | yes | Used for progress display. Reader checks final size. |
| `compression` | yes | `"gzip"` or `"none"`. Reader refuses unknown values. |
| `published_at` | yes | Distinct from `built_at` — when the file was UPLOADED. |

## Schema-version compatibility rule

The reader's `SUPPORTED_SCHEMA_MAJOR` is `(1,)`. The bootstrap refuses
to install a published dict whose major version isn't in this tuple.
The SQLite reader itself ALSO refuses at load time, so even a
manually-placed dict can't bypass the check.

When the backend ships a v2 dict, the user-facing arm MUST be
upgraded first — a v1 reader cannot load a v2 dict and will fail
loudly rather than silently miss every query.

## Source-of-truth (decision (c))

**Architecturally**: both dicts are legitimate sources of truth at
different lifecycle stages.

| Context | Source of truth | Rationale |
|---|---|---|
| Mining iteration | `~/.apecx/dictionary/dictionary.sqlite` (LOCAL) | Dev cycle; mining changes go here first. |
| Tests about mining changes | LOCAL | Tests of NEW mining work must read the changed dict. |
| Tests NOT about mining | PUBLISHED (consumed via local bootstrap) | Same release everyone else gets. |
| Applications | PUBLISHED (consumed via local bootstrap) | Production users see what was released. |
| Release | `publish_dictionary.py` promotes LOCAL → PUBLISHED | The release tag. |

**Operationally**: the LOCAL path is the same in both cases —
`~/.apecx/dictionary/dictionary.sqlite`. The bootstrap downloads into
that path, the backend mines into that path. The difference is who
WROTE it: the bootstrap (consumer) or the ingest (producer).

A test that runs `apecx-dict-update` first guarantees it's reading
the published version. A test that runs an SC-B mining pass first
guarantees it's reading the dev version. They can't both be true at
the same time in the same checkout, by design.

## Update policy

Manual. Operators run `apecx-dict-update` to refresh.

The user-facing arm does NOT auto-poll. Reasons:

1. **Reproducibility** — a biomedical literature search done at 10:00
   AM should give the same answer at 11:00 AM, even if the backend
   shipped a new dict mid-search. The auto-update model would silently
   change the harmonization layer under the user's feet.
2. **Latency budget** — every query check would add an HTTPS round
   trip; bad for interactive use.
3. **Privacy** — auto-poll leaks the user's query pattern to whoever
   logs the MANIFEST.json fetches.

Trade: the user is responsible for noticing when an upstream fix
matters to them.

## File-path layout (both arms)

| Path | Owner | Description |
|---|---|---|
| `~/.apecx/dictionary/dictionary.sqlite` | both | The canonical local path. Backend writes here during ingest; bootstrap writes here on `apecx-dict-update`. |
| `~/.apecx/dictionary/enriched/*.csv` | backend only | Mining inputs. NOT needed by user-facing arm. |
| `/tmp/dict_publish/` (operator-chosen) | backend transient | Stage directory for compress + manifest before Globus upload. |
| `${APECX_DICT_PUBLIC_BASE_URL}/MANIFEST.json` | published | Operator-controlled HTTPS URL pointing at the public Globus path. |

## What this contract does NOT cover

These are deliberate exclusions:

- **Dict-rebuild from scratch.** The backend assumes the dict exists
  in some form before ingest. Building a brand-new dict from NCBI's
  `names.dmp` + the enriched CSVs is a different pipeline
  (`apecx_integration.synonym_dictionary.build.build_dictionary`) and
  isn't shipped through this two-arm contract.
- **Cross-collection redundancy.** Today there is one published path.
  No mirror, no fallback URL. If the Globus collection is unreachable
  the user-facing arm degrades to whatever local dict is already
  installed.
- **Provisional synonyms.** The `ProvisionalSynonym` schema exists for
  future user-submitted candidates but is NOT exercised by this
  contract.
- **Cross-version reads.** A user-facing arm that pre-dates the
  published `schema_version` simply refuses to bootstrap. There is no
  silent downgrade.

## Reader-side library footprint

The thin reader (`apecx-harvesters[reader]`) is exactly:

```
apecx_harvesters/dict_reader/
├── __init__.py        public API surface
├── bootstrap.py       Globus HTTPS download + decompress + sha verify
├── cli.py             apecx-lookup + apecx-dict-update entry points
├── enums.py           EntityType / ResolutionStatus / OntologyName
├── loader.py          DictionaryIndex + process singleton
├── lookup.py          lookup_entity() + LookupResult
├── normalization.py   surface-form normalizer
├── schema.py          pydantic DictionaryEntry + BuildManifest
└── sqlite_reader.py   read-only SQLite access
```

**Total**: ~1,000 lines of Python. Runtime deps: `pydantic>=2.12` and
the stdlib. That's it.

Install URL while the harmonization PR is unmerged upstream:

```bash
pip install 'apecx-harvesters[reader] @ git+https://github.com/AlexandrNP/apecx-harvesters.git@main'
```

The fork at `AlexandrNP/apecx-harvesters` carries the dict_reader +
corpus mining + agent-skill that this contract documents. Once
`abought/apecx-harvesters` merges, the install simplifies to
`pip install 'apecx-harvesters[reader]'`.

**Note 2026-06-09**: the previous `[mcp]` extra and the standalone
`apecx-mcp-reader` server were removed because they duplicated tools
already exposed by the canonical `apecx-mcp` server in
`apecx-mcp-integration` (`resolve_canonical_entity`,
`query_globus_search`, etc.). MCP access to the harmonization layer
now goes through that canonical server.

## Tests pinning the contract

| Test | What it pins |
|---|---|
| `tests/test_dict_reader.py::test_normalize_idempotent` | Normalizer round-trip |
| `tests/test_dict_reader.py::test_parity_with_apecx_lookup[*]` | Reader output identical to upstream `apecx-lookup` CLI |
| `tests/test_dict_reader.py::test_reader_rejects_unsupported_schema_version` | v2 dict against v1 reader fails loudly |
| `tests/test_dict_bootstrap.py::test_bootstrap_dictionary_fresh` | End-to-end download + sha verify + decompress |
| `tests/test_dict_bootstrap.py::test_bootstrap_dictionary_idempotent` | No-op when local matches published |
| `tests/test_dict_bootstrap.py::test_bootstrap_atomic_replace` | Partial download never visible at canonical path |
| `tests/test_dict_bootstrap.py::test_get_public_base_url_requires_config` | No silent default URL |

Any change that breaks the contract MUST update or delete the
corresponding test, with the change explained in the commit body.
