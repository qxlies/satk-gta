# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``scene.tag`` and ``scene.items``: inventory item ids on the geometry (contract K6).

**Tags.** ``scene.tag {objects | object + select, item}`` writes the item id (``I05``) on whole objects or on a face
selection: the integer face attribute ``satk_item_idx`` (0 = no item, n = the n-th id of the list) plus the object
properties ``satk_item_ids`` (that list) and ``satk_item`` (the ids on the object, comma separated). The id list
is the scene's append-only registry (``scene["satk_item_ids"]``), so every object indexes the same list and the
tags survive ``scene.join``, ``kit.fill``, ``mesh.attach weld`` and MIRROR (face attributes travel with the
faces; a ``satk_item`` property set by hand on an object without face tags still counts for its faces).

**Facts.** ``scene.items {pairs, scope}`` (read-only) measures every item for :mod:`satk.inventory.api`: objects,
frames (and triangles per frame), triangles, size, connected pieces, and the gap in mm between each item and the
items it attaches to (``pairs`` = ``{child: [parents]}``; the worst-attached piece decides: BVH nearest points,
searched up to ``search`` m). **Own geometry:** a vertex is the item's own when only the item's faces use it;
``own_verts`` counts them, ``pieces`` counts the connected pieces that have one (a face patch of another item's
surface is no piece: ``patch_pieces``), and ``shares_with`` names the items whose surface the shared vertices
belong to. ``tex_share`` is the share of the item's area whose material carries an image texture (surface items
are built by their texture). Hidden, ghost and
reference objects, ``*_dam``/``*_vlo`` copies and collision, shadow and LOD slots do not count. ``scope: kit``
reads only the frames a kit export writes (and records their triangle counts). The facts go to a JSON file; the
reply names it.
"""

from __future__ import annotations

import json
import os
import re

import bpy
from mathutils.bvhtree import BVHTree

from satk.studio.core import readonly

from ..stats import GEOMETRY_TYPES
from . import _util as U

PROP = U.ITEM_PROP
ATTR = U.ITEM_ATTR
IDS = U.ITEM_IDS
_ID = re.compile(r"^I\d{2,4}$")
_SKIP_ROLES = frozenset({"col", "shadow", "lod", "dummy", "bone"})
_COPY = re.compile(r"_(dam|vlo)$", re.IGNORECASE)
FACTS_FORMAT = "satk.inventory-facts/1"
parse_ids = U.parse_ids
registry = U.item_registry
id_list = U.item_ids


def _register(item: str) -> int:
    """The 1-based value of ``item`` in the scene registry (appended when new)."""
    reg = registry()
    if item not in reg:
        reg.append(item)
        bpy.context.scene[IDS] = reg
    return reg.index(item) + 1


def _check_id(v, method: str) -> str:
    if not isinstance(v, str) or not _ID.match(v):
        raise U.bad(method, f"'item' must be an inventory id like I05, got {v!r}",
                    hint="satk asset inventory <project> lists the items and their ids")
    return v


def _project_item(ctx, item: str) -> tuple[str | None, bool]:
    """``(name, known)`` of ``item`` in the project's inventory when the session belongs to a project."""
    try:
        jp = ctx.journal.path
    except AttributeError:
        return None, True
    if not jp:
        return None, True
    f = os.path.join(os.path.dirname(jp), "design", "inventory.json")
    if not os.path.isfile(f):
        return None, True
    try:
        with open(f, encoding="utf-8-sig") as fh:
            inv = json.load(fh)
    except (OSError, ValueError):
        return None, True
    for it in inv.get("items") or []:
        if isinstance(it, dict) and it.get("id") == item:
            return str(it.get("name") or ""), True
    return None, False


def _face_values(me) -> list[int] | None:
    a = me.attributes.get(ATTR)
    if a is None or a.domain != "FACE" or a.data_type != "INT":
        return None
    vals = [0] * len(me.polygons)
    a.data.foreach_get("value", vals)
    return vals


