"""glTF 2.0 binary (``.glb``) writer for a :class:`~satk.model3d.mesh.ModelScene`. Stdlib only.

Layout of the file:

* node 0 ``satk_root`` converts GTA's Z-up to glTF's Y-up (and turns peds upright, see
  :attr:`ModelScene.root`); below it one node per DFF frame (local matrices) and one node per
  geometry no atomic uses;
* one mesh per geometry, one primitive per material slot that has triangles; attributes
  ``POSITION`` (with min/max), ``NORMAL`` (recomputed when the DFF has none), ``TEXCOORD_0/1``,
  ``COLOR_0`` = prelit (day) vertex colours, ``COLOR_1`` = night vertex colours;
* materials: base colour = material colour (vehicle key colours replaced by carcols colours),
  base colour texture = embedded PNG of mip 0; ``alphaMode`` ``MASK`` (cutoff 0.5) for textures
  with alpha, ``BLEND`` when the material colour is translucent; double sided;
* parts the game hides by default (``*_dam``, ``*_vlo``) keep their node with
  ``extras.satk_state`` = ``dam``/``vlo``.

Same input -> same bytes.
"""

from __future__ import annotations

import json
import math
import struct
from array import array

from .mesh import IDENTITY, Mat, ModelScene, mat_mul, smooth_normals
from .png import encode_png
from .textures import MatInfo, decode

__all__ = ["build_glb", "parse_glb", "ZUP_TO_YUP"]

#: (x, y, z) GTA -> (x, z, -y) glTF.
ZUP_TO_YUP: Mat = (1.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)

_FLOAT, _UBYTE, _USHORT, _UINT = 5126, 5121, 5123, 5125
_ARRAY_BUFFER, _ELEMENT_ARRAY_BUFFER = 34962, 34963
_WRAP = {1: 10497, 2: 33648, 3: 33071, 4: 33071}  # wrap, mirror, clamp, border->clamp


def _m16(m: Mat) -> list[float]:
    return [m[0], m[1], m[2], 0.0, m[3], m[4], m[5], 0.0, m[6], m[7], m[8], 0.0, m[9], m[10], m[11], 1.0]


def _r(x: float) -> float:
    """Round matrix/colour floats so the JSON stays short and stable."""
    v = round(float(x), 7)
    return 0.0 if v == 0 else v


class _Buf:
    def __init__(self) -> None:
        self.data = bytearray()
        self.views: list[dict] = []

    def add(self, raw: bytes, target: int | None = None, stride: int | None = None) -> int:
        while len(self.data) % 4:
            self.data.append(0)
        v: dict = {"buffer": 0, "byteOffset": len(self.data), "byteLength": len(raw)}
        if stride:
            v["byteStride"] = stride
        if target:
            v["target"] = target
        self.data += raw
        self.views.append(v)
        return len(self.views) - 1


def _le(a: array) -> bytes:
    import sys

    if sys.byteorder == "big":  # pragma: no cover - x86 only in practice
        a = array(a.typecode, a)
        a.byteswap()
    return a.tobytes()


