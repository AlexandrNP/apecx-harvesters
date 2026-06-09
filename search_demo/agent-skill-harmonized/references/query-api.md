# Globus Search Query API Reference

The query script accepts a JSON payload and an index ID. This document covers how to construct that payload and interpret the result.

## Request structure

```json
{
  "q": "search terms",
  "filters": [...],
  "facets": [...],
  "post_facet_filters": [...],
  "boosts": [...],
  "sort": [...],
  "limit": 200,
  "offset": 0
}
```

`q` or `filters` is required; both may be used together. Max 10,000 results per request.  
`post_facet_filters` uses the same syntax as `filters` but is applied after facet counts are computed, so bucket counts reflect the unfiltered result set.  
`boosts` and `sort` are mutually exclusive.

## Query modes (q_settings)

```json
{"mode": "query_string",          "default_operator": "or" | "and"}
{"mode": "advanced_query_string", "default_operator": "or" | "and"}
{"mode": "text_match",            "default_operator": "or" | "and", "fuzziness": 0-5}
```

`advanced_query_string` supports boolean operators (`AND`, `OR`, `NOT`, parentheses) and Lucene-style field-targeted syntax (e.g. `pdb.method:X-RAY\ DIFFRACTION`), which allows addressing specific fields directly in the query string.  
`text_match` enables fuzzy matching (edit distance).  
Omit `q_settings` for default full-text search.

As a shorthand, `"advanced": true` is equivalent to `q_settings: {mode: "advanced_query_string"}` and is mutually exclusive with `q_settings`.

## Filters

### match_any / match_all
```json
{"type": "match_any", "field_name": "publisher.name", "values": ["RCSB PDB"]}
{"type": "match_all", "field_name": "subjects.subject", "values": ["kinase", "inhibitor"]}
```

### range
```json
{"type": "range", "field_name": "publicationYear", "values": [{"from": "2020", "to": "2025"}]}
```
Use `"*"` for open-ended bounds. Supports dates and numbers.

### exists
```json
{"type": "exists", "field_name": "pdb.method"}
```

### like (wildcard)
```json
{"type": "like", "field_name": "titles.title", "value": "SOD*"}
```
`*` = any characters, `?` = single character.

### Logical combinators

Wrap any filter object (or array of them) to combine conditions:
```json
{"type": "not", "filter": {"type": "match_any", ...}}
{"type": "and", "filters": [{"type": "range", ...}, {"type": "exists", ...}]}
{"type": "or",  "filters": [{"type": "match_any", ...}, {"type": "like", ...}]}
```
Combinators can be nested arbitrarily.

## Facets

Useful for counting distinct values across a result set — e.g. tag/keyword frequency, year distribution.

### terms
```json
{"name": "by_method", "type": "terms", "field_name": "pdb.method", "size": 20}
```

### date_histogram
```json
{
  "name": "by_year",
  "type": "date_histogram",
  "field_name": "publicationYear",
  "date_interval": "year"
}
```
Intervals: `year`, `quarter`, `month`, `week`, `day`.

### numeric_histogram
```json
{
  "name": "by_resolution",
  "type": "numeric_histogram",
  "field_name": "pdb.resolution_angstrom",
  "size": 10,
  "histogram_range": {"low": 0, "high": 5}
}
```

### sum / avg
```json
{"name": "avg_resolution", "type": "avg", "field_name": "pdb.resolution_angstrom"}
```

`post_facet_filters` applies additional filters after facet counting (same syntax as `filters`).

## Boosts

Increase or decrease relevance of results where a field has higher values. Ignored when `sort` is specified.

```json
"boosts": [{"field_name": "publicationYear", "factor": 2.0}]
```

Factor > 1 increases relevance; factor < 1 decreases it. Range: 0–10.

## Sorting

```json
"sort": [{"field_name": "publicationYear", "order": "asc" | "desc"}]
```

Multi-valued fields: ascending uses minimum value, descending uses maximum.

## Pagination

Results are capped at 10,000 per request. Use `limit` and `offset` to page; the response includes `total` (all matches), `count` (returned this page), and `has_next_page`. The query script handles scrolling for large result sets automatically.

## Result completeness

When `total > count`, the result is truncated. If completeness matters, either increase `limit`, add filters to narrow the result set, or use scroll mode.
## Harmonization patterns (APECx-specific)

The APECx harmonized indices ship `subjects.valueUri` populated with NCBI
Taxonomy canonical IRIs. The patterns below leverage that.

### Filter all records anchored to a specific taxon

```json
{
  "filters": [{
    "type": "match_any",
    "field_name": "subjects.valueUri",
    "values": ["http://purl.obolibrary.org/obo/NCBITaxon_11021"]
  }],
  "limit": 200
}
```

### Hybrid query: harmonized OR raw

When the resolved IRI may not cover the entire intent (e.g., long-tail
strain isolates that didn't get fully cross-walked), union the
harmonized filter with a raw text match:

```json
{
  "q": "EEEV",
  "filters": [{
    "type": "or",
    "filters": [
      {"type": "match_any", "field_name": "subjects.valueUri",
       "values": ["http://purl.obolibrary.org/obo/NCBITaxon_11021"]},
      {"type": "like", "field_name": "bvbrc_genome.Genome_Name", "value": "*EEEV*"}
    ]
  }],
  "limit": 200
}
```

### AMBIGUOUS — multi-candidate filter

When the user-supplied surface form resolves to multiple candidate IRIs
(e.g., `RSV` → 6 candidates), match any of them and surface the result
set partitioned by IRI so the consumer can choose:

```json
{
  "filters": [{
    "type": "match_any",
    "field_name": "subjects.valueUri",
    "values": [
      "http://purl.obolibrary.org/obo/NCBITaxon_11246",
      "http://purl.obolibrary.org/obo/NCBITaxon_11250",
      "http://purl.obolibrary.org/obo/NCBITaxon_11886"
    ]
  }],
  "facets": [{
    "name": "by_iri", "type": "terms",
    "field_name": "subjects.valueUri", "size": 10
  }],
  "limit": 1000
}
```

The `by_iri` facet gives per-IRI counts so the UI can prompt the user
to pick the relevant taxon.

### Per-source provenance

Records carry `publisher.name` ∈ {"VIOLIN", "BV-BRC", "AntiviralDB",
"ProtaBank"}. Combine with the harmonized filter to slice by source:

```json
{
  "filters": [
    {"type": "match_any", "field_name": "subjects.valueUri",
     "values": ["http://purl.obolibrary.org/obo/NCBITaxon_37124"]},
    {"type": "match_any", "field_name": "publisher.name", "values": ["BV-BRC"]}
  ],
  "limit": 100
}
```
