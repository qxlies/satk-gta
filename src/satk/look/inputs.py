"""What a preview shows: SIDs, DFF files, mod folders and live sessions -> preview entries.

Accepted subjects (``satk blender preview <subject>``):

* a model SID or name: ``model:426``, ``426``, ``premier``, ``dff:premier``, ``inst:lae2_roads#4``;
* a DFF file: ``<path>/premier.dff`` (the ``premier.txd`` next to it is found automatically, or ``--txd``);
* a mod folder: every ``*.dff`` in it (with the TXD of the same name, or the folder's only TXD);
* ``session:NAME``: the scene of the running studio session ``NAME`` (``satk blender session``).

Every entry carries a Blender plan (``satk.blender.resolve``: cached copies under ``work``, never the
original files) and, for files and SIDs, the DFF bytes for the K1 numbers (``satk.style``). Stdlib only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import SatkError
from ..core.paths import jpath

__all__ = ["Entry", "MAX_ENTRIES", "resolve_subject", "resolve_peers", "session_name", "inst_row", "inst_model"]

#: At most this many entries from one folder (the rest is reported).
MAX_ENTRIES = 6


@dataclass
class Entry:
    key: str
    label: str
    kind: str                      # sid | file | session
    plan: dict | None = None       # Blender model plan (None for a session)
    sid: str | None = None         # game model SID (sid entries, or the --like model of a file)
    sec: str | None = None
    name: str | None = None
    path: str | None = None        # the user's file (file entries)
    warn: list[str] = field(default_factory=list)
    paint: list | None = None      # session: the like vehicle's paint colours for the scene's paint keys

    def spec(self) -> dict:
        """The entry as the Blender side reads it."""
        if self.kind == "session":
            out: dict = {"key": self.key, "label": self.label, "scene": True}
            if self.paint:
                out["paint"] = self.paint
            return out
        return {"key": self.key, "label": self.label, "plan": self.plan, "source": self.kind}

    def dff_bytes(self) -> bytes | None:
        if not self.plan or not self.plan.get("dff"):
            return None
        try:
            return Path(self.plan["dff"]).read_bytes()
        except OSError:
            return None


def session_name(subject: str) -> str | None:
    s = str(subject).strip()
    return s[8:] if s.lower().startswith("session:") else None


def _txd_list(txd: Any) -> list[str]:
    if not txd:
        return []
    if isinstance(txd, (str, os.PathLike)):
        return [p for p in str(txd).split(",") if p.strip()]
    return [str(t) for t in txd]


def _file_entry(path: Path, key: str, *, txd=None, like: str | None, profile: str, col: bool = False) -> Entry:
    from ..blender.resolve import plan_file

    r = plan_file(path, txd or None, like=like, profile=profile, col=col)
    m = r["model"]
    return Entry(key, path.name.lower(), "file", m, sid=m.get("like"), sec=m.get("sec"), name=m.get("name"),
                 path=jpath(path), warn=list(r.get("warnings") or []))


def _folder_entries(d: Path, *, like: str | None, profile: str, start: int,
                    col: bool = False) -> tuple[list[Entry], list[str]]:
    from ..blender.resolve import sibling_txd

    dffs = sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".dff"), key=lambda p: p.name.lower())
    if not dffs:
        dffs = sorted((p for p in d.rglob("*") if p.is_file() and p.suffix.lower() == ".dff"),
                      key=lambda p: str(p).lower())
    if not dffs:
        raise SatkError("NOT_FOUND", f"no .dff files in {jpath(d)}", hint="give a DFF file, a mod folder or a SID")
    warn = []
    if len(dffs) > MAX_ENTRIES:
        warn.append(f"TRUNCATED: {len(dffs)} DFF files in {d.name}; the first {MAX_ENTRIES} are shown")
        dffs = dffs[:MAX_ENTRIES]
    out = []
    for i, p in enumerate(dffs):
        txd = None
        if sibling_txd(p) is None:
            txds = [t for t in p.parent.iterdir() if t.is_file() and t.suffix.lower() == ".txd"]
            if len(txds) == 1:
                txd = [str(txds[0])]
        out.append(_file_entry(p, f"e{start + i}", txd=txd, like=like, profile=profile, col=col))
    return out, warn


def resolve_subject(subject: str, *, txd=None, like: str | None = None, profile: str = "vanilla",
                    start: int = 0, col: bool = False) -> tuple[list[Entry], list[str]]:
    """Entries of one subject (see the module docstring) and warnings; ``col`` also plans the collision
    (a game model's COL archive entry, or the ``.col`` of the same name next to a DFF file)."""
    s = str(subject).strip().strip('"')
    if not s:
        raise SatkError("BAD_PARAMS", "no subject", hint="satk blender preview model:426")
    name = session_name(s)
    if name is not None:
        from ..studio.launcher import check_name

        n = check_name(name or None)
        return [Entry(f"e{start}", f"session:{n}", "session", None, name=n)], []
    p = Path(s).expanduser()
    if p.is_dir():
        return _folder_entries(p, like=like, profile=profile, start=start, col=col)
    if s.lower().endswith(".dff") or p.is_file():
        if not p.is_file():
            raise SatkError("NOT_FOUND", f"no file at {jpath(p)}", hint="a .dff file, a mod folder or a SID")
        if p.suffix.lower() != ".dff":
            raise SatkError("BAD_PARAMS", f"{p.name}: expected a .dff file", hint="TXD files go to --txd")
        e = _file_entry(p, f"e{start}", txd=_txd_list(txd), like=like, profile=profile, col=col)
        return [e], list(e.warn)
    from ..blender.resolve import plan_model

    notes = []
    if s.lower().startswith("inst:"):
        mid = inst_model(s, profile)
        notes.append(f"NOTE: {s} places model:{mid}")
        s = f"model:{mid}"
    r = plan_model(s, profile=profile, col=col)
    m = r["model"]
    return [Entry(f"e{start}", m["name"], "sid", m, sid=m["sid"], sec=m.get("sec"), name=m["name"])], \
        notes + list(r.get("warnings") or [])


def inst_row(sid: str, profile: str = "vanilla") -> tuple[int, list[float]]:
    """``(model id, [x, y, z])`` of a placement SID ``inst:<ipl>#<n>``."""
    from ..index.api import open_index

    key = sid.split(":", 1)[1]
    ipl, _, idx = key.partition("#")
    if not idx.isdigit():
        raise SatkError("BAD_ID", f"bad inst SID {sid}", hint="inst:<ipl>#<index>, e.g. inst:lan_stream2#170")
    env = open_index(profile).query("SELECT i.model_id, i.x, i.y, i.z FROM inst i JOIN ipl p ON p.id = i.ipl_id "
                                    "WHERE p.name = ? COLLATE NOCASE AND i.idx = ?", [ipl, int(idx)], limit=1)
    rows = env.get("rows") or []
    if not rows:
        raise SatkError("NOT_FOUND", f"no placement {sid}", hint=f"satk asset find {ipl} --kind ipl")
    return int(rows[0][0]), [float(v) for v in rows[0][1:4]]


def inst_model(sid: str, profile: str = "vanilla") -> int:
    """Model id of a placement SID."""
    return inst_row(sid, profile)[0]


def resolve_peers(sids: list[str], *, profile: str = "vanilla", start: int = 1,
                  col: bool = False) -> tuple[list[Entry], list[str]]:
    """Entries of game models (lineup peers)."""
    out, warn = [], []
    for i, sid in enumerate(sids):
        es, w = resolve_subject(sid, profile=profile, start=start + i, col=col)
        out += es
        warn += w
    return out, warn
