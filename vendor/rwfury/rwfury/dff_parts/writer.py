from __future__ import annotations

import io
import struct

from ..rwbinary import (
    RW_ANIM_ANIMATION,
    RwBinaryWriter,
    RW_STRUCT,
    RW_STRING,
    RW_EXTENSION,
    RW_TEXTURE,
    RW_MATERIAL,
    RW_MATERIAL_LIST,
    RW_FRAME_LIST,
    RW_GEOMETRY,
    RW_CLUMP,
    RW_LIGHT,
    RW_ATOMIC,
    RW_GEOMETRY_LIST,
    RW_FRAME_NAME,
    RW_BIN_MESH,
    RW_SKIN,
    RW_HANIM,
    RW_MAT_EFFECTS,
    RW_2DFX,
    RW_NIGHT_COLORS,
    RW_COLLISION,
    RW_SPECULAR_MAT,
    RW_REFLECTION_MAT,
    RW_UV_ANIM_DICT,
    RW_UV_ANIM_PLG,
    GEO_PRELIT,
    GEO_NATIVE,
)
from .models import (
    BinMeshPLG,
    DffAtomic,
    DffGeometry,
    DffLight,
    DffMaterial,
    DffUvAnimation,
    DffUvAnimationFrame,
    DffUvAnimationRef,
    Effect2dfxEntry,
    Effect2dfxLight,
    Effect2dfxParticle,
    HAnimPLG,
    MorphTarget,
    SkinPLG,
    rw_version_raw,
)


