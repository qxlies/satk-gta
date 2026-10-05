"""Model scene: frames, parts (atomics) and decoded meshes of a DFF (SPEC §4.4). Stdlib only.

:func:`build_scene` turns DFF bytes into a :class:`ModelScene` used by every exporter and by the
software renderer:

* frames with their local and model-space matrices (RW layout: ``right, up, at, pos``);
* one :class:`Part` per atomic (plus geometries no atomic uses), each with the model-space matrix
  of its frame;
* game-like preview rules (``Part.hidden``/extra parts), applied only by previews, never by exports:
  vehicle damage states ``*_dam`` and low LOD ``*_vlo`` are hidden, the single ``wheel`` atomic is
  copied onto every empty ``wheel_??_dummy`` (left side turned by 180° like the game);
* skinned peds (frame ``Pelvis``) are stored Y-up in the DFF bind pose: ``ModelScene.root`` turns
  them upright and facing +Y like vehicles (+90° about X, then 180° about Z) for previews *and* exports.

Matrices are 12-float tuples ``(rx, ry, rz, ux, uy, uz, ax, ay, az, px, py, pz)``: a point maps to
``x*right + y*up + z*at + pos``.

Example::

    scene = build_scene(dff_bytes, name="infernus", sec="cars")
    for part in scene.parts:
        mesh = scene.meshes[part.geom]
        print(part.name, part.hidden, len(mesh.tris) // 3)
"""

from __future__ import annotations

import math
import struct
from array import array
from dataclasses import dataclass, field

from ..formats.dff import F_SKIN, DffInfo, Material, Mesh, decode_geometries, scan_dff
from ..formats.rw import FormatError, iter_children

__all__ = [
    "IDENTITY", "Mat", "SceneFrame", "Part", "ModelScene", "build_scene",
    "mat_mul", "mat_apply", "mat_apply_dir", "mat_det", "rot_x", "rot_z",
    "smooth_normals", "is_vehicle",
]

Mat = tuple  # 12 floats

IDENTITY: Mat = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)

_STRUCT, _EXT = 0x01, 0x03
_FRAMELIST, _GEOMETRY, _CLUMP, _ATOMIC, _GEOMLIST, _UVANIMDICT = 0x0E, 0x0F, 0x10, 0x14, 0x1A, 0x2B
_FRAMENAME = 0x253F2FE
_FRAME = struct.Struct("<12fiI")

#: Wheel dummies of 4/6-wheel vehicles (the DFF holds one ``wheel`` atomic, the game clones it).
WHEEL_DUMMIES = ("wheel_rf_dummy", "wheel_lf_dummy", "wheel_rb_dummy", "wheel_lb_dummy",
                 "wheel_rm_dummy", "wheel_lm_dummy")


# ----------------------------------------------------------------------------- matrices
def mat_mul(a: Mat, b: Mat) -> Mat:
    """``a ∘ b``: apply ``b`` first, then ``a`` (``parent ∘ local`` = model matrix of a child)."""
    out = []
    for c in range(3):                      # columns of b's rotation, mapped through a's rotation
        x, y, z = b[3 * c], b[3 * c + 1], b[3 * c + 2]
        out += [x * a[0] + y * a[3] + z * a[6], x * a[1] + y * a[4] + z * a[7], x * a[2] + y * a[5] + z * a[8]]
    x, y, z = b[9], b[10], b[11]
    out += [x * a[0] + y * a[3] + z * a[6] + a[9], x * a[1] + y * a[4] + z * a[7] + a[10],
            x * a[2] + y * a[5] + z * a[8] + a[11]]
    return tuple(out)


def mat_apply(m: Mat, x: float, y: float, z: float) -> tuple[float, float, float]:
    return (x * m[0] + y * m[3] + z * m[6] + m[9], x * m[1] + y * m[4] + z * m[7] + m[10],
            x * m[2] + y * m[5] + z * m[8] + m[11])


