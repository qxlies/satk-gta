"""Fastman92 extended GTA SA path file support."""

from __future__ import annotations

import io
import struct

from .paths_common import (
    DEFAULT_SECTION4_FILLER,
    DEFAULT_TRAILING_DATA,
    LINK_SIZE,
    PATH_NODE_SIZE,
    POSITION_SCALE,
    SECTION4_FILLER_SIZE,
    NaviLink,
    PathFileFormat,
    PathIntersectionFlag,
    PathNodeKind,
    _clamp_u8,
    _to_i16,
    _to_i32,
    _to_u16,
    pack_navi_node_with_position,
    pack_path_node_with_position,
    read_exact_labeled,
    read_link,
    read_navi_node,
    read_path_node,
    validate_count,
)


FASTMAN92_MAGIC = b"\xff\xff\xff\xffFM92"
FASTMAN92_EOF_MARKER = b"EOF\x00"
FASTMAN92_TAIL_SIZE = 384
FASTMAN92_SECTOR_SIZE = 2048


def is_fastman92_path_data(data: bytes) -> bool:
    return data.startswith(FASTMAN92_MAGIC)


def parse_fastman92_path_file(
    data: bytes,
    path_file_cls,
    *,
    area_id: int | None = None,
    format: PathFileFormat = PathFileFormat.AUTO,
):
    stream = io.BytesIO(data)
    magic = read_exact_labeled(stream, len(FASTMAN92_MAGIC), "Fastman92 magic")
    if magic != FASTMAN92_MAGIC:
        raise ValueError("Not a Fastman92 path file: invalid magic")

    author_len = struct.unpack(
        "<B", read_exact_labeled(stream, 1, "Fastman92 author length")
    )[0]
    author = read_exact_labeled(stream, author_len, "Fastman92 author")
    version = read_exact_labeled(stream, 4, "Fastman92 version").decode(
        "ascii", errors="replace"
    )
    if version not in ("VER2", "VER3"):
        raise ValueError(f"Unsupported Fastman92 path version: {version!r}")

    detected_format = (
        PathFileFormat.FASTMAN92_VER3
        if version == "VER3"
        else PathFileFormat.FASTMAN92_VER2
    )
    if format not in (PathFileFormat.AUTO, detected_format):
        raise ValueError(
            f"Requested {format.value} but Fastman92 file is {detected_format.value}"
        )

    counts = struct.unpack(
        "<5I", read_exact_labeled(stream, 20, "Fastman92 path counts")
    )
    total_nodes, vehicle_count, ped_count, navi_count, link_count = counts
    if total_nodes != vehicle_count + ped_count:
        raise ValueError(
            "Fastman92 path header node count does not match vehicle + ped counts"
        )
    validate_count("Fastman92 node count", total_nodes)
    validate_count("Fastman92 navi node count", navi_count)
    validate_count("Fastman92 link count", link_count)
    _validate_fastman92_min_size(
        len(data),
        stream.tell(),
        version,
        total_nodes,
        navi_count,
        link_count,
    )

    path_file = path_file_cls(
        area_id=area_id,
        source_format=detected_format,
        fastman92_version=version,
        fastman92_author=author,
    )

    if total_nodes == 0 and navi_count == 0 and link_count == 0:
        marker = read_exact_labeled(
            stream, len(FASTMAN92_EOF_MARKER), "Fastman92 EOF marker"
        )
        if marker != FASTMAN92_EOF_MARKER:
            raise ValueError("Fastman92 empty path file is missing EOF marker")
        path_file.section4_filler = b""
        path_file.trailing_data = b""
        path_file.sector_padding = stream.read()
        return path_file

    for _ in range(vehicle_count):
        node = read_path_node(stream, PathNodeKind.VEHICLE)
        path_file.legacy_path_positions.append(node.position)
        extended = _read_fastman92_path_position(stream, version)
        node.position = extended
        path_file.extended_path_positions.append(extended)
        if version == "VER3":
            node.flood_fill = _read_fastman92_flood_fill(stream)
        path_file.vehicle_nodes.append(node)

    for _ in range(ped_count):
        node = read_path_node(stream, PathNodeKind.PED)
        path_file.legacy_path_positions.append(node.position)
        extended = _read_fastman92_path_position(stream, version)
        node.position = extended
        path_file.extended_path_positions.append(extended)
        if version == "VER3":
            node.flood_fill = _read_fastman92_flood_fill(stream)
        path_file.ped_nodes.append(node)

    for _ in range(navi_count):
        node = read_navi_node(stream)
        path_file.legacy_navi_positions.append(node.position)
        extended = _read_fastman92_navi_position(stream)
        node.position = extended
        path_file.extended_navi_positions.append(extended)
        path_file.navi_nodes.append(node)

    for _ in range(link_count):
        path_file.links.append(read_link(stream))

    path_file.section4_filler = read_exact_labeled(
        stream, SECTION4_FILLER_SIZE, "Fastman92 section 4 filler"
    )

    for _ in range(link_count):
        node_id, area_id_value = struct.unpack(
            "<HH", read_exact_labeled(stream, 4, "Fastman92 navi link")
        )
        path_file.navi_links.append(NaviLink(area_id=area_id_value, node_id=node_id))

    path_file.link_lengths = list(
        read_exact_labeled(stream, link_count, "Fastman92 link lengths")
    )
    path_file.intersection_flags = [
        PathIntersectionFlag(value)
        for value in read_exact_labeled(stream, link_count, "Fastman92 intersection flags")
    ]

    remaining = stream.read()
    if len(remaining) < FASTMAN92_TAIL_SIZE + len(FASTMAN92_EOF_MARKER):
        raise EOFError("Fastman92 path file is missing tail and EOF marker")
    tail = remaining[:FASTMAN92_TAIL_SIZE]
    marker = remaining[
        FASTMAN92_TAIL_SIZE:FASTMAN92_TAIL_SIZE + len(FASTMAN92_EOF_MARKER)
    ]
    if marker != FASTMAN92_EOF_MARKER:
        raise ValueError("Fastman92 path file is missing EOF marker after 384-byte tail")
    path_file.trailing_data = tail
    path_file.sector_padding = remaining[
        FASTMAN92_TAIL_SIZE + len(FASTMAN92_EOF_MARKER):
    ]
    path_file.validate()
    return path_file


