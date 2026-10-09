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

__all__ = ["blender_preview", "look_leak", "K1_KEYS"]

#: K1 numbers of each entry in the answer (``satk.style.metrics``; equal to ``asset.check``).
K1_KEYS = ("geo.tris", "dff.verts_per_tri", "shade.normal_bend", "shade.flat_share", "uv.zero_area_share",
           "geo.pieces", "geo.largest_piece_share")
_PASSES = ("game", "clay", "wire", "raw", "tex", "leak")
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
            "game/clay/wire/leak passes, ok/dam/vlo/col states, lineup with class peers at one scale, day/night, map "
            "context, --regions close-ups per asset kind; JPEG sheets (<=1024 px) + K1 shading stats.",
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
                    profile: str = "vanilla", timeout: float | None = None, ref: str | None = None,
                    ref_view: Literal["side", "front", "rear"] = "side", ref_flip: bool = False,
                    ref_box: list[int] | None = None, regions: list[str] | None = None,
                    kind: str | None = None) -> dict:
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
            (own textures at native scale, lossless PNG), leak (the light-leak check of the subject: the model black
            on a bright background, gaps marked; see look.leak).
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
        ref: a TRUE side, front or rear photo (far away, wheels round) drawn behind the subject in the matching
            view of the game pass at 30 %, scaled so the object in the photo spans the subject (an outline check,
            no numbers; never a three-quarter photo).
        ref_view: side (the side view: the front on the left), front or rear.
        ref_flip: mirror the photo left-right (a side photo whose car faces right).
        ref_box: x0,y0,x1,y1 pixels of the object in the photo (default: found against the photo's border colour).
        regions: close-up review regions of the subject's kind (all, or names such as front,wheels,interior; satk
            kit kinds lists the kinds): one sheet per region (preview-NNN-<region>.jpg) plus an index sheet; views
            and lineups do not apply.
        kind: asset kind for the regions and the leak rules (default: asset.json, the index row of the model or of
            --like, else a guess from the frame names).
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
    ref_spec = _ref_spec(ref, ref_view, ref_flip, ref_box) if ref else None
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
    region_l = _split(regions, "regions")
    if region_l and context:
        raise SatkError("BAD_PARAMS", "--regions and --context do not combine", hint="regions review one model")
    if region_l and (lineup or views):
        warn.append("NOTE: --regions frames the subject alone: --lineup and --views are ignored")
        lineup, views = None, None
    kind_info = _kind_info(subj[0], kind, asset, like, profile, warn) if (region_l or "leak" in pass_l) else None
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
    if ref_spec and not any(v == ref_view or (ref_view == "side" and v == "left") for v in view_l):
        view_l.append(ref_view)
    if ref_spec and "game" not in pass_l:
        warn.append("NOTE: --ref is drawn in the game pass only")
    nrows = len(state_l) * max(1, len(render_passes)) * len(times)
    aspect = min(3.0, 0.75 * len(entries)) if lineup_mode else 1.0
    cw, chh, grid = compose.layout(nrows, len(view_l), int(size), aspect=max(1.0, aspect))
    spec: dict = {"entries": [e.spec() for e in entries], "views": view_l, "passes": render_passes,
                  "states": state_l, "size": [cw, chh], "dirt": float(dirt), "lights": lights, "times": times,
                  "envs": envs, "weather": weather, "profile": profile}
    if ref_spec:
        spec["ref"] = ref_spec
    regs: list[dict] = []
    if kind_info:
        spec["leak"] = kind_info["leak"]
    if region_l:
        from . import regions as RG

        regs = RG.select(kind_info["defn"], region_l)
        spec.update(regions=regs, views=["3q"], size=[int(size), int(size)])
        if any("vlo" in (r.get("states") or []) for r in regs) and kind_info["kind"] not in RG.VEHICLE_KINDS:
            # a map model's LOD is a model of its own: show it in the low state of the lod region
            lod = lod_entry(subj[0], profile, warn, txd=txd)
            if lod is not None:
                spec["entries"].append(dict(lod.spec(), lod_of=subj[0].key))
        if kind_info["defn"].get("ref"):
            spec["region_ref"] = kind_info["defn"]["ref"]
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
    region_rows = None
    if res.get("cells") and regs:
        from . import review

        rs = review.region_sheets(res, regs, title=title, out_dir=out_dir, fmt=fmt, cell=int(size))
        files["index"] = rs["index"]
        region_rows = rs["regions"]
        # a region the model has no surface in is skipped: a marker file says so (a reviewer counts it as seen)
        for w in res.get("warnings") or []:
            if str(w).startswith("SKIPPED: region "):
                name = str(w)[len("SKIPPED: region "):].split(":", 1)[0]
                from ..core.paths import atomic_write

                mark = out_dir / f"preview-{rs['n']:03d}-{review._slug(name)}.skipped.txt"  # noqa: SLF001
                atomic_write(mark, str(w) + "\n")
                region_rows.append([name, jpath(mark), 0, "skipped: the model has no surface inside this region"])
        legend.update(rows=res.get("rows"))
        sheet_info = {}
    elif res.get("cells"):
        info = compose.sheet(res["cells"], res.get("rows") or [], res.get("cols") or [], title=title,
                             out=out_dir / f"preview.{fmt}", fmt=fmt, grid=grid)
        files["sheet"] = numbered_copy(Path(info["path"]))
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
    extra: dict = {}
    if region_rows:
        extra["regions"] = {"cols": ["region", "file", "views", "checks"], "rows": region_rows}
    if kind_info:
        extra["kind"] = kind_info["kind"]
    if res.get("leak") is not None:
        lk = res["leak"]
        gaps = lk.get("gaps") or []
        extra["leak"] = {"clean": not gaps, "gaps": len(gaps), "cols": list(_LEAK_COLS),
                         "rows": [_leak_row(g) for g in gaps[:12]]}
    return obj(None, subject=str(subject), files=files, sheet=sheet_info or None, legend=legend, stats=stats, **extra,
               env=[{k: e.get(k) for k in ("time", "balance", "weather", "source")} for e in envs],
               session=session, job=job, seconds=round(_time.perf_counter() - t0, 2),
               blender_s=(res.get("seconds") or None), warn=warn)