class DffWriterMixin:
    # -----------------------------------------------------------------------
    # Writing
    # -----------------------------------------------------------------------

    def _write(self, w: RwBinaryWriter):
        """Write the full DFF clump."""
        ver = self.rw_version or 0x1803FFFF

        if self.uv_animations:
            self._write_uv_animation_dict(w, ver)

        # We build the clump body in memory to know the size
        body_stream = io.BytesIO()
        body_w = RwBinaryWriter(body_stream)

        # Clump struct
        struct_data = struct.pack("<III", len(self.atomics), len(self.lights), 0)
        body_w.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        body_w.write_bytes(struct_data)

        # Frame list
        self._write_frame_list(body_w, ver)

        # Geometry list
        self._write_geometry_list(body_w, ver)

        # Atomics
        for atomic in self.atomics:
            self._write_atomic(body_w, ver, atomic)

        # Lights
        for light in self.lights:
            self._write_light(body_w, ver, light)

        # Clump extension
        ext_stream = io.BytesIO()
        ext_w = RwBinaryWriter(ext_stream)
        if self.collision and self.collision.raw:
            ext_w.write_chunk_header(RW_COLLISION, len(self.collision.raw), ver)
            ext_w.write_bytes(self.collision.raw)
        ext_data = ext_stream.getvalue()
        body_w.write_chunk_header(RW_EXTENSION, len(ext_data), ver)
        body_w.write_bytes(ext_data)

        # Write clump header + body
        body_data = body_stream.getvalue()
        w.write_chunk_header(RW_CLUMP, len(body_data), ver)
        w.write_bytes(body_data)

    def _write_uv_animation_dict(self, w: RwBinaryWriter, ver: int):
        body_stream = io.BytesIO()
        bw = RwBinaryWriter(body_stream)

        struct_data = struct.pack("<I", len(self.uv_animations))
        bw.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        bw.write_bytes(struct_data)

        for animation in self.uv_animations:
            self._write_uv_animation(bw, ver, animation)

        body_data = body_stream.getvalue()
        w.write_chunk_header(RW_UV_ANIM_DICT, len(body_data), ver)
        w.write_bytes(body_data)

    def _write_uv_animation(self, w: RwBinaryWriter, ver: int, animation: DffUvAnimation):
        if animation.animation_type == 0x1C1 and not animation.raw_data:
            body = struct.pack(
                "<IIIIf",
                animation.version,
                animation.animation_type,
                animation.frame_count,
                animation.flags,
                animation.duration,
            )
            body += struct.pack("<i", animation.unknown)
            body += animation.name.encode("ascii", errors="replace").ljust(32, b"\x00")[:32]
            body += struct.pack("<8f", *animation.node_to_uv)
            for frame in animation.frames:
                body += struct.pack(
                    "<f3f3fi",
                    frame.time,
                    *frame.scale,
                    *frame.position,
                    frame.previous_frame,
                )
        else:
            body = struct.pack(
                "<IIIIf",
                animation.version,
                animation.animation_type,
                animation.frame_count,
                animation.flags,
                animation.duration,
            )
            body += animation.raw_data

        w.write_chunk_header(RW_ANIM_ANIMATION, len(body), ver)
        w.write_bytes(body)

    def _write_light(self, w: RwBinaryWriter, ver: int, light: DffLight):
        body_stream = io.BytesIO()
        bw = RwBinaryWriter(body_stream)

        r, g, b = light.color
        struct_data = struct.pack(
            "<IfffffHH",
            light.frame_index,
            light.radius,
            r, g, b,
            light.cone_angle,
            int(light.flags),
            int(light.light_type),
        )
        bw.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        bw.write_bytes(struct_data)
        bw.write_chunk_header(RW_EXTENSION, len(light.extension_data), ver)
        bw.write_bytes(light.extension_data)
        for chunk_id, data in light.extra_chunks:
            bw.write_chunk_header(chunk_id, len(data), ver)
            bw.write_bytes(data)

        body_data = body_stream.getvalue()
        w.write_chunk_header(RW_LIGHT, len(body_data), ver)
        w.write_bytes(body_data)

    def _write_frame_list(self, w: RwBinaryWriter, ver: int):
        num_frames = len(self.frames)
        # Struct: num_frames + 56 bytes per frame
        struct_data = struct.pack("<I", num_frames)
        for frame in self.frames:
            struct_data += struct.pack("<9f", *frame.rotation_matrix)
            struct_data += struct.pack("<3f", *frame.position)
            struct_data += struct.pack("<iI", frame.parent, 0)

        # Extensions per frame
        ext_chunks = b""
        for frame in self.frames:
            ext_body = b""
            # Frame name
            if frame.name:
                name_bytes = frame.name.encode("ascii", errors="replace")
                ext_body += struct.pack("<III", RW_FRAME_NAME, len(name_bytes), ver)
                ext_body += name_bytes
            # HAnim
            if frame.hanim:
                hanim_data = self._build_hanim_data(frame.hanim)
                ext_body += struct.pack("<III", RW_HANIM, len(hanim_data), ver)
                ext_body += hanim_data
            ext_chunks += struct.pack("<III", RW_EXTENSION, len(ext_body), ver)
            ext_chunks += ext_body

        body = struct.pack("<III", RW_STRUCT, len(struct_data), ver) + struct_data + ext_chunks
        w.write_chunk_header(RW_FRAME_LIST, len(body), ver)
        w.write_bytes(body)

    def _build_hanim_data(self, hanim: HAnimPLG) -> bytes:
        data = struct.pack("<III", hanim.version, hanim.node_id, len(hanim.bones))
        if hanim.bones:
            data += struct.pack("<II", hanim.hierarchy_flags, hanim.key_frame_size)
            for bone in hanim.bones:
                data += struct.pack("<III", bone.node_id, bone.node_index, bone.flags)
        return data

    def _write_geometry_list(self, w: RwBinaryWriter, ver: int):
        body_stream = io.BytesIO()
        body_w = RwBinaryWriter(body_stream)

        struct_data = struct.pack("<I", len(self.geometries))
        body_w.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        body_w.write_bytes(struct_data)

        for geom in self.geometries:
            self._write_geometry(body_w, ver, geom)

        body_data = body_stream.getvalue()
        w.write_chunk_header(RW_GEOMETRY_LIST, len(body_data), ver)
        w.write_bytes(body_data)

    def _write_geometry(self, w: RwBinaryWriter, ver: int, geom: DffGeometry):
        geom_stream = io.BytesIO()
        gw = RwBinaryWriter(geom_stream)

        # Struct
        num_verts = len(geom.vertices)
        num_tris = len(geom.triangles)
        num_morph = max(1, len(geom.morph_targets))

        struct_data = struct.pack("<HBBiii",
            geom.flags, geom.num_uv_sets, 0,
            num_tris, num_verts, num_morph)

        # Pre-RW 3.4: surface properties stored in geometry struct
        rw_ver = rw_version_raw(ver)
        if rw_ver < 0x34000:
            struct_data += struct.pack("<fff", 1.0, 1.0, 1.0)  # ambient, specular, diffuse

        is_native = bool(geom.flags & GEO_NATIVE)

        if not is_native:
            # Vertex colors
            if geom.flags & GEO_PRELIT:
                for r, g, b, a in geom.vertex_colors:
                    struct_data += struct.pack("<4B", r, g, b, a)
            # Texcoords
            for uv_set in geom.texcoord_sets:
                for u, v in uv_set:
                    struct_data += struct.pack("<ff", u, v)
            # Triangles (RW format: v2, v1, mat, v3)
            for v1, v2, v3, mat in geom.triangles:
                struct_data += struct.pack("<HHHH", v2, v1, mat, v3)

        # Morph targets
        targets = geom.morph_targets if geom.morph_targets else [
            MorphTarget(
                bounding_sphere=geom.bounding_sphere,
                vertices=geom.vertices,
                normals=geom.normals,
            )
        ]
        for mt in targets:
            has_pos = 1 if mt.vertices else 0
            has_norm = 1 if mt.normals else 0
            struct_data += struct.pack("<4fII",
                *mt.bounding_sphere, has_pos, has_norm)
            if not is_native:
                if mt.vertices:
                    for x, y, z in mt.vertices:
                        struct_data += struct.pack("<fff", x, y, z)
                if mt.normals:
                    for nx, ny, nz in mt.normals:
                        struct_data += struct.pack("<fff", nx, ny, nz)

        gw.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        gw.write_bytes(struct_data)

        # Material list
        self._write_material_list(gw, ver, geom)

        # Extension
        ext_data = b""
        if geom.bin_mesh:
            ext_data += self._build_bin_mesh_data(geom.bin_mesh, ver)
        if geom.skin:
            ext_data += self._build_skin_data(geom.skin, num_verts, ver)
        if geom.night_colors is not None:
            ext_data += self._build_night_colors_data(geom.night_colors, ver)
        if geom.effects_2dfx:
            ext_data += self._build_2dfx_data(geom.effects_2dfx, ver)

        gw.write_chunk_header(RW_EXTENSION, len(ext_data), ver)
        gw.write_bytes(ext_data)

        geom_data = geom_stream.getvalue()
        w.write_chunk_header(RW_GEOMETRY, len(geom_data), ver)
        w.write_bytes(geom_data)

    def _build_bin_mesh_data(self, bm: BinMeshPLG, ver: int) -> bytes:
        total_indices = sum(len(s.indices) for s in bm.splits)
        body = struct.pack("<III", bm.flags, len(bm.splits), total_indices)
        for split in bm.splits:
            body += struct.pack("<II", len(split.indices), split.material_index)
            for idx in split.indices:
                body += struct.pack("<I", idx)
        return struct.pack("<III", RW_BIN_MESH, len(body), ver) + body

    def _build_skin_data(self, skin: SkinPLG, num_verts: int, ver: int) -> bytes:
        body = struct.pack("<4B", skin.num_bones, len(skin.used_bone_indices), 4, 0)
        for idx in skin.used_bone_indices:
            body += struct.pack("<B", idx)
        for bi in skin.bone_indices:
            body += struct.pack("<4B", *bi)
        for wt in skin.weights:
            body += struct.pack("<4f", *wt)
        for mat in skin.inverse_matrices:
            body += struct.pack("<16f", *mat)
        # Bone limit/groups (unused in GTA SA)
        body += struct.pack("<III", 0, 0, 0)
        return struct.pack("<III", RW_SKIN, len(body), ver) + body

    def _build_night_colors_data(self, colors: list, ver: int) -> bytes:
        body = struct.pack("<I", 1)  # has_colors
        for r, g, b, a in colors:
            body += struct.pack("<4B", r, g, b, a)
        return struct.pack("<III", RW_NIGHT_COLORS, len(body), ver) + body

    def _build_2dfx_data(self, entries: list[Effect2dfxEntry], ver: int) -> bytes:
        body = struct.pack("<I", len(entries))
        for entry in entries:
            if isinstance(entry.data, Effect2dfxLight):
                light = entry.data
                edata = struct.pack("<4B", *light.color)
                edata += struct.pack("<4f",
                    light.corona_far_clip, light.pointlight_range,
                    light.corona_size, light.shadow_size)
                edata += struct.pack("<5B", light.corona_show_mode,
                    light.corona_enable_reflection, light.corona_flare_type,
                    light.shadow_color_multiplier, light.flags1)
                edata += light.corona_tex_name.encode("ascii").ljust(24, b"\x00")[:24]
                edata += light.shadow_tex_name.encode("ascii").ljust(24, b"\x00")[:24]
                edata += struct.pack("<3B", light.shadow_z_distance, light.flags2, 0)
                if light.look_direction:
                    edata += struct.pack("<3B", *light.look_direction)
                    edata += b"\x00" * 2
            elif isinstance(entry.data, Effect2dfxParticle):
                edata = entry.data.effect_name.encode("ascii").ljust(24, b"\x00")[:24]
            elif isinstance(entry.data, bytes):
                edata = entry.data
            else:
                edata = b""

            body += struct.pack("<3fII", *entry.position, entry.entry_type, len(edata))
            body += edata

        return struct.pack("<III", RW_2DFX, len(body), ver) + body

    def _write_material_list(self, w: RwBinaryWriter, ver: int, geom: DffGeometry):
        body_stream = io.BytesIO()
        bw = RwBinaryWriter(body_stream)

        # Struct: count + indices (all -1)
        struct_data = struct.pack("<I", len(geom.materials))
        for _ in geom.materials:
            struct_data += struct.pack("<i", -1)
        bw.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        bw.write_bytes(struct_data)

        for mat in geom.materials:
            self._write_material(bw, ver, mat)

        body_data = body_stream.getvalue()
        w.write_chunk_header(RW_MATERIAL_LIST, len(body_data), ver)
        w.write_bytes(body_data)

    def _write_material(self, w: RwBinaryWriter, ver: int, mat: DffMaterial):
        mat_stream = io.BytesIO()
        mw = RwBinaryWriter(mat_stream)

        is_textured = 1 if mat.texture_name else 0
        struct_data = struct.pack("<I4BIIfff",
            0, *mat.color, 0, is_textured,
            mat.ambient, mat.specular, mat.diffuse)
        mw.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        mw.write_bytes(struct_data)

        if mat.texture_name:
            self._write_texture_ref(mw, ver, mat.texture_name, mat.mask_name)

        # Extension
        ext_data = b""
        fx_body = self._build_mat_fx_body(mat, ver)
        if fx_body:
            ext_data += struct.pack("<III", RW_MAT_EFFECTS, len(fx_body), ver) + fx_body
        if mat.uv_animations:
            ext_data += self._build_uv_animation_plg_data(mat.uv_animations, ver)
        if mat.specular_level > 0 or mat.specular_texture:
            spec_body = struct.pack("<f", mat.specular_level)
            spec_body += mat.specular_texture.encode("ascii").ljust(24, b"\x00")[:24]
            ext_data += struct.pack("<III", RW_SPECULAR_MAT, len(spec_body), ver) + spec_body
        if mat.reflection:
            ref = mat.reflection
            ref_body = struct.pack("<5fI",
                ref.get("scale_x", 0), ref.get("scale_y", 0),
                ref.get("offset_x", 0), ref.get("offset_y", 0),
                ref.get("intensity", 0), 0)
            ext_data += struct.pack("<III", RW_REFLECTION_MAT, len(ref_body), ver) + ref_body

        mw.write_chunk_header(RW_EXTENSION, len(ext_data), ver)
        mw.write_bytes(ext_data)

        mat_data = mat_stream.getvalue()
        w.write_chunk_header(RW_MATERIAL, len(mat_data), ver)
        w.write_bytes(mat_data)

    def _write_texture_ref(self, w: RwBinaryWriter, ver: int, name: str, mask: str):
        tex_stream = io.BytesIO()
        tw = RwBinaryWriter(tex_stream)

        tw.write_chunk_header(RW_STRUCT, 4, ver)
        tw.write_u32(0x1106)  # default filter flags

        # Pad strings to 4-byte aligned
        name_bytes = name.encode("ascii", errors="replace") + b"\x00"
        name_bytes = name_bytes.ljust((len(name_bytes) + 3) & ~3, b"\x00")
        tw.write_chunk_header(RW_STRING, len(name_bytes), ver)
        tw.write_bytes(name_bytes)

        mask_bytes = mask.encode("ascii", errors="replace") + b"\x00"
        mask_bytes = mask_bytes.ljust((len(mask_bytes) + 3) & ~3, b"\x00")
        tw.write_chunk_header(RW_STRING, len(mask_bytes), ver)
        tw.write_bytes(mask_bytes)

        tw.write_chunk_header(RW_EXTENSION, 0, ver)

        tex_data = tex_stream.getvalue()
        w.write_chunk_header(RW_TEXTURE, len(tex_data), ver)
        w.write_bytes(tex_data)

    def _build_uv_animation_plg_data(
        self, refs: list[DffUvAnimationRef], ver: int
    ) -> bytes:
        channel_mask = 0
        struct_body = b""
        for ref in sorted(refs, key=lambda item: item.channel):
            channel_mask |= 1 << ref.channel
            struct_body += ref.name.encode("ascii", errors="replace").ljust(32, b"\x00")[:32]
        struct_body = struct.pack("<I", channel_mask) + struct_body
        return (
            struct.pack("<III", RW_UV_ANIM_PLG, 12 + len(struct_body), ver)
            + struct.pack("<III", RW_STRUCT, len(struct_body), ver)
            + struct_body
        )

    def _build_mat_fx_body(self, mat: DffMaterial, ver: int) -> bytes:
        mat_fx_type = mat.mat_fx_type or (5 if mat.uv_animations else 0)
        if not mat_fx_type:
            return b""

        body = struct.pack("<I", mat_fx_type)
        effect1 = mat.mat_fx_data.get("effect1") if mat.mat_fx_data else None
        effect2 = mat.mat_fx_data.get("effect2") if mat.mat_fx_data else None

        if effect1 is None and effect2 is None and mat_fx_type == 5:
            effect1 = {"type": 5}
            effect2 = {"type": 0}

        if effect1 is None and effect2 is None:
            return body

        body += self._build_mat_fx_slot(effect1 or {"type": mat_fx_type}, ver)
        if effect2 is not None or mat_fx_type in (3, 5, 6):
            body += self._build_mat_fx_slot(effect2 or {"type": 0}, ver)
        return body

    def _build_mat_fx_slot(self, slot: dict, ver: int) -> bytes:
        slot_type = int(slot.get("type", 0))
        body = struct.pack("<I", slot_type)

        if slot_type == 1:
            body += struct.pack("<fI", slot.get("intensity", 1.0), int(slot.get("has_bump_map", 0)))
            if slot.get("has_bump_map"):
                body += self._build_inline_texture(slot.get("bump_map", {}), ver)
            body += struct.pack("<I", int(slot.get("has_height_map", 0)))
            if slot.get("has_height_map"):
                body += self._build_inline_texture(slot.get("height_map", {}), ver)
        elif slot_type == 2:
            body += struct.pack(
                "<fII",
                slot.get("reflection_coeff", 1.0),
                int(slot.get("use_fb_alpha", 0)),
                int(slot.get("has_env_map", 0)),
            )
            if slot.get("has_env_map"):
                body += self._build_inline_texture(slot.get("env_map", {}), ver)
        elif slot_type == 4:
            body += struct.pack(
                "<iiI",
                int(slot.get("src_blend", 0)),
                int(slot.get("dst_blend", 0)),
                int(slot.get("has_texture", 0)),
            )
            if slot.get("has_texture"):
                body += self._build_inline_texture(slot.get("texture", {}), ver)

        return body

    def _build_inline_texture(self, texture: dict, ver: int) -> bytes:
        tex_stream = io.BytesIO()
        tw = RwBinaryWriter(tex_stream)

        filter_flags = int(texture.get("filter", 0x1106))
        tw.write_chunk_header(RW_STRUCT, 4, ver)
        tw.write_u32(filter_flags)

        for key in ("name", "mask"):
            value = str(texture.get(key, ""))
            raw = value.encode("ascii", errors="replace") + b"\x00"
            raw = raw.ljust((len(raw) + 3) & ~3, b"\x00")
            tw.write_chunk_header(RW_STRING, len(raw), ver)
            tw.write_bytes(raw)

        tw.write_chunk_header(RW_EXTENSION, 0, ver)

        tex_data = tex_stream.getvalue()
        return struct.pack("<III", RW_TEXTURE, len(tex_data), ver) + tex_data

    def _write_atomic(self, w: RwBinaryWriter, ver: int, atomic: DffAtomic):
        body_stream = io.BytesIO()
        bw = RwBinaryWriter(body_stream)

        struct_data = struct.pack("<IIII",
            atomic.frame_index, atomic.geometry_index, atomic.flags, 0)
        bw.write_chunk_header(RW_STRUCT, len(struct_data), ver)
        bw.write_bytes(struct_data)
        bw.write_chunk_header(RW_EXTENSION, 0, ver)

        body_data = body_stream.getvalue()
        w.write_chunk_header(RW_ATOMIC, len(body_data), ver)
        w.write_bytes(body_data)
