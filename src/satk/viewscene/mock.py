"""The SAAP ``scene.*`` methods of the mock endpoint (stdlib only).

:class:`MockScene` is attached lazily to :class:`satk.saap.mock.MockWorld`: placed entities
become boxes of the synthetic world, so captures, the ID layer, ``pick`` and ``entity.query``
see them like the viewer's. Files given as ``dff``/``txd``/``anim.ifp`` must exist and the DFF
must start with a RenderWare clump (or UV animation dictionary) chunk; their content is not
drawn. ``watch`` is emulated: every scene call first re-reads the stamps of watched files and
counts a reload when they changed.

Example::

    from satk.saap.mock import MockWorld
    w = MockWorld()
    w.handle("scene.vehicle", {"model": 411, "pos": [2495, -1675, 13.4], "dirt": 2})
    w.handle("scene.list", {})["total"]   # 1
"""

from __future__ import annotations

import math
import os
import re
import struct
from dataclasses import dataclass
from typing import Any

from ..core.errors import SatkError
from ..saap.mock import GROUND_Z, MODELS, MockEntity, entity_ref

__all__ = ["MockScene", "SceneEntity", "MAX_ENTITIES", "VEHICLE_PARTS"]

MAX_ENTITIES = 256
#: Parts every mock vehicle has (``parts`` keys).
VEHICLE_PARTS = ("bonnet", "boot", "bump_front", "bump_rear", "door_lf", "door_lr", "door_rf", "door_rr",
                 "windscreen")
_HALF = {"object": (0.5, 0.5, 0.5), "vehicle": (1.0, 2.4, 0.75), "ped": (0.35, 0.35, 0.95)}
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")
#: RenderWare chunk ids a DFF may start with: clump, UV animation dictionary.
_RW_FIRST = (0x10, 0x2B)


@dataclass(frozen=True)
class SceneEntity(MockEntity):
    """A placed entity drawn as a box; ``label`` is its model name (file stem or game name)."""

    label: str = ""

    @property
    def name(self) -> str:
        return self.label or MODELS.get(self.model_id, (f"model{self.model_id}", ""))[0]


def _stamp(path: str) -> tuple:
    try:
        st = os.stat(path)
    except OSError:
        return (False, 0, 0)
    return (True, st.st_size, st.st_mtime_ns)


def _abs(path: Any, key: str) -> str:
    if not isinstance(path, str) or not re.match(r"^[A-Za-z]:[\\/]", path):
        raise SatkError("BAD_PARAMS", f"{key} must be an absolute path (X:/...)")
    return path.replace("\\", "/")


def _need_file(path: str) -> None:
    if not os.path.isfile(path):
        raise SatkError("NOT_FOUND", f"no such file: {path}", data={"path": path})


def _check_dff(path: str) -> None:
    _need_file(path)
    with open(path, "rb") as f:
        head = f.read(12)
    if len(head) < 12 or struct.unpack("<I", head[:4])[0] not in _RW_FIRST:
        raise SatkError("BAD_PARAMS", f"{path} is not a RenderWare clump (DFF)", data={"path": path})


def _quat(p: dict) -> list[float]:
    if "rot" in p and "heading" in p:
        raise SatkError("BAD_PARAMS", "give rot or heading, not both")
    if "rot" in p:
        r = [float(c) for c in p["rot"]]
        if len(r) == 4:
            n = math.sqrt(sum(c * c for c in r))
            if n < 1e-6:
                raise SatkError("BAD_PARAMS", "rot quaternion must not be zero")
            return [c / n for c in r]
        hx, hy, hz = (math.radians(c) / 2 for c in r)
        cx, sx, cy, sy, cz, sz = math.cos(hx), math.sin(hx), math.cos(hy), math.sin(hy), math.cos(hz), math.sin(hz)
        return [cz * cy * sx - sz * sy * cx, cz * sy * cx + sz * cy * sx, sz * cy * cx - cz * sy * sx,
                cz * cy * cx + sz * sy * sx]
    h = math.radians(float(p.get("heading", 0.0))) / 2
    return [0.0, 0.0, math.sin(h), math.cos(h)]


def _heading(q: list[float]) -> float:
    return math.degrees(2 * math.atan2(q[2], q[3])) % 360.0


