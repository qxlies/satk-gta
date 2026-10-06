"""Registry of the canonical style metrics: one name, unit, scope and definition per number.

Every doc, lint rule, brief, profile and check uses these names (``veh.hd_tris``, ``shade.normal_bend``,
...). ``computed_by`` names the function that computes a metric today (``mesh_metrics`` =
:func:`satk.style.metrics.mesh_metrics`, ``measure`` = :func:`satk.style.measure.measure`, ``texture`` =
:func:`satk.style.texture.texture_stats`); an empty value means the definition is fixed but nothing
computes it yet. A name with ``[<...>]`` takes a parameter
(``part.tris[chassis]``); :func:`lookup` resolves it.

Pure data, no imports from satk: it loads in Blender's Python too.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Metric", "REGISTRY", "SCOPES", "UNITS", "CONVENTIONS", "lookup", "rows"]


@dataclass(frozen=True, slots=True)
class Metric:
    """One canonical metric. ``unit`` is a key of :data:`UNITS`, ``scope`` a key of :data:`SCOPES`."""

    name: str
    unit: str
    scope: str
    definition: str
    computed_by: str = ""


#: What a metric is measured on.
SCOPES = {
    "mesh": "any triangle mesh: one part, or the parts of a model merged in model space",
    "model": "a whole DFF (all its atomics)",
    "vehicle": "a vehicle DFF (frames and dummies of the vehicle conventions)",
    "part": "one frame (part) of a model",
    "texture": "one texture, mip level 0",
    "map": "a map model with prelight (vertex colours)",
    "collision": "one COL model",
}

#: Units.
UNITS = {
    "count": "number of items",
    "share": "fraction 0..1",
    "ratio": "quotient of two counts or sizes",
    "deg": "degrees",
    "m": "metres (game units)",
    "m2": "square metres",
    "1/m2": "items per square metre",
    "cm": "centimetres",
    "uv": "UV units (1.0 = one texture width or height)",
    "px/m": "texture pixels per metre",
    "luma": "0..255, luma = 0.299 R + 0.587 G + 0.114 B",
    "hsv": "0..1 (HSV saturation or value)",
    "level": "integer level 0..15",
    "bbox": "[minx, miny, minz, maxx, maxy, maxz] in metres",
    "bins": "dict bin -> share",
}

#: Conventions shared by every metric.
CONVENTIONS = {
    "axes": "game axes: +X right, +Y forward, +Z up; metres",
    "weld": "topology (edges, pieces, dihedrals) is measured on the mesh welded by position on a 1e-4 m grid; "
            "parts of a model never weld together",
    "degenerate": "a triangle with |cross(b - a, c - a)| <= 1e-12 has no face normal and is skipped by "
                  "normal and dihedral metrics",
    "percentiles": "p10/p50/p90 = numpy.percentile (linear interpolation) over one value per model of the "
                   "named peer set; every answer names its peer set",
    "selection": "HD selection = every atomic that is not *_dam or *_vlo, the wheel mesh once "
                 "(satk.style.metrics.is_hd_part); vehicles also report veh.hd_tris (chassis + *_ok parts)",
    "missing": "a metric that is undefined for the input (no normals, no UVs, no hard edge) is left out, "
               "never null",
}

_M = Metric
_DEFS = (
    # ---- mesh_metrics (contract K1)
    _M("geo.tris", "count", "mesh", "Triangles of the mesh as given (degenerate ones included).", "mesh_metrics"),
    _M("dff.verts", "count", "mesh",
       "Estimate of the vertices a DFF stores: distinct (vertex, corner normal rounded to 1e-3, corner UV "
       "rounded to 1e-5) over all triangle corners. On a DFF it equals the vertices used by triangles.",
       "mesh_metrics"),
    _M("dff.verts_per_tri", "ratio", "mesh",
       "dff.verts / geo.tris. Split normals and UV seams raise it (one vertex per corner gives 3.0).",
       "mesh_metrics"),
    _M("geo.area_m2", "m2", "mesh", "Total triangle area.", "mesh_metrics"),
    _M("geo.tris_per_m2", "1/m2", "mesh", "geo.tris / geo.area_m2 (triangle density of the surface).",
       "mesh_metrics"),
    _M("geo.median_edge_m", "m", "mesh", "Median length of the unique edges of the welded mesh.", "mesh_metrics"),
    _M("geo.median_dihedral", "deg", "mesh",
       "Median, over the manifold edges of the welded mesh (exactly two triangles), of the angle between the "
       "two face normals (0 = coplanar). Flat skins cut into many triangles lower it.", "mesh_metrics"),
    _M("geo.pieces", "count", "mesh",
       "Connected pieces of the welded mesh (triangles sharing a welded vertex are connected).", "mesh_metrics"),
    _M("geo.largest_piece_share", "share", "mesh", "Triangles of the largest piece / geo.tris.", "mesh_metrics"),
    _M("geo.sliver_share", "share", "mesh",
       "Share of triangles whose smallest interior angle is below 8 deg (degenerate ones included).",
       "mesh_metrics"),
    _M("geo.open_edges", "count", "mesh", "Edges of the welded mesh used by exactly one triangle.", "mesh_metrics"),
    _M("geo.nonmanifold_edges", "count", "mesh",
       "Edges of the welded mesh used by three or more triangles (double-sided faces count here).",
       "mesh_metrics"),
    _M("shade.normal_bend", "deg", "mesh",
       "Area-weighted mean, over non-degenerate triangles, of the mean angle between each corner normal and "
       "the face normal. 0 = fully flat (faceted); higher = softer shading.", "mesh_metrics"),
    _M("shade.flat_share", "share", "mesh",
       "Share of non-degenerate triangles whose three corner normals are all within 1 deg of the face normal "
       "(flat shaded).", "mesh_metrics"),
    _M("shade.hard_edge_share", "share", "mesh",
       "Share of the manifold edges of the welded mesh that are hard: at one of its two ends the corner "
       "normals of the two triangles differ by more than 1 deg (split normals).", "mesh_metrics"),
    _M("shade.hard_at_seam", "share", "mesh",
       "Share of the hard edges that lie on a seam: a material border, or a UV seam (the corner UVs of the "
       "two triangles differ by more than 1e-4 at one end).", "mesh_metrics"),
    _M("shade.hard_by_dihedral", "bins", "mesh",
       "Per dihedral bin 0-10, 10-20, 20-30, 30-45, 45-60, 60-90, 90+ deg (angle between the two face "
       "normals of a manifold edge): share of the edges in the bin that are hard. Bins without edges are "
       "left out.", "mesh_metrics"),
    _M("uv.zero_area_share", "share", "mesh",
       "Share of triangles whose UV set 0 area is (near) zero: |cross(uv1 - uv0, uv2 - uv0)| < 1e-7, i.e. "
       "one texel smeared over the face.", "mesh_metrics"),
    _M("uv.span", "uv", "mesh", "[u, v] range (max - min) of UV set 0 over all corners; above 1 = tiling.",
       "mesh_metrics"),
    _M("bbox", "bbox", "mesh",
       "Bounding box of the vertices used by triangles (model space for merged parts).", "mesh_metrics"),
    # ---- vehicles and models
    _M("veh.hd_tris", "count", "vehicle",
       "Triangles of chassis + every *_ok part except exhaust_ok + the wheel mesh once: the body that renders "
       "undamaged. Optional parts (extraN, misc_*, plates without _ok) are not counted "
       "(satk.style.measure.hd_tris).", "measure"),
    _M("veh.hi_tris", "count", "vehicle",
       "veh.hd_tris with the wheel mesh counted once per wheel dummy (4 on cars).", "measure"),
    _M("veh.wheel_mesh_d", "m", "vehicle",
       "Diameter of the wheel mesh: max of its Y and Z extent (the game scales it to wheel_scale).", "measure"),
    _M("file.tris", "count", "model", "Triangles of every geometry of the DFF (damage and VLO included).",
       "measure"),
    _M("mat.count", "count", "mesh", "Materials of the geometries of the HD selection.", "measure"),
    _M("part.tris[<frame>]", "count", "part", "Triangles of the geometry of one frame (atomic).", "measure"),
    _M("dam.ok_ratio[<part>]", "ratio", "part", "tris(<part>_dam) / tris(<part>_ok).", "measure"),
    _M("dam.disp_cm", "cm", "part",
       "Distance of each <part>_dam vertex to the nearest point of the <part>_ok surface (both in the part "
       "frame); reported as p50 and p90."),
    _M("geo.thirds", "share", "vehicle",
       "Triangle shares [front, middle, rear] of the HD selection by triangle centroid in thirds of the bbox "
       "length along +Y.", "measure"),
    _M("dims.L", "m", "model", "Length: bbox size along Y of the HD selection.", "measure"),
    _M("dims.W", "m", "model",
       "Width: bbox size along X of the chassis (vehicles: mirrors on doors excluded), else of the HD "
       "selection.", "measure"),
    _M("dims.H", "m", "model", "Height: bbox size along Z of the HD selection (peds stood upright).", "measure"),
    _M("dims.size", "m", "model", "Largest bbox side of the HD selection (sets the size bucket of map models).",
       "measure"),
    _M("dims.wheelbase", "m", "vehicle", "Y distance between the front and the rear left wheel dummies.",
       "measure"),
    _M("dims.track", "m", "vehicle", "X distance between the left and the right front wheel dummies.", "measure"),
    _M("dims.wheel_d", "m", "vehicle",
       "Wheel diameter: wheel_scale of the IDE line (the game scales the wheel mesh to it).", "measure"),
    _M("dims.L_over_wheel", "ratio", "vehicle",
       "dims.L / wheel diameter (dims.wheel_d, else veh.wheel_mesh_d): the length in wheels, an intrinsic "
       "scale anchor.", "measure"),
    _M("dims.W_over_wheel", "ratio", "vehicle", "dims.W / wheel diameter.", "measure"),
    _M("dims.H_over_wheel", "ratio", "vehicle", "dims.H / wheel diameter.", "measure"),
    _M("dims.wheelbase_over_wheel", "ratio", "vehicle", "dims.wheelbase / wheel diameter.", "measure"),
    _M("dims.track_over_wheel", "ratio", "vehicle", "dims.track / wheel diameter.", "measure"),
    # ---- texturing and textures
    _M("uv.span_max", "uv", "mesh", "Largest of the two uv.span values (tiling repeats).", "measure"),
    _M("uv.texel_px_m", "px/m", "mesh",
       "sqrt(sum of UV areas x texture width x height / sum of surface areas) over the HD triangles whose "
       "texture size is known (pooled over all textures; vanilla: the model's TXD chain).", "measure"),
    _M("uv.texel_px_m[<texture>]", "px/m", "part",
       "sqrt(sum of UV areas x texture width x height / sum of surface areas) over the triangles that use "
       "<texture> (pooled per model; profiles take one value per model)."),
    _M("tex.colours", "count", "texture", "Exact distinct RGB colours."),
    _M("tex.colours15", "count", "texture", "Distinct colours with every channel reduced to 5 bits (15-bit colour)."),
    _M("tex.lum_mean", "luma", "texture", "Mean luma of the pixels with alpha > 0."),
    _M("tex.lum_std", "luma", "texture", "Standard deviation of the luma of the pixels with alpha > 0."),
    _M("tex.sat_mean", "hsv", "texture", "Mean HSV saturation of the pixels with alpha > 0."),
    _M("tex.val_mean", "hsv", "texture", "Mean HSV value of the pixels with alpha > 0."),
    _M("tex.hf_energy", "luma", "texture",
       "Mean absolute 3x3 Laplacian of the luma: high-frequency detail (photo-like textures score high, flat "
       "vector art low)."),
    # ---- map lighting
    _M("light.prelit_lum_p50", "luma", "map", "Median luma of the day prelight colours of the vertices.",
       "measure"),
    _M("light.night_ratio", "ratio", "map", "Median night luma / median day luma of the vertices.", "measure"),
    _M("light.night_tint", "luma", "map", "Mean R - mean B of the night prelight colours (positive = warm).",
       "measure"),
    _M("light.lit_vertex_share", "share", "map",
       "Share of vertices whose night luma is above their day luma (lit by lamps at night).", "measure"),
    # ---- collision
    _M("col.spheres", "count", "collision", "Collision spheres.", "measure"),
    _M("col.boxes", "count", "collision", "Collision boxes.", "measure"),
    _M("col.mesh_faces", "count", "collision", "Faces of the collision mesh.", "measure"),
    _M("col.shadow_faces", "count", "collision", "Faces of the shadow mesh (COL3).", "measure"),
    _M("col.face_light_dominant", "level", "collision", "Most common face light value of the collision mesh faces."),
)

#: name -> :class:`Metric`, in definition order.
REGISTRY: dict[str, Metric] = {m.name: m for m in _DEFS}


def lookup(name: str) -> Metric | None:
    """The metric of ``name``; ``part.tris[chassis]`` resolves to ``part.tris[<frame>]``."""
    m = REGISTRY.get(name)
    if m is not None or "[" not in name:
        return m
    base = name.split("[", 1)[0]
    for k, v in REGISTRY.items():
        if k.split("[", 1)[0] == base and "[<" in k:
            return v
    return None


def rows(scope: str | None = None) -> list[list[str]]:
    """``[name, unit, scope, definition]`` rows (optionally one scope), for tables and docs."""
    return [[m.name, m.unit, m.scope, m.definition] for m in REGISTRY.values() if scope in (None, m.scope)]
