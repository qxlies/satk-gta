# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 satk authors. See ../LICENSE for the full license.
"""Kit material hook for vanilla:<txd>/<texture>, with a synthetic preview only."""

from __future__ import annotations


def apply_reference(mat, preset: str, *, asset_class="map") -> dict:
    """Mark a reference-only material and its placeholder as shared before export.

    No vanilla image is decoded or packed. Kit must carry satk_vanilla into its
    export manifest and pass the references to satk.texlib.vanilla.export_plan.
    """
    import bpy

    from satk.texlib.vanilla import material_preset
    from satk_blender.kit.materials import apply_preset, set_image

    data = material_preset(preset, asset_class=asset_class)
    # Do not reuse or relabel an existing user image that happens to have this
    # texture name. The exporter recognises the exact name on the image node.
    apply_preset(mat, {**data, "texture": None})
    img = bpy.data.images.new(data["texture"], 8, 8, alpha=bool(data["alpha"]))
    img.generated_color = (0.45, 0.45, 0.45, 1.0)
    img["satk_shared"] = True
    img["satk_texture"] = data["texture"]
    set_image(mat, img)
    mat["satk_vanilla"] = data["vanilla"]
    mat["satk_txd"] = data["ide_txd"]
    return data
