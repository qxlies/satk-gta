from __future__ import annotations

from ..rwbinary import (
    RW_ANIM_ANIMATION,
    RwBinaryReader,
    RW_STRUCT,
    RW_EXTENSION,
    RW_FRAME_LIST,
    RW_GEOMETRY_LIST,
    RW_CLUMP,
    RW_LIGHT,
    RW_ATOMIC,
    RW_COLLISION,
    RW_UV_ANIM_DICT,
)
from .models import CollisionData, DffUvAnimation, DffUvAnimationFrame
from .read_geometry import DffGeometryReaderMixin
from .read_material import DffMaterialReaderMixin
from .read_scene import DffSceneReaderMixin


class DffReaderMixin(DffSceneReaderMixin, DffGeometryReaderMixin, DffMaterialReaderMixin):
    def _bounded_chunk_end(
        self, reader: RwBinaryReader, size: int, parent_end: int | None = None
    ) -> int:
        chunk_end = reader.tell() + max(0, size)
        if parent_end is not None:
            chunk_end = min(chunk_end, parent_end)
        return min(chunk_end, reader.file_size)

    def _can_read_chunk_header(self, reader: RwBinaryReader, chunk_end: int) -> bool:
        return reader.tell() + 12 <= min(chunk_end, reader.file_size)

    def _parse(self, reader: RwBinaryReader):
        found_clump = False
        while reader.tell() + 12 <= reader.file_size:
            header = reader.read_chunk_header()
            chunk_end = self._bounded_chunk_end(reader, header.size)

            if header.id == RW_UV_ANIM_DICT:
                self._parse_uv_animation_dict(reader, chunk_end)
            elif header.id == RW_CLUMP:
                self._parse_clump(reader, header, chunk_end)
                found_clump = True
            else:
                reader.seek(chunk_end)

        if not found_clump:
            raise ValueError(f"Not a DFF: expected top-level 0x{RW_CLUMP:04X} chunk")

    def _parse_clump(self, reader: RwBinaryReader, header, clump_end: int):
        clump_end = self._bounded_chunk_end(reader, header.size)
        self.rw_version = header.version

        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT
        _num_atomics = reader.read_u32()
        if struct_h.size >= 12:
            _num_lights = reader.read_u32()
            reader.read_u32()  # num_cameras
        elif struct_h.size > 4:
            reader.skip(struct_h.size - 4)

        while self._can_read_chunk_header(reader, clump_end):
            child = reader.read_chunk_header()
            child_end = self._bounded_chunk_end(reader, child.size, clump_end)

            if child.id == RW_FRAME_LIST:
                self._parse_frame_list(reader, child_end)
            elif child.id == RW_GEOMETRY_LIST:
                self._parse_geometry_list(reader, child_end)
            elif child.id == RW_ATOMIC:
                self._parse_atomic(reader, child_end)
            elif child.id == RW_LIGHT:
                self.lights.append(self._parse_light(reader, child_end))
            elif child.id == RW_EXTENSION:
                self._parse_clump_extension(reader, child_end)
            else:
                reader.seek(child_end)

    def _parse_clump_extension(self, reader: RwBinaryReader, chunk_end: int):
        while self._can_read_chunk_header(reader, chunk_end):
            plugin = reader.read_chunk_header()
            plugin_end = self._bounded_chunk_end(reader, plugin.size, chunk_end)

            if plugin.id == RW_COLLISION:
                self.collision = CollisionData(raw=reader.read_bytes(plugin.size))
            else:
                reader.seek(plugin_end)

    def _parse_uv_animation_dict(self, reader: RwBinaryReader, chunk_end: int):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        animation_count = reader.read_u32()
        struct_end = self._bounded_chunk_end(reader, struct_h.size - 4, chunk_end)
        if reader.tell() < struct_end:
            reader.seek(struct_end)

        for _ in range(animation_count):
            if reader.tell() >= chunk_end:
                break
            anim_header = reader.read_chunk_header()
            anim_end = self._bounded_chunk_end(reader, anim_header.size, chunk_end)

            if anim_header.id == RW_ANIM_ANIMATION:
                self.uv_animations.append(self._parse_uv_animation(reader, anim_end))
            else:
                reader.seek(anim_end)

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)

    def _parse_uv_animation(self, reader: RwBinaryReader, chunk_end: int) -> DffUvAnimation:
        animation = DffUvAnimation(
            version=reader.read_u32(),
            animation_type=reader.read_u32(),
            declared_frame_count=reader.read_u32(),
            flags=reader.read_u32(),
            duration=reader.read_f32(),
        )
        frame_count = animation.declared_frame_count

        if animation.animation_type == 0x1C1:
            animation.unknown = reader.read_i32()
            animation.name = reader.read_string(32)
            animation.node_to_uv = tuple(reader.read_f32() for _ in range(8))
            for _ in range(frame_count):
                animation.frames.append(DffUvAnimationFrame(
                    time=reader.read_f32(),
                    scale=(reader.read_f32(), reader.read_f32(), reader.read_f32()),
                    position=(reader.read_f32(), reader.read_f32(), reader.read_f32()),
                    previous_frame=reader.read_i32(),
                ))
        else:
            animation.raw_data = reader.read_bytes(chunk_end - reader.tell())

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return animation
