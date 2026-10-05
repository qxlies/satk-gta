"""Keep game assets and decompiled code out of git (SPEC §2.5, R14).

A file is a violation if it

* starts with an asset magic: ``VER2`` ``COLL`` ``COL2`` ``COL3`` ``COL4`` ``bnry`` ``ANP3`` ``ANPK``;
* starts with a RenderWare chunk header: ``u32 type`` in {0x10 clump, 0x16 texdictionary,
  0x0E frame list, 0x2B UV anim dict} and a plausible library ID (``rw_version`` 3.1–3.7);
* is a PNG/JPEG (by content or extension) outside ``docs/img/``;
* has a game-asset extension (``.img .dff .txd .col .ifp``);
* lives under a path containing ``gta-reversed`` or ``samp-source``;
* is larger than 10 MiB.

Synthetic test fixtures that intentionally look like assets (``tests/core/data/fake_rw.bin``)
are listed with their sha256 in ``.assetguard-allow`` at the repo root. The allowlist is
honored when scanning the repository (``--staged``, ``--all``); an explicit file list is
checked strictly unless ``--allowlist`` is given.
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator

from .errors import SatkError

__all__ = [
    "MAGICS",
    "RW_TYPES",
    "ASSET_EXTS",
    "MAX_BYTES",
    "ALLOWLIST_FILE",
    "Finding",
    "rw_version",
    "plausible_libid",
    "sniff",
    "check_blob",
    "load_allowlist",
    "scan_files",
    "scan_staged",
    "scan_all",
]

MAGICS: dict[bytes, str] = {
    b"VER2": "IMG v2 archive", b"COLL": "COL1 collision", b"COL2": "COL2 collision",
    b"COL3": "COL3 collision", b"COL4": "COL4 collision", b"bnry": "binary IPL",
    b"ANP3": "IFP animation (ANP3)", b"ANPK": "IFP animation (ANPK)",
}
RW_TYPES: dict[int, str] = {0x10: "clump", 0x16: "texdictionary", 0x0E: "frame list", 0x2B: "UV anim dict"}
ASSET_EXTS = frozenset({".img", ".dff", ".txd", ".col", ".ifp"})
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg"})
FORBIDDEN_PARTS = ("gta-reversed", "samp-source")
IMAGE_DIR = "docs/img/"
MAX_BYTES = 10 * 1024 * 1024
ALLOWLIST_FILE = ".assetguard-allow"
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_HEAD = 64


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    rule: str
    detail: str


def rw_version(libid: int) -> int:
    """RenderWare version from a chunk library ID (``0x1803FFFF`` -> ``0x36003``)."""
    if libid & 0xFFFF0000:
        return (((libid >> 14) & 0x3FF00) + 0x30000) | ((libid >> 16) & 0x3F)
    return libid << 8  # pre-3.1 style


def plausible_libid(libid: int) -> bool:
    """True for library IDs real RW 3.1–3.7 files use (top 3 bits clear, version in range)."""
    if libid & 0xFFFF0000:
        if libid >> 29:
            return False
    v = rw_version(libid)
    return 0x31000 <= v <= 0x37FFF


def sniff(head: bytes) -> tuple[str, str] | None:
    """(rule, detail) if the first bytes look like a game asset or an image, else ``None``."""
    if len(head) >= 4 and head[:4] in MAGICS:
        return "magic", f"{MAGICS[head[:4]]} ({head[:4].decode('ascii')})"
    if len(head) >= 12:
        t, _size, lib = struct.unpack_from("<III", head)
        if t in RW_TYPES and plausible_libid(lib):
            return "rw-chunk", f"RenderWare {RW_TYPES[t]} chunk, RW 0x{rw_version(lib):05x}"
    if head.startswith(_PNG):
        return "image", "PNG data"
    if head.startswith(_JPEG):
        return "image", "JPEG data"
    return None


def _norm_rel(rel: str) -> str:
    return rel.replace("\\", "/")


def check_blob(relpath: str, head: bytes, size: int) -> list[Finding]:
    """All findings for one file given its repo-relative path, first bytes and size."""
    rel = _norm_rel(relpath)
    low = rel.lower()
    out: list[Finding] = []
    for part in FORBIDDEN_PARTS:
        if part in low:
            out.append(Finding(rel, "forbidden-path", f"path contains '{part}' (reference code stays out of git)"))
            break
    ext = PurePosixPath(low).suffix
    in_img_dir = low.startswith(IMAGE_DIR) or f"/{IMAGE_DIR}" in low
    if ext in ASSET_EXTS:
        out.append(Finding(rel, "asset-ext", f"game asset extension {ext}"))
    s = sniff(head)
    if s is not None:
        rule, detail = s
        if rule == "image":
            if not in_img_dir:
                out.append(Finding(rel, "image", f"{detail} outside {IMAGE_DIR}"))
        else:
            out.append(Finding(rel, rule, detail))
    elif ext in IMAGE_EXTS and not in_img_dir:
        out.append(Finding(rel, "image", f"{ext} file outside {IMAGE_DIR}"))
    if size > MAX_BYTES:
        out.append(Finding(rel, "large-file", f"{size} bytes > {MAX_BYTES}"))
    return out


def load_allowlist(repo: Path) -> dict[str, set[str]]:
    """``{relpath_lower: {sha256,...}}`` from ``<repo>/.assetguard-allow``."""
    path = Path(repo) / ALLOWLIST_FILE
    out: dict[str, set[str]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            continue
        out.setdefault(_norm_rel(parts[1].strip()).lower(), set()).add(parts[0].lower())
    return out


def _allowed(rel: str, data: bytes, allow: dict[str, set[str]]) -> bool:
    shas = allow.get(_norm_rel(rel).lower())
    return bool(shas) and hashlib.sha256(data).hexdigest() in shas


def _git(repo: Path, *args: str, input_: bytes | None = None) -> bytes:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], input=input_, capture_output=True, check=False)
    except FileNotFoundError:
        raise SatkError("EXTERNAL_TOOL", "git is not installed or not on PATH") from None
    if r.returncode != 0:
        raise SatkError("EXTERNAL_TOOL", f"git {' '.join(args[:2])} failed: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout


def _rel_to(repo: Path | None, p: Path) -> str:
    if repo is not None:
        try:
            return p.resolve().relative_to(Path(repo).resolve()).as_posix()
        except ValueError:
            pass
    return p.as_posix()


def _iter_files(paths: Iterable[str | os.PathLike]) -> Iterator[Path]:
    skip = {".git", ".venv", "__pycache__", ".pytest_cache"}
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if d not in skip)
                for f in sorted(filenames):
                    yield Path(dirpath) / f
        elif p.is_file():
            yield p
        else:
            raise SatkError("NOT_FOUND", f"no such file: {p}")


def _check_path(rel: str, path: Path, allow: dict[str, set[str]] | None) -> list[Finding]:
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(_HEAD)
    found = check_blob(rel, head, size)
    shas = allow.get(_norm_rel(rel).lower()) if found and allow else None
    if shas:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() in shas:
            return []
    return found


def scan_files(paths: Iterable[str | os.PathLike], *, repo: Path | None = None,
               use_allowlist: bool = False) -> tuple[int, list[Finding]]:
    """Check files/directories on disk; returns (files checked, findings)."""
    allow = load_allowlist(repo) if (use_allowlist and repo is not None) else None
    n = 0
    findings: list[Finding] = []
    for p in _iter_files(paths):
        n += 1
        findings.extend(_check_path(_rel_to(repo, p), p, allow))
    return n, findings


def scan_all(repo: Path, *, use_allowlist: bool = True) -> tuple[int, list[Finding]]:
    """Check tracked + untracked-not-ignored files of the repository working tree."""
    out = _git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    rels = sorted({r for r in out.decode("utf-8").split("\0") if r})
    allow = load_allowlist(repo) if use_allowlist else None
    n = 0
    findings: list[Finding] = []
    for rel in rels:
        p = Path(repo) / rel
        if not p.is_file():
            continue  # deleted in the working tree
        n += 1
        findings.extend(_check_path(rel, p, allow))
    return n, findings


def _staged_contents(repo: Path, rels: list[str]) -> Iterator[tuple[str, bytes]]:
    if not rels:
        return
    req = "".join(f":{r}\n" for r in rels).encode("utf-8")
    data = _git(repo, "cat-file", "--batch", input_=req)
    pos = 0
    for rel in rels:
        nl = data.index(b"\n", pos)
        header = data[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        if len(header) < 3 or header[-1] == "missing":
            continue
        size = int(header[2])
        yield rel, data[pos:pos + size]
        pos += size + 1


def scan_staged(repo: Path, *, use_allowlist: bool = True) -> tuple[int, list[Finding]]:
    """Check the staged (index) content of added/copied/modified/renamed files."""
    out = _git(repo, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRT")
    rels = [r for r in out.decode("utf-8").split("\0") if r]
    allow = load_allowlist(repo) if use_allowlist else None
    findings: list[Finding] = []
    n = 0
    for rel, blob in _staged_contents(repo, rels):
        n += 1
        found = check_blob(rel, blob[:_HEAD], len(blob))
        if found and allow and _allowed(rel, blob, allow):
            continue
        findings.extend(found)
    return n, findings
