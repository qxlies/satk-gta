"""Shared models and helpers for GTA SA path file formats."""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from enum import Enum, IntEnum, IntFlag


PATH_NODE_SIZE = 28
NAVI_NODE_SIZE = 14
LINK_SIZE = 4
SECTION4_FILLER_SIZE = 768
DEFAULT_SECTION4_FILLER = b"\xff\xff\x00\x00" * 192
# Original Steam GTA SA nodes*.dat files carry 384 bytes after section 7.
DEFAULT_TRAILING_DATA = b"\x00" * 384
POSITION_SCALE = 8.0
NAVI_DIRECTION_SCALE = 100.0
NAVI_NODE_ID_MASK = 0x03FF
MAX_PATH_RECORDS = 2_000_000
PATH_NODE_BEHAVIOR_FLAG_MASK = (
    (1 << 6) | (1 << 7) | (1 << 8) | (1 << 10) |
    (1 << 12) | (1 << 13) | (1 << 20) | (1 << 21) | (1 << 23)
)


class PathFileFormat(Enum):
    AUTO = "auto"
    SA_NATIVE = "sa_native"
    FASTMAN92_VER2 = "fastman92_ver2"
    FASTMAN92_VER3 = "fastman92_ver3"


class PathNodeKind(IntEnum):
    VEHICLE = 0
    PED = 1


class PathTrafficLevel(IntEnum):
    FULL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class PathNodeFlag(IntFlag):
    ROAD_BLOCKS = 1 << 6
    BOATS = 1 << 7
    EMERGENCY_VEHICLES_ONLY = 1 << 8
    UNKNOWN_GROVE_HOUSE_ENTRANCE = 1 << 10
    IS_NOT_HIGHWAY = 1 << 12
    IS_HIGHWAY = 1 << 13
    ROAD_BLOCK = 1 << 20
    PARKING = 1 << 21
    ROAD_BLOCK_ALT = 1 << 23


class PathIntersectionFlag(IntFlag):
    ROAD_CROSS = 1 << 0
    PED_TRAFFIC_LIGHT = 1 << 1


