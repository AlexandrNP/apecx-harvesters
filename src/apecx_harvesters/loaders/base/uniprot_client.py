from __future__ import annotations

import logging
import re

import httpx

from .http_retry import http_request
from .rate_limit import RateLimiter

log = logging.getLogger(__name__)

_STREAM_URL = "https://rest.uniprot.org/uniprotkb/stream"
# Accessions per OR-query. UniProt 400s an over-long `accession:(A OR B OR …)`
# query well before the URL length limit (≥150 terms fails; ~20 is safe), so
# keep this conservative; `_resolve_chunk` bisects on a 400 as a safety net.
_CHUNK_SIZE = 40
_DEFAULT_REQUESTS_PER_SECOND = 5.0

# Canonical UniProtKB accession syntax. The `accession:` query field rejects a
# malformed token with HTTP 400, poisoning the whole chunk, so we drop anything
# that isn't accession-shaped up front — it could never resolve anyway.
_ACCESSION_RE = re.compile(
    r"[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}"
)


def split_accession_field(value: str) -> list[str]:
    """Split one identifier field that may pack several accessions into a single
    string (ProtaBank does this, e.g. ``"P04608; Q72501; Q900A7"``) into the
    individual accession-shaped tokens. Non-accession tokens are dropped."""
    return [
        t for t in re.split(r"[;,\s]+", value.strip()) if t and _ACCESSION_RE.fullmatch(t.upper())
    ]


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _parse_tsv(text: str) -> dict[str, int]:
    """Parse a `Entry\\tOrganism (ID)` TSV into a {uppercased accession: taxid} map."""
    resolved: dict[str, int] = {}
    lines = text.splitlines()
    for row in lines[1:]:  # skip header
        accession, _, taxid = row.partition("\t")
        if not accession or not taxid:
            continue
        try:
            resolved[accession.upper()] = int(taxid)
        except ValueError:
            log.debug("non-integer taxid for %s: %r", accession, taxid)
    return resolved


async def resolve_uniprot_taxids(
    accessions: list[str],
    *,
    client: httpx.AsyncClient | None = None,
    rate_limiter: RateLimiter | None = None,
) -> dict[str, int]:
    """Map each UniProt accession -> NCBI organism taxid via the UniProt REST stream API.
    Accessions that don't resolve (obsolete/secondary/deleted) are simply absent from the result
    (logged at debug). Deduplicates input; chunks to keep URLs bounded."""
    unique = list(dict.fromkeys(accessions))
    queryable = [a for a in unique if _ACCESSION_RE.fullmatch(a.upper())]
    for dropped in set(unique) - set(queryable):
        log.debug("skipping non-accession-shaped token: %s", dropped)
    if not queryable:
        return {}

    if rate_limiter is None:
        rate_limiter = RateLimiter(_DEFAULT_REQUESTS_PER_SECOND, name="uniprot")

    owned = client is None
    if owned:
        client = httpx.AsyncClient()
    by_upper: dict[str, int] = {}

    async def _resolve_chunk(chunk: list[str]) -> None:
        query = "accession:(" + " OR ".join(chunk) + ")"
        try:
            response = await http_request(
                client,
                "GET",
                _STREAM_URL,
                rate_limiter=rate_limiter,
                params={"query": query, "fields": "accession,organism_id", "format": "tsv"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # A 5xx that survived http_request's retries is a real outage — re-raise
            # so a mid-republish storm fails loud instead of silently under-stamping.
            if exc.response.status_code >= 500:
                raise
            # A 4xx (over-long OR query, or a token UniProt rejects) poisons the
            # whole chunk. Bisect to isolate the failure and still make progress;
            # a lone token that keeps 4xx-ing is dropped.
            if len(chunk) == 1:
                log.debug("UniProt rejected accession query for %s", chunk[0])
                return
            mid = len(chunk) // 2
            await _resolve_chunk(chunk[:mid])
            await _resolve_chunk(chunk[mid:])
            return
        by_upper.update(_parse_tsv(response.text))

    try:
        for chunk in _chunks(queryable, _CHUNK_SIZE):
            await _resolve_chunk(chunk)
    finally:
        if owned:
            await client.aclose()

    result: dict[str, int] = {}
    for accession in unique:
        taxid = by_upper.get(accession.upper())
        if taxid is None:
            log.debug("UniProt accession did not resolve to a taxid: %s", accession)
        else:
            result[accession] = taxid
    return result
