"""``kit.export``: a kit model from Blender to a game-ready package (MIT side of K6).

Steps (each degrades gracefully when an optional op is missing):

1. Blender (``kit.export`` method through K4, session or one-shot): DFF with frame-local bounding spheres,
   vehicle COL embedded as ``<stem>_col``, own-texture PNGs;
2. collision: ``col.gen`` (w2) on the exported DFF - vehicles: spheres + shadow embedded into the DFF,
   world: ``<stem>.col`` with surfaces and face light (primitives for small props by the class rule,
   :func:`satk.kit.kinds.col_choice`); fallback: the kit/DragonFF collision. Map models are exported with their
   baked prelight (day and night colours), so the face light comes from it;
3. TXD of the model's own textures only (shared ``vehicle.txd`` atlases are referenced, never copied):
   ``texture.pack --asset-class`` when available, else explicit DXT1/DXT3 one-level formats;
4. packaging: a Mod Loader folder with a readme (``--replace``) or ``mod.add`` (``--add``); a LOD model goes
   along (``lod<name>.dff``, its IDE line and, with ``place``, the HD placement that points to it);
5. re-import diff against the template plan, ``asset.lint`` and an ``asset.check`` summary.

Example::

    from satk.kit.export import run
    run(session="default", replace="model:426")     # -> {"out": ..., "files": {...}, "lint": {...}}
"""

from __future__ import annotations

import json
import os
import shutil
import struct
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.errors import SatkError
from . import kinds as K

__all__ = ["run", "embed_col", "clump_extensions", "embedded_col", "merge_contact_faces", "rename_col", "diff_dff", "out_dir", "modadd_kind", "MODADD_KIND", "TXD_CLASS",
           "pack_own_textures", "COL_MODES"]

_COLPLG, _EXT, _CLUMP = 0x253F2FA, 0x03, 0x10
#: kit group/kind -> ``mod.add`` kind.
MODADD_KIND = {"vehicle": "vehicle", "ped": "ped", "weapon": "weapon", "world": "object"}


def modadd_kind(kind: str) -> str:
    """``mod.add`` kind of a kit kind: tuning parts and pickups are objects."""
    if kind in ("ped", "weapon", "weapon_melee"):
        return "weapon" if kind == "weapon_melee" else kind
    if kind in ("vehicle_upgrade", "pickup"):
        return "object"
    return MODADD_KIND.get(K.get(kind)["group"], "object")
#: kit kind -> ``texture.pack`` asset class.
TXD_CLASS = {"vehicle": "vehicle", "ped": "ped", "weapon": "weapon", "world": "map"}
#: ``asset.check`` verdicts copied into the export summary (blocking, advisory and out-of-band rows).
CHECK_SHOWN = frozenset({"error", "defect", "warn", "missing", "low", "high"})


def out_dir(name: str, out: str | None = None) -> Path:
    wk = Path(os.path.abspath(paths.cfg().paths.work))
    p = Path(out) if out else wk / "out" / "kit" / name
    p = paths.ensure_writable(p.absolute())
    try:
        p.relative_to(wk)
    except ValueError:
        raise SatkError("PROTECTED_PATH", f"kit exports are written only under {paths.jpath(wk)}: {paths.jpath(p)}",
                        hint="omit --out (default <work>/out/kit/<name>)") from None
    return p


def _op(name: str):
    from ..core.registry import get_op

    try:
        return get_op(name)
    except SatkError:
        return None


def _has_param(spec, name: str) -> bool:
    try:
        spec.param(name)
        return True
    except KeyError:
        return False


def _choices(spec, name: str) -> tuple:
    try:
        return tuple(getattr(spec.param(name), "choices", None) or ())
    except KeyError:
        return ()


# --------------------------------------------------------------------------- RW helpers (stdlib)


def _chunk(t: int, payload: bytes, lib: int) -> bytes:
    return struct.pack("<III", t, len(payload), lib) + payload


