"""Executable agent workflows S1-S8 and their measured cost (``satk dev workflow-cost``, WP-12).

``docs/agent/workflows.md`` and the skill describe eight standard call chains. This module is
their executable form: each ``s1``...``s8`` runs the chain the docs recommend, through the
operation registry exactly as the MCP server dispatches a tool call (``op_by_mcp`` + ``invoke``;
the result text is ``envelope.dumps``, what the agent reads). A :class:`Session` counts the calls
(an image Read is a call) and estimates their token cost:

* text: ``(len(arguments JSON) + len(result text)) / CHARS_PER_TOKEN`` -- compact JSON, no
  tokenizer offline, so this is an estimate (+-25 %);
* image Read: ``w * h / 750`` (SPEC §3.5).

Every workflow returns a short ``answer`` dict with the facts a correct agent answer contains; the
tests (``tests/e2e/test_workflows.py``) assert them against the golden numbers. Workflows need
the built indexes / symbol DB / Blender of the workspace; a missing piece is reported as
``skip`` with the reason (never built implicitly: only S2 starts the viewer, as the agent would).
"""

from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.envelope import dumps
from ..core.errors import SatkError

__all__ = ["CHARS_PER_TOKEN", "Call", "Session", "WORKFLOWS", "Skip", "run", "png_size", "image_tokens"]

CHARS_PER_TOKEN = 3.5
#: The street-level Grove Street pose of the E2 eval (bookmark ``grove_center``): CJ's house
#: (``carlshou1_lae2``) straight ahead, the house on the left is ``inst:lae2_stream0#39``.
GROVE_POSE = {"pos": [2490.0, -1655.0, 16.0], "look": [2500.0, -1700.0, 16.0], "fov_h_deg": 70.0}
#: The crash-dump lines of the E3 eval (MTA ``CrashInfo``-style: Module and Offset on their own lines).
CRASH_TEXT = ("Module = C:\\Games\\GTA San Andreas\\gta_sa.exe\n"
              "Code = 0xC0000005\n"
              "Offset = 0x0013BF09\n")


class Skip(Exception):
    """The workflow cannot run here (a component is not built / not available)."""


def png_size(path: str | Path) -> tuple[int, int]:
    """``(w, h)`` from the PNG header (no decoding)."""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise SatkError("BAD_PARAMS", f"not a PNG: {path}")
    return struct.unpack(">II", head[16:24])


def image_tokens(w: int, h: int) -> int:
    """Approximate tokens of an image the agent reads (SPEC §3.5: w*h/750)."""
    return round(w * h / 750)


@dataclass
class Call:
    tool: str
    args: dict
    chars: int
    tokens: int
    ok: bool
    image: tuple[int, int] | None = None
    ms: float = 0.0

    def row(self) -> list:
        a = json.dumps(self.args, ensure_ascii=False, separators=(",", ":"))
        return [self.tool, a if len(a) <= 90 else a[:87] + "...", self.chars, self.tokens, self.ok,
                f"{self.image[0]}x{self.image[1]}" if self.image else None, round(self.ms)]


@dataclass
class Session:
    """Counts tool calls and their (estimated) token cost like an agent session would."""

    calls: list[Call] = field(default_factory=list)
    strict: bool = True

    def call(self, tool: str, **args: Any) -> dict:
        """Call an MCP tool by name; raise ``SatkError`` on ``ok:false`` (when ``strict``)."""
        from ..core.registry import invoke, op_by_mcp

        args = {k: v for k, v in args.items() if v is not None}
        t0 = time.perf_counter()
        env = invoke(op_by_mcp(tool), args)
        ms = (time.perf_counter() - t0) * 1000
        text = dumps(env)
        arg_text = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
        tokens = round((len(arg_text) + len(text)) / CHARS_PER_TOKEN)
        self.calls.append(Call(tool, args, len(text), tokens, bool(env.get("ok")), ms=ms))
        if self.strict and not env.get("ok"):
            err = env.get("error") or {}
            raise SatkError(err.get("code", "INTERNAL"), f"{tool}: {err.get('msg')}", hint=err.get("hint"),
                            data={"envelope": env})
        return env

    def read_image(self, path: str | Path) -> tuple[int, int]:
        """The agent opens an image with Read: one call, ``w*h/750`` tokens."""
        w, h = png_size(path)
        self.calls.append(Call("Read", {"file": Path(path).name}, 0, image_tokens(w, h), True, image=(w, h)))
        return w, h

    @property
    def n(self) -> int:
        return len(self.calls)

    @property
    def tokens(self) -> int:
        return sum(c.tokens for c in self.calls)

    @property
    def chars(self) -> int:
        return sum(c.chars for c in self.calls)

    @property
    def images(self) -> int:
        return sum(1 for c in self.calls if c.image)