def write_fastman92_path_file(path_file, format: PathFileFormat) -> bytes:
    path_file.validate()
    version = "VER3" if format == PathFileFormat.FASTMAN92_VER3 else "VER2"
    author = path_file.fastman92_author
    if len(author) > 255:
        raise ValueError("Fastman92 author payload must fit in one byte length")

    out = bytearray()
    out += FASTMAN92_MAGIC
    out += struct.pack("<B", len(author))
    out += author
    out += version.encode("ascii")
    out += struct.pack(
        "<5I",
        path_file.node_count,
        len(path_file.vehicle_nodes),
        len(path_file.ped_nodes),
        len(path_file.navi_nodes),
        len(path_file.links),
    )

    if path_file.node_count == 0 and len(path_file.navi_nodes) == 0 and len(path_file.links) == 0:
        out += FASTMAN92_EOF_MARKER
        out += path_file.sector_padding or _sector_padding(len(out))
        return bytes(out)

    legacy_path_positions = path_file.legacy_path_positions
    extended_path_positions = path_file.extended_path_positions
    path_index = 0
    for node in path_file.vehicle_nodes:
        legacy_pos = _list_get(legacy_path_positions, path_index, node.position)
        extended_pos = _list_get(extended_path_positions, path_index, node.position)
        out += pack_path_node_with_position(node, PathNodeKind.VEHICLE, legacy_pos)
        out += _pack_fastman92_path_position(extended_pos)
        if version == "VER3":
            out += struct.pack("<h", _to_i16(node.flood_fill))
        path_index += 1
    for node in path_file.ped_nodes:
        legacy_pos = _list_get(legacy_path_positions, path_index, node.position)
        extended_pos = _list_get(extended_path_positions, path_index, node.position)
        out += pack_path_node_with_position(node, PathNodeKind.PED, legacy_pos)
        out += _pack_fastman92_path_position(extended_pos)
        if version == "VER3":
            out += struct.pack("<h", _to_i16(node.flood_fill))
        path_index += 1

    legacy_navi_positions = path_file.legacy_navi_positions
    extended_navi_positions = path_file.extended_navi_positions
    for i, node in enumerate(path_file.navi_nodes):
        legacy_pos = _list_get(legacy_navi_positions, i, node.position)
        extended_pos = _list_get(extended_navi_positions, i, node.position)
        out += pack_navi_node_with_position(node, legacy_pos)
        out += _pack_fastman92_navi_position(extended_pos)

    for link in path_file.links:
        out += struct.pack("<HH", _to_u16(link.area_id), _to_u16(link.node_id))

    section4 = path_file.section4_filler or DEFAULT_SECTION4_FILLER
    if len(section4) != SECTION4_FILLER_SIZE:
        raise ValueError("Fastman92 section4_filler must be exactly 768 bytes")
    out += section4

    for navi_link in path_file.navi_links:
        out += struct.pack("<HH", _to_u16(navi_link.node_id), _to_u16(navi_link.area_id))
    out += bytes(_clamp_u8(length) for length in path_file.link_lengths)
    out += bytes(_clamp_u8(int(flags)) for flags in path_file.intersection_flags)

    tail = path_file.trailing_data if path_file.trailing_data else DEFAULT_TRAILING_DATA
    if len(tail) != FASTMAN92_TAIL_SIZE:
        raise ValueError("Fastman92 trailing_data must be exactly 384 bytes")
    out += tail
    out += FASTMAN92_EOF_MARKER
    out += path_file.sector_padding or _sector_padding(len(out))
    return bytes(out)


