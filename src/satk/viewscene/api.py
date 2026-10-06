"""Operations behind ``satk view place|vehicle|ped|reload|remove|list`` (SAAP ``scene.*``).

Every function talks to the running viewer target through :func:`satk.viewer.backends.get_backend`
(the endpoint discovery files; ``mock`` without a running endpoint uses the in-process mock). The
SAAP side is ``proto/SAAP-v1.md`` §11; this module only checks and shapes the arguments and the
answers (handles, ``el:scene/<handle>`` SIDs, timings, warnings).
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.envelope import obj, round_pos, table, with_warn
from ..core.errors import SatkError

__all__ = ["place", "vehicle", "ped", "reload", "remove", "list_scene", "model_arg", "file_arg", "parse_parts",
           "parse_colors"]

START_HINT = "satk view start --target ariane"
REBUILD_HINT = ("build the viewer from a branch with the scene patch: "
                "powershell -ExecutionPolicy Bypass -File <workspace>\\viewer\\ariane\\satk\\build.ps1")


# --------------------------------------------------------------------------- arguments


def model_arg(model: Any) -> int | str | None:
    """Game model from the CLI: ``426``, ``"426"``, ``"model:426"`` -> 426; a name -> lower case."""
    if model is None or model == "":
        return None
    if isinstance(model, bool):
        raise SatkError("BAD_PARAMS", "model must be an id or a name")
    if isinstance(model, int):
        return model
    s = str(model).strip()
    if s.lower().startswith("model:"):
        s = s[6:]
    if s.lstrip("-").isdigit():
        return int(s)
    if not s:
        raise SatkError("BAD_PARAMS", "model must be an id or a name")
    return s.lower()


def file_arg(path: str, what: str) -> str:
    """An existing file as an absolute forward-slash path (relative paths: from the current folder)."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"{what} not found: {paths.jpath(p)}",
                        hint="give the path of an existing file (absolute, or relative to the current folder)",
                        data={"path": paths.jpath(p)})
    return paths.jpath(p)


def _vec3(v: Any, name: str) -> list[float]:
    if v is None:
        raise SatkError("BAD_PARAMS", f"--{name} is required (x,y,z)", hint=f"--{name} 2495,-1675,13.4")
    try:
        out = [float(c) for c in v]
    except (TypeError, ValueError):
        raise SatkError("BAD_PARAMS", f"--{name} needs 3 numbers x,y,z") from None
    if len(out) != 3 or not all(math.isfinite(c) for c in out):
        raise SatkError("BAD_PARAMS", f"--{name} needs 3 numbers x,y,z")
    return out


def parse_parts(parts: Any) -> dict[str, str] | None:
    """``["door_lf=dam", "bonnet=off"]`` (or a dict) -> ``{"door_lf": "dam", "bonnet": "off"}``."""
    if not parts:
        return None
    if isinstance(parts, dict):
        items = list(parts.items())
    else:
        items = []
        for s in parts:
            if not isinstance(s, str) or "=" not in s:
                raise SatkError("BAD_PARAMS", f"part {s!r} must be NAME=ok|dam|off", hint="--parts door_lf=dam bonnet=off")
            k, v = s.split("=", 1)
            items.append((k.strip(), v.strip()))
    out = {}
    for k, v in items:
        v = str(v).lower()
        if not k or v not in ("ok", "dam", "off"):
            raise SatkError("BAD_PARAMS", f"part {k!r}: state must be ok, dam or off")
        out[k.lower()] = v
    return out


def parse_colors(colors: Any) -> list[int | str] | None:
    """``["3", "1"]`` or ``["#ff0000"]`` -> carcols indices or ``#rrggbb`` strings (1-4 slots)."""
    if not colors:
        return None
    out: list[int | str] = []
    for c in colors:
        if isinstance(c, int) and not isinstance(c, bool):
            out.append(c)
            continue
        s = str(c).strip()
        if s.isdigit():
            out.append(int(s))
        elif len(s) == 7 and s.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in s[1:]):
            out.append(s.lower())
        else:
            raise SatkError("BAD_PARAMS", f"colour {s!r} must be a carcols.dat index or #rrggbb")
    if len(out) > 4:
        raise SatkError("BAD_PARAMS", "at most 4 colours (paint slots 1-4)")
    return out


def _common(params: dict, *, model: Any, dff: str | None, txd: list[str] | None, pos: Any, rot: Any,
            heading: float | None, ground: bool, watch: bool, id: str | None) -> dict:  # noqa: A002
    m = model_arg(model)
    if m is not None:
        params["model"] = m
    if dff:
        params["dff"] = file_arg(dff, "DFF")
    if txd:
        params["txd"] = [file_arg(t, "TXD") for t in txd]
    params["pos"] = _vec3(pos, "pos")
    if rot is not None and heading is not None:
        raise SatkError("BAD_PARAMS", "give --rot or --heading, not both")
    if rot is not None:
        r = [float(c) for c in rot]
        if len(r) not in (3, 4):
            raise SatkError("BAD_PARAMS", "--rot needs rx,ry,rz degrees or a quaternion x,y,z,w")
        params["rot"] = r
    elif heading is not None:
        params["heading"] = float(heading)
    if ground:
        params["ground"] = True
    if watch:
        params["watch"] = True
    if id:
        params["id"] = str(id)
    return params


