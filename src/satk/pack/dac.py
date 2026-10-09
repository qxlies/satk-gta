"""The DAC (derived-asset cache) file: per-vertex and per-model data the render track derives once per source asset.

The renderer needs data the stock files do not carry or carry badly: vertex **normals** for prelit building
geometry (68 % of the vanilla geometries have none), the **night colour** stream as a ready vertex stream for the
GPU day/night blend, **tangents**, the **light list** of a model and derived **mip chains**. A DAC file stores such
sections for one source asset. It is content addressed: the cache key is the SHA-256 of the source bytes, the
derivation recipe id and the derivation parameters, so a changed source or recipe never finds a stale file.
DAC files are **local only** (derived from the user's own files, never redistributed; a pack that carries one is
marked ``local_only``).

Layout (little-endian; the tables are in :mod:`.layout` and printed in ``docs/en/dac.md``)::

    header (128 B) | section table (64 B per section) | payloads (each 16-byte aligned)

Streams are indexed by the vertex index of the geometry in the source file, so ``count`` equals the geometry's
vertex count and a shader can bind a stream next to the geometry's own vertex buffer.

Encodings: a normal is a unit vector stored as an octahedral map in two snorm16 (4 bytes, ``D3DDECLTYPE_SHORT2N``;
:func:`oct_encode` / :func:`oct_decode` define it exactly); a tangent is x, y, z in snorm8 plus a bitangent sign
(+-127); colours are the r, g, b, a bytes in the order of the RW vertex colour stream; a light is the 32-byte
``LIGHT_V1`` record.

:func:`derive_dff` makes a DAC from a DFF: normals (computed seam-aware for geometries that have none, or for all),
night colours (copied from the night-colour extension), tangents (from UV set 0) and the lights (2dEffect type 0).
Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import layout as L
from .saepak import Finding

__all__ = ["DacError", "DacSection", "Dac", "cache_key", "params_json", "encode_dac", "decode_dac", "read_dac",
           "verify_dac", "oct_encode", "oct_decode", "pack_normals", "unpack_normals", "pack_tangents",
           "unpack_tangents", "pack_lights", "unpack_lights", "derive_dff", "dac_cache_path", "describe_dac"]

_LIGHT = struct.Struct("<3f4B3fI")
assert _LIGHT.size == 32


class DacError(ValueError):
    """The bytes are not a readable DAC. ``code`` is a finding code (``BAD_MAGIC``, ``DAC_CRC``, ...)."""

    def __init__(self, code: str, msg: str, where: str = ""):
        self.code = code
        self.msg = msg
        self.where = where
        super().__init__(f"{code}: {msg}" + (f" ({where})" if where else ""))


@dataclass
class DacSection:
    kind: int
    index: int
    count: int
    format: int
    data: bytes
    stream: int = 0
    stride: int = 0
    flags: int = 0
    aux: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass
class Dac:
    source_sha256: bytes
    derive_id: int = L.DERIVE_DFF_V1
    source_kind: int = L.SRC_DFF
    params: dict = field(default_factory=dict)
    sections: list[DacSection] = field(default_factory=list)

    @property
    def key(self) -> bytes:
        return cache_key(self.source_sha256, self.derive_id, self.params)

    def find(self, kind: int, index: int, stream: int = 0) -> DacSection | None:
        for s in self.sections:
            if s.kind == kind and s.index == index and s.stream == stream:
                return s
        return None


# --------------------------------------------------------------------------- cache key and paths


def params_json(params: dict) -> bytes:
    """Canonical JSON of the derivation parameters (sorted keys, no spaces, ASCII)."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def cache_key(source_sha256: bytes, derive_id: int, params: dict) -> bytes:
    """``SHA-256("DACKEY1\\n" || source_sha256 || u32 derive_id || canonical params JSON)``."""
    h = hashlib.sha256()
    h.update(b"DACKEY1\n")
    h.update(source_sha256)
    h.update(struct.pack("<I", derive_id))
    h.update(params_json(params))
    return h.digest()


def dac_cache_path(root: Path, key: bytes) -> Path:
    """``<root>/<hh>/<key hex>.dac``: the content-addressed place of a DAC file."""
    hx = key.hex()
    return Path(root) / hx[:2] / f"{hx}.dac"


# --------------------------------------------------------------------------- element encodings


def _sgn(v: float) -> float:
    return -1.0 if v < 0 else 1.0


