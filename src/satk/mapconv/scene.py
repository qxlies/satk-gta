"""The canonical map scene of ``satk.mapconv`` (report 23 §13 P0) and its JSON form.

Every reader (Pawn, MTA ``.map``, IPL, JSON) produces a :class:`Scene`; every writer consumes one.
Field semantics are SA-MP's, because it is the richest source:

* ``rot`` - Euler degrees in the SA-MP/MTA convention (:mod:`satk.mapconv.rot`);
* ``interior`` / ``world`` - ``-1`` = all interiors / all virtual worlds (SA-MP ``-1``, MTA
  ``dimension="-1"``); IPL placements get their area code and world ``0``;
* ``draw`` - per-instance draw distance (``0`` = the IDE default); ``stream`` - streamer stream
  distance (``None`` = ``STREAMER_OBJECT_SD``, 300 m);
* MTA-only fields (``scale``, ``alpha``, ``doublesided``, ``collisions``, ``breakable``, ``frozen``)
  and IPL-only fields (``lod`` = index in :attr:`Scene.objects`, ``iflags``) keep their defaults
  when the source has no such thing;
* ``materials`` / ``texts`` - ``Set(Dynamic)ObjectMaterial(Text)`` per slot (the last call wins);
* ``line`` - provenance: source line (Pawn/IPL/``.map``), ``0`` when unknown.

:class:`Issue` is one diagnostic (``sev`` = ``error`` | ``warn`` | ``info``); readers append them to
:attr:`Scene.issues`, the converter adds losses, :mod:`satk.mapconv.validate` adds index checks.
"""

from __future__ import annotations

import json
import math
from dataclasses import MISSING, dataclass, field, fields
from typing import Any

__all__ = [
    "Material", "MaterialText", "MapObject", "Removal", "CustomModel", "Issue", "Scene",
    "scene_to_json", "scene_from_json", "num", "SEVERITIES", "JSON_FORMAT",
]

JSON_FORMAT = "satk-map"
SEVERITIES = ("error", "warn", "info")


