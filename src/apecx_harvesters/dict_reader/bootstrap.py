"""Bootstrap the apecx synonym dictionary from a published Globus path.

The user-facing arm relies on a published copy of the dictionary at
``${APECX_DICT_PUBLIC_BASE_URL}/`` — typically the Globus HTTPS endpoint
for the "APECx Data at Argonne LCF" collection, path
``/apecx-ramanathan-anl/public/synonyms_dictionary/``. Operators set
``APECX_DICT_PUBLIC_BASE_URL`` once during install; the bootstrap then
fetches the manifest + compressed dict on demand.

File layout at the public path::

    MANIFEST.json
    dictionary-<version>.sqlite.gz

The manifest pins the filename, expected sha256, schema version, and
``built_at`` so the user-facing arm can decide whether to refresh.

Authentication: anonymous HTTPS GET. If the public path is ACL'd to
require auth, the operator either reconfigures the ACL or runs the
backend's transfer-based path (which requires Globus credentials and
is NOT the user-facing arm's concern).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from apecx_harvesters.dict_reader.loader import default_dictionary_path
from apecx_harvesters.dict_reader.schema import BuildManifest
from apecx_harvesters.dict_reader.sqlite_reader import SQLiteDictionaryReader

log = logging.getLogger(__name__)

# Schema versions this bootstrap accepts. Mirrors
# SQLiteDictionaryReader.SUPPORTED_SCHEMA_MAJOR. Bump in lockstep.
SUPPORTED_SCHEMA_MAJOR: tuple[int, ...] = (1,)

# The manifest filename at the public path. Stable contract.
MANIFEST_FILENAME = "MANIFEST.json"

_PUBLIC_BASE_ENV = "APECX_DICT_PUBLIC_BASE_URL"
_BOOTSTRAP_AGENT = "apecx-dict-reader/bootstrap"

# Production default — the Argonne LCF public path where the canonical
# APECx dictionary is published. Treated as public knowledge (cited in
# install docs + runbooks) so a clean install can bootstrap anonymously
# without the user pre-exporting any URL. Override via env var or the
# explicit ``override=`` argument for staging / mirror deployments.
DEFAULT_PUBLIC_BASE_URL = (
    "https://g-958ce2.fd635.8443.data.globus.org"
    "/apecx-ramanathan-anl/public/synonyms_dictionary"
)


@dataclass(frozen=True)
class PublishedManifest:
    """Subset of MANIFEST.json the bootstrap needs.

    The full BuildManifest lives inside the SQLite file. This sidecar
    manifest at the public path is a small JSON that tells the bootstrap
    WHICH file to download and how to verify it — without re-downloading
    a 100+ MB SQLite to discover its version.
    """

    schema_version: str
    dictionary_version: str
    built_at: str
    dictionary_filename: str
    dictionary_sha256: str
    dictionary_size_bytes: int
    compression: str

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "PublishedManifest":
        required = (
            "schema_version", "dictionary_version", "built_at",
            "dictionary_filename", "dictionary_sha256",
            "dictionary_size_bytes", "compression",
        )
        missing = [k for k in required if k not in raw]
        if missing:
            raise ValueError(
                f"MANIFEST.json missing required keys: {missing}"
            )
        return cls(
            schema_version=str(raw["schema_version"]),
            dictionary_version=str(raw["dictionary_version"]),
            built_at=str(raw["built_at"]),
            dictionary_filename=str(raw["dictionary_filename"]),
            dictionary_sha256=str(raw["dictionary_sha256"]),
            dictionary_size_bytes=int(raw["dictionary_size_bytes"]),
            compression=str(raw["compression"]),
        )


def get_public_base_url(override: str | None = None) -> str:
    """Resolve the published-dict base URL.

    Order of precedence: explicit override → environment variable →
    :data:`DEFAULT_PUBLIC_BASE_URL`. The default points at the
    canonical APECx public path so a clean install needs zero
    pre-configuration; operators can still pin a staging URL via the
    env var or the explicit argument.
    """
    if override:
        return override.rstrip("/")
    env_val = os.environ.get(_PUBLIC_BASE_ENV)
    if env_val:
        return env_val.rstrip("/")
    return DEFAULT_PUBLIC_BASE_URL.rstrip("/")


def fetch_manifest(
    *, base_url: str | None = None, timeout: float = 30.0
) -> PublishedManifest:
    """GET ``${base_url}/MANIFEST.json`` and parse it."""
    base = get_public_base_url(base_url)
    url = f"{base}/{MANIFEST_FILENAME}"
    log.info("fetching manifest from %s", url)
    req = Request(url, headers={"User-Agent": _BOOTSTRAP_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    return PublishedManifest.from_json(raw)


def _validate_supported(manifest: PublishedManifest) -> None:
    """Refuse to bootstrap a dict whose schema version is unsupported.

    Loud fail rather than silent download — a v2 dict will not load
    correctly with a v1 reader, and we'd rather surface that at update
    time than at first query time.
    """
    try:
        major = int(manifest.schema_version.split(".", 1)[0])
    except ValueError as exc:
        raise ValueError(
            f"invalid schema_version {manifest.schema_version!r} "
            f"in published MANIFEST.json"
        ) from exc
    if major not in SUPPORTED_SCHEMA_MAJOR:
        raise RuntimeError(
            f"published dictionary uses schema major v{major} which is not "
            f"supported by this reader (supports: {SUPPORTED_SCHEMA_MAJOR}). "
            f"Upgrade apecx-harvesters to a version that supports v{major}."
        )
    if manifest.compression not in ("gzip", "none"):
        raise RuntimeError(
            f"unknown compression {manifest.compression!r} — only "
            f"'gzip' and 'none' are supported"
        )


def _download_with_progress(
    url: str, dest: Path, expected_size: int, *,
    timeout: float = 600.0, quiet: bool = False,
) -> None:
    """GET ``url`` and stream it to ``dest`` with optional progress."""
    req = Request(url, headers={"User-Agent": _BOOTSTRAP_AGENT})
    if not quiet:
        sys.stderr.write(f"downloading {url}\n")
    with urlopen(req, timeout=timeout) as resp, dest.open("wb") as fh:
        total = expected_size
        downloaded = 0
        chunk = 64 * 1024
        last_pct = -1
        while True:
            buf = resp.read(chunk)
            if not buf:
                break
            fh.write(buf)
            downloaded += len(buf)
            if not quiet and total > 0:
                pct = int(downloaded * 100 / total)
                if pct != last_pct and pct % 5 == 0:
                    sys.stderr.write(
                        f"\r  {downloaded:,} / {total:,} bytes ({pct}%)"
                    )
                    sys.stderr.flush()
                    last_pct = pct
    if not quiet:
        sys.stderr.write("\n")


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def current_local_version(dest: Path | None = None) -> str | None:
    """Return the dictionary_version of the local dict, or None if absent."""
    target = Path(dest) if dest else default_dictionary_path()
    if not target.exists():
        return None
    try:
        reader = SQLiteDictionaryReader(target)
        try:
            return reader.read_manifest().dictionary_version
        finally:
            reader.close()
    except Exception as exc:
        log.warning("cannot read local manifest from %s: %s", target, exc)
        return None


def bootstrap_dictionary(
    *,
    base_url: str | None = None,
    dest: Path | None = None,
    force: bool = False,
    quiet: bool = False,
    timeout: float = 600.0,
) -> Path:
    """Ensure the local dict is current with the published version.

    Returns the path to the on-disk dictionary file. No-op when the
    local version already matches the published version and ``force``
    is False.

    Steps:
      1. Fetch MANIFEST.json from the public base URL.
      2. If local exists AND its version matches AND not ``force``,
         return the local path unchanged.
      3. Download the compressed dictionary to a temp file.
      4. Verify its sha256 against the manifest.
      5. Decompress to ``dest`` (atomic rename via temp file in same dir).

    Steps 3-5 are NEVER applied to ``dest`` directly — a partial download
    must never leave a half-baked file at the canonical path. The atomic
    rename at the end is the only mutation visible to the loader.
    """
    target = Path(dest) if dest else default_dictionary_path()
    manifest = fetch_manifest(base_url=base_url)
    _validate_supported(manifest)

    if not force:
        local_version = current_local_version(target)
        if local_version == manifest.dictionary_version:
            log.info(
                "local dict already at version %s — no update needed",
                local_version,
            )
            if not quiet:
                sys.stderr.write(
                    f"local dict already at version {local_version}; "
                    f"no update needed\n"
                )
            return target

    base = get_public_base_url(base_url)
    download_url = f"{base}/{manifest.dictionary_filename}"
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="apecx-dict-bootstrap-") as td_str:
        td = Path(td_str)
        compressed_path = td / manifest.dictionary_filename
        _download_with_progress(
            download_url, compressed_path,
            expected_size=manifest.dictionary_size_bytes,
            timeout=timeout, quiet=quiet,
        )

        actual_sha = _sha256_of(compressed_path)
        if actual_sha != manifest.dictionary_sha256:
            raise RuntimeError(
                f"sha256 mismatch on downloaded dictionary: expected "
                f"{manifest.dictionary_sha256}, got {actual_sha}. "
                f"Download corrupted or manifest stale; re-run with --force."
            )

        if manifest.compression == "gzip":
            decompressed_path = td / "dictionary.sqlite"
            if not quiet:
                sys.stderr.write("decompressing...\n")
            with gzip.open(compressed_path, "rb") as src, decompressed_path.open(
                "wb"
            ) as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        else:
            decompressed_path = compressed_path

        # Validate the dict is loadable before swapping it in. Catches
        # the case where compression succeeded but the dict itself is
        # malformed — better to keep the old local copy than overwrite
        # with a broken one.
        try:
            r = SQLiteDictionaryReader(decompressed_path)
            r.close()
        except Exception as exc:
            raise RuntimeError(
                f"downloaded dictionary failed to load: {exc}"
            ) from exc

        # Atomic move: replace via os.replace into target's directory.
        # We can't shutil.move across filesystems without losing atomicity,
        # so copy to target's directory first then rename in-place.
        staging = target.with_suffix(target.suffix + ".staging")
        shutil.copy2(decompressed_path, staging)
        os.replace(staging, target)

    if not quiet:
        sys.stderr.write(
            f"dictionary updated to version {manifest.dictionary_version} "
            f"at {target}\n"
        )
    log.info(
        "bootstrap complete: version %s at %s",
        manifest.dictionary_version, target,
    )
    return target


def ensure_dictionary_available(
    *, base_url: str | None = None, quiet: bool = True
) -> Path:
    """Return a usable dictionary path. Bootstrap if missing.

    Convenience wrapper used by the user-facing arm on first query.
    Differs from ``bootstrap_dictionary`` in that it never tries to
    UPDATE an existing local dict — only bootstraps when nothing is
    locally present. This avoids surprise downloads during a normal
    query session; explicit updates go through ``apecx-dict-update``.
    """
    target = default_dictionary_path()
    if target.exists():
        return target
    return bootstrap_dictionary(
        base_url=base_url, dest=target, force=True, quiet=quiet,
    )
