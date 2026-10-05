"""Where crash dumps and logs live: our MTA fork's ``Bin``, installed MTA builds, ``work/dumps``, the
configured single-player game, samples.

MTA writes ``<MTA>\\mta\\dumps\\private\\client_*.dmp`` (copy of ``mta\\core.dmp``) and appends text
reports to ``mta\\core.log``; the server writes ``<server>\\dumps\\private\\server_*.dmp`` and
``dumps\\server_pending_upload.log``. ``work/dumps`` is where ProcDump/cdb dumps go (report 19). In a
single-player game folder ``modloader\\modloader.log`` counts only when it holds a crash report.
"""

from __future__ import annotations

import os
from pathlib import Path

from .mta import parse_dump_name

__all__ = ["default_dirs", "find_files", "SAMPLE_DIR_PARTS", "SP_SAMPLE_DIR_PARTS"]

SAMPLE_DIR_PARTS = ("out", "crash", "sample")
SP_SAMPLE_DIR_PARTS = ("out", "crash", "sample-sp")
_NAMES = ("core.log", "server_pending_upload.log")
#: logs that count only when they contain a crash report (single-player)
_SP_NAMES = ("modloader.log",)
_SP_TAIL = 1 << 20
_MTA_SUBDIRS = (("MTA", "dumps", "private"), ("MTA", "dumps"), ("MTA",), ("mta", "dumps", "private"),
                ("server", "dumps", "private"), ("server", "dumps"))


def _mta_installs() -> list[Path]:
    """``Last Install Location`` of MTA:SA versions from the registry (Windows; read-only)."""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return []
    out: list[Path] = []
    root = r"SOFTWARE\Multi Theft Auto: San Andreas All"
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, sub) as sk:
                        v, _t = winreg.QueryValueEx(sk, "Last Install Location")
                except OSError:
                    continue
                if isinstance(v, str) and v.strip():
                    p = Path(v.strip())
                    if p not in out:
                        out.append(p)
    return out


def default_dirs() -> list[tuple[Path, str]]:
    """``[(directory, origin)]`` searched by ``satk crash list`` / ``--last`` (existing or not)."""
    from ..core.paths import cfg

    c = cfg().paths
    work = Path(os.path.abspath(c.work))
    out: list[tuple[Path, str]] = [(work / "dumps", "work")]
    roots: list[tuple[Path, str]] = []
    eng = c.get("engine")
    if eng is not None:
        roots.append((Path(eng) / "Bin", "fork"))
    roots += [(p, "mta") for p in _mta_installs()]
    for r, origin in roots:
        out += [(r.joinpath(*parts), origin) for parts in _MTA_SUBDIRS]
    for key in ("game_root", "installed"):
        g = c.get(key)
        if g is not None:
            out.append((Path(g) / "modloader", "game"))
    out.append((work.joinpath(*SAMPLE_DIR_PARTS), "sample"))
    out.append((work.joinpath(*SP_SAMPLE_DIR_PARTS), "sample"))
    seen, uniq = set(), []
    for d, o in out:
        k = os.path.normcase(str(d))
        if k not in seen:
            seen.add(k)
            uniq.append((d, o))
    return uniq


def find_files(dirs: list[tuple[Path, str]]) -> list[dict]:
    """Dumps and crash logs in ``dirs`` (not recursive), newest first."""
    out: list[dict] = []
    seen: set[str] = set()
    for d, origin in dirs:
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            name = e.name
            low = name.lower()
            if not (low.endswith(".dmp") or low in _NAMES or low in _SP_NAMES):
                continue
            try:
                if not e.is_file():
                    continue
                st = e.stat()
            except OSError:
                continue
            key = os.path.normcase(os.path.abspath(e.path))
            if key in seen:
                continue
            if low in _SP_NAMES and not _has_crash(Path(e.path), st.st_size):
                continue
            seen.add(key)
            info = parse_dump_name(name) if low.endswith(".dmp") else {}
            out.append({"path": Path(e.path), "kind": "dump" if low.endswith(".dmp") else "log", "size": st.st_size,
                        "mtime": st.st_mtime, "origin": origin, **info})
    out.sort(key=lambda r: (-r["mtime"], str(r["path"])))
    return out


def _has_crash(path: Path, size: int) -> bool:
    """Whether the tail of a single-player log holds a crash report."""
    from ..core.paths import open_ro
    from .sptext import has_crash

    try:
        with open_ro(path) as f:
            if size > _SP_TAIL:
                f.seek(size - _SP_TAIL)
            text = f.read(_SP_TAIL).decode("utf-8", "replace")
    except OSError:
        return False
    return has_crash(text)