def embed_col(dff: bytes, col: bytes) -> bytes:
    """``dff`` with exactly ONE clump Extension chunk that carries the collision plug-in (0x253F2FA) = ``col``.

    DragonFF writes one Extension per collision plus a trailing empty one; the engine reads only the first clump
    Extension, so a second one (or a COL in the second) leaves the vehicle without collision and invisible in game.
    Every clump-level Extension is merged: their other plug-ins are kept in order, every old COL plug-in is dropped
    and ``col`` is appended once; the Extension is the last child of the clump."""
    if len(dff) < 12:
        raise SatkError("BAD_PARAMS", "not a DFF")
    t, size, lib = struct.unpack_from("<III", dff, 0)
    if t != _CLUMP:
        raise SatkError("BAD_PARAMS", "the DFF does not start with a clump")
    end = 12 + size
    if end > len(dff):
        raise SatkError("BAD_PARAMS", "the clump chunk is longer than the file")
    kids = []
    off = 12
    while off + 12 <= end:
        ct, cs, cl = struct.unpack_from("<III", dff, off)
        if off + 12 + cs > end:
            raise SatkError("BAD_PARAMS", f"a clump child at {off} runs past the clump")
        kids.append((ct, dff[off + 12:off + 12 + cs], cl))
        off += 12 + cs
    plugins = []
    ext_lib = None
    for ct, payload, cl in kids:
        if ct != _EXT:
            continue
        ext_lib = cl if ext_lib is None else ext_lib
        o = 0
        while o + 12 <= len(payload):
            pt, ps, pl = struct.unpack_from("<III", payload, o)
            plugins.append((pt, payload[o + 12:o + 12 + ps], pl))
            o += 12 + ps
    plugins = [p for p in plugins if p[0] != _COLPLG] + [(_COLPLG, bytes(col), lib)]
    ext = _chunk(_EXT, b"".join(_chunk(*p) for p in plugins), ext_lib if ext_lib is not None else lib)
    body = b"".join(_chunk(*k) for k in kids if k[0] != _EXT) + ext
    return _chunk(_CLUMP, body, lib) + bytes(dff[end:])


def clump_extensions(dff: bytes) -> list[list[int]]:
    """The clump-level Extension chunks of a DFF: one row ``[offset, size, plug-in count, COL plug-ins]`` each
    (a game-ready vehicle has exactly one, with one COL)."""
    t, size, _lib = struct.unpack_from("<III", dff, 0)
    if t != _CLUMP:
        raise SatkError("BAD_PARAMS", "the DFF does not start with a clump")
    rows = []
    off, end = 12, 12 + size
    while off + 12 <= end:
        ct, cs, _cl = struct.unpack_from("<III", dff, off)
        if ct == _EXT:
            n = cols = 0
            o = off + 12
            while o + 12 <= off + 12 + cs:
                pt, ps, _pl = struct.unpack_from("<III", dff, o)
                n += 1
                cols += pt == _COLPLG
                o += 12 + ps
            rows.append([off, cs, n, cols])
        off += 12 + cs
    return rows


def embedded_col(dff: bytes) -> bytes | None:
    """The bytes of the first embedded COL model of a DFF (``None`` without one)."""
    from ..formats.dff import find_embedded_col

    r = find_embedded_col(dff)
    return bytes(dff[r[0]:r[0] + r[1]]) if r else None


def merge_contact_faces(gen: bytes, kit: bytes) -> tuple[bytes, int]:
    """``gen`` (a generated vehicle COL: spheres + shadow) with the contact faces of ``kit`` (the Blender collision of
    ``kit.col``: roof, bonnet, boot and glass faces) when ``gen`` has none; returns ``(col, faces added)``. Vanilla
    vehicles carry 8-12 such faces next to their spheres (surfaces CAR 63 and GLASS 45)."""
    from ..rw import col as RC

    try:
        g = RC.decode_model(gen)
        k = RC.decode_model(kit)
    except RC.ColError:
        return gen, 0
    if g.faces or not k.faces or g.version < 2 or k.version < 2:
        return gen, 0
    g.vertices = list(k.vertices)
    g.faces = list(k.faces)
    RC.face_groups(g)
    RC.compute_bounds(g)
    g.flags = RC.canonical_flags(g)
    return RC.encode_model(g), len(g.faces)


