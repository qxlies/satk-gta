# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Shared helpers of the studio methods: parameter checks, object lookup, face selectors, bmesh editing.

**Face selector** (``select`` parameter of the mesh methods; all keys combine with AND, object space)::

    {"all": true}                      # every face (the default when nothing is given)
    {"side": "+z", "within": 30}       # faces whose normal is within 30 deg of +Z (+x -x +y -y +z -z)
    {"normal": [0, 0.7, 0.7], "within": 20}
    {"where": ["z>0.4", "y<-1.2"]}     # face centres in half-spaces (x y z, > >= < <=)
    {"box": [[x0, y0, z0], [x1, y1, z1]]}
    {"group": "roof"}                  # faces whose vertices are all in the vertex group
    {"material": "glass"}              # material name or slot index
    {"faces": [0, 5, 6]}, {"invert": true}
    {"near": {"point": [0.9, 1.4, 0.5], "radius": 0.3}}   # face centres within radius of the point
    {"loop": {"point": [0.9, 1.4, 0.5], "dir": "z"}}      # the edge loop through the edge nearest the point
    {"loop": {"point": [0.9, 1.4, 0.5], "ring": true}}    # the strip of quads across that edge (a face ring)
    {"grow": 2}                                           # then grow the selection by 2 rings of faces
    {"linked": true}                                      # then the whole welded pieces the faces belong to
    {"item": "I05"}                                       # faces tagged with an inventory item (scene.tag)

``loop`` picks the edge nearest to ``point`` (``dir`` x|y|z or a vector: only edges running within 45 deg of
it) and walks the loop through 4-valent vertices; for vertex edits (``mesh.transform``, ``mesh.relax``) an edge
loop means its own vertices, for face edits the faces on both sides of it. ``invert`` applies before ``grow``.

Methods that make new geometry take ``save_group`` and store the result as a vertex group, so the
next step can address it by name instead of by coordinates.
"""

from __future__ import annotations

import math
import re
from contextlib import contextmanager

import bmesh
import bpy
from mathutils import Vector

from satk.core.errors import SatkError

_AXES = {"x": 0, "y": 1, "z": 2}
_SIDES = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}
_WHERE = re.compile(r"^\s*([xyz])\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?(?:e-?\d+)?)\s*$", re.IGNORECASE)
SELECT_KEYS = frozenset({"all", "side", "normal", "within", "where", "box", "group", "material", "faces", "invert",
                         "near", "loop", "grow", "linked", "item"})
#: Inventory tags (contract K6): object property with the ids on the object, face attribute, id list property.
ITEM_PROP = "satk_item"
ITEM_ATTR = "satk_item_idx"
ITEM_IDS = "satk_item_ids"


def parse_ids(v) -> list[str]:
    """Ids of a ``satk_item`` value: ``"I05"``, ``"I05,I06"`` or a list."""
    if v is None:
        return []
    if isinstance(v, str):
        parts = v.replace(";", ",").split(",")
    else:
        try:
            parts = [str(x) for x in list(v)]
        except TypeError:
            return []
    out: list[str] = []
    for x in parts:
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out


def item_registry(scene=None) -> list[str]:
    """The scene's append-only id list (face values index it, 1-based)."""
    scene = scene or bpy.context.scene
    return parse_ids(scene.get(ITEM_IDS))


def item_ids(o) -> list[str]:
    """The id list ``o``'s face values index: the longer of its own list and the scene registry (prefixes of one)."""
    own = parse_ids(o.get(ITEM_IDS))
    reg = item_registry()
    return reg if len(reg) >= len(own) else own


