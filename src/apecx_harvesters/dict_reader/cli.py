"""CLI entry points for the dict_reader.

Two console scripts:

- ``apecx-lookup TERM [--json]`` — resolve a term against the local dict.
  Mirrors the existing ``apecx-integration``'s ``apecx-lookup`` command
  result format so existing consumers (e.g. ``harmonized_query.py``'s
  CLI-fallback path) can switch without changing their JSON parser.

- ``apecx-dict-update [--base-url URL] [--force] [--check-only] [--quiet]``
  Bootstrap or refresh the local dict from the published Globus path.

Both scripts are intentionally minimal: argparse + the underlying
library calls. The console_scripts entry points are wired in
``apecx-harvesters-work/pyproject.toml`` under the ``[reader]`` extra.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from apecx_harvesters.dict_reader import (
    EntityType,
    configure_dictionary_path,
    default_dictionary_path,
    lookup_entity,
)
from apecx_harvesters.dict_reader.bootstrap import (
    SUPPORTED_SCHEMA_MAJOR,
    bootstrap_dictionary,
    current_local_version,
    fetch_manifest,
)


# ---------------------------------------------------------------------------
# apecx-lookup
# ---------------------------------------------------------------------------


def lookup_main(argv: list[str] | None = None) -> int:
    """Entry point for ``apecx-lookup``."""
    parser = argparse.ArgumentParser(
        prog="apecx-lookup",
        description=(
            "Resolve a surface form against the apecx synonym dictionary. "
            "Loads from APECX_SYNONYM_DICT_PATH or "
            "~/.apecx/dictionary/dictionary.sqlite."
        ),
    )
    parser.add_argument("term", help="Surface form to resolve")
    parser.add_argument(
        "--entity-type",
        choices=[e.value for e in EntityType],
        default=None,
        help="Optional entity-type hint (default: search all types)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON (matches apecx-integration's apecx-lookup format)",
    )
    args = parser.parse_args(argv)

    dict_path = default_dictionary_path()
    if not dict_path.exists():
        sys.stderr.write(
            f"dictionary not found at {dict_path}; run `apecx-dict-update` "
            f"to bootstrap from the published Globus path.\n"
        )
        return 2
    configure_dictionary_path(dict_path)

    et = EntityType(args.entity_type) if args.entity_type else None
    result = lookup_entity(args.term, entity_type=et)

    if args.json:
        payload = {
            "surface_form": result.surface_form,
            "path": result.path,
            "canonical_iri": result.canonical_iri,
            "canonical_label": result.canonical_label,
            "canonical_ontology": result.canonical_ontology,
            "confidence": result.confidence,
            "resolution_status": result.resolution_status.value,
            "synonyms": list(result.synonyms),
            "evidence": result.evidence,
            "candidates": [asdict(c) for c in result.candidates],
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(
            f"{args.term} -> path={result.path} iri={result.canonical_iri} "
            f"label={result.canonical_label!r} conf={result.confidence}\n"
        )
        if result.candidates:
            sys.stdout.write(
                f"  {len(result.candidates)} candidate(s):\n"
            )
            for c in result.candidates:
                sys.stdout.write(
                    f"    - {c.canonical_iri} ({c.canonical_label!r}, "
                    f"conf={c.confidence})\n"
                )
    return 0 if result.path != "miss" else 1


# ---------------------------------------------------------------------------
# apecx-dict-update
# ---------------------------------------------------------------------------


def update_main(argv: list[str] | None = None) -> int:
    """Entry point for ``apecx-dict-update``."""
    parser = argparse.ArgumentParser(
        prog="apecx-dict-update",
        description=(
            "Bootstrap or refresh the local apecx synonym dictionary "
            "from the published Globus path. Resolves the base URL from "
            "APECX_DICT_PUBLIC_BASE_URL unless --base-url is supplied."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="HTTPS base URL of the published dictionary (overrides "
             "APECX_DICT_PUBLIC_BASE_URL)",
    )
    parser.add_argument(
        "--dest", type=Path, default=None,
        help="Local destination (default: ~/.apecx/dictionary/dictionary.sqlite)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download even when the local version matches",
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Print local vs published version and exit (no download)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output",
    )
    args = parser.parse_args(argv)

    try:
        if args.check_only:
            manifest = fetch_manifest(base_url=args.base_url)
            local = current_local_version(args.dest)
            sys.stdout.write(
                f"local:     {local or '(absent)'}\n"
                f"published: {manifest.dictionary_version}\n"
                f"schema:    {manifest.schema_version} "
                f"(supported major: {SUPPORTED_SCHEMA_MAJOR})\n"
                f"built_at:  {manifest.built_at}\n"
                f"file:      {manifest.dictionary_filename} "
                f"({manifest.dictionary_size_bytes:,} bytes, "
                f"compression={manifest.compression})\n"
            )
            if local == manifest.dictionary_version:
                sys.stdout.write("status: up-to-date\n")
                return 0
            sys.stdout.write("status: update available\n")
            return 0

        bootstrap_dictionary(
            base_url=args.base_url,
            dest=args.dest,
            force=args.force,
            quiet=args.quiet,
        )
        return 0
    except (RuntimeError, ValueError) as exc:
        sys.stderr.write(f"apecx-dict-update failed: {exc}\n")
        return 1


if __name__ == "__main__":
    # Convenience for ad-hoc invocation: python -m apecx_harvesters.dict_reader.cli
    sys.exit(lookup_main())