def rename_col(col: bytes, name: str) -> bytes:
    """The first COL model of ``col`` with its 22-byte name field set to ``name``."""
    b = name.encode("latin-1")
    if len(b) > 21:
        raise SatkError("BAD_PARAMS", f"collision name {name!r} is longer than 21 characters")
    out = bytearray(col)
    out[8:30] = b.ljust(22, b"\0")
    return bytes(out)


def _first_col(buf: bytes) -> bytes:
    """The first COL model record of a ``.col`` file."""
    if len(buf) < 8:
        raise SatkError("BAD_PARAMS", "empty COL")
    size = struct.unpack_from("<I", buf, 4)[0]
    return bytes(buf[:8 + size])


def diff_dff(dff: bytes, plan: dict | None, exported: dict) -> list[list]:
    """Re-import check: rows ``[check, expected, found, verdict]`` of the exported DFF against the plan."""
    from ..formats.dff import find_embedded_col, scan_dff

    info = scan_dff(dff)
    names = [(f.name or "").strip().lower() for f in info.frames]
    rows: list[list] = []
    want = [f.strip().lower() for f in exported.get("frames") or []]
    if plan:
        plan_names = [(f.get("name") or "").strip().lower() for f in plan.get("frames") or []]
        want = plan_names
    rows.append(["frames", len(want), len(names), "ok" if len(want) == len(names) else "diff"])
    order_ok = names == want
    missing = [n for n in want if n not in names]
    if not order_ok:
        rows.append(["frame order", "vanilla order", f"missing {missing[:4]}" if missing else "reordered", "diff"])
    else:
        rows.append(["frame order", "vanilla order", "same", "ok"])
    if plan:
        par_bad = 0
        for i, f in enumerate(plan.get("frames") or []):
            if i < len(info.frames) and f.get("parent", -1) != info.frames[i].parent:
                par_bad += 1
        rows.append(["parents", 0, par_bad, "ok" if par_bad == 0 else "diff"])
    rows.append(["atomics", exported.get("atomics"), info.atomics, "ok" if exported.get("atomics") == info.atomics else "diff"])
    exts = clump_extensions(dff)
    if exts and (len(exts) != 1 or any(x[3] > 1 for x in exts)):
        rows.append(["clump extensions", "1 (COL inside)", f"{len(exts)}, COL {[x[3] for x in exts]}", "diff"])
    r = find_embedded_col(dff)
    if exported.get("col", {}).get("embedded") or r:
        from ..formats.col import iter_col

        cn = None
        if r:
            for m in iter_col(bytes(dff[r[0]:r[0] + r[1]])):
                cn = m.name
                break
        want_col = f"{exported.get('stem')}_col"
        rows.append(["embedded COL", want_col, cn or "-", "ok" if cn == want_col else "diff"])
    return rows


# --------------------------------------------------------------------------- steps


def _blender(model: str | None, stage: Path, stem: str, col: str, session: str | None, blend: str | None,
             timeout: float, prelight: str = "auto") -> dict:
    from ..studio import api

    p: dict[str, Any] = {"out": str(stage), "stem": stem, "col": col, "prelight": prelight}
    if model:
        p["model"] = model
    res = api.call("kit.export", p, session=session, blend=blend, timeout=timeout, stats="none")
    man = (res.get("result") or {}).get("manifest")
    if not man or not Path(man).is_file():
        raise SatkError("EXTERNAL_TOOL", "kit.export: Blender wrote no manifest", data={"reply": res})
    return json.loads(Path(man).read_text(encoding="utf-8"))


#: ``col`` values that name a ``col.gen`` mode (the others: auto, kit, none).
COL_MODES = ("box", "boxes", "spheres", "hull", "mesh")


