"""What ``satk col gen`` reads: DFFs named by a SID, a file, a folder or an IMG (``satk.colgen.sources``).

* SIDs ``model:411``, ``model:infernus``, ``dff:infernus``, ``inst:<ipl>#<n>`` (and a bare model name that
  is not a path) resolve through the index of the profile: the model name, IDE section and TXD come along;
* a ``.dff`` path (absolute, relative to the current folder or to the profile's game root, or
  ``<archive>.img/<entry>.dff``); its stem is looked up in the index for the section and TXD (a
  replacement mod keeps the vanilla name) when the index exists;
* a folder: every ``*.dff`` in it (not recursive); an ``.img``: every ``.dff`` entry.

Game files are only read (``open_ro``). Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core.errors import SatkError
from ..core.paths import jpath, open_ro, profile_root

__all__ = ["DffSource", "resolve_sources", "SID_KINDS"]

SID_KINDS = ("model", "dff", "inst")


@dataclass
class DffSource:
    label: str
    name: str
    read: Callable[[], bytes]
    sec: str | None = None
    txd: str | None = None
    model_id: int | None = None


def _is_sid(target: str) -> bool:
    head, sep, _rest = target.partition(":")
    return bool(sep) and head.lower() in SID_KINDS


def _file_reader(p: Path) -> Callable[[], bytes]:
    def read() -> bytes:
        with open_ro(p) as fh:
            return fh.read()
    return read


def _find(path: str, profile: str) -> Path | None:
    from ..formats.dat import resolve_ci

    p = Path(path)
    if p.is_absolute():
        return p if p.exists() else None
    if (Path.cwd() / p).exists():
        return Path.cwd() / p
    try:
        hit = resolve_ci(profile_root(profile), path)
    except SatkError:
        hit = None
    return hit


def _index_info(profile: str, stem: str) -> tuple[str | None, str | None, int | None]:
    """``(sec, txd, model id)`` of the model named ``stem`` in the index (``None`` when unknown)."""
    try:
        from ..index.api import open_index
        from ..model3d.resolve import model_files

        mf = model_files(open_index(profile), stem)
    except (SatkError, OSError, ValueError):
        return None, None, None
    txd = mf.txd_chain[0].name.rsplit(".", 1)[0].lower() if mf.txd_chain else None
    return mf.sec, txd, mf.model_id


def _from_sid(target: str, profile: str) -> DffSource:
    from ..index.api import read_blob_bytes
    from ..model3d.resolve import resolve

    src = resolve(target, profile)
    if src.dff is None:
        raise SatkError("NOT_FOUND", f"{target} has no DFF in the {profile} profile",
                        hint=f"satk asset get {src.sid}")
    ref = src.dff
    txd = src.txd_chain[0].name.rsplit(".", 1)[0].lower() if src.txd_chain else None
    return DffSource(src.sid, src.name, lambda: read_blob_bytes(ref), src.sec, txd, src.model_id)


def resolve_sources(target: str, profile: str = "vanilla", *, use_index: bool = True) -> list[DffSource]:
    """DFF sources named by ``target`` (see the module doc)."""
    from ..formats.img import ImgArchive
    from ..formats.rw import FormatError

    t = target.strip()
    if not t:
        raise SatkError("BAD_PARAMS", "empty target", hint="satk col gen model:1337 --mode hull")
    if _is_sid(t):
        return [_from_sid(t, profile)]
    norm = t.replace("\\", "/")
    cut = norm.lower().find(".img/")
    if cut > 0:
        arc = _find(norm[:cut + 4], profile)
        if arc is None:
            raise SatkError("NOT_FOUND", f"no archive {norm[:cut + 4]!r}", hint="give a path relative to the game root")
        entry = norm[cut + 5:]
        with ImgArchive.open(arc) as a:
            e = a.find(entry)
            if e is None:
                raise SatkError("NOT_FOUND", f"no entry {entry!r} in {jpath(arc)}",
                                hint=f"satk formats ls {norm[:cut + 4]} --name {entry.rsplit('.', 1)[0]}")
            data = a.read(e)
        stem = e.name.rsplit(".", 1)[0]
        sec, txd, mid = _index_info(profile, stem) if use_index else (None, None, None)
        return [DffSource(f"{norm[:cut + 4]}/{e.name}", stem, lambda: data, sec, txd, mid)]
    p = _find(t, profile)
    if p is None:
        if "/" not in norm and "." not in t:                     # a bare model name
            return [_from_sid(f"model:{t}", profile)]
        raise SatkError("NOT_FOUND", f"no file, folder or SID {target!r}",
                        hint="a .dff path, a folder of .dff files, an .img, <img>/<entry>.dff or model:<id|name>")
    out: list[DffSource] = []
    if p.is_dir():
        files = sorted((f for f in p.iterdir() if f.is_file() and f.suffix.lower() == ".dff"),
                       key=lambda f: f.name.lower())
        for f in files:
            sec, txd, mid = _index_info(profile, f.stem) if use_index else (None, None, None)
            out.append(DffSource(jpath(f), f.stem, _file_reader(f), sec, txd, mid))
    elif p.suffix.lower() == ".img":
        try:
            with ImgArchive.open(p) as a:
                for e in a.entries:
                    if e.ext != "dff":
                        continue
                    stem = e.name.rsplit(".", 1)[0]

                    def read(_p=p, _e=e) -> bytes:
                        with ImgArchive.open(_p) as aa:
                            return aa.read(_e)
                    sec, txd, mid = _index_info(profile, stem) if use_index else (None, None, None)
                    out.append(DffSource(f"{jpath(p)}/{e.name}", stem, read, sec, txd, mid))
        except FormatError as e:
            raise SatkError("UNSUPPORTED", f"cannot read {jpath(p)}: {e}") from None
    elif p.suffix.lower() == ".dff":
        sec, txd, mid = _index_info(profile, p.stem) if use_index else (None, None, None)
        out.append(DffSource(jpath(p), p.stem, _file_reader(p), sec, txd, mid))
    else:
        raise SatkError("BAD_PARAMS", f"{jpath(p)} is not a .dff, a folder or an .img",
                        hint="satk col gen <model.dff|folder|archive.img|model:ID>")
    if not out:
        raise SatkError("NOT_FOUND", f"no .dff files in {jpath(p)}")
    return out
