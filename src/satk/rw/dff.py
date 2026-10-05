"""DFF patching on the lossless chunk tree (``satk.rw.dff``). Stdlib only.

:class:`DffDoc` wraps a parsed DFF (:mod:`satk.rw.chunk`) and edits it in place; untouched chunks are
written back byte for byte. Operations (all return a list of change records ``(kind, count)``):

* :meth:`DffDoc.rename_textures` - texture and mask names of materials, textures embedded in MatFX
  effects (environment/bump/dual) and the specular-material texture (case-insensitive match);
* :meth:`DffDoc.restamp` - RW version III/VC/SA with librw's struct conversions (Geometry surface
  properties below 3.4, Clump light/camera counts above 3.3, old/new Skin PLG format);
* :meth:`DffDoc.night_colors` - add (copy of the prelit day colours) or remove ``ExtraVertColour``;
* :meth:`DffDoc.recalc_normals` - area-weighted smooth vertex normals, sets the NORMALS flag;
* :meth:`DffDoc.set_material_color` - material RGBA, sets MODULATE_MATERIAL_COLOR on the geometry.

Materials are numbered like ``satk formats dump --level full``: geometries in stream order (global over
clumps), then MaterialList slots; instanced slots (``index >= 0``) point at the same Material chunk.
"""

from __future__ import annotations

import math
import struct
from array import array
from dataclasses import dataclass

from . import codecs as C
from .chunk import (ATOMIC, BREAKABLE, CAMERA, CLUMP, EXTENSION, FX2D, GEOMETRY, GEOMLIST, LIGHT, MATERIAL, MATFX,
                    MATLIST, NIGHT, SKIN, SPECULAR, STRING, STRUCT, TEXTURE, Chunk, RwStream, chunk_name, pack_libid,
                    parse)

__all__ = ["DffDoc", "GeomRef", "MatRef", "PatchError", "TYPED", "typed_roundtrip", "first_difference"]

NIGHT_MAGIC = 1           # "has colours" word written for added night colours (any non-zero value works)


class PatchError(ValueError):
    """The requested edit is impossible for this file (message says why)."""


@dataclass
class GeomRef:
    index: int
    node: Chunk               # Geometry
    struct: Chunk             # its Struct

    @property
    def version(self) -> int:
        return self.struct.version

    def data(self) -> C.GeometryData:
        return C.decode_geometry(self.struct.data or b"", self.version)

    def store(self, g: C.GeometryData) -> None:
        self.struct.data = C.encode_geometry(g, self.version)

    def ext(self, create: bool = False) -> Chunk | None:
        e = self.node.child(EXTENSION)
        if e is None and create:
            e = Chunk(EXTENSION, self.node.libid, kids=[])
            self.node.kids.append(e)
        return e


@dataclass
class MatRef:
    row: int                  # global slot number (formats dump row)
    geom: int
    slot: int
    node: Chunk               # Material (shared by instanced slots)


