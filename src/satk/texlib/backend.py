"""MIT process boundary to the GPL shader-node baker; no bpy imports here."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from ..core import paths
from ..core.errors import SatkError


def bake(recipe: dict, size: int, seed: int) -> dict:
    from ..blender import runner

    runner.blender_exe()  # fail before making a job if Blender is unavailable
    script = runner.addon_dir() / "texlib" / "bake.py"
    if not script.is_file():
        raise SatkError("NOT_READY", "the texlib Blender baker is missing",
                        hint="use a full satk checkout including blender/satk_blender/texlib")
    job = paths.ensure_writable(Path(tempfile.gettempdir()) / ("satk-texlib-" + uuid.uuid4().hex))
    job.mkdir(parents=True)
    request = {"recipe": recipe, "size": size, "seed": seed, "out": paths.jpath(job)}
    paths.atomic_write(job / "request.json", json.dumps(request, sort_keys=True))
    env = runner.blender_env(job)
    # Keep even Blender's writable profile entirely inside this job.
    for part in ("CONFIG", "SCRIPTS", "EXTENSIONS", "DATAFILES", "RESOURCES"):
        directory = paths.ensure_writable(job / "profile" / part.lower())
        directory.mkdir(parents=True, exist_ok=True)
        env["BLENDER_USER_" + part] = str(directory)
    log = job / "blender.log"
    code, elapsed = runner.run_blender(
        ["-b", "--factory-startup", *runner.thread_args(), "--python-exit-code", "1",
         "--python", str(script), "--", str(job / "request.json")],
        log=log, timeout=60, env=env, cwd=job,
    )
    response = job / "response.json"
    if code or not response.is_file():
        raise SatkError("EXTERNAL_TOOL", f"texlib Cycles bake failed (exit {code})",
                        hint=f"inspect {paths.jpath(log)}",
                        data={"log": paths.jpath(log), "tail": runner.log_tail(log)})
    try:
        data = json.loads(response.read_text(encoding="utf-8"))
        if data.get("ok") is not True or not Path(data["colour"]).is_file():
            raise ValueError("no baked colour image")
        if recipe.get("alpha") and not Path(data["mask"]).is_file():
            raise ValueError("no baked alpha image")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SatkError("EXTERNAL_TOOL", f"invalid texlib bake response: {exc}",
                        data={"log": paths.jpath(log)}) from None
    data.update(process_seconds=round(elapsed, 4), log=paths.jpath(log))
    return data