def _summary(o) -> None:
    """``satk_item`` = the ids on the object's faces (object property for the reports and scene.object)."""
    vals = _face_values(o.data) if o.type == "MESH" else None
    if vals is None:
        return
    ids = id_list(o)
    seen: list[str] = []
    for v in vals:
        if v > 0 and v <= len(ids) and ids[v - 1] not in seen:
            seen.append(ids[v - 1])
    if seen:
        o[PROP] = ",".join(seen)
        o[IDS] = list(ids)
    else:
        for k in (PROP, IDS):
            if k in o:
                del o[k]


def tag(ctx, p: dict) -> dict:
    """Tag objects or a face selection with an inventory item id (item: I05; clear=true removes the tags)."""
    M = "scene.tag"
    clear = U.flag(p, "clear", M, False)
    item = None if clear else _check_id(U.require(p, "item", M, hint="an inventory id such as I05"), M)
    objs = U.resolve(ctx, p, M)
    sel = p.get("select")
    if sel is not None:
        U.check_select(sel, M)
    value = _register(item) if item else 0
    faces_n = 0
    out_objs = []
    for o in objs:
        if o.get("satk_ghost") or o.get("satk_ref"):
            raise U.bad(M, f"{o.name} is a ghost or reference object: tag the model's own geometry")
        if o.type != "MESH":
            if sel is not None:
                raise U.bad(M, f"{o.name} is a {o.type}: a face selection needs a mesh (mesh.convert first)")
            if o.type not in GEOMETRY_TYPES:
                raise U.bad(M, f"{o.name} is a {o.type} without geometry: tag the mesh that shows the item")
            if clear:
                o.pop(PROP, None)
            else:
                ids = parse_ids(o.get(PROP))
                o[PROP] = ",".join(ids + [item] if item not in ids else ids)
            out_objs.append(o.name)
            continue
        if o.data.users > 1 and sel is not None:
            raise U.bad(M, f"{o.name} shares its mesh with other objects: a face tag would tag them too",
                        hint="scene.duplicate with linked=false makes a single-user copy")
        if sel is None:
            me = o.data
            n = len(me.polygons)
            if me.users > 1:
                ctx.warn(f"SHARED: {o.name} shares its mesh with {me.users - 1} other object(s): they get the tag too")
            if clear:
                a = me.attributes.get(ATTR)
                if a is not None:
                    me.attributes.remove(a)
                for k in (PROP, IDS):
                    o.pop(k, None)
            else:
                a = me.attributes.get(ATTR)
                if a is None or a.domain != "FACE" or a.data_type != "INT":
                    if a is not None:
                        me.attributes.remove(a)
                    a = me.attributes.new(ATTR, "INT", "FACE")
                a.data.foreach_set("value", [value] * n)
                o[IDS] = registry()
                _summary(o)
                if n == 0:
                    o[PROP] = item
            faces_n += n
        else:
            with U.edit_mesh(o) as bm:
                layer = bm.faces.layers.int.get(ATTR)
                if layer is None:
                    layer = bm.faces.layers.int.new(ATTR)
                    # faces tagged so far through the object property keep their id
                    own = parse_ids(o.get(PROP))
                    if len(own) == 1 and not clear:
                        v0 = _register(own[0])
                        for f in bm.faces:
                            f[layer] = v0
                faces = U.require_faces(U.select_faces(o, bm, sel, M), M, sel)
                for f in faces:
                    f[layer] = value
                faces_n += len(faces)
            o[IDS] = registry()
            _summary(o)
        out_objs.append(o.name)
    out: dict = {"objects": out_objs, "faces": faces_n, "changed": out_objs}
    if item:
        out["item"] = item
        name, known = _project_item(ctx, item)
        if name:
            out["name"] = name[:80]
        if not known:
            ctx.warn(f"UNKNOWN: {item} is not in the project's design/inventory.json")
    else:
        out["cleared"] = True
    return out


