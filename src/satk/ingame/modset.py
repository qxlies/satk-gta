"""A mod folder or loose files -> the models of an in-game test (:class:`ModelSpec`).

Every ``.dff`` is a model; its ``.txd`` and ``.col`` are found by name (the DFF stem, the TXD named in
the mod's IDE line, or the only TXD of the set; a ``.col`` archive with several models gives the
matching entry). A ``.txd`` without a DFF of the same name replaces the textures of the vanilla
models that use that TXD. Data lines in the mod's text files (``*.ide *.cfg *.dat *.txt``) add the
IDE definition (kind, draw distance, wheel scale), the ``handling.cfg`` line (converted to MTA
``setVehicleHandling`` properties) and the first ``carcols`` colours.

The mode of a model:

* ``replace`` -- the files replace a vanilla id: ``--replace SID``, or by default when the DFF name is a
  vanilla model name (the Mod Loader convention); weapons always replace;
* ``new`` -- a fresh id from ``engineRequestModel(kind, base)``: ``--new`` (base = ``--base SID``, the
  vanilla model of the same name, or a per-kind default), or by default for names the game does not
  know. The vanilla base stays visible next to it as the reference of the checks.

Vanilla names, kinds and handling come from the index of the profile (``satk index build``); weapon
ids from the game's ``data/weapon.dat``. Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import SatkError

__all__ = ["KINDS", "DEFAULT_BASE", "ModelSpec", "Vanilla", "collect", "scan", "handling_props", "key_of"]

KINDS = ("vehicle", "object", "ped", "weapon")
#: IDE section -> kind.
SEC_KIND = {"cars": "vehicle", "objs": "object", "tobj": "object", "anim": "object", "hier": "object",
            "peds": "ped", "weap": "weapon"}
#: Parent of a new model when nothing names one (premier, MTA's default object 1337, male01).
DEFAULT_BASE = {"vehicle": 426, "object": 1337, "ped": 7}
#: MTA multiplies the file's fEngineAcceleration by this (CHandlingManagerSA: 25.0 -> 10.0).
MTA_ACCEL_SCALE = 0.4
_DATA_EXT = (".ide", ".cfg", ".dat", ".txt")
_MODEL_EXT = (".dff", ".txd", ".col")
_MAX_FILES = 4000
_MAX_DATA_BYTES = 4 << 20


@dataclass
class ModelSpec:
    """One model of the test set (``files`` are source paths; ``base`` the vanilla id)."""

    key: str
    name: str
    kind: str
    mode: str
    base: int
    base_name: str = ""
    ref: bool = True
    files: dict[str, Path] = field(default_factory=dict)
    col_entry: str | None = None          # name of the model inside a multi-model .col
    label: str = ""
    handling: dict[str, Any] | None = None
    handling_line: str | None = None
    wheels: list[float] | None = None
    colors: list[int] | None = None
    draw: float | None = None
    weapon: int | None = None
    expect: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def manifest(self, files: dict[str, str]) -> dict[str, Any]:
        """The manifest entry (``files`` = kind -> path inside the content resource)."""
        out: dict[str, Any] = {"key": self.key, "kind": self.kind, "mode": self.mode, "base": self.base,
                               "label": self.label or self.name, "files": files, "ref": self.ref}
        for k in ("handling", "wheels", "colors", "draw", "weapon"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        if self.expect:
            out["expect"] = self.expect
        return out


def key_of(name: str) -> str:
    k = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    return k[:24] or "model"


# --------------------------------------------------------------------------- vanilla data


class Vanilla:
    """Vanilla lookups through the profile's index (lazy; ``INDEX_MISSING`` only when needed)."""

    def __init__(self, profile: str = "vanilla"):
        self.profile = profile
        self._db = None
        self._weapons: dict[int, int] | None = None

    def _q(self, sql: str, params: list) -> list[list]:
        if self._db is None:
            from ..index.api import open_index

            self._db = open_index(self.profile)
        return self._db.query(sql, params, limit=50).get("rows") or []

    def by_name(self, name: str) -> dict | None:
        rows = self._q("select id, name, sec, txd from model where lower(name) = ? and active = 1 order by id "
                       "limit 1", [name.lower()])
        return {"id": int(rows[0][0]), "name": rows[0][1], "sec": rows[0][2], "txd": rows[0][3]} if rows else None

    def by_id(self, mid: int) -> dict | None:
        rows = self._q("select id, name, sec, txd from model where id = ? and active = 1 limit 1", [int(mid)])
        return {"id": int(rows[0][0]), "name": rows[0][1], "sec": rows[0][2], "txd": rows[0][3]} if rows else None

    def by_txd(self, txd: str) -> list[dict]:
        rows = self._q("select id, name, sec, txd from model where lower(txd) = ? and active = 1 order by id",
                       [txd.lower()])
        return [{"id": int(r[0]), "name": r[1], "sec": r[2], "txd": r[3]} for r in rows]

    def vehicle(self, mid: int) -> dict | None:
        rows = self._q("select handling, mass, max_vel, accel, drive from v_vehicle where id = ?", [int(mid)])
        if not rows:
            return None
        h, mass, mv, acc, drive = rows[0]
        return {"handling": h, "mass": mass, "max_vel": mv, "accel": acc, "drive": drive}

    def resolve(self, sid: str) -> dict:
        """``model:411`` / ``411`` / ``infernus`` -> the vanilla model (``NOT_FOUND`` otherwise)."""
        s = str(sid).strip()
        m = re.fullmatch(r"(?:model:)?(\d+)", s, re.I)
        try:
            found = self.by_id(int(m.group(1))) if m else self.by_name(s.split(":", 1)[-1] if s.lower().startswith(
                "model:") else s)
        except SatkError as e:
            if e.code != "INDEX_MISSING" or not m:
                raise
            found = {"id": int(m.group(1)), "name": "", "sec": None, "txd": None}  # no index: the id as given
        if not found:
            raise SatkError("NOT_FOUND", f"no vanilla model {sid!r}", hint="satk asset find <name> --kind model")
        return found

    def weapon_id(self, model_id: int) -> int | None:
        """Weapon type of a weapon model (data/weapon.dat, the first matching gun or melee line)."""
        if self._weapons is None:
            self._weapons = {}
            try:
                from ..addon import weapon as W
                from ..core import paths

                path = paths.profile_root(self.profile) / "data" / "weapon.dat"
                with paths.open_ro(path) as f:
                    text = f.read().decode("latin-1")
                wf = W.parse(text)
                for rec in wf.recs.values():
                    if rec.kind not in ("gun", "melee"):
                        continue
                    mid = rec.values.get("model1")
                    tid = W.type_id(rec.name)
                    if isinstance(mid, int) and mid > 0 and tid is not None:
                        self._weapons.setdefault(mid, tid)
            except (OSError, SatkError, AttributeError, KeyError, ValueError):
                self._weapons = {}
        return self._weapons.get(int(model_id))