def mat_apply_dir(m: Mat, x: float, y: float, z: float) -> tuple[float, float, float]:
    return (x * m[0] + y * m[3] + z * m[6], x * m[1] + y * m[4] + z * m[7], x * m[2] + y * m[5] + z * m[8])


def mat_det(m: Mat) -> float:
    return (m[0] * (m[4] * m[8] - m[5] * m[7]) - m[3] * (m[1] * m[8] - m[2] * m[7])
            + m[6] * (m[1] * m[5] - m[2] * m[4]))


def rot_x(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    c, s = round(c, 12), round(s, 12)
    return (1.0, 0.0, 0.0, 0.0, c, s, 0.0, -s, c, 0.0, 0.0, 0.0)


def rot_z(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    c, s = round(c, 12), round(s, 12)
    return (c, s, 0.0, -s, c, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)


# ----------------------------------------------------------------------------- data
@dataclass(frozen=True, slots=True)
class SceneFrame:
    """A frame (global index across clumps, like :class:`satk.formats.dff.Frame`)."""

    idx: int
    parent: int          # -1 for a root
    name: str | None
    local: Mat           # relative to the parent frame
    model: Mat           # model space (root of its clump = identity, like the engine's model info)


@dataclass(frozen=True, slots=True)
class Part:
    """One drawable: an atomic (or a geometry no atomic uses, or a preview-only copy).

    ``matrix`` is model space (without :attr:`ModelScene.root`); ``hidden`` parts are skipped by
    previews (exports keep them and mark them); ``kind`` is ``atomic`` | ``orphan`` | ``copy``.
    """

    idx: int
    name: str
    geom: int
    frame: int
    matrix: Mat
    hidden: bool = False
    kind: str = "atomic"


@dataclass
class ModelScene:
    """Everything an exporter or the renderer needs from one DFF."""

    name: str
    sec: str | None
    info: DffInfo
    frames: list[SceneFrame]
    meshes: list[Mesh]
    materials: list[list[Material]]          # per geometry, in material-slot order
    parts: list[Part]                        # exportable parts (atomics + orphans), DFF order
    preview_parts: list[Part]                # what a game-like preview draws
    root: Mat = IDENTITY                     # applied on top of every part (ped upright fix)
    notes: list[str] = field(default_factory=list)

    @property
    def tris(self) -> int:
        """Triangles of all geometries (after strip expansion), like ``dff.tris`` of the index."""
        return sum(len(m.tris) // 3 for m in self.meshes)

    @property
    def verts(self) -> int:
        return sum(len(m.positions) // 3 for m in self.meshes)

    def texture_names(self) -> list[str]:
        """Distinct material texture names (lower case, sorted)."""
        return sorted({m.texture.lower() for ms in self.materials for m in ms if m.texture})


def is_vehicle(sec: str | None, frames: list[SceneFrame]) -> bool:
    """``cars`` definition, or a DFF that has the vehicle frame layout (chassis/wheel dummies)."""
    if sec == "cars":
        return True
    names = {(f.name or "").lower() for f in frames}
    return "chassis_dummy" in names or any(w in names for w in WHEEL_DUMMIES)


# ----------------------------------------------------------------------------- parsing
def _cstr(buf, off: int, n: int) -> str:
    raw = bytes(buf[off:off + n]).split(b"\0", 1)[0]
    return raw.decode("latin-1").strip()


def _clumps(buf) -> list:
    """``[(frames, atomics)]`` per clump: frames ``(rot9, pos3, parent, name)``, atomics ``(frame, geom_local)``."""
    out = []
    n = len(buf)
    off = 0
    while off + 12 <= n:
        t, s, _v = struct.unpack_from("<III", buf, off)
        end = off + 12 + s
        if end > n:
            raise FormatError("dff", off, f"chunk 0x{t:X} overruns the buffer")
        if t == _UVANIMDICT:
            off = end
            continue
        if t != _CLUMP:
            break
        frames: list = []
        atomics: list = []
        ngeo = 0
        for k in iter_children(buf, off + 12, end):
            if k.type == _FRAMELIST:
                kids = list(iter_children(buf, k.data_off, k.end))
                if not kids or kids[0].type != _STRUCT or kids[0].size < 4:
                    raise FormatError("dff", k.data_off, "FrameList without Struct")
                st = kids[0]
                (nf,) = struct.unpack_from("<I", buf, st.data_off)
                if 4 + 56 * nf > st.size:
                    raise FormatError("dff", st.data_off, f"FrameList struct too short for {nf} frames")
                p = st.data_off + 4
                for _i in range(nf):
                    v = _FRAME.unpack_from(buf, p)
                    p += 56
                    frames.append([v[:9], v[9:12], v[12], None])
                fi = 0
                for e in kids[1:]:
                    if e.type != _EXT or fi >= nf:
                        continue
                    for x in iter_children(buf, e.data_off, e.end):
                        if x.type == _FRAMENAME:
                            frames[fi][3] = _cstr(buf, x.data_off, x.size)
                    fi += 1
            elif k.type == _GEOMLIST:
                ngeo += sum(1 for g in iter_children(buf, k.data_off, k.end) if g.type == _GEOMETRY)
            elif k.type == _ATOMIC:
                fr = gi = None
                for a in iter_children(buf, k.data_off, k.end):
                    if a.type == _STRUCT and fr is None and a.size >= 8:
                        fr, gi = struct.unpack_from("<II", buf, a.data_off)
                    elif a.type == _GEOMETRY:            # geometry stored inside the atomic
                        gi = ngeo
                        ngeo += 1
                if fr is not None:
                    atomics.append((fr, gi))
        out.append((frames, atomics, ngeo))
        off = end
    return out


def _local(rot, pos) -> Mat:
    return tuple(float(x) for x in rot) + tuple(float(x) for x in pos)


def _finite(m: Mat) -> bool:
    return all(math.isfinite(x) for x in m)


def build_scene(dff: bytes, *, name: str = "model", sec: str | None = None) -> ModelScene:
    """Decode a DFF into a :class:`ModelScene` (``FormatError`` on malformed data).

    Args:
        dff: the DFF bytes (sector padding after the RW data is fine).
        name: model name (used for part names of frameless geometries and in exports).
        sec: IDE section of the model (``cars`` enables the vehicle preview rules).
    """
    info = scan_dff(dff)
    meshes = decode_geometries(dff)
    try:
        clumps = _clumps(dff)
    except (struct.error, IndexError, ValueError) as e:
        if isinstance(e, FormatError):
            raise
        raise FormatError("dff", 0, f"malformed DFF: {type(e).__name__}: {e}") from None
    mats: list[list[Material]] = [[] for _ in meshes]
    for m in info.materials:
        if 0 <= m.geom < len(mats):
            mats[m.geom].append(m)

    frames: list[SceneFrame] = []
    parts: list[Part] = []
    used: set[int] = set()
    fbase = gbase = 0
    for cframes, catomics, ngeo in clumps:
        model: list[Mat] = []
        for i, (rot, pos, parent, fname) in enumerate(cframes):
            loc = _local(rot, pos)
            if not _finite(loc):
                loc = IDENTITY
            if parent < 0 or parent >= i:
                mm = IDENTITY                     # clump root: identity (the engine ignores it)
                par = -1 if parent < 0 or parent >= len(cframes) else fbase + parent
            else:
                mm = mat_mul(model[parent], loc)
                par = fbase + parent
            model.append(mm)
            frames.append(SceneFrame(fbase + i, par, fname, loc, mm))
        for fr, gi in catomics:
            if gi is None or not (0 <= gi < ngeo) or not (0 <= fr < len(cframes)):
                continue
            g = gbase + gi
            if g >= len(meshes):
                continue
            used.add(g)
            fname = cframes[fr][3] or f"{name}_{g}"
            parts.append(Part(len(parts), fname, g, fbase + fr, model[fr]))
        fbase += len(cframes)
        gbase += ngeo
    for g in range(len(meshes)):                  # geometries without an atomic: keep them (identity)
        if g not in used:
            parts.append(Part(len(parts), f"{name}_geom{g}", g, -1, IDENTITY, kind="orphan"))

    scene = ModelScene(name, sec, info, frames, meshes, mats, parts, list(parts))
    if info.flags & F_SKIN and any((f.name or "").strip().lower() == "pelvis" for f in frames):
        scene.root = mat_mul(rot_z(180.0), rot_x(90.0))   # ped bind pose is Y-up facing +Z: stand on +Z, face +Y
        scene.notes.append("ped: bind pose turned upright (+90 deg about X, 180 deg about Z)")
    if is_vehicle(sec, frames):
        _vehicle_preview(scene)
    return scene


def _vehicle_preview(scene: ModelScene) -> None:
    """Hide damage/VLO states and clone the wheel onto empty wheel dummies (preview only)."""
    out: list[Part] = []
    for p in scene.parts:
        n = p.name.lower()
        hidden = n.endswith("_dam") or n.endswith("_vlo")
        out.append(Part(p.idx, p.name, p.geom, p.frame, p.matrix, hidden, p.kind))
    if not any(not p.hidden for p in out):        # never hide everything
        out = list(scene.parts)
    by_idx = {f.idx: f for f in scene.frames}
    wheels = [p for p in out if p.name.lower() == "wheel" and p.frame >= 0]
    if wheels:
        tpl = wheels[0]
        tf = by_idx[tpl.frame]
        dummy = by_idx.get(tf.parent)
        dname = (dummy.name or "").lower() if dummy is not None else ""
        rel = tf.local if dname in WHEEL_DUMMIES else IDENTITY
        tpl_left = dname[5:7] == "_l"
        occupied = {by_idx[p.frame].parent for p in out if p.frame >= 0}
        n = len(scene.parts)
        for f in scene.frames:
            fname = (f.name or "").lower()
            if fname not in WHEEL_DUMMIES or f.idx in occupied:
                continue
            m = mat_mul(f.model, rel)
            if (fname[5:7] == "_l") != tpl_left:  # other side: wheel turned to face outwards
                m = mat_mul(m, rot_z(180.0))
            out.append(Part(n, f"wheel@{f.name}", tpl.geom, f.idx, m, False, "copy"))
            n += 1
    scene.preview_parts = out


# ----------------------------------------------------------------------------- normals
def smooth_normals(mesh: Mesh) -> array:
    """Per-vertex normals (area-weighted face normals), ``array('f')`` xyz; for meshes without normals."""
    pos = mesh.positions
    nv = len(pos) // 3
    acc = [0.0] * (3 * nv)
    t = mesh.tris
    for i in range(0, len(t) - 2, 3):
        a, b, c = t[i], t[i + 1], t[i + 2]
        ax, ay, az = pos[3 * a], pos[3 * a + 1], pos[3 * a + 2]
        ux, uy, uz = pos[3 * b] - ax, pos[3 * b + 1] - ay, pos[3 * b + 2] - az
        vx, vy, vz = pos[3 * c] - ax, pos[3 * c + 1] - ay, pos[3 * c + 2] - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for k in (a, b, c):
            acc[3 * k] += nx
            acc[3 * k + 1] += ny
            acc[3 * k + 2] += nz
    out = array("f", bytes(12 * nv))
    for k in range(nv):
        x, y, z = acc[3 * k], acc[3 * k + 1], acc[3 * k + 2]
        ln = math.sqrt(x * x + y * y + z * z)
        if ln > 1e-20:
            out[3 * k], out[3 * k + 1], out[3 * k + 2] = x / ln, y / ln, z / ln
        else:
            out[3 * k + 2] = 1.0
    return out
