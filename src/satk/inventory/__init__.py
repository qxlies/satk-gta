"""satk.inventory: the task list of an asset (contract K6).

An asset project holds ``design/inventory.json``: every part and detail as an item (id, name, region, category,
how it is built, what it attaches to, the gate that builds it). Builders tag the geometry with the item ids
(``scene.tag``); :func:`satk.inventory.api.report` says which items are built, missing, unattached or rejected, from
the live Blender session or from an exported DFF and its sidecar. Starter lists per kind and detail level live in
``data/kit/inventory/<kind>.json`` (:mod:`satk.inventory.starter`); :mod:`satk.inventory.schema` validates.
Operations: ``asset.inventory``, ``inventory.starter``, ``inventory.validate``, ``inventory.mark``.
"""