def _colgen(dff_path: Path, kind: str, group: str, stem: str, stage: Path, warn: list[str], *, shape: dict | None = None,
            col: str = "auto") -> tuple[bytes | None, dict]:
    spec = _op("col.gen")
    if spec is None:
        warn.append("DEPENDENCY: col.gen is not installed; the kit collision is used")
        return None, {"by": "kit"}
    vehicle = group == "vehicle" and kind != "vehicle_upgrade"
    extra: dict[str, Any] = {}
    why = ""
    if vehicle:
        mode = "spheres"
    elif col in COL_MODES:
        mode, why = col, "--col"
    else:
        mode, extra, why = K.col_choice(kind, shape)
    args: dict[str, Any] = {"target": str(dff_path), "mode": mode, "out": str(stage / "colgen")}
    if "max_prims" in extra and _has_param(spec, "max_prims"):
        args["max_prims"] = extra["max_prims"]
    if vehicle:
        args["shadow"] = True
    try:
        r = spec.call(args)
    except SatkError as e:
        warn.append(f"{e.code}: col.gen failed ({e.msg}); the kit collision is used")
        return None, {"by": "kit"}
    cols = sorted((stage / "colgen").glob("*.col"))
    if not cols:
        warn.append("NOT_FOUND: col.gen wrote no .col; the kit collision is used")
        return None, {"by": "kit"}
    data = cols[0].read_bytes()
    info: dict[str, Any] = {"by": "col.gen", "mode": mode}
    row = next((x for x in (r.get("rows") or []) if isinstance(x, list) and len(x) > 5), None)
    if row:                                  # col.gen row: name, mode, spheres, boxes, faces, shadow faces, ...
        info["counts"] = {k: v for k, v in zip(("spheres", "boxes", "faces", "shadow"), row[2:6]) if v}
    if why:
        info["why"] = why
    if "max_prims" in args:
        info["max_prims"] = args["max_prims"]
    return data, info


def _txd(textures: list[dict], stage: Path, txd_name: str, group: str, pkg: Path, warn: list[str]) -> dict:
    """Pack the own-texture PNGs into ``pkg/<txd_name>.txd``."""
    target = pkg / f"{txd_name}.txd"
    if not textures:
        from ..texmod.txdwrite import txd_chunk

        paths.atomic_write(target, txd_chunk([]))
        return {"file": paths.jpath(target), "textures": 0, "by": "empty"}
    tex_dir = stage / "tex"
    cls = TXD_CLASS.get(group, "map")
    spec = _op("texture.pack")
    if spec is None:
        raise SatkError("DEPENDENCY", "texture.pack is not available", hint="the texmod package is needed for the TXD")
    args: dict[str, Any] = {"folder": str(tex_dir), "name": f"kit_{txd_name}", "file": f"{txd_name}.txd"}
    if _has_param(spec, "asset_class") and cls in _choices(spec, "asset_class"):
        args["asset_class"] = cls
        how = f"texture.pack --asset-class {cls}"
    else:
        man = {"textures": [{"file": Path(t["png"]).name, "format": "DXT3", "levels": 1 if cls != "map" else 9}
                            for t in textures]}
        paths.atomic_write(tex_dir / "texmod.json", json.dumps(man, indent=1))
        if cls != "map":
            args["mips"] = 0
        how = "texture.pack (explicit DXT1/DXT3, 1 level)" if cls != "map" else "texture.pack (DXT1/DXT3, full chain)"
    if _has_param(spec, "out"):
        args["out"] = str(pkg)
    r = spec.call(args)
    built = Path(str(r.get("file") or ""))
    if not built.is_file():
        raise SatkError("EXTERNAL_TOOL", "texture.pack wrote no TXD", data={"reply": {k: r.get(k) for k in ("file", "mod")}})
    if built.resolve() != target.resolve():
        shutil.copyfile(built, target)
    warn.extend(str(w) for w in (r.get("warn") or []) if not str(w).startswith("OTHER_FILES"))
    return {"file": paths.jpath(target), "textures": len(textures), "by": how}


