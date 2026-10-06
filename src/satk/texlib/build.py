"""Immutable output bundles and preview sheets for the procedural library."""

from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path

from ..core import paths
from ..core.envelope import table
from ..core.errors import SatkError, require_module
from .catalog import catalog, colour, recipe, validate_size_seed


def _json(data) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _signature() -> str:
    from ..blender.runner import addon_dir

    files = [Path(__file__).with_name(n) for n in ("build.py", "finish.py", "backend.py", "catalog.py")]
    files.append(addon_dir() / "texlib" / "bake.py")
    return hashlib.sha256(b"".join(p.read_bytes() if p.is_file() else b"missing" for p in files)).hexdigest()


def _key(data) -> str:
    return hashlib.sha256(_json(data).encode("utf-8")).hexdigest()[:20]


def _destination(out, *default) -> Path:
    root = Path(paths.cfg().paths.work).resolve()
    p = paths.ensure_writable(Path(out).absolute() if out else root.joinpath("out", "texlib", *default))
    if not p.resolve().is_relative_to(root):
        raise SatkError("PROTECTED_PATH", "texlib outputs must be inside the work directory",
                        hint="omit --out to use work/out/texlib")
    if p.exists() and not p.is_dir():
        raise SatkError("EXISTS", f"output is not a directory: {paths.jpath(p)}", hint="choose a new output directory")
    return p


def _cached(directory, key, expected_files):
    manifest = directory / "texlib.json"
    if not directory.exists() or not any(directory.iterdir()):
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format") != "satk.texlib-output/1":
            raise ValueError("invalid bundle manifest")
        if data["key"] != key:
            raise ValueError("the directory belongs to a different input")
        if not isinstance(data["sha256"], dict) or set(data["sha256"]) != set(expected_files):
            raise ValueError("incomplete file checksums")
        if not isinstance(data["result"], dict):
            raise ValueError("invalid cached result")
        for relative, expected in data["sha256"].items():
            p = directory / relative
            if not p.resolve().is_relative_to(directory.resolve()):
                raise ValueError("invalid cached file path")
            if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                raise ValueError(f"{relative} changed after verification")
        return data["result"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SatkError("EXISTS", f"texlib will not overwrite an existing output: {exc}",
                        hint="choose a new --out directory or restore the verified bundle") from None


def _publish(directory, key, files, result):
    # Never replace files in a pre-existing, non-empty directory, even after a failed bake.
    if directory.exists() and any(directory.iterdir()):
        raise SatkError("EXISTS", f"output appeared during baking: {paths.jpath(directory)}",
                        hint="choose another --out directory")
    paths.ensure_writable(directory)
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for relative, payload in files.items():
        payload = payload.encode("utf-8") if isinstance(payload, str) else payload
        paths.atomic_write(directory / relative, payload)
        hashes[relative] = hashlib.sha256(payload).hexdigest()
    paths.atomic_write(directory / "texlib.json", _json({"format": "satk.texlib-output/1", "key": key,
                                                       "sha256": hashes, "result": result}) + "\n")


def _png(array) -> bytes:
    Image = require_module("PIL.Image")
    stream = io.BytesIO()
    Image.fromarray(array).save(stream, format="PNG")
    return stream.getvalue()


def make(preset: str, *, size: int = 256, seed: int = 0, tint: str | None = None,
         out: str | None = None) -> dict:
    from . import backend
    from .finish import finish

    started = time.perf_counter()
    validate_size_seed(size, seed)
    r, rgb = recipe(preset), colour(tint)
    data = catalog()
    dist = {"roles": data["roles"]}
    key = _key({"recipe": r, "size": size, "seed": seed, "tint": rgb, "roles": data["roles"], "code": _signature()})
    directory = _destination(out, r["name"] + "-" + key)
    filename = r["name"] + ".png"
    locations = {"file": paths.jpath(directory / filename),
                 "dxt_preview": paths.jpath(directory / "preview" / filename),
                 "manifest": paths.jpath(directory / "texmod.json"), "out": paths.jpath(directory)}
    cached = _cached(directory, key, (filename, "preview/" + filename, "texmod.json"))
    if cached is not None:
        return {**cached, **locations, "cached": True}
    baked = backend.bake(r, size, seed)
    pixels, preview, quality = finish(baked, r, size, seed, rgb, dist)
    levels = 1 if r["role"] == "interior" else (9 if size == 256 else 8)
    pack = {"textures": [{"file": filename, "name": r["name"], "format": quality["format"], "levels": levels}]}
    result = {"preset": r["name"], "role": r["role"], "size": size, "seed": seed, "tint": rgb,
              **locations,
              "key": key, "cached": False, "engine": "CYCLES", "blender": baked["blender"],
              "bake_seconds": baked["bake_seconds"], "process_seconds": baked["process_seconds"],
              "build_seconds": round(time.perf_counter() - started, 4), "log": baked["log"],
              "style_source": data["source"], **quality}
    files = {filename: _png(pixels), "preview/" + filename: _png(preview), "texmod.json": _json(pack) + "\n"}
    _publish(directory, key, files, result)
    return result


def listing(*, previews=True, size=128, seed=0, out=None) -> dict:
    validate_size_seed(size, seed)
    data = catalog()
    presets = sorted(data["presets"])
    rows = [[name, data["presets"][name]["role"], "DXT3" if data["presets"][name].get("alpha") else "DXT1",
             data["presets"][name]["description"]] for name in presets]
    env = table(["preset", "role", "format", "description"], rows)
    if not previews:
        if out is not None:
            raise SatkError("BAD_PARAMS", "--out needs previews enabled")
        return env
    key = _key({"catalog": data, "size": size, "seed": seed, "code": _signature(), "kind": "sheet"})
    directory = _destination(out, "sheets", key)
    cached = _cached(directory, key, ("previews.png",))
    if cached is not None:
        return {**env, **cached, "sheet": paths.jpath(directory / "previews.png"), "cached": True}
    Image = require_module("PIL.Image")
    Draw = require_module("PIL.ImageDraw")
    cell = size + 12
    columns = 4
    sheet = Image.new("RGB", (cell * columns, (cell + 28) * ((len(presets) + columns - 1) // columns)), (35, 35, 35))
    draw = Draw.Draw(sheet)
    legend = []
    for i, name in enumerate(presets):
        result = make(name, size=size, seed=seed)
        x, y = (i % columns) * cell + 6, (i // columns) * (cell + 28) + 6
        with Image.open(result["dxt_preview"]) as im:
            # Checkerboard makes the transparent overlays visible without changing their pixels.
            background = Image.new("RGBA", (size, size), (105, 105, 105, 255))
            bd = Draw.Draw(background)
            for by in range(0, size, 8):
                for bx in range(0, size, 8):
                    if (bx // 8 + by // 8) % 2:
                        bd.rectangle((bx, by, bx + 7, by + 7), fill=(145, 145, 145, 255))
            background.alpha_composite(im.convert("RGBA"))
            sheet.paste(background.convert("RGB"), (x, y))
        draw.text((x, y + size + 2), f"{i + 1}. {name}", fill=(230, 230, 230))
        draw.text((x, y + size + 15), result["role"], fill=(165, 165, 165))
        legend.append([i + 1, name, result["file"]])
    stream = io.BytesIO()
    sheet.save(stream, format="PNG")
    result = {"sheet": paths.jpath(directory / "previews.png"), "size": size, "seed": seed,
              "legend": {"cols": ["cell", "preset", "file"], "rows": legend}, "cached": False}
    _publish(directory, key, {"previews.png": stream.getvalue()}, result)
    return {**env, **result}