def num(v: float, nd: int = 6) -> str:
    """Shortest fixed-point text of ``v`` with at most ``nd`` decimals: ``12.5``, ``-3``, ``0``."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    s = f"{float(v):.{nd}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass
class Material:
    """``SetObjectMaterial(obj, slot, model, txd, tex, color)``; ``color`` is ARGB (0 = keep)."""

    slot: int
    model: int
    txd: str
    tex: str
    color: int = 0
    line: int = 0


@dataclass
class MaterialText:
    """``SetObjectMaterialText``: ``size`` is the OBJECT_MATERIAL_SIZE_* value (90 = 256x128)."""

    slot: int
    text: str
    size: int = 90
    font: str = "Arial"
    font_size: int = 24
    bold: int = 1
    color: int = 0xFFFFFFFF
    back: int = 0
    align: int = 0
    line: int = 0


@dataclass
class MapObject:
    """One placed object (see the module docstring for field semantics)."""

    model: int
    pos: tuple[float, float, float]
    rot: tuple[float, float, float] = (0.0, 0.0, 0.0)
    interior: int = -1
    world: int = -1
    draw: float = 0.0
    stream: float | None = None
    scale: float = 1.0
    alpha: int = 255
    doublesided: bool = False
    collisions: bool = True
    breakable: bool | None = None
    frozen: bool = False
    lod: int = -1
    iflags: int = 0
    name: str | None = None      # model name (IDE / IPL / MTA id "object (name) (n)")
    label: str | None = None     # MTA element id as written in the source
    func: str | None = None      # Pawn function that created it
    materials: list[Material] = field(default_factory=list)
    texts: list[MaterialText] = field(default_factory=list)
    line: int = 0

    def set_material(self, m: Material) -> bool:
        """Put ``m`` into its slot; True if it replaced an earlier call for the same slot."""
        for i, old in enumerate(self.materials):
            if old.slot == m.slot:
                self.materials[i] = m
                return True
        self.materials.append(m)
        return False

    def set_text(self, t: MaterialText) -> bool:
        """Put ``t`` into its slot; True if it replaced an earlier call for the same slot."""
        for i, old in enumerate(self.texts):
            if old.slot == t.slot:
                self.texts[i] = t
                return True
        self.texts.append(t)
        return False


@dataclass
class Removal:
    """``RemoveBuildingForPlayer`` / MTA ``removeWorldObject``: model ``-1`` = every model."""

    model: int
    pos: tuple[float, float, float]
    radius: float
    lod_model: int | None = None
    interior: int = -1
    name: str | None = None
    label: str | None = None
    line: int = 0


@dataclass
class CustomModel:
    """``AddSimpleModel(Timed)`` / ``AddCharModel`` (SA-MP 0.3.DL / open.mp ``artconfig.txt``)."""

    kind: str  # simple | timed | char
    base: int
    id: int
    dff: str
    txd: str
    world: int = -1
    time_on: int | None = None
    time_off: int | None = None
    line: int = 0


@dataclass
class Issue:
    """One diagnostic: ``where`` is ``line N``, ``obj N`` or ``rm N`` (0-based scene indexes)."""

    sev: str
    code: str
    where: str
    msg: str

    def row(self) -> list:
        return [self.sev, self.code, self.where, self.msg]


@dataclass
class Scene:
    """A whole map: objects, removals, custom models, plus diagnostics and source info."""

    objects: list[MapObject] = field(default_factory=list)
    removals: list[Removal] = field(default_factory=list)
    models: list[CustomModel] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)   # what the source had and we do not convert
    source: str = ""
    fmt: str = ""
    encoding: str = "utf-8"

    def issue(self, sev: str, code: str, where: str, msg: str) -> None:
        self.issues.append(Issue(sev, code, where, msg))

    def skip(self, what: str, n: int = 1) -> None:
        self.skipped[what] = self.skipped.get(what, 0) + n

    @property
    def n_materials(self) -> int:
        return sum(len(o.materials) for o in self.objects)

    @property
    def n_texts(self) -> int:
        return sum(len(o.texts) for o in self.objects)

    def counts(self) -> dict[str, int]:
        """``objects removals materials texts models errors warnings`` (zero counts kept)."""
        sev = [i.sev for i in self.issues]
        return {"objects": len(self.objects), "removals": len(self.removals), "materials": self.n_materials,
                "texts": self.n_texts, "models": len(self.models), "errors": sev.count("error"),
                "warnings": sev.count("warn")}


# --------------------------------------------------------------------------- JSON form

_DEFAULTS: dict[type, dict[str, Any]] = {}


def _defaults(cls) -> dict[str, Any]:
    """Field defaults of a dataclass; fields without one are absent."""
    d = _DEFAULTS.get(cls)
    if d is None:
        d = {}
        for f in fields(cls):
            if f.default is not MISSING:
                d[f.name] = f.default
            elif f.default_factory is not MISSING:
                d[f.name] = f.default_factory()
        _DEFAULTS[cls] = d
    return d


def _plain(o) -> dict:
    """Dataclass -> dict without default-valued fields (``line`` dropped: provenance only)."""
    out: dict[str, Any] = {}
    dflt = _defaults(type(o))
    for f in fields(o):
        v = getattr(o, f.name)
        if f.name == "line":
            continue
        if isinstance(v, list):
            if v:
                out[f.name] = [_plain(x) for x in v]
            continue
        if isinstance(v, tuple):
            v = [float(c) for c in v]
            if isinstance(dflt.get(f.name), tuple) and list(dflt[f.name]) == v:
                continue
            out[f.name] = v
            continue
        if f.name in dflt and dflt[f.name] == v and type(dflt[f.name]) is type(v):
            continue
        out[f.name] = v
    return out


def scene_to_json(scene: Scene) -> str:
    """Deterministic JSON text of a scene (``{"format": "satk-map", "version": 1, ...}``)."""
    doc: dict[str, Any] = {"format": JSON_FORMAT, "version": 1}
    if scene.objects:
        doc["objects"] = [_plain(o) for o in scene.objects]
    if scene.removals:
        doc["removals"] = [_plain(r) for r in scene.removals]
    if scene.models:
        doc["models"] = [_plain(m) for m in scene.models]
    return json.dumps(doc, ensure_ascii=False, indent=1, allow_nan=False) + "\n"


_INT = {"model", "interior", "world", "alpha", "lod", "iflags", "slot", "color", "size", "font_size", "bold",
        "back", "align", "lod_model", "base", "id", "time_on", "time_off"}
_FLOAT = {"draw", "stream", "scale", "radius"}
_BOOL = {"doublesided", "collisions", "breakable", "frozen"}


def _coerce(k: str, v: Any, where: str) -> Any:
    if v is None:
        return None
    if k in _BOOL:
        if not isinstance(v, bool):
            raise ValueError(f"{where}.{k}: expected true/false")
        return v
    if k in _INT:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
            raise ValueError(f"{where}.{k}: expected an integer")
        return int(v)
    if k in _FLOAT:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"{where}.{k}: expected a number")
        return float(v)
    if k in ("pos", "rot"):
        if not isinstance(v, list) or len(v) != 3 or not all(
                isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) for c in v):
            raise ValueError(f"{where}.{k}: expected [x, y, z] (finite numbers)")
        return tuple(float(c) for c in v)
    if not isinstance(v, str):
        raise ValueError(f"{where}.{k}: expected a string")
    return v


def _build(cls, d: dict, where: str):
    if not isinstance(d, dict):
        raise ValueError(f"{where}: expected an object, got {type(d).__name__}")
    names = {f.name for f in fields(cls)}
    unknown = sorted(set(d) - names)
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {', '.join(unknown)}")
    kw: dict[str, Any] = {}
    for k, v in d.items():
        if k in ("materials", "texts"):
            sub = Material if k == "materials" else MaterialText
            if not isinstance(v, list):
                raise ValueError(f"{where}.{k}: expected a list")
            v = [_build(sub, x, f"{where}.{k}[{i}]") for i, x in enumerate(v)]
        else:
            v = _coerce(k, v, where)
        kw[k] = v
    try:
        return cls(**kw)
    except TypeError as e:
        raise ValueError(f"{where}: {e}") from None


def scene_from_json(text: str) -> Scene:
    """Parse :func:`scene_to_json` output; ``ValueError`` with a JSON path on bad input."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"line {e.lineno}: {e.msg}") from None
    if not isinstance(doc, dict) or doc.get("format") != JSON_FORMAT:
        raise ValueError(f'not a satk map: expected {{"format": "{JSON_FORMAT}", ...}}')
    if doc.get("version") != 1:
        raise ValueError(f"unsupported satk map version {doc.get('version')!r} (this satk reads 1)")
    for key in ("objects", "removals", "models"):
        if not isinstance(doc.get(key) or [], list):
            raise ValueError(f"{key}: expected a list")
    sc = Scene(fmt="json")
    sc.objects = [_build(MapObject, o, f"objects[{i}]") for i, o in enumerate(doc.get("objects") or [])]
    sc.removals = [_build(Removal, r, f"removals[{i}]") for i, r in enumerate(doc.get("removals") or [])]
    sc.models = [_build(CustomModel, m, f"models[{i}]") for i, m in enumerate(doc.get("models") or [])]
    return sc
