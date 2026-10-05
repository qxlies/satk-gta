"""COL (Collision) file parser/writer for GTA III, Vice City, and San Andreas.

Supports COL1 (GTA III/VC), COL2 (SA PS2), and COL3 (SA PC/Xbox).
Standalone .col files can contain multiple collision models.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field


# Magic identifiers
COL1_MAGIC = b"COLL"
COL2_MAGIC = b"COL2"
COL3_MAGIC = b"COL3"

COL_FLAG_NOT_EMPTY = 0x02
COL_FLAG_FACE_GROUPS = 0x08
COL_FLAG_SHADOW_MESH = 0x10


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ColSurface:
    """Material/surface properties for a collision primitive (4 bytes)."""
    material: int = 0
    flag: int = 0
    brightness: int = 0
    light: int = 0


@dataclass
class ColBounds:
    """Bounding volume (sphere + AABB)."""
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius: float = 0.0
    min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    max: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class ColSphere:
    """Sphere collision primitive."""
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius: float = 0.0
    surface: ColSurface = field(default_factory=ColSurface)


@dataclass
class ColBox:
    """Axis-aligned box collision primitive."""
    min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    max: tuple[float, float, float] = (0.0, 0.0, 0.0)
    surface: ColSurface = field(default_factory=ColSurface)


@dataclass
class ColFace:
    """Triangle face."""
    a: int = 0
    b: int = 0
    c: int = 0
    material: int = 0
    light: int = 0


@dataclass
class ColFaceGroup:
    """Spatial face grouping (COL2/3 only)."""
    min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    max: tuple[float, float, float] = (0.0, 0.0, 0.0)
    start_face: int = 0
    end_face: int = 0


@dataclass
class ColModel:
    """A single collision model."""
    name: str = ""
    model_id: int = 0
    version: int = 1  # 1, 2, or 3
    bounds: ColBounds = field(default_factory=ColBounds)
    spheres: list[ColSphere] = field(default_factory=list)
    boxes: list[ColBox] = field(default_factory=list)
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    faces: list[ColFace] = field(default_factory=list)
    face_groups: list[ColFaceGroup] = field(default_factory=list)
    # COL3 shadow mesh
    shadow_vertices: list[tuple[float, float, float]] = field(default_factory=list)
    shadow_faces: list[ColFace] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main Col class
# ---------------------------------------------------------------------------

class Col:
    """COL collision file container. May hold multiple collision models."""

    def __init__(self):
        self.models: list[ColModel] = []

    @classmethod
    def from_file(cls, path: str) -> Col:
        with open(path, "rb") as f:
            data = f.read()
        return cls.from_bytes(data)

    @classmethod
    def from_bytes(cls, data: bytes) -> Col:
        """Parse COL data (standalone .col or embedded DFF collision blob)."""
        col = cls()
        stream = io.BytesIO(data)
        total = len(data)

        while stream.tell() < total:
            pos = stream.tell()
            remaining = total - pos
            if remaining < 8:
                break

            magic = stream.read(4)
            if magic not in (COL1_MAGIC, COL2_MAGIC, COL3_MAGIC):
                break

            file_size = struct.unpack("<I", stream.read(4))[0]
            model_start = stream.tell()
            model_end = model_start + file_size
            offset_base = pos + 4

            if magic == COL1_MAGIC:
                model = _parse_col1(stream, model_end)
            else:
                version = 2 if magic == COL2_MAGIC else 3
                model = _parse_col23(stream, model_end, version, offset_base)

            col.models.append(model)
            stream.seek(model_end)

        return col

    def to_file(self, path: str):
        """Write all models to a .col file."""
        with open(path, "wb") as f:
            f.write(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize all models to bytes."""
        parts = []
        for model in self.models:
            if model.version == 1:
                parts.append(_write_col1(model))
            else:
                parts.append(_write_col23(model))
        return b"".join(parts)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _read_surface(stream: io.BytesIO) -> ColSurface:
    data = stream.read(4)
    return ColSurface(
        material=data[0], flag=data[1],
        brightness=data[2], light=data[3],
    )


