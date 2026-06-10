"""Read-only proof that the republish resolver stamps species ancestors.

Pulls real DEST records for the strain-stamped BVBRC sources (anonymous
Globus read), runs each through the per-source resolver against the live
enriched dictionary, and reports — per record — the NCBITaxon subjects it
gains, flagging which are species-rank ancestors of a strain taxon.

No credentials, no writes. This is the correctness gate that must pass
before the outward-facing republish.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import globus_sdk

from apecx_harvesters.dict_reader import configure_dictionary_path, get_dictionary_index
from apecx_harvesters.pipeline.canonical_resolver_adapter import make_resolver_for_source
from apecx_harvesters.pipeline.harmonize import DEST_REGISTRY, SOURCE_REGISTRY
from apecx_harvesters.pipeline.republish_with_canonical import _reparse_dest_content

_PREF = "http://purl.obolibrary.org/obo/NCBITaxon_"
_SOURCES = ["bvbrc_protein", "bvbrc_genome"]


def _name_to_uuids(name: str) -> tuple[str, str]:
    src = next(u for u, (n, _) in SOURCE_REGISTRY.items() if n == name)
    return src, DEST_REGISTRY[src]


def _fetch(dest_uuid: str, n: int) -> list[dict]:
    c = globus_sdk.SearchClient()
    r = c.post_search(dest_uuid, {"q": "*", "limit": n})
    return [g["entries"][0]["content"] for g in r.data.get("gmeta", []) if g.get("entries")]


def main() -> int:
    dict_path = Path(
        os.environ.get(
            "APECX_SYNONYM_DICT_PATH",
            str(Path.home() / ".apecx" / "dictionary" / "dictionary.sqlite"),
        )
    )
    if not dict_path.exists():
        print(f"dictionary not present at {dict_path}", file=sys.stderr)
        return 2
    configure_dictionary_path(dict_path)
    index, err = get_dictionary_index()
    if index is None:
        print(f"dictionary failed to load: {err}", file=sys.stderr)
        return 2

    total_records = 0
    total_with_species = 0
    for name in _SOURCES:
        src, dst = _name_to_uuids(name)
        _, parser = SOURCE_REGISTRY[src]
        resolver = make_resolver_for_source(name)
        samples = _fetch(dst, 8)
        print(f"\n=== {name}  ({len(samples)} sample records) ===")
        for content in samples:
            rid = content.get("id", "?")
            record = _reparse_dest_content(content, parser, rid)
            before = {s.valueUri for s in (record.subjects or []) if s.valueUri}
            resolved = resolver(record)
            after = {s.valueUri for s in (resolved.subjects or []) if s.valueUri}
            added = after - before
            total_records += 1

            taxa = sorted(i for i in after if i.startswith(_PREF))
            species_added = []
            for tid_iri in taxa:
                tid = int(tid_iri[len(_PREF):])
                sp = index.species_iri_for(tid_iri)
                if sp is not None and sp != tid_iri and sp in added:
                    species_added.append((tid, int(sp[len(_PREF):])))
            if species_added:
                total_with_species += 1
            sp_str = ", ".join(f"{t}→species {s}" for t, s in species_added) or "(none)"
            print(f"  {rid[:40]:40s} taxa={[int(i[len(_PREF):]) for i in taxa]} "
                  f"species_added={sp_str}")

    print(f"\n=== {total_records} records, {total_with_species} gained a species ancestor ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
