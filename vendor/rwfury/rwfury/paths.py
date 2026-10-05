"""GTA San Andreas `nodes*.dat` path file parser/writer."""

from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .paths_common import (
    DEFAULT_SECTION4_FILLER,
    DEFAULT_TRAILING_DATA,
    SECTION4_FILLER_SIZE,
    NaviLink,
    NaviNode,
    PathFileFormat,
    PathIntersectionFlag,
    PathLink,
    PathLinkRecord,
    PathNode,
    PathNodeFlag,
    PathNodeKind,
    PathTrafficLevel,
    normalize_path_format,
    pack_navi_node,
    pack_path_node,
    read_exact,
    read_intersection_flags,
    read_link,
    read_navi_node,
    read_path_node,
    _clamp_u8,
)
from .paths_fastman92 import (
    is_fastman92_path_data,
    parse_fastman92_path_file,
    write_fastman92_path_file,
)


@dataclass
class SaPathFile:
    """A single GTA SA `nodes*.dat` path file."""

    vehicle_nodes: list[PathNode] = field(default_factory=list)
    ped_nodes: list[PathNode] = field(default_factory=list)
    navi_nodes: list[NaviNode] = field(default_factory=list)
    links: list[PathLink] = field(default_factory=list)
    navi_links: list[NaviLink] = field(default_factory=list)
    link_lengths: list[int] = field(default_factory=list)
    intersection_flags: list[int | PathIntersectionFlag] = field(default_factory=list)
    section4_filler: bytes = DEFAULT_SECTION4_FILLER
    trailing_data: bytes = DEFAULT_TRAILING_DATA
    area_id: int | None = None
    source_format: PathFileFormat = PathFileFormat.SA_NATIVE
    fastman92_version: str = ""
    fastman92_author: bytes = b""
    legacy_path_positions: list[tuple[float, float, float]] = field(default_factory=list)
    legacy_navi_positions: list[tuple[float, float]] = field(default_factory=list)
    extended_path_positions: list[tuple[float, float, float]] = field(default_factory=list)
    extended_navi_positions: list[tuple[float, float]] = field(default_factory=list)
    sector_padding: bytes = b""

    @classmethod
    def from_file(
        cls,
        path: str,
        *,
        area_id: int | None = None,
        format: PathFileFormat | str = PathFileFormat.AUTO,
    ) -> SaPathFile:
        with open(path, "rb") as f:
            data = f.read()
        if area_id is None:
            area_id = cls.area_id_from_filename(path)
        return cls.from_bytes(data, area_id=area_id, format=format)

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        area_id: int | None = None,
        format: PathFileFormat | str = PathFileFormat.AUTO,
    ) -> SaPathFile:
        path_format = normalize_path_format(format)
        if path_format == PathFileFormat.AUTO:
            if is_fastman92_path_data(data):
                return parse_fastman92_path_file(
                    data, cls, area_id=area_id, format=PathFileFormat.AUTO
                )
            path_format = PathFileFormat.SA_NATIVE

        if path_format in (PathFileFormat.FASTMAN92_VER2, PathFileFormat.FASTMAN92_VER3):
            return parse_fastman92_path_file(
                data, cls, area_id=area_id, format=path_format
            )
        if path_format != PathFileFormat.SA_NATIVE:
            raise ValueError(f"Unsupported path file format: {path_format}")
        return cls._from_native_bytes(data, area_id=area_id)

    @classmethod
    def _from_native_bytes(cls, data: bytes, area_id: int | None = None) -> SaPathFile:
        stream = io.BytesIO(data)
        if len(data) < 20:
            raise ValueError("SA path file is too small to contain a header")

        total_nodes, vehicle_count, ped_count, navi_count, link_count = struct.unpack(
            "<5I", read_exact(stream, 20)
        )
        if total_nodes != vehicle_count + ped_count:
            raise ValueError(
                "SA path header node count does not match vehicle + ped counts"
            )

        path_file = cls(area_id=area_id, source_format=PathFileFormat.SA_NATIVE)
        for _ in range(vehicle_count):
            path_file.vehicle_nodes.append(read_path_node(stream, PathNodeKind.VEHICLE))
        for _ in range(ped_count):
            path_file.ped_nodes.append(read_path_node(stream, PathNodeKind.PED))
        for _ in range(navi_count):
            path_file.navi_nodes.append(read_navi_node(stream))
        for _ in range(link_count):
            path_file.links.append(read_link(stream))

        path_file.section4_filler = read_exact(stream, SECTION4_FILLER_SIZE)

        for _ in range(link_count):
            packed = struct.unpack("<H", read_exact(stream, 2))[0]
            path_file.navi_links.append(NaviLink.from_packed(packed))
        path_file.link_lengths = list(read_exact(stream, link_count))
        path_file.intersection_flags = read_intersection_flags(stream, link_count)
        path_file.trailing_data = stream.read()
        return path_file

    @staticmethod
    def area_id_from_filename(path: str) -> int | None:
        match = re.search(r"nodes(\d+)\.dat$", Path(path).name, re.IGNORECASE)
        return int(match.group(1)) if match else None

    @staticmethod
    def area_origin(area_id: int, tile_size: float = 750.0) -> tuple[float, float]:
        if not 0 <= area_id < 64:
            raise ValueError("GTA SA node area_id must be in range 0..63")
        return (
            -3000.0 + (area_id % 8) * tile_size,
            -3000.0 + (area_id // 8) * tile_size,
        )

    @property
    def nodes(self) -> list[PathNode]:
        return [*self.vehicle_nodes, *self.ped_nodes]

    @property
    def node_count(self) -> int:
        return len(self.vehicle_nodes) + len(self.ped_nodes)

    @property
    def link_count(self) -> int:
        return len(self.links)

    def get_link_records(self) -> list[PathLinkRecord]:
        self.validate()
        return [
            PathLinkRecord(
                link=self.links[i],
                navi_link=self.navi_links[i],
                length=self.link_lengths[i],
                intersection_flags=PathIntersectionFlag(self.intersection_flags[i]),
            )
            for i in range(len(self.links))
        ]

    def links_for_node(self, node: PathNode) -> list[PathLinkRecord]:
        records = self.get_link_records()
        start = node.link_id
        end = start + node.link_count
        if start > len(records) or end > len(records):
            raise ValueError("node link range points outside the link table")
        return records[start:end]

    def validate(self):
        link_count = len(self.links)
        if len(self.navi_links) != link_count:
            raise ValueError("navi_links length must match links length")
        if len(self.link_lengths) != link_count:
            raise ValueError("link_lengths length must match links length")
        if len(self.intersection_flags) != link_count:
            raise ValueError("intersection_flags length must match links length")
        if self.section4_filler and len(self.section4_filler) != SECTION4_FILLER_SIZE:
            raise ValueError("section4_filler must be exactly 768 bytes")
        for node in self.nodes:
            start = node.link_id
            end = start + node.link_count
            if start > link_count or end > link_count:
                raise ValueError("node link range points outside the link table")

    def to_file(
        self,
        path: str,
        *,
        format: PathFileFormat | str = PathFileFormat.SA_NATIVE,
    ):
        with open(path, "wb") as f:
            f.write(self.to_bytes(format=format))

    def to_bytes(
        self,
        *,
        format: PathFileFormat | str = PathFileFormat.SA_NATIVE,
    ) -> bytes:
        path_format = normalize_path_format(format)
        if path_format == PathFileFormat.AUTO:
            path_format = self.source_format
        if path_format in (PathFileFormat.FASTMAN92_VER2, PathFileFormat.FASTMAN92_VER3):
            return write_fastman92_path_file(self, path_format)
        if path_format != PathFileFormat.SA_NATIVE:
            raise ValueError(f"Unsupported path file format: {path_format}")
        return self._to_native_bytes()

    def _to_native_bytes(self) -> bytes:
        self.validate()
        out = bytearray()
        out += struct.pack(
            "<5I",
            self.node_count,
            len(self.vehicle_nodes),
            len(self.ped_nodes),
            len(self.navi_nodes),
            len(self.links),
        )

        for node in self.vehicle_nodes:
            out += pack_path_node(node, PathNodeKind.VEHICLE)
        for node in self.ped_nodes:
            out += pack_path_node(node, PathNodeKind.PED)
        for node in self.navi_nodes:
            out += pack_navi_node(node)
        for link in self.links:
            out += struct.pack("<HH", link.area_id, link.node_id)

        out += self.section4_filler or DEFAULT_SECTION4_FILLER

        for navi_link in self.navi_links:
            out += struct.pack("<H", navi_link.to_packed())
        out += bytes(_clamp_u8(length) for length in self.link_lengths)
        out += bytes(_clamp_u8(int(flags)) for flags in self.intersection_flags)
        out += self.trailing_data if self.trailing_data else DEFAULT_TRAILING_DATA
        return bytes(out)


SaPaths = SaPathFile
