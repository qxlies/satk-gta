"""Collision models: exact codec, JSON form and a writer (``satk.rw.col``). Stdlib only.

:func:`decode_model` / :func:`encode_model` handle one record (``COLL`` v1, ``COL2``, ``COL3``, ``COL4``);
:func:`split_models` / :func:`join_models` a whole ``.col`` (many records + trailing padding). The
encoder always writes the layout Rockstar's files use (measured on all 10 381 vanilla records):

* the fixed header, then the sections in the order spheres, boxes, lines, vertices, face groups
  (+ ``u32`` count), faces, shadow vertices, shadow faces - no gaps, offset ``0`` for an empty section;
* compressed vertices (``int16 / 128``) followed by 2 bytes of padding when their size is not a multiple
  of 4 (Rockstar's padding is often non-zero garbage: kept in ``pad``);
* ``flags``: ``0x02`` spheres/boxes/faces present, ``0x08`` face groups, ``0x10`` shadow mesh;
* face groups only above 80 faces, contiguous ranges, at most 50 faces each (see :func:`face_groups`).

The decoder keeps every byte the layout does not explain (name garbage after the NUL, padding, unknown
data at the end of a record) so that ``encode_model(decode_model(rec)) == rec`` for vanilla files.

JSON (:func:`model_to_json` / :func:`model_from_json`) is the agent-facing form of ``satk col export`` and
``satk col write``; see ``docs/en/rw.md`` for the schema. Missing bounds, flags and face groups are
computed; ``raw`` (export only) carries the garbage bytes so that the JSON round trip is exact too.
"""

from __future__ import annotations

import functools
import math
import struct
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ColError", "ColModel", "decode_model", "encode_model", "split_models", "join_models",
           "model_to_json", "model_from_json", "face_groups", "canonical_flags", "compute_bounds",
           "FOURCC", "FG_MIN_FACES", "FG_MAX_GROUP"]

FOURCC = {1: b"COLL", 2: b"COL2", 3: b"COL3", 4: b"COL4"}
_VERSION = {v: k for k, v in FOURCC.items()}
FG_MIN_FACES = 81          # vanilla: face groups exactly when a model has more than 80 faces
FG_MAX_GROUP = 50          # vanilla: at most 50 faces per group (99.9 %)
_V2 = struct.Struct("<10f3HBBI6I")


class ColError(ValueError):
    """Malformed collision data or an impossible JSON model."""


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise ColError(msg)


Surface = tuple[int, int, int, int]       # material, flag, brightness, light


@dataclass
class ColModel:
    """One collision model. Coordinates are floats; v2+ vertices are stored as ``int16`` triples."""

    version: int = 3
    name: str = ""
    name_raw: bytes | None = None          # the 22-byte field when it holds bytes after the NUL
    model_id: int = 0
    bmin: tuple = (0.0, 0.0, 0.0)
    bmax: tuple = (0.0, 0.0, 0.0)
    center: tuple = (0.0, 0.0, 0.0)
    radius: float = 0.0
    spheres: list = field(default_factory=list)      # (center3, radius, surface)
    boxes: list = field(default_factory=list)        # (min3, max3, surface)
    lines: bytes = b""                               # raw line records (none in vanilla)
    num_lines: int = 0
    vertices: list = field(default_factory=list)     # v2+: int16 triples; v1: float triples
    faces: list = field(default_factory=list)        # v2+: (a, b, c, material, light); v1: (a, b, c, surface)
    groups: list = field(default_factory=list)       # (min3, max3, start, end)
    shadow_vertices: list = field(default_factory=list)
    shadow_faces: list = field(default_factory=list)
    flags: int = 0
    pad_byte: int = 0
    col4: int = 0
    pad: dict = field(default_factory=dict)          # section -> bytes after its records
    tail: bytes = b""

    @property
    def fourcc(self) -> bytes:
        return FOURCC[self.version]


# ============================================================================ helpers


def _name_field(m: ColModel) -> bytes:
    raw = m.name_raw
    if raw is not None and len(raw) == 22 and raw.split(b"\0", 1)[0].decode("latin-1") == m.name:
        return raw
    b = m.name.encode("latin-1")
    _need(len(b) <= 21, f"collision name {m.name!r} is longer than 21 characters")
    return b.ljust(22, b"\0")


def _surf(raw: bytes, at: int) -> Surface:
    return raw[at], raw[at + 1], raw[at + 2], raw[at + 3]


# ============================================================================ decode


