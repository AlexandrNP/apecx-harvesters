"""Multi-entity query detector — split a query into an organism + a residual protein/gene/record-type.

The alias refine collapses a two-entity query to a single organism, silently discarding the rest:
"HIV protease" -> "HIV-1" throws away PROTEASE. This detector finds that residual so the alias layer can
REFUSE the lossy collapse and the query layer can build a compound (taxon AND protein) request.

Classification of the non-organism residual:
  PROTEIN/GENE (confident) -> a viral-protein lexicon match OR a dict EntityType.GENE hit -> protein constraint
  RECORD-TYPE  (genome/structure/vaccine/...) -> an INDEX-routing hint, NOT a discarded entity
  UNKNOWN      -> neither -> surface for HITL, never silently dropped

Deterministic (lexicon + dict); no LLM needed. `resolve_query` (the dict) confirms the organism part.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from harmonization_ablation import resolve_query

# Curated viral/structural protein + functional-domain terms. Multi-word entries are matched first so
# "spike protein" / "reverse transcriptase" win over their single tokens. Not exhaustive — the dict
# EntityType.GENE lookup backs it up for names not listed here.
_VIRAL_PROTEINS = {
    "reverse transcriptase", "matrix protein", "fusion protein", "nucleocapsid protein",
    "envelope protein", "capsid protein", "membrane protein", "surface glycoprotein", "spike protein",
    "protease", "polymerase", "spike", "glycoprotein", "capsid", "envelope", "nucleoprotein",
    "hemagglutinin", "neuraminidase", "integrase", "nucleocapsid", "helicase", "replicase",
    "ns1", "ns3", "ns5", "nsp1", "rdrp", "vp1", "vp2", "vp7", "gp120", "gp41", "e1", "e2",
}
# Record-TYPE modifiers: these route to a source index / record type, they are NOT a discarded entity.
_RECORD_TYPES = {"genome", "genomes", "structure", "structures", "sequence", "sequences",
                 "vaccine", "vaccines", "epitope", "epitopes", "assembly", "proteome"}
# Generic words to strip when isolating the organism (kept out of the "unknown residual" bucket).
_GENERIC = {"protein", "proteins", "the", "a", "of", "for", "and", "data", "record", "records"}


@dataclass
class QuerySplit:
    term: str
    organism: str | None              # the organism substring (query minus protein/record-type/generic)
    organism_iris: set = field(default_factory=set)   # resolved taxon IRIs (empty if it doesn't resolve)
    protein_term: str | None = None   # the protein/gene residual (the thing the lossy alias discards)
    protein_confident: bool = False   # lexicon/dict-confirmed (vs an unknown residual)
    record_type: str | None = None    # genome/vaccine/structure/... (index hint, not a loss)
    residual_unknown: str | None = None  # a residual that is neither protein nor record-type -> HITL
    multi_entity: bool = False        # organism resolves AND a protein/gene residual is present


def _find_lexicon_protein(low: str) -> str | None:
    """Longest viral-protein lexicon term present as a whole word/phrase in the query, else None."""
    for cand in sorted(_VIRAL_PROTEINS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(cand)}\b", low):
            return cand
    return None


def _dict_is_gene(token: str) -> bool:
    """Does the dict type this token as a GENE/protein? (Secondary confidence for names not in the lexicon.)"""
    try:
        from apecx_harvesters.dict_reader import EntityType, get_dictionary_index
    except Exception:  # noqa: BLE001
        return False
    index, _ = get_dictionary_index()
    if index is None:
        return False
    try:
        return index.lookup(EntityType.GENE, token) is not None
    except Exception:  # noqa: BLE001
        return False


def classify_query_entities(term: str, resolver=resolve_query) -> QuerySplit:
    """resolver is injectable for unit tests (defaults to the dict-backed resolve_query)."""
    low = term.lower()
    # 1. protein/gene residual (lexicon first, then a dict-GENE check on leftover tokens).
    protein = _find_lexicon_protein(low)
    protein_confident = protein is not None
    # 2. record-type modifier.
    record_type = next((w for w in low.split() if w in _RECORD_TYPES), None)
    # 3. organism candidate = the query minus the protein term, record-type, and generic words.
    remainder = low
    for piece in filter(None, [protein, record_type]):
        remainder = re.sub(rf"\b{re.escape(piece)}\b", " ", remainder)
    org_tokens = [t for t in re.split(r"\s+", remainder) if t and t not in _GENERIC and t not in _RECORD_TYPES]
    # a leftover token that is itself a dict GENE is a protein we missed in the lexicon.
    if protein is None:
        gene_tok = next((t for t in org_tokens if _dict_is_gene(t)), None)
        if gene_tok:
            protein, protein_confident = gene_tok, True
            org_tokens = [t for t in org_tokens if t != gene_tok]
    organism = " ".join(org_tokens).strip() or None
    iris = resolver(organism).iris if organism else set()
    # 4. an unknown residual: meaningful organism tokens remain but the organism doesn't resolve and there
    #    is no protein/record-type explanation -> the caller should HITL rather than guess.
    residual_unknown = None
    if organism and not iris and not protein and not record_type:
        residual_unknown = organism
    multi_entity = bool(protein) and bool(iris)
    return QuerySplit(term=term, organism=organism, organism_iris=set(iris), protein_term=protein,
                      protein_confident=protein_confident, record_type=record_type,
                      residual_unknown=residual_unknown, multi_entity=multi_entity)
