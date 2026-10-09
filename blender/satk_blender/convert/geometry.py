# SPDX-License-Identifier: GPL-3.0-or-later
"""Import and simplify an author's meshes, retaining the unreduced source for baking."""

from __future__ import annotations

import math
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

from satk.core.errors import SatkError
from satk.style.texture import role_of
from satk_blender.kit.gen_vlo import merged_mesh
from satk_blender.kit.shade import _uv_seam_edges

from . import common as C


def _import(source: Path) -> None:
    suffix = source.suffix.lower()
    if suffix == ".blend":
        with bpy.data.libraries.load(str(source), link=False) as (src, dst):
            dst.objects = list(src.objects)
            dst.collections = list(src.collections)
        children = {child for collection in dst.collections if collection for child in collection.children}
        for collection in dst.collections:
            if collection and collection not in children:
                bpy.context.scene.collection.children.link(collection)
        for obj in dst.objects:
            if obj is not None and not obj.users_collection:
                bpy.context.scene.collection.objects.link(obj)
        return
    operator, kwargs = {
        ".glb": (bpy.ops.import_scene.gltf, {}), ".gltf": (bpy.ops.import_scene.gltf, {}),
        ".fbx": (bpy.ops.import_scene.fbx, {"use_image_search": False}),
        ".obj": (bpy.ops.wm.obj_import, {"forward_axis": "NEGATIVE_Z", "up_axis": "Y"}),
        ".dae": (bpy.ops.wm.collada_import, {}),
    }.get(suffix, (None, {}))
    if operator is None:
        raise SatkError("UNSUPPORTED", f"no importer for {suffix}")
    try:
        operator.get_rna_type()
    except (AttributeError, RuntimeError):
        raise SatkError("UNSUPPORTED", f"this Blender build has no built-in {suffix} importer",
                        hint="export the source to glTF, FBX or OBJ with an installed authoring tool") from None
    result = operator(filepath=str(source), **kwargs)
    if "FINISHED" not in result:
        raise SatkError("EXTERNAL_TOOL", f"Blender did not finish importing {source.name}")


def _image_dependencies(objects) -> set[str]:
    """Include reused images and images in node groups, not just new bpy.data.images entries."""
    trees, images, seen = [], set(), set()
    for obj in objects:
        if obj.type == "MESH":
            trees += [s.material.node_tree for s in obj.material_slots if s.material and s.material.node_tree]
    while trees:
        tree = trees.pop()
        if tree in seen:
            continue
        seen.add(tree)
        for node in tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                images.add(node.image)
            elif node.type == "GROUP" and node.node_tree:
                trees.append(node.node_tree)
    paths = set()
    for image in images:
        if image.source not in ("FILE", "GENERATED"):
            raise SatkError("UNSUPPORTED", f"animated or tiled source image {image.name!r} is not supported",
                            hint="bake it to an ordinary image in the source application")
        if image.source != "FILE" or image.packed_file:
            continue
        file = Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()
        if not file.is_file():
            raise SatkError("NOT_FOUND", f"missing source texture {file.name}",
                            hint="restore the source texture before converting")
        paths.add(file.as_posix())
    return paths


def _render_visible(scene) -> set:
    """An object is render-visible through any visible collection path, including all ancestors."""
    visible, visited = set(), set()
    pending = [scene.collection]
    while pending:
        collection = pending.pop()
        if collection in visited or collection.hide_render:
            continue
        visited.add(collection)
        visible.update(collection.objects)
        pending.extend(collection.children)
    return visible


