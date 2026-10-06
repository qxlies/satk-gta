# SPDX-License-Identifier: GPL-3.0-or-later
"""Put converted meshes into a kit scaffold and use the existing kit generators and shading."""

from __future__ import annotations

import json
from pathlib import Path

import bpy

from satk.core.errors import SatkError
from satk.kit import kinds as K
from satk_blender.kit import materials as M, util as U

from . import common as C


def _material(model: str, texture: dict, group: str):
    role = texture["role"]
    preset = K.preset(("map_alpha" if texture.get("alpha") else "map") if group == "world"
                      else "weapon" if group == "character" else "paint1" if role == "body" else "decal", model)
    preset.update(role=role, texture=texture["name"], shared=False, size=[texture["size"]] * 2,
                  rgba=[255, 255, 255, 255], alpha=texture.get("alpha", False))
    mat = M.make(f"{model}.convert_{role}", preset, {texture["name"]: texture["file"]})
    mat["satk_shared"] = False
    if texture.get("alpha"):
        node = next(n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.get("satk_base"))
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        mat.node_tree.links.new(node.outputs["Alpha"], bsdf.inputs["Alpha"])
    return mat


def _lod_textures(obj, model: str):
    for slot in obj.material_slots:
        if slot.material is None:
            continue
        mat = slot.material.copy()
        slot.material = mat
        node = next((n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.get("satk_base")), None)
        if node and node.image:
            image = node.image.copy()
            image.scale(64, 64)
            name = f"lod_{model}_{mat.get('satk_role', 'map')}"[:31]
            image.name = name
            image["satk_texture"] = name
            image["satk_shared"] = False
            image.pack()
            M.set_image(mat, image)


def assemble_method(ctx, params: dict) -> dict:
    """Fill kit slots, apply sa_shade, bake warm world prelight, and generate kit LOD/wheels; plan=<plan.json>."""
    plan, folder, key, state = C.load(ctx, params, "bake")
    finished_path = folder / "finished.json"
    if not finished_path.is_file():
        raise SatkError("NOT_READY", "the baked textures have not been finished",
                        hint=f"satk convert finish {folder.as_posix()}/plan.json")
    finished = json.loads(finished_path.read_text(encoding="utf-8"))
    from satk.convert.plan import texture_rows

    texture_rows(finished, plan, folder, "textures")
    model = plan["name"]
    U.ensure_dff()
    ctx.call("kit.template", {"plan": plan["template_path"], "textures": {}})
    coll = U.clump(model)
    targets = C.objects(list(state["low"].values()))
    textures = {t["role"]: t for t in finished["textures"]}
    for role, name in state["low"].items():
        obj = C.objects([name])[0]
        mat = _material(model, textures[role], plan["group"])
        obj.data.materials.clear()
        obj.data.materials.append(mat)
        for face in obj.data.polygons:
            face.material_index = 0
    slots = [o for o in coll.objects if o.type == "MESH" and o.get("satk_slot") in ("hd", "ok")
             and o.get("satk_part") != "wheel"]
    if not slots:
        raise SatkError("UNSUPPORTED", f"the {plan['kind']} kit has no rigid body slot")
    slot = max(slots, key=lambda o: int(o.get("satk_vanilla_tris", 0)))
    ctx.call("kit.fill", {"slot": slot.name, "objects": [o.name for o in targets], "keep": True})
    C.real_materials(slot.data)
    slot.data.validate(clean_customdata=True)
    C.select(slot)
    if slot.data.has_custom_normals:
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    slot.data.update()
    bpy.context.view_layer.update()
    # kit.fill preserves scaffold presets as unused slots. Remove them so no blank own texture is exported.
    used = sorted({p.material_index for p in slot.data.polygons})
    mats = [slot.data.materials[i] for i in used]
    indices = {old: new for new, old in enumerate(used)}
    face_mats = [indices[p.material_index] for p in slot.data.polygons]
    slot.data.materials.clear()
    for mat in mats:
        slot.data.materials.append(mat)
    for face, index in zip(slot.data.polygons, face_mats):
        face.material_index = index
    generators = {}
    wheel = next((o for o in coll.objects if o.type == "MESH" and o.get("satk_part") == "wheel"), None)
    if wheel is not None:
        generators["wheel"] = ctx.call("kit.wheel", {"model": model})
        rim = M.ensure(model, K.preset("rim", model))
        image = bpy.data.images.load(textures["wheel"]["file"], check_existing=False)
        image.name = textures["wheel"]["name"]
        image["satk_texture"] = image.name
        image["satk_shared"] = False
        image.pack()
        M.set_image(rim, image)
    from satk_blender.kit.shade import shade_object

    shaded = [o for o in coll.objects if o.type == "MESH" and len(o.data.polygons)]
    for obj in shaded:
        shade_object(obj)
    shade = {"objects": len(shaded)}
    if any(o.get("satk_slot") == "vlo" for o in coll.objects):
        generators["vlo"] = ctx.call("kit.vlo", {"model": model})
        for obj in coll.objects:
            if obj.type == "MESH":
                C.real_materials(obj.data)
    lod = next((c for c in bpy.data.collections if c.get("satk_lod_of") == model), None)
    if lod is not None:
        # LOD keeps vanilla's absolute budget even when the HD tier grows.
        vanilla_tris = plan["template"]["counts"]["vanilla_tris"]
        cap = max(12, round(vanilla_tris * 0.21))
        ratio = max(0.01, min(1.0, cap / max(1, C.tris(slot))))
        generators["lod"] = ctx.call("kit.lod", {"model": model, "ratio": ratio})
        for obj in lod.objects:
            if obj.type == "MESH":
                C.real_materials(obj.data)
                _lod_textures(obj, model)
    lighting = {}
    if plan["group"] == "world":
        from satk_blender.gameready import prelight

        meshes = [o for o in coll.objects if o.type == "MESH" and len(o.data.polygons)]
        if lod is not None:
            meshes += [o for o in lod.objects if o.type == "MESH" and len(o.data.polygons)]
        for obj in meshes:
            lighting[obj.name] = prelight(obj, "bake")
            obj.dff.export_normals = False
    # Source copies remain in the blend for inspection, hidden and outside every kit collection.
    for obj in C.objects(state["source"] + list(state["high"].values()) + list(state["low"].values())):
        obj.hide_render = True
        obj.hide_set(True)
    return C.save(ctx, folder, key, state, "assemble", {"model": model, "body_slot": slot.name,
                  "body_tris": C.tris(slot), "shaded": shade.get("objects"), "generators": generators,
                  "prelight": lighting, "changed": [slot.name]})
