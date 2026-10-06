"""What an ``anim`` target names: an IFP file, an IMG entry, an index SID, or a whole IMG / profile. Stdlib only.

Targets (``load_ifp``):

* ``ifp:<pack>`` / ``anim:<pack>/<name>`` - the active IFP of the profile's index (``anim:`` also selects one
  animation); a bare pack name (``ped``, ``bar``) is tried as ``ifp:<name>`` when no such file exists;
* ``file:<relpath>`` - a file of the profile (``file:anim/ped.ifp``, ``file:models/gta3.img/bar.ifp``);
* a path: absolute, relative to the current folder or to the profile root, or ``<img>/<entry>.ifp``
  (``anim/anim.img/bar.ifp``).

``iter_ifps`` lists the IFPs inside an IMG archive or the whole game folder of a profile (loose ``*.ifp`` and
every ``*.ifp`` entry of every ``*.img``) for ``satk anim list <img>`` and ``satk anim roundtrip``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from ..core.errors import SatkError
from ..core.paths import jpath, open_ro

__all__ = ["IfpSource", "load_ifp", "load_dff", "iter_ifps", "find_path"]


@dataclass
class IfpSource:
    """Bytes of one IFP. ``label`` is what answers show (``file:anim/ped.ifp`` or a path)."""

    label: str
    name: str
    data: bytes
    anim: str | None = None
    path: Path | None = None

    @property
    def stem(self) -> str:
        return self.name.rsplit(".", 1)[0] if "." in self.name else self.name


def _profile_root(profile: str) -> Path | None:
    from ..core.paths import profile_root

    try:
        return Path(profile_root(profile))
    except SatkError:
        return None


def _rel_label(p: Path, profile: str) -> str:
    root = _profile_root(profile)
    if root is not None:
        try:
            rel = Path(os.path.abspath(p)).relative_to(Path(os.path.abspath(root)))
            return "file:" + rel.as_posix().lower()
        except ValueError:
            pass
    return jpath(p)


def find_path(target: str, profile: str, *, cwd: bool = True) -> Path | None:
    """An existing file or folder: absolute, relative to the current folder, to the profile root, else to
    ``<work>/out/anim`` (where the ``anim`` operations write)."""
    p = Path(target)
    if p.is_absolute():
        return p if p.exists() else None
    if cwd and (Path.cwd() / p).exists():
        return Path(os.path.abspath(Path.cwd() / p))
    root = _profile_root(profile)
    if root is not None:
        from ..formats.dat import resolve_ci

        hit = resolve_ci(root, target.replace("/", "\\"))
        if hit is not None and hit.exists():
            return hit
    if cwd and ".." not in p.parts:
        from ..core.paths import cfg

        out = Path(os.path.abspath(cfg().paths.work)) / "out" / "anim" / p
        if out.exists():
            return out
    return None


def _read_img_entry(arc: Path, entry: str, label: str) -> IfpSource:
    from ..formats.img import ImgArchive
    from ..formats.rw import FormatError

    try:
        with ImgArchive.open(arc) as a:
            e = a.find(entry)
            if e is None:
                raise SatkError("NOT_FOUND", f"no entry {entry!r} in {jpath(arc)}",
                                hint=f"satk anim list {jpath(arc)}")
            return IfpSource(label, e.name, a.read(e))
    except FormatError as ex:
        raise SatkError("UNSUPPORTED", f"cannot read {jpath(arc)}: {ex}") from None


def _from_index(sid: str, profile: str) -> IfpSource:
    from ..index.api import open_index, read_blob_bytes

    db = open_index(profile)
    low = sid.lower()
    anim = None
    if low.startswith("anim:"):
        got = db.get(sid)
        anim = got.get("name")
        sid = got["ifp"]
    info = db.get(sid)
    ref = db.blob_ref(info["file"])
    return IfpSource(info["file"], ref.name, read_blob_bytes(ref), anim=anim)


def load_ifp(target: str, profile: str = "vanilla") -> IfpSource:
    """Read the IFP a target names (module docstring). ``NOT_FOUND`` with a hint when there is none."""
    s = str(target or "").strip().strip('"')
    if not s:
        raise SatkError("BAD_PARAMS", "no IFP given", hint="satk anim list ifp:ped (or a path to an .ifp)")
    low = s.lower().replace("\\", "/")
    if low.startswith(("ifp:", "anim:")):
        return _from_index(s, profile)
    is_sid = low.startswith("file:")
    path = (s[5:] if is_sid else s).replace("\\", "/")
    cut = path.lower().find(".img/")
    if cut > 0:
        arc = find_path(path[:cut + 4], profile, cwd=not is_sid)
        if arc is None or not arc.is_file():
            raise SatkError("NOT_FOUND", f"no such IMG archive: {path[:cut + 4]}",
                            hint="an IMG path relative to the current folder or the profile root, e.g. "
                                 "anim/anim.img/bar.ifp")
        entry = path[cut + 5:]
        return _read_img_entry(arc, entry, _rel_label(arc, profile) + "/" + entry.lower())
    p = find_path(path, profile, cwd=not is_sid)
    if p is not None and p.is_file():
        with open_ro(p) as f:
            data = f.read()
        return IfpSource(_rel_label(p, profile), p.name, data, path=p)
    if p is None and not is_sid and "/" not in path and "." not in path:
        try:
            return _from_index(f"ifp:{path}", profile)
        except SatkError as e:
            if e.code not in ("NOT_FOUND", "NOT_READY", "INDEX_MISSING"):
                raise
    if p is not None and p.is_dir():
        raise SatkError("BAD_PARAMS", f"{jpath(p)} is a folder, not an IFP",
                        hint="give one .ifp (satk anim list <folder> lists the IFPs of a folder or an .img)")
    raise SatkError("NOT_FOUND", f"no such IFP: {s}",
                    hint="a .ifp path (absolute, current folder or profile root), <img>/<entry>.ifp, ifp:<pack> "
                         "or anim:<pack>/<name>; satk anim list anim/anim.img lists an archive")


def iter_ifps(root: Path, *, label: Callable[[Path], str] | None = None) -> Iterator[tuple[str, Callable[[], bytes]]]:
    """``(label, reader)`` of every IFP under ``root``: a folder (loose ``*.ifp`` and the ``*.ifp`` entries of
    every ``*.img``, sorted) or one ``.img``."""
    from ..formats.img import ImgArchive
    from ..formats.rw import FormatError

    lab = label or (lambda p: jpath(p))
    if root.is_file():
        files = [root]
    else:
        files = []
        for dp, dn, fn in os.walk(root):
            dn.sort()
            for f in sorted(fn):
                if f.lower().endswith((".ifp", ".img")):
                    files.append(Path(dp) / f)
    for p in files:
        if p.suffix.lower() == ".ifp":
            yield lab(p), (lambda p=p: _read_file(p))
            continue
        try:
            a = ImgArchive.open(p)
        except (FormatError, OSError):
            continue
        with a:
            for e in a.entries:
                if e.ext == "ifp":
                    yield f"{lab(p)}/{e.name.lower()}", (lambda a=a, e=e: a.read(e))


def _read_file(p: Path) -> bytes:
    with open_ro(p) as f:
        return f.read()


def load_dff(spec: str, profile: str = "vanilla") -> tuple[str, str, bytes]:
    """``(label, name, bytes)`` of a model DFF: ``model:<id|name>``, ``dff:<name>``, a bare model id or name, a
    ``.dff`` path or ``<img>/<entry>.dff``."""
    s = str(spec).strip().strip('"')
    low = s.lower().replace("\\", "/")
    if low.endswith(".dff") or ".img/" in low:
        is_sid = low.startswith("file:")
        path = (s[5:] if is_sid else s).replace("\\", "/")
        cut = path.lower().find(".img/")
        if cut > 0:
            arc = find_path(path[:cut + 4], profile, cwd=not is_sid)
            if arc is None or not arc.is_file():
                raise SatkError("NOT_FOUND", f"no such IMG archive: {path[:cut + 4]}")
            src = _read_img_entry(arc, path[cut + 5:], _rel_label(arc, profile) + "/" + path[cut + 5:].lower())
            return src.label, src.stem, src.data
        p = find_path(path, profile, cwd=not is_sid)
        if p is None or not p.is_file():
            raise SatkError("NOT_FOUND", f"no such DFF: {s}", hint="a .dff path, model:<id|name> or dff:<name>")
        with open_ro(p) as f:
            return _rel_label(p, profile), p.stem, f.read()
    from ..index.api import open_index, read_blob_bytes

    db = open_index(profile)
    if low.startswith("dff:"):
        ref = db.blob_ref(s)
        return str(ref.sid), ref.name.rsplit(".", 1)[0], read_blob_bytes(ref)
    key = s[6:] if low.startswith("model:") else s
    mf = db.model_files(int(key) if key.lstrip("-").isdigit() else key)
    if mf.dff is None:
        raise SatkError("NOT_FOUND", f"model:{mf.model_id} ({mf.name}) has no DFF")
    return f"model:{mf.model_id}", mf.name, read_blob_bytes(mf.dff)
