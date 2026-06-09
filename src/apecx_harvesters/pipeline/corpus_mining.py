"""SC-B1 (2026-06-08) — corpus-mined synonym observations + accumulator.

Backs the synonym-completeness plan's Phase B (``SYNONYM_COMPLETENESS_PLAN.md``
in apecx-mcp-integration design package). The accumulator is fed by
parser-lift over every harvested record; each ``(surface_form, taxon_id)``
pair the parser sees becomes an observation. The accumulator dedups by
``(surface_form, taxon_id)`` and tracks per-source counts so the dictionary
build (SC-B4) can ingest observations with provenance + corroboration.

This module is intentionally I/O-free: no SQLite, no Globus, no parser
imports. The mining hook is just ``accumulator.observe(surface_form,
taxon_id, source)`` — call it from anywhere with the inputs.

Conflict-HITL semantics (SC-B3) are NOT enforced here: the accumulator
records every observation faithfully. When the same surface form
appears for multiple distinct taxon ids, the accumulator surfaces all
of them via :meth:`MinedSynonymAccumulator.surface_form_conflicts`;
SC-B3's downstream consumer decides whether to (a) ingest the most-
corroborated as the winner and the rest as ``alternative_canonical_iri``
rows, OR (b) route the whole conflict to HITL. That policy lives in
the dictionary build, not here.

Cross-references (canonical source of truth for the contract):
- ``apecx-harvesters-work/design/SYNONYM_COMPLETENESS_PLAN.md`` §SC-B
- Synonym dictionary plan: ``apecx-mcp-integration`` §3.3 conflict
  policy (HITL on cross-source conflicts), §4 confidence model.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

# Surface forms shorter than 2 chars or matching a "pure noise" pattern
# are dropped before they reach the accumulator. Avoids polluting the
# observation set with single-letter pandas-NaN coercion artifacts,
# integer-only strings (accession suffixes), etc.
_MIN_SURFACE_LEN = 2
_NOISE_PATTERNS = (
    re.compile(r"^\s*$"),         # whitespace-only
    re.compile(r"^\d+\.?\d*$"),   # pure numeric ("123" or "123.0")
    re.compile(r"^(nan|none|null|na|n/a)$", re.IGNORECASE),  # pandas/sql null sentinels
)


# ---------------------------------------------------------------------------
# SC-B5 (2026-06-08): strain-level descriptor detection.
#
# BVBRC's ``Genome_Name`` and similar fields carry hundreds of thousands of
# unique strings that are NOT pathogen synonyms but strain isolates:
#
#   "Influenza A virus (A/common pochard/Shanxi/16B/2015(H5N1))"
#   "Porcine reproductive and respiratory syndrome virus isolate 14LY01-FJ"
#   "Human alphaherpesvirus 1 strain KOS"
#   "KP715069 Missing Influenza A virus (A/common pochard/...)"
#
# Mining these as synonyms of the species would pollute the inverse_index
# with surface forms users never type. The plan (§SC-B5) explicitly carves
# them out. Detection heuristics:
#
#   - Length > 60 chars AND contains '(' / '/' → likely strain isolate.
#   - Matches "<species> strain <id>" / "isolate <id>" / "subsp. <id>".
#   - Starts with a NCBI / GenBank accession prefix (2 letters + 6+ digits).
#   - Flu-style strain string: ``A/<host>/<location>/<isolate>/<year>``
#     anywhere in the string.
#
# The heuristics are intentionally conservative: false negatives (a strain
# string that sneaks through) cost one polluted synonym; false positives
# (a real species name caught as strain) cost a missing synonym. With 281k
# real entries we can absorb the FN cost; the FP cost is one missing
# (typically obscure) species name in the dictionary.
# ---------------------------------------------------------------------------

_STRAIN_MAX_PARENS_LEN = 60
# Note the trailing ``\s+\S+`` and no closing ``\b``: ``subsp.`` has a
# trailing period which does NOT create a word boundary with the
# following space, so a final ``\b`` would refuse to match. The space
# requirement (``\s+``) handles the boundary.
_STRAIN_KEYWORDS = re.compile(
    r"\b(strain|isolate|subspecies|clone|sub-?type|variant)\b\s+\S+"
    r"|\bsubsp\.\s+\S+",
    re.IGNORECASE,
)
# Flu-style strain: host names can contain spaces ("common pochard",
# "whooper swan"), so all segments allow ``\w`` + space + hyphen.
_FLU_STYLE = re.compile(r"\b[A-Z]/[\w -]+/[\w. -]+/[\w -]+/\d{2,4}")
_ACCESSION_PREFIX = re.compile(r"^[A-Z]{2,3}_?\d{6,}(\s|$)")
_COMMA_LIST = re.compile(r",\s*[A-Z]{2,3}\d{4,}")  # accession list


# ---------------------------------------------------------------------------
# SC-B7 (2026-06-08): strain-prefix acronym extraction.
#
# BVBRC's Genome_Name often takes the form ``<Species> <ACRONYM><sep>...``
# where ``<sep>`` is ``/``, ``-``, or ``_``:
#
#   "Chikungunya virus CHIKV/IRL/2007"               -> CHIKV
#   "Western equine encephalitis virus WEEV-UY-228"  -> WEEV
#   "Mayaro virus MAYV_BR/MT_CbaAr66/2017"           -> MAYV
#
# These tokens are real species synonyms but the rest-of-string is a strain
# isolate that SC-B5 (correctly) drops. SC-B7 extracts the acronym *before*
# SC-B5 rejects the surface form. The downstream batch miner applies a
# frequency floor (count >= N records AND >= X% of the species' records)
# so this is a no-hardcoded-list, frequency-inferred synonym pass.
# ---------------------------------------------------------------------------

# Token rule: leading uppercase letter, then 3-6 more chars that may be
# letters or digits, followed by a slash / underscore / hyphen. Anchored to
# whitespace (or start) so we only catch the strain-prefix slot, not a
# token-internal acronym (which we'd false-positive on accession lists).
_STRAIN_PREFIX_ACRONYM = re.compile(r"(?:\A|\s)([A-Z][A-Z0-9]{3,6})[/_\-]")


def _is_acronym_shape(token: str) -> bool:
    """High-precision shape check for prefix-acronym candidates.

    Accepts ``CHIKV`` / ``EEEV`` / ``WEEV`` / ``MAYV`` / ``VEEV`` / ``GETV``
    / ``MADV`` / ``VEEVIAB``. Rejects ``F02`` / ``F06`` (one letter then
    digits — strain IDs) and ``KEN`` (3 chars — too short, ambiguous as a
    country code). The rule: 4-7 chars total AND a >= 3-char leading run of
    uppercase letters.
    """
    if not (4 <= len(token) <= 7):
        return False
    leading_letters = 0
    for ch in token:
        if ch.isalpha():
            leading_letters += 1
        else:
            break
    return leading_letters >= 3


def extract_strain_prefix_acronyms(
    genome_name: str, *, species: str | None = None
) -> list[str]:
    """Return acronym candidates extracted from one BVBRC ``Genome_Name``.

    If ``species`` is provided and case-insensitively prefixes
    ``genome_name``, that span is stripped first so we don't scan tokens
    inside the species name itself. Returns only shape-valid candidates;
    the caller applies the per-species frequency filter via the batch
    miner.
    """
    if not isinstance(genome_name, str) or not genome_name:
        return []
    rest = genome_name
    if species and isinstance(species, str):
        sp = species.strip()
        if sp and genome_name.lower().startswith(sp.lower()):
            rest = genome_name[len(sp):]
    candidates = _STRAIN_PREFIX_ACRONYM.findall(" " + rest)
    return [a for a in candidates if _is_acronym_shape(a)]


# ---------------------------------------------------------------------------
# SC-B8 (2026-06-08): parenthetical-acronym extraction from VIOLIN prose.
#
# VIOLIN's Pathogen records carry no Other_Names column; what they DO
# carry are descriptive prose fields (``Pathogen_Description``,
# ``Microbial_Pathogenesis``, ``Host_Ranges_and_Animal_Models``,
# ``Host_Protective_Immunity``) where biomedical writers conventionally
# introduce acronyms as parenthetical asides:
#
#   "Herpes simplex virus 1 and 2 (HSV-1 and HSV-2), also known as
#    Human herpes virus 1 and 2 (HHV-1 and -2), are two members of..."
#
# This is the textbook initialism pattern. SC-B8 extracts the acronym
# tokens with TWO precision guards:
#
#   1. The PHRASE preceding the parens must overlap with the record's
#      canonical Pathogen field by >= 2 consecutive content words —
#      so acronyms introduced near (but unrelated to) the pathogen
#      are not attached.
#   2. The acronym's UPPERCASE-letter signature must form a CONSECUTIVE
#      subsequence of the phrase's word-initials — so citation markers
#      like "(CDC: ...)" inside a sentence about the pathogen don't
#      mis-attribute.
# ---------------------------------------------------------------------------

# Capture ``<phrase> (CONTENT)`` non-greedily so the phrase trims to the
# shortest preamble that still admits a parenthetical. The trailing
# ``\s*`` absorbs whitespace before the open-paren.
_PAREN_PAT = re.compile(r"([A-Za-z][A-Za-z0-9 /\-]{2,80}?)\s*\(([^()]+?)\)")

# Acronym candidate inside a parenthetical: 3-7 chars; starts uppercase;
# allows one lowercase second char (FeHV-1 / GaHV-1 style) followed by
# at least one more uppercase letter; optional trailing -NN digit suffix.
_PAREN_ACRONYM_PAT = re.compile(
    r"\b([A-Z][A-Za-z][A-Z0-9]{1,5}(?:-[A-Za-z0-9]{1,3})?)\b"
)


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text or "")


def _acronym_uppercase_core(acronym: str) -> str:
    """Strip digits, hyphens, and lowercase letters from the acronym.

    ``"HSV-1"`` -> ``"HSV"``; ``"FeHV-1"`` -> ``"FHV"``; ``"CCHF"`` ->
    ``"CCHF"``. This signature is what the initialism check compares
    against the phrase's word-initials.
    """
    return re.sub(r"[^A-Z]", "", (acronym or "").upper())


def _is_initialism_of(acronym: str, phrase: str) -> bool:
    """True if ``acronym``'s uppercase signature is a CONSECUTIVE
    subsequence of ``phrase``'s word-initials (case-insensitive).

    Implements the standard biomedical-NLP introduction check: an
    acronym defined as ``"<words> (ACR)"`` only counts when ACR's
    letters are spelled out by the consecutive first letters of the
    preceding words. Single-character acronyms are rejected (too noisy).
    """
    core = _acronym_uppercase_core(acronym)
    if len(core) < 2:
        return False
    initials = [t[0].upper() for t in _word_tokens(phrase) if t]
    if len(initials) < len(core):
        return False
    target = list(core)
    span = len(target)
    for start in range(len(initials) - span + 1):
        if initials[start:start + span] == target:
            return True
    return False


def _phrase_overlaps_pathogen(
    phrase: str, pathogen: str, *, min_overlap: int = 2
) -> bool:
    """True if ``phrase`` shares ``>= min_overlap`` consecutive
    content-word windows with ``pathogen`` (case-insensitive)."""
    p = [t.lower() for t in _word_tokens(phrase)]
    a = [t.lower() for t in _word_tokens(pathogen)]
    if len(p) < min_overlap or len(a) < min_overlap:
        return False
    for i in range(len(a) - min_overlap + 1):
        window = a[i:i + min_overlap]
        for j in range(len(p) - min_overlap + 1):
            if p[j:j + min_overlap] == window:
                return True
    return False


def extract_parenthetical_acronyms(
    text: str, *, pathogen: str
) -> list[str]:
    """SC-B8: yield acronym candidates introduced parenthetically in
    ``text`` for a record about ``pathogen``.

    Each candidate satisfies BOTH guards: the introducing phrase
    overlaps the record's canonical pathogen surface form, AND the
    candidate is an initialism of that phrase. Deduplicates within the
    text but does NOT apply any cross-record frequency floor — that
    belongs to the batch miner.
    """
    if not isinstance(text, str) or not text or not pathogen:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for m in _PAREN_PAT.finditer(text):
        phrase, content = m.group(1), m.group(2)
        if not _phrase_overlaps_pathogen(phrase, pathogen):
            continue
        for am in _PAREN_ACRONYM_PAT.finditer(content):
            acr = am.group(1)
            if not _is_initialism_of(acr, phrase):
                continue
            if acr in seen:
                continue
            seen.add(acr)
            found.append(acr)
    return found


def is_strain_level(surface: str) -> bool:
    """SC-B5: True if ``surface`` looks like a strain/isolate descriptor.

    Conservative: errs toward false negatives (a strain slipping through
    is one polluted synonym; a real name flagged as strain is a missing
    synonym, harder to debug later).
    """
    if not isinstance(surface, str) or not surface:
        return False
    text = surface.strip()
    if not text:
        return False
    # 1. Flu-style strain notation anywhere in the string.
    if _FLU_STYLE.search(text):
        return True
    # 2. Accession-prefixed (GenBank-like) at the start.
    if _ACCESSION_PREFIX.match(text):
        return True
    # 3. Trailing comma-separated accession list.
    if _COMMA_LIST.search(text):
        return True
    # 4. Strain/isolate/subsp. keyword followed by a descriptor token.
    if _STRAIN_KEYWORDS.search(text):
        return True
    # 5. Long string with parenthetical content (strain isolates frequently
    #    embed the lineage in parens). Conservative: require BOTH length
    #    AND a paren OR slash AND a digit (a year, an H/N number).
    if len(text) > _STRAIN_MAX_PARENS_LEN and (
        ("(" in text or "/" in text) and any(c.isdigit() for c in text)
    ):
        return True
    return False


@dataclass(frozen=True)
class MinedSynonymObservation:
    """One observation: this source recorded this surface form against this taxon.

    Frozen so observations are hashable + safe to put in sets / dict
    keys without surprise mutation. The accumulator dedupes by
    ``(surface_form_normalized, taxon_id)`` independent of source —
    each unique pair counts once per source.

    Attributes
    ----------
    surface_form:
        The exact string the source carried (preserves case, punctuation).
        The accumulator normalizes when keying for dedup, but the
        original is kept on the observation for provenance.
    surface_form_normalized:
        The dedup key — lowercased and whitespace-collapsed by the
        accumulator before storage. Use this when comparing across
        sources.
    taxon_id:
        Integer NCBI Taxonomy ID this source attached to the surface
        form. Must be > 0; the accumulator rejects ``<= 0`` defensively.
    source:
        Source identifier (``"violin_pathogen"``, ``"bvbrc_genome"``,
        ...). One observation per (surface, taxon, source) triple.
    """

    surface_form: str
    surface_form_normalized: str
    taxon_id: int
    source: str


def _normalize(text: str) -> str:
    """Match the synonym dictionary's normalization: casefold + whitespace collapse.

    Intentionally lighter than the apecx-mcp-integration
    ``normalize_surface_form`` (which is also case + whitespace, but
    lives in the consuming repo). Duplicating the few lines avoids
    introducing a cross-repo runtime dep just for the accumulator.
    When the SC-B4 ingest writes into the dictionary, the consumer's
    normalization runs again — so any drift between the two does not
    silently mis-key.
    """
    if not isinstance(text, str):
        return ""
    return " ".join(text.casefold().split())


def _is_meaningful(surface: str) -> bool:
    """Reject surface forms unlikely to be real synonyms."""
    if not surface or len(surface) < _MIN_SURFACE_LEN:
        return False
    for pattern in _NOISE_PATTERNS:
        if pattern.match(surface):
            return False
    return True


class MinedSynonymAccumulator:
    """Collect ``(surface_form, taxon_id)`` observations across many sources.

    Usage::

        acc = MinedSynonymAccumulator()
        for record in violin_pathogen_records:
            acc.observe(record.Pathogen, record.NCBI_Taxonomy_ID,
                        source="violin_pathogen")
        for record in bvbrc_genome_records:
            acc.observe(record.genome_name, taxon_of(record.genome_id),
                        source="bvbrc_genome")

        # Dictionary build (SC-B4) reads these:
        for obs in acc.observations_corroborated(min_sources=2):
            ...  # confidence 0.95 — see SC plan §3.1

    The accumulator is NOT thread-safe. Production parser-lift is
    single-threaded per source; if you ever fan out across processes,
    accumulate per-process then ``merge`` at the end.
    """

    def __init__(self) -> None:
        # Keyed by (normalized_surface, taxon_id) — one bucket per
        # observation tuple. Value is the set of sources that recorded it.
        self._buckets: dict[tuple[str, int], set[str]] = defaultdict(set)
        # Original surface form per normalized key — keep the FIRST
        # spelling seen for provenance. Stable across runs (first parser
        # in registration order wins).
        self._original_form: dict[str, str] = {}
        # Per-source observation counter (for SC-B6 reports).
        self._source_observations: dict[str, int] = defaultdict(int)
        # Per-source REJECTED counts (noise filter dropped them).
        self._source_rejected: dict[str, int] = defaultdict(int)

    def observe(
        self,
        surface_form: str | None,
        taxon_id: int | str | None,
        *,
        source: str,
    ) -> bool:
        """Record one observation. Returns True if it was accepted.

        Defensive coercion:
        - ``surface_form`` None or non-string → reject.
        - ``taxon_id`` None / non-positive / non-int-coercible → reject.
        - Noise patterns (numeric-only, nulls, whitespace) → reject.

        Multiple ``observe(...)`` calls with the same (surface, taxon,
        source) are idempotent — the bucket is a set.
        """
        # Coerce + validate taxon id.
        try:
            tid = int(taxon_id) if taxon_id is not None else 0
        except (TypeError, ValueError):
            self._source_rejected[source] += 1
            return False
        if tid <= 0:
            self._source_rejected[source] += 1
            return False

        if surface_form is None or not isinstance(surface_form, str):
            self._source_rejected[source] += 1
            return False
        if not _is_meaningful(surface_form):
            self._source_rejected[source] += 1
            return False

        # SC-B5: strain-level descriptors get dropped before normalization.
        # Check on the ORIGINAL string because patterns like "A/host/..."
        # rely on case and punctuation; the normalized form loses those.
        if is_strain_level(surface_form):
            self._source_rejected[source] += 1
            return False

        normalized = _normalize(surface_form)
        if not _is_meaningful(normalized):
            self._source_rejected[source] += 1
            return False

        key = (normalized, tid)
        self._buckets[key].add(source)
        self._original_form.setdefault(normalized, surface_form.strip())
        self._source_observations[source] += 1
        return True

    def observations(self) -> Iterable[MinedSynonymObservation]:
        """Yield every accepted observation (across all sources).

        One ``MinedSynonymObservation`` per ``(normalized_surface,
        taxon_id, source)`` triple — so a single ``(surface, taxon)``
        seen by N sources yields N observations. Use
        :meth:`observations_corroborated` when you want the unique pairs
        with multi-source support.
        """
        for (normalized, tid), sources in self._buckets.items():
            original = self._original_form[normalized]
            for source in sources:
                yield MinedSynonymObservation(
                    surface_form=original,
                    surface_form_normalized=normalized,
                    taxon_id=tid,
                    source=source,
                )

    def unique_pairs(self) -> Iterable[tuple[MinedSynonymObservation, int, frozenset[str]]]:
        """Yield ``(observation, source_count, source_set)`` per unique
        ``(surface, taxon)`` pair.

        Used by SC-B3 / SC-B4 to compute the confidence tier for each
        mined entry per SC plan §3.1:

        - ``source_count >= 2`` → confidence 0.95 (``mined_corroborated``)
        - ``source_count == 1`` → confidence 0.90 (``mined_observed``)
        """
        for (normalized, tid), sources in self._buckets.items():
            obs = MinedSynonymObservation(
                surface_form=self._original_form[normalized],
                surface_form_normalized=normalized,
                taxon_id=tid,
                source=next(iter(sources)),  # representative
            )
            yield obs, len(sources), frozenset(sources)

    def observations_corroborated(
        self, *, min_sources: int = 2
    ) -> Iterable[MinedSynonymObservation]:
        """Yield observations seen by ≥ ``min_sources`` distinct sources.

        Default ``min_sources=2`` matches the SC plan's ``mined_corroborated``
        tier. Pass ``min_sources=1`` to get every accepted observation.
        """
        for (normalized, tid), sources in self._buckets.items():
            if len(sources) < min_sources:
                continue
            yield MinedSynonymObservation(
                surface_form=self._original_form[normalized],
                surface_form_normalized=normalized,
                taxon_id=tid,
                source=",".join(sorted(sources)),
            )

    def surface_form_conflicts(
        self,
    ) -> Iterable[tuple[str, frozenset[int]]]:
        """Yield ``(normalized_surface, {taxon_id, ...})`` for surfaces
        mapped to ≥2 distinct taxa.

        These are the SC-B3 conflict candidates: the same surface form
        was observed against different canonical taxa by one or more
        sources. The dictionary build decides whether to ingest the
        most-corroborated as the winner + alternatives, or to route the
        entire conflict to HITL.
        """
        by_surface: dict[str, set[int]] = defaultdict(set)
        for (normalized, tid) in self._buckets:
            by_surface[normalized].add(tid)
        for normalized, taxa in by_surface.items():
            if len(taxa) >= 2:
                yield normalized, frozenset(taxa)

    def per_source_stats(self) -> dict[str, dict[str, int]]:
        """Return ``{source: {observed: N, rejected: M, unique_pairs: K}}``."""
        unique_per_source: dict[str, int] = defaultdict(int)
        for sources in self._buckets.values():
            for s in sources:
                unique_per_source[s] += 1
        return {
            source: {
                "observed": self._source_observations.get(source, 0),
                "rejected": self._source_rejected.get(source, 0),
                "unique_pairs": unique_per_source.get(source, 0),
            }
            for source in set(self._source_observations)
            | set(self._source_rejected)
            | set(unique_per_source)
        }

    def unique_pair_count(self) -> int:
        return len(self._buckets)

    def conflict_count(self) -> int:
        return sum(1 for _ in self.surface_form_conflicts())

    def merge(self, other: "MinedSynonymAccumulator") -> None:
        """Merge another accumulator's state into this one.

        Use when accumulating per-process and combining at the end.
        Original-form tie-break: existing wins (first-seen stability).
        """
        for (normalized, tid), sources in other._buckets.items():
            self._buckets[(normalized, tid)].update(sources)
            self._original_form.setdefault(
                normalized, other._original_form[normalized]
            )
        for source, count in other._source_observations.items():
            self._source_observations[source] += count
        for source, count in other._source_rejected.items():
            self._source_rejected[source] += count


__all__ = [
    "MinedSynonymObservation",
    "MinedSynonymAccumulator",
    "is_strain_level",
    "extract_strain_prefix_acronyms",
    "extract_parenthetical_acronyms",
]