# --------------------------------------------------------------------------- inputs


def collect(inputs: list[str]) -> tuple[list[Path], list[Path]]:
    """``(model files, data files)`` of folders (recursive) and loose files, sorted."""
    models: list[Path] = []
    data: list[Path] = []
    seen: set[str] = set()
    for raw in inputs:
        p = Path(raw).expanduser()
        if not p.exists():
            raise SatkError("NOT_FOUND", f"mod path not found: {raw}")
        files = sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]
        for f in files:
            k = str(f.resolve()).lower()
            if k in seen:
                continue
            seen.add(k)
            ext = f.suffix.lower()
            if ext in _MODEL_EXT:
                models.append(f)
            elif ext in _DATA_EXT:
                data.append(f)
            if len(seen) > _MAX_FILES:
                raise SatkError("BAD_PARAMS", f"more than {_MAX_FILES} files under {raw}: point at the mod folder")
    if not any(f.suffix.lower() in (".dff", ".txd") for f in models):
        raise SatkError("NOT_FOUND", "no .dff or .txd in the given mod paths", data={"paths": list(inputs)})
    return models, data


def _read_data(files: list[Path]) -> list[tuple[Path, str]]:
    out = []
    for f in files:
        try:
            if f.stat().st_size > _MAX_DATA_BYTES:
                continue
            out.append((f, f.read_bytes().decode("latin-1")))
        except OSError:
            continue
    return out


