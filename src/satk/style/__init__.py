"""satk.style: the single source of truth for the vanilla GTA:SA style (metrics, classes, profiles, tiers).

* :mod:`satk.style.metrics` (contract K1): ``mesh_metrics(pos, tris, corner_normals=None, uv=None,
  mat=None)`` returns the canonical metric keys of one mesh. numpy only, no other satk import: it runs
  in Blender's bundled Python too.
* :mod:`satk.style.registry`: ``REGISTRY`` with the name, unit, scope and definition of every metric.
* :mod:`satk.style.dffmesh`: the same metrics for the parts of a DFF (``dff_metrics``).

Importing this package is cheap (no numpy until a metric is computed).
"""
