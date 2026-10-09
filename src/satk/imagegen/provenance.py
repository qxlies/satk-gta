"""Provenance: ``<file>.provenance.json`` sidecars and the ``PROVENANCE.md`` table.

A record names the tool, the endpoint host, the model, the prompt, the parameters, the UTC time, the response
text, the SHA-256 of the raw and the final image and the post-processing steps. It never holds a key, a header
or a request object: every record passes through :func:`~satk.imagegen.client.scrub` before it is written.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterable

from ..core.paths import atomic_write
from .client import scrub

__all__ = ["sha256_bytes", "sha256_file", "utc_now", "sidecar_path", "write_sidecar", "write_json", "provenance_md"]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sidecar_path(file: str | Path) -> Path:
    p = Path(file)
    return p.with_name(p.name + ".provenance.json")


def write_json(path: str | Path, doc: Any) -> Path:
    """Scrubbed, sorted, indented JSON (UTF-8, LF) written atomically under ``work``."""
    text = json.dumps(scrub(doc), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return atomic_write(path, text)


def write_sidecar(file: str | Path, record: dict) -> Path:
    """Write ``<file>.provenance.json``; returns the sidecar path."""
    rec = {"file": Path(file).name, **record}
    return write_json(sidecar_path(file), rec)


def _cell(s: Any) -> str:
    return str(scrub(s)).replace("|", "\\|").replace("\n", " ")


def provenance_md(title: str, intro: Iterable[str], header: list[str], rows: list[list[Any]]) -> str:
    """A Markdown page: title, intro paragraphs, one table (``header`` + ``rows``)."""
    out = [f"# {title}", ""]
    for line in intro:
        out += [line, ""]
    out.append("| " + " | ".join(header) + " |")
    out.append("|" + "|".join("---" for _ in header) + "|")
    for r in rows:
        out.append("| " + " | ".join(_cell(c) for c in r) + " |")
    out.append("")
    return "\n".join(out)