# --------------------------------------------------------------------------- facts


def _hidden(o) -> bool:
    try:
        vis = o.visible_get()
    except RuntimeError:
        vis = True
    return bool(o.get("satk_ghost") or o.get("satk_ref") or o.hide_render or not vis)


def _role_skip(o) -> bool:
    if str(o.get("satk_role") or "") in _SKIP_ROLES:
        return True
    if _COPY.search(str(o.get("satk_frame") or o.name).split(".")[0]):
        return True
    for c in o.users_collection:
        if c.get("satk_col_of") or c.get("satk_lod_of"):
            return True
    return False


def _scope_objects(scope: str, model: str | None) -> list[tuple]:
    """``[(object, frame name | None)]`` of the scope."""
    if scope == "kit":
        from satk_blender.kit import util as KU

        coll = KU.clump(model)
        out = []
        for o in coll.objects:
            if o.get("satk_role") not in ("root", "part") or o.type not in GEOMETRY_TYPES:
                continue
            fr = str(o.get("satk_frame") or o.name)
            if _COPY.search(fr):
                continue
            out.append((o, fr))
        return out
    return [(o, None) for o in bpy.context.scene.objects if o.type in GEOMETRY_TYPES and not _role_skip(o)]


class _Acc:
    """The geometry of one item: world triangles and their vertex keys."""

    def __init__(self):
        self.objects: list[str] = []
        self.frames: list[str] = []
        self.pos: list = []          # world vertex positions
        self.tris: list = []         # triangles as indices into pos
        self.vkey: dict = {}         # (object, vertex index) -> index into pos
        self.area = 0.0
        self.tex_area = 0.0          # area of faces whose material carries an image texture
        self.paint_area = 0.0        # area of faces on a vehicle paint key colour
        self.frame_tris: dict = {}   # frame -> triangles of this item in it
        self.textures: list[str] = []

    def add_tri(self, oname: str, vids, co_world, textured: bool = False, painted: bool = False) -> None:
        idx = []
        for v in vids:
            k = (oname, v)
            j = self.vkey.get(k)
            if j is None:
                j = self.vkey[k] = len(self.pos)
                self.pos.append(co_world[v])
            idx.append(j)
        self.tris.append(idx)
        a, b, c = (self.pos[i] for i in idx)
        ar = (b - a).cross(c - a).length / 2
        self.area += ar
        if textured:
            self.tex_area += ar
        if painted:
            self.paint_area += ar

    def roots(self) -> list[int]:
        """The connected piece of every triangle (a root vertex index)."""
        parent = list(range(len(self.pos)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for t in self.tris:
            r0 = find(t[0])
            for v in t[1:]:
                r = find(v)
                if r != r0:
                    parent[r] = r0
        return [find(t[0]) for t in self.tris]

    def pieces(self) -> int:
        return len(set(self.roots()))

    def split(self, keep=None) -> list["_Acc"]:
        """One accumulator per connected piece (``keep``: the piece roots to keep)."""
        groups: dict = {}
        for t, r in zip(self.tris, self.roots()):
            if keep is None or r in keep:
                groups.setdefault(r, []).append(t)
        out = []
        for _r, tris in sorted(groups.items()):
            a = _Acc()
            remap: dict = {}
            for t in tris:
                idx = []
                for i in t:
                    j = remap.get(i)
                    if j is None:
                        j = remap[i] = len(a.pos)
                        a.pos.append(self.pos[i])
                    idx.append(j)
                a.tris.append(idx)
            out.append(a)
        return out

    def bvh(self):
        return BVHTree.FromPolygons(self.pos, self.tris, all_triangles=True)


def _samples(a: _Acc) -> list:
    """Points of an item's surface: its vertices and the centres of its triangles (a face lying on another face
    touches it even when no vertex of either lies on the other)."""
    pts = list(a.pos)
    for t in a.tris:
        p0, p1, p2 = (a.pos[i] for i in t)
        pts.append((p0 + p1 + p2) / 3.0)
    step = max(1, len(pts) // 40000)
    return pts[::step]


def _gap(a: _Acc, b: _Acc, search: float, cache: dict, ka: str, kb: str) -> float | None:
    """Smallest distance (m) between the geometry of ``a`` and ``b`` within ``search`` (``None``: farther)."""
    lo_a, hi_a = _box(a.pos)
    lo_b, hi_b = _box(b.pos)
    if any(lo_a[i] - hi_b[i] > search or lo_b[i] - hi_a[i] > search for i in range(3)):
        return None
    ta = cache.get(ka) or cache.setdefault(ka, a.bvh())
    tb = cache.get(kb) or cache.setdefault(kb, b.bvh())
    if ta.overlap(tb):
        return 0.0                                  # the surfaces cross
    best = None
    for src, tree, dst in ((a, tb, b), (b, ta, a)):
        lo, hi = _box(dst.pos)
        for co in _samples(src):
            if any(co[i] < lo[i] - search or co[i] > hi[i] + search for i in range(3)):
                continue
            hit = tree.find_nearest(co, search)
            if hit[0] is not None and (best is None or hit[3] < best):
                best = hit[3]
                if best <= 1e-6:
                    return 0.0
    return best


def _piece_gap(pieces: list, b: _Acc, search: float, cache: dict, kb: str) -> float | None:
    """The gap of the worst-attached piece of an item to ``b`` (``None``: one piece is farther than ``search``)."""
    worst = 0.0
    for pc in pieces:
        g = _gap_one(pc, b, search, cache, kb)
        if g is None:
            return None
        worst = max(worst, g)
    return worst


def _gap_one(a: _Acc, b: _Acc, search: float, cache: dict, kb: str) -> float | None:
    """:func:`_gap` of a piece (its BVH is not cached)."""
    lo_a, hi_a = _box(a.pos)
    lo_b, hi_b = _box(b.pos)
    if any(lo_a[i] - hi_b[i] > search or lo_b[i] - hi_a[i] > search for i in range(3)):
        return None
    ta = a.bvh()
    tb = cache.get(kb) or cache.setdefault(kb, b.bvh())
    if ta.overlap(tb):
        return 0.0
    best = None
    for src, tree, dst in ((a, tb, b), (b, ta, a)):
        lo, hi = _box(dst.pos)
        for co in _samples(src):
            if any(co[i] < lo[i] - search or co[i] > hi[i] + search for i in range(3)):
                continue
            hit = tree.find_nearest(co, search)
            if hit[0] is not None and (best is None or hit[3] < best):
                best = hit[3]
                if best <= 1e-6:
                    return 0.0
    return best


def _textured(mat) -> bool:
    """A material whose node tree draws an image texture (the satk look's own nodes aside)."""
    try:
        nt = mat.node_tree if mat is not None else None
    except AttributeError:
        return False
    if nt is None:
        return False
    for n in nt.nodes:
        if n.type == "TEX_IMAGE" and n.image is not None and not n.name.startswith("SATK"):
            return True
    return False


_PAINT_RGB = ((60, 255, 0), (255, 0, 175))


def _painted(mat) -> bool:
    """A material on a vehicle paint key colour (primary 60,255,0 or secondary 255,0,175) or a kit paint role."""
    if mat is None:
        return False
    if str(mat.get("satk_role") or "") in ("paint1", "paint2"):
        return True
    try:
        rgb = tuple(int(round(float(c) * 255.0)) for c in mat.diffuse_color[:3])
    except (AttributeError, TypeError, ValueError):
        return False
    return any(all(abs(a - b) <= 2 for a, b in zip(rgb, key)) for key in _PAINT_RGB)


def _texture_name(mat) -> str:
    try:
        for n in mat.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image is not None and not n.name.startswith("SATK"):
                return n.image.name.split("/")[-2 if n.image.name.count("/") >= 2 else -1][:40]
    except AttributeError:
        pass
    return ""


def _box(pos) -> tuple[list[float], list[float]]:
    lo = [min(v[i] for v in pos) for i in range(3)]
    hi = [max(v[i] for v in pos) for i in range(3)]
    return lo, hi


@readonly
def items(ctx, p: dict) -> dict:
    """Measure every tagged inventory item (objects, frames, size, pieces, gaps to its parents) into a facts file."""
    M = "scene.items"
    scope = U.text(p, "scope", M, "scene", choices=("scene", "kit"))
    search = U.num(p, "search", M, 0.3, lo=0.01, hi=5.0)
    pairs = p.get("pairs") or {}
    if not isinstance(pairs, dict):
        raise U.bad(M, "'pairs' must be {child id: [parent ids]}")
    model = p.get("model") if scope == "kit" else None
    objs = _scope_objects(scope, model)
    dg = bpy.context.evaluated_depsgraph_get()
    acc: dict[str, _Acc] = {}
    hidden: dict[str, list[str]] = {}
    ambiguous: dict[str, list[str]] = {}
    untagged: list[dict] = []
    frames: dict[str, int] = {}
    tagged: set[str] = set()
    vusers: dict = {}            # (object, vertex) -> ids of the faces that use it ("" = untagged)
    tex_cache: dict = {}
    for o, frame in objs:
        prop_ids = parse_ids(o.get(PROP))
        if scope != "kit" and _hidden(o):              # a kit export writes hidden slots too
            vals_ids = []
            if o.type == "MESH":
                vals = _face_values(o.data) or []
                ids = id_list(o)
                vals_ids = sorted({ids[v - 1] for v in vals if 0 < v <= len(ids)})
            for i in sorted(set(prop_ids) | set(vals_ids)):
                hidden.setdefault(i, []).append(o.name)
            continue
        ev = o.evaluated_get(dg)
        try:
            me = ev.to_mesh()
        except RuntimeError:
            continue
        try:
            me.calc_loop_triangles()
            n_tri = len(me.loop_triangles)
            if frame is not None:
                frames[frame] = frames.get(frame, 0) + n_tri
            if not n_tri:
                for i in prop_ids:
                    hidden.setdefault(i, []).append(o.name)
                continue
            vals = _face_values(me)
            ids = id_list(o)
            face_ids = sorted({ids[v - 1] for v in vals if 0 < v <= len(ids)}) if vals else []
            rest = [i for i in prop_ids if i not in face_ids]
            zero_id = None
            if len(rest) == 1:
                zero_id = rest[0]
            elif len(rest) > 1:
                ambiguous[o.name] = rest
            mw = o.matrix_world
            co = [mw @ v.co for v in me.vertices]
            slots = [sl.material for sl in o.material_slots]
            tex_of = []
            for m in slots:
                key = m.name if m is not None else ""
                if key not in tex_cache:
                    tex_cache[key] = (_textured(m), _texture_name(m), _painted(m))
                tex_of.append(tex_cache[key])
            n_untagged = 0
            for lt in me.loop_triangles:
                v = vals[lt.polygon_index] if vals else 0
                iid = ids[v - 1] if 0 < v <= len(ids) else zero_id
                for vi in lt.vertices:
                    vusers.setdefault((o.name, vi), set()).add(iid or "")
                if iid is None:
                    n_untagged += 1
                    continue
                a = acc.get(iid)
                if a is None:
                    a = acc[iid] = _Acc()
                if o.name not in a.objects:
                    a.objects.append(o.name)
                    if frame is not None and frame not in a.frames:
                        a.frames.append(frame)
                mi = me.polygons[lt.polygon_index].material_index
                tx = tex_of[mi] if mi < len(tex_of) else (False, "", False)
                a.add_tri(o.name, tuple(lt.vertices), co, textured=tx[0], painted=tx[2])
                if tx[1] and tx[1] not in a.textures and len(a.textures) < 4:
                    a.textures.append(tx[1])
                if frame is not None:
                    a.frame_tris[frame] = a.frame_tris.get(frame, 0) + 1
            if n_untagged == n_tri:
                untagged.append({"object": o.name, "tris": n_tri})
            tagged |= set(face_ids) | set(prop_ids)
        finally:
            ev.to_mesh_clear()
    out_items: dict = {}
    own_pieces: dict = {}
    for iid, a in sorted(acc.items()):
        lo, hi = _box(a.pos)
        own = [False] * len(a.pos)
        shares: set = set()
        for (oname, vi), j in a.vkey.items():
            users = vusers.get((oname, vi)) or {iid}
            if users == {iid}:
                own[j] = True
            else:
                shares |= users - {iid}
        roots = a.roots()
        piece_own: dict = {}
        for t, r in zip(a.tris, roots):
            piece_own[r] = piece_own.get(r, False) or any(own[i] for i in t)
        n_own = sum(1 for v in piece_own.values() if v)
        own_pieces[iid] = {r for r, v in piece_own.items() if v}
        row = {"objects": a.objects, "tris": len(a.tris), "area": round(a.area, 5),
               "size": [round(hi[i] - lo[i], 4) for i in range(3)],
               "center": [round((hi[i] + lo[i]) / 2, 3) for i in range(3)], "pieces": n_own,
               "own_verts": int(sum(own)), "tex_share": round(a.tex_area / a.area, 3) if a.area > 0 else 0.0,
               "paint_share": round(a.paint_area / a.area, 3) if a.area > 0 else 0.0}
        if len(piece_own) > n_own:
            row["patch_pieces"] = len(piece_own) - n_own
        if shares:
            row["shares_with"] = sorted("untagged geometry" if x == "" else x for x in shares)[:6]
        if a.frames:
            row["frames"] = a.frames
        if a.frame_tris:
            row["frame_tris"] = dict(sorted(a.frame_tris.items()))
        if a.textures:
            row["textures"] = a.textures
        out_items[iid] = row
    gaps: dict = {}
    cache: dict = {}
    for child, parents in sorted(pairs.items()):
        if child not in acc:
            continue
        ps = [parents] if isinstance(parents, str) else [q for q in parents if isinstance(q, str)]
        row = {}
        keep = own_pieces.get(child) or None
        pieces = acc[child].split(keep) if keep and len(keep) > 1 else None
        for q in ps:
            if q in acc and q != child:
                if pieces:      # every piece of the item touches its parent: the worst one decides
                    g = _piece_gap(pieces, acc[q], search, cache, q)
                elif keep:
                    g = _gap_one(acc[child].split(keep)[0], acc[q], search, cache, q)
                else:
                    g = _gap(acc[child], acc[q], search, cache, child, q)
                row[q] = round(g * 1000.0, 1) if g is not None else None
        if row:
            gaps[child] = row
    facts = {"format": FACTS_FORMAT, "version": 2, "scope": scope, "search_mm": round(search * 1000),
             "items": out_items,
             "gaps": gaps, "tagged": sorted(tagged | set(acc)), "objects": len(objs)}
    for key, val in (("ambiguous", ambiguous), ("hidden", hidden), ("untagged", untagged), ("frames", frames)):
        if val:
            facts[key] = val
    path = os.path.join(ctx.out_dir, "items", f"{ctx.n:04d}-{scope}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=1, sort_keys=True)
    res = {"file": os.path.abspath(path).replace("\\", "/"), "items": len(out_items), "tagged": len(facts["tagged"]),
           "objects": len(objs)}
    if untagged:
        res["untagged_objects"] = len(untagged)
    return res


METHODS = {"scene.tag": tag, "scene.items": items}