@dataclass
class PathNode:
    """Vehicle or pedestrian path graph node from section 1."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    link_id: int = 0
    area_id: int = 0
    node_id: int = 0
    path_width: int = 0
    flood_fill: int = 0
    flags: int = 0
    heuristic_cost: int = 0x7FFE
    mem_address: int = 0
    zero: int = 0
    kind: PathNodeKind = PathNodeKind.VEHICLE

    @property
    def link_count(self) -> int:
        return self.flags & 0x0F

    @link_count.setter
    def link_count(self, value: int):
        if not 0 <= value <= 0x0F:
            raise ValueError("link_count must fit in 4 bits")
        self.flags = (self.flags & ~0x0F) | value

    @property
    def traffic_level(self) -> PathTrafficLevel:
        return PathTrafficLevel((self.flags >> 4) & 0x03)

    @traffic_level.setter
    def traffic_level(self, value: int | PathTrafficLevel):
        value = int(value)
        if not 0 <= value <= 0x03:
            raise ValueError("traffic_level must fit in 2 bits")
        self.flags = (self.flags & ~(0x03 << 4)) | (value << 4)

    @property
    def behavior_flags(self) -> PathNodeFlag:
        return PathNodeFlag(self.flags & PATH_NODE_BEHAVIOR_FLAG_MASK)

    @property
    def spawn_probability(self) -> int:
        return (self.flags >> 16) & 0x0F

    @spawn_probability.setter
    def spawn_probability(self, value: int):
        if not 0 <= value <= 0x0F:
            raise ValueError("spawn_probability must fit in 4 bits")
        self.flags = (self.flags & ~(0x0F << 16)) | (value << 16)

    @property
    def width(self) -> float:
        return self.path_width / POSITION_SCALE

    @width.setter
    def width(self, value: float):
        self.path_width = _clamp_u8(round(value * POSITION_SCALE))

    @property
    def is_vehicle(self) -> bool:
        return self.kind == PathNodeKind.VEHICLE

    @property
    def is_ped(self) -> bool:
        return self.kind == PathNodeKind.PED


@dataclass
class NaviNode:
    """Vehicle navigation node from section 2."""

    position: tuple[float, float] = (0.0, 0.0)
    area_id: int = 0
    node_id: int = 0
    direction: tuple[float, float] = (0.0, 0.0)
    flags: int = 0

    @property
    def path_width(self) -> int:
        return self.flags & 0xFF

    @path_width.setter
    def path_width(self, value: int):
        if not 0 <= value <= 0xFF:
            raise ValueError("path_width must fit in 8 bits")
        self.flags = (self.flags & ~0xFF) | value

    @property
    def width(self) -> float:
        return self.path_width / POSITION_SCALE

    @width.setter
    def width(self, value: float):
        self.path_width = _clamp_u8(round(value * POSITION_SCALE))

    @property
    def left_lanes(self) -> int:
        return (self.flags >> 8) & 0x07

    @left_lanes.setter
    def left_lanes(self, value: int):
        if not 0 <= value <= 0x07:
            raise ValueError("left_lanes must fit in 3 bits")
        self.flags = (self.flags & ~(0x07 << 8)) | (value << 8)

    @property
    def right_lanes(self) -> int:
        return (self.flags >> 11) & 0x07

    @right_lanes.setter
    def right_lanes(self, value: int):
        if not 0 <= value <= 0x07:
            raise ValueError("right_lanes must fit in 3 bits")
        self.flags = (self.flags & ~(0x07 << 11)) | (value << 11)

    @property
    def traffic_light_direction(self) -> bool:
        return bool(self.flags & (1 << 14))

    @traffic_light_direction.setter
    def traffic_light_direction(self, value: bool):
        self.flags = _set_bool_bit(self.flags, 14, value)

    @property
    def traffic_light_behavior(self) -> int:
        return (self.flags >> 16) & 0x03

    @traffic_light_behavior.setter
    def traffic_light_behavior(self, value: int):
        if not 0 <= value <= 0x03:
            raise ValueError("traffic_light_behavior must fit in 2 bits")
        self.flags = (self.flags & ~(0x03 << 16)) | (value << 16)

    @property
    def train_crossing(self) -> bool:
        return bool(self.flags & (1 << 18))

    @train_crossing.setter
    def train_crossing(self, value: bool):
        self.flags = _set_bool_bit(self.flags, 18, value)


@dataclass
class PathLink:
    area_id: int = 0
    node_id: int = 0


@dataclass
class NaviLink:
    area_id: int = 0
    node_id: int = 0

    @classmethod
    def from_packed(cls, value: int) -> NaviLink:
        return cls(area_id=(value >> 10) & 0x3F, node_id=value & NAVI_NODE_ID_MASK)

    def to_packed(self) -> int:
        if not 0 <= self.area_id <= 0x3F:
            raise ValueError("navi link area_id must fit in 6 bits")
        if not 0 <= self.node_id <= NAVI_NODE_ID_MASK:
            raise ValueError("navi link node_id must fit in 10 bits")
        return (self.area_id << 10) | self.node_id


@dataclass
class PathLinkRecord:
    """Combined view over sections 3, 5, 6, and 7 for one link index."""

    link: PathLink
    navi_link: NaviLink
    length: int
    intersection_flags: PathIntersectionFlag


def read_exact(stream: io.BytesIO, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise EOFError(f"Expected {count} bytes, got {len(data)}")
    return data


def read_exact_labeled(stream: io.BytesIO, count: int, label: str) -> bytes:
    try:
        return read_exact(stream, count)
    except EOFError as exc:
        raise EOFError(f"{label}: {exc}") from exc


def normalize_path_format(format: PathFileFormat | str) -> PathFileFormat:
    if isinstance(format, PathFileFormat):
        return format
    return PathFileFormat(format)


def validate_count(label: str, count: int):
    if count > MAX_PATH_RECORDS:
        raise ValueError(f"{label} is too large: {count}")


def read_intersection_flags(
    stream: io.BytesIO, link_count: int
) -> list[PathIntersectionFlag]:
    """Read section 7, tolerating path files that omit it entirely."""
    remaining = len(stream.getbuffer()) - stream.tell()
    if remaining < link_count:
        return [PathIntersectionFlag(0) for _ in range(link_count)]
    return [PathIntersectionFlag(value) for value in read_exact(stream, link_count)]


def read_path_node(stream: io.BytesIO, kind: PathNodeKind) -> PathNode:
    data = read_exact(stream, PATH_NODE_SIZE)
    mem_address, zero, x, y, z, heuristic_cost, link_id, area_id, node_id, path_width, flood_fill, flags = struct.unpack(
        "<II3hhHHHBBI", data
    )
    return PathNode(
        position=(x / POSITION_SCALE, y / POSITION_SCALE, z / POSITION_SCALE),
        link_id=link_id,
        area_id=area_id,
        node_id=node_id,
        path_width=path_width,
        flood_fill=flood_fill,
        flags=flags,
        heuristic_cost=heuristic_cost,
        mem_address=mem_address,
        zero=zero,
        kind=kind,
    )


def pack_path_node(node: PathNode, kind: PathNodeKind) -> bytes:
    return pack_path_node_with_position(node, kind, node.position)


def pack_path_node_with_position(
    node: PathNode,
    kind: PathNodeKind,
    position: tuple[float, float, float],
) -> bytes:
    x, y, z = (_to_i16(coord * POSITION_SCALE) for coord in position)
    return struct.pack(
        "<II3hhHHHBBI",
        node.mem_address,
        node.zero,
        x, y, z,
        _to_i16(node.heuristic_cost),
        _to_u16(node.link_id),
        _to_u16(node.area_id),
        _to_u16(node.node_id),
        _clamp_u8(node.path_width),
        _clamp_u8(node.flood_fill),
        int(node.flags),
    )


def read_navi_node(stream: io.BytesIO) -> NaviNode:
    x, y, area_id, node_id, dx, dy, flags = struct.unpack(
        "<2hHH2bI", read_exact(stream, NAVI_NODE_SIZE)
    )
    return NaviNode(
        position=(x / POSITION_SCALE, y / POSITION_SCALE),
        area_id=area_id,
        node_id=node_id,
        direction=(dx / NAVI_DIRECTION_SCALE, dy / NAVI_DIRECTION_SCALE),
        flags=flags,
    )


def pack_navi_node(node: NaviNode) -> bytes:
    return pack_navi_node_with_position(node, node.position)


def pack_navi_node_with_position(
    node: NaviNode,
    position: tuple[float, float],
) -> bytes:
    return struct.pack(
        "<2hHH2bI",
        _to_i16(position[0] * POSITION_SCALE),
        _to_i16(position[1] * POSITION_SCALE),
        _to_u16(node.area_id),
        _to_u16(node.node_id),
        _to_i8(node.direction[0] * NAVI_DIRECTION_SCALE),
        _to_i8(node.direction[1] * NAVI_DIRECTION_SCALE),
        int(node.flags),
    )


def read_link(stream: io.BytesIO) -> PathLink:
    area_id, node_id = struct.unpack("<HH", read_exact(stream, LINK_SIZE))
    return PathLink(area_id=area_id, node_id=node_id)


def set_bool_bit(flags: int, bit: int, value: bool) -> int:
    if value:
        return flags | (1 << bit)
    return flags & ~(1 << bit)


def _set_bool_bit(flags: int, bit: int, value: bool) -> int:
    return set_bool_bit(flags, bit, value)


def _clamp_u8(value: int) -> int:
    return max(0, min(255, int(value)))


def _to_i8(value: float) -> int:
    return max(-128, min(127, int(round(value))))


def _to_i16(value: float) -> int:
    return max(-32768, min(32767, int(round(value))))


def _to_i32(value: float) -> int:
    value = int(round(value))
    if not -2147483648 <= value <= 2147483647:
        raise ValueError("value must fit in signed 32 bits")
    return value


def _to_u16(value: int) -> int:
    return max(0, min(65535, int(value)))
