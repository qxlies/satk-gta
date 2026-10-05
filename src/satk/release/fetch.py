"""Verified downloads for the release build (the only network access of ``satk dev release``).

Files land in ``<work>/cache/release/`` under their published names and are reused while their
SHA-256 matches the lock. ``offline=True`` never touches the network: a missing or corrupt file is
``NOT_READY``. Only HTTPS URLs on python.org and PyPI are accepted (:data:`ALLOWED_HOSTS`).
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, ensure_writable, jpath

__all__ = ["ALLOWED_HOSTS", "TIMEOUT", "sha256_file", "get_json", "fetch", "check_url"]

#: Hosts the release build may download from.
ALLOWED_HOSTS = frozenset({"www.python.org", "pypi.org", "files.pythonhosted.org"})
#: Seconds per request.
TIMEOUT = 120
_UA = "satk-release (+https://pypi.org/project/satk-gta/)"


def sha256_file(path: str | os.PathLike, *, algo: str = "sha256") -> str:
    """Hex digest of a file (``algo``: any :mod:`hashlib` name, e.g. ``md5`` for python.org's sums)."""
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_url(url: str) -> str:
    """``url`` if it is HTTPS on an allowed host, else ``BAD_PARAMS``."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS:
        raise SatkError("BAD_PARAMS", f"release downloads are limited to https on {', '.join(sorted(ALLOWED_HOSTS))}: "
                        f"{url}")
    return url


def _open(url: str):
    req = urllib.request.Request(check_url(url), headers={"User-Agent": _UA})
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT)  # noqa: S310 - scheme and host checked above
    except OSError as e:
        raise SatkError("EXTERNAL_TOOL", f"download failed: {url}: {e}",
                        hint="check the network, or build with --offline from a filled work/cache/release") from None


def get_json(url: str) -> dict | list:
    """GET ``url`` and parse JSON (python.org and PyPI APIs)."""
    with _open(url) as r:
        data = r.read()
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise SatkError("PROTOCOL", f"bad JSON from {url}: {e}") from None


def fetch(url: str, dest: Path, *, sha256: str | None, size: int | None = None, offline: bool = False) -> Path:
    """``dest`` with the content of ``url``; reused when it matches ``sha256`` (and ``size``).

    Without ``sha256`` (``--relock``) the file is downloaded once and kept; the caller verifies it
    some other way and pins the digest. A mismatch after download is ``CHECK_FAILED`` and the file
    is not kept.
    """
    ensure_writable(dest)
    if dest.is_file() and (sha256 is None or sha256_file(dest) == sha256) \
            and (size is None or dest.stat().st_size == size):
        return dest
    if offline:
        raise SatkError("NOT_READY", f"{dest.name} is not in the release cache ({jpath(dest.parent)})",
                        hint="run satk dev release once without --offline (downloads from python.org/PyPI)")
    with _open(url) as r:
        data = r.read()
    if size is not None and len(data) != size:
        raise SatkError("CHECK_FAILED", f"{dest.name}: {len(data)} bytes downloaded, the lock says {size}",
                        data={"url": url})
    got = hashlib.sha256(data).hexdigest()
    if sha256 is not None and got != sha256:
        raise SatkError("CHECK_FAILED", f"{dest.name}: sha256 {got} does not match the lock ({sha256})",
                        hint="the file on the server changed or the download was tampered with; do not relock "
                             "blindly - compare with the publisher's checksums first", data={"url": url})
    atomic_write(dest, data)
    return dest