def bad(method: str, msg: str, **kw) -> SatkError:
    return SatkError("BAD_PARAMS", f"{method}: {msg}", **kw)


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def num(p: dict, key: str, method: str, default=None, *, lo: float | None = None, hi: float | None = None,
        required: bool = False) -> float | None:
    v = p.get(key, default)
    if v is None:
        if required:
            raise bad(method, f"'{key}' is required")
        return None
    if not is_num(v) or (lo is not None and v < lo) or (hi is not None and v > hi):
        rng = f" ({lo if lo is not None else '-inf'}..{hi if hi is not None else 'inf'})"
        raise bad(method, f"'{key}' must be a number{rng}, got {v!r}")
    return float(v)


def integer(p: dict, key: str, method: str, default=None, *, lo: int = 0, hi: int = 1 << 20,
            required: bool = False) -> int | None:
    v = p.get(key, default)
    if v is None:
        if required:
            raise bad(method, f"'{key}' is required ({lo}-{hi})")
        return None
    if not is_num(v) or int(v) != v or not lo <= int(v) <= hi:
        raise bad(method, f"'{key}' must be an integer {lo}-{hi}, got {v!r}")
    return int(v)


def flag(p: dict, key: str, method: str, default: bool = False) -> bool:
    v = p.get(key, default)
    if not isinstance(v, bool):
        raise bad(method, f"'{key}' must be true or false, got {v!r}")
    return v


def text(p: dict, key: str, method: str, default: str | None = None, *, required: bool = False,
         choices: tuple | list | None = None) -> str | None:
    v = p.get(key, default)
    if v is None:
        if required:
            raise bad(method, f"'{key}' is required" + (f" (one of {', '.join(choices)})" if choices else ""))
        return None
    if not isinstance(v, str) or not v:
        raise bad(method, f"'{key}' must be a non-empty string, got {v!r}")
    if choices is not None and v not in choices:
        import difflib

        raise bad(method, f"'{key}' must be one of {', '.join(choices)}, got {v!r}",
                  did_you_mean=difflib.get_close_matches(v, list(choices), n=3, cutoff=0.5))
    return v


def vec(p: dict, key: str, method: str, default=None, *, n: int = 3, required: bool = False) -> list[float] | None:
    v = p.get(key, default)
    if v is None:
        if required:
            raise bad(method, f"'{key}' is required ([{', '.join('xyzw'[:n])}])")
        return None
    if is_num(v) and n > 1:
        v = [float(v)] * n
    if not isinstance(v, (list, tuple)) or len(v) != n or not all(is_num(x) for x in v):
        raise bad(method, f"'{key}' must be a list of {n} numbers, got {v!r}")
    return [float(x) for x in v]


def require(p: dict, key: str, method: str, hint: str | None = None):
    """``p[key]`` or ``BAD_PARAMS`` (marks the key required in the generated reference)."""
    v = p.get(key)
    if v is None:
        raise bad(method, f"'{key}' is required", hint=hint)
    return v


def points2(p: dict, key: str, method: str, *, min_n: int = 2, max_n: int = 4096) -> list[tuple[float, float]]:
    v = p.get(key)
    if not isinstance(v, (list, tuple)) or not min_n <= len(v) <= max_n or not all(
            isinstance(q, (list, tuple)) and len(q) == 2 and all(is_num(x) for x in q) for q in v):
        raise bad(method, f"'{key}' must be a list of {min_n}-{max_n} [a, b] pairs")
    return [(float(a), float(b)) for a, b in v]


def axis(p: dict, key: str, method: str, default: str = "z") -> int:
    a = text(p, key, method, default, choices=("x", "y", "z"))
    return _AXES[a]


def deg(p: dict, key: str, method: str, default: float, *, lo: float = 0.0, hi: float = 180.0) -> float:
    """An angle given in degrees, returned in radians."""
    return math.radians(num(p, key, method, default, lo=lo, hi=hi))


def r3(v) -> list[float]:
    return [round(float(x) + 0.0, 4) for x in v]