def _read_name_and_id(stream: io.BytesIO) -> tuple[str, int]:
    name_raw = stream.read(22)
    name = name_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    model_id = struct.unpack("<h", stream.read(2))[0]
    return name, model_id


def _read_bounds_v1(stream: io.BytesIO) -> ColBounds:
    radius = struct.unpack("<f", stream.read(4))[0]
    center = struct.unpack("<3f", stream.read(12))
    bmin = struct.unpack("<3f", stream.read(12))
    bmax = struct.unpack("<3f", stream.read(12))
    return ColBounds(center=center, radius=radius, min=bmin, max=bmax)


def _read_bounds_v23(stream: io.BytesIO) -> ColBounds:
    bmin = struct.unpack("<3f", stream.read(12))
    bmax = struct.unpack("<3f", stream.read(12))
    center = struct.unpack("<3f", stream.read(12))
    radius = struct.unpack("<f", stream.read(4))[0]
    return ColBounds(center=center, radius=radius, min=bmin, max=bmax)


def _read_faces_v23(stream: io.BytesIO, count: int) -> list[ColFace]:
    faces: list[ColFace] = []
    for _ in range(count):
        a, b, c = struct.unpack("<HHH", stream.read(6))
        mat = struct.unpack("<B", stream.read(1))[0]
        light = struct.unpack("<B", stream.read(1))[0]
        faces.append(ColFace(a=a, b=b, c=c, material=mat, light=light))
    return faces


def _read_vertices_v23(stream: io.BytesIO, count: int) -> list[tuple[float, float, float]]:
    vertices: list[tuple[float, float, float]] = []
    for _ in range(count):
        x, y, z = struct.unpack("<3h", stream.read(6))
        vertices.append((x / 128.0, y / 128.0, z / 128.0))
    return vertices


def _max_face_index(faces: list[ColFace]) -> int:
    if not faces:
        return -1
    return max(max(face.a, face.b, face.c) for face in faces)


def _pack_vertices_v23(vertices: list[tuple[float, float, float]]) -> bytes:
    data = bytearray()
    for x, y, z in vertices:
        data += struct.pack(
            "<3h",
            max(-32768, min(32767, int(round(x * 128.0)))),
            max(-32768, min(32767, int(round(y * 128.0)))),
            max(-32768, min(32767, int(round(z * 128.0)))),
        )
    if len(data) % 4:
        data += b"\x00" * (4 - (len(data) % 4))
    return bytes(data)


# ---------------------------------------------------------------------------
# COL1 parser
# ---------------------------------------------------------------------------

def _parse_col1(stream: io.BytesIO, model_end: int) -> ColModel:
    model = ColModel(version=1)
    model.name, model.model_id = _read_name_and_id(stream)
    model.bounds = _read_bounds_v1(stream)

    # Spheres
    num_spheres = struct.unpack("<I", stream.read(4))[0]
    for _ in range(num_spheres):
        radius = struct.unpack("<f", stream.read(4))[0]
        center = struct.unpack("<3f", stream.read(12))
        surface = _read_surface(stream)
        model.spheres.append(ColSphere(center=center, radius=radius, surface=surface))

    # Unknown (always 0)
    _num_unk = struct.unpack("<I", stream.read(4))[0]

    # Boxes
    num_boxes = struct.unpack("<I", stream.read(4))[0]
    for _ in range(num_boxes):
        bmin = struct.unpack("<3f", stream.read(12))
        bmax = struct.unpack("<3f", stream.read(12))
        surface = _read_surface(stream)
        model.boxes.append(ColBox(min=bmin, max=bmax, surface=surface))

    # Vertices
    num_vertices = struct.unpack("<I", stream.read(4))[0]
    for _ in range(num_vertices):
        model.vertices.append(struct.unpack("<3f", stream.read(12)))

    # Faces
    num_faces = struct.unpack("<I", stream.read(4))[0]
    for _ in range(num_faces):
        a, b, c = struct.unpack("<III", stream.read(12))
        surface = _read_surface(stream)
        model.faces.append(ColFace(a=a, b=b, c=c, material=surface.material, light=surface.light))

    return model


