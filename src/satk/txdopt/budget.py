"""``satk texture budget``: streaming memory of the models (DFF) and textures (TXD) a profile, a mod or an area
needs, the biggest of them, and how that compares with vanilla and with the streaming memory limit.

The game accounts every streamed resource by its size in the IMG archive: ``CStreaming`` adds ``2048 x sectors``
to ``ms_memoryUsedBytes`` when a model or TXD is loaded and subtracts the same when it is removed. So the budget of
a resource is its IMG directory size (a loose Mod Loader file: its size rounded up to 2048-byte sectors), the same
number the index stores in ``blob.size``. A model needs its DFF and its TXD chain (own TXD, ``txdp`` parents;
``vehicle.txd`` for cars is loaded at start and not streamed). Files outside the IMG archives (``loose`` in the
index) are not streamed and do not count.

Scopes: every active model of the profile (``users`` = models), or the placements whose bounding box reaches a
circle ``x y r`` in the exterior (``users`` = placements; HD and LOD by default). The limit is the kb fact
``streaming.memory`` (50 MiB: ``CStreaming::Init2`` writes 52 428 800); MTA, SA-MP and stream.ini mods raise it.

A mod (folder, .zip, .img) is overlaid on the base profile the way Mod Loader loads it: its DFF/TXD files replace
the files of the same name, its IDE definitions and ``txdp`` lines are added, its text and binary IPL placements
join the area. Stdlib only.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from .inputs import Bundle, stream_size

__all__ = ["DEFAULT_LIMIT", "COLS", "World", "stream_limit", "index_world", "overlay", "budget"]

DEFAULT_LIMIT = 52_428_800
COLS = ["id", "kind", "bytes", "vanilla", "delta", "users"]
_STREAM_NS = ("main", "player", "anim", "cuts")
_MIB = 1024 * 1024


@dataclass
class Model:
    id: int
    name: str
    sec: str
    txd: str | None
    dff: str | None


@dataclass
class World:
    """What a profile (plus mods) streams: models, resource sizes, TXD parents, extra placements."""

    label: str
    models: dict[int, Model] = field(default_factory=dict)
    size: dict[tuple[str, str], int] = field(default_factory=dict)       # (kind, stem) -> stream bytes
    streamed: dict[tuple[str, str], bool] = field(default_factory=dict)
    parent: dict[str, str] = field(default_factory=dict)
    insts: list[tuple[int, float, float, int]] = field(default_factory=list)   # (model, x, y, area) from mods

    def copy(self, label: str) -> "World":
        return World(label, dict(self.models), dict(self.size), dict(self.streamed), dict(self.parent),
                     list(self.insts))

    def chain(self, m: Model) -> list[str]:
        out: list[str] = []
        s = m.txd
        while s and ("txd", s) in self.size and s not in out:
            out.append(s)
            s = self.parent.get(s)
        if m.sec == "cars" and ("txd", "vehicle") in self.size and "vehicle" not in out:
            out.append("vehicle")
        return out

    def assets(self, m: Model) -> list[tuple[str, str]]:
        keys = ([("dff", m.dff)] if m.dff and ("dff", m.dff) in self.size else []) + [("txd", s) for s in self.chain(m)]
        return [k for k in keys if self.streamed.get(k, False)]


def stream_limit() -> tuple[int, str]:
    """``(bytes, source)`` of the default streaming memory limit (kb fact ``streaming.memory``)."""
    try:
        from ..kb.facts import FACTS
    except ImportError:  # pragma: no cover
        return DEFAULT_LIMIT, "built-in default (kb facts missing)"
    for f in FACTS:
        if f.get("key") != "streaming.memory":
            continue
        for c in f.get("checks", []):
            if c[0] == "u32" and c[1] == 0x5B8E6A:      # mov [ms_memoryAvailable], imm32 in CStreaming::Init2
                return int(c[2]), f"kb fact streaming.memory ({_fact_status()})"
    return DEFAULT_LIMIT, "built-in default (kb fact streaming.memory not found)"


def _fact_status() -> str:
    try:
        from ..kb.query import fact

        return str(fact("streaming.memory").get("status") or "unchecked")
    except Exception:  # noqa: BLE001 - no kb built: the fact is still the curated value
        return "kb not built"


def index_world(profile: str) -> World:
    """The world of a profile's index (active models, canonical DFF/TXD blobs, TXD parents)."""
    from .usage import index_conn

    _db, con = index_conn(profile)
    w = World(profile)
    try:
        for stem, size, parent, ns in con.execute(
                "SELECT b.stem, b.size, t.parent, b.ns FROM txd t JOIN blob b ON b.id = t.blob_id "
                "WHERE b.active = 1 AND b.ns IN ('main','loose','player','anim','cuts') "
                "ORDER BY lower(b.stem), (b.ns NOT IN ('main','loose')), b.id"):
            k = ("txd", stem.lower())
            if k in w.size:
                continue
            w.size[k] = stream_size(size)
            w.streamed[k] = ns in _STREAM_NS
            if parent:
                w.parent[k[1]] = parent.lower()
        dffs = {did: (stem.lower(), size, ns) for did, stem, size, ns in con.execute(
            "SELECT d.id, b.stem, b.size, b.ns FROM dff d JOIN blob b ON b.id = d.blob_id")}
        for mid, name, sec, txd, dff_id in con.execute(
                "SELECT m.id, m.name, m.sec, m.txd, ml.dff_id FROM model m LEFT JOIN model_link ml ON ml.id = m.id "
                "WHERE m.active = 1"):
            d = dffs.get(dff_id)
            if d is not None:
                k = ("dff", d[0])
                w.size[k] = stream_size(d[1])
                w.streamed[k] = d[2] in _STREAM_NS
            w.models[int(mid)] = Model(int(mid), name, sec, txd.lower() if txd else None, d[0] if d else None)
    finally:
        con.close()
    return w