def _ide_lines(texts: list[tuple[Path, str]]) -> dict[str, tuple[str, Any]]:
    """name -> (section, IdeRec) from IDE sections and loose IDE-like lines (Mod Loader readmes)."""
    from ..formats import ide as I

    out: dict[str, tuple[str, Any]] = {}
    for _path, text in texts:
        try:
            defs, _txdp, _fx = I.parse_ide(text)
        except Exception:  # noqa: BLE001 - other people's files: skip what does not parse
            defs = []
        for d in defs:
            out.setdefault(d.name.lower(), (d.sec, d))
        for raw in text.splitlines():
            f = [x for x in raw.split("#", 1)[0].replace(",", " ").split()]
            if len(f) >= 11 and f[0].isdigit() and f[3].lower() in ("car", "bike", "bmx", "quad", "mtruck", "heli",
                                                                    "plane", "boat", "trailer", "train"):
                try:
                    rec = I._rec("cars", f, 0)
                except (ValueError, IndexError):
                    continue
                out.setdefault(rec.name.lower(), ("cars", rec))
    return out


def _handling_recs(texts: list[tuple[Path, str]]) -> dict[str, Any]:
    from ..addon import handling as H

    out: dict[str, Any] = {}
    for _path, text in texts:
        try:
            hf = H.parse(text)
        except Exception:  # noqa: BLE001
            continue
        for (hid, kind), rec in hf.recs.items():
            if kind == "car" and len(rec.values) >= 30:
                out.setdefault(hid.lower(), rec)
    return out


def _carcols(texts: list[tuple[Path, str]], name: str) -> list[int] | None:
    pat = re.compile(rf"^\s*{re.escape(name)}\s*,\s*(.+)$", re.I)
    for _path, text in texts:
        for raw in text.splitlines():
            m = pat.match(raw.split("#", 1)[0])
            if not m:
                continue
            nums = [int(x) for x in re.split(r"[\s,]+", m.group(1).strip()) if x.isdigit()]
            if len(nums) >= 2 and all(0 <= n <= 255 for n in nums[:2]):
                return [nums[0], nums[1], 0, 0]  # the first primary/secondary pair (carcols.dat 'car' section)
    return None


def handling_props(rec) -> dict[str, Any]:
    """A ``handling.cfg`` car record -> MTA ``setVehicleHandling`` properties (``engineAcceleration`` x 0.4)."""
    from ..addon import fields as F

    t = F.table("handling")
    enums = t["enums"]
    v = rec.values
    out: dict[str, Any] = {}
    for f in t["car"]:
        prop, key = f.get("mta"), f["key"]
        if not prop or f.get("mta_set") is False or key not in v or key in ("com_y", "com_z"):
            continue
        val = v[key]
        if prop == "centerOfMass":
            out[prop] = [round(float(v["com_x"]), 4), round(float(v["com_y"]), 4), round(float(v["com_z"]), 4)]
        elif key == "drive":
            out[prop] = enums["mta_drive"].get(str(val).upper(), "rwd")
        elif key == "engine":
            out[prop] = enums["mta_engine"].get(str(val).upper(), "petrol")
        elif key == "abs":
            out[prop] = bool(int(val))
        elif key == "accel":
            out[prop] = round(float(val) * MTA_ACCEL_SCALE, 4)
        elif f["type"] in ("i", "x"):
            out[prop] = int(val)
        else:
            out[prop] = round(float(val), 4)
    return out


def _col_entry(path: Path, name: str) -> str | None:
    """Name of the model in ``path`` matching ``name`` (``None`` = take the file as it is)."""
    from ..formats.col import iter_col

    try:
        models = list(iter_col(path.read_bytes()))
    except Exception:  # noqa: BLE001
        return None
    if len(models) <= 1:
        return None
    for m in models:
        if m.name.lower() == name.lower():
            return m.name
    return None


def col_bytes(path: Path, entry: str | None) -> bytes:
    """The bytes of one model of a .col archive (the whole file when ``entry`` is None)."""
    data = path.read_bytes()
    if entry is None:
        return data
    from ..formats.col import iter_col

    off = 0
    for m in iter_col(data):
        if m.name.lower() == entry.lower():
            return data[off:off + m.size]
        off += m.size
    raise SatkError("NOT_FOUND", f"{path.name} has no collision model {entry!r}")