def names_of(p: dict, method: str, *, key: str = "objects", single: str = "object") -> list[str]:
    """Object names from ``object`` (one name) or ``objects`` (names and globs)."""
    if p.get(single) is not None:
        v = p[single]
        if not isinstance(v, str) or not v:
            raise bad(method, f"'{single}' must be an object name")
        return [v]
    v = p.get(key)
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)) or not v or not all(isinstance(x, str) and x for x in v):
        raise bad(method, f"'{single}' (a name) or '{key}' (names or globs) is required")
    return list(v)


def resolve(ctx, p: dict, method: str, *, types: tuple[str, ...] | None = None, key: str = "objects",
            single: str = "object") -> list:
    """Objects named by ``object``/``objects`` (globs allowed; each must match something)."""
    out, seen = [], set()
    for pat in names_of(p, method, key=key, single=single):
        if any(ch in pat for ch in "*?["):
            hits = ctx.objects(pat)
            if not hits:
                raise SatkError("NOT_FOUND", f"{method}: no object matches {pat!r}")
        else:
            hits = [ctx.obj(pat)]
        for o in hits:
            if o.name not in seen:
                seen.add(o.name)
                out.append(o)
    if types is not None:
        wrong = [o.name for o in out if o.type not in types]
        if wrong:
            raise bad(method, f"{', '.join(wrong[:5])}: type must be {'/'.join(types)}")
    return out


def mesh_obj(ctx, p: dict, method: str):
    o = ctx.obj(text(p, "object", method, required=True))
    if o.type != "MESH":
        raise bad(method, f"{o.name} is a {o.type}, not a MESH", hint="mesh.convert turns curves into meshes")
    return o


@contextmanager
def edit_mesh(o):
    """``bmesh`` of an object's mesh in object mode; written back when the block ends without an error."""
    if o.mode != "OBJECT":
        raise SatkError("BAD_PARAMS", f"{o.name} is in {o.mode} mode", hint="leave edit mode in the Blender window")
    me = o.data
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        yield bm
        bm.normal_update()
        bm.to_mesh(me)
        me.update()
    finally:
        bm.free()


def check_select(sel, method: str) -> dict:
    if sel is None:
        return {"all": True}
    if not isinstance(sel, dict):
        raise bad(method, "'select' must be an object such as {\"side\": \"+z\"}")
    extra = set(sel) - SELECT_KEYS
    if extra:
        raise bad(method, f"select: unknown key(s) {sorted(extra)}", data={"keys": sorted(SELECT_KEYS)})
    return sel


def _group_index(o, name: str, method: str) -> int:
    vg = o.vertex_groups.get(name)
    if vg is None:
        raise SatkError("NOT_FOUND", f"{method}: {o.name} has no vertex group {name!r}",
                        data={"groups": [g.name for g in o.vertex_groups][:20]})
    return vg.index


def _material_index(o, ref, method: str) -> int:
    if is_num(ref):
        i = int(ref)
        if 0 <= i < max(1, len(o.material_slots)):
            return i
        raise bad(method, f"select.material: {o.name} has no slot {i}")
    for i, s in enumerate(o.material_slots):
        if s.material is not None and s.material.name == ref:
            return i
    raise SatkError("NOT_FOUND", f"{method}: {o.name} has no material {ref!r}",
                    data={"materials": [s.material.name for s in o.material_slots if s.material][:20]})


def select_faces(o, bm, sel: dict | None, method: str) -> list:
    """Faces of ``bm`` chosen by the selector (see the module doc)."""
    return _select(o, bm, sel, method)[0]


def select_verts(o, bm, sel: dict | None, method: str) -> tuple[list, list]:
    """``(faces, verts)`` of the selector: the vertices a vertex edit moves (an edge loop: its own vertices)."""
    faces, loop_verts = _select(o, bm, sel, method)
    fv = {v for f in faces for v in f.verts}
    if loop_verts is not None:
        verts = [v for v in loop_verts if v in fv]
    else:
        verts = sorted(fv, key=lambda v: v.index)
    return faces, verts


def _point(d: dict, key: str, method: str) -> Vector:
    return Vector(vec(d, key, method, required=True))