_LEAK_COLS = ("id", "kind", "region", "view", "area_cm2", "point", "near", "seen_in", "deep_m")


def lod_of_file(path: str | Path) -> Path | None:
    """The LOD DFF of a map model file: the one its kit sidecar names, else ``lod<name>.dff``, ``lod*.dff`` with the
    name's tail, or ``<name>_lod.dff`` next to it."""
    p = Path(path)
    stem = p.stem.lower()
    side = p.parent / f"{p.stem}.inventory.json"
    if side.is_file():
        try:
            name = (json.loads(side.read_text(encoding="utf-8")) or {}).get("lod")
        except (OSError, ValueError, AttributeError):
            name = None
        if name and (p.parent / f"{name}.dff").is_file():
            return p.parent / f"{name}.dff"
    cands = {f.stem.lower(): f for f in p.parent.glob("*.dff") if f.stem.lower() != stem}
    for want in (f"lod{stem}", f"lod{stem[3:]}", f"{stem}_lod", f"{stem}lod"):
        if want in cands:
            return cands[want]
    loose = [f for n, f in sorted(cands.items()) if n.startswith("lod") or n.endswith("_lod")]
    return loose[0] if len(loose) == 1 else None


def lod_entry(entry, profile: str, warn: list[str], txd=None):
    """The LOD of a subject as a preview entry (``None`` + a ``NOT_FOUND`` note when it has none): the IPL LOD of a
    game model (a placement's lod link), or the LOD DFF next to a file."""
    from . import inputs

    target = None
    if entry.kind == "sid" and entry.sid:
        try:
            from ..index.api import open_index

            mid = int(str(entry.sid).split(":", 1)[1])
            r = open_index(profile).query("SELECT DISTINCT j.model_id FROM inst i JOIN inst j ON j.id = i.lod_id "
                                          "WHERE i.model_id = ?", [mid], limit=2)
            if r.get("rows"):
                target = f"model:{r['rows'][0][0]}"
        except (SatkError, ValueError, IndexError):
            target = None
    elif entry.kind == "file" and entry.path:
        f = lod_of_file(entry.path)
        target = str(f) if f is not None else None
    if not target:
        warn.append(f"NOT_FOUND: {entry.label} has no LOD model (no IPL LOD link, no lod*.dff next to it): the lod "
                    "region shows the HD model only")
        return None
    try:
        ents, w = inputs.resolve_subject(target, txd=txd if entry.kind == "file" else None, profile=profile, start=90)
    except SatkError as e:
        warn.append(f"NOT_FOUND: the LOD {target} of {entry.label} does not load: {e.msg}"[:200])
        return None
    warn += w
    ent = ents[0]
    ent.key = f"{entry.key}lod"
    return ent


