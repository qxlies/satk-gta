"""DFF (RenderWare Clump) parser/writer facade."""

from __future__ import annotations

from .dff_parts.api import DffApiMixin
from .dff_parts.reader import DffReaderMixin
from .dff_parts.writer import DffWriterMixin
from .dff_parts.models import (
    CollisionData,
    DffAtomic,
    DffFrame,
    DffGeometry,
    DffLight,
    DffUvAnimation,
)
from .rwbinary import RwBinaryReader


class Dff(DffApiMixin, DffReaderMixin, DffWriterMixin):
    """DFF (RenderWare Clump) parser with full plugin support."""

    def __init__(self):
        self.frames: list[DffFrame] = []
        self.geometries: list[DffGeometry] = []
        self.atomics: list[DffAtomic] = []
        self.lights: list[DffLight] = []
        self.uv_animations: list[DffUvAnimation] = []
        self.collision: CollisionData | None = None
        self.rw_version: int = 0

    @classmethod
    def from_file(cls, path: str) -> Dff:
        dff = cls()
        with RwBinaryReader.from_file(path) as reader:
            dff._parse(reader)
        return dff

    @classmethod
    def from_bytes(cls, data: bytes) -> Dff:
        """Parse a DFF from raw bytes (e.g. read from an IMG archive)."""
        import io
        dff = cls()
        reader = RwBinaryReader(io.BytesIO(data))
        dff._parse(reader)
        return dff