def oct_encode(x: float, y: float, z: float) -> tuple[int, int]:
    """Unit vector -> octahedral map -> two snorm16 (``round(v * 32767)``). A zero vector encodes as +Z."""
    n = abs(x) + abs(y) + abs(z)
    if n < 1e-20 or not math.isfinite(n):
        return 0, 0
    px, py = x / n, y / n
    if z < 0:
        px, py = (1.0 - abs(py)) * _sgn(px), (1.0 - abs(px)) * _sgn(py)
    return (max(-32767, min(32767, int(round(px * 32767)))), max(-32767, min(32767, int(round(py * 32767)))))


def oct_decode(ix: int, iy: int) -> tuple[float, float, float]:
    """Inverse of :func:`oct_encode`: two snorm16 -> a unit vector."""
    px, py = ix / 32767.0, iy / 32767.0
    pz = 1.0 - abs(px) - abs(py)
    if pz < 0:
        px, py = (1.0 - abs(py)) * _sgn(px), (1.0 - abs(px)) * _sgn(py)
    n = math.sqrt(px * px + py * py + pz * pz)
    return px / n, py / n, pz / n


def pack_normals(normals) -> bytes:
    """Interleaved xyz floats -> OCT16X2 bytes (4 per vertex)."""
    out = bytearray()
    for i in range(0, len(normals) - 2, 3):
        out += struct.pack("<2h", *oct_encode(normals[i], normals[i + 1], normals[i + 2]))
    return bytes(out)


def unpack_normals(data: bytes) -> list[tuple[float, float, float]]:
    return [oct_decode(a, b) for a, b in struct.iter_unpack("<2h", data)]


def _snorm8(v: float) -> int:
    return max(-127, min(127, int(round(v * 127))))


def pack_tangents(tangents: list[tuple[float, float, float, float]]) -> bytes:
    """``(x, y, z, sign)`` per vertex -> SNORM8X4 bytes."""
    return b"".join(struct.pack("<4b", _snorm8(x), _snorm8(y), _snorm8(z), 127 if w >= 0 else -127)
                    for x, y, z, w in tangents)


def unpack_tangents(data: bytes) -> list[tuple[float, float, float, float]]:
    return [(a / 127.0, b / 127.0, c / 127.0, 1.0 if d > 0 else -1.0) for a, b, c, d in struct.iter_unpack("<4b", data)]


def pack_lights(lights: list[tuple]) -> bytes:
    """``(x, y, z, r, g, b, a, corona_far_clip, point_range, corona_size, flags)`` -> LIGHT_V1 records."""
    return b"".join(_LIGHT.pack(*t) for t in lights)


def unpack_lights(data: bytes) -> list[tuple]:
    return list(_LIGHT.iter_unpack(data))


# --------------------------------------------------------------------------- encode / decode


