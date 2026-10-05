"""Headless Blender observations for synthetic media regressions; run by test_media_regressions."""

import json
import math
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO / "blender")]

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

from satk_blender import common, exporter, importer, render, shading


def reset():
    common.clean_scene()
    scene = bpy.context.scene
    scene.render.film_transparent = False
    scene.render.dither_intensity = 0.0
    scene.view_settings.view_transform = "Standard"
    return scene


def placements(out):
    reset()
    bpy.ops.mesh.primitive_cube_add()
    proto = bpy.context.object
    local = Matrix.Translation((2, -3, 1)) @ Matrix.Rotation(math.radians(30), 4, "X")
    proto.matrix_world = local
    common.tag(proto, satk_model="model:1000", satk_sid="model:1000", satk_proto=1)
    inst = bpy.data.objects.new("placed", proto.data)
    bpy.context.scene.collection.objects.link(inst)
    common.tag(inst, satk_model="model:1000", satk_sid="inst:test#0", satk_pos=[0, 0, 0],
               satk_q=[0, 0, 0, 1], satk_area=2, satk_iflags=4)
    edit = Matrix.Translation((10, 20, 30)) @ Matrix.Rotation(math.pi / 2, 4, "Z")
    inst.matrix_world = edit @ local
    bpy.context.view_layer.update()
    return exporter._placements("model:1000")


def area_roots(out):
    reset()
    locals_ = [Matrix.Translation((2, -3, 1)) @ Matrix.Rotation(0.4, 4, "X"),
               Matrix.Translation((-5, 2, 3)) @ Matrix.Rotation(-0.2, 4, "Z")]

    def fake_import(*_):
        coll = common.ensure_collection("test.dff")
        for i, local in enumerate(locals_):
            bpy.ops.mesh.primitive_cube_add()
            o = bpy.context.object
            o.name = f"part{i}"
            for c in list(o.users_collection):
                c.objects.unlink(o)
            coll.objects.link(o)
            o.matrix_world = local
        return SimpleNamespace(current_collection=coll)

    original = importer._import_dff
    importer._import_dff = fake_import
    try:
        q = Quaternion((0, 0, 1), 0.3)
        plan = {"models": [{"sid": "model:1000", "id": 1000, "name": "test", "dff": "synthetic", "txd": []}],
                "insts": [{"sid": "inst:test#0", "model": 1000, "pos": [1, 2, 3],
                           "q": [q.x, q.y, q.z, q.w], "area": 2, "iflags": 4}]}
        importer.import_area(plan, {})
    finally:
        importer._import_dff = original
    parts = [o for o in bpy.data.objects if o.type == "MESH" and o.get("satk_sid") == "inst:test#0"]
    parents = {o.parent for o in parts}
    root = next(iter(parents)) if len(parents) == 1 else None
    result = {"parts": len(parts), "root": root is not None}
    if root is not None:
        edit = Matrix.Translation((10, 20, 30)) @ Matrix.Rotation(math.pi / 2, 4, "Z")
        root.matrix_world = edit
        bpy.context.view_layer.update()
        result["matrices_match"] = all(
            max(abs(x - y) for r1, r2 in zip(o.matrix_world, edit @ local) for x, y in zip(r1, r2)) < 1e-5
            for o, local in zip(sorted(parts, key=lambda o: o.name), locals_))
        result["placements"] = exporter._placements("model:1000")
        # Moving a mesh in object mode must not silently disappear on export either. A single
        # part defines a placement; incompatible edits to multiple parts cannot fit one IPL row.
        parts.sort(key=lambda o: o.name)
        moved = Matrix.Translation((40, 50, 60)) @ Matrix.Rotation(0.6, 4, "Z")
        parts[0].matrix_world = moved @ locals_[0]
        bpy.context.view_layer.update()
        try:
            exporter._placements("model:1000")
            result["partial_edit_error"] = None
        except exporter.ExportError as exc:
            result["partial_edit_error"] = exc.code
        bpy.data.objects.remove(parts[1], do_unlink=True)
        bpy.context.view_layer.update()
        result["mesh_edit"] = exporter._placements("model:1000")
    return result