def pack_own_textures(textures: list[dict], stage: Path, txd_name: str, group: str, pkg: Path, warn: list[str]) -> dict:
    """``<pkg>/<txd_name>.txd`` from the own-texture PNGs in ``<stage>/tex`` through ``texture.pack`` (also used by
    ``blender.export``): DXT1/DXT3 by the class of ``group`` (vehicle, ped, weapon, world); no textures = an empty TXD."""
    return _txd(textures, stage, txd_name, group, pkg, warn)


def _readme(stem: str, replace: dict | None, kind: str, lod: str | None = None) -> str:
    lines = [f"satk kit export: {stem} ({kind})", ""]
    if replace:
        lines += [f"Replaces model {replace['name']} ({replace['sid']}).",
                  f"Install: copy this folder to <game>/modloader/{stem}/ (Mod Loader).",
                  "Never copy files into the game's IMG archives.",
                  "Data bound to the model id (handling, carcols, carmods, audio) stays the original's."]
        if lod:
            lines.append(f"{lod}.dff is the LOD model; it replaces a LOD only when it carries the name of the "
                         "vanilla LOD: rename it, or use --add for a new object with its own LOD line.")
    else:
        lines += ["Files only: use mod.add to make an add-on folder with data lines."]
    return "\n".join(lines) + "\n"


def _lint(pkg: Path, tier: str, warn: list[str]) -> dict:
    spec = _op("asset.lint")
    if spec is None:
        return {}
    presets = _choices(spec, "preset")
    preset = tier if tier in presets else ("strict" if "strict" in presets else "game")
    try:
        r = spec.call({"target": str(pkg), "preset": preset, "index": True, "limit": 10, "sev": "warn"})
    except SatkError as e:
        warn.append(f"{e.code}: asset.lint: {e.msg}")
        return {}
    return {"preset": preset, "summary": r.get("summary"), "by_rule": r.get("by_rule"),
            "rows": (r.get("rows") or [])[:10]}


def _check(dff: Path, like: str | None, tier: str, warn: list[str]) -> dict:
    spec = _op("asset.check")
    if spec is None:
        return {"note": "asset.check is not installed (lane style)"}
    args: dict[str, Any] = {"target": str(dff)}
    if like and _has_param(spec, "like"):
        args["like"] = like
    if _has_param(spec, "tier"):
        args["tier"] = tier
    try:
        r = spec.call(args)
    except SatkError as e:
        warn.append(f"{e.code}: asset.check: {e.msg}")
        return {}
    keep = {k: r[k] for k in ("verdict", "counts", "sections", "defects", "out_of_band") if k in r}
    rows = r.get("rows") or []
    # asset.check rows: [check, part, value, p10, p50, p90, verdict, hint, ref]
    bad = [row for row in rows if isinstance(row, list) and len(row) > 6 and row[6] in CHECK_SHOWN]
    keep["rows"] = bad[:8]
    keep["total"] = len(rows)
    return keep


