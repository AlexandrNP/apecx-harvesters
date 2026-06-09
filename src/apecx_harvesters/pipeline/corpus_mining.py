"""Corpus-mined synonym observations + accumulator.

Parser-lift feeds ``(surface_form, taxon_id)`` pairs into a
:class:`MinedSynonymAccumulator`; the dictionary build later ingests
the accumulated observations with provenance + corroboration tiers.

I/O-free by design — no SQLite, no Globus, no parser imports. The
mining hook is just ``accumulator.observe(surface, taxon, source=...)``.

Conflict surfacing is recording-only: when the same surface form maps
to multiple taxa, :meth:`MinedSynonymAccumulator.surface_form_conflicts`
returns the full set. The dictionary-build consumer decides whether to
keep the most-corroborated as the winner or route the entire conflict
to HITL.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

_MIN_SURFACE_LEN = 2
_NOISE_PATTERNS = (
    re.compile(r"^\s*$"),
    re.compile(r"^\d+\.?\d*$"),
    re.compile(r"^(nan|none|null|na|n/a)$", re.IGNORECASE),
)


# Strain-level descriptor detection.
#
# BVBRC's ``Genome_Name`` carries strain isolates (e.g. "Influenza A virus
# (A/common pochard/Shanxi/16B/2015(H5N1))") that would pollute the
# inverse_index with surface forms users never type. Heuristics:
#
# - Flu-style ``A/<host>/<location>/<isolate>/<year>`` anywhere.
# - GenBank-like accession prefix at start (2-3 letters + 6+ digits).
# - "strain"/"isolate"/"subsp."/"clone"/"variant" keyword + descriptor.
# - Long (>60 char) strings containing paren or slash AND a digit.
#
# Conservative — favours false negatives (one polluted synonym) over false
# positives (one missing species name).

_STRAIN_MAX_PARENS_LEN = 60
# ``subsp.`` ends in a period that does NOT form a word boundary with the
# following space, so a trailing ``\b`` would refuse to match.
_STRAIN_KEYWORDS = re.compile(
    r"\b(strain|isolate|subspecies|clone|sub-?type|variant)\b\s+\S+"
    r"|\bsubsp\.\s+\S+",
    re.IGNORECASE,
)
# Host names can contain spaces ("common pochard"), so segments allow
# ``\w`` + space + hyphen.
_FLU_STYLE = re.compile(r"\b[A-Z]/[\w -]+/[\w. -]+/[\w -]+/\d{2,4}")
_ACCESSION_PREFIX = re.compile(r"^[A-Z]{2,3}_?\d{6,}(\s|$)")
_COMMA_LIST = re.compile(r",\s*[A-Z]{2,3}\d{4,}")


# Strain-prefix acronym extraction.
#
# BVBRC genome names often take the form "<Species> <ACRONYM><sep>..."
# with ``<sep>`` ∈ {``/``, ``-``, ``_``}:
#
#   "Chikungunya virus CHIKV/IRL/2007"               -> CHIKV
#   "Western equine encephalitis virus WEEV-UY-228"  -> WEEV
#
# The acronym is a real species synonym but the rest is a strain isolate
# the strain filter (correctly) drops. Extract the acronym BEFORE the
# strain filter rejects the surface form. The batch miner applies a
# frequency floor downstream.

# Anchored to whitespace (or start) so we only catch the strain-prefix
# slot, not a token-internal acronym (false-positives on accession lists).
_STRAIN_PREFIX_ACRONYM = re.compile(r"(?:\A|\s)([A-Z][A-Z0-9]{3,6})[/_\-]")


def _is_acronym_shape(token: str) -> bool:
    """Accept CHIKV/EEEV/WEEV/MAYV; reject F02 (1 letter + digits) and
    KEN (3 chars — too short, ambiguous as a country code).
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
    """Acronym candidates extracted from one BVBRC ``Genome_Name``.

    If ``species`` case-insensitively prefixes ``genome_name``, that span
    is stripped first so we don't scan tokens inside the species name.
    The caller applies the per-species frequency filter via the batch miner.
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


# Parenthetical-acronym extraction from VIOLIN prose.
#
# VIOLIN Pathogen records carry no Other_Names column but DO carry prose
# fields where writers introduce acronyms parenthetically:
#
#   "Herpes simplex virus 1 and 2 (HSV-1 and HSV-2), also known as ..."
#
# Two precision guards:
#   1. The phrase preceding the parens must overlap the record's canonical
#      Pathogen field by >=2 consecutive content words.
#   2. The acronym's uppercase signature must be a consecutive subsequence
#      of the phrase's word-initials (textbook initialism check) so
#      citation markers like "(CDC: ...)" don't mis-attribute.

# Non-greedy phrase so it trims to the shortest preamble admitting a paren.
_PAREN_PAT = re.compile(r"([A-Za-z][A-Za-z0-9 /\-]{2,80}?)\s*\(([^()]+?)\)")

# 3-7 chars; uppercase-start; one lowercase second char allowed
# (FeHV-1/GaHV-1) followed by at least one more uppercase; optional -NN.
_PAREN_ACRONYM_PAT = re.compile(
    r"\b([A-Z][A-Za-z][A-Z0-9]{1,5}(?:-[A-Za-z0-9]{1,3})?)\b"
)


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text or "")


def _acronym_uppercase_core(acronym: str) -> str:
    """``"HSV-1"`` -> ``"HSV"``; ``"FeHV-1"`` -> ``"FHV"``; ``"CCHF"`` -> ``"CCHF"``."""
    return re.sub(r"[^A-Z]", "", (acronym or "").upper())


def _is_initialism_of(acronym: str, phrase: str) -> bool:
    """True iff the acronym's uppercase signature is a consecutive
    subsequence of ``phrase``'s word-initials. Single-character
    acronyms are rejected (too noisy).
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
    """True iff ``phrase`` shares >= ``min_overlap`` consecutive
    content-word windows with ``pathogen`` (case-insensitive).
    """
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
    """Acronym candidates introduced parenthetically in ``text`` for a
    record about ``pathogen``. Each candidate satisfies BOTH guards.
    Deduplicates within the text; no cross-record frequency floor here.
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
    """True iff ``surface`` looks like a strain/isolate descriptor.

    Conservative: a strain slipping through is one polluted synonym;
    a real name flagged as strain is a missing synonym, harder to debug
    later.
    """
    if not isinstance(surface, str) or not surface:
        return False
    text = surface.strip()
    if not text:
        return False
    if _FLU_STYLE.search(text):
        return True
    if _ACCESSION_PREFIX.match(text):
        return True
    if _COMMA_LIST.search(text):
        return True
    if _STRAIN_KEYWORDS.search(text):
        return True
    # Long strings with parenthetical content frequently embed lineage in
    # parens. Require BOTH length AND (paren OR slash) AND a digit.
    if len(text) > _STRAIN_MAX_PARENS_LEN and (
        ("(" in text or "/" in text) and any(c.isdigit() for c in text)
    ):
        return True
    return False


@dataclass(frozen=True)
class MinedSynonymObservation:
    """One observation: this source recorded this surface form against this taxon.

    Frozen so observations are hashable. The accumulator dedupes by
    ``(surface_form_normalized, taxon_id)`` independent of source — each
    unique pair counts once per source.
    """

    surface_form: str
    surface_form_normalized: str
    taxon_id: int
    source: str


def _normalize(text: str) -> str:
    """Casefold + whitespace collapse — matches the dictionary's normalization.

    Intentionally duplicated rather than imported from apecx-mcp-integration
    to keep the harvester accumulator I/O- and cross-repo-free. When the
    dictionary ingest runs, the consumer normalizes again — any drift
    between the two does not silently mis-key.
    """
    if not isinstance(text, str):
        return ""
    return " ".join(text.casefold().split())


def _is_meaningful(surface: str) -> bool:
    if not surface or len(surface) < _MIN_SURFACE_LEN:
        return False
    for pattern in _NOISE_PATTERNS:
        if pattern.match(surface):
            return False
    return True


class MinedSynonymAccumulator:
    """Collect ``(surface_form, taxon_id)`` observations across sources.

    Usage::

        acc = MinedSynonymAccumulator()
        for record in violin_pathogen_records:
            acc.observe(record.Pathogen, record.NCBI_Taxonomy_ID,
                        source="violin_pathogen")
        for obs in acc.observations_corroborated(min_sources=2):
            ...  # multi-source observation (confidence tier "mined_corroborated")

    NOT thread-safe. Production parser-lift is single-threaded per source;
    if fanning out across processes, accumulate per-process then ``merge``.
    """

    def __init__(self) -> None:
        # (normalized_surface, taxon_id) -> set of sources that recorded it.
        self._buckets: dict[tuple[str, int], set[str]] = defaultdict(set)
        # normalized -> first-seen original spelling (stable across runs).
        self._original_form: dict[str, str] = {}
        self._source_observations: dict[str, int] = defaultdict(int)
        self._source_rejected: dict[str, int] = defaultdict(int)

    def observe(
        self,
        surface_form: str | None,
        taxon_id: int | str | None,
        *,
        source: str,
    ) -> bool:
        """Record one observation. Returns True iff it was accepted.

        Rejects: None / non-string surface, non-positive taxon, numeric-only
        or pandas-null surface, and strain-level descriptors (checked on
        the original — patterns like "A/host/..." rely on case + punctuation).
        Idempotent on repeat (surface, taxon, source) triples.
        """
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
        """Yield every accepted observation. One per (normalized, taxon,
        source) triple — a pair seen by N sources yields N observations.
        Use :meth:`observations_corroborated` for unique pairs with
        multi-source support.
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
        """``(observation, source_count, source_set)`` per unique
        ``(surface, taxon)`` pair. The downstream consumer assigns the
        confidence tier from ``source_count`` (>=2 corroborated; 1 observed).
        """
        for (normalized, tid), sources in self._buckets.items():
            obs = MinedSynonymObservation(
                surface_form=self._original_form[normalized],
                surface_form_normalized=normalized,
                taxon_id=tid,
                source=next(iter(sources)),
            )
            yield obs, len(sources), frozenset(sources)

    def observations_corroborated(
        self, *, min_sources: int = 2
    ) -> Iterable[MinedSynonymObservation]:
        """Observations seen by >= ``min_sources`` distinct sources.
        ``source`` carries comma-joined sorted contributing sources.
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
        """Surfaces mapped to >= 2 distinct taxa. The dictionary build
        decides ingest-with-alternatives vs. HITL routing.
        """
        by_surface: dict[str, set[int]] = defaultdict(set)
        for (normalized, tid) in self._buckets:
            by_surface[normalized].add(tid)
        for normalized, taxa in by_surface.items():
            if len(taxa) >= 2:
                yield normalized, frozenset(taxa)

    def per_source_stats(self) -> dict[str, dict[str, int]]:
        """``{source: {observed: N, rejected: M, unique_pairs: K}}``."""
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
        """Merge another accumulator's state. Original-form tie-break:
        existing wins (first-seen stability).
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