def split_models(buf: bytes) -> tuple[list[bytes], bytes]:
    """Records of a ``.col`` blob and the bytes after the last one (IMG padding)."""
    out = []
    off = 0
    n = len(buf)
    while off + 8 <= n and bytes(buf[off:off + 4]) in _VERSION:
        (size,) = struct.unpack_from("<I", buf, off + 4)
        _need(size >= 24 and off + 8 + size <= n, f"record at {off} of {size} bytes overruns the buffer")
        out.append(bytes(buf[off:off + 8 + size]))
        off += 8 + size
    _need(bool(out), "not a collision file (no COLL/COL2/COL3/COL4 record)")
    return out, bytes(buf[off:])


def join_models(models: list[ColModel], tail: bytes = b"") -> bytes:
    return b"".join(encode_model(m) for m in models) + tail


def decode_model(rec: bytes) -> ColModel:
    """One record (``fourcc`` .. end) -> :class:`ColModel`."""
    _need(len(rec) >= 32, "collision record shorter than its header")
    version = _VERSION.get(bytes(rec[:4]))
    _need(version is not None, f"unknown collision fourcc {bytes(rec[:4])!r}")
    (size,) = struct.unpack_from("<I", rec, 4)
    _need(8 + size == len(rec), "record size field does not match the record")
    raw_name = bytes(rec[8:30])
    name = raw_name.split(b"\0", 1)[0].decode("latin-1")
    (mid,) = struct.unpack_from("<H", rec, 30)
    m = ColModel(version=version, name=name, model_id=mid,
                 name_raw=raw_name if raw_name != name.encode("latin-1").ljust(22, b"\0") else None)
    if version == 1:
        _decode_v1(rec, m)
    else:
        _decode_v234(rec, m)
    return m


def _decode_v1(rec: bytes, m: ColModel) -> None:
    n = len(rec)
    _need(n >= 72, "COLL bounds truncated")
    r, cx, cy, cz, x0, y0, z0, x1, y1, z1 = struct.unpack_from("<10f", rec, 32)
    m.radius, m.center, m.bmin, m.bmax = r, (cx, cy, cz), (x0, y0, z0), (x1, y1, z1)
    o = 72

    def count(stride: int, what: str) -> int:
        nonlocal o
        _need(o + 4 <= n, f"COLL {what} count truncated")
        (c,) = struct.unpack_from("<I", rec, o)
        o += 4
        _need(o + c * stride <= n, f"COLL {c} {what} overrun the record")
        return c

    for _ in range(count(20, "spheres")):
        rr, x, y, z = struct.unpack_from("<4f", rec, o)
        m.spheres.append(((x, y, z), rr, _surf(rec, o + 16)))
        o += 20
    nl = count(24, "lines")
    m.num_lines, m.lines = nl, rec[o:o + 24 * nl]
    o += 24 * nl
    for _ in range(count(28, "boxes")):
        v = struct.unpack_from("<6f", rec, o)
        m.boxes.append((v[:3], v[3:], _surf(rec, o + 24)))
        o += 28
    for _ in range(count(12, "vertices")):
        m.vertices.append(struct.unpack_from("<3f", rec, o))
        o += 12
    for _ in range(count(16, "faces")):
        a, b, c = struct.unpack_from("<3I", rec, o)
        m.faces.append((a, b, c, _surf(rec, o + 12)))
        o += 16
    m.tail = rec[o:]