def texture_pixels(out):
    reset()

    def chunk(kind, data):
        return struct.pack("<III", kind, len(data), 0x1803FFFF) + data

    # BC2 always has four colours, including when c0 < c1. Indices 2/3 are 1/3 and 2/3 white.
    pixels = b"\xff" * 8 + struct.pack("<HHI", 0, 65535, 0xAAAAAAAA)
    pixels += b"\xff" * 8 + struct.pack("<HHI", 0, 65535, 0xFFFFFFFF)
    head = struct.pack("<IBBH32s32sII", 9, 1, 0x11, 0, b"bc2_probe", b"", 0x500,
                       int.from_bytes(b"DXT3", "little"))
    head += struct.pack("<HHBBBBI", 4, 8, 32, 1, 4, 9, len(pixels)) + pixels
    native = chunk(0x15, chunk(1, head) + chunk(3, b""))
    txd = chunk(0x16, chunk(1, struct.pack("<HH", 1, 2)) + native + chunk(3, b""))
    path = out / "synthetic.txd"
    path.write_bytes(txd)
    image = common.load_txd(str(path))["bc2_probe"][0]
    a = np.empty(4 * 8 * 4, dtype=np.float32)
    image.pixels.foreach_get(a)
    actual = np.rint(a.reshape(8, 4, 4)[::-1] * 255).astype(np.uint8)
    expected = np.array([(85, 85, 85, 255)] * 16 + [(170, 170, 170, 255)] * 16, dtype=np.uint8).reshape(8, 4, 4)
    return {"max_error": int(np.abs(actual.astype(int) - expected).max()),
            "top": actual[0, 0].tolist(), "bottom": actual[-1, 0].tolist(), "packed": image.packed_file is not None}


def texture_failures(out):
    from satk.formats.rw import FormatError

    reset()

    def chunk(kind, data):
        return struct.pack("<III", kind, len(data), 0x1803FFFF) + data

    def native(name, pixels, *, fmt=21, declared_size=None):
        head = struct.pack("<IBBH32s32sII", 9, 1, 0x11, 0, name, b"", 0x500, fmt)
        head += struct.pack("<HHBBBBI", 2, 2, 32, 1, 4, 1,
                            len(pixels) if declared_size is None else declared_size) + pixels
        return chunk(0x15, chunk(1, head) + chunk(3, b""))

    pixels = bytes((9, 17, 33, 255)) * 4  # BGRA, decoded to [33, 17, 9, 255].
    first = native(b"intact_first", pixels)
    last = native(b"intact_last", pixels)
    cases = {
        "truncated_native": native(b"broken", pixels[:-4], declared_size=len(pixels)),
        "truncated_tail": native(b"broken", pixels),
        "short_mip": native(b"broken", pixels[:-4]),
        "unsupported_format": native(b"broken", pixels, fmt=int.from_bytes(b"DXT2", "little")),
    }
    results = {}
    for case, broken in cases.items():
        txd = chunk(0x16, chunk(1, struct.pack("<HH", 3, 2)) + first + broken + last + chunk(3, b""))
        if case == "truncated_tail":
            # A file cut inside the second native also leaves the dictionary size too large.
            txd = chunk(0x16, chunk(1, struct.pack("<HH", 2, 2)) + first + broken)[:-16]
        path = out / (case + ".txd")
        path.write_bytes(txd)
        try:
            images = common.load_txd(str(path))
        except FormatError as exc:
            results[case] = {"error": str(exc)}
            continue
        observations = {}
        for name, loaded in images.items():
            image = loaded[0]
            rgba = np.empty(2 * 2 * 4, dtype=np.float32)
            image.pixels.foreach_get(rgba)
            observations[name] = {"packed": image.packed_file is not None,
                                  "pixels": np.rint(rgba * 255).astype(np.uint8).reshape(-1, 4).tolist()}
        bpy.ops.mesh.primitive_plane_add()
        obj = bpy.context.object
        mat = bpy.data.materials.new(case)
        mat.use_nodes = True
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.label = "broken"
        tex.image = images.get("broken", [None])[0]
        obj.data.materials.append(mat)
        results[case] = {"images": observations, "missing": common.missing_textures([obj])}
    return results


