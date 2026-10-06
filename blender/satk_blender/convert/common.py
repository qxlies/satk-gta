# SPDX-License-Identifier: GPL-3.0-or-later
"""Small state and path helpers for the conversion K3 methods."""

from __future__ import annotations

import json
import os
from pathlib import Path

import bpy

from satk.core.errors import SatkError


def writable(ctx, path: Path) -> Path:
    root = os.path.normcase(os.path.realpath(ctx.host.out_root))
    dest = os.path.normcase(os.path.realpath(path))
    try:
        inside = os.path.commonpath([root, dest]) == root
    except ValueError:
        inside = False
    if not inside:
        raise SatkError("PROTECTED_PATH", "conversion outputs must stay inside the studio work directory")
    return path


def write(ctx, path: Path, data: dict) -> None:
    writable(ctx, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def load(ctx, p: dict, stage: str | tuple[str, ...] | None = None):
    try:
        path = Path(p["plan"]).resolve()
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError, TypeError) as e:
        raise SatkError("BAD_PARAMS", f"convert: cannot read plan: {e}",
                        hint="use the plan.json from satk convert prepare") from None
    writable(ctx, path.parent)
    from satk.convert.plan import validate

    validate(plan, path)
    key = "satk_convert_" + plan["key"][:16]
    state = json.loads(ctx.scene.get(key, "{}"))
    wanted = (stage,) if isinstance(stage, str) else stage
    if wanted and state.get("stage") not in wanted:
        raise SatkError("NOT_READY", f"convert: expected stage {' or '.join(wanted)}, got {state.get('stage', 'none')}",
                        hint="run import, normalize, clean, reduce, bake, convert finish, then assemble")
    return plan, path.parent, key, state


def save(ctx, folder: Path, key: str, state: dict, stage: str, result: dict) -> dict:
    state["stage"] = stage
    ctx.scene[key] = json.dumps(state)
    report_path = folder / "stages.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    if stage == "import":
        report = {"dependencies": state.get("dependencies", []), "inputs": state.get("inputs", {})}
    report[stage] = result
    if result.get("warn"):
        report.setdefault("warn", []).extend(result["warn"])
    write(ctx, report_path, report)
    return dict(result, report=report_path.as_posix())


def objects(names: list[str]) -> list:
    result = [bpy.data.objects.get(n) for n in names]
    if any(o is None or o.type != "MESH" for o in result):
        raise SatkError("NOT_READY", "conversion meshes were removed from the scene", hint="restore the saved blend or re-import")
    return result


def tris(obj) -> int:
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def real_materials(mesh) -> None:
    """Keep persistent material IDs after a kit merge of evaluated meshes (Blender 5.x)."""
    for index, material in enumerate(mesh.materials):
        if material is not None and material.is_evaluated:
            mesh.materials[index] = material.original


def select(obj) -> None:
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
