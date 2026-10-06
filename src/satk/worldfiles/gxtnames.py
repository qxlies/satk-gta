"""Names of GXT keys. A GXT stores only JAMCRC32 hashes; names make exports readable. Stdlib only.

Sources, in order:

* ``data/worldfiles/gxt_keys.txt``: key names of the stock game (names only, no texts), recovered by
  :func:`scan` from the vanilla scripts, cutscenes, data files and ``gta_sa.exe`` (about 80 % of the
  16 588 keys of ``american.gxt``; the rest are shown as ``0x`` hashes);
* ``<work>/cache/worldfiles/gxt_keys-<profile>.txt``: names found by ``satk gxt keys --scan`` in a modded
  game (a total conversion's own ``main.scm`` names its new keys);
* names given in the document being written (they are hashed, so any name works).

:func:`scan` collects ``[A-Za-z0-9_]{1,8}`` tokens from the files (SCM text labels are 8-byte fields), keeps
those whose hash is a key of the target GXT, then grows the set from the numbering patterns of the found
names (``CRED001`` -> ``CRED000``..``CRED999``; ``INT1_AB`` -> ``INT1_AA``..``INT1_ZZ``) until nothing new
matches. Only names whose hash is in the GXT are ever reported, so guesses cannot add wrong names except
by a CRC collision (1 in 4 billion per candidate).
"""

from __future__ import annotations

import os
import re
import string
import zlib
from pathlib import Path

__all__ = ["shipped", "cached", "names_for", "scan", "cache_path", "save_cache"]

_TOKEN = re.compile(rb"[A-Za-z0-9_]{1,8}")
_SCAN_FILES = ("data/script/main.scm", "data/script/script.img", "anim/cuts.img", "gta_sa.exe")
_SCAN_DIRS = ("data",)
_NUM = re.compile(r"^(.*?)(\d{1,3})([A-Z]?)$")
_L = string.ascii_uppercase
_MAX_FILE = 16 << 20  # read chunk
_shipped: dict[int, str] | None = None


def _h(s: str) -> int:
    return zlib.crc32(s.encode("latin-1")) ^ 0xFFFFFFFF


def _parse(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for line in text.splitlines():
        n = line.strip().upper()
        if n and not n.startswith("#"):
            out.setdefault(_h(n), n)
    return out


def shipped() -> dict[int, str]:
    """Hash -> name of the stock key list shipped with satk."""
    global _shipped
    if _shipped is None:
        from ..core import resources

        try:
            _shipped = _parse(resources.read_text("worldfiles", "gxt_keys.txt"))
        except Exception:  # noqa: BLE001 - a missing list only costs readability
            _shipped = {}
    return _shipped


def cache_path(profile: str) -> Path:
    from ..core.paths import cfg

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", profile)
    return Path(os.path.abspath(cfg().paths.work)) / "cache" / "worldfiles" / f"gxt_keys-{safe}.txt"


def cached(profile: str | None) -> dict[int, str]:
    if not profile:
        return {}
    p = cache_path(profile)
    try:
        return _parse(p.read_text(encoding="utf-8"))
    except OSError:
        return {}


def names_for(profile: str | None = None) -> dict[int, str]:
    """Shipped names plus the scanned names of ``profile`` (cache)."""
    out = dict(shipped())
    out.update(cached(profile))
    return out


def save_cache(profile: str, names: dict[int, str]) -> Path:
    from ..core.paths import atomic_write

    p = cache_path(profile)
    body = "# GXT key names found by satk gxt keys --scan (names only)\n" + \
        "".join(n + "\n" for n in sorted(set(names.values())))
    atomic_write(p, body.encode("utf-8"))
    return p


def _files(root: Path) -> list[Path]:
    from ..formats.dat import resolve_ci

    seen: dict[str, Path] = {}
    for rel in _SCAN_FILES:
        p = resolve_ci(root, rel.replace("/", "\\"))
        if p is not None and p.is_file():
            seen[os.path.normcase(str(p))] = p
    for d in _SCAN_DIRS:
        base = resolve_ci(root, d)
        if base is None or not base.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(base):
            for f in sorted(files):
                p = Path(dirpath) / f
                seen.setdefault(os.path.normcase(str(p)), p)
    return [seen[k] for k in sorted(seen)]


def _grow(found: dict[int, str], want: set[int], rounds: int = 4) -> None:
    done_num: set[tuple[str, int, str]] = set()
    done_pre: set[tuple[str, int]] = set()
    frontier = set(found.values())
    for _ in range(rounds):
        new: set[str] = set()

        def hit(c: str) -> None:
            h = _h(c)
            if h in want and h not in found:
                found[h] = c
                new.add(c)

        for name in sorted(frontier):
            m = _NUM.match(name)
            if m:
                pre, dig, suf = m.groups()
                w = len(dig)
                key = (pre, w, suf)
                if key not in done_num:
                    done_num.add(key)
                    sufs = ("", suf) + (tuple(_L) if w <= 2 else ())
                    for ww in ((w, w + 1) if w < 3 else (w,)):
                        for num in range(10 ** ww):
                            ds = str(num).zfill(ww)
                            for s in dict.fromkeys(sufs if ww == w else ("", suf)):
                                c = pre + ds + s
                                if len(c) <= 8:
                                    hit(c)
            elif (name, 0) not in done_pre:
                done_pre.add((name, 0))
                for num in range(100):
                    for c in (f"{name}{num}", f"{name}{num:02d}", f"{name}_{num}"):
                        if len(c) <= 8:
                            hit(c)
            for cut, alpha in ((1, _L), (2, None)):
                if len(name) <= cut + 1 or not name[-cut:].isalpha():
                    continue
                pre = name[:-cut]
                if (pre, cut) in done_pre:
                    continue
                done_pre.add((pre, cut))
                if cut == 1:
                    for a in alpha:
                        hit(pre + a)
                else:
                    for a in _L:
                        for b in _L:
                            hit(pre + a + b)
        if not new:
            break
        frontier = new


def scan(root: Path, want: set[int], extra_files: list[Path] = ()) -> dict[int, str]:
    """Names (hash -> name) of the hashes in ``want`` found in the game folder ``root``."""
    from ..core.paths import open_ro

    found: dict[int, str] = {}
    for p in list(_files(Path(root))) + list(extra_files):
        toks: set[bytes] = set()
        try:
            with open_ro(p) as f:
                tail = b""
                while True:
                    chunk = f.read(_MAX_FILE)
                    if not chunk:
                        break
                    toks.update(_TOKEN.findall(tail + chunk))
                    tail = chunk[-16:]
        except OSError:
            continue
        for tok in toks:
            c = tok.decode("ascii").upper()
            h = _h(c)
            if h in want and h not in found:
                found[h] = c
    for h, n in shipped().items():
        if h in want:
            found.setdefault(h, n)
    _grow(found, want)
    return found