class DffDoc:
    """A DFF as an editable chunk tree. ``DffDoc.parse(blob).to_bytes() == blob``."""

    def __init__(self, stream: RwStream):
        self.stream = stream
        if not self.clumps():
            raise PatchError("not a DFF: no Clump chunk")

    @classmethod
    def parse(cls, data: bytes) -> "DffDoc":
        try:
            return cls(parse(data))
        except ValueError as e:
            raise PatchError(str(e)) from None

    def to_bytes(self, *, with_tail: bool = False) -> bytes:
        """The DFF bytes (without the IMG sector padding unless ``with_tail``)."""
        return self.stream.to_bytes(with_tail=with_tail)

    # ------------------------------------------------------------------ navigation
    def clumps(self) -> list[Chunk]:
        return [c for c in self.stream.chunks if c.type == CLUMP and c.kids is not None]

    def geometries(self) -> list[GeomRef]:
        out: list[GeomRef] = []
        for cl in self.clumps():
            nodes: list[Chunk] = []
            for k in cl.kids:
                if k.type == GEOMLIST and k.kids is not None:
                    nodes += [g for g in k.kids if g.type == GEOMETRY]
                elif k.type == ATOMIC and k.kids is not None:
                    nodes += [g for g in k.kids if g.type == GEOMETRY]      # geometry stored in the atomic
            for g in nodes:
                st = g.child(STRUCT) if g.kids is not None else None
                if st is None:
                    raise PatchError(f"geometry {len(out)} has no Struct (opaque or broken chunk)")
                out.append(GeomRef(len(out), g, st))
        return out

    def materials(self) -> list[MatRef]:
        out: list[MatRef] = []
        for gr in self.geometries():
            ml = gr.node.child(MATLIST)
            if ml is None or ml.kids is None:
                continue
            st = ml.child(STRUCT)
            if st is None or len(st.data or b"") < 4:
                raise PatchError(f"geometry {gr.index}: MaterialList without Struct")
            (n,) = struct.unpack_from("<i", st.data, 0)
            if n < 0 or 4 + 4 * n > len(st.data):
                raise PatchError(f"geometry {gr.index}: MaterialList struct too short for {n} slots")
            refs = struct.unpack_from(f"<{n}i", st.data, 4)
            mats = ml.children(MATERIAL)
            slots: list[Chunk] = []
            j = 0
            for i, r in enumerate(refs):
                if r < 0:
                    if j >= len(mats):
                        raise PatchError(f"geometry {gr.index}: MaterialList slot {i} has no Material chunk")
                    slots.append(mats[j])
                    j += 1
                elif r < i:
                    slots.append(slots[r])
                else:
                    raise PatchError(f"geometry {gr.index}: slot {i} references slot {r}")
            for i, m in enumerate(slots):
                out.append(MatRef(len(out), gr.index, i, m))
        return out

    def unique_materials(self) -> list[Chunk]:
        seen: set[int] = set()
        out = []
        for m in self.materials():
            if id(m.node) not in seen:
                seen.add(id(m.node))
                out.append(m.node)
        return out

    def texture_names(self) -> list[str]:
        """Diffuse texture names of all materials (stream order, duplicates removed)."""
        out: list[str] = []
        for m in self.unique_materials():
            t = m.child(TEXTURE)
            if t is not None and t.kids is not None:
                s = t.children(STRING)
                if s:
                    name = C.decode_string(s[0].data or b"").text
                    if name and name not in out:
                        out.append(name)
        return out

    # ------------------------------------------------------------------ rename
    def rename_textures(self, mapping: dict[str, str]) -> list[tuple[str, int]]:
        """Rename texture references; ``mapping`` keys are matched case-insensitively."""
        low = {k.lower(): v for k, v in mapping.items()}
        for new in low.values():
            _check_tex_name(new, 31)
        counts = {"texture": 0, "mask": 0, "matfx": 0, "specular": 0}

        def rename_tex_chunk(tex: Chunk, kinds: tuple[str, str]) -> None:
            if tex.kids is None:
                return
            for kind, s in zip(kinds, tex.children(STRING)):
                cur = C.decode_string(s.data or b"").text
                new = low.get(cur.lower())
                if cur and new is not None and new != cur:
                    s.data = C.make_string(new)
                    counts[kind] += 1

        for mat in self.unique_materials():
            tex = mat.child(TEXTURE)
            if tex is not None:
                rename_tex_chunk(tex, ("texture", "mask"))
            ext = mat.child(EXTENSION)
            if ext is None or ext.kids is None:
                continue
            for plg in ext.kids:
                if plg.type == MATFX and plg.data:
                    try:
                        fx = C.decode_matfx(plg.data)
                    except C.CodecError:
                        continue
                    before = counts["matfx"]
                    for t in fx.textures():
                        rename_tex_chunk(t, ("matfx", "matfx"))
                    if counts["matfx"] != before:
                        plg.data = C.encode_matfx(fx)
                elif plg.type == SPECULAR and plg.data:
                    try:
                        sp = C.decode_specular(plg.data)
                    except C.CodecError:
                        continue
                    new = low.get(sp.texture.lower())
                    if sp.texture and new is not None and new != sp.texture:
                        _check_tex_name(new, 23)
                        sp.name = new.encode("ascii").ljust(24, b"\0")
                        plg.data = C.encode_specular(sp)
                        counts["specular"] += 1
        return [(k, v) for k, v in counts.items() if v]

    # ------------------------------------------------------------------ material colour
    def set_material_color(self, specs: list[tuple[int | None, int, tuple[int, int, int, int]]]
                           ) -> list[tuple[str, int]]:
        """``specs``: ``(geom or None, slot-or-row, rgba)``; ``geom=None`` -> global row number."""
        mats = self.materials()
        geoms = self.geometries()
        done = 0
        modulate = set()
        for geom, idx, rgba in specs:
            if geom is None:
                if not 0 <= idx < len(mats):
                    raise PatchError(f"material {idx} out of range (0..{len(mats) - 1})")
                ref = mats[idx]
            else:
                cand = [m for m in mats if m.geom == geom and m.slot == idx]
                if not cand:
                    raise PatchError(f"no material {geom}:{idx} (geometries 0..{len(geoms) - 1})")
                ref = cand[0]
            st = ref.node.child(STRUCT)
            if st is None:
                raise PatchError(f"material {ref.row} has no Struct")
            md = C.decode_material(st.data or b"", st.version)
            new = bytes(rgba)
            if md.color != new:
                md.color = new
                st.data = C.encode_material(md, st.version)
                done += 1
            if new != b"\xff\xff\xff\xff":
                gr = geoms[ref.geom]
                g = gr.data()
                if not g.flags & C.GEO_MODULATE:
                    g.flags |= C.GEO_MODULATE
                    gr.store(g)
                    modulate.add(ref.geom)
        out = [("material_color", done)]
        if modulate:
            out.append(("modulate_flag", len(modulate)))
        return [x for x in out if x[1]]

    # ------------------------------------------------------------------ night colours
    def night_colors(self, mode: str, warn: list[str] | None = None) -> list[tuple[str, int]]:
        """``mode``: ``add`` (copy the prelit colours where missing) or ``remove``."""
        n = 0
        for gr in self.geometries():
            ext = gr.ext(create=(mode == "add"))
            if mode == "remove":
                if ext is not None and ext.kids is not None:
                    keep = [k for k in ext.kids if k.type != NIGHT]
                    n += len(ext.kids) - len(keep)
                    ext.kids = keep
                continue
            if ext.kids is None:
                raise PatchError(f"geometry {gr.index}: opaque Extension")
            cur = [k for k in ext.kids if k.type == NIGHT]
            g = gr.data()
            if cur:
                nd = C.decode_night(cur[0].data or b"", g.num_verts)
                if nd.magic:
                    continue
            if g.prelit is None:
                if warn is not None:
                    warn.append(f"NO_PRELIT: geometry {gr.index} has no prelit colours; night colours not added")
                continue
            node = Chunk(NIGHT, gr.node.libid, data=C.encode_night(C.NightData(NIGHT_MAGIC, g.prelit)))
            if cur:
                ext.kids[ext.kids.index(cur[0])] = node
            else:
                types = [k.type for k in ext.kids]
                at = types.index(BREAKABLE) + 1 if BREAKABLE in types else (
                    types.index(FX2D) if FX2D in types else len(types))
                ext.kids.insert(at, node)
            n += 1
        return [("night_" + ("added" if mode == "add" else "removed"), n)] if n else []

    # ------------------------------------------------------------------ normals
    def recalc_normals(self, warn: list[str] | None = None) -> list[tuple[str, int]]:
        n = 0
        for gr in self.geometries():
            g = gr.data()
            if g.is_native:
                raise PatchError(f"geometry {gr.index} is platform-native; normals cannot be rebuilt")
            if not g.num_verts or not g.morphs:
                continue
            tris = _triangles(g, gr)
            if not tris:
                if warn is not None:
                    warn.append(f"NO_TRIANGLES: geometry {gr.index} has no triangles; normals left as they are")
                continue
            for m in g.morphs:
                if not m.has_verts:
                    continue
                m.normals = _smooth_normals(m.verts, g.num_verts, tris)
                m.has_normals = 1
            g.flags |= C.GEO_NORMALS
            gr.store(g)
            n += 1
        return [("normals", n)] if n else []

    # ------------------------------------------------------------------ RW version
    def restamp(self, target: int) -> list[tuple[str, int]]:
        """Re-stamp every chunk with RW version ``target`` and convert the version-dependent structs."""
        libid = pack_libid(target)
        counts = {"chunks": 0, "geometry_struct": 0, "clump_struct": 0, "skin": 0, "lights_dropped": 0}
        geoms = {id(g.node): g for g in self.geometries()}
        if target <= 0x33000:
            for cl in self.clumps():
                counts["lights_dropped"] += _drop_lights(cl)
        heads = {id(cl.kids[0]) for cl in self.clumps() if cl.kids and cl.kids[0].type == STRUCT}

        for top in self.stream.chunks:
            for node, path in list(top.walk()):
                parent = path[-1] if path else None
                old = node.version
                if node.type == STRUCT and parent == GEOMETRY:
                    g = C.decode_geometry(node.data or b"", old)
                    if g.is_native:
                        raise PatchError("platform-native geometry cannot change RW version")
                    new = C.encode_geometry(g, target)
                    if new != node.data:
                        node.data = new
                        counts["geometry_struct"] += 1
                elif node.type == STRUCT and parent == CLUMP and id(node) in heads:
                    c = C.decode_clump(node.data or b"", old)
                    if target <= 0x33000:
                        c.lights = c.cameras = 0
                    if old <= 0x33000 < target:
                        c.short = False          # librw writes the light/camera counts from 3.3 on
                    new = C.encode_clump(c, target)
                    if new != node.data:
                        node.data = new
                        counts["clump_struct"] += 1
                elif node.type == STRUCT and parent == MATERIAL:
                    node.data = C.encode_material(C.decode_material(node.data or b"", old), target)
                elif node.type == SKIN and node.data:
                    owner = _owner_geometry(top, node, geoms)
                    nv = owner.data().num_verts if owner is not None else 0
                    s = C.decode_skin(node.data, nv)
                    s2 = C.skin_to_old(s) if target < 0x34000 else C.skin_to_new(s, nv)
                    new = C.encode_skin(s2)
                    if new != node.data:
                        node.data = new
                        counts["skin"] += 1
                elif node.type == MATFX and node.data:
                    try:
                        fx = C.decode_matfx(node.data)
                    except C.CodecError:
                        fx = None
                    if fx is not None and fx.textures():
                        for t in fx.textures():
                            for sub, _p in t.walk():
                                sub.libid = libid
                        node.data = C.encode_matfx(fx)
                if node.libid != libid:
                    node.libid = libid
                    counts["chunks"] += 1
        return [(k, v) for k, v in counts.items() if v]


