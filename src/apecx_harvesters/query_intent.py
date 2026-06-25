"""Pure query-intent helpers shared by the harmonized query path and the benchmark loop.

A multi-entity query like "HIV protease" carries an organism (HIV-1) AND a protein/gene (protease). The
alias/resolution layer must not collapse it to the organism alone — that discards the protein. These
helpers identify the protein/gene term and record-type modifiers so callers can build a compound
(taxon AND protein) request instead. Pure: no dictionary / network / package-internal dependency.
"""
from __future__ import annotations

import re

# Curated viral/structural protein + functional-domain terms. Multi-word entries are matched first so
# "spike protein" / "reverse transcriptase" win over their single tokens.
PROTEIN_TERMS: frozenset[str] = frozenset({
    "reverse transcriptase", "matrix protein", "fusion protein", "nucleocapsid protein",
    "envelope protein", "capsid protein", "membrane protein", "surface glycoprotein", "spike protein",
    "protease", "polymerase", "spike", "glycoprotein", "capsid", "envelope", "nucleoprotein",
    "hemagglutinin", "neuraminidase", "integrase", "nucleocapsid", "helicase", "replicase",
    "ns1", "ns3", "ns5", "nsp1", "rdrp", "vp1", "vp2", "vp7", "gp120", "gp41", "e1", "e2",
})

# Record-TYPE modifiers — these route to a source index / record type, NOT a discarded entity.
RECORD_TYPES: frozenset[str] = frozenset({
    "genome", "genomes", "structure", "structures", "sequence", "sequences",
    "vaccine", "vaccines", "epitope", "epitopes", "assembly", "proteome",
})

# Generic words to strip when isolating the organism (kept out of the "unknown residual" bucket).
GENERIC_WORDS: frozenset[str] = frozenset({
    "protein", "proteins", "the", "a", "of", "for", "and", "data", "record", "records",
})


def find_protein_term(text: str) -> str | None:
    """The longest PROTEIN_TERMS entry present as a whole word/phrase in *text* (lowercased), else None."""
    low = text.lower()
    for cand in sorted(PROTEIN_TERMS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(cand)}\b", low):
            return cand
    return None


def find_record_type(text: str) -> str | None:
    """The first record-type modifier token in *text*, else None."""
    return next((w for w in text.lower().split() if w in RECORD_TYPES), None)
