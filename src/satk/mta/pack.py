"""``satk mta pack``: an MTA:SA resource that loads custom models (vehicles, skins, objects).

Input: a folder of ``.dff``/``.txd``/``.col`` files (grouped by file name), one ``.dff``, or a JSON list
(``[{"dff": ..., "txd": ..., "col": ..., "name": ..., "replace": 411, "parent": 400, "lod": 300}]`` or
``{"models": [...]}``). Two modes:

* **replace** (default): ``engineReplaceModel`` over an existing model id. The id comes from ``--replace``
  (``411``, ``model:411`` or ``model:infernus``, one per model in name order), from the JSON, or from the file
  name when it is a stock model name (``infernus.dff`` -> 411, looked up in the satk index);
* **new id** (``--new-id``, MTA 1.6): ``engineRequestModel(type, parent)`` on every client; the server creates
  elements with the parent model plus resource-specific element data, and the clients switch them to the
  allocated id (``setElementModel``) when they stream in.

Each client loads COL -> TXD -> DFF (``engineLoadCOL``/``engineReplaceCOL``, ``engineLoadTXD``/
``engineImportTXD``, ``engineLoadDFF``/``engineReplaceModel``) and sets ``engineSetModelLODDistance`` when a LOD
distance is given. The answer reports every downloaded file with its size against a budget, with compression
advice; files are copied, never encrypted or obfuscated.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from ..core.errors import SatkError

__all__ = ["KINDS", "Model", "collect_models", "resolve_ids", "render_pack", "rw_info", "NAME_RE"]

#: kind -> (engineRequestModel type, default parent id, IDE sections that fit)
KINDS = {"vehicle": ("vehicle", 400, ("cars",)), "skin": ("ped", 7, ("peds",)),
         "object": ("object", 1337, ("objs", "tobj", "anim"))}
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MODEL_NAME = re.compile(r"[^A-Za-z0-9_]+")
_SA_RW = 0x36003


@dataclass
class Model:
    name: str
    dff: Path
    txd: Path | None = None
    col: Path | None = None
    replace: int | None = None
    parent: int | None = None
    lod: float | None = None
    note: list[str] = field(default_factory=list)

    def files(self) -> list[tuple[str, Path]]:
        """``(kind, path)`` in load order: COL, TXD, DFF."""
        out = []
        if self.col is not None:
            out.append(("col", self.col))
        if self.txd is not None:
            out.append(("txd", self.txd))
        out.append(("dff", self.dff))
        return out


def rw_info(p: Path) -> tuple[int, int] | None:
    """``(chunk id, RW version)`` of a RenderWare file (``None`` if unreadable)."""
    try:
        with open(p, "rb") as f:
            h = f.read(12)
    except OSError:
        return None
    if len(h) < 12:
        return None
    cid = int.from_bytes(h[0:4], "little")
    stamp = int.from_bytes(h[8:12], "little")
    if stamp & 0xFFFF0000:
        ver = (((stamp >> 14) & 0x3FF00) + 0x30000) | ((stamp >> 16) & 0x3F)
    else:
        ver = stamp << 8
    return cid, ver


def _ver_text(v: int) -> str:
    return f"{(v >> 16) & 0xF}.{(v >> 12) & 0xF}.{(v >> 8) & 0xF}.{v & 0xFF}"


def _read_list(p: Path) -> list[dict]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"{p.name}: not a JSON model list: {e}",
                        hint='[{"dff": "car.dff", "txd": "car.txd", "replace": 411}]') from None
    if isinstance(d, dict):
        d = d.get("models")
    if not isinstance(d, list) or not all(isinstance(x, dict) for x in d):
        raise SatkError("BAD_PARAMS", f"{p.name}: expected a list of objects (or {{\"models\": [...]}})")
    return d


def _clean_name(s: str) -> str:
    n = _MODEL_NAME.sub("_", s).strip("_")
    return n or "model"


def validate_name(name: str) -> None:
    if not NAME_RE.fullmatch(name) or PureWindowsPath(name).is_reserved():
        raise SatkError("BAD_PARAMS", f"bad resource name {name!r}",
                        hint="use 1..64 letters, digits, _ or -; Windows device names such as CON are reserved")


def positive_number(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = float("nan")
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise SatkError("BAD_PARAMS", f"{label} must be a finite positive number")
    return number


def _validate_models(models: list[Model]) -> list[Model]:
    if not models:
        raise SatkError("NOT_FOUND", "the model list is empty", hint="add at least one model with a .dff file")
    names: set[str] = set()
    for m in models:
        if m.name.lower() in names:
            raise SatkError("AMBIGUOUS", f"two models named {m.name!r}",
                            hint="give each model a distinct name (including after replacing punctuation with _)")
        names.add(m.name.lower())
        for kind, p in m.files():
            if p.suffix.lower() != f".{kind}":
                raise SatkError("BAD_PARAMS", f"{m.name}: {kind} must name a .{kind} file, got {p.name!r}")
            if not p.is_file():
                raise SatkError("NOT_FOUND", f"{m.name}: {kind} file not found: {p}",
                                hint="paths in a JSON model list are relative to that JSON file")
        if m.lod is not None:
            m.lod = positive_number(m.lod, f"{m.name}: lod")
    return sorted(models, key=lambda m: (m.name.lower(), m.name))


def collect_models(src: Path, warn: list[str]) -> list[Model]:
    """Models of a folder, a single DFF or a JSON list (paths in the JSON are relative to the JSON)."""
    if src.is_file() and src.suffix.lower() == ".json":
        out = []
        for i, d in enumerate(_read_list(src)):
            if not d.get("dff"):
                raise SatkError("BAD_PARAMS", f"{src.name}: entry {i + 1} has no 'dff'")
            base = src.parent

            def p(k: str) -> Path | None:
                v = d.get(k)
                if v is None and k != "dff":
                    return None
                if not isinstance(v, str) or not v.strip() or "\x00" in v:
                    raise SatkError("BAD_PARAMS", f"{src.name}: entry {i + 1} '{k}' must be a file path string")
                return base / v

            dff = p("dff")
            name = d.get("name", dff.stem)
            if not isinstance(name, str) or not name.strip():
                raise SatkError("BAD_PARAMS", f"{src.name}: entry {i + 1} 'name' must be a nonempty string")
            m = Model(name=_clean_name(name), dff=dff, txd=p("txd"), col=p("col"))
            for k in ("replace", "parent"):
                if d.get(k) is not None:
                    setattr(m, k, d[k])
            if d.get("lod") is not None:
                m.lod = positive_number(d["lod"], f"{m.name}: lod")
            out.append(m)
        return _validate_models(out)
    if src.is_file() and src.suffix.lower() == ".dff":
        files = [src] + [x for x in src.parent.iterdir() if x.is_file() and x.stem.lower() == src.stem.lower()
                         and x.suffix.lower() in (".txd", ".col")]
        folder = src.parent
        dffs = [src]
    elif src.is_dir():
        files = sorted(x for x in src.rglob("*") if x.is_file() and x.suffix.lower() in (".dff", ".txd", ".col"))
        folder = src
        dffs = [x for x in files if x.suffix.lower() == ".dff"]
    else:
        raise SatkError("BAD_PARAMS", f"{src.name}: give a folder with .dff/.txd/.col files, a .dff or a JSON list")
    if not dffs:
        raise SatkError("NOT_FOUND", f"no .dff files in {src}", hint="a model needs a .dff (and usually a .txd)")
    by_stem: dict[tuple[str, str], list[Path]] = {}
    for x in files:
        by_stem.setdefault((x.stem.lower(), x.suffix.lower()), []).append(x)
    txds = [x for x in files if x.suffix.lower() == ".txd"]
    out = []
    for d in sorted(dffs, key=lambda x: x.relative_to(folder).as_posix().lower()):
        stem = d.stem.lower()

        def companion(ext: str) -> Path | None:
            candidates = by_stem.get((stem, ext), [])
            nearby = [p for p in candidates if p.parent == d.parent]
            candidates = nearby or candidates
            if len(candidates) > 1:
                raise SatkError("AMBIGUOUS", f"{d.name}: more than one matching {ext} file",
                                hint="put the companion beside its DFF, or select it explicitly in a JSON list")
            return candidates[0] if candidates else None

        m = Model(name=_clean_name(d.stem), dff=d, txd=companion(".txd"), col=companion(".col"))
        if m.txd is None:
            nearby = [p for p in txds if p.parent == d.parent]
            shared = nearby or txds
            if len(shared) == 1:
                m.txd = shared[0]
                m.note.append(f"shares {m.txd.name}")
        if m.txd is None:
            warn.append(f"NOT_FOUND: no {d.stem}.txd for {d.name}: the model loads with the textures of the "
                        "replaced/parent model (or white)")
        out.append(m)
    return _validate_models(out)


def _model_id(v, profile: str) -> tuple[int, str | None]:
    """``411`` / ``"model:411"`` / ``"model:infernus"`` / ``"infernus"`` -> (id, IDE section or None)."""
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise SatkError("BAD_PARAMS", f"bad model id {v!r}")
    if isinstance(v, int):
        return v, None
    s = str(v).strip()
    if re.fullmatch(r"-?[0-9]+", s):
        return int(s), None
    from ..core.registry import invoke

    sid = s if s.startswith("model:") else f"model:{s}"
    if re.fullmatch(r"-?[0-9]+", sid[6:]):
        return int(sid[6:]), None
    env = invoke("asset.get", {"id": sid, "fields": ["name", "sec"], "profile": profile})
    if not env.get("ok"):
        err = env.get("error", {})
        raise SatkError("NOT_FOUND", f"no model {s!r} in the {profile} index ({err.get('code')})",
                        hint="give the numeric id (411) or build the index: satk index build",
                        did_you_mean=err.get("did_you_mean") or [])
    return int(str(env["id"]).split(":", 1)[1]), env.get("sec")


def _check_id(mid: int, kind: str, label: str) -> None:
    valid = 0 <= mid < 20000
    if kind == "vehicle":
        valid = 400 <= mid <= 611
    elif kind == "skin":
        valid = 0 <= mid <= 312 and mid != 74
    elif mid < 321 or 384 <= mid <= 397 or 400 <= mid <= 611:
        valid = False
    if not valid:
        raise SatkError("BAD_PARAMS", f"{label}: {mid} is not a stock {kind} model id",
                        hint="choose an original model of this kind; --new-id allocates the custom id at runtime")


def resolve_ids(models: list[Model], kind: str, new_id: bool, replace: list[str] | None, parent: str | None,
                profile: str, warn: list[str]) -> None:
    """Fill ``replace`` (replace mode) or ``parent`` (new-id mode) of every model."""
    _etype, default_parent, secs = KINDS[kind]
    if replace and new_id:
        raise SatkError("BAD_PARAMS", "--replace and --new-id exclude each other",
                        hint="replace stock models, or add new ids (MTA 1.6 engineRequestModel)")
    if parent is not None and not new_id:
        raise SatkError("BAD_PARAMS", "--parent requires --new-id")
    if replace and len(replace) != len(models):
        raise SatkError("BAD_PARAMS", f"--replace has {len(replace)} id(s) for {len(models)} model(s)",
                        hint="one id per model, in name order: " + ", ".join(m.name for m in models),
                        data={"models": [m.name for m in models]})
    used: dict[int, str] = {}
    for i, m in enumerate(models):
        if new_id:
            src = m.parent if m.parent is not None else (parent if parent is not None else None)
            if src is None:
                try:
                    pid, sec = _model_id(m.name, profile)
                    _check_id(pid, kind, "parent")
                    if sec is not None and sec not in secs:
                        pid = default_parent
                    else:
                        m.note.append(f"parent {pid} (same name)")
                except SatkError:
                    pid = default_parent
            else:
                pid, sec = _model_id(src, profile)
                if sec is not None and sec not in secs:
                    warn.append(f"BAD_PARAMS: parent {src} is a '{sec}' model, not a {kind}")
            m.parent = pid
            m.replace = None
            _check_id(pid, kind, f"{m.name}: parent")
            continue
        src = replace[i] if replace else m.replace
        how = "--replace"
        if src is None:
            src = m.name
            how = "file name"
        try:
            mid, sec = _model_id(src, profile)
        except SatkError as e:
            if how == "file name":
                raise SatkError("NOT_FOUND", f"{m.dff.name}: which model does it replace? '{m.name}' is not a stock "
                                             "model name", hint="pass --replace <id> (one per model) or --new-id",
                                did_you_mean=e.did_you_mean) from None
            raise
        if sec is not None and sec not in secs:
            warn.append(f"BAD_PARAMS: model {mid} ({src}) is a '{sec}' model, not a {kind}: check --kind")
        _check_id(mid, kind, f"{m.name}: replacement")
        if mid in used:
            raise SatkError("BAD_PARAMS", f"{m.name} and {used[mid]} both replace model {mid}",
                            hint="choose a distinct replacement id for every model, or use --new-id")
        used[mid] = m.name
        m.replace = mid


# --------------------------------------------------------------------------- rendering


def _lua_str(s: str) -> str:
    return '"' + "".join(f"\\{ord(c):03d}" if ord(c) < 32 or ord(c) == 127 else
                         "\\" + c if c in '\\"' else c for c in s) + '"'


def _lua_num(v: float) -> str:
    return format(positive_number(v, "lod"), ".15g")


def plan_files(models: list[Model]) -> tuple[list[dict], dict[str, Path]]:
    """Assign portable, collision-free resource paths; shared source files are copied once."""
    rows: list[dict] = []
    copies: dict[str, Path] = {}
    by_source: dict[Path, str] = {}
    used: set[str] = set()
    for m in models:
        row = {"name": m.name, "replace": m.replace, "parent": m.parent, "lod": m.lod}
        for kind, path in m.files():
            source = path.resolve()
            rel = by_source.get(source)
            if rel is None:
                stem = _clean_name(path.stem)[:80]
                rel = f"models/{stem}.{kind}"
                if rel.lower() in used:
                    stem = f"{m.name[:64]}_{stem}"
                    rel = f"models/{stem}.{kind}"
                n = 2
                while rel.lower() in used:
                    rel = f"models/{stem}_{n}.{kind}"
                    n += 1
                copies[rel] = source
                by_source[source] = rel
                used.add(rel.lower())
            row[kind] = rel
        rows.append(row)
    return rows, copies


def models_lua(name: str, kind: str, rows: list[dict]) -> str:
    etype = KINDS[kind][0]
    lines = [f"-- {name}: the models this resource loads (generated by satk mta pack; edit and keep the order)",
             f"-- kind {kind}; fields: name, dff, txd, col (paths inside the resource), replace (stock id) or",
             "-- parent (base model for a new id), lod (draw distance in metres)",
             f"MODEL_TYPE = {_lua_str(etype)}",
             'MODEL_DATA_KEY = "satk:" .. resourceName .. ":model"',
             "MODELS = {"]
    for r in rows:
        parts = [f"name = {_lua_str(r['name'])}"]
        for k in ("dff", "txd", "col"):
            if r.get(k):
                parts.append(f"{k} = {_lua_str(r[k])}")
        for k in ("replace", "parent"):
            if r.get(k) is not None:
                parts.append(f"{k} = {int(r[k])}")
        if r.get("lod"):
            parts.append(f"lod = {_lua_num(r['lod'])}")
        lines.append("    {" + ", ".join(parts) + "},")
    lines.append("}")
    return "\n".join(lines) + "\n"


_CLIENT = '''-- {name}: loads the models of MODELS (models.lua) on every client, in the order COL -> TXD -> DFF
-- generated by satk mta pack ({kind}); MTA restores replaced models and frees new ids when the resource stops

local ids = {{}}          -- model name -> model id on this client

local function loadModel(m)
    local id = m.replace
    if not id then
        id = engineRequestModel(MODEL_TYPE, m.parent)
        if not id then
            outputDebugString(("[{name}] engineRequestModel failed for %s"):format(m.name), 1)
            return
        end
    end
    if m.col then
        local col = engineLoadCOL(m.col)
        if not col or not engineReplaceCOL(col, id) then
            outputDebugString(("[{name}] cannot load %s"):format(m.col), 2)
        end
    end
    if m.txd then
        local txd = engineLoadTXD(m.txd)
        if not txd or not engineImportTXD(txd, id) then
            outputDebugString(("[{name}] cannot load %s"):format(m.txd), 2)
        end
    end
    local dff = engineLoadDFF(m.dff)
    if not dff or not engineReplaceModel(dff, id) then
        outputDebugString(("[{name}] cannot load %s"):format(m.dff), 1)
        if not m.replace then
            engineFreeModel(id)
        end
        if dff then
            destroyElement(dff)
        end
        return
    end
    if m.lod then
        engineSetModelLODDistance(id, m.lod)
    end
    ids[m.name] = id
end
{apply}
addEventHandler("onClientResourceStart", resourceRoot, function()
    for _, m in ipairs(MODELS) do
        loadModel(m)
    end{apply_all}
end)
'''

_CLIENT_APPLY = '''
-- Only this pack's element data applies; model names in other packs may be identical.
local function applyModel(element)
    if not isElement(element) then
        return
    end
    local elementType = getElementType(element)
    if not ({type_test}) then
        return
    end
    local name = getElementData(element, MODEL_DATA_KEY, false)
    local id = type(name) == "string" and ids[name]
    if id and getElementModel(element) ~= id then
        setElementModel(element, id)
    end
end

addEventHandler("onClientElementStreamIn", root, function()
    applyModel(source)
end)

addEventHandler("onClientElementDataChange", root, function(key)
    if key == MODEL_DATA_KEY and (source == localPlayer or isElementStreamedIn(source)) then
        applyModel(source)
    end
end)
'''

_CLIENT_APPLY_ALL = '''
    for _, element in ipairs(getElementsByType("{etype}", root, true)) do
        applyModel(element)
    end'''

_SERVER = '''-- {name}: server helpers for the new model ids (generated by satk mta pack)
-- Other resources call them with exports["{name}"]:{create}(...).

local function modelEntry(name)
    for _, m in ipairs(MODELS) do
        if m.name == name then
            return m
        end
    end
    return nil
end

{body}

-- /{cmd} <name>: admins spawn a model of this pack next to them (a development helper)
addCommandHandler("{cmd}", function(player, _, name)
    if not isElement(player) or getElementType(player) ~= "player" then
        return
    end
    local account = getPlayerAccount(player)
    local adminGroup = aclGetGroup("Admin")
    if not account or isGuestAccount(account)
        or not adminGroup or not isObjectInACLGroup("user." .. getAccountName(account), adminGroup) then
        outputChatBox("{cmd}: admins only", player, 255, 80, 80)
        return
    end
    local m = modelEntry(name or "") or MODELS[1]
    if not m then
        outputChatBox("{cmd}: this pack has no models", player, 255, 80, 80)
        return
    end
    local x, y, z = getElementPosition(player)
{spawn}
end)
'''

_SERVER_BODY = {
    "vehicle": ('''function createPackVehicle(name, x, y, z, rx, ry, rz)
    local m = modelEntry(name)
    if not m then
        return false
    end
    local vehicle = createVehicle(m.parent, x, y, z, rx or 0, ry or 0, rz or 0)
    if vehicle then
        setElementData(vehicle, MODEL_DATA_KEY, m.name)
    end
    return vehicle
end''', "createPackVehicle", '''    local vehicle = createPackVehicle(m.name, x + 3, y, z + 1)
    if vehicle then
        warpPedIntoVehicle(player, vehicle)
    end'''),
    "skin": ('''function setPackSkin(ped, name)
    local m = modelEntry(name)
    if not m or not isElement(ped) then
        return false
    end
    local elementType = getElementType(ped)
    if elementType ~= "ped" and elementType ~= "player" then
        return false
    end
    if not setElementModel(ped, m.parent) then
        return false
    end
    return setElementData(ped, MODEL_DATA_KEY, m.name)
end''', "setPackSkin", '''    setPackSkin(player, m.name)
    outputChatBox(("{cmd}: skin %s at %.1f %.1f %.1f"):format(m.name, x, y, z), player)'''),
    "object": ('''function createPackObject(name, x, y, z, rx, ry, rz)
    local m = modelEntry(name)
    if not m then
        return false
    end
    local object = createObject(m.parent, x, y, z, rx or 0, ry or 0, rz or 0)
    if object then
        setElementData(object, MODEL_DATA_KEY, m.name)
    end
    return object
end''', "createPackObject", '''    createPackObject(m.name, x + 3, y, z)'''),
}

_ELEMENT_TYPES = {"vehicle": ("vehicle",), "skin": ("ped", "player"), "object": ("object",)}


def render_pack(name: str, kind: str, rows: list[dict], new_id: bool, author: str = "satk",
                files: list[str] | None = None) -> dict[str, str]:
    """Texts of the resource files: ``meta.xml``, ``models.lua``, ``client.lua`` and (new ids) ``server.lua``."""
    cmd = re.sub(r"[^a-z0-9]", "", name.lower())[:24] or "pack"
    type_test = " or ".join(f'elementType == "{t}"' for t in _ELEMENT_TYPES[kind])
    apply = _CLIENT_APPLY.replace("{type_test}", type_test) if new_id else ""
    apply_all = "".join(_CLIENT_APPLY_ALL.format(etype=t) for t in _ELEMENT_TYPES[kind]) if new_id else ""
    if new_id and kind == "skin":
        apply_all += "\n    applyModel(localPlayer)"
    out = {"models.lua": models_lua(name, kind, rows),
           "client.lua": _CLIENT.format(name=name, kind=kind, apply=apply, apply_all=apply_all)}
    meta = ["<meta>",
            f'    <info author="{_xml(author)}" name="{_xml(name)}" type="script" version="1.0.0"',
            f'          description="{kind} models ({"new ids" if new_id else "replacements"}), made by satk mta pack"/>']
    if new_id:
        meta.append('    <min_mta_version client="1.6.0" server="1.6.0"/>')
    meta.append('    <script src="models.lua" type="shared"/>')
    meta.append('    <script src="client.lua" type="client"/>')
    if new_id:
        body, create, spawn = _SERVER_BODY[kind]
        out["server.lua"] = _SERVER.format(name=name, create=create, body=body, cmd=cmd,
                                           spawn=spawn.replace("{cmd}", cmd))
        meta.append('    <script src="server.lua" type="server"/>')
        meta.append(f'    <export function="{create}" type="server"/>')
    for f in files or []:
        meta.append(f'    <file src="{_xml(f)}"/>')
    meta.append("</meta>")
    out["meta.xml"] = "\n".join(meta) + "\n"
    return out


def _xml(s: str) -> str:
    if any(not (c in "\t\n\r" or 32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD
                or 0x10000 <= ord(c) <= 0x10FFFF) for c in s):
        raise SatkError("BAD_PARAMS", "text contains a character XML cannot represent")
    return (s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "&#10;").replace("\r", "&#13;").replace("\t", "&#9;"))


def copy_file(src: Path, dst: Path) -> None:
    from ..core.paths import ensure_writable

    dst = ensure_writable(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=dst.parent)
    staging = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, open(src, "rb") as input_file:
            shutil.copyfileobj(input_file, output, 1 << 20)
        os.replace(staging, dst)
    finally:
        if staging.exists():
            staging.unlink()
