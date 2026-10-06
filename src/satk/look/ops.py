"""Operation ``blender.preview``: an SA-like preview of a model, a DFF file, a mod folder or a live session.

``mcp=False`` (reached through ``satk_ops``/``satk_op`` and the CLI). Module-level imports are stdlib only;
Blender runs as a job (``satk.blender.runner``) or inside the studio session (method ``look.preview``).
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path
from typing import Literal

from ..core.envelope import obj
from ..core.errors import SatkError
from ..core.paths import jpath, work
from ..core.registry import op, report_progress

__all__ = ["blender_preview", "K1_KEYS"]

#: K1 numbers of each entry in the answer (``satk.style.metrics``; equal to ``asset.check``).
K1_KEYS = ("geo.tris", "dff.verts_per_tri", "shade.normal_bend", "shade.flat_share", "uv.zero_area_share",
           "geo.pieces", "geo.largest_piece_share")
_PASSES = ("game", "clay", "wire", "raw", "tex")
_STATES = ("ok", "dam", "vlo", "col")
#: Default time budget (s) of a preview inside a live session (a typical one takes 1-5 s; the session stops
#: it and stays usable) and of a cold job (a fresh Blender: 5-60 s).
SESSION_TIMEOUT_S = 60.0
COLD_TIMEOUT_S = 300.0


def _split(v, what: str) -> list[str]:
    if v is None:
        return []
    items = v if isinstance(v, (list, tuple)) else [v]
    out = [p.strip() for x in items for p in str(x).split(",") if p.strip()]
    return out


def _check(items: list[str], allowed: tuple, what: str) -> list[str]:
    for x in items:
        if x not in allowed:
            raise SatkError("BAD_PARAMS", f"{what}: unknown {x!r}", did_you_mean=list(allowed))
    return list(dict.fromkeys(items))


def _k1(entry) -> dict:
    """Compact K1 numbers of an entry's DFF (hd parts; vehicles count the single wheel atomic once)."""
    data = entry.dff_bytes()
    if data is None:
        return {}
    try:
        from ..style.dffmesh import dff_metrics

        m = dff_metrics(data, select="hd", name=entry.name or "model")["metrics"]
    except Exception as e:  # noqa: BLE001 - stats are advisory: the preview still renders
        return {"warn": f"INTERNAL: K1 metrics failed: {type(e).__name__}: {e}"[:200]}
    out = {k: m[k] for k in K1_KEYS if isinstance(m.get(k), (int, float))}
    bb = m.get("bbox")
    if isinstance(bb, (list, tuple)) and len(bb) == 6:
        d = [round(float(bb[3]) - float(bb[0]), 2), round(float(bb[4]) - float(bb[1]), 2),
             round(float(bb[5]) - float(bb[2]), 2)]
        if entry.sec == "peds":  # the bind pose is Y-up: width, depth, height as it stands
            d = [d[0], d[2], d[1]]
        out["dims"] = d
    return out


def _envs(times: list[str], weather: str, profile: str) -> list[dict]:
    from . import gamelook as G

    out = []
    for t in times:
        try:
            out.append(G.env_at(t, weather, profile))
        except ValueError as e:
            raise SatkError("BAD_PARAMS", str(e), hint="--time 12:00 or --time 12:00,22:00") from None
    return out


def _lineup(subject_entries, lineup: str | None, like: str | None, n: int, profile: str,
            asset: dict | None = None, notes: list[str] | None = None) -> list[str]:
    """Peer SIDs: ``class`` = the like model (when given) + ``n`` peers of its style peer set (a session
    without a like model: the class and size of its ``asset.json``); else an explicit SID list."""
    from . import lineup as LU

    first = subject_entries[0]
    own = first.kind in ("file", "session")
    if not lineup or lineup == "none":
        return [like] if like and first.kind == "file" else []
    if lineup != "class":
        return _split(lineup, "lineup")
    ref_sid = like or (first.sid if first.kind == "sid" else None)
    stats = getattr(first, "_k1", None) or {}
    dims = stats.get("dims") or ((asset or {}).get("dims") or {}).get("target")
    cls = _asset_class(asset) if not ref_sid else None
    sec = first.sec or (_class_sec(cls) if cls else None)
    if not ref_sid and not cls and not sec:
        raise SatkError("BAD_PARAMS", "a lineup needs the subject's class",
                        hint="give --like model:<id> (a session takes it from its project's asset.json)")
    ref = LU.reference(sid=ref_sid, sec=sec or "objs", dims=dims, tris=stats.get("geo.tris"), profile=profile)
    exclude = [ref.get("id")] + [e.plan.get("id") for e in subject_entries if e.plan]
    out = ([like] if like and own else [])
    got, how = LU.class_peers(ref, n, profile=profile, exclude=tuple(exclude), cls=cls)
    out += got
    if notes is not None:
        notes.append(f"NOTE: lineup peers from the {how}")
    return list(dict.fromkeys(out))