def _decode_v234(rec: bytes, m: ColModel) -> None:
    n = len(rec)
    v = m.version
    hdr = 32 + 76 + (12 if v >= 3 else 0) + (4 if v >= 4 else 0)
    _need(n >= hdr, f"COL{v} header truncated")
    f = _V2.unpack_from(rec, 32)
    m.bmin, m.bmax, m.center, m.radius = f[0:3], f[3:6], f[6:9], f[9]
    ns, nb, nf, nl, m.pad_byte, m.flags = f[10:16]
    o_sph, o_box, o_lin, o_vrt, o_face, o_pln = f[16:22]
    nsh = o_sv = o_sf = 0
    if v >= 3:
        nsh, o_sv, o_sf = struct.unpack_from("<3I", rec, 108)
    if v >= 4:
        (m.col4,) = struct.unpack_from("<I", rec, 120)
    _need(o_pln == 0, "COL triangle planes are not supported")
    base = 4                                           # offsets count from fourcc + 4

    ng = 0
    fg_start = 0
    if nf and m.flags & 0x08:
        _need(o_face >= 4 and base + o_face <= n, "face-group count outside the record")
        (ng,) = struct.unpack_from("<I", rec, base + o_face - 4)
        fg_start = base + o_face - 4 - 28 * ng
        _need(fg_start >= hdr, "face groups overlap the header")
    starts = {}
    if ns:
        starts["spheres"] = base + o_sph
    if nb:
        starts["boxes"] = base + o_box
    if nl:
        starts["lines"] = base + o_lin
    if o_vrt:
        starts["vertices"] = base + o_vrt
    if ng:
        starts["groups"] = fg_start
    if nf:
        starts["faces"] = base + o_face
    if nsh:
        starts["shadow_vertices"] = base + o_sv
        starts["shadow_faces"] = base + o_sf
    order = sorted(starts, key=starts.get)
    for s in order:
        _need(hdr <= starts[s] <= n, f"{s} offset outside the record")
    ends = {s: (starts[order[i + 1]] if i + 1 < len(order) else n) for i, s in enumerate(order)}
    m.tail = b""
    if not order:
        m.tail = rec[hdr:]
    elif starts[order[0]] != hdr:
        raise ColError("gap between the header and the first section")

    def records(sec: str, count: int, stride: int) -> int:
        a, e = starts[sec], ends[sec]
        _need(a + count * stride <= e, f"{count} {sec} overrun the next section")
        rest = rec[a + count * stride:e]
        if rest:
            m.pad[sec] = rest
        return a

    if ns:
        a = records("spheres", ns, 20)
        for i in range(ns):
            x, y, z, rr = struct.unpack_from("<4f", rec, a + 20 * i)
            m.spheres.append(((x, y, z), rr, _surf(rec, a + 20 * i + 16)))
    if nb:
        a = records("boxes", nb, 28)
        for i in range(nb):
            q = struct.unpack_from("<6f", rec, a + 28 * i)
            m.boxes.append((q[:3], q[3:], _surf(rec, a + 28 * i + 24)))
    if nl:
        m.num_lines = nl
        m.lines = rec[starts["lines"]:ends["lines"]]
    if "vertices" in starts:
        a, e = starts["vertices"], ends["vertices"]
        nv = (e - a) // 6
        m.vertices = [struct.unpack_from("<3h", rec, a + 6 * i) for i in range(nv)]
        if a + 6 * nv < e:
            m.pad["vertices"] = rec[a + 6 * nv:e]
    if ng:
        a = fg_start
        for i in range(ng):
            q = struct.unpack_from("<6f2H", rec, a + 28 * i)
            m.groups.append((q[:3], q[3:6], q[6], q[7]))
        e = ends["groups"]
        _need(a + 28 * ng + 4 == e, "face groups are not followed by their count")
    if nf:
        a = records("faces", nf, 8)
        m.faces = [struct.unpack_from("<3H2B", rec, a + 8 * i) for i in range(nf)]
    if nsh:
        a, e = starts["shadow_vertices"], ends["shadow_vertices"]
        nv = (e - a) // 6
        m.shadow_vertices = [struct.unpack_from("<3h", rec, a + 6 * i) for i in range(nv)]
        if a + 6 * nv < e:
            m.pad["shadow_vertices"] = rec[a + 6 * nv:e]
        a = records("shadow_faces", nsh, 8)
        m.shadow_faces = [struct.unpack_from("<3H2B", rec, a + 8 * i) for i in range(nsh)]
    for sec, b in list(m.pad.items()):                # trailing bytes of the last section = record tail
        if order and sec == order[-1] and sec not in ("vertices", "shadow_vertices"):
            m.tail = b
            del m.pad[sec]


# ============================================================================ encode


def canonical_flags(m: ColModel) -> int:
    f = 0
    if m.spheres or m.boxes or m.faces:
        f |= 0x02
    if m.groups:
        f |= 0x08
    if m.version >= 3 and m.shadow_faces:
        f |= 0x10
    return f


def _vpad(n: int, given: bytes | None) -> bytes:
    want = (-6 * n) % 4
    if given is not None and len(given) == want:
        return given
    return b"\0" * want


