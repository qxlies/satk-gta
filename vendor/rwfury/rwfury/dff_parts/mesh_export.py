"""DFF to GenericMesh conversion helpers."""

from __future__ import annotations

from ..generic_mesh import GenericMesh
from .models import DffFrame, DffGeometry


def expand_bin_mesh_indices(indices: list[int], flags: int) -> list[int]:
    if flags != 1:
        return list(indices)

    triangles: list[int] = []
    for i in range(len(indices) - 2):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        if a == b or b == c or a == c:
            continue
        if i % 2:
            triangles.extend((b, a, c))
        else:
            triangles.extend((a, b, c))
    return triangles


def build_generic_mesh_from_indices(
    geom: DffGeometry,
    frame: DffFrame,
    transform: list[float],
    material_index: int,
    source_indices: list[int],
) -> GenericMesh | None:
    if material_index < 0 or material_index >= len(geom.materials):
        return None
    if not source_indices:
        return None
    if any(i < 0 or i >= len(geom.vertices) for i in source_indices):
        return None

    material = geom.materials[material_index]
    used = sorted(set(source_indices))
    remap = {old: new for new, old in enumerate(used)}

    positions: list[float] = []
    for i in used:
        positions.extend(geom.vertices[i])

    normals: list[float] = []
    if geom.normals:
        for i in used:
            normals.extend(geom.normals[i])

    texcoords: list[list[float]] = []
    for uv_layer in geom.texcoord_sets:
        flat_uv: list[float] = []
        for i in used:
            flat_uv.extend(uv_layer[i])
        texcoords.append(flat_uv)

    colors: list[int] = []
    if geom.vertex_colors:
        for i in used:
            colors.extend(geom.vertex_colors[i])

    bone_idx_flat: list[int] = []
    bone_wt_flat: list[float] = []
    if geom.skin:
        for i in used:
            bone_idx_flat.extend(geom.skin.bone_indices[i])
            bone_wt_flat.extend(geom.skin.weights[i])

    return GenericMesh(
        name=frame.name,
        material_index=material_index,
        positions=positions,
        normals=normals,
        texcoords=texcoords,
        colors=colors,
        indices=[remap[i] for i in source_indices],
        texture_name=material.texture_name,
        mask_name=material.mask_name,
        diffuse_color=material.color,
        ambient=material.ambient,
        diffuse=material.diffuse,
        specular=material.specular,
        transform=transform,
        bone_indices=bone_idx_flat,
        bone_weights=bone_wt_flat,
    )
