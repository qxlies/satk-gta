"""Operations of satk.paths (owner M2-05): ``satk paths import|near|node|export|compile``.

CLI only (``mcp=False``); MCP reaches them through the generic operation tool. Module-level
imports are stdlib/satk only; the vendored gta-flow core is loaded on first use.
"""

from __future__ import annotations

from typing import Literal

from satk.core.envelope import clamp_limit, obj, round_pos, table
from satk.core.errors import SatkError
from satk.core.registry import op

from . import records as R

_KIND = Literal["all", "vehicle", "car", "boat", "ped"]


def _warned(env: dict, warns: list[str]) -> dict:
    if warns:
        env.setdefault("warn", []).extend(warns)
    return env


@op("paths.import",
    summary="Import the 64 path regions (nodes*.dat: car, boat and pedestrian nodes, navis, links) of a "
            "profile from its IMG archives into work/index/paths-<profile>.sqlite; reused when unchanged.",
    summary_ru="Импорт путей NODES*.DAT (64 региона) из IMG профиля в work/index/paths-<profile>.sqlite.",
    mcp=False, group="world",
    examples=("satk paths import", "satk paths import --profile installed --force"))
def paths_import(profile: str = "vanilla", force: bool = False) -> dict:
    """Import the path network of a profile (read-only on the game).

    Args:
        profile: load profile whose IMG archives are read (first registered archive wins).
        force: re-import even if the archives did not change.
    """
    from .db import import_profile

    res = import_profile(profile, force=force)
    warn = res.pop("warn", [])
    return _warned(obj(**res), warn)


@op("paths.near",
    summary="Path nodes near a world point, nearest first: address area:idx, kind (car/boat/ped), position, "
            "distance, width, flood component and linked nodes. Imports the paths on first use.",
    summary_ru="Узлы путей рядом с точкой: адрес area:idx, тип, позиция, расстояние, связи.",
    mcp=False, group="world",
    examples=("satk paths near 2495 -1687", "satk paths near 2495 -1687 --r 60 --kind car"))
def paths_near(x: float, y: float, r: float = 30.0, z: float | None = None, kind: _KIND = "all",
               limit: int = 20, profile: str = "vanilla") -> dict:
    """Nodes within ``r`` of (x, y[, z]).

    Args:
        x: world X.
        y: world Y.
        r: search radius in world units (2D, or 3D when z is given).
        z: world Z; measures 3D distance when given.
        kind: node kind (vehicle = car + boat).
        limit: rows to return (nearest first).
        profile: load profile.
    """
    from .build import KIND_FILTERS
    from .db import dist2, open_paths

    lim = clamp_limit(limit)
    if not r > 0:
        raise SatkError("BAD_PARAMS", f"r must be > 0, got {r}")
    db, warns = open_paths(profile)
    with db:
        cands = db.nodes_in_box(x - r, y - r, x + r, y + r, KIND_FILTERS[kind])
        hits = sorted(((dist2(x, y, z, c), c["area"], c["idx"], c) for c in cands), key=lambda t: t[:3])
        hits = [h for h in hits if h[0] <= r]
        rows = []
        for d, a, i, c in hits[:lim]:
            links = [R.addr(ln["to_area"], ln["to_idx"]) for ln in db.links_of(a, i)]
            rows.append([R.addr(a, i), c["kind"], round_pos([c["x"], c["y"], c["z"]]), round(d, 2),
                         round(c["width"], 2), c["flood"], links])
    env = table(["node", "kind", "pos", "d", "width", "flood", "links"], rows, total=len(hits))
    if not hits:
        what = "path node" if kind == "all" else f"{kind} path node"
        warns.append(f"EMPTY: no {what} within {r:g} of ({x:g}, {y:g}); try a larger --r")
    return _warned(env, warns)


@op("paths.node",
    summary="One path node by address area:idx: kind, position, width, flags, spawn/behaviour, links with "
            "distance, navi and lanes per direction, navis attached to it.",
    summary_ru="Один узел путей по адресу area:idx: флаги, связи, полосы, навигационные узлы.",
    mcp=False, group="world", examples=("satk paths node 15:6",))
