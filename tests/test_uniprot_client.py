"""Live integration test for the UniProt accession -> taxid resolver.

Hits the real UniProt REST API; skips gracefully when the network is unavailable.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from apecx_harvesters.loaders.base.uniprot_client import (
    resolve_uniprot_taxids,
    split_accession_field,
)


def test_split_accession_field():
    """Pure (no network): split a packed UniProt id field into accession-shaped
    tokens, dropping anything that isn't accession-shaped."""
    assert split_accession_field("P04608; Q72501; Q900A7") == [
        "P04608",
        "Q72501",
        "Q900A7",
    ]
    assert split_accession_field("P03452") == ["P03452"]
    assert split_accession_field("") == []
    assert split_accession_field("not an accession") == []


def _resolve(accessions: list[str]) -> dict[str, int]:
    try:
        return asyncio.run(resolve_uniprot_taxids(accessions))
    except httpx.TransportError as exc:
        pytest.skip(f"UniProt API unreachable: {exc}")


def test_resolves_known_accessions_to_taxids():
    # P03452 = influenza A/Puerto Rico/8/1934 (H1N1); P27958 = HCV genotype 1a
    result = _resolve(["P03452", "P27958"])

    assert result == {"P03452": 211044, "P27958": 63746}


def test_unresolvable_accession_is_absent():
    result = _resolve(["P03452", "ZZZ999"])

    assert result == {"P03452": 211044}


# ---------------------------------------------------------------------------
# FIX 2: HTTP status handling. These mock the module-level `http_request` (the
# external HTTP boundary the client calls). Integration parity for this boundary
# is provided by the live-UniProt tests above (test_resolves_known_accessions_*,
# test_unresolvable_accession_is_absent), which exercise the real REST API.
# ---------------------------------------------------------------------------


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://rest.uniprot.org/uniprotkb/stream")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"{status_code}", request=request, response=response
    )


def test_5xx_re_raises_does_not_swallow():
    """A 503 that survived http_request's retries is a real outage — it must
    RE-RAISE so a mid-republish storm fails loud, not silently under-stamp by
    returning {}."""
    with patch(
        "apecx_harvesters.loaders.base.uniprot_client.http_request",
        new=AsyncMock(side_effect=_status_error(503)),
    ):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            asyncio.run(resolve_uniprot_taxids(["P03452"]))
    assert exc_info.value.response.status_code == 503


def test_4xx_single_token_is_handled_gracefully():
    """A 400 on a lone token poisons only that token: the bisect bottoms out at
    a single accession, drops it, and returns {} WITHOUT raising."""
    with patch(
        "apecx_harvesters.loaders.base.uniprot_client.http_request",
        new=AsyncMock(side_effect=_status_error(400)),
    ):
        result = asyncio.run(resolve_uniprot_taxids(["P03452"]))
    assert result == {}
