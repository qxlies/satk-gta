"""``asset.anatomy``: a compact map of one model - frames, parts, materials and their roles, COL, TXD.

The frame tree carries local and model-space positions (rotations only where they are not identity),
parts carry triangles, UV sets and geometry flags, materials are grouped by role (paint keys, lamp keys,
glass, plain) with their MatFX/reflection/specular values, the collision is summarised by piece code and
face light, and the own TXD rows list size, format and levels. ``--md`` renders the same as Markdown
(a car with 51 frames stays under 4 KB).

Names and numbers only: no vertices, no pixels.
"""

from __future__ import annotations

from collections import Counter

from ..formats.dff import model_matrices
from .check import _mat_key, semantics
from .subject import Subject, col_summary

__all__ = ["anatomy", "to_markdown", "material_role"]

_GEO_FLAGS = ((0x08, "prelit"), (0x10, "normals"), (0x20, "light"), (0x40, "modulate"))


def material_role(m) -> str:
    """``paint1``..``paint4``, ``lamp:<which>``, ``glass`` (alpha < 255), ``plain`` or ``untextured``."""
    sem = semantics()
    key = _mat_key(m)
    if key in sem["paint_keys"]:
        return sem["paint_keys"][key]
    if key in sem["lamp_keys"]:
        return "lamp:" + sem["lamp_keys"][key].replace(" ", "_")
    if (m.rgba & 0xFF) < 255:
        return "glass"
    return "plain" if m.texture else "untextured"


def _r2(v) -> float:
    return round(float(v), 2) + 0.0


def _rot(m12) -> str | None:
    if all(abs(a - b) < 1e-4 for a, b in zip(m12[:9], (1, 0, 0, 0, 1, 0, 0, 0, 1))):
        return None
    return ",".join(f"{x:.2f}" for x in m12[:9])


def _fx(e: dict) -> str:
    out = []
    if "env" in e:
        out.append(f"env {e['env'].get('tex') or '-'} {e['env'].get('coef', 0):g}")
    if e.get("reflection", {}).get("intensity"):
        out.append(f"refl {e['reflection']['intensity']:g}")
    if e.get("specular", {}).get("level"):
        out.append(f"spec {e['specular']['level']:g}")
    if "bump" in e:
        out.append("bump")
    if "dual" in e:
        out.append("dual")
    if "uv_transform" in e:
        out.append("uvanim")
    return " ".join(out)