# --------------------------------------------------------------------------- scan


def scan(inputs: list[str], *, kind: str = "auto", replace: str | None = None, new: bool = False,
         base: str | None = None, label: str | None = None, profile: str = "vanilla",
         vanilla: Vanilla | None = None) -> tuple[list[ModelSpec], list[str]]:
    """Models of a mod (see the module docstring) and warnings."""
    if kind not in ("auto", *KINDS):
        raise SatkError("BAD_PARAMS", f"kind must be auto or one of {', '.join(KINDS)}")
    if replace and new:
        raise SatkError("BAD_PARAMS", "--replace and --new exclude each other", hint="--new --base SID loads a "
                        "new id next to the vanilla model")
    van = vanilla or Vanilla(profile)
    warn: list[str] = []
    model_files, data_files = collect(inputs)
    texts = _read_data(data_files)
    ides = _ide_lines(texts)
    hrecs = _handling_recs(texts)
    by_ext: dict[str, dict[str, Path]] = {".dff": {}, ".txd": {}, ".col": {}}
    for f in model_files:
        by_ext[f.suffix.lower()].setdefault(f.stem.lower(), f)
    dffs = sorted(by_ext[".dff"].items())
    txds = by_ext[".txd"]
    cols = by_ext[".col"]
    if replace and len(dffs) > 1:
        raise SatkError("BAD_PARAMS", f"--replace names one model but the mod has {len(dffs)} DFFs",
                        data={"dffs": [n for n, _ in dffs][:20]})

    def vanilla_of(name: str) -> dict | None:
        try:
            return van.by_name(name)
        except SatkError as e:
            if e.code != "INDEX_MISSING":
                raise
            return None

    specs: list[ModelSpec] = []
    used_txd: set[str] = set()
    for name, dff in dffs:
        ide = ides.get(name)
        same = vanilla_of(name)
        target = van.resolve(replace) if replace else None
        parent = van.resolve(base) if base else None
        k = kind
        if k == "auto":
            sec = (target or {}).get("sec") or (ide[0] if ide else None) or (same or {}).get("sec") or \
                (parent or {}).get("sec")
            k = SEC_KIND.get(str(sec or "").lower(), "")
            if not k:
                raise SatkError("BAD_PARAMS", f"cannot tell the kind of {dff.name}: no IDE line and no vanilla model "
                                "of that name", hint="give --kind vehicle|object|ped|weapon (and --replace SID or "
                                "--new --base SID)")
        if target:
            mode, b, ref = "replace", target, True
        elif new or not same:
            mode = "new"
            if parent:
                b, ref = parent, True
            elif same:
                b, ref = same, True
            else:
                d = DEFAULT_BASE.get(k)
                b = van.by_id(d) if d is not None else None
                if b is None:
                    raise SatkError("BAD_PARAMS", f"{dff.name} is not a vanilla model: say what it replaces or "
                                    "which model it is based on", hint="--replace SID or --new --base SID")
                ref = False
                warn.append(f"NO_REFERENCE: {name} is a new {k} on the default base {b['name']} ({b['id']}); "
                            "give --base SID for a vanilla reference")
        else:
            mode, b, ref = "replace", same, True
        if k == "weapon" and mode == "new":
            if not same and not target:
                raise SatkError("UNSUPPORTED", "MTA cannot request new weapon models: give --replace SID",
                                hint="satk asset find <weapon> --kind model")
            mode, b, ref = "replace", target or same, True
            warn.append(f"WEAPON_REPLACE: {name}: weapons can only replace a vanilla weapon model")
        spec = ModelSpec(key=key_of(name), name=name, kind=k, mode=mode, base=int(b["id"]), base_name=str(b["name"]),
                         ref=ref, label=label or name)
        spec.files["dff"] = dff
        txd_name = (ide[1].txd.lower() if ide and ide[1].txd else None)
        txd = txds.get(name) or (txds.get(txd_name) if txd_name else None)
        if txd is None and len(txds) == 1 and len(dffs) == 1:
            txd = next(iter(txds.values()))
        if txd is not None:
            spec.files["txd"] = txd
            used_txd.add(txd.stem.lower())
        else:
            spec.notes.append("no TXD: the model uses the textures of its id's TXD slot")
            warn.append(f"NO_TXD: {name}: no .txd found next to {dff.name}")
        if k == "object":
            col = cols.get(name)
            if col is None:
                for c in cols.values():
                    if _col_entry(c, name):
                        col = c
                        break
            if col is not None:
                spec.files["col"] = col
                spec.col_entry = _col_entry(col, name)
            elif mode == "new" and not same:
                warn.append(f"NO_COL: {name}: a new object without a .col has the collision of {b['name']}")
        if ide and ide[1].draw:
            spec.draw = float(ide[1].draw)
        if k == "vehicle":
            _vehicle_data(spec, ide, hrecs, texts, van, warn)
        if k == "weapon":
            spec.weapon = van.weapon_id(spec.base)
            if spec.weapon is None:
                warn.append(f"NO_WEAPON_ID: {name}: model {spec.base} is not in data/weapon.dat")
        specs.append(spec)
    for tname, txd in sorted(txds.items()):
        if tname in used_txd:
            continue
        try:
            users = van.by_txd(tname)
        except SatkError as e:
            if e.code != "INDEX_MISSING":
                raise
            users = []
        if not users:
            warn.append(f"UNUSED_TXD: {txd.name} matches no DFF of the mod and no vanilla TXD")
            continue
        u = users[0]
        k = SEC_KIND.get(str(u["sec"]).lower(), "object") if kind == "auto" else kind
        spec = ModelSpec(key=key_of(tname), name=tname, kind=k, mode="replace", base=int(u["id"]),
                         base_name=str(u["name"]), label=label or tname, files={"txd": txd})
        if len(users) > 1:
            spec.notes.append(f"TXD shared by {len(users)} vanilla models; tested on {u['name']}")
        if k == "vehicle":
            _vehicle_data(spec, None, hrecs, texts, van, warn)
        if k == "weapon":
            spec.weapon = van.weapon_id(spec.base)
        specs.append(spec)
    keys: dict[str, int] = {}
    for s in specs:
        n = keys.get(s.key, 0)
        keys[s.key] = n + 1
        if n:
            s.key = f"{s.key[:20]}_{n + 1}"
    if not specs:
        raise SatkError("NOT_FOUND", "no model to test in the given mod paths")
    return specs, warn


