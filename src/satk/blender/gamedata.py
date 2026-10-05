"""Direct game-data source for Blender jobs when there is no index yet (SPEC §5.3 WP-10:
"до готовности WP-03 — прямой разбор IPL через formats"). Owner: WP-10. Stdlib only.

It answers the two questions a Blender job asks, with the same value types as
``satk.index.api`` (:class:`~satk.index.api.BlobRef`, :class:`~satk.index.api.ColRef`,
:class:`~satk.index.api.ModelFiles`, :class:`~satk.index.api.InstRow`):

* :meth:`GameData.model_files` - DFF, TXD chain (own -> ``txdp`` parents -> ``vehicle`` for
  ``cars``) and COL of the active definition of a model;
* :meth:`GameData.insts` - placements in a box/radius with the index predicates
  (``match`` = ``center`` | ``aabb``, ``area``, ``lod`` = ``hd`` | ``lod`` | ``all``).

Load rules (same knowledge as the index, SPEC §4.2/§4.3.1): archives come from
``satk.formats.layout.archives`` ("first registered archive wins" per name); IDE files from the
DAT ``IDE`` lines (a later definition of an ID wins); text IPLs from ``IPL`` lines and binary
``<base>_streamN.ipl`` entries of the main archives; LOD targets: a text IPL indexes itself, a
``bnry`` indexes the text IPL of its base name; ``is_lod`` = "something points at it" (not
``lod == -1``, the DragonFF bug). ``aabb`` uses the DFF bounding sphere (the index uses the COL
box when it has one), so ``aabb`` results may differ slightly from the index.

Everything is read with ``open_ro``; parsing the vanilla profile takes about a second and is
cached per process.
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..core.errors import SatkError
from ..core.paths import jpath, open_ro
from ..formats import layout
from ..formats.dat import canon_relpath, parse_dat, read_text, resolve_ci
from ..formats.ide import parse_ide
from ..formats.img import ImgArchive
from ..formats.ipl import area as ipl_area
from ..formats.ipl import iflags as ipl_iflags
from ..formats.ipl import parse_ipl_binary, parse_ipl_text, stream_base
from ..formats.rw import FormatError
from ..index.api import BlobRef, ColRef, InstRow, ModelFiles, world_aabb

__all__ = ["GameData", "load", "clear_cache", "LOD_MARGIN"]

#: Extra search margin (m) around a box/radius for ``match="aabb"`` (LOD models reach ~300 m).
LOD_MARGIN = 400.0


@dataclass(slots=True)
class _Def:
    id: int
    name: str
    txd: str | None
    sec: str
    ide: str
    draw: float | None
    flags: int | None
    time_on: int | None = None
    time_off: int | None = None
    anim: str | None = None


@dataclass(slots=True)
class _Inst:
    ipl: str            # IPL name (stem, lower case): "lae2_stream0", "lae2"
    idx: int
    model_id: int
    pos: tuple[float, float, float]
    q: tuple[float, float, float, float]
    interior: int
    lod: tuple[str, int] | None
    is_lod: bool = False


@dataclass(slots=True)
class _Stats:
    ide_files: int = 0
    defs: int = 0
    text_ipl: int = 0
    binary_ipl: int = 0
    inst: int = 0
    lod_links: int = 0
    lod_unresolved: int = 0
    errors: list = field(default_factory=list)


class GameData:
    """Parsed DAT/IDE/IPL/IMG directories of one game root (see the module docstring)."""

    def __init__(self, root: Path, dat_files: list[str], img_order: str = "engine", profile: str = "vanilla"):
        self.root = Path(root)
        self.profile = profile
        self.dat_files = list(dat_files)
        self.img_order = img_order
        if not self.root.is_dir():
            raise SatkError("NOT_FOUND", f"game root not found: {jpath(self.root)}",
                            hint="check [profiles] in satk.toml / satk game info")
        self.stats = _Stats()
        self._lock = threading.Lock()
        self._imgs: dict[str, ImgArchive] = {}
        self._blob: dict[str, BlobRef] = {}          # "name.ext" (lower) -> first registered blob
        self._loose: dict[str, Path] = {}            # "name.ext" (lower) -> loose file
        self.defs: dict[int, _Def] = {}
        self.by_name: dict[str, int] = {}
        self.txdp: dict[str, str] = {}
        self.placements: list[_Inst] = []
        self._cols: dict[str, ColRef] | None = None
        self._bsphere: dict[int, tuple[float, float, float, float] | None] = {}
        self._load()

    # ------------------------------------------------------------------ loading

    def _dat_lines(self):
        for dat in self.dat_files:
            p = resolve_ci(self.root, dat)
            if p is None:
                raise SatkError("NOT_FOUND", f"DAT file not found: {dat} (root {jpath(self.root)})")
            for line in parse_dat(read_text(p)):
                yield dat, line

    def _load(self) -> None:
        # archives (main namespace only: models; player/anim/cuts are not placement sources)
        try:
            specs = layout.archives(self.root, self.dat_files, self.img_order)
        except FormatError as e:
            raise SatkError("NOT_FOUND", f"cannot read the load order: {e}") from None
        for spec in specs:
            if spec.ns != "main":
                continue
            p = resolve_ci(self.root, spec.relpath)
            if p is None or not p.is_file():
                self.stats.errors.append(f"IMG missing: {spec.relpath}")
                continue
            try:
                a = ImgArchive.open(p)
            except FormatError as e:
                self.stats.errors.append(f"IMG {spec.relpath}: {e}")
                continue
            self._imgs[spec.relpath] = a
            for e in a.entries:
                key = e.name.lower()
                if key not in self._blob:
                    self._blob[key] = BlobRef(f"file:{spec.relpath}/{key}", a.path, e.abs_offset, e.size, e.name)
        for rel in layout.loose_assets(self.root):
            key = rel.rsplit("/", 1)[-1]
            self._loose.setdefault(key, self.root / rel)

        ide_paths: list[Path] = []
        ipl_paths: list[Path] = []
        for _dat, line in self._dat_lines():
            if line.key in ("IDE", "IPL"):
                p = resolve_ci(self.root, line.path)
                if p is None:
                    self.stats.errors.append(f"{line.key} missing: {line.path}")
                    continue
                (ide_paths if line.key == "IDE" else ipl_paths).append(p)
            elif line.key == "TEXDICTION":
                p = resolve_ci(self.root, line.path)
                if p is not None:
                    self._loose[p.name.lower()] = p  # explicit TEXDICTION wins over the folder scan

        for p in ide_paths:
            rel = canon_relpath(os.path.relpath(p, self.root))
            defs, txdp, _fx = parse_ide(read_text(p))
            self.stats.ide_files += 1
            for d in defs:
                self.defs[d.id] = _Def(d.id, d.name, d.txd, d.sec, rel, d.draw, d.flags, d.time_on, d.time_off, d.anim)
            for child, parent in txdp:
                self.txdp[child.lower()] = parent.lower()
        self.stats.defs = len(self.defs)
        for d in sorted(self.defs.values(), key=lambda x: x.id):
            self.by_name[d.name.lower()] = d.id

        by_ipl: dict[str, list[_Inst]] = {}
        text_names: set[str] = set()  # noqa: F841 - kept for diagnostics
        for p in ipl_paths:
            name = p.stem.lower()
            insts, _items = parse_ipl_text(read_text(p))
            self.stats.text_ipl += 1
            text_names.add(name)
            lst = by_ipl.setdefault(name, [])
            for i in insts:
                lod = (name, i.lod) if i.lod >= 0 else None
                lst.append(_Inst(name, i.idx, i.model_id, i.pos, i.q, i.interior, lod))
        for rel, a in self._imgs.items():
            for e in a.entries:
                if e.ext.lower() != "ipl":
                    continue
                # every IPL entry of a main archive is an IPL-store slot (the five without a text
                # parent - barriers1/2, crack, carter, truthsfarm - are switched by scripts)
                name = e.stem.lower()
                base = stream_base(e.name) or name
                if name in by_ipl:  # first registered archive wins
                    continue
                try:
                    insts, _cars = parse_ipl_binary(a.read(e))
                except FormatError as ex:
                    self.stats.errors.append(f"{rel}/{e.name}: {ex}")
                    continue
                self.stats.binary_ipl += 1
                by_ipl[name] = [_Inst(name, i.idx, i.model_id, i.pos, i.q, i.interior,
                                      (base, i.lod) if i.lod >= 0 else None) for i in insts]
        for name in sorted(by_ipl):
            self.placements.extend(by_ipl[name])
        self.stats.inst = len(self.placements)
        for i in self.placements:
            if i.lod is None:
                continue
            self.stats.lod_links += 1
            tgt = by_ipl.get(i.lod[0])
            if tgt is not None and 0 <= i.lod[1] < len(tgt):
                tgt[i.lod[1]].is_lod = True
            else:
                self.stats.lod_unresolved += 1

    def close(self) -> None:
        for a in self._imgs.values():
            a.close()
        self._imgs.clear()

    # ------------------------------------------------------------------ blobs

    def blob(self, filename: str) -> BlobRef | None:
        """Active blob of ``name.ext`` (first registered IMG wins, then loose files)."""
        key = filename.lower()
        b = self._blob.get(key)
        if b is not None:
            return b
        p = self._loose.get(key)
        if p is not None and p.is_file():
            rel = canon_relpath(os.path.relpath(p, self.root))
            return BlobRef(f"file:{rel}", p, 0, p.stat().st_size, p.name)
        return None

    def read(self, ref: BlobRef) -> bytes:
        with open_ro(ref.path) as f:
            f.seek(ref.offset)
            return f.read(ref.size)

    # ------------------------------------------------------------------ models

    def resolve_model(self, model: int | str) -> _Def:
        """Definition by ID, name or ``model:`` SID; ``NOT_FOUND`` with suggestions."""
        key = str(model).strip()
        if key.lower().startswith("model:"):
            key = key[6:]
        key = key.split("@", 1)[0]
        d = None
        if key.lstrip("-").isdigit():
            d = self.defs.get(int(key))
        else:
            mid = self.by_name.get(key.lower())
            d = self.defs.get(mid) if mid is not None else None
        if d is None:
            import difflib

            names = difflib.get_close_matches(key.lower(), list(self.by_name), 5, 0.6)
            raise SatkError("NOT_FOUND", f"no model {model!r} in profile {self.profile!r}",
                            hint=f"satk asset find {key} --kind model",
                            did_you_mean=[f"model:{self.by_name[n]}" for n in names])
        return d

    def txd_chain_names(self, d: _Def) -> list[str]:
        out: list[str] = []
        t = (d.txd or "").lower()
        while t and t not in out and t != "null":
            out.append(t)
            t = self.txdp.get(t, "")
        if d.sec == "cars" and "vehicle" not in out:
            out.append("vehicle")
        return out

    def model_files(self, model: int | str) -> ModelFiles:
        d = self.resolve_model(model)
        dff = self.blob(d.name + ".dff")
        chain = [b for b in (self.blob(t + ".txd") for t in self.txd_chain_names(d)) if b is not None]
        col: ColRef | None = None
        if dff is not None and d.sec == "cars":
            col = ColRef(f"col:{d.name.lower()}", dff, 0, d.name.lower(), "embedded")
        else:
            col = self.cols().get(d.name.lower())
        return ModelFiles(d.id, d.name, d.sec, dff, chain, col)

    def cols(self) -> dict[str, ColRef]:
        """``{model name: ColRef}`` over all COL archives in the main IMGs (+ ``COLFILE`` files);
        first occurrence wins (SPEC §4.3.1). Built on first use."""
        with self._lock:
            if self._cols is not None:
                return self._cols
            from ..formats.col import iter_col

            out: dict[str, ColRef] = {}
            refs: list[BlobRef] = []
            for a in self._imgs.values():
                for e in a.entries:
                    if e.ext.lower() == "col":
                        b = self._blob.get(e.name.lower())
                        if b is not None and b.path == a.path and b.offset == e.abs_offset:
                            refs.append(b)
            for _dat, line in self._dat_lines():
                if line.key == "COLFILE":
                    p = resolve_ci(self.root, line.path)
                    if p is not None and p.is_file():
                        rel = canon_relpath(os.path.relpath(p, self.root))
                        refs.append(BlobRef(f"file:{rel}", p, 0, p.stat().st_size, p.name))
            for b in refs:
                try:
                    for m in iter_col(self.read(b)):
                        out.setdefault(m.name.lower(), ColRef(f"col:{m.name.lower()}", b, m.idx, m.name.lower(), "colfile"))
                except FormatError as e:
                    self.stats.errors.append(f"{b.sid}: {e}")
            self._cols = out
            return out

    def model_name(self, model_id: int) -> str:
        d = self.defs.get(model_id)
        return d.name if d else f"#{model_id}"

    def bsphere(self, model_id: int) -> tuple[float, float, float, float] | None:
        """Bounding sphere of the model's DFF (cached; ``None`` without a DFF)."""
        if model_id in self._bsphere:
            return self._bsphere[model_id]
        d = self.defs.get(model_id)
        bs = None
        if d is not None:
            b = self.blob(d.name + ".dff")
            if b is not None:
                try:
                    from ..formats.dff import scan_dff

                    bs = scan_dff(self.read(b)).bsphere
                except FormatError:
                    bs = None
        self._bsphere[model_id] = bs
        return bs

    # ------------------------------------------------------------------ placements

    def _aabb(self, i: _Inst) -> tuple[float, float, float, float, float, float]:
        bs = self.bsphere(i.model_id)
        x, y, z = i.pos
        if not bs:
            return (x, y, z, x, y, z)
        cx, cy, cz, r = bs
        # sphere centre rotated by the world (conjugated) quaternion; the sphere itself is round
        c = world_aabb((cx, cy, cz), (cx, cy, cz), i.pos, i.q)
        return (c[0] - r, c[1] - r, c[2] - r, c[3] + r, c[4] + r, c[5] + r)

    def insts(self, *, box: Iterable[float] | None = None, center: Iterable[float] | None = None,
              r: float | None = None, area: int | None = 0, lod: str = "hd", match: str = "aabb",
              limit: int | None = None) -> list[InstRow]:
        """Placements matching the filters (same semantics as ``IndexDB.insts``), ordered by
        distance of the position to the box/circle centre, then SID."""
        if lod not in ("hd", "lod", "all") or match not in ("aabb", "center"):
            raise SatkError("BAD_PARAMS", "lod must be hd|lod|all and match aabb|center")
        bx = tuple(float(v) for v in box) if box is not None else None
        cc = tuple(float(v) for v in center)[:2] if center is not None else None
        if bx is None and (cc is None or r is None):
            raise SatkError("BAD_PARAMS", "insts needs a box or center + r")
        if bx is not None:
            cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
        else:
            cx, cy = cc  # type: ignore[misc]
        margin = LOD_MARGIN if match == "aabb" else 0.0
        out: list[tuple[float, str, InstRow]] = []
        for i in self.placements:
            if area is not None and ipl_area(i.interior) != area:
                continue
            if lod == "hd" and i.is_lod or lod == "lod" and not i.is_lod:
                continue
            x, y = i.pos[0], i.pos[1]
            if bx is not None:
                if not (bx[0] - margin <= x <= bx[2] + margin and bx[1] - margin <= y <= bx[3] + margin):
                    continue
            elif (x - cx) ** 2 + (y - cy) ** 2 > (r + margin) ** 2:  # type: ignore[operator]
                continue
            if match == "center":
                aabb = (i.pos[0], i.pos[1], i.pos[2], i.pos[0], i.pos[1], i.pos[2])
            else:
                aabb = self._aabb(i)
                if bx is not None:
                    if aabb[3] < bx[0] or aabb[0] > bx[2] or aabb[4] < bx[1] or aabb[1] > bx[3]:
                        continue
                else:
                    nx = min(max(cx, aabb[0]), aabb[3])
                    ny = min(max(cy, aabb[1]), aabb[4])
                    if (nx - cx) ** 2 + (ny - cy) ** 2 > r * r:  # type: ignore[operator]
                        continue
            sid = f"inst:{i.ipl}#{i.idx}"
            lod_sid = f"inst:{i.lod[0]}#{i.lod[1]}" if i.lod is not None else None
            row = InstRow(sid, i.model_id, self.model_name(i.model_id), i.pos, i.q, ipl_area(i.interior),
                          ipl_iflags(i.interior), lod_sid, i.is_lod, aabb)
            out.append((math.hypot(x - cx, y - cy), sid, row))
        out.sort(key=lambda t: (t[0], t[1]))
        rows = [t[2] for t in out]
        return rows[:limit] if limit is not None else rows


_cache: dict[tuple, GameData] = {}
_cache_lock = threading.Lock()


def load(profile: str = "vanilla") -> GameData:
    """The cached :class:`GameData` of a configured profile (``satk.toml`` ``[profiles.*]``)."""
    from ..core.paths import cfg

    p = cfg().profile(profile)
    key = (profile, str(p.root), tuple(p.dat), p.img_order)
    with _cache_lock:
        g = _cache.get(key)
        if g is None:
            g = GameData(Path(p.root), list(p.dat), p.img_order, profile)
            _cache[key] = g
        return g


def clear_cache() -> None:
    with _cache_lock:
        for g in _cache.values():
            g.close()
        _cache.clear()