def paths_node(node: str, profile: str = "vanilla") -> dict:
    """Details of one node.

    Args:
        node: node address <area>:<index>, e.g. 15:6 (from 'satk paths near').
        profile: load profile.
    """
    from .build import segment_lanes
    from .db import open_paths

    try:
        a, i = R.parse_addr(node)
    except ValueError as e:
        raise SatkError("BAD_PARAMS", str(e), hint="satk paths near <x> <y>") from None
    db, warns = open_paths(profile)
    with db:
        row = db.node(a, i)
        if row is None:
            raise SatkError("NOT_FOUND", f"no path node {node}", hint="satk paths near <x> <y>")
        links = []
        for ln in db.links_of(a, i):
            tk = (ln["to_area"], ln["to_idx"])
            item = {"to": R.addr(*tk), "dist": ln["dist"]}
            if ln["navi_area"] is not None:
                nk = (ln["navi_area"], ln["navi_idx"])
                item["navi"] = R.addr(*nk)
                lanes = segment_lanes(db.navi(*nk), (a, i), tk)
                if lanes is not None:
                    item["lanes"] = lanes
            if ln["inter"]:
                item["inter"] = ln["inter"]
            links.append(item)
        attached = [R.addr(v["area"], v["idx"]) for v in db.navis_attached_to(a, i)]
    env = obj(f"{a}:{i}", kind=row["kind"], pos=round_pos([row["x"], row["y"], row["z"]]),
              width=round(row["width"], 2), flood=row["flood"], spawn=row["spawn"], behaviour=row["behaviour"],
              flags=R.flag_names(row["flags"]), links=links, navis=attached,
              region=R.area_of(row["x"], row["y"]))
    return _warned(env, warns)


@op("paths.export",
    summary="Write the path nodes in a 2D box (plus segments with lanes and navis) as a JSON overlay "
            "work/out/paths/<profile>/<name>.json for viewers, Blender and plots.",
    summary_ru="JSON-оверлей путей в прямоугольнике: узлы, отрезки с полосами, навигационные узлы.",
    mcp=False, group="world",
    examples=("satk paths export --area 2400,-1750,2600,-1600", "satk paths export --kind car --name cars"))
def paths_export(area: list[float] | None = None, kind: _KIND = "all", name: str | None = None,
                 navis: bool = True, profile: str = "vanilla") -> dict:
    """Export a JSON overlay.

    Args:
        area: minx,miny,maxx,maxy in world units (omit for the whole map; with a leading minus use
            --area=-100,...).
        kind: node kind (vehicle = car + boat).
        name: file name without .json (default from kind and area).
        navis: include the navi records of the exported segments.
        profile: load profile.
    """
    from .build import export_overlay

    res = export_overlay(profile, area, kind, name, navis)
    warn = res.pop("warn", [])
    return _warned(obj(**res), warn)


@op("paths.compile",
    summary="Recompile the imported path network (optionally with node edits) through gta-flow, verify it "
            "with the independent byte oracle and compare every region with its source bytes; writes "
            "changed nodes*.dat + manifest.json to work/out/paths/.",
    summary_ru="Пересборка NODES*.DAT через gta-flow (с правками узлов или без), сверка байт в байт с исходником.",
    mcp=False, group="world",
    examples=("satk paths compile", "satk paths compile --write all --name roundtrip",
              "satk paths compile --edits {\"15:6\":[2500,-1669.75,13]} --name moved"))
def paths_compile(edits: dict | None = None, write: Literal["changed", "all", "none"] = "changed",
                  name: str = "compiled", profile: str = "vanilla") -> dict:
    """Compile the network.

    Args:
        edits: JSON {"area:idx": [x, y, z]} or {"area:idx": {"pos": [...], "width": w, "spawn": 0-15,
            "behaviour": 0-15}}; @file.json reads it from a file.
        write: which regions to write: changed, all or none.
        name: output directory work/out/paths/<profile>/<name>/.
        profile: load profile.
    """
    from .build import compile_network

    res = compile_network(profile, edits, write, name)
    warn = res.pop("warn", [])
    return _warned(obj(**res), warn)