def _vehicle_data(spec: ModelSpec, ide, hrecs: dict, texts, van: Vanilla, warn: list[str]) -> None:
    vv = None
    try:
        vv = van.vehicle(spec.base)
    except SatkError as e:
        if e.code != "INDEX_MISSING":
            raise
    hid = None
    if ide and ide[0] == "cars":
        hid = str(ide[1].extra.get("handling") or "") or None
        f, r = ide[1].extra.get("wheel_scale_f"), ide[1].extra.get("wheel_scale_r")
        if isinstance(f, (int, float)) and isinstance(r, (int, float)):
            spec.wheels = [round(float(f), 4), round(float(r), 4)]
    hid = hid or (vv or {}).get("handling")
    rec = hrecs.get(str(hid or "").lower()) if hid else None
    if rec is None and len(hrecs) == 1 and (spec.mode == "new" or vv is None):
        rec = next(iter(hrecs.values()))  # the mod's only handling line (no index to match its id)
    if rec is not None:
        spec.handling = handling_props(rec)
        spec.handling_line = rec.text.strip()
        spec.expect = {"source": "mod", "mass": rec.values.get("mass"), "max_vel": rec.values.get("max_vel"),
                       "accel": rec.values.get("accel"), "drive": rec.values.get("drive")}
    elif vv:
        spec.expect = {"source": "vanilla", "mass": vv["mass"], "max_vel": vv["max_vel"], "accel": vv["accel"],
                       "drive": vv["drive"]}
    if vv:
        spec.expect["vanilla_max_vel"] = vv["max_vel"]
        spec.expect["vanilla_accel"] = vv["accel"]
    spec.colors = _carcols(texts, spec.name)
