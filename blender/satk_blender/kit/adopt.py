# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.adopt``: turn an imported model (``satk blender import-model`` / DragonFF) into a kit model.

The DragonFF import already carries the frame order (``dff.frame_index``), atomics (``dff.atomic_index``)
and parenting; adopting adds the kit tags (``satk_kit`` on the clump, ``satk_role``/``satk_part``/
``satk_slot``/``satk_frame`` on the frame objects) and tags the collision collection, so ``kit.shade``,
the generators and ``kit.export`` work on an existing model (re-export, edits, round-trip checks).
Helper objects of the import (cloned wheels) stay out.
"""

from __future__ import annotations

import re

import bpy

from satk.core.errors import SatkError

from . import util as U

__all__ = ["adopt_method"]

_SUFFIX = re.compile(r"\.\d{3}$")


def _slot_of(name: str) -> tuple[str, str]:
    n = name.strip().lower()
    for suf, slot in (("_ok", "ok"), ("_dam", "dam"), ("_vlo", "vlo")):
        if n.endswith(suf):
            return n[: -len(suf)], slot
    return n, "flash" if n == "gunflash" else "hd"


def adopt_method(ctx, p: dict) -> dict:
    """Make an imported DFF collection a kit model (tags frames, slots and its collision) so kit.shade/kit.export work on it."""
    from satk.kit import kinds as K

    cands = [c for c in bpy.data.collections if c.name.lower().endswith(".dff") and not c.get("satk_kit")]
    want = p.get("collection")
    if want:
        coll = bpy.data.collections.get(str(want)) or bpy.data.collections.get(f"{want}.dff")
        if coll is None:
            raise SatkError("NOT_FOUND", f"kit.adopt: no collection {want!r}",
                            data={"did_you_mean": [c.name for c in cands][:5]})
    else:
        if len(cands) != 1:
            raise SatkError("AMBIGUOUS" if cands else "NOT_FOUND",
                            f"kit.adopt: {len(cands)} imported .dff collections; give 'collection'",
                            data={"collections": [c.name for c in cands][:10]})
        coll = cands[0]
    objs = [o for o in coll.objects if not o.get("satk_generated") and getattr(o.dff, "type", "OBJ") == "OBJ"
            and o.type in ("MESH", "EMPTY")]
    if not objs:
        raise SatkError("NOT_FOUND", f"kit.adopt: {coll.name} has no frame objects")
    sec = next((str(o["satk_sec"]) for o in objs if o.get("satk_sec")), "")
    kind = p.get("kind")
    if not kind:
        if sec == "cars":
            kind = "automobile"
        else:
            kind = K.kind_for(sec or "objs")
    kind = K.canonical(kind)
    group = K.get(kind)["group"]
    name = str(p.get("name") or coll.name[:-4]).lower()
    roots = 0
    for o in objs:
        frame = _SUFFIX.sub("", o.name)
        o["satk_frame"] = frame
        if o.parent is None or o.parent not in objs:
            o["satk_role"] = "root"
            roots += 1
        else:
            o["satk_role"] = "part" if o.type == "MESH" else "dummy"
        if o.type == "MESH":
            part, slot = _slot_of(frame)
            if o["satk_role"] == "root":
                part, slot = name, "hd"
            o["satk_part"], o["satk_slot"] = part, slot
    coll["satk_kit"] = kind
    coll["satk_name"] = name
    coll["satk_group"] = group
    coll["satk_tier"] = str(p.get("tier") or "vanilla")
    if coll.get("satk_sid"):
        coll["satk_like"] = str(coll["satk_sid"])
    ncol = 0
    for c in coll.children_recursive:
        cols = [o for o in c.objects if getattr(o.dff, "type", "") in ("COL", "SHA")]
        if cols:
            c["satk_col_of"] = name
            c["satk_col_embedded"] = group == "vehicle"
            for o in cols:
                o["satk_role"] = "shadow" if o.dff.type == "SHA" else "col"
            ncol += len(cols)
            break
    U.ensure_dff()
    return {"model": name, "kind": kind, "collection": coll.name, "frames": len(objs), "roots": roots,
            "slots": sum(1 for o in objs if o.type == "MESH"), "col": ncol,
            "changed": [o.name for o in objs if o.get("satk_role") == "root"][:1]}