def _leak_row(g: dict) -> list:
    return [g.get("id"), g.get("kind"), g.get("region") or "", g.get("view"), g.get("area_cm2"),
            [round(float(v), 2) for v in g.get("point") or []] or None, g.get("near"), len(g.get("views") or []),
            g.get("deep_m")]


def _ide_flags(entry) -> int | None:
    try:
        return int(((entry.plan or {}).get("ide") or {}).get("flags"))
    except (TypeError, ValueError):
        return None


def _kind_info(entry, kind: str | None, asset: dict | None, like: str | None, profile: str, warn: list[str],
               cull: bool | None = None) -> dict:
    """The subject's kind, its region file and its leak rules (a ``NOTE`` says how the kind was found)."""
    from . import regions as RG
    from . import review

    try:
        k, how = review.subject_kind(entry, kind=kind, asset=asset, like=like, profile=profile)
    except SatkError:
        if kind:
            raise
        k, how = "prop", "default (no kind found; give --kind)"
    warn.append(f"NOTE: kind {k} from {how}")
    defn = RG.load(k)
    return {"kind": defn.get("kind", k), "defn": defn,
            "leak": RG.leak_config(defn, ide_flags=_ide_flags(entry), cull=cull)}


def _resolve(subject: str, txd, like: str | None, profile: str, col: bool = False):
    """Entries of a subject, warnings, the live session (or None), its asset.json and the like model."""
    from . import inputs

    subj, warn = inputs.resolve_subject(subject, txd=txd, like=like, profile=profile, col=col)
    session = subj[0].name if subj[0].kind == "session" else None
    asset = None
    if session:
        asset = _session_asset(session)
        if not like and asset and asset.get("like"):
            like = str(asset["like"])
            warn.append(f"NOTE: like {like} from the project's asset.json")
        subj[0].paint = _session_paint(like, profile)
    return subj, warn, session, asset, like


def _execute(spec: dict, session: str | None, profile: str, budget: float) -> tuple[dict, str | None, list[str]]:
    """Run the preview spec in the live session or in a cold Blender job."""
    if session:
        res, job = _run_session(session, spec, budget)
        return res, job, []
    from ..blender import runner

    resp = runner.run_job("preview", {"spec": spec}, profile=profile, timeout=budget)
    return resp.get("preview") or {}, resp.get("job"), list(resp.get("warnings") or [])


@op("look.leak",
    summary="Light-leak check: the model black on a bright background from the review cameras of its kind; "
            "located gaps (see-through between parts, open shells, culled faces) with view, 3D point, nearest part; "
            "--package writes checks/<stem>.leak.json for asset.check --strict (a full run only).",
    summary_ru="Проверка просветов: модель чёрная на светлом фоне с камер обзора её вида; список просветов (сквозь "
               "детали, открытые оболочки, отсечённые грани) с видом, рамкой, 3D-точкой и ближайшей деталью; "
               "--package пишет checks/<stem>.leak.json (для --strict нужен прогон по всем регионам вида).",
    mcp=False, long_running=True, group="blender",
    examples=("satk look leak model:400",
              "satk look leak <workspace>/work/out/kit/mycar/modloader/mycar/mycar.dff --like model:400 "
              "--package <workspace>/work/out/kit/mycar/modloader/mycar",
              "satk look leak session:car --regions wheels,underside"))
