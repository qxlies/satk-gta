"""Operations of satk.studio: the live Blender authoring session (CLI ``satk blender session|call|methods``),
asset projects (``satk asset init|status``) and reference photos (``satk ref import``).

All are ``mcp=False``: MCP clients reach them through ``satk_ops``/``satk_op``. Module-level imports are
stdlib only; Blender runs as a separate process.
"""

from __future__ import annotations

from typing import Any, Literal

from ..core.envelope import obj, table
from ..core.errors import SatkError
from ..core.registry import op

Stats = Literal["auto", "none", "scene"]


@op("blender.session",
    summary="Live Blender modelling session for agents (headless, isolated profile): start (optionally for an "
            "asset project, resuming its newest checkpoint), status, list, stop, restore a checkpoint, replay a "
            "journal, prune old checkpoints and dead sessions. Steps run through blender.call.",
    summary_ru="Живая сессия Blender для пошагового моделирования: start (с проектом ассета и продолжением с "
               "последнего чекпойнта), status, list, stop, restore, replay журнала, prune.",
    mcp=False,
    examples=("satk blender session start", "satk blender session start --project mycar",
              "satk blender session status", "satk blender session restore --ref G1",
              "satk blender session replay --source mycar --name mycar-replay", "satk blender session stop"))
def blender_session(action: Literal["start", "status", "list", "stop", "restore", "replay", "prune"],
                    name: str | None = None, project: str | None = None, blend: str | None = None, gui: bool = False,
                    threads: int | None = None, resume: bool = True, idle: float = 3600.0, wait: float = 90.0,
                    ref: str | None = None, source: str | None = None, keep: int | None = None) -> dict:
    """Start, inspect, stop, restore, replay or prune live Blender studio sessions.

    Args:
        action: start (reuses a running session), status, list, stop, restore (load a checkpoint into the running
            session), replay (rebuild a journal in a session), prune (dead sessions; with name: old checkpoints).
        name: session name (a-z, 0-9, '-', '_'); default 'default', or the project name with project.
        project: asset project (name or folder from asset.init): its journal, checkpoints, bands and target size.
        blend: start: a .blend file to open.
        gui: start: open a visible Blender window instead of a headless one.
        threads: start: Blender threads (default all cores; SATK_BLENDER_THREADS).
        resume: start with project: open the newest project checkpoint (default true).
        idle: start: the session ends after this many seconds without requests.
        wait: start: seconds to wait for Blender to come up.
        ref: restore: checkpoint step number, tag (G1) or 'last'; replay: start from this checkpoint.
        source: replay: journal file, project or session name to replay.
        keep: prune: untagged checkpoints to keep (default 10).
    """
    from . import launcher as L

    if action == "start":
        return obj(None, **L.start(name or L.DEFAULT_NAME, blend=blend, gui=gui, idle=idle, wait=wait,
                                   project=project, threads=threads, resume=resume))
    if action == "list":
        rows = [[s["name"], s["up"], s.get("pid"), s.get("port"), s.get("started_at")] for s in L.list_sessions()]
        return table(["name", "up", "pid", "port", "started_at"], rows)
    if action == "prune":
        return obj(None, **L.prune(_name(name, project), keep=keep))
    sname = _name(name, project) or L.DEFAULT_NAME
    if action == "status":
        return obj(None, **L.status(sname))
    if action == "stop":
        return obj(None, **L.stop(sname))
    if action == "restore":
        from . import api

        res = api.call("session.restore", {"ref": _ref(ref)}, session=sname, stats="scene")
        return obj(None, **res)
    if not source:
        raise SatkError("BAD_PARAMS", "replay needs --source (a journal file, project or session name)")
    from . import replay

    st = L.status(sname, probe=False)
    if not st.get("up"):
        L.start(sname)
    return obj(None, **replay.run(source, session=sname, start=_ref(ref) if ref is not None else None))


def _name(name: str | None, project: str | None) -> str | None:
    if name:
        return name
    if project:
        from . import project as P

        return P.project_dir(project).name
    return None


def _ref(ref: str | None) -> Any:
    if ref is None:
        return "last"
    return int(ref) if str(ref).isdigit() else ref


@op("blender.call",
    summary="Run one studio method in Blender (scene, mesh, modifier, material, uv, camera, ref, io, shade, python, "
            "batch, session.* and kit/look plug-ins): in the running session (ms) or a one-shot run (s). Returns "
            "the result, changed objects, tris/shading/size stats and an optional JPEG snapshot path.",
    summary_ru="Выполнить метод студии в Blender (scene, mesh, modifier, material, uv, camera, ref, io, python, "
               "batch, session.*, плагины kit/look): результат, статистика, снимок.",
    mcp=False,
    examples=("satk blender call scene.info",
              "satk blender call mesh.primitive --params '{\"kind\":\"cube\",\"size\":2}' --snapshot 3q",
              "satk blender call modifier.add --params '{\"object\":\"body\",\"type\":\"MIRROR\"}'",
              "satk blender call batch --params @steps.json --snapshot sheet"))