def overlay(base: World, bundle: Bundle, warn: list[str]) -> World:
    """``base`` with the bundle's DFF/TXD sizes, IDE definitions, ``txdp`` lines and IPL placements."""
    from ..formats.ide import parse_ide
    from ..formats.ipl import parse_ipl_binary, parse_ipl_text
    from ..formats.rw import FormatError

    w = base.copy(f"mod:{bundle.name}")
    for it in bundle.items:
        if it.ext in ("dff", "txd"):
            k = (it.ext, it.stem)
            w.size[k] = stream_size(it.size)
            w.streamed[k] = True
    for it in bundle.of("ide"):
        try:
            defs, txdp, _fx = parse_ide(it.read().decode("latin-1"))
        except SatkError as e:
            warn.append(f"UNREADABLE: {it.rel}: {e.msg}")
            continue
        for d in defs:
            w.models[d.id] = Model(d.id, d.name, d.sec, d.txd.lower() if d.txd else None, d.name.lower())
            k = ("dff", d.name.lower())
            if k not in w.size:
                warn.append(f"NO_DFF: {it.rel}: model {d.id} {d.name}: no DFF in the mod or the game")
        for child, parent in txdp:
            if child.lower() != parent.lower():
                w.parent[child.lower()] = parent.lower()
    for it in bundle.of("ipl"):
        try:
            raw = it.read()
            if raw[:4] == b"bnry":
                insts, _cars = parse_ipl_binary(raw)
            else:
                insts, _items = parse_ipl_text(raw.decode("latin-1"))
        except (SatkError, FormatError) as e:
            warn.append(f"UNREADABLE: {it.rel}: {getattr(e, 'msg', e)}")
            continue
        for i in insts:
            w.insts.append((i.model_id, i.pos[0], i.pos[1], i.interior & 0xFF))
    return w


def _counts_area(profile: str, world: World, x: float, y: float, r: float, lod: str, mods: bool) -> Counter:
    from ..index.api import open_index

    db = open_index(profile)
    c = Counter(i.model_id for i in db.insts(center=(x, y), r=r, area=0, lod=lod))
    if mods:
        for mid, ix, iy, area in world.insts:
            if area == 0 and math.hypot(ix - x, iy - y) <= r:
                c[mid] += 1
    return c


def tally(world: World, counts: Counter) -> dict[tuple[str, str], list[int]]:
    """``(kind, stem) -> [bytes, users]`` of the streamed resources the counted models need."""
    out: dict[tuple[str, str], list[int]] = {}
    for mid, n in counts.items():
        m = world.models.get(mid)
        if m is None:
            continue
        for k in world.assets(m):
            e = out.setdefault(k, [world.size[k], 0])
            e[1] += n
    return out


def _totals(t: dict[tuple[str, str], list[int]]) -> dict:
    txd = [v[0] for k, v in t.items() if k[0] == "txd"]
    dff = [v[0] for k, v in t.items() if k[0] == "dff"]
    return {"txd": {"n": len(txd), "bytes": sum(txd)}, "dff": {"n": len(dff), "bytes": sum(dff)},
            "total": sum(txd) + sum(dff)}