# ============================================================================ helpers


def _drop_lights(clump: Chunk) -> int:
    """Remove Light/Camera chunks and the frame-index Struct before each (RW <= 3.3 cannot count them)."""
    kids = clump.kids or []
    keep: list[Chunk] = []
    dropped = 0
    for i, k in enumerate(kids):
        if k.type in (LIGHT, CAMERA):
            dropped += 1
            if keep and keep[-1].type == STRUCT and i > 0 and keep[-1] is not kids[0]:
                keep.pop()
            continue
        keep.append(k)
    clump.kids = keep
    return dropped


def _check_tex_name(name: str, maxlen: int) -> None:
    if not name or len(name) > maxlen or not name.isascii() or any(c in name for c in "\0\\/"):
        raise PatchError(f"bad texture name {name!r}: 1..{maxlen} ASCII characters, no slashes")


def _owner_geometry(top: Chunk, node: Chunk, geoms: dict) -> GeomRef | None:
    for g, _p in top.walk():
        if g.type == GEOMETRY and g.kids is not None:
            ext = g.child(EXTENSION)
            if ext is not None and ext.kids is not None and any(k is node for k in ext.kids):
                return geoms.get(id(g))
    return None


def _triangles(g: C.GeometryData, gr: GeomRef) -> list[tuple[int, int, int]]:
    """Triangles ``(v0, v1, v2)`` from the struct, else from the BinMesh (lists or strips)."""
    nv = g.num_verts
    out: list[tuple[int, int, int]] = []
    if g.num_tris:
        w = g.triangles()
        for i in range(0, len(w), 4):
            a, b, c = w[i + 1], w[i], w[i + 3]
            if a < nv and b < nv and c < nv:
                out.append((a, b, c))
        return out
    ext = gr.ext()
    bm = next((k for k in (ext.kids or ()) if k.type == 0x50E), None) if ext is not None else None
    if bm is None or not bm.data:
        return out
    try:
        b = C.decode_binmesh(bm.data)
    except C.CodecError:
        return out
    for _mat, raw in b.meshes:
        ix = array("I")
        ix.frombytes(raw)
        if b.flags & 1:                                   # triangle strip
            for i in range(len(ix) - 2):
                a, bb, c = ix[i], ix[i + 1], ix[i + 2]
                if a == bb or bb == c or a == c:
                    continue
                out.append((a, bb, c) if i % 2 == 0 else (bb, a, c))
        else:
            out += [(ix[i], ix[i + 1], ix[i + 2]) for i in range(0, len(ix) - 2, 3)]
    return [t for t in out if max(t) < nv]