def blender_call(method: str, params: dict[str, Any] | None = None, session: str | None = None,
                 blend: str | None = None, save: str | None = None, snapshot: str | None = None, size: int = 512,
                 look: str | None = None, stats: Stats = "auto", checkpoint: bool | None = None,
                 timeout: float = 90.0) -> dict:
    """One step of a Blender modelling session.

    Args:
        method: studio method name (satk blender methods lists them; 'batch' runs params.steps).
        params: the method's parameters as a JSON object (or @file.json).
        session: session name; omitted = 'default' when it runs, else a one-shot cold Blender run.
        blend: a .blend to open before the step.
        save: save the scene to this .blend after the step (under the work directory).
        snapshot: JPEG after the step: a view (3q front rear left right top), a camera name, 'sheet' (2x2) or JSON.
        size: snapshot size in pixels (64-1024).
        look: snapshot look: clay (default, with wire), raw, wire or game.
        stats: auto (changed objects after a mutating step), none, or scene (totals).
        checkpoint: --checkpoint saves a .blend checkpoint after the step, --no-checkpoint never; default: every
            5th mutating step, python steps, and once after a batch of 2+ mutating steps.
        timeout: the step's time budget in seconds: the session stops a method that runs longer and answers
            TIMEOUT (the session stays usable).
    """
    from . import api

    snap: Any = snapshot
    if snapshot is not None and look:
        snap = api.snapshot_spec(snapshot, None)
        snap["look"] = look
    res = api.call(method, params, session=session, blend=blend, save=save, snapshot=snap,
                   size=size if snapshot else None, stats=stats, checkpoint=checkpoint, timeout=timeout)
    return obj(None, **res)


@op("blender.methods",
    summary="List the studio methods a Blender session serves (scene, mesh, modifier, material, uv, camera, ref, "
            "io, shade, python, session and kit/look plug-ins), filtered by a word; the exact name of one method "
            "returns its parameter table (defaults, required, allowed values) and full description.",
    summary_ru="Список методов студии Blender с фильтром по слову; точное имя метода даёт его полное описание.",
    mcp=False, examples=("satk blender methods", "satk blender methods --query mesh",
                         "satk blender methods --query mesh.loft"))
def blender_methods(query: str | None = None, session: str | None = None, limit: int = 100,
                    timeout: float = 60.0) -> dict:
    """Studio method discovery.

    Args:
        query: a word to filter names and descriptions; an exact method name (mesh.lathe) gives its parameter
            table and full help.
        session: session name; omitted = 'default' when it runs, else a one-shot cold Blender run.
        limit: rows to return.
        timeout: seconds to wait for Blender.
    """
    from . import api

    res = api.methods(query, session=session, timeout=timeout)
    ms = res.get("methods") or []
    warn = [f"INTERNAL: plug-in {e}" for e in res.get("errors") or []]
    if res.get("help") is not None or res.get("params") is not None:  # an exact method name
        q = str(query or "").strip().lower()
        m = next((x for x in ms if x.get("name") == q), ms[0] if ms else {})
        env = table(["param", "default"], [list(r) for r in res.get("params") or []], warn=warn)
        env.update({"method": m.get("name") or q, "mode": "ro" if m.get("readonly") else "rw",
                    "help": res.get("help") or m.get("doc") or ""})
        if res.get("module"):
            env["module"] = res["module"]
        if res.get("params_error"):
            env.setdefault("warn", []).append(f"INTERNAL: parameter table: {res['params_error']}")
        env["note"] = "default 'required' = must be given; (a|b) = allowed values; object|objects = one name or a list"
        return env
    rows = [[m.get("name"), "ro" if m.get("readonly") else "rw", m.get("doc") or ""] for m in ms[: max(1, limit)]]
    env = table(["method", "mode", "doc"], rows, total=int(res.get("total") or len(ms)), warn=warn)
    if not query:
        env["reference"] = "docs/agent/studio-methods.md (every method with its parameters, one line each)"
    return env


@op("asset.init",
    summary="Start an asset project for authoring in Blender (any kind: vehicle, prop, building, interior, weapon, "
            "ped, pickup, upgrade): asset.json with kind, intent replace|add, like SID, target platform, tier "
            "(sa_plus default), target size from the like model, gates G0-G5.",
    summary_ru="Создать проект ассета (asset.json): тип, замена или добавление, образец SID, платформа, уровень "
               "детализации (по умолчанию sa_plus), размеры образца, этапы G0-G5.",
    mcp=False,
    examples=("satk asset init mycar --kind automobile --intent replace --like model:426",
              "satk asset init bench1 --kind prop --dims 1.8,0.6,0.9"))
