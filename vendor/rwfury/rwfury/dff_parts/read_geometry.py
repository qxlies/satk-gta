from __future__ import annotations

import struct

from ..rwbinary import (
    RwBinaryReader,
    RW_STRUCT,
    RW_EXTENSION,
    RW_MATERIAL_LIST,
    RW_GEOMETRY,
    RW_BIN_MESH,
    RW_SKIN,
    RW_MAT_EFFECTS,
    RW_2DFX,
    RW_NIGHT_COLORS,
    GEO_TEXTURED,
    GEO_PRELIT,
    GEO_TEXTURED2,
    GEO_NATIVE,
)
from .models import (
    BinMeshPLG,
    BinMeshSplit,
    DffGeometry,
    Effect2dfxEntry,
    Effect2dfxLight,
    Effect2dfxParticle,
    MorphTarget,
    SkinPLG,
    rw_version_raw,
)


class DffGeometryReaderMixin:
    def _parse_geometry_list(self, reader: RwBinaryReader, chunk_end: int):
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT
        num_geometries = reader.read_u32()

        for _ in range(num_geometries):
            if not self._can_read_chunk_header(reader, chunk_end):
                break
            geom_header = reader.read_chunk_header()
            if geom_header.id == RW_GEOMETRY:
                geom_end = self._bounded_chunk_end(reader, geom_header.size, chunk_end)
                self.geometries.append(self._parse_geometry(reader, geom_end, geom_header.version))
            else:
                reader.skip(geom_header.size)

    def _parse_geometry(self, reader: RwBinaryReader, chunk_end: int, version: int) -> DffGeometry:
        struct_h = reader.read_chunk_header()
        assert struct_h.id == RW_STRUCT

        geom = DffGeometry()
        geom.flags = reader.read_u16()
        geom.num_uv_sets = reader.read_u8()
        _native_flags = reader.read_u8()
        num_triangles = reader.read_u32()
        num_vertices = reader.read_u32()
        num_morph_targets = reader.read_u32()

        rw_ver = rw_version_raw(version)

        # Pre-RW 3.4: surface properties (ambient, specular, diffuse) are in geometry struct
        if rw_ver < 0x34000:
            _ambient = reader.read_f32()
            _specular = reader.read_f32()
            _diffuse = reader.read_f32()

        if geom.num_uv_sets == 0:
            if geom.flags & GEO_TEXTURED2:
                geom.num_uv_sets = 2
            elif geom.flags & GEO_TEXTURED:
                geom.num_uv_sets = 1

        is_native = bool(geom.flags & GEO_NATIVE)

        if not is_native:
            # Vertex colors
            if geom.flags & GEO_PRELIT:
                for _ in range(num_vertices):
                    geom.vertex_colors.append((
                        reader.read_u8(), reader.read_u8(),
                        reader.read_u8(), reader.read_u8(),
                    ))

            # Texcoords
            for _ in range(geom.num_uv_sets):
                uv_set = []
                for _ in range(num_vertices):
                    uv_set.append((reader.read_f32(), reader.read_f32()))
                geom.texcoord_sets.append(uv_set)

            # Triangles (RW stores: v2, v1, mat, v3)
            for _ in range(num_triangles):
                v2 = reader.read_u16()
                v1 = reader.read_u16()
                mat_id = reader.read_u16()
                v3 = reader.read_u16()
                geom.triangles.append((v1, v2, v3, mat_id))

        # Morph targets
        for mt_i in range(num_morph_targets):
            mt = MorphTarget()
            mt.bounding_sphere = (
                reader.read_f32(), reader.read_f32(),
                reader.read_f32(), reader.read_f32(),
            )
            has_positions = reader.read_u32()
            has_normals = reader.read_u32()

            if not is_native:
                if has_positions:
                    for _ in range(num_vertices):
                        mt.vertices.append((reader.read_f32(), reader.read_f32(), reader.read_f32()))
                if has_normals:
                    for _ in range(num_vertices):
                        mt.normals.append((reader.read_f32(), reader.read_f32(), reader.read_f32()))

            geom.morph_targets.append(mt)

            # First morph target populates the main geometry fields
            if mt_i == 0:
                geom.bounding_sphere = mt.bounding_sphere
                geom.vertices = mt.vertices
                geom.normals = mt.normals

        # Child chunks (material list + extension)
        while self._can_read_chunk_header(reader, chunk_end):
            child = reader.read_chunk_header()
            child_end = self._bounded_chunk_end(reader, child.size, chunk_end)

            if child.id == RW_MATERIAL_LIST:
                self._parse_material_list(reader, child_end, geom)
            elif child.id == RW_EXTENSION:
                self._parse_geometry_extension(reader, child_end, geom, num_vertices)
            else:
                reader.seek(child_end)

        return geom

    def _parse_geometry_extension(self, reader: RwBinaryReader, chunk_end: int,
                                  geom: DffGeometry, num_vertices: int):
        while self._can_read_chunk_header(reader, chunk_end):
            plugin = reader.read_chunk_header()
            plugin_end = self._bounded_chunk_end(reader, plugin.size, chunk_end)

            if plugin.id == RW_BIN_MESH:
                geom.bin_mesh = self._parse_bin_mesh(reader, plugin_end)
            elif plugin.id == RW_SKIN:
                geom.skin = self._parse_skin(reader, plugin_end, num_vertices)
            elif plugin.id == RW_NIGHT_COLORS:
                geom.night_colors = self._parse_night_colors(reader, plugin_end, num_vertices)
            elif plugin.id == RW_2DFX:
                geom.effects_2dfx = self._parse_2dfx(reader, plugin_end)
            elif plugin.id == RW_MAT_EFFECTS:
                reader.read_u32()  # enabled flag at geometry level, skip
                if reader.tell() < plugin_end:
                    reader.seek(plugin_end)
            else:
                reader.seek(plugin_end)

    def _parse_bin_mesh(self, reader: RwBinaryReader, chunk_end: int) -> BinMeshPLG:
        bm = BinMeshPLG()
        bm.flags = reader.read_u32()
        num_meshes = reader.read_u32()
        _total_indices = reader.read_u32()

        for _ in range(num_meshes):
            num_indices = reader.read_u32()
            mat_index = reader.read_u32()
            indices = [reader.read_u32() for _ in range(num_indices)]
            bm.splits.append(BinMeshSplit(material_index=mat_index, indices=indices))

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return bm

    def _parse_skin(self, reader: RwBinaryReader, chunk_end: int, num_vertices: int) -> SkinPLG:
        skin = SkinPLG()
        skin.num_bones = reader.read_u8()
        num_used = reader.read_u8()
        max_weights = reader.read_u8()
        _pad = reader.read_u8()

        skin.used_bone_indices = [reader.read_u8() for _ in range(num_used)]

        # Bone indices per vertex
        for _ in range(num_vertices):
            skin.bone_indices.append((
                reader.read_u8(), reader.read_u8(),
                reader.read_u8(), reader.read_u8(),
            ))

        # Weights per vertex
        for _ in range(num_vertices):
            skin.weights.append((
                reader.read_f32(), reader.read_f32(),
                reader.read_f32(), reader.read_f32(),
            ))

        # Inverse bone matrices (4x4, 16 floats each)
        for _ in range(skin.num_bones):
            mat = struct.unpack("<16f", reader.read_bytes(64))
            skin.inverse_matrices.append(mat)

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return skin

    def _parse_night_colors(self, reader: RwBinaryReader, chunk_end: int,
                            num_vertices: int) -> list[tuple[int, int, int, int]] | None:
        has_colors = reader.read_u32()
        if not has_colors:
            return None
        colors = []
        for _ in range(num_vertices):
            colors.append((reader.read_u8(), reader.read_u8(), reader.read_u8(), reader.read_u8()))
        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return colors

    def _parse_2dfx(self, reader: RwBinaryReader, chunk_end: int) -> list[Effect2dfxEntry]:
        entries: list[Effect2dfxEntry] = []
        num_entries = reader.read_u32()

        for _ in range(num_entries):
            if reader.tell() >= chunk_end:
                break
            px = reader.read_f32()
            py = reader.read_f32()
            pz = reader.read_f32()
            entry_type = reader.read_u32()
            data_size = reader.read_u32()
            data_start = reader.tell()

            entry = Effect2dfxEntry(position=(px, py, pz), entry_type=entry_type)

            if entry_type == 0 and data_size >= 76:  # Light
                light = Effect2dfxLight()
                light.color = (reader.read_u8(), reader.read_u8(), reader.read_u8(), reader.read_u8())
                light.corona_far_clip = reader.read_f32()
                light.pointlight_range = reader.read_f32()
                light.corona_size = reader.read_f32()
                light.shadow_size = reader.read_f32()
                light.corona_show_mode = reader.read_u8()
                light.corona_enable_reflection = reader.read_u8()
                light.corona_flare_type = reader.read_u8()
                light.shadow_color_multiplier = reader.read_u8()
                light.flags1 = reader.read_u8()
                light.corona_tex_name = reader.read_string(24)
                light.shadow_tex_name = reader.read_string(24)
                light.shadow_z_distance = reader.read_u8()
                light.flags2 = reader.read_u8()
                _pad = reader.read_u8()
                if data_size >= 80:
                    lx = reader.read_u8()
                    ly = reader.read_u8()
                    lz = reader.read_u8()
                    light.look_direction = (lx, ly, lz)
                    reader.skip(data_size - 79)  # remaining padding
                entry.data = light
            elif entry_type == 1:  # Particle
                entry.data = Effect2dfxParticle(effect_name=reader.read_string(min(data_size, 24)))
            else:
                entry.data = reader.read_bytes(data_size)

            reader.seek(data_start + data_size)
            entries.append(entry)

        if reader.tell() < chunk_end:
            reader.seek(chunk_end)
        return entries