def _smooth_normals(verts: bytes, nv: int, tris: list[tuple[int, int, int]]) -> bytes:
    p = array("f")
    p.frombytes(verts)
    acc = [0.0] * (3 * nv)
    for a, b, c in tris:
        ax, ay, az = p[3 * a], p[3 * a + 1], p[3 * a + 2]
        ux, uy, uz = p[3 * b] - ax, p[3 * b + 1] - ay, p[3 * b + 2] - az
        vx, vy, vz = p[3 * c] - ax, p[3 * c + 1] - ay, p[3 * c + 2] - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for v in (a, b, c):
            acc[3 * v] += nx
            acc[3 * v + 1] += ny
            acc[3 * v + 2] += nz
    out = array("f")
    for v in range(nv):
        x, y, z = acc[3 * v], acc[3 * v + 1], acc[3 * v + 2]
        ln = math.sqrt(x * x + y * y + z * z)
        if ln > 1e-12 and math.isfinite(ln):
            out.extend((x / ln, y / ln, z / ln))
        else:
            out.extend((0.0, 0.0, 1.0))
    return out.tobytes()


# ============================================================================ round-trip checks

#: (chunk type, parent type or None) -> name of the typed codec checked by :func:`typed_roundtrip`.
TYPED = ("geometry", "material", "clump", "framelist", "string", "skin", "night", "matfx", "specular",
         "binmesh", "2dfx")