def look_leak(subject: str, kind: str | None = None, regions: list[str] | None = None, views: list[str] | None = None,
              package: str | None = None, like: str | None = None, txd: list[str] | None = None, size: int = 320,
              cull: bool | None = None, limit: int = 20, profile: str = "vanilla", timeout: float | None = None) -> dict:
    """Find the places where a model lets the background through, located, from the review cameras of its kind.

    Args:
        subject: model SID or name, a .dff file, or session:NAME (a running studio session).
        kind: asset kind (default: asset.json, the index row of the model or of --like, else the frame names).
        regions: review regions whose cameras are used (default: every leak region of the kind).
        views: plain views instead of regions (3q, rear3q, front, rear, side, right, top).
        package: the package folder (the folder of the exported DFF) to write checks/<stem>.leak.json into;
            asset.check --strict accepts only a full run (every leak region, no --views, --cull or smaller --size).
        like: game model a file stands for (its kind), e.g. model:400.
        txd: TXD file(s) of a .dff subject (glass and alpha textures are told apart by their textures).
        size: pixels per camera (96..768).
        cull: override the back-face rule of the kind (true: back faces are invisible, as on world objects).
        limit: gap rows to show.
        profile: game profile.
        timeout: seconds Blender may take (default 60 in a live session, 300 for a cold job).
    """
    from ..core.envelope import clamp_limit
    from . import compose, regions as RG, review

    t0 = _time.perf_counter()
    if not 96 <= int(size) <= 768:
        raise SatkError("BAD_PARAMS", "size must be 96..768")
    subj, warn, session, asset, like = _resolve(subject, txd, like, profile)
    if len(subj) > 1:
        raise SatkError("BAD_PARAMS", f"{subject} holds {len(subj)} DFF files: check one at a time",
                        hint="give the .dff file of the model")
    ent = subj[0]
    ki = _kind_info(ent, kind, asset, like, profile, warn, cull=cull)
    ran: list[str] | None = None
    spec: dict = {"entries": [ent.spec()], "passes": ["leak"], "states": ["ok"], "size": [int(size), int(size)],
                  "dirt": 2.0, "lights": "off", "times": ["12:00"],
                  "envs": _envs(["12:00"], "EXTRASUNNY_LA", profile), "weather": "EXTRASUNNY_LA", "profile": profile,
                  "leak": ki["leak"]}
    view_l = _split(views, "views")
    if view_l:
        spec["views"] = view_l
    else:
        regs = [r for r in RG.select(ki["defn"], _split(regions, "regions")) if r.get("leak", True)]
        if not regs:
            have = [r["name"] for r in ki["defn"]["regions"] if r.get("leak", True)]
            raise SatkError("BAD_PARAMS", "no leak region among " + ", ".join(_split(regions, "regions")),
                            hint="regions with leak cameras: " + ", ".join(have))
        spec.update(views=["3q"], regions=regs)
        ran = [str(r["name"]) for r in regs]
        if ki["defn"].get("ref"):
            spec["region_ref"] = ki["defn"]["ref"]
    budget = float(timeout) if timeout else (SESSION_TIMEOUT_S if session else COLD_TIMEOUT_S)
    if not 5 <= budget <= 7200:
        raise SatkError("BAD_PARAMS", "timeout must be 5..7200 s")
    report_progress(1, 3, "Blender ray casting")
    res, job, w2 = _execute(spec, session, profile, budget)
    warn += w2
    warn += [w for w in (res.get("warnings") or []) if w not in warn]
    lk = res.get("leak")
    if lk is None:
        raise SatkError("EXTERNAL_TOOL", "the Blender job returned no leak result", data={"job": job})
    report_progress(2, 3, "sheet")
    out_dir = work("out", "leak", out_key(subject, spec, ["leak"], "jpg"))
    files: dict = {}
    cells = res.get("cells") or []
    ngaps = len(lk.get("gaps") or [])
    if cells:
        n = len({int(c[1]) for c in cells})
        _cw, _ch, grid = compose.layout(1, n, int(size))
        info = compose.sheet(cells, res.get("rows") or [], res.get("cols") or [],
                             title=f"{ent.label} leak ({ki['kind']}): {ngaps} gap(s)", out=out_dir / "leak.jpg",
                             fmt="jpg", grid=grid or min(4, n))
        files["sheet"] = numbered_copy(Path(info["path"]))
    cover = review.coverage(ki["defn"], ran, views=view_l or None, cull=cull, size=int(size))
    # the regions the run really framed (a region with no geometry is skipped by Blender with a note)
    framed = sorted({str(v.get("region")) for v in lk.get("views") or [] if v.get("region")})
    if ran is not None and framed:
        skipped = [r for r in ran if r not in framed]
        if skipped:
            cover.setdefault("skipped", skipped)
    doc = review.leak_report(lk, subject=str(subject), kind=ki["kind"], dff=ent.path or (ent.plan or {}).get("dff"),
                             sheet=files.get("sheet"), cover=cover)
    try:
        rec = review.write_record(doc, job=job)
        if rec:
            files["record"] = rec
    except (SatkError, OSError) as e:
        warn.append(f"INTERNAL: the leak record was not written ({e})"[:200])
    if package:
        files["leak_json"] = review.write_leak(package, doc)
    if not cover["full"]:
        warn.append(f"NOTE: not a full leak run ({cover['why']}): asset.check --strict does not accept it")
    gaps = doc["gaps"]
    lim = clamp_limit(limit, default=20)
    report_progress(3, 3, "done")
    info = doc.get("info") or []
    if info:
        warn.append(f"NOTE: {len(info)} non-blocking view(s) into a shell (info in the sheet and leak.json): "
                    + ", ".join(f"{g['id']} {g.get('region') or g['view']} {g.get('area_cm2')} cm2" for g in info[:4]))
    return obj(None, subject=str(subject), kind=ki["kind"], clean=doc["clean"], full=cover["full"],
               counts=doc["counts"] or None,
               cols=list(_LEAK_COLS), rows=[_leak_row(g) for g in gaps[:lim]], total=len(gaps), files=files,
               views=len(doc["views"]), session=session, job=job, seconds=round(_time.perf_counter() - t0, 2),
               warn=warn)


