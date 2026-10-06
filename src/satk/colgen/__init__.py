"""Collision generator (``satk col gen|check|surface|derive``): COL3/COL2 models from render DFFs.

Modules: :mod:`.geom` (render mesh in model space), :mod:`.surface` (texture -> surface table, lighting),
:mod:`.hull` (outer convex hull with a face budget), :mod:`.decimate` (QEM), :mod:`.voxel` (solid voxels:
boxes, spheres, closed shadow surface), :mod:`.build` (one ColModel per DFF), :mod:`.check` (structural
checks), :mod:`.derive` (the table from a game's own collisions), :mod:`.sources` (what a target names).
Writing goes through :mod:`satk.rw.col`. numpy is imported on first use (:mod:`._np`), never at import time.
"""
