"""Adapt kit's exported files to the existing Blender MTA resource writer."""

from __future__ import annotations

from pathlib import Path

from ..core import paths


def mta(plan: dict, exported: dict, folder: Path) -> dict:
    from ..blender.packaging import client_lua, meta_xml

    dest = paths.ensure_writable(folder / "mta" / plan["name"])
    dest.mkdir(parents=True, exist_ok=True)
    files = {}
    for key, filename in exported["files"].items():
        source = Path(filename)
        with paths.open_ro(source) as stream:
            paths.atomic_write(dest / source.name, stream.read())
        files[key] = paths.jpath(dest / source.name)
    model = {"name": plan["name"], "id": int(plan["template"]["like"]["sid"].split(":")[1]),
             "sec": plan["template"]["ide"]["sec"], "files": files}
    models = [model]
    if "lod_dff" in files:
        models.append({"name": Path(files["lod_dff"]).stem, "sec": "objs",
                       "files": {"dff": files["lod_dff"], "txd": files["txd"]}})
    # HD and LOD atlases share the kit TXD. Import it for both model IDs, but list each download once.
    meta_models, seen = [], set()
    for entry in models:
        unique = {k: v for k, v in entry["files"].items() if k in ("dff", "txd", "col") and v not in seen}
        seen.update(unique.values())
        meta_models.append(dict(entry, files=unique))
    # Reuse the established resource format; no engine/client process is launched.
    paths.atomic_write(dest / "meta.xml", meta_xml(plan["name"], meta_models))
    paths.atomic_write(dest / "client.lua", client_lua(plan["name"], models))
    return {"out": paths.jpath(dest), "meta": paths.jpath(dest / "meta.xml"),
            "client": paths.jpath(dest / "client.lua"), "files": files}