def encode_model(m: ColModel) -> bytes:
    """:class:`ColModel` -> one record in Rockstar's layout."""
    if m.version == 1:
        return _encode_v1(m)
    v = m.version
    _need(v in (2, 3, 4), f"unknown collision version {v}")
    _need(len(m.spheres) <= 0xFFFF and len(m.boxes) <= 0xFFFF and len(m.faces) <= 0xFFFF,
          "more than 65535 spheres, boxes or faces")
    hdr = 32 + 76 + (12 if v >= 3 else 0) + (4 if v >= 4 else 0)
    sec: list[bytes] = []
    pos = hdr

    def add(data: bytes) -> int:
        nonlocal pos
        at = pos
        sec.append(data)
        pos += len(data)
        return at - 4

    o_sph = o_box = o_lin = o_vrt = o_face = o_sv = o_sf = 0
    if m.spheres:
        o_sph = add(b"".join(struct.pack("<4f", *c, r) + bytes(s) for c, r, s in m.spheres)
                    + m.pad.get("spheres", b""))
    if m.boxes:
        o_box = add(b"".join(struct.pack("<6f", *lo, *hi) + bytes(s) for lo, hi, s in m.boxes)
                    + m.pad.get("boxes", b""))
    if m.num_lines:
        o_lin = add(m.lines)
    if m.vertices:
        o_vrt = add(b"".join(struct.pack("<3h", *p) for p in m.vertices)
                    + _vpad(len(m.vertices), m.pad.get("vertices")))
    if m.faces:
        if m.groups:
            add(b"".join(struct.pack("<6f2H", *lo, *hi, s, e) for lo, hi, s, e in m.groups)
                + struct.pack("<I", len(m.groups)))
        o_face = add(b"".join(struct.pack("<3H2B", *f) for f in m.faces) + m.pad.get("faces", b""))
    if v >= 3 and m.shadow_faces:
        o_sv = add(b"".join(struct.pack("<3h", *p) for p in m.shadow_vertices)
                   + _vpad(len(m.shadow_vertices), m.pad.get("shadow_vertices")))
        o_sf = add(b"".join(struct.pack("<3H2B", *f) for f in m.shadow_faces) + m.pad.get("shadow_faces", b""))
    head = struct.pack("<3f3f3ff", *m.bmin, *m.bmax, *m.center, m.radius)
    head += struct.pack("<3HBBI6I", len(m.spheres), len(m.boxes), len(m.faces), m.num_lines, m.pad_byte, m.flags,
                        o_sph, o_box, o_lin, o_vrt, o_face, 0)
    if v >= 3:
        head += struct.pack("<3I", len(m.shadow_faces) if m.shadow_faces else 0, o_sv, o_sf)
    if v >= 4:
        head += struct.pack("<I", m.col4)
    body = _name_field(m) + struct.pack("<H", m.model_id & 0xFFFF) + head + b"".join(sec) + m.tail
    return m.fourcc + struct.pack("<I", len(body)) + body


def _encode_v1(m: ColModel) -> bytes:
    out = [struct.pack("<f3f3f3f", m.radius, *m.center, *m.bmin, *m.bmax)]
    out.append(struct.pack("<I", len(m.spheres)))
    out += [struct.pack("<4f", r, *c) + bytes(s) for c, r, s in m.spheres]
    out.append(struct.pack("<I", m.num_lines) + m.lines)
    out.append(struct.pack("<I", len(m.boxes)))
    out += [struct.pack("<6f", *lo, *hi) + bytes(s) for lo, hi, s in m.boxes]
    out.append(struct.pack("<I", len(m.vertices)))
    out += [struct.pack("<3f", *p) for p in m.vertices]
    out.append(struct.pack("<I", len(m.faces)))
    out += [struct.pack("<3I", a, b, c) + bytes(s) for a, b, c, s in m.faces]
    body = _name_field(m) + struct.pack("<H", m.model_id & 0xFFFF) + b"".join(out) + m.tail
    return b"COLL" + struct.pack("<I", len(body)) + body


# ============================================================================ authoring helpers


def _f(v: int) -> float:
    return v / 128.0


def _verts_f(m: ColModel, shadow: bool = False) -> list[tuple[float, float, float]]:
    vs = m.shadow_vertices if shadow else m.vertices
    if m.version == 1:
        return [tuple(p) for p in vs]
    return [(_f(p[0]), _f(p[1]), _f(p[2])) for p in vs]