def _session_asset(name: str) -> dict | None:
    """``asset.json`` of the project a live session works on (``None`` without one)."""
    from ..saap import client as C
    from ..studio import launcher as L
    from ..studio import project as P

    proj = (C.read_session(L.role(name)) or {}).get("project")
    if not proj:
        return None
    try:
        return P.load(proj)[1]
    except SatkError:
        return None


def _asset_class(asset: dict | None) -> str | None:
    """The style class (with its size bucket) of an asset without a like model: kind + target size."""
    if not asset or not asset.get("kind"):
        return None
    try:
        from ..style import classes as SC

        try:
            cls = SC.resolve(str(asset["kind"]))
        except SatkError:
            cls = next((c for c, v in SC.taxonomy()["classes"].items() if v.get("kind") == asset["kind"]), None)
        if cls is None:
            return None
        dims = ((asset.get("dims") or {}).get("target")) or []
        size = max(float(x) for x in dims) if len(dims) == 3 else None
        return SC.peer_key(cls, SC.size_bucket(size))
    except Exception:  # noqa: BLE001 - no style package: the IDE fallback needs a like model
        return None


def _class_sec(cls: str) -> str:
    """The IDE section of a style class (for the IDE fallback of the peer choice)."""
    try:
        from ..style import classes as SC

        fam = SC.family(cls)
    except Exception:  # noqa: BLE001
        return "objs"
    return {"vehicle": "cars", "ped": "peds", "weapon": "weap"}.get(fam, "objs")


def _session_paint(like: str | None, profile: str) -> list | None:
    """Paint colours of the like model when it is a vehicle (a session's paint keys get them in the preview)."""
    if not like:
        return None
    try:
        from ..blender.resolve import plan_model
        from . import lineup as LU

        row = LU.model_row(like, profile)
        if row is None or row.get("sec") != "cars":
            return None
        return ((plan_model(like, profile=profile)["model"].get("vehicle") or {}).get("colors")) or None
    except SatkError:
        return None


@op("blender.preview",
    summary="SA-look preview of a model SID, your own DFF file, a mod folder or a live session (session:NAME): "
            "game/clay/wire passes, ok/dam/vlo/col states, lineup with class peers at one scale, day/night, map "
            "context; one JPEG sheet (<=1024 px, <=300 KB) + K1 shading stats.",
    summary_ru="Превью в стиле SA: SID модели, свой DFF, папка мода или живая сессия (session:NAME); проходы "
               "game/clay/wire, состояния ok/dam/vlo/col, линейка с одноклассниками в одном масштабе, день/ночь, "
               "контекст карты; один JPEG + статистика K1.",
    mcp=False, long_running=True,
    examples=("satk blender preview model:426",
              "satk blender preview <workspace>/work/out/mycar/premier.dff --like model:426 --lineup class",
              "satk blender preview model:426 --states ok,dam --passes game,wire --views 3q,side",
              "satk blender preview session:car --lineup class",
              "satk blender preview inst:lae2_roads#4 --context 60 --time 12:00,23:00"))
