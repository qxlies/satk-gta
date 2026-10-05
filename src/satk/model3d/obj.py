"""Wavefront OBJ + MTL writer for a :class:`~satk.model3d.mesh.ModelScene`. Stdlib only.

* one ``o`` per exported part (DFF atomic, frame transforms baked in), ``usemtl`` per material slot;
* coordinates are written **Y-up** like glTF (``x, z, -y``), so Blender's default OBJ import shows
  the model upright; ``vt`` uses OBJ's bottom-left origin (``v' = 1 - v``);
* textures are separate PNG files next to the ``.obj`` (``map_Kd``; ``map_d`` for alpha textures);
* vehicle key colours are replaced by carcols colours (``Kd``), translucent materials get ``d``.
"""

from __future__ import annotations

import math

from .gltf import ZUP_TO_YUP
from .mesh import ModelScene, mat_apply, mat_apply_dir, mat_mul, smooth_normals
from .png import encode_png
from .textures import MatInfo, decode

__all__ = ["build_obj"]


def _f(x: float) -> str:
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _safe(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return out or "x"


def build_obj(scene: ModelScene, mats: list[list[MatInfo]], stem: str) -> tuple[str, str, dict[str, bytes], dict]:
    """``(obj_text, mtl_text, {png_name: bytes}, stats)``; ``stem`` names the ``.mtl`` file."""
    lines = [f"# satk model3d: {scene.name}", "# Y-up (GTA x, y, z -> x, z, -y)", f"mtllib {stem}.mtl"]
    mtl = [f"# satk model3d: {scene.name}"]
    pngs: dict[str, bytes] = {}
    mat_names: dict[tuple, str] = {}
    missing: set[str] = set()

    def material(mi: MatInfo) -> str:
        alpha = mi.tex is not None and decode(mi.tex).alpha
        key = (mi.rgba, mi.tex.key if mi.tex else None, mi.tex_name, alpha, mi.slot)
        if key in mat_names:
            return mat_names[key]
        r, g, b, a = mi.rgba
        base = _safe(mi.tex_name) if mi.tex_name else f"mat_{r:02x}{g:02x}{b:02x}{a:02x}"
        name = base
        n = 1
        while name in mat_names.values():
            n += 1
            name = f"{base}_{n}"
        mat_names[key] = name
        mtl.extend(["", f"newmtl {name}", f"Kd {_f(r / 255)} {_f(g / 255)} {_f(b / 255)}", "Ka 0 0 0", "Ks 0 0 0", "illum 1"])
        if a < 255:
            mtl.append(f"d {_f(a / 255)}")
        if mi.tex is not None:
            fn = _safe(mi.tex.name) + ".png"
            if fn not in pngs:
                d = decode(mi.tex)
                if d.alpha:
                    pngs[fn] = encode_png(d.w, d.h, d.rgba, 4)
                else:
                    rgb = bytearray(d.w * d.h * 3)
                    rgb[0::3], rgb[1::3], rgb[2::3] = d.rgba[0::4], d.rgba[1::4], d.rgba[2::4]
                    pngs[fn] = encode_png(d.w, d.h, bytes(rgb), 3)
            mtl.append(f"map_Kd {fn}")
            if d_alpha(mi):
                mtl.append(f"map_d {fn}")
        elif mi.missing:
            mtl.append(f"# missing texture: {mi.tex_name}")
            missing.add(mi.tex_name)
        return name

    def d_alpha(mi: MatInfo) -> bool:
        return mi.tex is not None and decode(mi.tex).alpha

    base_v = base_vt = base_vn = 0
    tris_total = verts_total = 0
    for p in scene.parts:
        mesh = scene.meshes[p.geom]
        nv = len(mesh.positions) // 3
        nt = len(mesh.tris) // 3
        if nv == 0 or nt == 0:
            continue
        m = mat_mul(ZUP_TO_YUP, mat_mul(scene.root, p.matrix))
        lines.append(f"o {_safe(p.name)}")
        pos = mesh.positions
        for i in range(nv):
            x, y, z = mat_apply(m, pos[3 * i], pos[3 * i + 1], pos[3 * i + 2])
            lines.append(f"v {_f(x)} {_f(y)} {_f(z)}")
        uv = mesh.uv[0] if mesh.uv else None
        if uv is not None:
            for i in range(nv):
                u, v = uv[2 * i], uv[2 * i + 1]
                u, v = u if math.isfinite(u) else 0.0, v if math.isfinite(v) else 0.0
                lines.append(f"vt {_f(u)} {_f(1.0 - v)}")
        nrm = mesh.normals if mesh.normals is not None else smooth_normals(mesh)
        for i in range(nv):
            x, y, z = mat_apply_dir(m, nrm[3 * i], nrm[3 * i + 1], nrm[3 * i + 2])
            ln = math.sqrt(x * x + y * y + z * z) or 1.0
            lines.append(f"vn {_f(x / ln)} {_f(y / ln)} {_f(z / ln)}")
        slots = mats[p.geom] if p.geom < len(mats) else []
        cur = None
        tris, mids = mesh.tris, mesh.mat_ids
        order = sorted(range(nt), key=lambda t: mids[t])        # stable: grouped by material slot
        for t in order:
            mid = mids[t]
            if mid != cur:
                cur = mid
                if mid < len(slots):
                    lines.append(f"usemtl {material(slots[mid])}")
            a, b, c = (tris[3 * t + k] + 1 for k in range(3))
            if uv is not None:
                lines.append(f"f {a + base_v}/{a + base_vt}/{a + base_vn} {b + base_v}/{b + base_vt}/{b + base_vn} "
                             f"{c + base_v}/{c + base_vt}/{c + base_vn}")
            else:
                lines.append(f"f {a + base_v}//{a + base_vn} {b + base_v}//{b + base_vn} {c + base_v}//{c + base_vn}")
        base_v += nv
        base_vn += nv
        if uv is not None:
            base_vt += nv
        tris_total += nt
        verts_total += nv
    stats = {"tris": tris_total, "verts": verts_total, "materials": len(mat_names), "textures": len(pngs),
             "tex_missing": len(missing)}
    return "\n".join(lines) + "\n", "\n".join(mtl) + "\n", pngs, stats