def _dir(v, method: str) -> Vector | None:
    if v is None:
        return None
    if isinstance(v, str) and v.lower().lstrip("+-") in _AXES:
        d = Vector((0.0, 0.0, 0.0))
        d[_AXES[v.lower().lstrip("+-")]] = 1.0
        return d
    d = Vector(vec({"v": v}, "v", method))
    if d.length < 1e-9:
        raise bad(method, "select.loop.dir must not be zero")
    return d.normalized()


def _seg_dist(p: Vector, a: Vector, b: Vector) -> float:
    ab = b - a
    t = 0.0 if ab.length_squared < 1e-18 else max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
    return (p - (a + ab * t)).length


def nearest_edge(bm, point: Vector, direction: Vector | None = None, *, edges=None):
    """The edge nearest to ``point`` (only edges within 45 deg of ``direction`` when given), or ``None``."""
    best, bd = None, math.inf
    for e in (edges if edges is not None else bm.edges):
        a, b = e.verts[0].co, e.verts[1].co
        if direction is not None:
            d = b - a
            if d.length < 1e-12 or abs(d.normalized().dot(direction)) < 0.7071:
                continue
        dist = _seg_dist(point, a, b)
        if dist < bd - 1e-12:
            best, bd = e, dist
    return best


def _next_loop_edge(e, v):
    """The edge that continues the loop of ``e`` through ``v`` (4-valent vertex, or along an open border)."""
    others = [x for x in v.link_edges if x is not e]
    if e.is_boundary:
        nxt = [x for x in others if x.is_boundary]
        return nxt[0] if len(nxt) == 1 else None
    if len(v.link_edges) != 4 or any(len(f.verts) != 4 for f in v.link_faces):
        return None
    ef = set(e.link_faces)
    nxt = [x for x in others if not (set(x.link_faces) & ef)]
    return nxt[0] if len(nxt) == 1 else None


def edge_loop(e0) -> list:
    """The edges of the loop through ``e0`` (ordered from one end; a closed loop starts at ``e0``)."""
    fwd, back = [], []
    seen = {e0}
    for v0, acc in ((e0.verts[1], fwd), (e0.verts[0], back)):
        e, v = e0, v0
        while True:
            n = _next_loop_edge(e, v)
            if n is None or n in seen:
                break
            seen.add(n)
            acc.append(n)
            v = n.other_vert(v)
            e = n
    return list(reversed(back)) + [e0] + fwd


def edge_ring(e0) -> list:
    """The faces of the quad strip across ``e0`` (an edge ring), in both directions."""
    faces, seen = [], set()
    for f0 in e0.link_faces:
        e, f = e0, f0
        while f is not None and f not in seen and len(f.verts) == 4:
            seen.add(f)
            faces.append(f)
            opp = next((x for x in f.edges if not (set(x.verts) & set(e.verts))), None)
            if opp is None:
                break
            nxt = [x for x in opp.link_faces if x is not f]
            e, f = opp, (nxt[0] if len(nxt) == 1 else None)
    return faces


def _loop_sel(bm, spec, method: str) -> tuple[set, list | None]:
    """``(faces, loop vertices | None)`` of a ``loop`` selector."""
    if not isinstance(spec, dict):
        raise bad(method, "select.loop must be {\"point\": [x, y, z], \"dir\"?: x|y|z, \"ring\"?: bool}")
    extra = set(spec) - {"point", "dir", "ring"}
    if extra:
        raise bad(method, f"select.loop: unknown key(s) {sorted(extra)}")
    pt = _point(spec, "point", method)
    e0 = nearest_edge(bm, pt, _dir(spec.get("dir"), method))
    if e0 is None:
        raise SatkError("NOT_FOUND", f"{method}: no edge runs along select.loop.dir",
                        hint="drop 'dir' or use another axis")
    if flag(spec, "ring", method, False):
        return set(edge_ring(e0)), None
    edges = edge_loop(e0)
    verts: list = []
    seen: set = set()
    for e in edges:
        for v in e.verts:
            if v not in seen:
                seen.add(v)
                verts.append(v)
    return {f for e in edges for f in e.link_faces}, verts


