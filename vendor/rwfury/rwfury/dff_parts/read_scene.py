from __future__ import annotations

import struct

from ..rwbinary import (
    RwBinaryReader,
    RW_STRUCT,
    RW_EXTENSION,
    RW_FRAME_NAME,
    RW_HANIM,
)
from .models import DffAtomic, DffFrame, DffLight, HAnimBone, HAnimPLG


class DffSceneReaderMixin:
    def _parse_light(self, reader: RwBinaryReader, chunk_end: int) -> DffLight:
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT
        struct_end = self._bounded_chunk_end(reader, struct_h.size, chunk_end)

        light = DffLight()
        if struct_h.size >= 28:
            light.frame_index = reader.read_u32()
            light.radius = reader.read_f32()
            light.color = (reader.read_f32(), reader.read_f32(), reader.read_f32())
            light.cone_angle = reader.read_f32()
            light.flags = reader.read_u16()
            light.light_type = reader.read_u16()

        if reader.tell() < struct_end:
            reader.seek(struct_end)

        while self._can_read_chunk_header(reader, chunk_end):
            child = reader.read_chunk_header()
            child_end = self._bounded_chunk_end(reader, child.size, chunk_end)

            if child.id == RW_EXTENSION:
                light.extension_data = reader.read_bytes(child.size)
            else:
                light.extra_chunks.append((child.id, reader.read_bytes(child.size)))

        return light

    def _parse_frame_list(self, reader: RwBinaryReader, chunk_end: int):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        num_frames = reader.read_u32()

        for _ in range(num_frames):
            rot = struct.unpack("<9f", reader.read_bytes(36))
            pos = struct.unpack("<3f", reader.read_bytes(12))
            parent_idx = reader.read_i32()
            _flags = reader.read_u32()
            self.frames.append(DffFrame(rotation_matrix=rot, position=pos, parent=parent_idx))

        for i in range(num_frames):
            if reader.tell() >= chunk_end:
                break
            ext = reader.read_chunk_header()
            if ext.id != RW_EXTENSION:
                reader.seek(reader.tell() - 12 + ext.size)
                continue
            ext_end = self._bounded_chunk_end(reader, ext.size, chunk_end)

            while self._can_read_chunk_header(reader, ext_end):
                plugin = reader.read_chunk_header()
                plugin_end = self._bounded_chunk_end(reader, plugin.size, ext_end)

                if plugin.id == RW_FRAME_NAME and plugin.size > 0:
                    self.frames[i].name = reader.read_string(plugin.size)
                elif plugin.id == RW_HANIM:
                    self.frames[i].hanim = self._parse_hanim(reader, plugin_end)
                else:
                    reader.seek(plugin_end)

    def _parse_hanim(self, reader: RwBinaryReader, chunk_end: int) -> HAnimPLG:
        hanim = HAnimPLG()
        hanim.version = reader.read_u32()
        hanim.node_id = reader.read_u32()
        num_nodes = reader.read_u32()

        if num_nodes > 0:
            hanim.hierarchy_flags = reader.read_u32()
            hanim.key_frame_size = reader.read_u32()
            for _ in range(num_nodes):
                bone = HAnimBone(
                    node_id=reader.read_u32(),
                    node_index=reader.read_u32(),
                    flags=reader.read_u32(),
                )
                hanim.bones.append(bone)

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return hanim

    def _parse_atomic(self, reader: RwBinaryReader, chunk_end: int):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        self.atomics.append(DffAtomic(
            frame_index=reader.read_u32(),
            geometry_index=reader.read_u32(),
            flags=reader.read_u32(),
        ))
        reader.read_u32()  # unused

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
