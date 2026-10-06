"""Textures of a model: TXD-chain lookup, RGBA decoding with a per-process cache, vehicle colours.

WP-05 decodes textures itself through :mod:`satk.formats.dxt` (it does not depend on the WP-04 PNG
cache). Stdlib only; ``decode_rgba(backend="auto")`` uses Pillow/numpy when they are installed.

* :class:`TxdChain` — ``[own TXD, txdp parents..., vehicle]`` in lookup order; ``find(name)`` is
  case-insensitive and the first TXD that has the texture wins (like the engine's TXD parents).
* :func:`decode` — RGBA8 of mip 0, cached by pixel hash and decode format in an LRU bounded
  by bytes, so the batch thumbnailer decodes ``vehicle.txd`` once per worker process.
* :func:`vehicle_colours` — the first colour combination of a car from ``data/carcols.dat``
  (``car``/``car4`` sections), used for the recolourable material slots 1..4.

Example::

    chain = TxdChain([("infernus", txd1), ("vehicle", txd2)])
    tex = chain.find("vehiclegeneric256")
    img = decode(tex)            # img.w, img.h, img.rgba (bytes), img.alpha
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from ..formats.dxt import decode_rgba
from ..formats.rw import FormatError
from ..formats.txd import TexInfo, Txd, mip0_bytes, palette_bytes, parse_txd, texture_hash

__all__ = ["TexHandle", "Decoded", "TxdChain", "MatInfo", "resolve_materials", "decode", "cache_clear",
           "cache_stats", "vehicle_colours", "parse_carcols", "DEFAULT_CAR_COLOURS", "LIGHT_KEYS", "LIGHTS_TEXTURE",
           "ADDRESS_MODES"]

#: RW texture addressing -> name (1 wrap, 2 mirror, 3 clamp, 4 border).
ADDRESS_MODES = {1: "wrap", 2: "mirror", 3: "clamp", 4: "border"}
#: Material colours of the recolourable slots when carcols.dat has no entry (RGB).
DEFAULT_CAR_COLOURS: dict[int, tuple[int, int, int]] = {1: (42, 119, 161), 2: (245, 245, 245),
                                                       3: (88, 89, 90), 4: (88, 89, 90)}
#: Vehicle light key colours (RGB) -> head lights white, tail lights red (kept for callers; previews follow the
#: engine rule of :func:`resolve_materials` instead: every material on ``vehiclelights128`` is drawn white).
LIGHT_KEYS: dict[tuple[int, int, int], tuple[int, int, int]] = {
    (255, 175, 0): (235, 235, 225), (0, 255, 200): (235, 235, 225),
    (185, 255, 0): (170, 24, 24), (255, 60, 0): (170, 24, 24)}
#: The lamp texture of vehicles (``CVehicleModelInfo::SetEditableMaterialsCB``).
LIGHTS_TEXTURE = "vehiclelights128"


@dataclass(frozen=True, slots=True)
class TexHandle:
    """A texture found in a TXD of the chain (``buf`` = that TXD's bytes)."""

    txd: str
    info: TexInfo
    buf: bytes = field(repr=False, compare=False)

    @property
    def name(self) -> str:
        return self.info.name.lower()

    @property
    def key(self) -> str:
        """``pix`` hash (24 hex) of mip 0 (+palette), without entry-specific metadata."""
        return texture_hash(self.buf, self.info).hex()

    @property
    def pixel_key(self) -> tuple:
        """Decoded pixel identity: identical bytes can have different dimensions or formats."""
        t = self.info
        return self.key, t.w, t.h, t.d3dfmt, t.alpha, t.unsupported

    @property
    def binding_key(self) -> tuple:
        """Pixels plus this TXD entry's addressing (a renderer's texture binding)."""
        return (*self.pixel_key, self.info.uaddr, self.info.vaddr)


@dataclass(frozen=True, slots=True)
class Decoded:
    """Decoded mip 0: ``rgba`` is ``w*h*4`` bytes, row 0 = top (texture V=0)."""

    key: str
    w: int
    h: int
    rgba: bytes
    alpha: bool
    uaddr: int
    vaddr: int


class TxdChain:
    """Ordered TXDs of a model; ``find`` returns the first texture with that name.

    Args:
        txds: ``[(name, bytes)]`` in lookup order. Malformed TXDs are skipped with a note in
            :attr:`errors` (the model still renders, with those textures missing).
    """

    def __init__(self, txds: list[tuple[str, bytes]]):
        self.names = [n for n, _b in txds]
        self.errors: list[str] = []
        self._map: dict[str, TexHandle] = {}
        for name, buf in txds:
            try:
                t: Txd = parse_txd(buf)
            except FormatError as e:
                self.errors.append(f"{name}: {e}")
                continue
            for info in t.textures:
                k = info.name.lower()
                if k and k not in self._map and not info.unsupported:
                    self._map[k] = TexHandle(name, info, buf)

    def find(self, name: str | None) -> TexHandle | None:
        if not name:
            return None
        return self._map.get(name.lower())

    def __len__(self) -> int:
        return len(self._map)


@dataclass(frozen=True, slots=True)
class MatInfo:
    """A material slot after texture lookup and vehicle recolouring.

    ``rgba`` = material colour (recoloured for vehicle slots); ``tex`` = found texture or ``None``;
    ``tex_name`` = the name the DFF asks for (``None`` = untextured); ``missing`` = asked but not found.
    """

    rgba: tuple[int, int, int, int]
    tex: TexHandle | None
    tex_name: str | None
    missing: bool
    slot: int | None
    key_rgba: int


def resolve_materials(materials: list[list], chain: TxdChain | None,
                      colours: dict[int, tuple[int, int, int]] | None = None) -> list[list[MatInfo]]:
    """Per geometry, per material slot: texture lookup in ``chain`` + vehicle colour slots.

    ``colours`` (slot -> RGB) recolours materials whose colour is a vehicle key colour
    (``Material.color_slot``); pass ``None`` for non-vehicles. For vehicles every material on
    ``vehiclelights128`` is drawn white (the engine's lamp rule, lights off); a lamp key colour on any
    other texture stays as it is (that lamp never lights up in the game either).
    """
    out: list[list[MatInfo]] = []
    for ms in materials:
        row = []
        for m in ms:
            r, g, b, a = (m.rgba >> 24) & 255, (m.rgba >> 16) & 255, (m.rgba >> 8) & 255, m.rgba & 255
            slot = m.color_slot if colours else None
            if colours and m.texture and m.texture.lower() == LIGHTS_TEXTURE:
                r, g, b = 255, 255, 255
            elif slot is not None and slot in colours:
                r, g, b = colours[slot]
            tex = chain.find(m.texture) if chain is not None else None
            name = m.texture.lower() if m.texture else None
            row.append(MatInfo((r, g, b, a), tex, name, bool(name) and tex is None, slot, m.rgba))
        out.append(row)
    return out


# ----------------------------------------------------------------------------- decode cache
_CACHE_BYTES = 192 << 20
_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_cache_size = 0
_lock = threading.Lock()
_stats = {"hits": 0, "misses": 0}


def decode(tex: TexHandle) -> Decoded:
    """RGBA8 of mip 0; cache pixel bytes only, attach the requesting entry's sampler state."""
    global _cache_size
    t = tex.info
    key = tex.pixel_key
    with _lock:
        rgba = _cache.get(key)
        if rgba is not None:
            _cache.move_to_end(key)
            _stats["hits"] += 1
            return Decoded(key[0], t.w, t.h, rgba, bool(t.alpha), t.uaddr, t.vaddr)
    rgba = bytes(decode_rgba(t, mip0_bytes(tex.buf, t), palette_bytes(tex.buf, t)))
    with _lock:
        _stats["misses"] += 1
        if key not in _cache:
            _cache[key] = rgba
            _cache_size += len(rgba)
            while _cache_size > _CACHE_BYTES and len(_cache) > 1:
                _k, old = _cache.popitem(last=False)
                _cache_size -= len(old)
        else:
            rgba = _cache[key]
    return Decoded(key[0], t.w, t.h, rgba, bool(t.alpha), t.uaddr, t.vaddr)


def cache_clear() -> None:
    global _cache_size
    with _lock:
        _cache.clear()
        _cache_size = 0
        _stats.update(hits=0, misses=0)


def cache_stats() -> dict:
    with _lock:
        return {"entries": len(_cache), "bytes": _cache_size, **_stats}


# ----------------------------------------------------------------------------- carcols.dat
_carcols_cache: dict[str, tuple[float, dict]] = {}
_NUM = re.compile(r"\d+")


def parse_carcols(text: str) -> tuple[list[tuple[int, int, int]], dict[str, list[int]]]:
    """``(palette, cars)``: palette = ``col`` RGB list; cars = name -> first colour combination
    (2 indices from ``car``, 4 from ``car4``). Unknown lines are ignored."""
    palette: list[tuple[int, int, int]] = []
    cars: dict[str, list[int]] = {}
    sec = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        low = line.lower()
        if low in ("col", "car", "car4", "end"):
            sec = None if low == "end" else low
            continue
        if sec == "col":
            nums = [int(x) for x in _NUM.findall(line)[:3]]
            if len(nums) == 3:
                palette.append((min(nums[0], 255), min(nums[1], 255), min(nums[2], 255)))
        elif sec in ("car", "car4"):
            name, _, rest = line.partition(",")
            nums = [int(x) for x in _NUM.findall(rest)]
            want = 4 if sec == "car4" else 2
            if name.strip() and len(nums) >= want:
                cars.setdefault(name.strip().lower(), nums[:want])
    return palette, cars


def vehicle_colours(name: str, root: Path | None) -> dict[int, tuple[int, int, int]]:
    """Slot -> RGB for a car's first carcols combination (defaults for missing data)."""
    out = dict(DEFAULT_CAR_COLOURS)
    if root is None:
        return out
    p = Path(root) / "data" / "carcols.dat"
    key = str(p).lower()
    try:
        mt = p.stat().st_mtime
    except OSError:
        return out
    hit = _carcols_cache.get(key)
    if hit is None or hit[0] != mt:
        from ..core.paths import open_ro

        try:
            with open_ro(p) as f:
                text = f.read().decode("latin-1")
        except OSError:
            return out
        hit = (mt, dict(zip(("palette", "cars"), parse_carcols(text))))
        _carcols_cache[key] = hit
    palette, cars = hit[1]["palette"], hit[1]["cars"]
    combo = cars.get(name.lower())
    if combo:
        for slot, idx in enumerate(combo, start=1):
            if 0 <= idx < len(palette):
                out[slot] = palette[idx]
    return out