def import_method(ctx, params: dict) -> dict:
    """Import a local file with Blender's importer; plan=<plan.json>. Existing scene objects are left alone."""
    plan, folder, key, state = C.load(ctx, params)
    if state:
        raise SatkError("EXISTS", "this conversion already has meshes in the session",
                        hint="continue at its next step or use a new session")
    source = Path(plan["source"])
    if not source.is_file():
        raise SatkError("NOT_FOUND", f"source model no longer exists: {source.name}")
    from satk.convert.plan import fingerprints, unchanged

    if not unchanged(plan["input_files"]):
        raise SatkError("REVISION", "source files changed after preparing the conversion",
                        hint="prepare a new plan from the saved source")
    before = set(bpy.data.objects)
    collections_before = set(bpy.data.collections)
    libraries_before = set(bpy.data.libraries)
    made = []
    skipped = 0
    complete = False
    try:
        _import(source)
        bpy.context.view_layer.update()
        imported = sorted(set(bpy.data.objects) - before, key=lambda o: o.name)
        coll = bpy.data.collections.new(f"convert_{plan['name']}_{plan['key'][:8]}")
        ctx.scene.collection.children.link(coll)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        render_visible = _render_visible(ctx.scene)
        visible = [o for o in imported if o.type == "MESH" and not o.hide_render and o.visible_get()
                   and o in render_visible]
        dependencies = set(plan["input_files"]) | _image_dependencies(visible)
        for library in set(bpy.data.libraries) - libraries_before:
            file = Path(bpy.path.abspath(library.filepath)).resolve()
            if not file.is_file():
                raise SatkError("NOT_FOUND", f"missing source library {file.name}")
            dependencies.add(file.as_posix())
        inputs = fingerprints(dependencies)
        if any(inputs[p] != value for p, value in plan["input_files"].items()):
            raise SatkError("REVISION", "source files changed during import")
        for obj in imported:
            if obj not in visible:
                skipped += 1
                continue
            if any(m.type == "ARMATURE" for m in obj.modifiers):
                raise SatkError("UNSUPPORTED", "the source contains a skinned mesh",
                                hint="export a static posed mesh, or keep the SA skin and use kit.export")
            evaluated = obj.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
            C.real_materials(mesh)
            if not len(mesh.polygons):
                bpy.data.meshes.remove(mesh)
                skipped += 1
                continue
            if any(not math.isfinite(v) for point in mesh.vertices for v in point.co):
                bpy.data.meshes.remove(mesh)
                raise SatkError("BAD_PARAMS", "the source contains non-finite vertex coordinates")
            mesh.transform(obj.matrix_world)
            if obj.matrix_world.determinant() < 0:
                mesh.flip_normals()
            copy = bpy.data.objects.new(f"cv_{plan['name']}_{len(made):03d}", mesh)
            coll.objects.link(copy)
            made.append(copy)
        if not made:
            raise SatkError("BAD_PARAMS", "the source has no visible mesh faces",
                            hint="export at least one visible, static mesh")
        complete = True
    finally:
        # On failure also discard partial evaluated copies. Existing session objects always survive.
        keep = before | (set(made) if complete else set())
        for obj in list(set(bpy.data.objects) - keep):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in set(bpy.data.collections) - collections_before:
            if not any(obj in keep for obj in collection.all_objects):
                bpy.data.collections.remove(collection)
    state = {"source": [o.name for o in made], "collection": coll.name,
             "dependencies": sorted(dependencies), "inputs": inputs}
    return C.save(ctx, folder, key, state, "import", {"objects": len(made), "skipped_hidden_or_nonmesh": skipped,
                                                      "tris": sum(C.tris(o) for o in made), "changed": state["source"]})


def _bounds(objects) -> tuple[Vector, Vector]:
    coords = [o.matrix_world @ v.co for o in objects for v in o.data.vertices]
    return Vector([min(v[i] for v in coords) for i in range(3)]), Vector([max(v[i] for v in coords) for i in range(3)])


