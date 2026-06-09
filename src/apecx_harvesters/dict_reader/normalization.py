"""Surface-form normalization for the apecx synonym dictionary.

Both build-time (apecx-harvesters mining + ingest) and runtime (user-input
lookup) MUST use this same function — divergence here is silent breakage
of the lookup path.

Ported verbatim from ``apecx_integration.synonym_dictionary.normalization``
(2026-06-04). When the upstream module changes, this file must change
in lockstep — pinned by the dict_reader test
:func:`test_normalize_matches_apecx_integration`.
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RUN = re.compile(r"\s+")
_SURROUND_PUNCT = re.compile(r"^[\s()\[\]{}\"'`]+|[\s()\[\]{}\"'`]+$")


def normalize_surface_form(s: str) -> str:
    """Canonicalize a surface form for lookup-key purposes.

    Steps:
    1. Unicode NFKC compose.
    2. ``str.casefold()`` — handles non-ASCII case (Greek beta, German ß).
    3. Strip surrounding whitespace + brackets + quotes.
    4. Collapse runs of internal whitespace to a single space.

    Idempotent: ``normalize(normalize(s)) == normalize(s)``.
    """
    if not s:
        return ""
    nfkc = unicodedata.normalize("NFKC", s)
    folded = nfkc.casefold()
    stripped = _SURROUND_PUNCT.sub("", folded)
    collapsed = _WHITESPACE_RUN.sub(" ", stripped).strip()
    return collapsed