def _validate_fastman92_min_size(
    data_size: int,
    structured_offset: int,
    version: str,
    node_count: int,
    navi_count: int,
    link_count: int,
):
    if node_count == 0 and navi_count == 0 and link_count == 0:
        minimum = structured_offset + len(FASTMAN92_EOF_MARKER)
    else:
        path_node_size = PATH_NODE_SIZE + 12 + (2 if version == "VER3" else 0)
        navi_node_size = 14 + 8
        minimum = (
            structured_offset
            + node_count * path_node_size
            + navi_count * navi_node_size
            + link_count * LINK_SIZE
            + SECTION4_FILLER_SIZE
            + link_count * 4
            + link_count
            + link_count
            + FASTMAN92_TAIL_SIZE
            + len(FASTMAN92_EOF_MARKER)
        )
    if data_size < minimum:
        raise EOFError(
            f"Fastman92 path file is too small: expected at least {minimum} bytes, got {data_size}"
        )


def _read_fastman92_path_position(stream: io.BytesIO, version: str) -> tuple[float, float, float]:
    x, y, z = struct.unpack(
        "<3i", read_exact_labeled(stream, 12, f"Fastman92 {version} path node position")
    )
    return (x / POSITION_SCALE, y / POSITION_SCALE, z / POSITION_SCALE)


def _read_fastman92_flood_fill(stream: io.BytesIO) -> int:
    return struct.unpack(
        "<h", read_exact_labeled(stream, 2, "Fastman92 VER3 flood fill")
    )[0]


def _read_fastman92_navi_position(stream: io.BytesIO) -> tuple[float, float]:
    x, y = struct.unpack(
        "<2i", read_exact_labeled(stream, 8, "Fastman92 navi node position")
    )
    return (x / POSITION_SCALE, y / POSITION_SCALE)


def _pack_fastman92_path_position(position: tuple[float, float, float]) -> bytes:
    return struct.pack("<3i", *(_to_i32(coord * POSITION_SCALE) for coord in position))


def _pack_fastman92_navi_position(position: tuple[float, float]) -> bytes:
    return struct.pack("<2i", *(_to_i32(coord * POSITION_SCALE) for coord in position))


def _list_get(values: list, index: int, default):
    return values[index] if index < len(values) else default


def _sector_padding(current_size: int) -> bytes:
    padding = (FASTMAN92_SECTOR_SIZE - (current_size % FASTMAN92_SECTOR_SIZE)) % FASTMAN92_SECTOR_SIZE
    return b"\x00" * padding