def compute_bounds(m: ColModel) -> None:
    """AABB of all spheres, boxes and face vertices (shadow mesh if nothing else), centre, radius."""
    pts: list[tuple[float, float, float]] = []
    verts = _verts_f(m)
    used = sorted({i for f in m.faces for i in f[:3]})
    pts += [verts[i] for i in used if i < len(verts)]
    for lo, hi, _s in m.boxes:
        pts += [lo, hi]
    for c, r, _s in m.spheres:
        pts += [(c[0] - r, c[1] - r, c[2] - r), (c[0] + r, c[1] + r, c[2] + r)]
    if not pts and m.shadow_faces:
        sv = _verts_f(m, True)
        pts = [sv[i] for i in sorted({i for f in m.shadow_faces for i in f[:3]}) if i < len(sv)]
    if not pts:
        m.bmin = m.bmax = m.center = (0.0, 0.0, 0.0)
        m.radius = 0.0
        return
    lo = tuple(min(p[i] for p in pts) for i in range(3))
    hi = tuple(max(p[i] for p in pts) for i in range(3))
    c = tuple((lo[i] + hi[i]) / 2 for i in range(3))
    r = 0.0
    for i in used:
        if i < len(verts):
            r = max(r, math.dist(c, verts[i]))
    for blo, bhi, _s in m.boxes:
        for x in (blo[0], bhi[0]):
            for y in (blo[1], bhi[1]):
                for z in (blo[2], bhi[2]):
                    r = max(r, math.dist(c, (x, y, z)))
    for sc, sr, _s in m.spheres:
        r = max(r, math.dist(c, sc) + sr)
    if not r:
        r = math.dist(c, hi)
    m.bmin, m.bmax, m.center, m.radius = lo, hi, c, r


def face_groups(m: ColModel, max_group: int = FG_MAX_GROUP) -> None:
    """Build face groups like Rockstar's files (> 80 faces): recursive median split along the longest
    axis of the face centroids until a group has at most ``max_group`` faces. Faces are reordered so
    that every group is a contiguous range; group boxes are the bounds of their faces' vertices."""
    if len(m.faces) < FG_MIN_FACES:
        m.groups = []
        return
    verts = _verts_f(m)
    cent = []
    for i, f in enumerate(m.faces):
        p = [verts[f[k]] for k in range(3)]
        cent.append(((p[0][0] + p[1][0] + p[2][0]) / 3, (p[0][1] + p[1][1] + p[2][1]) / 3,
                     (p[0][2] + p[1][2] + p[2][2]) / 3, i))
    leaves: list[list] = []

    def split(items: list) -> None:
        if len(items) <= max_group:
            leaves.append(items)
            return
        ext = [max(c[k] for c in items) - min(c[k] for c in items) for k in range(3)]
        ax = ext.index(max(ext))
        items = sorted(items, key=lambda c: (c[ax], c[3]))
        mid = len(items) // 2
        split(items[:mid])
        split(items[mid:])

    split(cent)
    faces, groups = [], []
    for leaf in leaves:
        start = len(faces)
        idx = [c[3] for c in leaf]
        faces += [m.faces[i] for i in idx]
        pts = [verts[v] for i in idx for v in m.faces[i][:3]]
        lo = tuple(min(p[k] for p in pts) for k in range(3))
        hi = tuple(max(p[k] for p in pts) for k in range(3))
        groups.append((lo, hi, start, len(faces) - 1))
    m.faces, m.groups = faces, groups


# ============================================================================ JSON


def model_to_json(m: ColModel, *, raw: bool = True) -> dict[str, Any]:
    """JSON-ready dict. ``raw=False`` drops the garbage-byte section (``raw``)."""
    d: dict[str, Any] = {"name": m.name, "version": m.version, "model_id": m.model_id,
                         "bounds": {"min": list(m.bmin), "max": list(m.bmax), "center": list(m.center),
                                    "radius": m.radius},
                         "flags": m.flags}
    if m.spheres:
        d["spheres"] = [{"center": list(c), "radius": r, "surface": list(s)} for c, r, s in m.spheres]
    if m.boxes:
        d["boxes"] = [{"min": list(lo), "max": list(hi), "surface": list(s)} for lo, hi, s in m.boxes]
    if m.vertices:
        d["vertices"] = [list(p) for p in _verts_f(m)]
    if m.faces:
        d["faces"] = [[a, b, c, *s] for a, b, c, s in m.faces] if m.version == 1 else [list(f) for f in m.faces]
    if m.version >= 2 and m.faces:
        d["face_groups"] = [{"min": list(lo), "max": list(hi), "start": s, "end": e} for lo, hi, s, e in m.groups] \
            if m.groups else "none"
    if m.shadow_faces or m.shadow_vertices:
        d["shadow"] = {"vertices": [list(p) for p in _verts_f(m, True)], "faces": [list(f) for f in m.shadow_faces]}
    if raw:
        rw: dict[str, Any] = {}
        if m.name_raw is not None:
            rw["name"] = m.name_raw.hex()
        if m.pad:
            rw["pad"] = {k: v.hex() for k, v in sorted(m.pad.items())}
        if m.tail:
            rw["tail"] = m.tail.hex()
        if m.num_lines or m.lines:
            rw["lines"] = {"count": m.num_lines, "data": m.lines.hex()}
        if m.pad_byte:
            rw["pad_byte"] = m.pad_byte
        if m.col4:
            rw["col4"] = m.col4
        if rw:
            d["raw"] = rw
    return d


