from __future__ import annotations

from ..rwbinary import (
    RwBinaryReader,
    RW_STRUCT,
    RW_STRING,
    RW_EXTENSION,
    RW_TEXTURE,
    RW_MATERIAL,
    RW_MAT_EFFECTS,
    RW_SPECULAR_MAT,
    RW_REFLECTION_MAT,
    RW_UV_ANIM_PLG,
)
from .models import DffGeometry, DffMaterial, DffUvAnimationRef


class DffMaterialReaderMixin:
    def _parse_material_list(self, reader: RwBinaryReader, chunk_end: int, geom: DffGeometry):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        num_materials = reader.read_u32()
        for _ in range(num_materials):
            reader.read_i32()

        for _ in range(num_materials):
            if not self._can_read_chunk_header(reader, chunk_end):
                break
            mat_header = reader.read_chunk_header()
            if mat_header.id == RW_MATERIAL:
                mat_end = self._bounded_chunk_end(reader, mat_header.size, chunk_end)
                geom.materials.append(self._parse_material(reader, mat_end))
            else:
                reader.skip(mat_header.size)

    def _parse_material(self, reader: RwBinaryReader, chunk_end: int) -> DffMaterial:
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        mat = DffMaterial()
        _flags = reader.read_u32()
        mat.color = (reader.read_u8(), reader.read_u8(), reader.read_u8(), reader.read_u8())
        _unused = reader.read_u32()
        is_textured = reader.read_u32()
        mat.ambient = reader.read_f32()
        mat.specular = reader.read_f32()
        mat.diffuse = reader.read_f32()

        # Read remaining child chunks
        while self._can_read_chunk_header(reader, chunk_end):
            child = reader.read_chunk_header()
            child_end = self._bounded_chunk_end(reader, child.size, chunk_end)

            if child.id == RW_TEXTURE:
                self._parse_texture_ref(reader, child_end, mat)
            elif child.id == RW_EXTENSION:
                self._parse_material_extension(reader, child_end, mat)
            else:
                reader.seek(child_end)

        return mat

    def _parse_material_extension(self, reader: RwBinaryReader, chunk_end: int, mat: DffMaterial):
        while self._can_read_chunk_header(reader, chunk_end):
            plugin = reader.read_chunk_header()
            plugin_end = self._bounded_chunk_end(reader, plugin.size, chunk_end)

            if plugin.id == RW_MAT_EFFECTS:
                mat.mat_fx_type = reader.read_u32()
                mat.mat_fx_data = self._parse_mat_fx_data(reader, plugin_end, mat.mat_fx_type)
            elif plugin.id == RW_SPECULAR_MAT:
                mat.specular_level = reader.read_f32()
                mat.specular_texture = reader.read_string(24)
            elif plugin.id == RW_REFLECTION_MAT:
                mat.reflection = {
                    "scale_x": reader.read_f32(),
                    "scale_y": reader.read_f32(),
                    "offset_x": reader.read_f32(),
                    "offset_y": reader.read_f32(),
                    "intensity": reader.read_f32(),
                }
                reader.read_u32()  # env_tex_ptr (always 0)
            elif plugin.id == RW_UV_ANIM_PLG:
                mat.uv_animations = self._parse_uv_animation_plg(reader, plugin_end)
            else:
                reader.seek(plugin_end)

    def _parse_mat_fx_data(self, reader: RwBinaryReader, chunk_end: int, fx_type: int) -> dict:
        data: dict = {}

        def read_effect_slot():
            slot_type = reader.read_u32()
            slot: dict = {"type": slot_type}

            if slot_type == 1:  # Bump map
                slot["intensity"] = reader.read_f32()
                slot["has_bump_map"] = reader.read_u32()
                if slot["has_bump_map"]:
                    slot["bump_map"] = self._parse_inline_texture(reader)
                slot["has_height_map"] = reader.read_u32()
                if slot["has_height_map"]:
                    slot["height_map"] = self._parse_inline_texture(reader)
            elif slot_type == 2:  # Env map
                slot["reflection_coeff"] = reader.read_f32()
                slot["use_fb_alpha"] = reader.read_u32()
                slot["has_env_map"] = reader.read_u32()
                if slot["has_env_map"]:
                    slot["env_map"] = self._parse_inline_texture(reader)
            elif slot_type == 4:  # Dual texture
                slot["src_blend"] = reader.read_i32()
                slot["dst_blend"] = reader.read_i32()
                slot["has_texture"] = reader.read_u32()
                if slot["has_texture"]:
                    slot["texture"] = self._parse_inline_texture(reader)
            elif slot_type == 5:  # UV transform
                pass  # No additional data
            elif slot_type == 0:  # NULL
                pass

            return slot

        data["effect1"] = read_effect_slot()

        if reader.tell() < chunk_end:
            data["effect2"] = read_effect_slot()

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return data

    def _parse_inline_texture(self, reader: RwBinaryReader) -> dict:
        """Parse an inline RwTexture chunk embedded in material effects."""
        tex_h = reader.read_chunk_header()
        tex_end = self._bounded_chunk_end(reader, tex_h.size)
        tex: dict = {"name": "", "mask": ""}

        while self._can_read_chunk_header(reader, tex_end):
            child = reader.read_chunk_header()
            child_end = self._bounded_chunk_end(reader, child.size, tex_end)

            if child.id == RW_STRUCT:
                tex["filter"] = reader.read_u32()
            elif child.id == RW_STRING:
                if not tex["name"]:
                    tex["name"] = reader.read_string(child.size)
                else:
                    tex["mask"] = reader.read_string(child.size)
            else:
                reader.seek(child_end)

        reader.seek(tex_end)
        return tex

    def _parse_texture_ref(self, reader: RwBinaryReader, chunk_end: int, mat: DffMaterial):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT
        _filter_flags = reader.read_u16()
        _unknown = reader.read_u16()

        name_h = reader.read_chunk_header()
        if name_h.id == RW_STRING:
            mat.texture_name = reader.read_string(name_h.size)
        else:
            reader.skip(name_h.size)

        if reader.tell() < chunk_end:
            mask_h = reader.read_chunk_header()
            if mask_h.id == RW_STRING:
                mat.mask_name = reader.read_string(mask_h.size)
            else:
                reader.skip(mask_h.size)

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)

    def _parse_uv_animation_plg(
        self, reader: RwBinaryReader, chunk_end: int
    ) -> list[DffUvAnimationRef]:
        if reader.tell() >= chunk_end:
            return []

        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT
        struct_end = self._bounded_chunk_end(reader, struct_h.size, chunk_end)

        channel_mask = reader.read_u32()
        refs: list[DffUvAnimationRef] = []
        for channel in range(8):
            if channel_mask & (1 << channel):
                refs.append(DffUvAnimationRef(
                    channel=channel,
                    name=reader.read_string(32),
                ))

        if reader.tell() < struct_end:
            reader.seek(struct_end)
        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return refs
