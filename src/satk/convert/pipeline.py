"""Compose the existing studio, kit, style and preview APIs; never import Blender into the CLI."""

from __future__ import annotations

import json
import math
import os
import uuid
from pathlib import Path

from ..core import paths
from ..core.errors import SatkError
from ..core.registry import get_op, report_progress
from . import plan as P

# K3 exposes ctx.methods for plug-ins. Loading is explicit and journaled; no studio-owned file is patched.
BOOTSTRAP = ("from satk_blender.convert.methods import install\n"
             "result['methods'] = install(ctx)\n")


def install(session: str, timeout: float = 120.0) -> None:
    from ..blender import runner
    from ..studio import api

    if not math.isfinite(timeout) or timeout <= 0:
        raise SatkError("BAD_PARAMS", "timeout must be finite and positive")
    runner.ensure_dragonff()
    api.call("python", {"code": BOOTSTRAP}, session=session, stats="none", timeout=timeout)


def _call(name: str, args: dict) -> dict:
    result = get_op(name).call(args)
    if not result.get("ok", True):
        e = result.get("error", {})
        raise SatkError(e.get("code", "CHECK_FAILED"), e.get("msg", f"{name} failed"), hint=e.get("hint"))
    return result


def run(model: str, *, kind: str = "prop", like: str | None = None, tier: str = "sa_plus",
        dims: list[float] | None = None, out: str | None = None, name: str | None = None,
        session: str | None = None, profile: str = "vanilla", timeout: float = 600.0) -> dict:
    if not math.isfinite(timeout) or timeout <= 0:
        raise SatkError("BAD_PARAMS", "timeout must be finite and positive")
    p, path = P.prepare(model, kind=kind, like=like, tier=tier, dims=dims, out=out, name=name, profile=profile)
    with P.lock(path.parent):
        return _run(p, path, session=session, timeout=timeout)


def _run(p: dict, path: Path, *, session: str | None, timeout: float) -> dict:
    from ..studio import api, launcher
    from . import finish

    profile, tier, like = p["profile"], p["tier"], p["like"]
    folder = path.parent
    manifest = folder / "conversion.json"
    if manifest.is_file():
        previous = P.json_read(manifest)
        if previous.get("key") == p["key"] and P.unchanged(previous.get("inputs", {})) \
                and P.unchanged(previous.get("artifacts", {})):
            return dict(previous["result"], cached=True)
    report_progress(0, 9, "import and class scale")
    owned = session is None
    active = session or f"convert_{uuid.uuid4().hex[:10]}"
    if owned:
        launcher.start(active, owner_pid=os.getpid(), idle=max(600, int(timeout) + 60))
    try:
        install(active, timeout)
        for i, step in enumerate(("import", "normalize", "clean", "reduce", "bake"), 1):
            api.call(f"convert.{step}", {"plan": paths.jpath(path)}, session=active, stats="none", timeout=timeout)
            report_progress(i, 9, step)
        finished = finish.run(str(path))
        blend = folder / "converted.blend"
        api.call("convert.assemble", {"plan": paths.jpath(path)}, session=active, save=str(blend),
                 stats="none", timeout=timeout)
        report_progress(6, 9, "kit export and collision")
        exported = _call("kit.export", {"model": p["name"], "session": active, "out": str(folder / "package"),
                                        "col": "auto", "check": True, "lint": True,
                                        "profile": profile, "timeout": timeout})
    finally:
        if owned:
            try:
                launcher.stop(active)
            finally:
                api.close_all()
    dff = exported["files"]["dff"]
    report_progress(7, 9, "asset and texture checks")
    checked = _call("asset.check", {"target": dff, "like": like, "cls": p["style_class"], "tier": tier,
                                     "full": True, "limit": 500, "profile": profile})
    from ..style import measure, subject

    metrics = measure.measure(subject.load(dff, profile)[0].scene)
    tex_checks = finish.check_txd(exported["files"]["txd"], finished["textures"], p["texture_distribution"])
    report_progress(8, 9, "class lineup")
    preview = _call("blender.preview", {"subject": dff, "like": p["template"]["like"]["sid"],
                                        "lineup": "class", "peers": 2, "passes": ["game", "clay", "wire"],
                                        "views": ["side", "3q"], "size": 256, "profile": profile, "timeout": timeout})
    report = P.json_read(folder / "stages.json")
    inputs = report.get("inputs", p["input_files"])
    if not P.unchanged(inputs):
        raise SatkError("REVISION", "a source model or texture changed during conversion",
                        hint="repeat the conversion in a new output folder after saving the source")
    warnings = list(p.get("warn", [])) + list(report.get("warn", [])) + list(exported.get("warn", []))
    if checked.get("counts", {}).get("error", 0):
        warnings.append("CHECK_FAILED: the package has structural errors; inspect asset.check before using it")
    for row in tex_checks:
        if row["verdict"] != "in":
            warnings.append(f"CHECK_FAILED: texture {row['texture']} is outside its {row['role']} style band")
    # the triangle count is a plain number (never a target); the engine limits are asset.check's engine rows
    count = metrics.get(p["limit"]["metric"])
    # the class shading numbers are a reference for the reviewer (in_band is info), never a warning
    shading = {k: {"actual": metrics[k], "lo": band["lo"], "hi": band["hi"],
                   "in_band": band["lo"] <= metrics[k] <= band["hi"]}
               for k, band in p["bands"].items() if k.startswith("shade.") and k in metrics}
    from .package import mta

    mta_package = mta(p, exported, folder)
    result = {"name": p["name"], "kind": p["kind"], "tier": tier, "out": paths.jpath(folder),
              "plan": paths.jpath(path), "blend": paths.jpath(blend), "source": p["source"],
              "tris": count, "limit": p["limit"],
              "metrics": {k: metrics[k] for k in ("geo.tris", "veh.hd_tris", "shade.normal_bend", "shade.flat_share",
                                                    "uv.zero_area_share", "dims.L", "dims.W", "dims.H") if k in metrics},
              "package": exported, "mta": mta_package, "check": checked, "shading": shading,
              "textures": tex_checks, "preview": preview,
              "stages": paths.jpath(folder / "stages.json"), "cached": False, "warn": warnings}
    files = [x for x in folder.rglob("*") if x.is_file() and x != manifest
             and x.suffix != ".blend1" and x.name != ".convert.lock"]
    files += [Path(f) for f in preview.get("files", {}).values() if isinstance(f, str) and Path(f).is_file()]
    paths.atomic_write(manifest, json.dumps({"key": p["key"], "inputs": inputs, "artifacts": P.fingerprints(files),
                                            "result": result}, ensure_ascii=False, indent=1))
    report_progress(9, 9, "done")
    return result