def build_glb(scene: ModelScene, mats: list[list[MatInfo]], *, generator: str = "satk model3d") -> tuple[bytes, dict]:
    """``(glb_bytes, stats)``; stats = ``tris, verts, meshes, materials, textures, tex_missing``."""
    buf = _Buf()
    accessors: list[dict] = []
    meshes: list[dict] = []
    materials: list[dict] = []
    images: list[dict] = []
    textures: list[dict] = []
    samplers: list[dict] = []
    mat_index: dict[tuple, int] = {}
    tex_index: dict[tuple, int] = {}
    image_index: dict[tuple, int] = {}
    pixel_views: dict[tuple, int] = {}
    sampler_index: dict[tuple, int] = {}
    missing: set[str] = set()

    def accessor(view: int, ctype: int, count: int, kind: str, **extra) -> int:
        a = {"bufferView": view, "componentType": ctype, "count": count, "type": kind}
        a.update(extra)
        accessors.append(a)
        return len(accessors) - 1

    def texture(mi: MatInfo) -> int | None:
        h = mi.tex
        if h is None:
            return None
        k = (h.txd.lower(), h.name, h.binding_key)
        if k in tex_index:
            return tex_index[k]
        d = decode(h)
        pk = h.pixel_key
        ik = (h.txd.lower(), h.name, pk)
        if ik not in image_index:
            if pk not in pixel_views:
                if d.alpha:
                    png = encode_png(d.w, d.h, d.rgba, 4)
                else:
                    rgb = bytearray(d.w * d.h * 3)
                    rgb[0::3], rgb[1::3], rgb[2::3] = d.rgba[0::4], d.rgba[1::4], d.rgba[2::4]
                    png = encode_png(d.w, d.h, bytes(rgb), 3)
                pixel_views[pk] = buf.add(png)
            # Keep named images/materials (e.g. carplate), even when PNG payloads are shared.
            image_index[ik] = len(images)
            images.append({"name": h.name, "bufferView": pixel_views[pk], "mimeType": "image/png"})
        sk = (_WRAP.get(d.uaddr, 10497), _WRAP.get(d.vaddr, 10497))
        if sk not in sampler_index:
            samplers.append({"magFilter": 9729, "minFilter": 9987, "wrapS": sk[0], "wrapT": sk[1]})
            sampler_index[sk] = len(samplers) - 1
        textures.append({"name": h.name, "sampler": sampler_index[sk], "source": image_index[ik]})
        tex_index[k] = len(textures) - 1
        return tex_index[k]

    def material(mi: MatInfo) -> int:
        ti = texture(mi)
        alpha = mi.tex is not None and decode(mi.tex).alpha
        key = (mi.rgba, ti, mi.tex_name, alpha, mi.slot)
        if key in mat_index:
            return mat_index[key]
        r, g, b, a = mi.rgba
        pbr: dict = {"baseColorFactor": [_r(r / 255), _r(g / 255), _r(b / 255), _r(a / 255)],
                     "metallicFactor": 0.0, "roughnessFactor": 1.0}
        if ti is not None:
            pbr["baseColorTexture"] = {"index": ti, "texCoord": 0}
        m: dict = {"name": mi.tex_name or f"mat_{r:02x}{g:02x}{b:02x}{a:02x}", "pbrMetallicRoughness": pbr,
                   "doubleSided": True}
        if a < 255:
            m["alphaMode"] = "BLEND"
        elif alpha:
            m["alphaMode"] = "MASK"
            m["alphaCutoff"] = 0.5
        extras: dict = {}
        if mi.missing:
            extras["satk_missing_texture"] = mi.tex_name
            missing.add(mi.tex_name)
        if mi.slot is not None:
            extras["satk_color_slot"] = mi.slot
            extras["satk_key_rgba"] = f"{mi.key_rgba:08x}"
        if extras:
            m["extras"] = extras
        materials.append(m)
        mat_index[key] = len(materials) - 1
        return mat_index[key]

    mesh_of_geom: dict[int, int] = {}
    total_tris = total_verts = 0
    for gi, mesh in enumerate(scene.meshes):
        nv = len(mesh.positions) // 3
        nt = len(mesh.tris) // 3
        if nv == 0 or nt == 0:
            continue
        pos = mesh.positions
        xs, ys, zs = pos[0::3], pos[1::3], pos[2::3]
        attrs = {"POSITION": accessor(buf.add(_le(pos), _ARRAY_BUFFER), _FLOAT, nv, "VEC3",
                                      min=[_r(min(xs)), _r(min(ys)), _r(min(zs))],
                                      max=[_r(max(xs)), _r(max(ys)), _r(max(zs))])}
        nrm = mesh.normals if mesh.normals is not None else smooth_normals(mesh)
        nrm = _unit(nrm)
        attrs["NORMAL"] = accessor(buf.add(_le(nrm), _ARRAY_BUFFER), _FLOAT, nv, "VEC3")
        for k, uv in enumerate(mesh.uv[:2]):
            clean = array("f", (v if math.isfinite(v) else 0.0 for v in uv))
            attrs[f"TEXCOORD_{k}"] = accessor(buf.add(_le(clean), _ARRAY_BUFFER), _FLOAT, nv, "VEC2")
        if mesh.prelit is not None:
            attrs["COLOR_0"] = accessor(buf.add(bytes(mesh.prelit), _ARRAY_BUFFER), _UBYTE, nv, "VEC4", normalized=True)
        if mesh.night is not None:
            attrs["COLOR_1"] = accessor(buf.add(bytes(mesh.night), _ARRAY_BUFFER), _UBYTE, nv, "VEC4", normalized=True)
        groups: dict[int, array] = {}
        tris, mids = mesh.tris, mesh.mat_ids
        for t in range(nt):
            groups.setdefault(mids[t], array("I")).extend(tris[3 * t:3 * t + 3])
        prims = []
        slots = mats[gi] if gi < len(mats) else []
        for mid in sorted(groups):
            ix = groups[mid]
            if nv <= 65535:
                raw, ctype = _le(array("H", ix)), _USHORT
            else:
                raw, ctype = _le(ix), _UINT
            p: dict = {"attributes": attrs, "indices": accessor(buf.add(raw, _ELEMENT_ARRAY_BUFFER), ctype, len(ix), "SCALAR"),
                       "mode": 4}
            if mid < len(slots):
                p["material"] = material(slots[mid])
            prims.append(p)
        meshes.append({"name": f"{scene.name}_geom{gi}", "primitives": prims})
        mesh_of_geom[gi] = len(meshes) - 1
        total_tris += nt
        total_verts += nv

    # ---- nodes: root, frames, parts
    nodes: list[dict] = [{"name": "satk_root", "matrix": [_r(x) for x in _m16(mat_mul(ZUP_TO_YUP, scene.root))],
                          "children": []}]
    state = {}
    for p in scene.preview_parts:
        if p.hidden and p.kind != "copy":
            n = p.name.lower()
            state[p.idx] = "dam" if n.endswith("_dam") else "vlo"
    frame_node: dict[int, int] = {}
    for f in scene.frames:
        node: dict = {"name": f.name or f"frame{f.idx}"}
        m = f.local if f.parent >= 0 else IDENTITY
        if m != IDENTITY:
            node["matrix"] = [_r(x) for x in _m16(m)]
        nodes.append(node)
        frame_node[f.idx] = len(nodes) - 1
    for f in scene.frames:
        parent = frame_node.get(f.parent) if f.parent >= 0 else 0
        nodes[parent if parent is not None else 0].setdefault("children", []).append(frame_node[f.idx])
    for p in scene.parts:
        mi = mesh_of_geom.get(p.geom)
        if mi is None:
            continue
        if p.frame >= 0 and "mesh" not in nodes[frame_node[p.frame]]:
            target = nodes[frame_node[p.frame]]
        else:
            target = {"name": p.name}
            nodes.append(target)
            parent = frame_node[p.frame] if p.frame >= 0 else 0
            nodes[parent].setdefault("children", []).append(len(nodes) - 1)
        target["mesh"] = mi
        if p.idx in state:
            target["extras"] = {"satk_state": state[p.idx]}

    gltf: dict = {"asset": {"version": "2.0", "generator": generator}, "scene": 0,
                  "scenes": [{"name": scene.name, "nodes": [0]}], "nodes": nodes}
    for k, v in (("meshes", meshes), ("materials", materials), ("textures", textures), ("images", images),
                 ("samplers", samplers), ("accessors", accessors), ("bufferViews", buf.views)):
        if v:
            gltf[k] = v
    if buf.data:
        while len(buf.data) % 4:
            buf.data.append(0)
        gltf["buffers"] = [{"byteLength": len(buf.data)}]
    js = json.dumps(gltf, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js
    if buf.data:
        body += struct.pack("<II", len(buf.data), 0x004E4942) + bytes(buf.data)
    glb = struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body
    stats = {"tris": total_tris, "verts": total_verts, "meshes": len(meshes), "materials": len(materials),
             "textures": len(textures), "tex_missing": len(missing)}
    return glb, stats


def _unit(n: array) -> array:
    """Normalize normals (glTF requires unit length; some DFF normals are not)."""
    out = array("f", n)
    for i in range(0, len(out) - 2, 3):
        x, y, z = out[i], out[i + 1], out[i + 2]
        ln = math.sqrt(x * x + y * y + z * z)
        if ln > 1e-12 and abs(ln - 1.0) > 1e-6:
            out[i], out[i + 1], out[i + 2] = x / ln, y / ln, z / ln
        elif ln <= 1e-12:
            out[i], out[i + 1], out[i + 2] = 0.0, 0.0, 1.0
    return out


def parse_glb(data: bytes) -> tuple[dict, bytes]:
    """``(json, bin)`` of a GLB (``ValueError`` when malformed) — for tests and sanity checks."""
    if len(data) < 20:
        raise ValueError("GLB too short")
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise ValueError(f"bad GLB header {magic!r} v{version} len {length}/{len(data)}")
    jlen, jtype = struct.unpack_from("<II", data, 12)
    if jtype != 0x4E4F534A:
        raise ValueError("first chunk is not JSON")
    doc = json.loads(data[20:20 + jlen].decode("utf-8"))
    off = 20 + jlen
    binary = b""
    if off + 8 <= len(data):
        blen, btype = struct.unpack_from("<II", data, off)
        if btype != 0x004E4942:
            raise ValueError("second chunk is not BIN")
        binary = data[off + 8:off + 8 + blen]
    return doc, binary