def _vec(v: Any, what: str) -> tuple[float, float, float]:
    _need(isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                              for x in v), f"{what}: expected [x, y, z]")
    return float(v[0]), float(v[1]), float(v[2])


def _q(x: float, what: str) -> int:
    q = round(x * 128.0)
    _need(-32768 <= q <= 32767, f"{what}: coordinate {x} outside the COL2/3 range (+-256 m)")
    return q


#: Face lighting when a model gives neither ``light`` nor ``prelight``: day 15 (the dominant day value of vanilla
#: collision faces), night 3 (about the median night value of vanilla faces lit 15 by day).
FALLBACK_LIGHT = 0x3F
#: Linear fit ``nibble = a * prelit brightness (0..255) + b`` of vanilla face lighting against the prelit
#: colours of the render mesh above it; ``data/colgen/tex_surface.json`` (``satk col derive``) overrides it.
_LIGHT_FIT = {"day": (0.0809, 2.385), "night": (0.0889, 0.8793)}


@functools.lru_cache(maxsize=1)
def _light_fit() -> dict:
    try:
        from ..core.resources import read_json

        fit = read_json("colgen", "tex_surface.json").get("lighting") or {}
        return {k: tuple(fit.get(k, v)) for k, v in _LIGHT_FIT.items()}
    except Exception:  # noqa: BLE001 - optional data of another package; the built-in fit is the fallback
        return dict(_LIGHT_FIT)


def _brightness(x: Any, what: str) -> float:
    if isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 255:
        return float(x)
    if isinstance(x, (list, tuple)) and len(x) in (3, 4) and all(isinstance(c, (int, float)) for c in x[:3]):
        r, g, b = (float(c) for c in x[:3])
        return 0.299 * r + 0.587 * g + 0.114 * b
    raise ColError(f"{what}: expected a brightness 0..255 or [r, g, b]")


def face_light(day: float | None, night: float | None = None) -> int:
    """COL face lighting byte (low nibble day, high nibble night, 0..15 each) from prelit brightness 0..255."""
    fit = _light_fit()

    def nib(v: float, k: str) -> int:
        a, b = fit[k]
        return int(min(15, max(0, round(a * v + b))))
    d = nib(day, "day") if day is not None else FALLBACK_LIGHT & 15
    n = nib(night, "night") if night is not None else FALLBACK_LIGHT >> 4
    return d | (n << 4)


def _model_light(d: dict, who: str) -> tuple[int | None, str]:
    """Default face lighting of a model from ``"light"`` or ``"prelight"``: ``(byte, source)``."""
    lt = d.get("light")
    if lt is not None:
        if isinstance(lt, dict):
            day, night = lt.get("day", FALLBACK_LIGHT & 15), lt.get("night", FALLBACK_LIGHT >> 4)
            _need(all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 15 for x in (day, night)),
                  f"{who}: light day/night must be 0..15")
            return day | (night << 4), "light"
        return _u8(lt, f"{who} light"), "light"
    pl = d.get("prelight")
    if pl is not None:
        if isinstance(pl, dict):
            day = _brightness(pl["day"], f"{who} prelight day") if "day" in pl else None
            night = _brightness(pl["night"], f"{who} prelight night") if "night" in pl else None
        else:
            day, night = _brightness(pl, f"{who} prelight"), None
        return face_light(day, night), "prelight"
    return None, ""