def blender_preview(subject: str, txd: list[str] | None = None, like: str | None = None, lineup: str | None = None,
                    peers: int = 2, views: list[str] | None = None, passes: list[str] | None = None,
                    states: list[str] | None = None, dirt: float = 2.0, time: list[str] | None = None,  # noqa: A002
                    lights: Literal["off", "on"] = "off", weather: str = "EXTRASUNNY_LA",
                    context: float | None = None, size: int = 384, fmt: Literal["jpg", "webp"] = "jpg",
                    profile: str = "vanilla", timeout: float | None = None) -> dict:
    """Render an SA-like preview sheet.

    Args:
        subject: model SID or name, a .dff file, a mod folder, or session:NAME (a running studio session).
        txd: TXD file(s) of a .dff subject (default: the TXD of the same name next to it).
        like: game model a file stands for (its class, paint and lineup reference), e.g. model:426; a session
            of an asset project takes it from asset.json.
        lineup: class (the like model + peers from its style peer set) or a comma list of SIDs to stand next
            to the subject.
        peers: class peers in a 'class' lineup.
        views: 3q, rear3q, front, rear, side, top (default 3q,rear3q,side,top; a lineup: side,3q).
        passes: game (the engine's look), clay, wire (triangle edges), raw (materials as imported), tex
            (own textures at native scale, lossless PNG).
        states: ok, dam (damaged parts), vlo (low LOD), col (collision).
        dirt: dirt level 0..16 of vehiclegrunge256 (game: 0..14 at spawn; 16 = the raw texture).
        time: game time(s) HH:MM, one row each (12:00,23:00 = day and night).
        lights: off or on (lamps lit with vehiclelightson128).
        weather: timecyc weather of the light (EXTRASUNNY_LA ...).
        context: an inst: subject: radius of the map around it (m), rendered in place instead of alone.
        size: cell size in pixels (reduced so that the sheet fits 1024 px).
        fmt: jpg or webp.
        profile: game profile.
        timeout: seconds Blender may take (default 60 in a live session, which then stops the preview and
            stays usable; 300 for a cold job).
    """
    from . import compose, gamelook as G, inputs

    t0 = _time.perf_counter()
    pass_l = _check(_split(passes, "passes") or ["game"], _PASSES, "passes")
    state_l = _check(_split(states, "states") or ["ok"], _STATES, "states")
    times = _split(time, "time") or ["12:00"]
    if len(times) > 4:
        raise SatkError("BAD_PARAMS", "at most 4 times")
    if not 0 <= float(dirt) <= G.DIRT_MAX:
        raise SatkError("BAD_PARAMS", f"dirt must be 0..{G.DIRT_MAX}")
    if not 48 <= int(size) <= 1024:
        raise SatkError("BAD_PARAMS", "size must be 48..1024")
    if not 0 <= int(peers) <= 4:
        raise SatkError("BAD_PARAMS", "peers must be 0..4")
    envs = _envs(times, weather, profile)
    report_progress(0, 3, "resolving the subject")
    if context and not str(subject).lower().startswith("inst:"):
        raise SatkError("BAD_PARAMS", "--context needs an inst: subject (a placement on the map)",
                        hint="satk world near X,Y lists placements")
    want_col = "col" in state_l
    subj, warn = inputs.resolve_subject(subject, txd=txd, like=like, profile=profile, col=want_col)
    session = subj[0].name if subj[0].kind == "session" else None
    asset = None
    if session:
        asset = _session_asset(session)
        if not like and asset and asset.get("like"):
            like = str(asset["like"])
            warn.append(f"NOTE: like {like} from the project's asset.json")
        subj[0].paint = _session_paint(like, profile)
    for e in subj:
        if e.kind != "session":
            e._k1 = _k1(e)  # type: ignore[attr-defined]
    peer_sids = _lineup(subj, lineup, like, int(peers), profile, asset=asset, notes=warn) if not context else []
    peer_entries, w2 = inputs.resolve_peers(peer_sids, profile=profile, start=len(subj), col=want_col)
    warn += w2
    for e in peer_entries:
        e._k1 = _k1(e)  # type: ignore[attr-defined]
    entries = subj + peer_entries
    render_passes = [p for p in pass_l if p != "tex"]
    lineup_mode = len(entries) > 1
    view_l = _split(views, "views") or (["side", "3q"] if lineup_mode else ["3q", "rear3q", "side", "top"])
    if view_l == ["std"]:
        view_l = ["3q", "rear3q", "side", "top"]
    nrows = len(state_l) * max(1, len(render_passes)) * len(times)
    aspect = min(3.0, 0.75 * len(entries)) if lineup_mode else 1.0
    cw, chh, grid = compose.layout(nrows, len(view_l), int(size), aspect=max(1.0, aspect))
    spec: dict = {"entries": [e.spec() for e in entries], "views": view_l, "passes": render_passes,
                  "states": state_l, "size": [cw, chh], "dirt": float(dirt), "lights": lights, "times": times,
                  "envs": envs, "weather": weather, "profile": profile}
    if context:
        from ..blender import resolve

        if not 5 <= float(context) <= 500:
            raise SatkError("BAD_PARAMS", "context must be 5..500 m")
        pos = inputs.inst_row(str(subject), profile)[1]
        plan = resolve.plan_area(center=pos[:2], r=float(context), lod="hd", area=None, limit=2000, profile=profile)
        spec = dict(spec, entries=[], area={"plan": plan, "focus": str(subject).lower(), "margin": float(context) / 3},
                    states=["ok"])
        state_l = ["ok"]
        warn += plan.get("warnings") or []
    out_dir = work("out", "preview", out_key(subject, spec, pass_l, fmt))
    res: dict = {}
    job = None
    budget = float(timeout) if timeout else (SESSION_TIMEOUT_S if session else COLD_TIMEOUT_S)
    if not 5 <= budget <= 7200:
        raise SatkError("BAD_PARAMS", "timeout must be 5..7200 s")
    if render_passes:
        report_progress(1, 3, "Blender render")
        if session:
            res, job = _run_session(session, spec, budget)
        else:
            from ..blender import runner

            resp = runner.run_job("preview", {"spec": spec}, profile=profile, timeout=budget)
            res = resp.get("preview") or {}
            job = resp.get("job")
            warn += list(resp.get("warnings") or [])
    report_progress(2, 3, "sheet")
    files: dict = {}
    legend: dict = {"entries": [[e.key, e.label, e.kind] for e in entries]}
    title = ", ".join(e.label for e in entries) if not context else f"{subject} context {context:g} m"
    if res.get("cells"):
        info = compose.sheet(res["cells"], res.get("rows") or [], res.get("cols") or [], title=title,
                             out=out_dir / f"preview.{fmt}", fmt=fmt, grid=grid)
        files["sheet"] = info["path"]
        legend.update(rows=res.get("rows"), cols=res.get("cols"))
        sheet_info = {"w": info["w"], "h": info["h"], "bytes": info["bytes"]}
    else:
        sheet_info = {}
    if "tex" in pass_l:
        txds = []
        for e in subj:
            if e.plan:
                own = [t for t, n in zip(e.plan.get("txd") or [], e.plan.get("txd_names") or []) if n != "vehicle"]
                txds += own[:1] if e.kind == "sid" else own
        if txds:
            ti = compose.texture_sheet(txds, out_dir / "textures.png")
            files["textures"] = ti["path"]
            legend["textures"] = ti["legend"][:40]
            warn += ti.get("warn") or []
        else:
            warn.append("NOT_FOUND: tex pass: the subject has no TXD of its own")
    stats = {}
    for e in entries:
        k = getattr(e, "_k1", None)
        if k is None:
            k = (res.get("stats") or {}).get(e.key) or {}
        if isinstance(k, dict) and k.get("warn"):
            warn.append(k.pop("warn"))
        if k:
            stats[e.key] = k
    for e in entries:
        warn += [w for w in e.warn if w not in warn]
    warn += [w for w in (res.get("warnings") or []) if w not in warn]
    report_progress(3, 3, "done")
    return obj(None, subject=str(subject), files=files, sheet=sheet_info or None, legend=legend, stats=stats,
               env=[{k: e.get(k) for k in ("time", "balance", "weather", "source")} for e in envs],
               session=session, job=job, seconds=round(_time.perf_counter() - t0, 2),
               blender_s=(res.get("seconds") or None), warn=warn)


