"""Request/response contract between ``satk`` and headless Blender (SPEC §4.11). Owner: WP-10.

FROZEN in the first WP-10 commit (``satk-blender/1``): later changes only add optional fields.
Stdlib only: imported both by the runner (CPython 3.12) and by ``blender/satk_blender/agent_cli.py``
inside Blender (Python 3.13).

Call::

    blender.exe -b [scene.blend] --factory-startup --python-exit-code 1 \\
        --python tools/blender/satk_blender/agent_cli.py -- <out_dir>/request.json

Request (``request.json``, UTF-8)::

    {"cmd": "import_area",
     "args": {"center": [2495, -1687], "box": 100, "match": "center", "area": 0, "lod": "hd", "col": false},
     "profile": "vanilla",
     "out_dir": "<workspace>/work/blender/jobs/20261004-153012-ab12",
     "satk_src": "<checkout>/src"}

Additive request fields (all optional): ``contract`` (``"satk-blender/1"``), ``job`` (job id),
``dragonff`` (directory that contains the ``dragonff`` package, default ``work/blender``),
``plan`` (model/area plan resolved by the runner, see ``satk.blender.resolve``; when absent the
Blender side resolves it itself from ``satk_src``), ``response`` (response path, default
``<out_dir>/response.json``).

Response (``<out_dir>/response.json``; Blender's stdout is noise and never parsed)::

    {"ok": true, "cmd": "import_area",
     "files": {"blend": ".../scene.blend", "png": []},
     "stats": {"instances": 187, "models": 61, "meshes": 61, "images": 140, "missing_tex": 0, "seconds": 41.2},
     "warnings": [], "log": ".../blender.log"}

``stats`` of the imports describe the saved ``.blend``: ``images`` = textures in the file (all packed;
textures of a TXD that no material uses are dropped before saving, ``unused_images``), ``txd_images``
= everything loaded from the TXD chain(s) in the session, ``meshes`` = mesh datablocks used by the
placements (breakable meshes, ``breakables``, live hidden with their prototypes).

On failure: ``{"ok": false, "cmd": ..., "error": {"code": "EXTERNAL_TOOL", "msg": ..., "hint": ...},
"warnings": [...], "log": ...}`` with a code from ``satk.core.errors.ERROR_CODES``.

Commands and arguments (defaults in :data:`ARG_DEFAULTS`):

* ``doctor`` - Blender/Python versions, DragonFF path and commit, profile isolation;
* ``import_model {id, render, views, size, engine, col, time}`` - one model (vehicle/ped/object);
* ``import_area {center, r | box, match, area, lod, col, limit, time}`` - placements of an area;
* ``render {blend, pose | bm, time, size, engine, objindex}`` - PNG (+ visible SIDs);
* ``export {blend, objects, target, out, name}`` - DFF/TXD/COL (+ MTA resource or modloader files);
* ``game_ready {src, objects, name, budget, height, scale, origin, uv, bake, tex_size, prelight, col,
  surface, lod, draw, out, render, save}`` - any mesh (``.blend``/``.obj``/``.glb``/``.fbx``...) made
  game-ready: decimated to a triangle budget, UV, day/night prelight, COL, LOD; DFF + COL (+ LOD DFF)
  and PNG textures into ``out`` (added in M2-08; the TXD is packed on the satk side);
* ``preview {spec, save}`` - SA-look preview cells of model plans or a map context
  (``satk_blender.look.preview``; the spec is built by ``satk.look.ops``, added in wave A1).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

__all__ = [
    "CONTRACT",
    "CMDS",
    "ARG_DEFAULTS",
    "ENGINES",
    "TARGETS",
    "MATCH",
    "LOD",
    "ContractError",
    "normalize_args",
    "make_request",
    "ok_response",
    "error_response",
    "parse_size",
    "parse_time",
    "day_night_balance",
    "write_json",
]

CONTRACT = "satk-blender/1"
CMDS = ("doctor", "import_model", "import_area", "render", "export", "game_ready", "preview")
ENGINES = ("workbench", "eevee")
TARGETS = ("mta-resource", "modloader")
MATCH = ("aabb", "center")
LOD = ("hd", "lod", "all")
#: Hard cap of placements per area import (Blender is for districts, not the whole map: report 11 §3.3).
MAX_INSTANCES = 5000

# --------------------------------------------------------------------------- game_ready (M2-08)

#: Source files ``game_ready`` reads (``.blend`` is opened as the scene, the rest are imported).
GR_EXTS = (".blend", ".obj", ".glb", ".gltf", ".fbx", ".ply", ".stl")
GR_ORIGIN = ("base", "center", "keep")
GR_UV = ("auto", "keep", "smart", "box")
GR_BAKE = ("auto", "always", "never")
GR_PRELIGHT = ("bake", "simple", "none")
GR_COL = ("hull", "box", "mesh", "none")
GR_TEX_SIZES = (16, 32, 64, 128, 256, 512, 1024)
#: Model names: a COL model name holds 21 characters + NUL (``ColHelpers.h``, report 22 §3.4).
GR_NAME_RE = re.compile(r"[a-z0-9_]{1,21}")
#: Default HD triangle budget: p90 of the vanilla map models (report 22 §3.1: p50 216, p90 1 040).
GR_BUDGET = 1040
#: Highest surface type (``eSurfaceType``: SURFACE_RAILTRACK = 178).
GR_MAX_SURFACE = 178


def model_name(stem: str) -> str:
    """A model name from a file stem: lower case, ``[a-z0-9_]``, at most 21 characters."""
    s = re.sub(r"[^a-z0-9_]+", "_", str(stem).lower()).strip("_")[:21].strip("_")
    return s or "model"


def lod_name(name: str) -> str:
    """Name of the LOD model of ``name`` (vanilla style ``lod<name>``; DFF/IDE names hold 23 characters)."""
    return "lod" + name[:20]

#: Defaults per command. ``None`` = optional without default.
ARG_DEFAULTS: dict[str, dict[str, Any]] = {
    "doctor": {},
    "import_model": {"id": None, "render": False, "views": 1, "size": 768, "engine": "workbench",
                     "col": False, "time": "12:00", "save": True},
    "import_area": {"center": None, "r": None, "box": None, "match": "aabb", "area": 0, "lod": "hd",
                    "col": False, "limit": 2000, "time": "12:00", "save": True},
    "render": {"blend": None, "pose": None, "bm": None, "time": "12:00", "size": "960x540",
               "engine": "workbench", "objindex": False, "fov": 70.0},
    "export": {"blend": None, "objects": None, "target": "mta-resource", "out": None, "name": None},
    "game_ready": {"src": None, "objects": None, "name": None, "budget": GR_BUDGET, "height": None, "scale": 1.0,
                   "origin": "base", "uv": "auto", "bake": "auto", "tex_size": 256, "prelight": "bake",
                   "col": "hull", "surface": 0, "lod": 0.25, "draw": 150.0, "out": None, "render": False,
                   "save": True},
    "preview": {"spec": None, "save": False},
}


class ContractError(ValueError):
    """Invalid request/arguments; ``code`` is a satk error code (``BAD_PARAMS`` ...)."""

    def __init__(self, msg: str, code: str = "BAD_PARAMS", hint: str | None = None):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.hint = hint


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool):
        raise ContractError(f"{what}: expected a number, got a boolean")
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ContractError(f"{what}: expected a number, got {v!r}") from None


def _vec(v: Any, n: int, what: str) -> list[float]:
    if isinstance(v, str):
        v = [p for p in re.split(r"[,\s]+", v.strip()) if p]
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise ContractError(f"{what}: expected {n} numbers, got {v!r}")
    return [_num(x, what) for x in v]


def _bool(v: Any, what: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int) and v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on", "0", "false", "no", "off"):
        return v.strip().lower() in ("1", "true", "yes", "on")
    raise ContractError(f"{what}: expected true/false, got {v!r}")


def _choice(v: Any, choices: tuple[str, ...], what: str) -> str:
    if v not in choices:
        raise ContractError(f"{what} must be one of {'|'.join(choices)}, got {v!r}")
    return v


def parse_size(v: Any, default: str = "960x540") -> tuple[int, int]:
    """``"960x540"`` / ``[960, 540]`` / ``768`` (square) -> ``(w, h)``, each 16..4096."""
    if v is None:
        v = default
    if isinstance(v, bool):
        raise ContractError(f"size: bad value {v!r}")
    if isinstance(v, (int, float)):
        w = h = int(v)
    elif isinstance(v, (list, tuple)) and len(v) == 2:
        w, h = int(_num(v[0], "size")), int(_num(v[1], "size"))
    elif isinstance(v, str) and re.fullmatch(r"\s*\d+\s*[xX*]\s*\d+\s*", v):
        a, b = re.split(r"[xX*]", v)
        w, h = int(a), int(b)
    elif isinstance(v, str) and v.strip().isdigit():
        w = h = int(v.strip())
    else:
        raise ContractError(f"size: expected WxH or a number, got {v!r}")
    if not (16 <= w <= 4096 and 16 <= h <= 4096):
        raise ContractError(f"size: {w}x{h} outside 16..4096")
    return w, h


def parse_time(v: Any) -> tuple[int, int]:
    """``"21:30"`` -> ``(21, 30)``; also accepts a number of hours (``21.5``)."""
    if v is None:
        return 12, 0
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        m = int(round(float(v) * 60)) % (24 * 60)
        return m // 60, m % 60
    s = str(v).strip()
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", s)
    if not m or int(m.group(1)) > 24 or int(m.group(2) or 0) > 59:
        raise ContractError(f"time: expected HH:MM, got {v!r}")
    h, mi = int(m.group(1)) % 24, int(m.group(2) or 0)
    return h, mi


def day_night_balance(hours: int, minutes: int = 0) -> float:
    """Day/night prelight balance for a game time (1.0 = night colours, 0.0 = day colours).

    Same curve as the engine's custom building pipeline (report 11 §8.3, gta-reversed
    ``CustomBuildingRenderer.cpp:51-77``): 1.0 before 06:00, linear to 0.0 at 07:00,
    0.0 until 20:00, linear to 1.0 at 21:00.
    """
    t = (hours % 24) + minutes / 60.0
    if t < 6.0:
        return 1.0
    if t < 7.0:
        return 1.0 - (t - 6.0)
    if t < 20.0:
        return 0.0
    if t < 21.0:
        return t - 20.0
    return 1.0


def _area(v: Any) -> int | None:
    if v is None or (isinstance(v, str) and v.strip().lower() in ("any", "all", "*", "")):
        return None
    if isinstance(v, bool):
        raise ContractError("area: expected an integer or 'any'")
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ContractError(f"area: expected an integer or 'any', got {v!r}") from None
    if not 0 <= n <= 255:
        raise ContractError(f"area: {n} outside 0..255")
    return n


def _pose(v: Any) -> dict:
    if not isinstance(v, dict) or "pos" not in v:
        raise ContractError("pose: expected {pos, look|ypr[, fov_h_deg]}")
    out: dict[str, Any] = {"pos": _vec(v["pos"], 3, "pose.pos")}
    has_look, has_ypr = "look" in v, "ypr" in v
    if has_look == has_ypr:
        raise ContractError("pose needs exactly one of 'look' and 'ypr'")
    if has_look:
        out["look"] = _vec(v["look"], 3, "pose.look")
        if sum((a - b) ** 2 for a, b in zip(out["look"], out["pos"])) < 1e-6:
            raise ContractError("pose.look must differ from pose.pos")
    else:
        out["ypr"] = _vec(v["ypr"], 3, "pose.ypr")
    if v.get("fov_h_deg") is not None:
        f = _num(v["fov_h_deg"], "pose.fov_h_deg")
        if not 0 < f < 180:
            raise ContractError("pose.fov_h_deg must be in (0, 180)")
        out["fov_h_deg"] = f
    return out


def normalize_args(cmd: str, args: dict | None) -> dict:
    """Validate ``args`` of ``cmd`` and fill defaults. Raises :class:`ContractError`.

    Unknown keys are rejected (typos must not be ignored silently).
    """
    if cmd not in CMDS:
        raise ContractError(f"unknown cmd {cmd!r}; expected one of {'|'.join(CMDS)}")
    args = dict(args or {})
    defaults = ARG_DEFAULTS[cmd]
    unknown = sorted(set(args) - set(defaults))
    if unknown:
        raise ContractError(f"{cmd}: unknown argument(s) {', '.join(unknown)}; allowed: {', '.join(defaults) or '-'}")
    a = {k: args.get(k, d) for k, d in defaults.items()}
    if cmd == "import_model":
        if a["id"] is None or str(a["id"]).strip() == "":
            raise ContractError("import_model needs 'id' (model:411, model:infernus, 411 or a name)")
        a["id"] = str(a["id"]).strip()
        a["render"] = _bool(a["render"], "render")
        a["col"] = _bool(a["col"], "col")
        a["save"] = _bool(a["save"], "save")
        a["views"] = int(_num(a["views"], "views"))
        if a["views"] not in (1, 4):
            raise ContractError("views must be 1 or 4")
        a["size"] = parse_size(a["size"])[0]
        a["engine"] = _choice(a["engine"], ENGINES, "engine")
        parse_time(a["time"])
    elif cmd == "import_area":
        if a["center"] is None:
            raise ContractError("import_area needs 'center' [x, y]")
        c = a["center"]
        if isinstance(c, str):
            c = [p for p in re.split(r"[,\s]+", c.strip()) if p]
        if isinstance(c, (list, tuple)) and len(c) == 3:
            c = list(c)[:2]
        a["center"] = _vec(c, 2, "center")
        r, box = a["r"], a["box"]
        if (r is None) == (box is None):
            raise ContractError("import_area needs exactly one of 'r' (radius) and 'box' (side or [x0,y0,x1,y1])")
        if r is not None:
            a["r"] = _num(r, "r")
            if not 0 < a["r"] <= 2000:
                raise ContractError("r must be in (0, 2000]")
        else:
            if isinstance(box, str) and ("," in box or " " in box.strip()):
                box = [p for p in re.split(r"[,\s]+", box.strip()) if p]
            if isinstance(box, (list, tuple)):
                b = _vec(box, 4, "box")
                x0, y0, x1, y1 = min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])
            else:
                s = _num(box, "box")
                if not 0 < s <= 4000:
                    raise ContractError("box side must be in (0, 4000]")
                cx, cy = a["center"]
                x0, y0, x1, y1 = cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2
            if x1 - x0 <= 0 or y1 - y0 <= 0 or x1 - x0 > 4000 or y1 - y0 > 4000:
                raise ContractError("box must have a positive size up to 4000 m")
            a["box"] = [x0, y0, x1, y1]
        a["match"] = _choice(a["match"], MATCH, "match")
        a["lod"] = _choice(a["lod"], LOD, "lod")
        a["area"] = _area(a["area"])
        a["col"] = _bool(a["col"], "col")
        a["save"] = _bool(a["save"], "save")
        a["limit"] = int(_num(a["limit"], "limit"))
        if not 1 <= a["limit"] <= MAX_INSTANCES:
            raise ContractError(f"limit must be in 1..{MAX_INSTANCES}")
        parse_time(a["time"])
    elif cmd == "render":
        if not a["blend"]:
            raise ContractError("render needs 'blend' (a .blend from import_model/import_area)")
        a["blend"] = str(a["blend"])
        if a["pose"] is not None and a["bm"] is not None:
            raise ContractError("give either 'pose' or 'bm', not both")
        if a["pose"] is not None:
            a["pose"] = _pose(a["pose"])
        a["size"] = list(parse_size(a["size"]))
        a["engine"] = _choice(a["engine"], ENGINES, "engine")
        a["objindex"] = _bool(a["objindex"], "objindex")
        a["fov"] = _num(a["fov"], "fov")
        if not 1 <= a["fov"] <= 179:
            raise ContractError("fov must be in 1..179 (horizontal degrees)")
        parse_time(a["time"])
    elif cmd == "export":
        if not a["blend"]:
            raise ContractError("export needs 'blend'")
        a["blend"] = str(a["blend"])
        objs = a["objects"]
        if isinstance(objs, str):
            objs = [p for p in re.split(r"[,;]", objs) if p.strip()]
        if not objs or not isinstance(objs, (list, tuple)):
            raise ContractError("export needs 'objects' (object or model names, or SIDs)")
        a["objects"] = [str(o).strip() for o in objs]
        a["target"] = _choice(a["target"], TARGETS, "target")
        if a["name"] is not None:
            if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", str(a["name"])):
                raise ContractError("name: 1-40 characters A-Z a-z 0-9 _ -")
            a["name"] = str(a["name"])
    elif cmd == "game_ready":
        _game_ready_args(a)
    elif cmd == "preview":
        sp = a["spec"]
        if not isinstance(sp, dict) or not (sp.get("entries") or sp.get("area")):
            raise ContractError("preview needs 'spec' with 'entries' (model plans) or 'area'")
        a["save"] = _bool(a["save"], "save")
    return a


def _game_ready_args(a: dict) -> None:
    """Validate the ``game_ready`` arguments in place (see :data:`ARG_DEFAULTS`)."""
    objs = a["objects"]
    if isinstance(objs, str):
        objs = [objs]
    if objs is not None:
        if not isinstance(objs, (list, tuple)):
            raise ContractError("objects: a list of object names (default: every visible mesh)")
        # "A,B" and ["A", "B"] alike (the CLI passes --objects A,B as one item)
        objs = [p.strip() for o in objs for p in re.split(r"[,;]", str(o)) if p.strip()]
        if not objs:
            raise ContractError("objects: a list of object names (default: every visible mesh)")
    a["objects"] = objs
    src = a["src"]
    if src is None or not str(src).strip():
        # no file: the objects of the scene that is open (the add-on's operator)
        if not objs:
            raise ContractError("game_ready needs 'src' (a .blend, .obj, .glb, .gltf, .fbx, .ply or .stl file)")
        a["src"] = None
    else:
        a["src"] = str(src).strip()
        ext = os.path.splitext(a["src"])[1].lower()
        if ext not in GR_EXTS:
            raise ContractError(f"src: unsupported file type {ext or '(none)'!r}; expected one of {' '.join(GR_EXTS)}")
    if a["name"] is None:
        stem = os.path.splitext(os.path.basename(a["src"]))[0] if a["src"] else objs[0]
        a["name"] = model_name(stem)
    else:
        a["name"] = str(a["name"]).strip().lower()
        if not GR_NAME_RE.fullmatch(a["name"]):
            raise ContractError("name: 1-21 characters a-z 0-9 _ (the COL model name holds 21 characters)")
    a["budget"] = int(_num(a["budget"], "budget"))
    if not 12 <= a["budget"] <= 60000:
        raise ContractError("budget must be in 12..60000 triangles")
    if a["height"] is not None:
        a["height"] = _num(a["height"], "height")
        if not 0 < a["height"] <= 500:
            raise ContractError("height must be in (0, 500] metres")
    a["scale"] = _num(a["scale"], "scale")
    if not 0 < a["scale"] <= 1000:
        raise ContractError("scale must be in (0, 1000]")
    a["origin"] = _choice(a["origin"], GR_ORIGIN, "origin")
    a["uv"] = _choice(a["uv"], GR_UV, "uv")
    a["bake"] = _choice(a["bake"], GR_BAKE, "bake")
    a["tex_size"] = int(_num(a["tex_size"], "tex_size"))
    if a["tex_size"] not in GR_TEX_SIZES:
        raise ContractError(f"tex_size must be one of {', '.join(map(str, GR_TEX_SIZES))}")
    a["prelight"] = _choice(a["prelight"], GR_PRELIGHT, "prelight")
    a["col"] = _choice(a["col"], GR_COL, "col")
    a["surface"] = int(_num(a["surface"], "surface"))
    if not 0 <= a["surface"] <= GR_MAX_SURFACE:
        raise ContractError(f"surface must be in 0..{GR_MAX_SURFACE} (eSurfaceType)")
    a["lod"] = _num(a["lod"], "lod")
    if not 0 <= a["lod"] < 1:
        raise ContractError("lod: share of the triangles kept in the LOD model, 0 (no LOD) .. 0.99")
    a["draw"] = _num(a["draw"], "draw")
    if not 1 <= a["draw"] <= 3000:
        raise ContractError("draw (IDE draw distance) must be in 1..3000")
    if a["out"] is not None:
        a["out"] = str(a["out"])
    a["render"] = _bool(a["render"], "render")
    a["save"] = _bool(a["save"], "save")


def make_request(cmd: str, args: dict | None, *, profile: str, out_dir: str, satk_src: str,
                 job: str | None = None, dragonff: str | None = None, plan: dict | None = None) -> dict:
    """A complete, validated request (paths with forward slashes)."""
    req: dict[str, Any] = {
        "contract": CONTRACT,
        "cmd": cmd,
        "args": normalize_args(cmd, args),
        "profile": profile,
        "out_dir": _fwd(out_dir),
        "satk_src": _fwd(satk_src),
    }
    if job:
        req["job"] = job
    if dragonff:
        req["dragonff"] = _fwd(dragonff)
    if plan is not None:
        req["plan"] = plan
    return req


def _fwd(p: str | os.PathLike) -> str:
    s = os.path.abspath(os.fspath(p)).replace("\\", "/")
    return s[0].upper() + s[1:] if len(s) >= 2 and s[1] == ":" else s


def ok_response(cmd: str, *, files: dict | None = None, stats: dict | None = None,
                warnings: list[str] | None = None, log: str | None = None, **extra: Any) -> dict:
    """Successful response object (field order fixed for readability)."""
    r: dict[str, Any] = {"ok": True, "cmd": cmd, "files": files or {}, "stats": stats or {},
                         "warnings": list(warnings or [])}
    if log:
        r["log"] = log
    r.update(extra)
    return r


def error_response(cmd: str | None, code: str, msg: str, *, hint: str | None = None,
                   warnings: list[str] | None = None, log: str | None = None, data: dict | None = None) -> dict:
    """Failure response; ``code`` is a satk error code."""
    err: dict[str, Any] = {"code": code, "msg": msg}
    if hint:
        err["hint"] = hint
    if data:
        err["data"] = data
    r: dict[str, Any] = {"ok": False, "cmd": cmd, "error": err, "warnings": list(warnings or [])}
    if log:
        r["log"] = log
    return r


def write_json(path: str | os.PathLike, obj: Any) -> None:
    """Atomic JSON write (temp file + ``os.replace``), UTF-8. No path guard here: callers pass
    paths under ``work/`` (the runner checks them with ``paths.ensure_writable``)."""
    p = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, p)