def _select(o, bm, sel: dict | None, method: str) -> tuple[list, list | None]:
    sel = check_select(sel, method)
    loop_verts = None
    faces = list(bm.faces)
    if sel.get("faces") is not None:
        idx = sel["faces"]
        if not isinstance(idx, list) or not all(is_num(i) and 0 <= int(i) < len(bm.faces) for i in idx):
            raise bad(method, f"select.faces must be face indices 0-{len(bm.faces) - 1}")
        want = {int(i) for i in idx}
        faces = [f for f in faces if f.index in want]
    direction = None
    if sel.get("side") is not None:
        if sel["side"] not in _SIDES:
            raise bad(method, f"select.side must be one of {', '.join(_SIDES)}")
        direction = Vector(_SIDES[sel["side"]])
    elif sel.get("normal") is not None:
        direction = Vector(vec(sel, "normal", method))
        if direction.length < 1e-9:
            raise bad(method, "select.normal must not be zero")
        direction.normalize()
    if direction is not None:
        lim = math.radians(num(sel, "within", method, 30.0, lo=0.0, hi=180.0))
        faces = [f for f in faces if f.normal.length > 1e-9 and f.normal.angle(direction) <= lim + 1e-6]
    if sel.get("where") is not None:
        w = sel["where"]
        if isinstance(w, str):
            w = [w]
        conds = []
        for c in w if isinstance(w, list) else [None]:
            m = _WHERE.match(c) if isinstance(c, str) else None
            if not m:
                raise bad(method, f"select.where: {c!r} is not like 'z>0.5'")
            conds.append((_AXES[m.group(1).lower()], m.group(2), float(m.group(3))))
        ops = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}
        faces = [f for f in faces if all(ops[op](f.calc_center_median()[ax], v) for ax, op, v in conds)]
    if sel.get("box") is not None:
        b = sel["box"]
        if not isinstance(b, list) or len(b) != 2:
            raise bad(method, "select.box must be [[x0, y0, z0], [x1, y1, z1]]")
        lo = vec({"v": b[0]}, "v", method)
        hi = vec({"v": b[1]}, "v", method)
        faces = [f for f in faces if all(lo[i] - 1e-6 <= f.calc_center_median()[i] <= hi[i] + 1e-6 for i in range(3))]
    if sel.get("group") is not None:
        gi = _group_index(o, str(sel["group"]), method)
        dl = bm.verts.layers.deform.active
        if dl is None:
            faces = []
        else:
            faces = [f for f in faces if all(gi in v[dl] for v in f.verts)]
    if sel.get("material") is not None:
        mi = _material_index(o, sel["material"], method)
        faces = [f for f in faces if f.material_index == mi]
    if sel.get("near") is not None:
        nr = sel["near"]
        if not isinstance(nr, dict) or set(nr) - {"point", "radius"}:
            raise bad(method, "select.near must be {\"point\": [x, y, z], \"radius\": m}")
        c = _point(nr, "point", method)
        rad = num(nr, "radius", method, required=True, lo=0.0)
        faces = [f for f in faces if (f.calc_center_median() - c).length <= rad + 1e-9]
    if sel.get("loop") is not None:
        lf, loop_verts = _loop_sel(bm, sel["loop"], method)
        faces = [f for f in faces if f in lf]
    if sel.get("item") is not None:
        faces = [f for f in faces if f in _item_faces(o, bm, sel["item"], method)]
    if sel.get("invert"):
        keep = set(faces)
        faces = [f for f in bm.faces if f not in keep]
        loop_verts = None
    grow = integer(sel, "grow", method, 0, lo=0, hi=64)
    if grow:
        have = set(faces)
        ring = set(faces)
        for _ in range(grow):
            nxt = {g for f in ring for v in f.verts for g in v.link_faces} - have
            if not nxt:
                break
            have |= nxt
            ring = nxt
        faces = sorted(have, key=lambda f: f.index)
        loop_verts = None
    if sel.get("linked"):
        have = set(faces)
        todo = list(faces)
        while todo:
            f = todo.pop()
            for v in f.verts:
                for g in v.link_faces:
                    if g not in have:
                        have.add(g)
                        todo.append(g)
        faces = sorted(have, key=lambda f: f.index)
        loop_verts = None
    return faces, loop_verts