def typed_roundtrip(doc: DffDoc) -> tuple[DffDoc, list[str]]:
    """Decode every struct with a typed codec and encode it again (from the decoded fields).

    Returns ``(rebuilt doc, problems)``; ``problems`` lists ``codec:<name>`` for payloads a codec could
    not decode and ``typed:<name>`` for payloads whose re-encoding differs. The rebuilt document holds
    the re-encoded payloads, so ``rebuilt.to_bytes() == original`` proves the codecs are exact.
    """
    problems: list[str] = []
    geoms = {id(g.node): g for g in doc.geometries()}
    heads = {id(cl.kids[0]) for cl in doc.clumps() if cl.kids and cl.kids[0].type == STRUCT}

    def check(name: str, node: Chunk, fn) -> None:
        try:
            new = fn(node.data or b"")
        except C.CodecError:
            problems.append(f"codec:{name}")
            return
        if new != node.data:
            problems.append(f"typed:{name}")
        node.data = new

    for top in doc.stream.chunks:
        for node, path in list(top.walk()):
            if node.data is None:
                continue
            parent = path[-1] if path else None
            v = node.version
            if node.type == STRUCT and parent == GEOMETRY:
                check("geometry", node, lambda d: C.encode_geometry(C.decode_geometry(d, v), v))
            elif node.type == STRUCT and parent == MATERIAL:
                check("material", node, lambda d: C.encode_material(C.decode_material(d, v), v))
            elif node.type == STRUCT and parent == CLUMP and id(node) in heads:
                check("clump", node, lambda d: C.encode_clump(C.decode_clump(d, v), v))
            elif node.type == STRUCT and parent == 0x0E:
                check("framelist", node, lambda d: C.encode_framelist(C.decode_framelist(d)))
            elif node.type == STRING:
                check("string", node, lambda d: C.encode_string(C.decode_string(d)))
            elif node.type in (SKIN, NIGHT) and parent == EXTENSION:
                owner = _owner_geometry(top, node, geoms)
                nv = owner.data().num_verts if owner is not None else 0
                if node.type == SKIN:
                    check("skin", node, lambda d: C.encode_skin(C.decode_skin(d, nv)))
                else:
                    check("night", node, lambda d: C.encode_night(C.decode_night(d, nv)))
            elif node.type == MATFX and len(path) >= 2 and path[-2] == MATERIAL:
                check("matfx", node, lambda d: C.encode_matfx(C.decode_matfx(d)))
            elif node.type == SPECULAR:
                check("specular", node, lambda d: C.encode_specular(C.decode_specular(d)))
            elif node.type == 0x50E:
                check("binmesh", node, lambda d: C.encode_binmesh(C.decode_binmesh(d)))
            elif node.type == FX2D:
                check("2dfx", node, lambda d: C.encode_2dfx(C.decode_2dfx(d)))
    return doc, problems