# --------------------------------------------------------------------------- calls


def _call(target: str, method: str, params: dict) -> tuple[dict, float, list[str]]:
    from ..viewer.backends import get_backend

    b = get_backend(target)
    try:
        t0 = time.perf_counter()
        r = b.call(method, params)
        return r, round((time.perf_counter() - t0) * 1000.0, 1), list(getattr(b, "warnings", []) or [])
    except SatkError as e:
        if e.code == "UNKNOWN_METHOD":
            raise SatkError("UNSUPPORTED", f"target {target!r} has no {method}: its viewer build predates scene.*",
                            hint=REBUILD_HINT, data={"method": method, "target": target}) from None
        if e.code == "UNSUPPORTED" and not e.hint:
            raise SatkError("UNSUPPORTED", e.msg, data=e.data, hint=(
                "scene.* runs in the viewer (satk view start --target ariane); behaviour in the real game "
                "goes through MTA (docs: mta-agent.md)")) from None
        if e.code == "NOT_READY" and not e.hint:
            raise SatkError(e.code, e.msg, data=e.data, hint=START_HINT) from None
        raise
    finally:
        try:
            b.close()
        except Exception:  # noqa: BLE001
            pass


def _heading(q: Any) -> float | None:
    if not isinstance(q, list) or len(q) != 4:
        return None
    if abs(q[0]) > 1e-6 or abs(q[1]) > 1e-6:
        return None
    h = math.degrees(2.0 * math.atan2(q[2], q[3])) % 360.0
    return round(h, 1) % 360.0


def _placed(r: dict, rtt: float, warns: list[str], target: str) -> dict:
    ent = r.get("entity") or {}
    h = str(r.get("handle"))
    stats = dict(r.get("stats") or {})
    missing = stats.pop("missing_textures", None)
    w = list(warns) + [str(x) for x in r.get("warn") or []]
    if missing:
        w.append(f"NOT_FOUND: {len(missing)} texture(s) not in the TXDs: {', '.join(missing[:12])}")
    q = (ent.get("rot") or {}).get("q_world")
    heading = _heading(q)
    scene = ent.get("scene") or {}
    out = obj(h, sid=f"el:scene/{h}", ref=r.get("ref"), kind=ent.get("kind"), model=ent.get("model_name"),
              model_id=ent.get("model_id"), pos=round_pos(ent["pos"]) if ent.get("pos") else None,
              heading=heading, q=None if heading is not None else q, grounded=r.get("grounded"),
              replaced=r.get("replaced") or None, dff=scene.get("dff"), txd=scene.get("txd"),
              watch=scene.get("watch"), stats=stats, vehicle=r.get("vehicle"), ped=r.get("ped"),
              load_ms=r.get("load_ms"), rtt_ms=rtt, target=target)
    return with_warn(out, *w)


def place(target: str = "ariane", *, dff: str | None = None, txd: list[str] | None = None, model: Any = None,
          pos: Any = None, rot: Any = None, heading: float | None = None, scale: list[float] | None = None,
          ground: bool = False, watch: bool = False, id: str | None = None) -> dict:  # noqa: A002
    """``scene.place``: a model from files or a game object."""
    if not dff and model_arg(model) is None:
        raise SatkError("BAD_PARAMS", "give a DFF file or --model", hint="satk view place my.dff --txd my.txd --pos X,Y,Z")
    p = _common({}, model=model, dff=dff, txd=txd, pos=pos, rot=rot, heading=heading, ground=ground, watch=watch, id=id)
    if scale is not None:
        s = [float(c) for c in scale]
        if len(s) not in (1, 3) or not all(0 < c <= 1000 for c in s):
            raise SatkError("BAD_PARAMS", "--scale needs 1 or 3 numbers in (0, 1000]")
        p["scale"] = s[0] if len(s) == 1 else s
    r, rtt, w = _call(target, "scene.place", p)
    return _placed(r, rtt, w, target)