def anatomy(subj: Subject) -> dict:
    """The anatomy of one subject as a JSON-ready dict."""
    sc = subj.scene
    info = sc.info
    mm = model_matrices(info.frames)
    atom_of: dict[int, list[str]] = {}
    for p in sc.parts:
        if p.frame >= 0 and p.kind == "atomic":
            atom_of.setdefault(p.frame, []).append(str(p.geom))
    frames = []
    for f in info.frames:
        lm = f.matrix if len(f.matrix) == 12 else (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
        row = {"i": f.idx, "name": f.name or "", "parent": f.parent,
               "local": [_r2(x) for x in lm[9:12]], "model": [_r2(x) for x in mm[f.idx][9:12]]}
        r = _rot(lm)
        if r:
            row["rot"] = r
        if f.idx in atom_of:
            row["geom"] = ",".join(atom_of[f.idx])
        frames.append(row)
    parts = []
    for p in sc.parts:
        g = info.geoms[p.geom] if p.geom < len(info.geoms) else None
        mesh = sc.meshes[p.geom]
        flags = [n for b, n in _GEO_FLAGS if g is not None and g.rw_flags & b]
        if mesh.night is not None:
            flags.append("night")
        row = {"name": p.name, "geom": p.geom, "tris": len(mesh.tris) // 3, "verts": len(mesh.positions) // 3,
               "mats": len(sc.materials[p.geom]), "uv": len(mesh.uv), "flags": " ".join(flags)}
        if p.kind != "atomic":
            row["kind"] = p.kind
        if g is not None and g.bbox:
            b = g.bbox
            row["size"] = [_r2(b[3] - b[0]), _r2(b[4] - b[1]), _r2(b[5] - b[2])]
        parts.append(row)
    groups: Counter = Counter()
    tris_of: Counter = Counter()
    for gi, mats in enumerate(sc.materials):
        counts = Counter(sc.meshes[gi].mat_ids)
        for i, m in enumerate(mats):
            a = m.rgba & 0xFF
            key = (material_role(m), (m.texture or "").lower(), a if a < 255 else 255, _fx(m.effects))
            groups[key] += 1
            tris_of[key] += counts.get(i, 0)
    mats = [{"role": k[0], "tex": k[1] or "-", "alpha": k[2], "fx": k[3], "n": n, "tris": tris_of[k]}
            for k, n in sorted(groups.items(), key=lambda kv: (-tris_of[kv[0]], kv[0]))]
    out = {"target": subj.label, "name": subj.name, "rw": f"0x{info.rw_version:X}", "clumps": info.clumps,
           "frames_n": len(info.frames), "atomics": info.atomics, "geoms": len(info.geoms),
           "tris": sum(len(m.tris) // 3 for m in sc.meshes), "frames": frames, "parts": parts, "materials": mats}
    if info.effects:
        out["fx2d"] = dict(Counter(e.type_name for e in info.effects))
    cs = col_summary(subj.col)
    if cs:
        cs["via"] = subj.col_via
        pieces = _col_pieces(subj.col)
        if pieces:
            cs["sphere_pieces"] = pieces
        out["col"] = cs
    if subj.tex:
        out["txd"] = [[n, f"{t['w']}x{t['h']}", t.get("fmt"), t.get("levels"), t.get("via", "own")]
                      for n, t in sorted(subj.tex.items())]
    if subj.ide:
        out["ide"] = {k: v for k, v in subj.ide.items() if v is not None}
    return out


def _col_pieces(rec: bytes | None) -> dict | None:
    if not rec:
        return None
    from ..rw.col import decode_model

    try:
        m = decode_model(rec)
    except Exception:  # noqa: BLE001
        return None
    c = Counter(int(s[2][1]) for s in m.spheres if len(s) >= 3 and len(s[2]) >= 2)
    return {str(k): v for k, v in sorted(c.items())} or None


def to_markdown(a: dict) -> str:
    """Markdown of :func:`anatomy` (compact: positions to 0.01 m, one line per frame and material group)."""
    L = [f"# {a['name']} ({a['target']})",
         f"rw {a['rw']}, {a['clumps']} clump(s), {a['frames_n']} frames, {a['atomics']} atomics, "
         f"{a['geoms']} geometries, {a['tris']} triangles"]
    if a.get("ide"):
        L.append("IDE: " + ", ".join(f"{k} {v}" for k, v in a["ide"].items()))
    by_geom = {str(p["geom"]): p for p in a["parts"]}
    short = {"prelit": "P", "normals": "N", "light": "L", "modulate": "M", "night": "X"}
    L += ["", "## Frames (model x,y,z; part: tris t, materials m, UV sets, flags P prelit N normals L light "
          "M modulate X night)", "|#|name|parent|x,y,z|part|", "|-|-|-|-|-|"]
    for f in a["frames"]:
        xyz = ",".join(f"{v:g}" for v in f["model"])
        rot = " r" if f.get("rot") else ""
        part = ""
        for g in f.get("geom", "").split(","):
            p = by_geom.get(g)
            if p is not None:
                fl = "".join(short.get(x, "") for x in p["flags"].split())
                part += f"{p['tris']}t {p['mats']}m uv{p['uv']} {fl}".rstrip() + " "
        L.append(f"|{f['i']}|{f['name']}|{f['parent']}|{xyz}{rot}|{part.strip()}|")
    orphans = [p for p in a["parts"] if p.get("kind")]
    if orphans:
        L.append("Geometries without a frame: " + ", ".join(f"{p['name']} {p['tris']}t" for p in orphans))
    L += ["", "## Materials (by role)", "|role|texture|alpha|fx|n|tris|", "|-|-|-|-|-|-|"]
    for m in a["materials"]:
        L.append(f"|{m['role']}|{m['tex']}|{m['alpha']}|{m['fx']}|{m['n']}|{m['tris']}|")
    if a.get("col"):
        c = a["col"]
        s = f"COL{c.get('version')} ({c.get('via')}): {c['spheres']} spheres, {c['boxes']} boxes, {c['faces']} faces, " \
            f"{c['shadow_faces']} shadow faces"
        if "light_dominant" in c:
            s += f", face light {c['light_dominant']}"
        if c.get("sphere_pieces"):
            s += "; sphere pieces " + " ".join(f"{k}:{v}" for k, v in c["sphere_pieces"].items())
        L += ["", s]
    if a.get("txd"):
        L += ["", "TXD: " + "; ".join(f"{r[0]} {r[1]} {r[2]} L{r[3]}" for r in a["txd"] if r[4] == "own")]
    if a.get("fx2d"):
        L += ["", "2DFX: " + ", ".join(f"{k} {v}" for k, v in a["fx2d"].items())]
    return "\n".join(L) + "\n"