def model_from_json(d: dict, *, surfaces: dict[str, int] | None = None, default_version: int = 3,
                    warn: list[str] | None = None) -> ColModel:
    """Build a :class:`ColModel` from the JSON form (see the module doc). ``surfaces`` maps upper-case
    surface names to material IDs (for ``"surface": "TARMAC"``).

    Collision faces (COL2+) without their own ``light`` take the model's ``light`` (a byte, or
    ``{"day": 0..15, "night": 0..15}``), else the lighting fitted from the model's ``prelight`` (brightness
    0..255 or ``[r, g, b]``, or ``{"day": ..., "night": ...}``), else :data:`FALLBACK_LIGHT` with a
    ``FACE_LIGHT_DEFAULT`` warning (light 0 would make peds dark and switch car headlights on)."""
    _need(isinstance(d, dict), "a collision model must be a JSON object")
    name = d.get("name")
    _need(isinstance(name, str) and name and name.isascii() and len(name) <= 21,
          f"model name must be 1..21 ASCII characters, got {name!r}")
    v = d.get("version", default_version)
    _need(v in (1, 2, 3, 4), f"{name}: version must be 1, 2, 3 or 4")
    m = ColModel(version=v, name=name, model_id=int(d.get("model_id", 0)) & 0xFFFF)
    who = f"model {name!r}"

    def surf(s: Any, what: str) -> Surface:
        if s is None:
            return (0, 0, 0, 0)
        if isinstance(s, bool):
            raise ColError(f"{what}: bad surface {s!r}")
        if isinstance(s, int):
            return (_u8(s, what), 0, 0, 0)
        if isinstance(s, str):
            return (_surface_id(s, surfaces, what), 0, 0, 0)
        if isinstance(s, dict):
            mat = s.get("material", 0)
            mat = _surface_id(mat, surfaces, what) if isinstance(mat, str) else _u8(mat, what)
            return (mat, _u8(s.get("flag", 0), what), _u8(s.get("brightness", 0), what), _u8(s.get("light", 0), what))
        if isinstance(s, (list, tuple)) and 1 <= len(s) <= 4:
            vals = list(s) + [0] * (4 - len(s))
            mat = _surface_id(vals[0], surfaces, what) if isinstance(vals[0], str) else _u8(vals[0], what)
            return (mat, _u8(vals[1], what), _u8(vals[2], what), _u8(vals[3], what))
        raise ColError(f"{what}: bad surface {s!r}")

    for i, s in enumerate(d.get("spheres") or []):
        r = s.get("radius")
        _need(isinstance(r, (int, float)) and r >= 0, f"{who} sphere {i}: radius must be >= 0")
        m.spheres.append((_vec(s.get("center"), f"{who} sphere {i} center"), float(r),
                          surf(s.get("surface"), f"{who} sphere {i}")))
    for i, b in enumerate(d.get("boxes") or []):
        lo, hi = _vec(b.get("min"), f"{who} box {i} min"), _vec(b.get("max"), f"{who} box {i} max")
        m.boxes.append((tuple(min(a, c) for a, c in zip(lo, hi)), tuple(max(a, c) for a, c in zip(lo, hi)),
                        surf(b.get("surface"), f"{who} box {i}")))

    def verts(raw_list: Any, what: str) -> list:
        out = []
        for i, p in enumerate(raw_list or []):
            x = _vec(p, f"{who} {what} {i}")
            out.append(x if v == 1 else tuple(_q(c, f"{who} {what} {i}") for c in x))
        return out

    implicit: list[int] = []                 # indices of collision faces without their own light

    def faces(raw_list: Any, nv: int, what: str, v1: bool, track: bool = False) -> list:
        out = []
        for i, f in enumerate(raw_list or []):
            has_light = False
            if isinstance(f, dict):
                vv = f.get("v") or f.get("vertices")
                _need(isinstance(vv, (list, tuple)) and len(vv) == 3, f"{who} {what} {i}: need 3 vertex indices")
                s = surf(f.get("surface", f.get("material")), f"{who} {what} {i}")
                sf = f.get("surface", f.get("material"))
                has_light = "light" in f or (isinstance(sf, dict) and "light" in sf) or \
                    (isinstance(sf, (list, tuple)) and len(sf) >= 4)
                if "light" in f:
                    s = (s[0], s[1], s[2], _u8(f["light"], f"{who} {what} {i}"))
                a, b, c = vv
            else:
                _need(isinstance(f, (list, tuple)) and len(f) >= 3, f"{who} {what} {i}: expected [a, b, c, material, light]")
                a, b, c = f[:3]
                rest = list(f[3:])
                if v1:
                    s = surf(rest or None, f"{who} {what} {i}")
                else:
                    mat = rest[0] if rest else 0
                    mat = _surface_id(mat, surfaces, f"{who} {what} {i}") if isinstance(mat, str) else _u8(mat, what)
                    s = (mat, 0, 0, _u8(rest[1], what) if len(rest) > 1 else 0)
                    has_light = len(rest) > 1
            for x in (a, b, c):
                _need(isinstance(x, int) and not isinstance(x, bool) and 0 <= x < nv,
                      f"{who} {what} {i}: vertex index {x!r} outside 0..{nv - 1}")
            out.append((a, b, c, s) if v1 else (a, b, c, s[0], s[3]))
            if track and not v1 and not has_light:
                implicit.append(len(out) - 1)
        return out

    m.vertices = verts(d.get("vertices"), "vertex")
    _need(len(m.vertices) <= 65536 or v == 1, f"{who}: more than 65536 vertices")
    m.faces = faces(d.get("faces"), len(m.vertices), "face", v == 1, track=True)
    if implicit:
        light, src = _model_light(d, who)
        if light is None:
            light = FALLBACK_LIGHT
            if warn is not None:
                warn.append(f"FACE_LIGHT_DEFAULT: {name}: {len(implicit)} face(s) without light set to "
                            f"0x{light:02X} (day {light & 15}, night {light >> 4}); give \"light\" or \"prelight\" "
                            "for the model (0 makes peds dark and switches car headlights on)")
        for k in implicit:
            a, b, c, mat, _l = m.faces[k]
            m.faces[k] = (a, b, c, mat, light)
    sh = d.get("shadow") or {}
    if sh:
        _need(v >= 3, f"{who}: a shadow mesh needs version 3")
        m.shadow_vertices = verts(sh.get("vertices"), "shadow vertex")
        m.shadow_faces = faces(sh.get("faces"), len(m.shadow_vertices), "shadow face", False)

    fg = d.get("face_groups", "auto")
    if v >= 2 and m.faces:
        if fg == "auto":
            face_groups(m)
        elif fg == "none" or fg is None:
            m.groups = []
        else:
            _need(isinstance(fg, list), f"{who}: face_groups must be 'auto', 'none' or a list")
            for i, g in enumerate(fg):
                s, e = g.get("start"), g.get("end")
                _need(isinstance(s, int) and isinstance(e, int) and 0 <= s <= e < len(m.faces),
                      f"{who} face group {i}: start/end outside 0..{len(m.faces) - 1}")
                m.groups.append((_vec(g.get("min"), f"{who} face group {i} min"),
                                 _vec(g.get("max"), f"{who} face group {i} max"), s, e))
    b = d.get("bounds")
    if b:
        m.bmin, m.bmax = _vec(b.get("min"), f"{who} bounds min"), _vec(b.get("max"), f"{who} bounds max")
        m.center = _vec(b.get("center"), f"{who} bounds center") if b.get("center") is not None else \
            tuple((m.bmin[i] + m.bmax[i]) / 2 for i in range(3))
        r = b.get("radius")
        m.radius = float(r) if isinstance(r, (int, float)) else max(
            math.dist(m.center, m.bmin), math.dist(m.center, m.bmax))
    else:
        compute_bounds(m)
    m.flags = int(d["flags"]) if isinstance(d.get("flags"), int) else canonical_flags(m)
    rw = d.get("raw") or {}
    if rw:
        if isinstance(rw.get("name"), str):
            m.name_raw = bytes.fromhex(rw["name"])
        for k, h in (rw.get("pad") or {}).items():
            m.pad[k] = bytes.fromhex(h)
        if isinstance(rw.get("tail"), str):
            m.tail = bytes.fromhex(rw["tail"])
        if isinstance(rw.get("lines"), dict):
            m.num_lines = int(rw["lines"].get("count", 0))
            m.lines = bytes.fromhex(rw["lines"].get("data", ""))
        m.pad_byte = int(rw.get("pad_byte", 0)) & 0xFF
        m.col4 = int(rw.get("col4", 0))
    if warn is not None and m.faces and not m.groups and len(m.faces) >= FG_MIN_FACES and v >= 2:
        warn.append(f"NO_FACE_GROUPS: {name} has {len(m.faces)} faces and no face groups (slower collision)")
    return m


def _u8(x: Any, what: str) -> int:
    _need(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 255, f"{what}: expected 0..255, got {x!r}")
    return x


def _surface_id(x: Any, surfaces: dict[str, int] | None, what: str) -> int:
    if isinstance(x, int) and not isinstance(x, bool):
        return _u8(x, what)
    _need(isinstance(x, str), f"{what}: bad surface {x!r}")
    if x.strip().isdigit():
        return _u8(int(x), what)
    _need(surfaces is not None, f"{what}: surface names need the rwfury table (vendor/rwfury); use the numeric ID")
    key = x.strip().upper().replace(" ", "_").replace("-", "_")
    _need(key in surfaces, f"{what}: unknown surface {x!r}")
    return surfaces[key]