def normalize_method(ctx, params: dict) -> dict:
    """Normalize imported axes and apply target L,W,H metres; plan=<plan.json>. Vehicle length runs along +Y."""
    plan, folder, key, state = C.load(ctx, params, "import")
    objects = C.objects(state["source"])
    lo, hi = _bounds(objects)
    extent = hi - lo
    rotated = False
    if plan["group"] == "vehicle" and extent.x > extent.y and plan["dims"][0] > plan["dims"][1]:
        rot = Matrix.Rotation(math.pi / 2, 4, "Z")
        for o in objects:
            o.data.transform(rot)
        lo, hi = _bounds(objects)
        extent = hi - lo
        rotated = True
    dims = Vector((plan["dims"][1], plan["dims"][0], plan["dims"][2]))
    if min(extent) <= 1e-9:
        raise SatkError("BAD_PARAMS", "a source bounding-box dimension is zero",
                        hint="give the mesh thickness in the source application before converting")
    center = (lo + hi) / 2
    # A kit vehicle's ground and dummy origins are in its exemplar's frame space.
    template = plan["template"]
    target_z = 0.0
    if plan["group"] == "vehicle":
        target_z = template["dims"]["bbox_like"][0][2] * template["dims"]["scale"][2]
    clearance = 0.0
    has_wheel = any(role_of(s.material.name) == "wheel" for o in objects for s in o.material_slots if s.material)
    if plan["kind"] == "automobile" and not has_wheel:
        wheel_z = [f["matrix"][11] for f in template["frames"] if f["name"].startswith("wheel_")
                   and f["name"].endswith("_dummy") and f["parent"] == 0]
        if wheel_z:
            # A body-only source excludes the kit wheels: reserve their ground clearance in the total height.
            bottom = min(wheel_z) - float(template["anchors"].get("wheel_scale", 0.7)) * 0.1
            clearance = max(0.0, min(float(dims.z) * 0.3, bottom - target_z))
            target_z += clearance
            dims.z -= clearance
    scale = Vector([dims[i] / extent[i] for i in range(3)])
    transform = Matrix.Translation((0, 0, target_z)) @ Matrix.Diagonal((*scale, 1.0)) \
        @ Matrix.Translation((-center.x, -center.y, -lo.z))
    for o in objects:
        o.data.transform(transform)
        o.data.update()
    return C.save(ctx, folder, key, state, "normalize", {"dims": plan["dims"], "rotated_to_y": rotated,
                                                         "wheel_clearance": round(clearance, 4),
                                                         "scale": [round(v, 6) for v in scale], "changed": state["source"]})


def clean_method(ctx, params: dict) -> dict:
    """Merge coincident vertices, remove hidden/degenerate faces and loose vertices; plan=<plan.json>."""
    plan, folder, key, state = C.load(ctx, params, "normalize")
    objects = C.objects(state["source"])
    dist = max(plan["dims"]) * plan["presets"]["weld_relative"]
    before = sum(C.tris(o) for o in objects)
    welded = 0
    for o in objects:
        bm = bmesh.new()
        try:
            bm.from_mesh(o.data)
            hidden = [f for f in bm.faces if f.hide]
            if hidden:
                bmesh.ops.delete(bm, geom=hidden, context="FACES")
            n = len(bm.verts)
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=dist)
            welded += n - len(bm.verts)
            bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=dist * 0.1)
            dead = [f for f in bm.faces if f.calc_area() <= dist * dist * 0.01]
            if dead:
                bmesh.ops.delete(bm, geom=dead, context="FACES")
            loose = [v for v in bm.verts if not v.link_faces]
            if loose:
                bmesh.ops.delete(bm, geom=loose, context="VERTS")
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
            bm.to_mesh(o.data)
        finally:
            bm.free()
    after = sum(C.tris(o) for o in objects)
    if not after:
        raise SatkError("BAD_PARAMS", "cleaning removed every face (the source was degenerate)")
    return C.save(ctx, folder, key, state, "clean", {"before": before, "after": after, "welded": welded,
                                                     "changed": state["source"]})


def _role(mat, plan: dict) -> str:
    role = role_of(mat.name if mat else "")
    if role == "generic" or (plan["group"] == "world" and role in ("body", "interior", "wheel")):
        default = plan["presets"]["default_roles"]
        role = default.get(plan["kind"], default[plan["group"]])
    return role if role in plan["presets"]["roles"] else "generic"