def vehicle(target: str = "ariane", *, model: Any = None, dff: str | None = None, txd: list[str] | None = None,
            pos: Any = None, rot: Any = None, heading: float | None = None, colors: Any = None, dirt: int = 0,
            lights: bool = False, parts: Any = None, wheel_model: Any = None,
            wheel_scale: list[float] | None = None, ground: bool = True, watch: bool = False,
            id: str | None = None) -> dict:  # noqa: A002
    """``scene.vehicle``: a vehicle with paint, dirt, lamps, part states and wheels."""
    if not dff and model_arg(model) is None:
        raise SatkError("BAD_PARAMS", "give a vehicle model (id or name) or --dff",
                        hint="satk view vehicle 426 --pos 2495,-1675,13.4 --dirt 2")
    p = _common({}, model=model, dff=dff, txd=txd, pos=pos, rot=rot, heading=heading, ground=ground, watch=watch, id=id)
    if not 0 <= int(dirt) <= 15:
        raise SatkError("BAD_PARAMS", "--dirt must be 0..15")
    p["dirt"] = int(dirt)
    if lights:
        p["lights"] = True
    cols = parse_colors(colors)
    if cols:
        p["colors"] = cols
    ps = parse_parts(parts)
    if ps:
        p["parts"] = ps
    wheels: dict[str, Any] = {}
    wm = model_arg(wheel_model)
    if wm is not None:
        wheels["model"] = wm
    if wheel_scale:
        ws = [float(c) for c in wheel_scale]
        if len(ws) not in (1, 2) or not all(0 < c < 10 for c in ws):
            raise SatkError("BAD_PARAMS", "--wheel-scale needs a diameter in m or front,rear")
        wheels["scale"] = ws[0] if len(ws) == 1 else ws
    if wheels:
        p["wheels"] = wheels
    r, rtt, w = _call(target, "scene.vehicle", p)
    return _placed(r, rtt, w, target)


def ped(target: str = "ariane", *, model: Any = None, dff: str | None = None, txd: list[str] | None = None,
        pos: Any = None, rot: Any = None, heading: float | None = None, anim: str | None = None,
        ifp: str | None = None, anim_time: float = 0.0, ground: bool = True, watch: bool = False,
        id: str | None = None) -> dict:  # noqa: A002
    """``scene.ped``: a skinned ped in a static pose from an IFP frame."""
    if not dff and model_arg(model) is None:
        raise SatkError("BAD_PARAMS", "give a ped model (id or name) or --dff",
                        hint="satk view ped 105 --pos 2497,-1673,13.4")
    p = _common({}, model=model, dff=dff, txd=txd, pos=pos, rot=rot, heading=heading, ground=ground, watch=watch, id=id)
    a: dict[str, Any] = {}
    if ifp:
        looks_like_file = ifp.lower().endswith(".ifp") or "/" in ifp or "\\" in ifp
        a["ifp"] = file_arg(ifp, "IFP") if looks_like_file else ifp
    if anim:
        a["name"] = anim
    if anim_time:
        if not 0 <= float(anim_time) <= 3600:
            raise SatkError("BAD_PARAMS", "--anim-time must be 0..3600 s")
        a["time"] = float(anim_time)
    if a:
        p["anim"] = a
    r, rtt, w = _call(target, "scene.ped", p)
    return _placed(r, rtt, w, target)


def reload(target: str = "ariane", handle: str | None = None) -> dict:
    """``scene.reload`` of one handle or of every placed entity."""
    p = {"handle": handle[1:] if handle and handle.startswith("@") else handle} if handle else {}
    r, rtt, w = _call(target, "scene.reload", p)
    rows = [[x.get("handle"), bool(x.get("ok")), x.get("load_ms"), x.get("error")] for x in r.get("reloaded") or []]
    out = table(["handle", "ok", "load_ms", "error"], rows, warn=w)
    out["rtt_ms"] = rtt
    if r.get("failed"):
        with_warn(out, f"BAD_PARAMS: {r['failed']} reload(s) failed; the previous models stay")
    return out


def remove(target: str = "ariane", handles: list[str] | None = None, all: bool = False) -> dict:  # noqa: A002
    """``scene.remove`` (handles) or ``scene.clear`` (``all``)."""
    if all:
        if handles:
            raise SatkError("BAD_PARAMS", "give handles or --all, not both")
        r, rtt, w = _call(target, "scene.clear", {})
        return with_warn(obj(None, removed=int(r.get("removed", 0)), left=0, rtt_ms=rtt, target=target), *w)
    if not handles:
        raise SatkError("BAD_PARAMS", "give the handles to remove (or --all)", hint="satk view list")
    hs = [h[1:] if h.startswith("@") else h for h in handles]
    r, rtt, w = _call(target, "scene.remove", {"handles": hs})
    return with_warn(obj(None, removed=list(r.get("removed") or []), left=r.get("left"), rtt_ms=rtt, target=target), *w)


def list_scene(target: str = "ariane") -> dict:
    """``scene.list`` as a table."""
    r, rtt, w = _call(target, "scene.list", {})
    cols = ["handle", "sid", "kind", "model", "x", "y", "z", "heading", "watch", "reloads", "load_ms", "file", "error"]
    rows = []
    for it in r.get("items") or []:
        ent = it.get("entity") or {}
        pos = round_pos(ent.get("pos") or [0.0, 0.0, 0.0])
        scene = ent.get("scene") or {}
        h = it.get("handle")
        rows.append([h, f"el:scene/{h}", ent.get("kind"), ent.get("model_name"), pos[0], pos[1], pos[2],
                     _heading((ent.get("rot") or {}).get("q_world")), bool(scene.get("watch")), it.get("reloads", 0),
                     it.get("load_ms"), scene.get("dff"), it.get("error")])
    out = table(cols, rows, total=r.get("total", len(rows)), warn=w)
    out["rtt_ms"] = rtt
    return out