_CONT = {0x10, 0x0E, 0x1A, 0x0F, 0x08, 0x07, 0x06, 0x14, 0x03, 0x2B, 0x12, 0x135, 0x15, 0x16, 0x05, 0x510}


def first_difference(a: bytes, b: bytes) -> str | None:
    """Chunk path of the first difference between two RW streams (``"Clump/GeometryList/..."``)."""
    def kids(buf, s, e):
        out = []
        o = s
        while o + 12 <= e:
            t, sz, v = struct.unpack_from("<III", buf, o)
            out.append((t, sz, v, o + 12, min(o + 12 + sz, e)))
            o += 12 + sz
        return out

    def rec(path, sa, ea, sb, eb):
        ka, kb = kids(a, sa, ea), kids(b, sb, eb)
        for x, y in zip(ka, kb):
            if a[x[3] - 12:x[4]] == b[y[3] - 12:y[4]]:
                continue
            nm = chunk_name(x[0])
            if x[0] != y[0]:
                return path + [f"{nm}!={chunk_name(y[0])}"]
            if x[0] in _CONT:
                r = rec(path + [nm], x[3], x[4], y[3], y[4])
                if r:
                    return r
            if x[2] != y[2]:
                return path + [nm + ":version"]
            return path + [nm + (":size" if x[1] != y[1] else ":data")]
        if len(ka) != len(kb):
            return path + ["chunk count"]
        return None

    if a == b:
        return None
    r = rec([], 0, len(a), 0, len(b))
    return "/".join(r) if r else "trailing bytes"