def _rows(env: dict) -> list[dict]:
    cols = env.get("cols") or []
    return [dict(zip(cols, r)) for r in env.get("rows") or []]


def _need(cond: bool, why: str) -> None:
    if not cond:
        raise Skip(why)


def _status() -> dict:
    from ..core.registry import invoke

    return invoke("status", {"deep": False})


def _need_index(profile: str = "vanilla") -> None:
    st = _status()
    _need(bool(((st.get("index") or {}).get(profile) or {}).get("built")), f"index {profile!r} not built (satk index build --profile {profile})")


# --------------------------------------------------------------------------- the workflows


def s1(s: Session, tex: str = "ws_rooftarmac1", **_: Any) -> dict:
    """S1. Where is texture X used and what does it look like?"""
    _need_index()
    found = s.call("asset_find", query=tex, kind="tex", limit=5)
    used = s.call("index_query", sql="SELECT v.sid, v.name, v.n_inst FROM model_tex t JOIN v_model v "
                                     "ON v.id = t.model_id WHERE t.texture = ? ORDER BY v.n_inst DESC",
                  params=[tex], limit=5)
    variants = s.call("index_query", sql="SELECT v.pix, count(*) AS txds FROM texture t JOIN v_tex v "
                                         "ON v.rid = t.id WHERE t.name = ? GROUP BY v.pix ORDER BY txds DESC",
                      params=[tex])
    pix = [r["pix"] for r in _rows(variants)][:16]
    sheet = s.call("texture_image", ids=pix, mode="sheet")
    s.read_image(sheet["files"][0])
    return {"textures": found.get("total"), "models": used.get("total"),
            "top_model": (_rows(used) or [{}])[0].get("name"), "pixel_variants": len(pix),
            "sheet": sheet["files"][0]}


def s2(s: Session, target: str = "mock", bm: str | None = None, pose: dict | None = None,
       window: str = "960x540", stop: bool = True, **_: Any) -> dict:
    """S2. Fly to Grove Street: what is the building on the left?"""
    _need_index()
    st = s.call("view_control", action="status", target=target)
    started = False
    if not st.get("up") and target != "mock":
        s.call("view_control", action="start", target=target, window=window)
        started = True
    try:
        cap = s.call("view_capture", target=target, bm=bm, pose=None if bm else (pose or GROVE_POSE), marks=6)
        s.read_image(cap.get("marks_file") or cap["file"])
        legend = cap.get("legend") or []
        # "on the left": the marked building with the smallest x of its mark among the legend; the agent
        # does this by looking at the marks image. Here: pick at the left third of the frame and take
        # the first building-like hit (an inst: SID that is not a road/land/vegetation piece).
        w, h = cap.get("w", 960), cap.get("h", 540)
        pick = s.call("view_pick", target=target, points=[[round(w * 0.16), round(h * 0.5)]],
                      capture=cap.get("id"))
        hit = (_rows(pick) or [{}])[0]
        sid = hit.get("id")
        obj = s.call("asset_get", id=sid) if sid and str(sid).startswith("inst:") else {}
        return {"capture": cap["file"], "settled": cap.get("settled"), "marks": len(legend),
                "approx": bool(cap.get("approx")), "left": sid, "left_model": obj.get("model"),
                "left_name": obj.get("name") or hit.get("name"), "ipl": obj.get("ipl"),
                "legend_sids": [row[1] for row in legend]}
    finally:
        if started and stop:
            from ..core.registry import invoke, op_by_mcp

            invoke(op_by_mcp("view_control"), {"action": "stop", "target": target})  # cleanup, not counted


def s3(s: Session, text: str = CRASH_TEXT, **_: Any) -> dict:
    """S3. MTA crashed: crash address -> function -> source -> MTA patches."""
    st = _status()
    _need(bool((st.get("re") or {}).get("built")), "symbol DB not built (satk re build)")
    a = s.call("re_addr", text=[text])
    fn = a.get("fn")
    src = s.call("re_src", fn=fn, context=12)
    patches = s.call("re_patches", fn=fn, limit=10)
    hits = [r for r in (a.get("patches") or {}).get("rows") or [] if r[-1] is True]
    return {"fn": fn, "off": a.get("off"), "src": a.get("src"), "confidence": a.get("confidence"),
            "patch_at_addr": sorted({r[2] for r in hits if r[2]}), "patches_in_fn": patches.get("total"),
            "src_lines": len(src.get("lines") or [])}