# ---------------------------------------------------------------------------
# COL2/3 parser
# ---------------------------------------------------------------------------

def _parse_col23(stream: io.BytesIO, model_end: int, version: int, offset_base: int) -> ColModel:
    model = ColModel(version=version)
    model.name, model.model_id = _read_name_and_id(stream)

    # Re-read: we already consumed name+id, now read bounds
    model.bounds = _read_bounds_v23(stream)

    num_spheres = struct.unpack("<H", stream.read(2))[0]
    num_boxes = struct.unpack("<H", stream.read(2))[0]
    num_faces = struct.unpack("<H", stream.read(2))[0]
    _num_lines = struct.unpack("<B", stream.read(1))[0]
    _pad = stream.read(1)
    flags = struct.unpack("<I", stream.read(4))[0]

    off_spheres = struct.unpack("<I", stream.read(4))[0]
    off_boxes = struct.unpack("<I", stream.read(4))[0]
    _off_lines = struct.unpack("<I", stream.read(4))[0]
    off_vertices = struct.unpack("<I", stream.read(4))[0]
    off_faces = struct.unpack("<I", stream.read(4))[0]
    _off_tri_planes = struct.unpack("<I", stream.read(4))[0]

    num_shadow_faces = 0
    off_shadow_verts = 0
    off_shadow_faces = 0
    if version >= 3:
        num_shadow_faces = struct.unpack("<I", stream.read(4))[0]
        off_shadow_verts = struct.unpack("<I", stream.read(4))[0]
        off_shadow_faces = struct.unpack("<I", stream.read(4))[0]

    # Spheres (COL2/3 format: center first, then radius)
    if num_spheres > 0 and off_spheres > 0:
        stream.seek(offset_base + off_spheres)
        for _ in range(num_spheres):
            center = struct.unpack("<3f", stream.read(12))
            radius = struct.unpack("<f", stream.read(4))[0]
            surface = _read_surface(stream)
            model.spheres.append(ColSphere(center=center, radius=radius, surface=surface))

    # Boxes
    if num_boxes > 0 and off_boxes > 0:
        stream.seek(offset_base + off_boxes)
        for _ in range(num_boxes):
            bmin = struct.unpack("<3f", stream.read(12))
            bmax = struct.unpack("<3f", stream.read(12))
            surface = _read_surface(stream)
            model.boxes.append(ColBox(min=bmin, max=bmax, surface=surface))

    if num_faces > 0 and off_faces > 0:
        face_pos = offset_base + off_faces
        if flags & COL_FLAG_FACE_GROUPS:
            fg_count_pos = face_pos - 4
            stream.seek(fg_count_pos)
            fg_count = struct.unpack("<I", stream.read(4))[0]
            if 0 < fg_count < 10000:
                fg_start = fg_count_pos - fg_count * 28
                if fg_start >= offset_base:
                    stream.seek(fg_start)
                    for _ in range(fg_count):
                        fgmin = struct.unpack("<3f", stream.read(12))
                        fgmax = struct.unpack("<3f", stream.read(12))
                        start, end = struct.unpack("<HH", stream.read(4))
                        model.face_groups.append(ColFaceGroup(
                            min=fgmin, max=fgmax,
                            start_face=start, end_face=end,
                        ))

        stream.seek(face_pos)
        model.faces = _read_faces_v23(stream, num_faces)

    # Shadow mesh (COL3)
    if version >= 3 and num_shadow_faces > 0 and off_shadow_faces > 0:
        stream.seek(offset_base + off_shadow_faces)
        model.shadow_faces = _read_faces_v23(stream, num_shadow_faces)

    if off_vertices > 0:
        vertex_start = offset_base + off_vertices
        vertex_end = model_end
        if num_faces > 0 and off_faces > off_vertices:
            vertex_end = offset_base + off_faces
            if model.face_groups:
                vertex_end -= 4 + len(model.face_groups) * 28
        elif version >= 3 and off_shadow_verts > off_vertices:
            vertex_end = offset_base + off_shadow_verts

        max_vertex_count = max(0, (vertex_end - vertex_start) // 6)
        face_vertex_count = _max_face_index(model.faces) + 1
        vertex_count = face_vertex_count if 0 <= face_vertex_count <= max_vertex_count else max_vertex_count

        if vertex_count > 0:
            stream.seek(vertex_start)
            model.vertices = _read_vertices_v23(stream, vertex_count)

    if version >= 3 and off_shadow_verts > 0:
        shadow_vert_start = offset_base + off_shadow_verts
        shadow_vert_end = offset_base + off_shadow_faces if off_shadow_faces > off_shadow_verts else model_end
        max_shadow_vertex_count = max(0, (shadow_vert_end - shadow_vert_start) // 6)
        face_shadow_count = _max_face_index(model.shadow_faces) + 1
        shadow_vertex_count = (
            face_shadow_count
            if 0 <= face_shadow_count <= max_shadow_vertex_count
            else max_shadow_vertex_count
        )

        if shadow_vertex_count > 0:
            stream.seek(shadow_vert_start)
            model.shadow_vertices = _read_vertices_v23(stream, shadow_vertex_count)

    return model


# ---------------------------------------------------------------------------
# COL1 writer
# ---------------------------------------------------------------------------

def _write_col1(model: ColModel) -> bytes:
    body = b""

    # Name + ID
    name_bytes = model.name.encode("ascii", errors="replace")[:21].ljust(22, b"\x00")
    body += name_bytes
    body += struct.pack("<h", model.model_id)

    # Bounds (COL1 order: radius, center, min, max)
    b = model.bounds
    body += struct.pack("<f", b.radius)
    body += struct.pack("<3f", *b.center)
    body += struct.pack("<3f", *b.min)
    body += struct.pack("<3f", *b.max)

    # Spheres
    body += struct.pack("<I", len(model.spheres))
    for s in model.spheres:
        body += struct.pack("<f", s.radius)
        body += struct.pack("<3f", *s.center)
        body += struct.pack("<4B", s.surface.material, s.surface.flag,
                            s.surface.brightness, s.surface.light)

    # Unknown (always 0)
    body += struct.pack("<I", 0)

    # Boxes
    body += struct.pack("<I", len(model.boxes))
    for box in model.boxes:
        body += struct.pack("<3f", *box.min)
        body += struct.pack("<3f", *box.max)
        body += struct.pack("<4B", box.surface.material, box.surface.flag,
                            box.surface.brightness, box.surface.light)

    # Vertices
    body += struct.pack("<I", len(model.vertices))
    for v in model.vertices:
        body += struct.pack("<3f", *v)

    # Faces
    body += struct.pack("<I", len(model.faces))
    for f in model.faces:
        body += struct.pack("<III", f.a, f.b, f.c)
        body += struct.pack("<4B", f.material, 0, 0, f.light)

    # Header: magic + file_size
    header = COL1_MAGIC + struct.pack("<I", len(body))
    return header + body


# ---------------------------------------------------------------------------
# COL2/3 writer
# ---------------------------------------------------------------------------

def _write_col23(model: ColModel) -> bytes:
    magic = COL3_MAGIC if model.version >= 3 else COL2_MAGIC

    # Offsets are relative to byte 4 of the model header (immediately after fourcc).
    header_size = 24 + 40 + 12 + 24
    if model.version >= 3:
        header_size += 12

    # Spheres
    sphere_data = b""
    for s in model.spheres:
        sphere_data += struct.pack("<3f", *s.center)
        sphere_data += struct.pack("<f", s.radius)
        sphere_data += struct.pack("<4B", s.surface.material, s.surface.flag,
                                   s.surface.brightness, s.surface.light)

    # Boxes
    box_data = b""
    for box in model.boxes:
        box_data += struct.pack("<3f", *box.min)
        box_data += struct.pack("<3f", *box.max)
        box_data += struct.pack("<4B", box.surface.material, box.surface.flag,
                                box.surface.brightness, box.surface.light)

    vert_data = _pack_vertices_v23(model.vertices)

    # Face groups + faces
    fg_data = b""
    for fg in model.face_groups:
        fg_data += struct.pack("<3f", *fg.min)
        fg_data += struct.pack("<3f", *fg.max)
        fg_data += struct.pack("<HH", fg.start_face, fg.end_face)
    if model.face_groups:
        fg_data += struct.pack("<I", len(model.face_groups))

    face_data = b""
    for f in model.faces:
        face_data += struct.pack("<HHH", f.a, f.b, f.c)
        face_data += struct.pack("<BB", f.material, f.light)

    # Shadow mesh (COL3)
    shadow_vert_data = b""
    shadow_face_data = b""
    if model.version >= 3:
        shadow_vert_data = _pack_vertices_v23(model.shadow_vertices)
        for f in model.shadow_faces:
            shadow_face_data += struct.pack("<HHH", f.a, f.b, f.c)
            shadow_face_data += struct.pack("<BB", f.material, f.light)

    # Compute offsets relative to byte 4 of the model header.
    off = header_size
    off_spheres = 4 + off if sphere_data else 0
    off += len(sphere_data)
    off_boxes = 4 + off if box_data else 0
    off += len(box_data)
    off_lines = 0
    off_vertices = 4 + off if vert_data else 0
    off += len(vert_data)
    off += len(fg_data)
    off_faces = 4 + off if face_data else 0
    off += len(face_data)
    off_tri_planes = 0

    off_shadow_verts = 4 + off if shadow_vert_data else 0
    off += len(shadow_vert_data)
    off_shadow_faces = 4 + off if shadow_face_data else 0
    off += len(shadow_face_data)

    flags = 0
    if model.spheres or model.boxes or model.faces or model.shadow_faces:
        flags |= COL_FLAG_NOT_EMPTY
    if model.face_groups:
        flags |= COL_FLAG_FACE_GROUPS
    if model.version >= 3 and (model.shadow_vertices or model.shadow_faces):
        flags |= COL_FLAG_SHADOW_MESH

    # Build header
    body = b""
    name_bytes = model.name.encode("ascii", errors="replace")[:21].ljust(22, b"\x00")
    body += name_bytes
    body += struct.pack("<h", model.model_id)

    # Bounds (COL2/3 order: min, max, center, radius)
    bn = model.bounds
    body += struct.pack("<3f", *bn.min)
    body += struct.pack("<3f", *bn.max)
    body += struct.pack("<3f", *bn.center)
    body += struct.pack("<f", bn.radius)

    body += struct.pack("<HHH", len(model.spheres), len(model.boxes), len(model.faces))
    body += struct.pack("<BB", 0, 0)  # lines, pad
    body += struct.pack("<I", flags)

    body += struct.pack("<I", off_spheres)
    body += struct.pack("<I", off_boxes)
    body += struct.pack("<I", off_lines)
    body += struct.pack("<I", off_vertices)
    body += struct.pack("<I", off_faces)
    body += struct.pack("<I", off_tri_planes)

    if model.version >= 3:
        body += struct.pack("<I", len(model.shadow_faces))
        body += struct.pack("<I", off_shadow_verts)
        body += struct.pack("<I", off_shadow_faces)

    # Append data sections
    body += sphere_data
    body += box_data
    body += vert_data
    body += fg_data
    body += face_data
    body += shadow_vert_data
    body += shadow_face_data

    # Header: magic + file_size
    header = magic + struct.pack("<I", len(body))
    return header + body