def _blend_project(blend: str | None) -> Path | None:
    """The asset project a ``.blend`` lives in (``<project>/checkpoints/x.blend``, ``<project>/x.blend``)."""
    if not blend:
        return None
    cur = Path(blend).absolute().parent
    for _ in range(3):
        if (cur / "asset.json").is_file():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def run(*, model: str | None = None, session: str | None = None, blend: str | None = None,
        replace: str | None = None, add: bool = False, name: str | None = None, out: str | None = None,
        col: str = "auto", lint: bool = True, check: bool = True, profile: str = "vanilla",
        timeout: float = 600.0, prelight: str = "auto", place: list[float] | None = None) -> dict:
    """The whole export (module docstring). Returns the envelope fields."""
    from ..studio import api

    if replace and add:
        raise SatkError("BAD_PARAMS", "give --replace SID or --add, not both")
    warn: list[str] = []
    if session is None and blend is None:
        from ..studio import launcher as L

        if not L.status(L.DEFAULT_NAME).get("up"):
            raise SatkError("BAD_PARAMS", "no Blender session runs and no --blend was given",
                            hint="satk kit export --blend <workspace>/work/out/kit/<name>/<name>.blend (kit template "
                                 "saved it) or --session <name>")
    info = (api.call("kit.info", {"model": model} if model else {}, session=session, blend=blend,
                     timeout=timeout, stats="none").get("result") or {})
    kname = str(info.get("model") or model or "")
    kind = str(info.get("kind") or "prop")
    if kind == "ped":
        raise SatkError("UNSUPPORTED", "kit export cannot write a skinned ped yet (no Skin/HAnim writer): the game "
                                       "crashes on an unskinned ped",
                        hint="a new ped is a re-skin of a vanilla ped: satk help creation, section Ped re-skin (texture work on the "
                             "like model's TXD, then satk mod check)")
    tier = str(info.get("tier") or "sa_plus")
    group = K.get(kind)["group"]
    rep: dict | None = None
    if replace:
        from ..model3d.resolve import open_db, resolve

        src = resolve(replace, profile, open_db(profile))
        rep = {"sid": src.sid, "name": src.name.lower()}
    if place is not None and (len(place) != 3 or not add or group != "world"):
        raise SatkError("BAD_PARAMS", "place takes X,Y,Z and works with --add on a map model",
                        hint="satk kit export --add --place 2495,-1687,13")
    stem = (rep["name"] if rep else (name or kname)).lower()
    d = out_dir(stem, out)
    stage = d / "build"
    stage.mkdir(parents=True, exist_ok=True)
    man = _blender(kname or None, stage, stem, "auto" if col in COL_MODES else col, session, blend, timeout, prelight)
    warn += [str(w) for w in man.get("warn") or []]
    dff_path = Path(man["files"]["dff"])
    dff = dff_path.read_bytes()
    col_info: dict[str, Any] = {"by": "kit" if man.get("col", {}).get("objects") else "none"}
    col_file: bytes | None = None
    vehicle = group == "vehicle" and kind != "vehicle_upgrade"
    kit_col = embedded_col(dff) if vehicle else None        # what DragonFF embedded from the Blender collision
    if col in ("auto", "gen") or col in COL_MODES:
        cdata, col_info = _colgen(dff_path, kind, group, stem, stage, warn, shape=man.get("shape"), col=col)
        if cdata is not None:
            if vehicle:
                gen = rename_col(_first_col(cdata), f"{stem}_col")
                if kit_col:
                    gen, n = merge_contact_faces(gen, kit_col)
                    if n:
                        col_info["contact_faces"] = n
                dff = embed_col(dff, gen)
            else:
                col_file = rename_col(cdata, stem) if len(cdata) else None
    if vehicle and kit_col and len(clump_extensions(dff)) != 1:
        dff = embed_col(dff, kit_col)                       # one clump Extension, the COL inside it
    if col_file is None and man["files"].get("col_kit") and group != "vehicle":
        col_file = Path(man["files"]["col_kit"]).read_bytes()
        col_info.setdefault("by", "kit")
    pkg = d / ("files" if add else "modloader") / stem
    pkg.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    paths.atomic_write(pkg / f"{stem}.dff", dff)
    files["dff"] = paths.jpath(pkg / f"{stem}.dff")
    if col_file:
        paths.atomic_write(pkg / f"{stem}.col", col_file)
        files["col"] = paths.jpath(pkg / f"{stem}.col")
    lod_name = None
    if man["files"].get("lod_dff"):
        lp = Path(man["files"]["lod_dff"])
        lod_name = lp.stem
        shutil.copyfile(lp, pkg / lp.name)
        files["lod_dff"] = paths.jpath(pkg / lp.name)
    # the TXD class follows the kind (peds and weapons are in the character group)
    txd_group = "weapon" if kind in ("weapon", "weapon_melee") else "ped" if kind == "ped" else group
    txd = _txd(man.get("textures") or [], stage, stem, txd_group, pkg, warn)
    files["txd"] = txd["file"]
    plan = None
    if man.get("plan") and Path(man["plan"]).is_file():
        plan = json.loads(Path(man["plan"]).read_text(encoding="utf-8"))
    diff = diff_dff(dff, plan, dict(man, stem=stem))
    package: dict[str, Any] = {"dir": paths.jpath(pkg)}
    lint_dir = pkg
    like = (plan or {}).get("like") or {}
    if add:
        spec = _op("mod.add")
        if spec is None:
            warn.append("DEPENDENCY: mod.add is not installed; files only")
        else:
            margs = {"kind": modadd_kind(kind), "dff": files["dff"], "txd": files["txd"], "name": stem,
                     "force": True}
            if like.get("sid"):
                margs["like"] = like["sid"]
            if files.get("col") and margs["kind"] in ("object", "weapon"):
                margs["col"] = files["col"]
            if files.get("lod_dff") and margs["kind"] == "object" and _has_param(spec, "lod_dff"):
                margs["lod_dff"] = files["lod_dff"]
                if place is not None:
                    margs["place"] = [float(v) for v in place]
            try:
                r = spec.call(margs)
                package.update(by="mod.add", out=r.get("out") or r.get("dir"), id=r.get("id"))
                if r.get("lod"):
                    package["lod"] = r["lod"]
                if r.get("out") and Path(str(r["out"])).is_dir():
                    lint_dir = Path(str(r["out"]))      # the add-on folder has the IDE lines: no orphan warnings
            except SatkError as e:
                warn.append(f"{e.code}: mod.add: {e.msg}")
    else:
        if rep is None and like.get("name") == stem:
            rep = {"sid": like.get("sid"), "name": stem}
        paths.atomic_write(pkg / "readme.txt", _readme(stem, rep, kind, lod_name))
        package["by"] = "modloader" if rep else "files"
        if rep:
            package["replaces"] = rep["sid"]
        else:
            warn.append(f"INFO: {stem} is a new name: --add makes an add-on (mod.add), --replace SID a replacement")
    from ..inventory.sidecar import export_sidecar  # contract K6: item tags travel beside the DFF

    if (side := export_sidecar([pkg, lint_dir], stem, session=session, blend=blend, model=kname or None,
                               project=_blend_project(blend) if session is None else None, warn=warn,
                               lod=lod_name, dff=pkg / f"{stem}.dff")):
        try:                                          # listed when it carries an inventory (else: the marker only)
            if (json.loads(Path(side).read_text(encoding="utf-8")) or {}).get("inventory"):
                files["inventory"] = side
        except (OSError, ValueError):
            files["inventory"] = side
    res: dict[str, Any] = {"model": kname, "kind": kind, "tier": tier, "stem": stem, "out": paths.jpath(d),
                           "files": files, "package": package, "txd": {k: v for k, v in txd.items() if k != "file"},
                           "col": col_info, "bsphere": man.get("bsphere"), "frames": len(man.get("frames") or []),
                           "atomics": man.get("atomics"), "dropped_slots": len(man.get("dropped_slots") or []),
                           "diff": {"cols": ["check", "expected", "found", "verdict"], "rows": diff}}
    if man.get("prelight"):
        res["prelight"] = man["prelight"]
    if man.get("lod"):
        res["lod"] = {k: man["lod"][k] for k in ("name", "tris") if k in man["lod"]}
    if man.get("shape"):
        res["shape"] = {k: man["shape"][k] for k in ("size", "volume", "closed") if k in man["shape"]}
    if lint:
        res["lint"] = _lint(lint_dir, tier, warn)
    if check:
        res["check"] = _check(lint_dir / f"{stem}.dff" if (lint_dir / f"{stem}.dff").is_file() else pkg / f"{stem}.dff",
                              (plan or {}).get("like", {}).get("sid"), tier, warn)
    paths.atomic_write(d / "export.json", json.dumps(res, ensure_ascii=False, indent=1, default=str))
    res["warn"] = warn
    return res