def s4(s: Session, profile: str = "installed", **_: Any) -> dict:
    """S4. Check a mod (here: what the original install adds over vanilla) -> look at it."""
    _need_index()
    _need_index(profile)
    d = s.call("index_diff", a="vanilla", b=profile, kind="txd", limit=10)
    rows = _rows(d)
    _need(bool(rows), f"no TXD differences between vanilla and {profile}")
    sheet = s.call("texture_image", ids=[rows[0]["id"]], mode="sheet", profile=profile)
    s.read_image(sheet["files"][0])
    return {"summary": d.get("summary"), "first": rows[0]["id"], "sheet": sheet["files"][0],
            "textures": len(sheet.get("legend") or [])}


def s5(s: Session, center: tuple[float, float] = (2495.0, -1687.0), box: float = 60.0, **_: Any) -> dict:
    """S5. Area -> Blender -> render (and back as an MTA resource)."""
    _need_index()
    st = _status()
    _need(bool((st.get("blender") or {}).get("ok")), "Blender not available (satk blender doctor)")
    imp = s.call("blender_job", cmd="import_area", args={"center": list(center), "box": box, "lod": "hd"})
    blend = (imp.get("files") or {}).get("blend")
    pose = {"pos": [center[0], center[1] - 70, 45], "look": [center[0], center[1], 13]}
    ren = s.call("blender_job", cmd="render", args={"blend": blend, "pose": pose, "size": "960x540",
                                                       "objindex": True})
    png = ((ren.get("files") or {}).get("png") or [None])[0]
    if png:
        s.read_image(png)
    exp = s.call("blender_job", cmd="export", args={"blend": blend, "objects": ["carlshou1_lae2"],
                                                       "target": "mta-resource"})
    ist, rst = imp.get("stats") or {}, ren.get("stats") or {}
    return {"blend": blend, "stats": {k: ist[k] for k in list(ist)[:8]}, "render": png,
            "render_stats": {k: rst[k] for k in list(rst)[:8]}, "export_files": exp.get("files")}


def s6(s: Session, **_: Any) -> dict:
    """S6. Does the viewer match the game? (needs target=game: WP-13/16)."""
    st = s.call("view_control", action="status", target="game")
    _need(bool(st.get("up")), "target=game is not available (the real game is not running)")
    return {}


def s7(s: Session, **_: Any) -> dict:
    """S7. How many models have no collision in Las Venturas? One SQL."""
    _need_index()
    q = s.call("index_query", sql=(
        "SELECT i.is_lod, count(DISTINCT m.id) AS models FROM v_inst i JOIN v_model m ON m.id = i.model_id "
        "JOIN zone z ON z.name = 'VE' WHERE i.x BETWEEN z.minx AND z.maxx AND i.y BETWEEN z.miny AND z.maxy "
        "AND i.area = 0 AND m.col_via IS NULL GROUP BY i.is_lod"))
    by = {r["is_lod"]: r["models"] for r in _rows(q)}
    return {"hd_models_without_col": by.get(0, 0), "lod_models_without_col": by.get(1, 0)}


def s8(s: Session, **_: Any) -> dict:
    """S8. What does SA-MP override?"""
    _need_index()
    _need_index("samp")
    d = s.call("index_diff", a="vanilla", b="samp", kind="model", limit=10)
    m300 = s.call("asset_get", id="model:300", profile="samp", fields=["name", "sec", "layer"])
    return {"summary": (d.get("summary") or {}).get("model"), "model_300": m300.get("name"),
            "model_300_layer": m300.get("layer")}


#: id -> (function, question, call budget from the docs)
WORKFLOWS: dict[str, tuple[Callable[..., dict], str, int]] = {
    "S1": (s1, "texture: where used, how it looks", 5),
    "S2": (s2, "viewer: the building on the left at Grove Street", 8),
    "S3": (s3, "crash address -> function -> MTA patches", 4),
    "S4": (s4, "check a mod (profile diff) and look at it", 4),
    "S5": (s5, "area -> Blender -> render -> MTA resource", 5),
    "S6": (s6, "viewer vs game (target=game)", 3),
    "S7": (s7, "one SQL: models without collision in Las Venturas", 1),
    "S8": (s8, "what SA-MP overrides", 2),
}


def run(wid: str, **kw: Any) -> dict:
    """Run one workflow; ``{"wf", "status": ok|skip|fail, "calls", "tokens", "answer"|"reason", "steps"}``."""
    fn, question, budget = WORKFLOWS[wid]
    s = Session()
    t0 = time.perf_counter()
    try:
        answer = fn(s, **kw)
        status, extra = "ok", {"answer": answer}
    except Skip as e:
        status, extra = "skip", {"reason": str(e)}
    except SatkError as e:
        status, extra = "fail", {"reason": f"{e.code}: {e}"}
    return {"wf": wid, "question": question, "status": status, "calls": s.n, "budget": budget,
            "tokens": s.tokens, "chars": s.chars, "images": s.images,
            "seconds": round(time.perf_counter() - t0, 2), "steps": [c.row() for c in s.calls], **extra}
