"""Shared helpers of satk.worldfiles: input files of a profile, output folders, text lines, number formats.

Inputs are read with ``open_ro`` (game copies are never written); outputs go to ``<work>/out/worldfiles/``
(a plain folder or file name) or to an explicit absolute path that passes ``ensure_writable``. Stdlib only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath, open_ro, profile_root

__all__ = ["MAX_INPUT", "out_root", "out_folder", "out_file", "game_file", "read_bytes", "read_text",
           "split_lines", "join_lines", "eol_of", "fmt_g", "fmt_num", "parse_sets", "write_files", "rel_name"]

#: Upper bound for any input file (the biggest text/data file of SA is far below).
MAX_INPUT = 64 << 20
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SET = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.\[\]]*)\s*(\*=|\+=|-=|=)(.*)$", re.S)


def out_root() -> Path:
    """``<work>/out/worldfiles`` (not created)."""
    return Path(os.path.abspath(cfg().paths.work)) / "out" / "worldfiles"


def out_folder(name: str) -> Path:
    """``<work>/out/worldfiles/<name>/`` for a plain folder name (created); paths are refused."""
    if not _NAME.fullmatch(name or "") or set(name) == {"."}:
        raise SatkError("BAD_PARAMS", f"bad output folder name {name!r}",
                        hint="use letters, digits, '_', '-' and '.' (at most 64); the folder is made under "
                             "<work>/out/worldfiles/")
    d = ensure_writable(out_root() / name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def out_file(out: str | None, default: str) -> Path:
    """Output file: an absolute path (write-guarded) or a relative one under ``<work>/out/worldfiles/``."""
    p = Path(out or default)
    if p.is_absolute():
        return ensure_writable(p)
    if ".." in p.parts or not p.parts:
        raise SatkError("BAD_PARAMS", f"--out must be a file name, a relative path or an absolute path: {out}")
    return ensure_writable(out_root() / p)


def rel_name(path: Path) -> str:
    return jpath(path)


def game_file(file: str | None, profile: str, default_rel: str, what: str) -> Path:
    """A data file: ``file`` as given (a path, or a path relative to the profile's game root) or the
    profile's own ``default_rel`` (``data/info.zon``)."""
    from ..formats.dat import resolve_ci

    if file:
        p = Path(file)
        if p.is_file():
            return p
        if not p.is_absolute():
            try:
                hit = resolve_ci(profile_root(profile), file.replace("/", "\\"))
            except SatkError:
                hit = None
            if hit is not None and hit.is_file():
                return hit
        raise SatkError("NOT_FOUND", f"no {what} file {file!r}",
                        hint=f"give a path, or a path inside the game of profile {profile!r} ({default_rel})")
    try:
        root = profile_root(profile)
    except SatkError as e:
        raise SatkError("NOT_FOUND", f"profile {profile!r} has no game folder: {e.msg}",
                        hint="satk status; or pass the file path") from None
    hit = resolve_ci(root, default_rel.replace("/", "\\"))
    if hit is None or not hit.is_file():
        raise SatkError("NOT_FOUND", f"no {default_rel} in the game of profile {profile!r} ({jpath(root)})",
                        hint="satk status; or pass the file path")
    return hit


def read_bytes(path: str | os.PathLike, limit: int = MAX_INPUT) -> bytes:
    """Bytes of a file opened read-only; refuses files above ``limit``."""
    p = Path(path)
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"no file {jpath(p)}")
    with open_ro(p) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise SatkError("BAD_PARAMS", f"{jpath(p)} is larger than {limit >> 20} MB")
    return data


def read_text(path: str | os.PathLike) -> str:
    """A game text file decoded as latin-1 (every byte survives a round trip)."""
    return read_bytes(path).decode("latin-1")


def split_lines(text: str) -> list[tuple[str, str]]:
    """``[(content, eol)]`` with the exact end of each line (``\\r\\n``, ``\\n``, ``\\r`` or ``""`` at EOF)."""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"([^\r\n]*)(\r\n|\n|\r|$)", text):
        content, eol = m.group(1), m.group(2)
        if not content and not eol:
            if m.start() == len(text):
                break
            continue
        out.append((content, eol))
    return out


def join_lines(lines: list[tuple[str, str]]) -> str:
    return "".join(c + e for c, e in lines)


def eol_of(lines: list[tuple[str, str]], default: str = "\r\n") -> str:
    """The most common line ending of a file (for new lines)."""
    counts: dict[str, int] = {}
    for _c, e in lines:
        if e:
            counts[e] = counts.get(e, 0) + 1
    return max(counts, key=counts.get) if counts else default


def fmt_g(v: float) -> str:
    """A float as the C++ stream default the Rockstar tools wrote (``%g``, 3-digit exponent, ``.0`` on
    integers: ``200.0``, ``-4.57764e-005``) when that is exact; otherwise the shortest exact form."""
    v = float(v)
    s = "%g" % v
    if float(s) != v:
        s = repr(v)
    s = re.sub(r"e([+-])(\d+)$", lambda m: "e%s%03d" % (m.group(1), int(m.group(2))), s)
    if re.fullmatch(r"-?\d+", s):
        s += ".0"
    return s


def fmt_num(v, decimals: int = 2) -> str:
    """An int as is; a float with ``decimals`` places when that is exact, else the shortest exact form."""
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    s = f"{v:.{decimals}f}"
    if float(s) != float(v):
        s = repr(float(v))
    return s


def parse_sets(items: list[str] | None, example: str) -> list[tuple[str, str, str]]:
    """``field=value``, ``field*=k``, ``field+=k``, ``field-=k`` -> ``[(field, op, value text)]``."""
    out = []
    for it in items or []:
        m = _SET.match(it)
        if not m:
            raise SatkError("BAD_PARAMS", f"expected field=value, got {it!r}", hint=example)
        out.append((m.group(1).strip(), m.group(2), m.group(3).strip()))
    if not out:
        raise SatkError("BAD_PARAMS", "nothing to change", hint=example)
    return out


def write_files(folder: Path, files: dict[str, bytes]) -> list[str]:
    """Write ``{relative path: bytes}`` under ``folder`` atomically; returns the relative paths."""
    done = []
    for rel, data in sorted(files.items()):
        atomic_write(folder.joinpath(*rel.split("/")), data)
        done.append(rel)
    return done