def out_key(subject: str, spec: dict, passes: list[str], fmt: str) -> str:
    """Output folder of a preview: ``<subject slug>-<hash of the spec>`` (the same call reuses it)."""
    import hashlib
    import re

    s = str(subject)
    stem = Path(s).stem if ("/" in s or "\\" in s) else s
    slug = re.sub(r"[^a-z0-9_]+", "_", stem.lower()).strip("_")[:32] or "preview"
    h = hashlib.sha1(json.dumps([spec, passes, fmt], sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{h}"


def _run_session(name: str, spec: dict, timeout: float) -> tuple[dict, str | None]:
    from ..studio import api

    try:
        r = api.call("look.preview", {"spec": spec}, session=name, stats="none", timeout=timeout)
    except SatkError as e:
        if e.code == "TIMEOUT":
            raise SatkError("TIMEOUT", f"the preview in session {name!r} did not finish within {timeout:g} s: {e.msg}"
                            [:300], hint=e.hint or f"satk blender session status --name {name}",
                            data=e.data) from None
        raise
    res = r.get("result") or {}
    p = res.get("json")
    if not p:
        raise SatkError("EXTERNAL_TOOL", "the session's look.preview returned no result",
                        hint="satk blender methods --query look (is the look plug-in loaded?)", data={"reply": r})
    try:
        return json.loads(Path(p).read_text(encoding="utf-8")), None
    except (OSError, ValueError) as e:
        raise SatkError("EXTERNAL_TOOL", f"cannot read the session preview result {jpath(Path(p))}: {e}") from None