def _resolve(profile: str, base: str, warn: list[str]) -> tuple[World, World | None, Bundle | None]:
    """``(world, vanilla world or None, bundle or None)`` for a profile name or a mod path."""
    from ..core.paths import cfg
    from .inputs import load_bundle

    p = Path(profile)
    is_path = p.exists() or any(ch in profile for ch in "/\\:") or profile.lower().endswith((".zip", ".img"))
    if is_path:
        bundle = load_bundle(profile, base)
        try:
            bw = index_world(base)
            return overlay(bw, bundle, warn), bw, bundle
        except BaseException:
            bundle.close()
            raise
    try:
        cfg().profile(profile)
    except SatkError as e:
        raise SatkError("BAD_PARAMS", f"{profile!r} is neither a profile nor an existing mod path",
                        hint="--profile vanilla|installed|samp or a mod folder/.zip/.img", data=e.data) from None
    w = index_world(profile)
    van = w
    if cfg().canonical_profile(profile) != cfg().canonical_profile("vanilla"):
        try:
            van = index_world("vanilla")
        except SatkError as e:
            warn.append(f"{e.code}: no vanilla index to compare with ({e.hint or 'satk index build'})")
            van = None
    return w, van, None


def budget(profile: str = "vanilla", *, area: list[float] | None = None, lod: str = "all", kind: str = "all",
           sort: str = "size", base: str = "vanilla", limit: int = 20, cursor: str | None = None) -> dict:
    from ..core.envelope import table

    if area is not None and (len(area) != 3 or area[2] <= 0):
        raise SatkError("BAD_PARAMS", f"--area takes x y r with r > 0, got {area}", hint="--area 2495 -1666 150")
    warn: list[str] = []
    world, van, bundle = _resolve(profile, base, warn)
    try:
        if area is not None:
            x, y, r = (float(v) for v in area)
            if bundle is not None:
                counts = _counts_area(base, world, x, y, r, lod, True)
                vcounts = _counts_area(base, van, x, y, r, lod, False)
            else:
                counts = _counts_area(profile, world, x, y, r, lod, False)
                vcounts = counts if van is None or van is world else _counts_area("vanilla", van, x, y, r, lod, False)
        else:
            counts = Counter({mid: 1 for mid in world.models})
            vcounts = Counter({mid: 1 for mid in van.models}) if van is not None else counts
        mine = tally(world, counts)
        theirs = tally(van, vcounts) if van is not None else mine
    finally:
        if bundle is not None:
            bundle.close()
    missing = sorted(mid for mid in counts if mid not in world.models)
    if missing:
        warn.append(f"UNDEFINED: {len(missing)} placed model id(s) have no definition: {missing[:8]}")
    lim, lim_src = stream_limit()
    tot = _totals(mine)
    vtot = _totals(theirs)
    rows = []
    for k, (b, users) in mine.items():
        if kind != "all" and k[0] != kind:
            continue
        vb = van.size.get(k) if van is not None else None
        rows.append([f"{k[0]}:{k[1]}", k[0], b, vb, b - (vb or 0) if vb is not None else b, users])
    if sort == "delta":
        rows.sort(key=lambda r: (-r[4], -r[2], r[0]))
    else:
        rows.sort(key=lambda r: (-r[2], r[0]))
    off = int(cursor) if cursor and str(cursor).isdigit() else 0
    if cursor and not str(cursor).isdigit():
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page")
    lim_rows = max(1, int(limit))
    page = rows[off:off + lim_rows]
    nxt = str(off + lim_rows) if off + lim_rows < len(rows) else None
    summary = {"profile": world.label, "scope": "area" if area is not None else "all models",
               "models": sum(1 for mid in counts if mid in world.models), **tot,
               "total_mib": round(tot["total"] / _MIB, 2),
               "vanilla": {"txd": vtot["txd"]["bytes"], "dff": vtot["dff"]["bytes"], "total": vtot["total"]},
               "delta": tot["total"] - vtot["total"],
               "limit": {"bytes": lim, "source": lim_src, "used_pct": round(100.0 * tot["total"] / lim, 1)}}
    if area is not None:
        summary["area"] = [round(float(v), 2) for v in area]
        summary["instances"] = sum(n for mid, n in counts.items() if mid in world.models)
        if tot["total"] > lim:
            warn.append(f"OVER_LIMIT: this area needs {tot['total'] / _MIB:.1f} MiB of streamed DFF+TXD, the default "
                        f"limit is {lim / _MIB:.0f} MiB (MTA/SA-MP/stream.ini raise it; vehicles, peds and "
                        "collisions need memory too)")
    else:
        worst = max(((sum(world.size[k] for k in world.assets(m)), m) for m in world.models.values()),
                    key=lambda t: t[0], default=(0, None))
        if worst[1] is not None:
            summary["largest_model"] = {"id": f"model:{worst[1].id}", "name": worst[1].name, "bytes": worst[0]}
            if worst[0] > lim:
                warn.append(f"OVER_LIMIT: model {worst[1].id} {worst[1].name} alone needs {worst[0] / _MIB:.1f} MiB "
                            f"(DFF + TXD chain), more than the {lim / _MIB:.0f} MiB limit: it can never stream in")
    env = table(COLS, page, total=len(rows), next=nxt, warn=warn)
    env["summary"] = summary
    return env