def _align(n: int) -> int:
    return -(-n // L.DAC_ALIGN) * L.DAC_ALIGN


def _check_section(s: DacSection) -> None:
    fmt = L.FORMATS.get(s.format)
    if fmt is None:
        raise DacError("DAC_FORMAT", f"unknown element format {s.format}")
    stride = fmt[1]
    if stride and (s.stride not in (0, stride) or len(s.data) != s.count * stride):
        raise DacError("DAC_FORMAT", f"{fmt[0]} section needs {s.count} x {stride} bytes, has {len(s.data)}")


def encode_dac(dac: Dac) -> bytes:
    """The DAC file bytes. Sections are written sorted by ``(kind, index, stream)``; the meta section comes last."""
    meta = DacSection(L.DAC_META, 0, 0, L.FMT_JSON, params_json(dac.params))
    meta.count = len(meta.data)
    secs = sorted((s for s in dac.sections if s.kind != L.DAC_META), key=lambda s: (s.kind, s.index, s.stream)) + [meta]
    for s in secs:
        _check_section(s)
    table_off = L.DAC_HEADER.size
    off = _align(table_off + L.DAC_SECTION.size * len(secs))
    rows, blobs = [], []
    for s in secs:
        stride = s.stride or L.FORMATS[s.format][1]
        rows.append(L.DAC_SECTION.pack(kind=s.kind, stream=s.stream, index=s.index, count=s.count, format=s.format,
                                       stride=stride, offset=off, size=len(s.data), crc32=zlib.crc32(s.data),
                                       flags=s.flags, aux0=s.aux[0], aux1=s.aux[1], aux2=s.aux[2], aux3=s.aux[3]))
        blobs.append((off, s.data))
        off = _align(off + len(s.data))
    values = dict(magic=L.DAC_MAGIC, major=L.DAC_MAJOR, minor=L.DAC_MINOR, header_size=L.DAC_HEADER.size,
                  section_count=len(secs), derive_id=dac.derive_id, source_kind=dac.source_kind,
                  section_table_off=table_off, file_size=off, source_sha256=dac.source_sha256,
                  cache_key=dac.key)
    raw = L.DAC_HEADER.pack(**values)
    head = L.DAC_HEADER.pack(**values, crc32=zlib.crc32(raw[:L.DAC_HEADER_CRC_SPAN]))
    out = bytearray(off)
    out[:len(head)] = head
    out[table_off:table_off + len(rows) * L.DAC_SECTION.size] = b"".join(rows)
    for o, data in blobs:
        out[o:o + len(data)] = data
    return bytes(out)


def decode_dac(buf: bytes) -> Dac:
    """Parse and validate a DAC file; ``DacError`` names the first problem (bounds, CRCs, formats)."""
    if len(buf) < L.DAC_HEADER.size or buf[:8] != L.DAC_MAGIC:
        raise DacError("BAD_MAGIC", "not a DAC file (no SAEDACFL magic)", "header")
    h = L.DAC_HEADER.unpack(buf[:L.DAC_HEADER.size])
    if zlib.crc32(buf[:L.DAC_HEADER_CRC_SPAN]) != h["crc32"]:
        raise DacError("DAC_CRC", "the header CRC does not match", "header")
    if h["major"] != L.DAC_MAJOR:
        raise DacError("BAD_VERSION", f"DAC format {h['major']}.{h['minor']}; this reader knows {L.DAC_MAJOR}.x",
                       "header")
    if h["file_size"] != len(buf):
        raise DacError("SIZE_MISMATCH", f"the header says {h['file_size']} bytes, the file has {len(buf)}", "header")
    n, to = h["section_count"], h["section_table_off"]
    if h["header_size"] < L.DAC_HEADER.size or to < h["header_size"] or to + n * L.DAC_SECTION.size > len(buf):
        raise DacError("DAC_FORMAT", "the section table does not fit the file", "header")
    sections = []
    for i in range(n):
        r = L.DAC_SECTION.unpack(buf[to + i * 64:to + (i + 1) * 64])
        where = f"section {i}"
        if r["offset"] % L.DAC_ALIGN or r["offset"] < to + n * L.DAC_SECTION.size or r["offset"] + r["size"] > len(buf):
            raise DacError("DAC_RANGE", "the payload is misaligned or outside the file", where)
        data = bytes(buf[r["offset"]:r["offset"] + r["size"]])
        if zlib.crc32(data) != r["crc32"]:
            raise DacError("DAC_CRC", "the payload CRC does not match", where)
        s = DacSection(r["kind"], r["index"], r["count"], r["format"], data, r["stream"], r["stride"], r["flags"],
                       (r["aux0"], r["aux1"], r["aux2"], r["aux3"]))
        if r["kind"] not in L.DAC_KINDS:
            raise DacError("DAC_FORMAT", f"unknown section kind {r['kind']}", where)
        try:
            _check_section(s)
        except DacError as e:
            raise DacError(e.code, e.msg, where) from None
        sections.append(s)
    params: dict = {}
    for s in sections:
        if s.kind == L.DAC_META:
            try:
                params = json.loads(s.data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise DacError("DAC_FORMAT", "the meta section is not JSON", "meta") from None
    dac = Dac(h["source_sha256"], h["derive_id"], h["source_kind"], params,
              [s for s in sections if s.kind != L.DAC_META])
    if dac.key != h["cache_key"]:
        raise DacError("CACHE_KEY", "the cache key does not match the source hash, recipe and parameters", "header")
    return dac


def read_dac(path: str | Path) -> Dac:
    return decode_dac(Path(path).read_bytes())


def verify_dac(buf: bytes) -> list[Finding]:
    """Findings for a DAC file: one error for the first structural problem, warnings for the rest."""
    try:
        dac = decode_dac(buf)
    except DacError as e:
        return [Finding("error", e.code, e.where or "file", e.msg)]
    out: list[Finding] = []
    h = L.DAC_HEADER.unpack(buf[:L.DAC_HEADER.size])
    for nm in L.DAC_HEADER.reserved_nonzero(h):
        out.append(Finding("warn", "RESERVED", "header", f"reserved field {nm} is not zero"))
    seen = set()
    for i, s in enumerate(dac.sections):
        k = (s.kind, s.index, s.stream)
        if k in seen:
            out.append(Finding("error", "SECTION_DUP", f"section {i}", f"kind {s.kind} index {s.index} stream "
                                                                       f"{s.stream} appears twice"))
        seen.add(k)
        if s.kind == L.DAC_GEOM_STREAM and s.stream not in L.STREAMS:
            out.append(Finding("warn", "STREAM_UNKNOWN", f"section {i}", f"unknown stream type {s.stream}"))
    return out


def describe_dac(buf: bytes) -> tuple[dict, list[list]]:
    """``(summary, rows)`` of a DAC for ``pack.inspect``: header facts and one row per section."""
    dac = decode_dac(buf)
    rows = []
    for s in dac.sections:
        rows.append([L.DAC_KINDS[s.kind], L.STREAMS.get(s.stream, "") if s.kind == L.DAC_GEOM_STREAM else "",
                     s.index, s.count, L.FORMATS[s.format][0], len(s.data),
                     "computed" if s.flags & L.SF_COMPUTED else ""])
    summary = {"format": f"{L.DAC_MAJOR}.{L.DAC_MINOR}", "source_sha256": dac.source_sha256.hex(),
               "cache_key": dac.key.hex(), "derive_id": dac.derive_id,
               "source_kind": {1: "dff", 2: "txd"}.get(dac.source_kind, "other"), "params": dac.params,
               "sections": len(dac.sections), "bytes": len(buf)}
    return summary, rows


# --------------------------------------------------------------------------- derivation from a DFF


def _tangents(pos, uv, normals, tris) -> list[tuple[float, float, float, float]]:
    """Per-vertex tangents (x, y, z, bitangent sign) from positions, UV set 0, unit normals and triangles."""
    nv = len(pos) // 3
    t = [0.0] * (3 * nv)
    b = [0.0] * (3 * nv)
    for a, c1, c2, _m in tris:
        p0, p1, p2 = (pos[3 * a], pos[3 * a + 1], pos[3 * a + 2]), (pos[3 * c1], pos[3 * c1 + 1], pos[3 * c1 + 2]), \
            (pos[3 * c2], pos[3 * c2 + 1], pos[3 * c2 + 2])
        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        du1, dv1 = uv[2 * c1] - uv[2 * a], uv[2 * c1 + 1] - uv[2 * a + 1]
        du2, dv2 = uv[2 * c2] - uv[2 * a], uv[2 * c2 + 1] - uv[2 * a + 1]
        det = du1 * dv2 - du2 * dv1
        if abs(det) < 1e-12:
            continue
        r = 1.0 / det
        tx, ty, tz = ((e1[i] * dv2 - e2[i] * dv1) * r for i in range(3))
        bx, by, bz = ((e2[i] * du1 - e1[i] * du2) * r for i in range(3))
        for v in (a, c1, c2):
            t[3 * v] += tx
            t[3 * v + 1] += ty
            t[3 * v + 2] += tz
            b[3 * v] += bx
            b[3 * v + 1] += by
            b[3 * v + 2] += bz
    out = []
    for v in range(nv):
        nx, ny, nz = normals[3 * v], normals[3 * v + 1], normals[3 * v + 2]
        tx, ty, tz = t[3 * v], t[3 * v + 1], t[3 * v + 2]
        d = nx * tx + ny * ty + nz * tz
        tx, ty, tz = tx - nx * d, ty - ny * d, tz - nz * d
        ln = math.sqrt(tx * tx + ty * ty + tz * tz)
        if ln < 1e-12 or not math.isfinite(ln):
            # no usable UV gradient: any unit vector perpendicular to the normal
            ax, ay, az = (1.0, 0.0, 0.0) if abs(nx) < 0.9 else (0.0, 1.0, 0.0)
            tx, ty, tz = ay * nz - az * ny, az * nx - ax * nz, ax * ny - ay * nx
            ln = math.sqrt(tx * tx + ty * ty + tz * tz) or 1.0
        tx, ty, tz = tx / ln, ty / ln, tz / ln
        cx, cy, cz = ny * tz - nz * ty, nz * tx - nx * tz, nx * ty - ny * tx
        w = 1.0 if (cx * b[3 * v] + cy * b[3 * v + 1] + cz * b[3 * v + 2]) >= 0 else -1.0
        out.append((tx, ty, tz, w))
    return out


def derive_dff(data: bytes, *, normals: str = "missing", tangents: bool = True, night: bool = True,
               lights: bool = True, angle: float = 45.0, warn: list[str] | None = None) -> Dac:
    """Derive a :class:`Dac` from DFF bytes.

    ``normals``: ``missing`` (a stream for each geometry without normals, computed seam-aware at ``angle`` degrees),
    ``all`` (a stream for every geometry: the stored normals when present) or ``none``. Night colours are copied from
    the night-colour extension, tangents need UV set 0, lights come from the 2dEffect entries of type 0. Platform
    native geometries are skipped with a ``NATIVE`` warning.
    """
    from ..rw import codecs as C
    from ..rw.chunk import FX2D, NIGHT
    from ..rw.dff import DffDoc, _floats, _triangles_mat, smooth_geometry

    if normals not in ("missing", "all", "none"):
        raise ValueError(f"normals must be missing, all or none, got {normals!r}")
    w = warn if warn is not None else []
    doc = DffDoc.parse(data)
    sha = hashlib.sha256(data).digest()
    params = {"angle": float(angle), "lights": bool(lights), "night": bool(night), "normals": normals,
              "tangents": bool(tangents)}
    dac = Dac(sha, L.DERIVE_DFF_V1, L.SRC_DFF, params)
    for gr in doc.geometries():
        g = gr.data()
        i, nv = gr.index, g.num_verts
        ext = gr.ext()
        if g.is_native:
            w.append(f"NATIVE: geometry {i} is platform-native; no streams derived")
            continue
        if not nv or not g.morphs or not g.morphs[0].has_verts:
            continue
        pos = _floats(g.morphs[0].verts)
        tris = _triangles_mat(g, gr)
        stored = g.morphs[0].normals if g.morphs[0].has_normals and len(g.morphs[0].normals) == 12 * nv else None
        nrm = None
        computed = False
        if stored is not None:
            nrm = _floats(stored)
        elif tris and (normals != "none" or tangents):
            nrm = smooth_geometry(pos, tris, angle=angle)
            computed = True
        elif not tris and (normals != "none" or tangents):
            w.append(f"NO_TRIANGLES: geometry {i} has no triangles; no normals or tangents derived")
        if normals == "all" or (normals == "missing" and computed):
            if nrm is not None:
                dac.sections.append(DacSection(L.DAC_GEOM_STREAM, i, nv, L.FMT_OCT16X2, pack_normals(nrm),
                                               L.STREAM_NORMAL, 4, L.SF_COMPUTED if computed else 0))
        if night and ext is not None:
            nc = next((k for k in (ext.kids or ()) if k.type == NIGHT), None)
            if nc is not None and nc.data is not None:
                try:
                    nd = C.decode_night(nc.data, nv)
                except C.CodecError:
                    w.append(f"BAD_NIGHT: geometry {i}: the night colour chunk is truncated; stream skipped")
                    nd = None
                if nd is not None and nd.magic and len(nd.colors) == 4 * nv:
                    dac.sections.append(DacSection(L.DAC_GEOM_STREAM, i, nv, L.FMT_RGBA8, nd.colors,
                                                   L.STREAM_NIGHT_COLOR, 4, 0))
        if tangents and nrm is not None and g.num_uv >= 1 and g.uvs and tris:
            uv = _floats(g.uvs[0])
            if len(uv) >= 2 * nv:
                dac.sections.append(DacSection(L.DAC_GEOM_STREAM, i, nv, L.FMT_SNORM8X4,
                                               pack_tangents(_tangents(pos, uv, nrm, tris)), L.STREAM_TANGENT, 4,
                                               L.SF_COMPUTED))
        if lights and ext is not None:
            fx = next((k for k in (ext.kids or ()) if k.type == FX2D), None)
            if fx is not None and fx.data is not None:
                try:
                    fxd = C.decode_2dfx(fx.data)
                except C.CodecError:
                    w.append(f"BAD_2DFX: geometry {i}: the 2dEffect chunk is truncated; lights skipped")
                    fxd = None
                rec = []
                for pos12, typ, d in (fxd.entries if fxd else ()):
                    if typ != 0 or len(d) < 76:
                        continue
                    x, y, z = struct.unpack("<3f", pos12)
                    far, rng, csize, _ssize = struct.unpack_from("<4f", d, 4)
                    flags = d[24] | (d[74] << 8)
                    rec.append((x, y, z, d[0], d[1], d[2], d[3], far, rng, csize, flags))
                if rec:
                    dac.sections.append(DacSection(L.DAC_LIGHT_LIST, i, len(rec), L.FMT_LIGHT_V1, pack_lights(rec),
                                                   0, 32, 0))
    return dac
