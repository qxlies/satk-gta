"""FXT text files (CLEO ``CLEO_TEXT/*.fxt``, Mod Loader ``*.fxt``): ``KEY text`` lines. Stdlib only.

One entry per line: the key, one space, the text up to the end of the line; lines starting with ``#`` are
comments. CLEO and Mod Loader (std.text) look FXT keys up before the GXT, so an FXT entry adds a key or
replaces the text of an existing GXT key in every table, without touching ``american.gxt``. Texts are
stored in the game's 8-bit code page (:mod:`satk.worldfiles.charset`); line ends are CRLF.
"""

from __future__ import annotations

import re

from . import charset as CS
from .gxt import KEY_LABEL_MAX

__all__ = ["parse_fxt", "write_fxt", "check_entries"]

_KEY = re.compile(r"^[A-Za-z0-9_\-]+$")


def parse_fxt(text: str) -> list[tuple[str, str]]:
    """Entries of an FXT (or of the same ``KEY text`` format in UTF-8)."""
    out = []
    for line in text.splitlines():
        s = line.lstrip()
        if not s or s.startswith("#"):
            continue
        k, _sep, rest = s.partition(" ")
        if "\t" in k:
            k, _sep, rest = s.partition("\t")
        out.append((k, rest))
    return out


def check_entries(entries: list[tuple[str, str]], warn: list[str]) -> None:
    """``ValueError`` on keys an FXT cannot hold; warnings for keys scripts cannot reference."""
    seen: dict[str, int] = {}
    long_keys = []
    for i, (k, v) in enumerate(entries):
        if not _KEY.match(k):
            raise ValueError(f"bad FXT key {k!r}: letters, digits, '_' and '-' only")
        if "\n" in v or "\r" in v:
            raise ValueError(f"text of {k} has a line break; use ~n~ for a new line in game text")
        ku = k.upper()
        if ku in seen:
            raise ValueError(f"key {k} appears twice (entries {seen[ku] + 1} and {i + 1})")
        seen[ku] = i
        if len(k) > KEY_LABEL_MAX:
            long_keys.append(k)
    if long_keys:
        warn.append(f"KEY_LENGTH: {', '.join(long_keys[:5])}{' ...' if len(long_keys) > 5 else ''}: longer than "
                    f"{KEY_LABEL_MAX} characters; SCM/CLEO text-label parameters and zone/vehicle labels hold 7")


def write_fxt(entries: list[tuple[str, str]], charset: str = "gta", title: str | None = None,
              warn: list[str] | None = None) -> bytes:
    """Bytes of an FXT file (CRLF lines, texts in ``charset``)."""
    w: list[str] = [] if warn is None else warn
    check_entries(entries, w)
    folded: list[str] = []
    lines = []
    if title:
        lines.append(b"# " + title.encode("ascii", errors="replace"))
    for k, v in entries:
        try:
            enc = CS.encode(v, charset, folded)
        except ValueError as e:
            raise ValueError(f"{k}: {e}") from None
        lines.append(k.encode("ascii") + b" " + enc)
    if folded:
        w.append("CHARSET: replaced " + " ".join(repr(c) for c in folded) + " by ASCII (no glyph in the fonts)")
    return b"\r\n".join(lines) + b"\r\n"