def _group_sources(objects: list, plan: dict, collection) -> dict[str, object]:
    groups: dict[str, list] = {}
    for obj in objects:
        materials = list(obj.data.materials)
        roles = {p.material_index: _role(materials[p.material_index] if p.material_index < len(materials) else None, plan)
                 for p in obj.data.polygons}
        for role in sorted(set(roles.values())):
            bm = bmesh.new()
            try:
                bm.from_mesh(obj.data)
                bmesh.ops.delete(bm, geom=[f for f in bm.faces if roles[f.material_index] != role], context="FACES")
                loose = [v for v in bm.verts if not v.link_faces]
                if loose:
                    bmesh.ops.delete(bm, geom=loose, context="VERTS")
                mesh = obj.data.copy()
                bm.to_mesh(mesh)
            finally:
                bm.free()
            part = bpy.data.objects.new(f"cv_{role}_source", mesh)
            collection.objects.link(part)
            groups.setdefault(role, []).append(part)
    result = {}
    for role, parts in sorted(groups.items()):
        root = bpy.data.objects.new(f"cv_{plan['name']}_{role}_hi", None)
        collection.objects.link(root)
        mesh = merged_mesh(parts, root, root.name)
        C.real_materials(mesh)
        bpy.data.objects.remove(root, do_unlink=True)
        root = bpy.data.objects.new(f"cv_{plan['name']}_{role}_hi", mesh)
        collection.objects.link(root)
        result[role] = root
        for part in parts:
            mesh = part.data
            bpy.data.objects.remove(part, do_unlink=True)
            if not mesh.users:
                bpy.data.meshes.remove(mesh)
    return result


