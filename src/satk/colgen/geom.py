"""Render mesh of a DFF in model space, as numpy arrays (``satk.colgen.geom``).

:func:`render_mesh` decodes a DFF through :func:`satk.model3d.mesh.build_scene` and keeps what a
collision generator needs: positions in model space (frame matrices applied), triangles, the texture
and part of every triangle and the prelit day/night brightness of every triangle (the source of the
COL face lighting byte). Parts that never collide are dropped:

* damage and low-LOD states (``*_dam``, ``*_vlo``) of every model;
* wheels of vehicles (the game builds wheel collision from the wheel dummies, not from the COL);
* geometries no atomic uses (the game never draws them) when the model has atomics;
* parts or textures matching an ``exclude`` glob (``*leaf*``, ``*_lod``).

:func:`weld` snaps positions to the COL grid (1/128 m) and merges equal vertices, then drops
degenerate and duplicate triangles: what is left is exactly what a COL2/3 file can store.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from ._np import np

from ..formats.rw import FormatError
from ..model3d.mesh import build_scene, is_vehicle, mat_mul

__all__ = ["RenderMesh", "render_mesh", "weld", "tri_areas", "tri_normals", "GRID"]

#: COL2/3 vertex resolution (int16 / 128).
GRID = 1.0 / 128.0
#: Part name suffixes the collision never covers.
_SKIP_SUFFIX = ("_dam", "_vlo")


@dataclass
class RenderMesh:
    """Triangles of the colliding parts of a model, in model space.

    ``tex`` / ``part`` index :attr:`textures` / :attr:`parts` (``-1`` = no texture); ``day`` / ``night``
    are the mean prelit RGB brightness (0..255) of each triangle's vertices, ``-1`` when the
    geometry has no prelit / night colours.
    """

    name: str
    sec: str | None
    vehicle: bool
    P: np.ndarray                      # (n, 3) float64
    T: np.ndarray                      # (m, 3) int64
    tex: np.ndarray                    # (m,) int32
    part: np.ndarray                   # (m,) int32
    day: np.ndarray                    # (m,) float32
    night: np.ndarray                  # (m,) float32
    textures: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    part_boxes: dict[str, tuple] = field(default_factory=dict)   # vehicle "_ok" parts: name -> (min3, max3)
    skipped: list[str] = field(default_factory=list)

    @property
    def tris(self) -> int:
        return int(len(self.T))

    def texture_of(self, i: int) -> str | None:
        t = int(self.tex[i])
        return self.textures[t] if t >= 0 else None

    def subset(self, keep: np.ndarray) -> "RenderMesh":
        """The triangles where ``keep`` is true (vertices are kept as they are)."""
        return RenderMesh(self.name, self.sec, self.vehicle, self.P, self.T[keep], self.tex[keep], self.part[keep],
                          self.day[keep], self.night[keep], self.textures, self.parts, self.part_boxes,
                          list(self.skipped))


def _match(name: str, pats: list[str]) -> bool:
    n = name.lower()
    return any(fnmatch.fnmatchcase(n, p) for p in pats)


def _brightness(rgba, nv: int) -> np.ndarray | None:
    if rgba is None or len(rgba) < 4 * nv:
        return None
    a = np.frombuffer(bytes(rgba), dtype=np.uint8)[:4 * nv].reshape(nv, 4).astype(np.float32)
    return a[:, :3].mean(axis=1)


def render_mesh(dff: bytes, name: str, sec: str | None = None, *, exclude: list[str] | None = None,
                wheels: bool = False, preview: bool = False) -> RenderMesh:
    """Colliding triangles of a DFF (see the module doc).

    Args:
        dff: DFF bytes.
        name: model name (part names of frameless geometries).
        sec: IDE section (``cars`` = vehicle rules).
        exclude: case-insensitive globs over part names and texture names to leave out.
        wheels: keep vehicle wheels (for a shadow mesh that reaches the ground).
        preview: take the game-like preview parts (the single wheel copied onto every wheel dummy).
    """
    scene = build_scene(dff, name=name, sec=sec)
    pats = [p.lower() for p in (exclude or [])]
    vehicle = is_vehicle(sec, scene.frames)
    texid: dict[str, int] = {}
    textures: list[str] = []
    parts: list[str] = []
    P: list[np.ndarray] = []
    T: list[np.ndarray] = []
    TX: list[np.ndarray] = []
    PT: list[np.ndarray] = []
    DY: list[np.ndarray] = []
    NT: list[np.ndarray] = []
    skipped: list[str] = []
    boxes: dict[str, tuple] = {}
    has_atomic = any(p.kind == "atomic" for p in scene.parts)
    base = 0
    for p in (scene.preview_parts if preview else scene.parts):
        if p.hidden:
            continue
        pname = p.name or f"{name}_{p.geom}"
        low = pname.lower()
        why = None
        if p.kind == "orphan" and has_atomic:
            why = "unused geometry"
        elif low.endswith(_SKIP_SUFFIX):
            why = "damage/LOD state"
        elif vehicle and not wheels and low.startswith("wheel"):
            why = "wheel"
        elif pats and _match(low, pats):
            why = "excluded"
        if why:
            skipped.append(f"{pname} ({why})")
            continue
        mesh = scene.meshes[p.geom]
        nv = len(mesh.positions) // 3
        nt = len(mesh.tris) // 3
        if not nv or not nt:
            continue
        pos = np.frombuffer(mesh.positions.tobytes(), dtype=np.float32).reshape(nv, 3).astype(np.float64)
        m = mat_mul(scene.root, p.matrix)
        rot = np.array(m[:9], dtype=np.float64).reshape(3, 3)       # rows = right, up, at
        pos = pos @ rot + np.array(m[9:12], dtype=np.float64)
        tri = np.frombuffer(mesh.tris.tobytes(), dtype=np.uint32).reshape(nt, 3).astype(np.int64)
        mats = np.frombuffer(mesh.mat_ids.tobytes(), dtype=np.uint16).astype(np.int64)
        mlist = scene.materials[p.geom]
        slot_tex = []
        for mm in mlist:
            t = (mm.texture or "").lower()
            if t and t not in texid:
                texid[t] = len(textures)
                textures.append(t)
            slot_tex.append(texid[t] if t else -1)
        lut = np.array(slot_tex + [-1], dtype=np.int32)
        tx = lut[np.clip(mats, 0, len(slot_tex))] if slot_tex else np.full(nt, -1, dtype=np.int32)
        keep = np.ones(nt, dtype=bool)
        if pats:
            for k, t in enumerate(textures):
                if _match(t, pats):
                    keep &= tx != k
            if not keep.any():
                skipped.append(f"{pname} (excluded textures)")
                continue
        if np.linalg.det(rot) < 0:                                  # mirrored frame: keep outward winding
            tri = tri[:, ::-1]
        day = _brightness(mesh.prelit, nv)
        night = _brightness(mesh.night, nv)
        pi = len(parts)
        parts.append(pname)
        if vehicle and low.endswith("_ok"):
            boxes[low[:-3]] = (tuple(pos.min(axis=0)), tuple(pos.max(axis=0)))
        P.append(pos)
        T.append(tri[keep] + base)
        TX.append(tx[keep])
        PT.append(np.full(int(keep.sum()), pi, dtype=np.int32))
        DY.append(day[tri[keep]].mean(axis=1) if day is not None else np.full(int(keep.sum()), -1.0, np.float32))
        NT.append(night[tri[keep]].mean(axis=1) if night is not None else np.full(int(keep.sum()), -1.0, np.float32))
        base += nv
    if not T:
        raise FormatError("dff", 0, f"{name}: no colliding geometry"
                          + (f" (skipped: {', '.join(skipped[:6])})" if skipped else ""))
    return RenderMesh(name, sec, vehicle, np.concatenate(P), np.concatenate(T), np.concatenate(TX).astype(np.int32),
                      np.concatenate(PT), np.concatenate(DY).astype(np.float32),
                      np.concatenate(NT).astype(np.float32), textures, parts, boxes, skipped)


def tri_normals(P: np.ndarray, T: np.ndarray) -> np.ndarray:
    """Un-normalised face normals (length = 2 x area)."""
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    return np.cross(b - a, c - a)


def tri_areas(P: np.ndarray, T: np.ndarray) -> np.ndarray:
    return 0.5 * np.linalg.norm(tri_normals(P, T), axis=1)


def weld(P: np.ndarray, T: np.ndarray, *, grid: float = GRID) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Snap to ``grid``, merge equal vertices, drop degenerate and duplicate triangles.

    Returns ``(P2, T2, src)``: ``src[i]`` = index of the input triangle that became ``T2[i]``.
    """
    q = np.round(P / grid).astype(np.int64)
    uq, inv = np.unique(q, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    T2 = inv[T]
    ok = (T2[:, 0] != T2[:, 1]) & (T2[:, 1] != T2[:, 2]) & (T2[:, 0] != T2[:, 2])
    P2 = uq.astype(np.float64) * grid
    idx = np.nonzero(ok)[0]
    if len(idx):
        area = tri_areas(P2, T2[idx])
        idx = idx[area > 1e-9]
    if len(idx):                                     # duplicates (same three vertices, any order)
        key = np.sort(T2[idx], axis=1)
        _u, first = np.unique(key, axis=0, return_index=True)
        idx = idx[np.sort(first)]
    T3 = T2[idx]
    used = np.unique(T3) if len(T3) else np.zeros(0, dtype=np.int64)
    remap = np.full(len(P2), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return P2[used], remap[T3] if len(T3) else T3.reshape(0, 3), idx