def asset_init(dir: str, kind: str | None = None, intent: Literal["replace", "add"] = "add", like: str | None = None,
               target: Literal["sp", "mta", "samp"] = "sp", tier: Literal["vanilla", "sa_plus"] = "sa_plus",
               dims: list[float] | None = None, force: bool = False) -> dict:
    """Create an asset project (contract K7).

    Args:
        dir: project name (-> work/assets/<name>) or a folder under the work directory.
        kind: asset kind (automobile, bike, boat, plane, heli, prop, building, interior_shell, weapon, ped, pickup, ...).
        intent: replace a vanilla model (needs like) or add a new one.
        like: the vanilla model it replaces or resembles (model:426): class, bands and target size.
        target: platform: sp (single player, Mod Loader), mta or samp.
        tier: vanilla (blends into traffic) or sa_plus (more detail, the default for new assets).
        dims: target size in metres: width (x), length (y), height (z); overrides the like model.
        force: overwrite an existing asset.json.
    """
    from . import project as P

    if not kind:
        raise SatkError("BAD_PARAMS", "--kind is required", data={"kinds": list(P.kinds())},
                        hint="satk asset init <name> --kind automobile|prop|building|weapon|ped|...")
    return obj(None, **P.init(dir, kind=kind, intent=intent, like=like, target=target, tier=tier, dims=dims,
                              force=force))


@op("asset.status",
    summary="Resume card of an asset project (at most 1 KB): gates done/open, next gate, target size, journal steps, "
            "checkpoints and tags, decisions, open issues, last check/export, session state; --record stores a "
            "decision, gate state, issue or result; --sheet renders a 2x2 snapshot sheet.",
    summary_ru="Карточка проекта ассета (до 1 КБ): этапы, журнал, чекпойнты, решения, проблемы; --record "
               "записывает решение, этап или результат; --sheet делает лист снимков.",
    mcp=False,
    examples=("satk asset status mycar", "satk asset status mycar --record '{\"gate\":\"G1\",\"state\":\"done\"}'",
              "satk asset status mycar --sheet"))
def asset_status(dir: str, record: dict[str, Any] | None = None, sheet: bool = False) -> dict:
    """Resume card of an asset project.

    Args:
        dir: project name or folder.
        record: store {decision | gate+state | issue | close | check | export | checkpoint | dims} first.
        sheet: render a 2x2 snapshot sheet in the running session (else the newest snapshot is named).
    """
    from . import launcher as L
    from . import project as P

    pdir, data = P.load(dir)
    sname = data.get("session") or pdir.name
    rec_out = None
    if record:
        rec_out = P.record(dir, record)
        gate = str(record.get("gate") or "").upper()
        if gate and record.get("state", "done") == "done":
            rec_out["checkpoint"] = _gate_checkpoint(dir, sname, gate)
    try:
        st = L.status(sname, probe=False)
        sess = {"name": sname, "up": bool(st.get("up"))}
    except SatkError:
        sess = None
    card = P.status(dir, session=sess)
    if sheet:
        card["sheet"] = _sheet(sname, bool(sess and sess.get("up")))
    if rec_out:
        card["recorded"] = rec_out["recorded"]
        if rec_out.get("checkpoint"):
            card["gate_checkpoint"] = rec_out["checkpoint"]
    return obj(None, **card)


def _gate_checkpoint(project: str, session: str, gate: str) -> str | None:
    """A tagged checkpoint for a finished gate when the session runs (tagged = never pruned)."""
    from . import api
    from . import project as P

    try:
        res = api.call("session.checkpoint", {"tag": gate}, session=session, stats="none", timeout=60)
    except SatkError:
        return None
    path = (res.get("result") or {}).get("checkpoint")
    if path:
        P.record(project, {"checkpoint": {"n": int(res.get("n") or 0), "tag": gate}})
    return path


def _sheet(session: str, up: bool) -> str | None:
    if not up:
        return None
    from . import api

    res = api.call("scene.info", {"limit": 0}, session=session, stats="none",
                   snapshot={"views": ["3q", "front", "left", "top"], "size": 768}, timeout=120)
    return res.get("snapshot")


@op("ref.import",
    summary="Prepare a reference photo for modelling: EXIF rotation applied and metadata stripped, shrunk to at "
            "most 1600 px JPEG, plus a copy with a labelled pixel grid to read coordinates for ref.plane "
            "(points + real distance). Into the project's refs folder or work/refs.",
    summary_ru="Подготовить фото-референс: без EXIF, не больше 1600 px, JPEG и копия с сеткой пикселей для "
               "ref.plane.",
    mcp=False, examples=("satk ref import photo.jpg --project mycar",))
def ref_import(photo: str, project: str | None = None, max_px: int = 1600, grid: int = 100) -> dict:
    """Reference photo preparation.

    Args:
        photo: the photo file (jpg, png, webp, bmp, tif).
        project: asset project (its refs folder); omitted = work/refs.
        max_px: longest side in pixels (256-4096).
        grid: grid step of the coordinate copy in pixels.
    """
    from .refimg import import_photo

    return obj(None, **import_photo(photo, project=project, max_px=max_px, grid=grid))