def _item_faces(o, bm, item, method: str) -> set:
    """Faces tagged with the inventory item ``item`` (face attribute, or the object's single ``satk_item``)."""
    if not isinstance(item, str) or not item:
        raise bad(method, "select.item must be an inventory id such as I05")
    layer = bm.faces.layers.int.get(ITEM_ATTR)
    ids = item_ids(o)
    if layer is not None and item in ids:
        v = ids.index(item) + 1
        hit = {f for f in bm.faces if f[layer] == v}
        if hit:
            return hit
    own = parse_ids(o.get(ITEM_PROP))
    if item in own:
        present = {ids[f[layer] - 1] for f in bm.faces if 0 < f[layer] <= len(ids)} if layer is not None else set()
        rest = [i for i in own if i not in present]
        if rest == [item]:
            return {f for f in bm.faces if layer is None or f[layer] == 0}
    return set()


def require_faces(faces: list, method: str, sel) -> list:
    if not faces:
        raise SatkError("NOT_FOUND", f"{method}: the selector matched no faces", data={"select": sel},
                        hint="mesh.info shows the groups and materials; loosen 'within' or 'where'")
    return faces


def save_group(o, bm, verts, name: str | None, method: str) -> None:
    """Store ``verts`` as the vertex group ``name`` (replacing its members)."""
    if not name:
        return
    if not isinstance(name, str) or len(name) > 63:
        raise bad(method, "'save_group' must be a name of at most 63 characters")
    bm.verts.index_update()
    want = {v.index for v in verts if v.is_valid}  # before the layer: adding it invalidates BMVert references
    vg = o.vertex_groups.get(name) or o.vertex_groups.new(name=name)
    dl = bm.verts.layers.deform.verify()
    for v in bm.verts:
        d = v[dl]
        if v.index in want:
            d[vg.index] = 1.0
        elif vg.index in d:
            del d[vg.index]


def edges_of(faces) -> list:
    seen, out = set(), []
    for f in faces:
        for e in f.edges:
            if e.index not in seen:
                seen.add(e.index)
                out.append(e)
    return out


def boundary_edges(faces) -> list:
    """Edges of the region border: used by exactly one face of ``faces``."""
    fs = set(faces)
    return [e for e in edges_of(faces) if sum(1 for f in e.link_faces if f in fs) == 1]


def dihedral(e) -> float:
    """Angle between the two face normals of a manifold edge (radians; 0 = flat); pi for open edges."""
    if len(e.link_faces) != 2:
        return math.pi
    a, b = e.link_faces
    if a.normal.length < 1e-9 or b.normal.length < 1e-9:
        return 0.0
    return a.normal.angle(b.normal)


def link_new(ctx, obj, collection: str | None = None):
    ctx.collection(collection).objects.link(obj)
    return obj


def new_mesh_object(ctx, name: str, bm, collection: str | None = None, location=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    me.update()
    ob = bpy.data.objects.new(name, me)
    link_new(ctx, ob, collection)
    if location is not None:
        ob.location = Vector(location)
    return ob


def counts(o) -> dict:
    me = o.data
    me.calc_loop_triangles()
    return {"verts": len(me.vertices), "faces": len(me.polygons), "tris": len(me.loop_triangles)}