def numbered_copy(latest: Path) -> str:
    """Keep every run of a preview: ``preview.jpg`` is the latest; a copy ``preview-NNN.jpg`` (the next number in
    the folder) is the file the answer names. A run that drew the same pixels as the newest numbered copy reuses it
    (a repeat writes nothing new)."""
    from ..core.paths import atomic_write

    data = latest.read_bytes()
    stem, ext = latest.stem, latest.suffix
    nums = []
    for f in latest.parent.glob(f"{stem}-[0-9][0-9][0-9]{ext}"):
        try:
            nums.append(int(f.stem.rsplit("-", 1)[1]))
        except ValueError:
            continue
    if nums:
        newest = latest.parent / f"{stem}-{max(nums):03d}{ext}"
        if newest.read_bytes() == data:
            return jpath(newest)
    target = latest.parent / f"{stem}-{(max(nums) if nums else 0) + 1:03d}{ext}"
    atomic_write(target, data)
    return jpath(target)


def _ref_spec(ref: str, view: str, flip: bool, box) -> dict:
    """The ``ref`` entry of a preview spec: the photo, its view and the object's pixel box (found when not given)."""
    from ..core.errors import require_module

    path = Path(ref).expanduser()
    if not path.is_file():
        raise SatkError("NOT_FOUND", f"no reference photo {jpath(path.absolute())}", hint="satk ref import <photo> --view side")
    if box is not None:
        b = [int(v) for v in box]
        if len(b) != 4 or b[2] <= b[0] or b[3] <= b[1]:
            raise SatkError("BAD_PARAMS", "ref_box takes x0,y0,x1,y1 pixels with x1 > x0 and y1 > y0")
    else:
        Image = require_module("PIL.Image", pip="Pillow", purpose="--ref")
        np = require_module("numpy", purpose="--ref")
        from . import silhouette as S

        with Image.open(path) as im:
            rgb = np.asarray(im.convert("RGB"))
        found = S.photo_box(rgb)
        b = list(found) if found else None
    return {"image": str(path.absolute()), "view": view, "flip": bool(flip), "box": b, "alpha": 0.3}


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