def _anchors(mesh, seam_vertices: set[int], target: int) -> set[int]:
    """Sample redundant seam chains in object space, leaving room for the curved surface."""
    import numpy as np

    points = np.array([tuple(v.co) for v in mesh.vertices])
    # Keep the silhouette extrema even when a source's UV seam runs along only one side.
    keep = set(map(int, points.argmin(axis=0))) | set(map(int, points.argmax(axis=0)))
    # Already simple disconnected details (a grille bar, latch or pane) should not collapse to slivers
    # while a dense curved surface still has vertices to spare. Bound this reserve by the whole budget.
    neighbours = [set() for _ in mesh.vertices]
    for edge in mesh.edges:
        a, b = edge.vertices
        neighbours[a].add(b)
        neighbours[b].add(a)
    visited = set()
    for start in range(len(neighbours)):
        if start in visited:
            continue
        component, pending = set(), [start]
        visited.add(start)
        while pending:
            vertex = pending.pop()
            component.add(vertex)
            for other in neighbours[vertex] - visited:
                visited.add(other)
                pending.append(other)
        if len(component) <= 8 and len(keep | component) <= target // 4:
            keep.update(component)
    pool = np.array(sorted(seam_vertices), dtype=int)
    if not len(pool):
        return keep
    limit = min(len(pool), max(8, min(256, target // 10)))
    distance = np.full(len(pool), np.inf)
    for index in sorted(keep):
        distance = np.minimum(distance, ((points[pool] - points[index]) ** 2).sum(axis=1))
    for _ in range(limit):
        index = int(pool[distance.argmax()])
        if distance.max() <= 1e-14:
            break
        keep.add(index)
        distance = np.minimum(distance, ((points[pool] - points[index]) ** 2).sum(axis=1))
    return keep


def _surface_error(source, reduced) -> float:
    """Bidirectional sampled surface distance; catches a seam-pinned mesh collapsing to a sliver."""
    from mathutils.bvhtree import BVHTree

    error = 0.0
    for a, b in ((source, reduced), (reduced, source)):
        tree = BVHTree.FromPolygons([v.co for v in a.vertices], [list(f.vertices) for f in a.polygons])
        stride = max(1, len(b.vertices) // 4096)
        for vertex in list(b.vertices)[::stride]:
            nearest = tree.find_nearest(vertex.co)
            if nearest and nearest[0] is not None:
                error = max(error, nearest[3])
    return error


def _simplify(obj, target: int, angle: float) -> dict:
    before = C.tris(obj)
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.index_update()
        bm.edges.index_update()
        seams = _uv_seam_edges(bm)
        for e in bm.edges:
            e.seam = e.seam or e.index in seams or len({f.material_index for f in e.link_faces}) > 1
        bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(angle), verts=list(bm.verts), edges=list(bm.edges),
                                use_dissolve_boundaries=False, delimit={"NORMAL", "MATERIAL", "SEAM", "SHARP", "UV"})
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    planar = C.tris(obj)
    # Seams stay marked. Preserve spaced samples rather than pinning every redundant vertex:
    # a dense polar UV fan must not consume the entire budget and flatten the rest of a sphere.
    protected = {v for e in obj.data.edges if e.use_seam or e.use_edge_sharp for v in e.vertices}
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.index_update()
        protected.update(v.index for e in bm.edges if e.is_boundary for v in e.verts)
    finally:
        bm.free()
    original = obj.data.copy()
    seam_count = len(protected)
    protected = _anchors(obj.data, protected, target)
    protected_coords = {tuple(round(c, 6) for c in obj.data.vertices[i].co) for i in protected}
    group = obj.vertex_groups.new(name="satk_convert_collapse")
    free = [v.index for v in obj.data.vertices if v.index not in protected]
    if free:
        group.add(free, 1.0, "REPLACE")
    if protected:
        group.add(sorted(protected), 0.0, "REPLACE")
    reached = planar <= target
    for _ in range(5):
        count = C.tris(obj)
        if count <= target:
            reached = True
            break
        C.select(obj)
        modifier = obj.modifiers.new("satk_convert_reduce", "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = max(0.001, target / count * 0.995)
        modifier.use_collapse_triangulate = True
        modifier.vertex_group = group.name
        modifier.vertex_group_factor = 1000.0
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        coords = {tuple(round(c, 6) for c in v.co) for v in obj.data.vertices}
        if not protected_coords.issubset(coords):
            old = obj.data
            obj.data = original.copy()
            if not old.users:
                bpy.data.meshes.remove(old)
            break
        if C.tris(obj) >= count:
            break
    deviation = _surface_error(original, obj.data)
    bpy.data.meshes.remove(original)
    group = obj.vertex_groups.get("satk_convert_collapse")
    if group is not None:
        obj.vertex_groups.remove(group)
    return {"before": before, "planar": planar, "after": C.tris(obj), "target": target,
            "seam_vertices": seam_count, "preserved_samples": len(protected),
            "surface_error": round(deviation, 6), "reached": reached or C.tris(obj) <= target}


def reduce_method(ctx, params: dict) -> dict:
    """Planar dissolve, then collapse only above the engine safety cap (no triangle target), preserving material/UV seams; plan=<plan.json>."""
    plan, folder, key, state = C.load(ctx, params, "clean")
    coll = bpy.data.collections[state["collection"]]
    source = C.objects(state["source"])
    groups = _group_sources(source, plan, coll)
    total = sum(C.tris(o) for o in groups.values())
    # Triangle counts are never a target: the source keeps its detail and only a mesh above the engine cap
    # (65,535 vertices per geometry, 3 per triangle at worst) is collapsed, each role by its share.
    cap = int(plan["limit"]["max_tris"])
    rows, targets = {}, {}
    for role, hi in groups.items():
        low = bpy.data.objects.new(hi.name[:-3] + "_lo", hi.data.copy())
        coll.objects.link(low)
        count = max(4, math.floor(cap * C.tris(hi) / total)) if total > cap else max(4, C.tris(hi))
        rows[role] = _simplify(low, count, plan["presets"]["dissolve_degrees"])
        targets[role] = low.name
        hi.hide_render = True
        hi.hide_set(True)
    for obj in source:
        obj.hide_render = True
        obj.hide_set(True)
    state.update(high={r: o.name for r, o in groups.items()}, low=targets)
    count = sum(v["after"] for v in rows.values())
    warnings = [f"CHECK_FAILED: {r} keeps {v['after']} triangles to preserve its seam samples, above the engine "
                f"safety share {v['target']}" for r, v in rows.items() if not v["reached"] and count > cap]
    warnings += [f"CHECK_FAILED: {r} reduction deviates {v['surface_error']:.4f} m from the source; "
                 "review the silhouette in the saved blend" for r, v in rows.items()
                 if v["surface_error"] > max(plan["dims"]) * 0.04]
    return C.save(ctx, folder, key, state, "reduce", {"roles": rows, "tris": sum(v["after"] for v in rows.values()),
                                                      "warn": warnings, "changed": list(targets.values())})
