from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..generic_mesh import GenericMesh
from ..rwbinary import RwBinaryWriter
from .mesh_export import build_generic_mesh_from_indices, expand_bin_mesh_indices
from .models import DffFrame, DffLight, DffLightFlags, DffUvAnimation, Mesh

if TYPE_CHECKING:
    from .models import HAnimBone


class DffApiMixin:
    def get_hanim_bones(self) -> list[HAnimBone]:
        """Return the first complete HAnim hierarchy in node-index order."""
        for frame in self.frames:
            if frame.hanim and frame.hanim.bones:
                return sorted(frame.hanim.bones, key=lambda bone: bone.node_index)
        return []

    def get_hanim_bone_index(self, node_id: int) -> int | None:
        """Resolve an HAnim node ID to the matching skin bone index."""
        for bone in self.get_hanim_bones():
            if bone.node_id == node_id:
                return bone.node_index
        return None

    def get_hanim_frame_index(self, node_id: int) -> int | None:
        """Resolve an HAnim node ID to its DFF frame index."""
        for frame_index, frame in enumerate(self.frames):
            if frame.hanim and frame.hanim.node_id == node_id:
                return frame_index
        return None

    def get_light_frame(self, light: DffLight) -> DffFrame | None:
        if 0 <= light.frame_index < len(self.frames):
            return self.frames[light.frame_index]
        return None

    def iter_lights_with_frames(self):
        for light in self.lights:
            yield light, self.get_light_frame(light)

    def get_lights(self) -> list[dict]:
        """Return real RenderWare lights with resolved frame metadata."""
        return [
            {
                "light": light,
                "frame": frame,
                "frame_name": frame.name if frame else "",
                "type": light.type_name,
                "flags": light.flags_enum,
                "radius": light.radius,
                "color": light.color,
                "spot_angle_degrees": light.spot_angle_degrees,
                "affects_scene": light.affects_scene,
                "affects_world": light.affects_world,
            }
            for light, frame in self.iter_lights_with_frames()
        ]

    def add_light(self, light: DffLight) -> DffLight:
        self.lights.append(light)
        return light

    def get_uv_animations(self) -> list[DffUvAnimation]:
        return list(self.uv_animations)

    def get_uv_animation(self, name: str) -> DffUvAnimation | None:
        name_lower = name.lower()
        for animation in self.uv_animations:
            if animation.name.lower() == name_lower:
                return animation
        return None

    def get_material_uv_animations(self) -> list[dict]:
        results = []
        for geom_index, geom in enumerate(self.geometries):
            for material_index, material in enumerate(geom.materials):
                if not material.uv_animations:
                    continue
                results.append({
                    "geometry_index": geom_index,
                    "material_index": material_index,
                    "material": material,
                    "references": list(material.uv_animations),
                    "animations": [
                        self.get_uv_animation(ref.name)
                        for ref in material.uv_animations
                    ],
                })
        return results

    def to_uv_animation_data(self) -> dict:
        usage_by_name: dict[str, list[dict]] = {}
        for geom_index, geom in enumerate(self.geometries):
            for material_index, material in enumerate(geom.materials):
                if not material.uv_animations:
                    continue
                usage = {
                    "geometry_index": geom_index,
                    "material_index": material_index,
                    "texture_name": material.texture_name,
                    "mask_name": material.mask_name,
                    "uv_animations": [
                        {
                            "channel": ref.channel,
                            "name": ref.name,
                        }
                        for ref in material.uv_animations
                    ],
                }
                for ref in material.uv_animations:
                    usage_by_name.setdefault(ref.name, []).append(usage)

        animations = []
        for animation in self.uv_animations:
            animations.append({
                "name": animation.name,
                "version": animation.version,
                "type": animation.animation_type,
                "flags": animation.flags,
                "duration": animation.duration,
                "unknown": animation.unknown,
                "frame_count": animation.frame_count,
                "node_to_uv": list(animation.node_to_uv),
                "frames": [
                    {
                        "time": frame.time,
                        "scale": list(frame.scale),
                        "position": list(frame.position),
                        "previous_frame": frame.previous_frame,
                    }
                    for frame in animation.frames
                ],
                "materials": usage_by_name.get(animation.name, []),
            })

        return {
            "animation_count": len(animations),
            "material_animation_count": sum(
                len(material.uv_animations)
                for geom in self.geometries
                for material in geom.materials
            ),
            "animations": animations,
        }

    def export_uv_animations(self, path: str) -> dict:
        """Export UV animation definitions and material usage as JSON."""
        data = self.to_uv_animation_data()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data

    def to_uv_animation_json(self, indent: int = 2) -> str:
        """Serialize UV animation definitions and material usage to JSON."""
        return json.dumps(self.to_uv_animation_data(), indent=indent)

    def add_ambient_light(
        self,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return self.add_light(DffLight.ambient(color=color, frame_index=frame_index, flags=flags))

    def add_directional_light(
        self,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return self.add_light(DffLight.directional(color=color, frame_index=frame_index, flags=flags))

    def add_point_light(
        self,
        radius: float,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return self.add_light(DffLight.point(
            radius=radius,
            color=color,
            frame_index=frame_index,
            flags=flags,
        ))

    def add_spot_light(
        self,
        radius: float,
        angle_degrees: float,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        soft: bool = False,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return self.add_light(DffLight.spot(
            radius=radius,
            angle_degrees=angle_degrees,
            color=color,
            frame_index=frame_index,
            soft=soft,
            flags=flags,
        ))

    def get_meshes(self) -> list[dict]:
        """Extract mesh data grouped by atomic/material.

        Returns a list of dicts, each with:
            meshes: list[Mesh]  -- one Mesh per material in the geometry
            texture: str        -- diffuse texture name of the first material
            name: str           -- frame name
        """
        results = []

        for atomic in self.atomics:
            geom = self.geometries[atomic.geometry_index]
            frame = self.frames[atomic.frame_index] if atomic.frame_index < len(self.frames) else DffFrame()

            meshes = []
            for mat_idx, material in enumerate(geom.materials):
                mat_tris = [t for t in geom.triangles if t[3] == mat_idx]
                if not mat_tris:
                    continue

                used_indices = sorted({i for a, b, c, _ in mat_tris for i in (a, b, c)})
                remap = {old: new for new, old in enumerate(used_indices)}

                positions = [geom.vertices[i] for i in used_indices]
                indices = [remap[i] for a, b, c, _ in mat_tris for i in (a, b, c)]

                texcoords = []
                for uv_layer in geom.texcoord_sets:
                    texcoords.append([uv_layer[i] for i in used_indices])

                normals = [geom.normals[i] for i in used_indices] if geom.normals else None
                colors = [geom.vertex_colors[i] for i in used_indices] if geom.vertex_colors else None

                meshes.append(Mesh(
                    positions=positions,
                    indices=indices,
                    texcoords=texcoords,
                    normals=normals,
                    vertex_colors=colors,
                ))

            results.append({
                "meshes": meshes,
                "texture": geom.materials[0].texture_name if geom.materials else "",
                "name": frame.name,
            })

        return results

    def to_generic_meshes(self) -> list[GenericMesh]:
        """Export all meshes as flat, format-agnostic GenericMesh objects.

        Each material split in each atomic produces one GenericMesh with:
        - Flat position/normal/texcoord/color arrays
        - Triangle indices (remapped to local vertex space)
        - 4x4 transform from the associated frame
        - Skinning data (if the geometry has a SkinPLG)
        """
        results: list[GenericMesh] = []

        for atomic in self.atomics:
            geom = self.geometries[atomic.geometry_index]
            frame = (self.frames[atomic.frame_index]
                     if atomic.frame_index < len(self.frames) else DffFrame())

            # Build 4x4 row-major transform from frame
            r = frame.rotation_matrix  # 9 floats (3x3 row-major)
            p = frame.position
            xform = [
                r[0], r[1], r[2], 0.0,
                r[3], r[4], r[5], 0.0,
                r[6], r[7], r[8], 0.0,
                p[0], p[1], p[2], 1.0,
            ]

            if geom.bin_mesh and geom.bin_mesh.splits:
                for split in geom.bin_mesh.splits:
                    mesh = build_generic_mesh_from_indices(
                        geom=geom,
                        frame=frame,
                        transform=xform,
                        material_index=split.material_index,
                        source_indices=expand_bin_mesh_indices(
                            split.indices, geom.bin_mesh.flags
                        ),
                    )
                    if mesh:
                        results.append(mesh)
                continue

            for mat_idx, _material in enumerate(geom.materials):
                source_indices = [
                    i for a, b, c, tri_mat in geom.triangles
                    if tri_mat == mat_idx
                    for i in (a, b, c)
                ]
                mesh = build_generic_mesh_from_indices(
                    geom=geom,
                    frame=frame,
                    transform=xform,
                    material_index=mat_idx,
                    source_indices=source_indices,
                )
                if mesh:
                    results.append(mesh)

        return results

    def to_file(self, path: str):
        """Write this DFF back to a binary file."""
        with RwBinaryWriter.to_file(path) as writer:
            self._write(writer)