class MockScene:
    """State of the mock's placed entities (one per :class:`MockWorld`)."""

    def __init__(self, world: Any):
        self.world = world
        self.items: dict[str, dict] = {}
        self.next_slot = 1

    # -- MockWorld hooks ---------------------------------------------------------------------

    def entities(self) -> tuple[SceneEntity, ...]:
        """Placed entities as boxes for the mock renderer (insertion order)."""
        return tuple(it["entity"] for it in self.items.values())

    def call(self, method: str, p: dict) -> dict:
        self._poll()
        fn = getattr(self, "_" + method.split(".", 1)[1])
        return fn(p or {})

    # -- helpers -----------------------------------------------------------------------------

    def _poll(self) -> None:
        for it in self.items.values():
            if not it["watch"]:
                continue
            cur = [_stamp(f) for f in it["files"]]
            if cur != it["stamps"] and all(s[0] for s in cur):
                it["stamps"] = cur
                it["reloads"] += 1
                self.world.scene_rev += 1

    def _model(self, p: dict, kind: str) -> tuple[int | None, str]:
        m = p.get("model")
        if m is None:
            return None, ""
        if isinstance(m, bool) or not isinstance(m, (int, str)):
            raise SatkError("BAD_PARAMS", "model must be an id or a name")
        if isinstance(m, int):
            if kind == "ped" and m == 0:
                raise SatkError("NOT_FOUND", "unknown ped; CJ (model 0) uses the clothes system, pick a ped skin "
                                             "such as 105 (fam1)", data={"model": m})
            return m, MODELS.get(m, (f"model{m}", ""))[0]
        name = m.lower()
        for mid, (n, _) in MODELS.items():
            if n == name:
                return mid, n
        return None, name

    def _files(self, p: dict) -> tuple[str | None, list[str]]:
        dff = p.get("dff")
        if dff is not None:
            dff = _abs(dff, "dff")
            _check_dff(dff)
        txd = p.get("txd")
        txds = [txd] if isinstance(txd, str) else list(txd or [])
        out = []
        for t in txds:
            t = _abs(t, "txd")
            _need_file(t)
            out.append(t)
        return dff, out

    def _vehicle_info(self, p: dict, model_id: int | None) -> dict:
        cols = p.get("colors") or [1, 1]
        colors = []
        for i, c in enumerate(cols):
            if isinstance(c, str):
                rgb, idx = c.lower(), None
            else:
                if not 0 <= int(c) < 128:
                    raise SatkError("BAD_PARAMS", "colour index must be 0..127 (data/carcols.dat)")
                v = (int(c) * 37) % 256
                rgb, idx = f"#{v:02x}{(v * 3) % 256:02x}{(v * 7) % 256:02x}", int(c)
            colors.append({"slot": i + 1, "index": idx, "rgb": rgb} if idx is not None else {"slot": i + 1, "rgb": rgb})
        parts = {k: "ok" for k in VEHICLE_PARTS}
        for k, v in (p.get("parts") or {}).items():
            key = k.lower()
            for suffix in ("_dummy", "_ok", "_dam"):
                if key.endswith(suffix):
                    key = key[: -len(suffix)]
                    break
            if key not in parts:
                raise SatkError("BAD_PARAMS", f"no part '{key}' in this vehicle", data={"parts": list(VEHICLE_PARTS)})
            parts[key] = v
        wheels = p.get("wheels") or {}
        sc = wheels.get("scale", 0.7)
        scale = [float(sc), float(sc)] if not isinstance(sc, list) else [float(x) for x in sc]
        return {"type": "car", "wheels": {"from": f"model {wheels['model']}" if "model" in wheels else "own",
                                          "scale": scale, "count": 4},
                "colors": colors, "dirt": int(p.get("dirt", 0)), "lights": bool(p.get("lights", False)), "parts": parts}

    def _ped_info(self, p: dict) -> tuple[dict, list[str]]:
        anim = p.get("anim") or {}
        ifp = anim.get("ifp") or "ped"
        files = []
        if re.match(r"^[A-Za-z]:[\\/]", ifp):
            ifp = ifp.replace("\\", "/")
            _need_file(ifp)
            files.append(ifp)
        name = (anim.get("name") or "idle_stance").lower()
        t = min(float(anim.get("time", 0.0)), 1.5)
        return {"anim": {"ifp": ifp, "name": name, "time": round(t, 3), "duration": 1.5, "bones": 32, "nodes": 32}}, files

    def _entity_json(self, it: dict) -> dict:
        e = entity_ref(it["entity"])
        if e.get("model_id") is not None and it["model_id"] is None:
            e.pop("model_id")
        e["rot"] = {"q_world": [round(c, 6) for c in it["q"]]}
        scene: dict[str, Any] = {"handle": it["handle"]}
        if it["dff"]:
            scene["dff"] = it["dff"]
        if it["txd"]:
            scene["txd"] = list(it["txd"])
        if it["watch"]:
            scene["watch"] = True
        e["scene"] = scene
        return e

    def _details(self, it: dict, out: dict) -> dict:
        half = it["entity"].half
        if it["kind"] == "vehicle":
            out["vehicle"] = it["info"]
        elif it["kind"] == "ped":
            out["ped"] = it["info"]
        out["stats"] = {"atomics": 1, "tris": 12, "textures": len(it["txd"]),
                        "bounds": [[-half[0], -half[1], -half[2]], [half[0], half[1], half[2]]]}
        if it["grounded"]:
            out["grounded"] = True
        return out

    # -- methods -----------------------------------------------------------------------------

    def _place_kind(self, p: dict, kind: str) -> dict:
        hid = p.get("id")
        if hid is not None and (not isinstance(hid, str) or not _ID.match(hid)):
            raise SatkError("BAD_PARAMS", "id must be 1-48 characters [A-Za-z0-9_.-]")
        existing = self.items.get(hid) if hid else None
        if existing is None and len(self.items) >= MAX_ENTITIES:
            raise SatkError("BAD_PARAMS", f"at most {MAX_ENTITIES} scene entities (scene.remove or scene.clear)")
        model_id, model_name = self._model(p, kind)
        dff, txds = self._files(p)
        if dff is None and p.get("model") is None:
            raise SatkError("BAD_PARAMS", f"scene.{'place' if kind == 'object' else kind} needs dff (a file) or model")
        label = model_name or os.path.splitext(os.path.basename(dff or ""))[0].lower()
        q = _quat(p)
        pos = [float(c) for c in p["pos"]]
        half = _HALF[kind]
        sc = p.get("scale", 1.0)
        s = [float(sc)] * 3 if not isinstance(sc, list) else [float(x) for x in sc]
        half = (half[0] * s[0], half[1] * s[1], half[2] * s[2])
        grounded = bool(p.get("ground"))
        if grounded:
            pos[2] = GROUND_Z + half[2]
        files = ([dff] if dff else []) + txds
        info: dict = {}
        if kind == "vehicle":
            info = self._vehicle_info(p, model_id)
        elif kind == "ped":
            info, more = self._ped_info(p)
            files += more
        if existing is not None:
            handle, slot = existing["handle"], existing["slot"]
        else:
            slot = self.next_slot
            self.next_slot += 1
            handle = hid or f"s{slot}"
            while handle in self.items:
                slot = self.next_slot
                self.next_slot += 1
                handle = f"s{slot}"
        ent = SceneEntity(f"@{handle}", kind, model_id if model_id is not None else -1, tuple(pos), half,
                          {"kind": "runtime", "type": "scene", "id": handle}, heading=round(_heading(q), 4),
                          label=label)
        it = {"handle": handle, "slot": slot, "kind": kind, "entity": ent, "model_id": model_id, "q": q,
              "dff": dff, "txd": txds, "files": files, "watch": bool(p.get("watch")),
              "stamps": [_stamp(f) for f in files], "reloads": existing["reloads"] if existing else 0,
              "info": info, "grounded": grounded, "params": dict(p)}
        self.items[handle] = it
        self.world.scene_rev += 1
        out = {"handle": handle, "ref": f"@{handle}", "entity": self._entity_json(it), "replaced": existing is not None}
        out = self._details(it, out)
        out["load_ms"] = 0.0
        return out

    def _place(self, p: dict) -> dict:
        return self._place_kind(p, "object")

    def _vehicle(self, p: dict) -> dict:
        return self._place_kind(p, "vehicle")

    def _ped(self, p: dict) -> dict:
        return self._place_kind(p, "ped")

    def _reload(self, p: dict) -> dict:
        h = p.get("handle")
        if h:
            h = h[1:] if h.startswith("@") else h
            if h not in self.items:
                raise SatkError("NOT_FOUND", f"no scene entity '{h}'", data={"handle": h})
            todo = [self.items[h]]
        else:
            todo = list(self.items.values())
        rows = []
        for it in todo:
            missing = [f for f in it["files"] if not os.path.isfile(f)]
            if missing:
                if h:
                    raise SatkError("BAD_PARAMS", f"reload of '{it['handle']}' failed: NOT_FOUND: no such file: "
                                                  f"{missing[0]} (the previous model stays)", data={"handle": h})
                rows.append({"handle": it["handle"], "ok": False, "load_ms": 0.0,
                             "error": f"NOT_FOUND: no such file: {missing[0]}"})
                continue
            it["stamps"] = [_stamp(f) for f in it["files"]]
            it["reloads"] += 1
            rows.append({"handle": it["handle"], "ok": True, "load_ms": 0.0})
        self.world.scene_rev += 1
        return {"reloaded": rows, "failed": sum(1 for r in rows if not r["ok"])}

    def _remove(self, p: dict) -> dict:
        hs = ([p["handle"]] if "handle" in p else []) + list(p.get("handles") or [])
        if not hs:
            raise SatkError("BAD_PARAMS", "scene.remove needs handle or handles (scene.clear removes all)")
        hs = [h[1:] if h.startswith("@") else h for h in hs]
        for h in hs:
            if h not in self.items:
                raise SatkError("NOT_FOUND", f"no scene entity '{h}'", data={"handle": h})
        removed = []
        for h in hs:
            if self.items.pop(h, None) is not None:
                removed.append(h)
        self.world.scene_rev += 1
        return {"removed": removed, "left": len(self.items)}

    def _clear(self, p: dict) -> dict:
        n = len(self.items)
        self.items.clear()
        self.world.scene_rev += 1
        return {"removed": n}

    def _list(self, p: dict) -> dict:
        items = []
        for it in self.items.values():
            row = {"handle": it["handle"], "entity": self._entity_json(it)}
            row = self._details(it, row)
            row["reloads"] = it["reloads"]
            row["load_ms"] = 0.0
            items.append(row)
        return {"items": items, "total": len(items)}