def object_ids(out):
    scene = reset()
    bpy.ops.mesh.primitive_cube_add(size=2)
    target = bpy.context.object
    target["satk_sid"] = "inst:target#0"
    bpy.ops.mesh.primitive_cube_add(size=4, location=(0, -4, 0))
    front = bpy.context.object
    front["satk_sid"] = "inst:front#0"
    render.camera(scene, (0, -10, 0), (0, 1, 0))
    bpy.context.view_layer.update()
    tagged = render.objindex(scene, str(out / "ids_tagged.png"), (64, 64))
    del front["satk_sid"]
    untagged = render.objindex(scene, str(out / "ids_untagged.png"), (64, 64))
    hidden = front.hide_render
    # A transparent untagged occluder must still reveal the target through its alpha.
    front.hide_render = False
    mat = bpy.data.materials.new("transparent_occluder")
    mat.use_nodes = True
    image = bpy.data.images.new("clear", 1, 1, alpha=True)
    image.pixels[:] = [1, 1, 1, 0]
    tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    front.data.materials.clear()
    front.data.materials.append(mat)
    clear = render.objindex(scene, str(out / "ids_clear.png"), (64, 64))
    return {"tagged": tagged, "untagged": untagged, "clear": clear, "hidden": hidden}


def orbits(out):
    scene = reset()
    bpy.ops.mesh.primitive_cube_add()
    seen = []
    original = render.render_png

    def observe(scene, path, size):
        # The camera's local +Z points back from the target towards the eye.
        d = scene.camera.rotation_euler.to_quaternion() @ Vector((0, 0, 1))
        seen.append(list(d))
        return path

    render.render_png = observe
    try:
        render.orbit_views(scene, [bpy.context.object], str(out), views=4, size=64, engine="workbench")
    finally:
        render.render_png = original
    return seen


def alpha_render(out):
    scene = reset()
    for name, z, alpha in [("ground", 0, 1.0), ("decal", 0.1, 0.2)]:
        bpy.ops.mesh.primitive_plane_add(size=6, location=(0, 0, z))
        o = bpy.context.object
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        image = bpy.data.images.new(name + "_image", 2, 2, alpha=True)
        rgb = [1, 1, 1] if name == "ground" else [0, 0, 0]
        image.pixels[:] = (rgb + [alpha]) * 4
        image.pack()
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = image
        mat.node_tree.nodes.active = tex
        o.data.materials.append(mat)
        shading.building_material(mat, False, False)
    render.camera(scene, (0, 0, 10), (0, 0, -1))
    bpy.context.view_layer.update()
    # Packed images are unloaded on reopen: this is the actual `render --blend` path.
    blend = str(out / "alpha.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend, check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=blend)
    scene = bpy.context.scene
    unloaded = sum(not img.has_data for img in common.used_images(scene.objects))
    selected = render.set_engine(scene, "workbench")
    default_path = render.render_png(scene, str(out / "alpha_default.png"), (64, 64))
    render.set_engine(scene, "eevee")
    reference_path = render.render_png(scene, str(out / "alpha_eevee.png"), (64, 64))
    actual, ai = render._pixels(default_path)
    ref, ri = render._pixels(reference_path)
    try:
        return {"engine": selected, "unloaded": unloaded,
                "warnings": render.engine_warnings("workbench", selected),
                "max_error": float(np.abs(actual[24:40, 24:40] - ref[24:40, 24:40]).max()),
                "default": actual[32, 32].tolist(), "eevee": ref[32, 32].tolist()}
    finally:
        bpy.data.images.remove(ai)
        bpy.data.images.remove(ri)


if __name__ == "__main__":
    out = Path(sys.argv[sys.argv.index("--") + 1])
    dragonff = sys.argv[sys.argv.index("--") + 2]
    common.load_dragonff(dragonff)
    results = {fn.__name__: fn(out) for fn in
               (placements, area_roots, texture_pixels, texture_failures, orbits, object_ids, alpha_render)}
    (out / "observations.json").write_text(json.dumps(results), encoding="utf-8")
