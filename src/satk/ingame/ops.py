"""Operations of satk.ingame: ``satk ingame start|reload|status|stop|spawn|drive|check|shot|logs|spots|play``.

All are ``mcp=False`` (agents reach them through ``satk_ops``/``satk_op``); ``ingame play`` starts the MTA
client and is CLI only with consent. Module-level imports are stdlib and satk only; the work lives in the
sibling modules.
"""

from __future__ import annotations

import os
import re
import time as _time
from typing import Any, Literal

from ..core.envelope import obj, table
from ..core.errors import SatkError
from ..core.registry import op, report_progress
from ..mcp.generic import cli_only

Kind = Literal["auto", "vehicle", "object", "ped", "weapon"]
Camera = Literal["chase", "front", "rear", "side", "three_quarter", "rear_quarter", "top", "wheel"]


def _time_ok(t: str | None) -> str | None:
    if t is None:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d\d)", str(t).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise SatkError("BAD_PARAMS", f"time must be HH:MM, got {t!r}")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _weather_ok(w: int | None) -> int | None:
    if w is not None and not 0 <= int(w) <= 255:
        raise SatkError("BAD_PARAMS", "weather must be 0..255")
    return w


def _bridge(need_client: bool = False):
    from ..viewer.backends import mta_lua
    from . import session as S

    st = mta_lua.status(probe=False)
    if not st.get("up"):
        raise SatkError("NOT_READY", "the in-game test server is not running", hint="satk ingame start")
    b = S.Bridge()
    if need_client and not b.client_joined():
        port = st.get("server_port") or S.load_state().get("port") or S.PORT
        raise SatkError("NOT_READY", "no MTA client has joined the test server",
                        hint=f"satk ingame play (or in a running MTA: F8, connect 127.0.0.1 {port})")
    return b


def _model_rows(specs) -> list[dict[str, Any]]:
    out = []
    for s in specs:
        d: dict[str, Any] = {"key": s.key, "kind": s.kind, "mode": s.mode, "base": f"{s.base} {s.base_name}".strip(),
                             "files": sorted(s.files)}
        if not s.ref:
            d["ref"] = False
        if s.handling:
            d["handling"] = "from the mod"
        if s.weapon is not None:
            d["weapon"] = s.weapon
        if s.notes:
            d["notes"] = s.notes
        out.append(d)
    return out


def _scan_from(st: dict[str, Any]):
    from . import modset as MS

    inputs = st.get("inputs") or []
    if not inputs:
        return [], []
    return MS.scan(inputs, kind=st.get("kind") or "auto", replace=st.get("replace"), new=bool(st.get("new")),
                   base=st.get("base"), label=st.get("name"), profile=st.get("profile") or "vanilla")


def _players(applied: dict[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for p in (applied or {}).get("players") or []:
        if not isinstance(p, dict):
            continue
        models = p.get("models") if isinstance(p.get("models"), dict) else {}
        bad = {k: v.get("err") for k, v in models.items() if isinstance(v, dict) and not v.get("ok")}
        d = {"name": p.get("name"), "rev": p.get("rev"), "apply_ms": p.get("ms"),
             "restart_to_loaded_ms": p.get("total_ms"), "failed": bad or None,
             "engineRequestModel": p.get("request_model")}
        out.append({k: v for k, v in d.items() if v is not None})
    return out


@op("ingame.start", mcp=False, group="view", long_running=True,
    summary="Start (or refresh) the loopback MTA test server with satk-agent and satk-testdrive and load a mod "
            "(DFF/TXD/COL + data lines; replace a vanilla id or engineRequestModel a new one). Tells whether the "
            "client setup is done and how to join.",
    summary_ru="Запустить (обновить) локальный тестовый сервер MTA с satk-agent и satk-testdrive и загрузить мод "
               "(DFF/TXD/COL + строки данных); сообщает, готов ли клиент и как подключиться.",
    examples=("satk ingame start --mod <mod folder>", "satk ingame start --mod premier.dff premier.txd --new",
              "satk ingame start --mod mycar --kind vehicle --replace model:411", "satk ingame start --clear"))
def ingame_start(mod: list[str] | None = None, kind: Kind = "auto", replace: str | None = None, new: bool = False,
                 base: str | None = None, name: str | None = None, spot: str = "grove", port: int = 22040,
                 httpport: int = 22045, clear: bool = False, timeout: float = 90.0, profile: str = "vanilla") -> dict:
    """Build the test resources, start or reuse the server, apply the models in a connected client.

    Args:
        mod: mod folder(s) or files (.dff .txd .col and data files); without it the previous set is reloaded.
        kind: vehicle, object, ped or weapon (auto: from the IDE line or the vanilla model of the same name).
        replace: vanilla model the files replace (SID, id or name); also the reference of the checks.
        new: load as a new model id (engineRequestModel) next to the vanilla base (see --base).
        base: vanilla parent of a new model (default: the model of the same name, else a per-kind default).
        name: label of the model set in the game.
        spot: where players spawn (satk ingame spots).
        port: game port (UDP, 127.0.0.1 only); a free one is taken when it is busy.
        httpport: HTTP port of the server (TCP, 127.0.0.1 only).
        clear: forget the previous mod set (vanilla only).
        timeout: seconds to wait for the server and the resources.
        profile: index profile for vanilla names and handling.
    """
    from . import modset as MS
    from . import resource as R
    from . import session as S
    from . import spots as SP

    SP.spot(spot)
    marks = S.log_marks()  # 'ingame logs --since start' reads what is written from here on
    st = {} if clear else S.load_state()
    if mod:
        st = {"inputs": [os.path.abspath(m) for m in mod], "kind": kind, "replace": replace, "new": new, "base": base,
              "name": name, "profile": profile}
    elif clear:
        st = {"inputs": [], "profile": profile}
    elif any(v not in (None, False, "auto") for v in (replace, new, base, name)) or kind != "auto":
        st.update({k: v for k, v in (("kind", kind), ("replace", replace), ("new", new), ("base", base),
                                      ("name", name)) if v not in (None, False, "auto")})
    specs, warn = _scan_from(st)
    label = st.get("name") or (specs[0].label if specs else "vanilla")
    report_progress(1, 5, "resources")
    res_dir = S.resources_dir()
    logic = R.install_logic(res_dir)
    content = R.build_content(res_dir, specs, label=label, defaults={"spot": spot, "time": "12:00", "weather": 0})
    report_progress(2, 5, "server")
    up, w2 = S.ensure_server(port, httpport, timeout)
    warn += w2
    reused = bool(up.get("reused"))
    bridge = S.Bridge()
    started = S.start_resources(bridge, logic_changed=reused and bool(logic["changed"]),
                                content_changed=reused and bool(content["changed"] or content["removed"]),
                                timeout=min(timeout, 30.0))
    report_progress(3, 5, "client")
    applied = S.wait_applied(bridge, content["rev"], timeout=min(timeout, 30.0))
    pre = S.preflight()
    sport = int(up.get("server_port") or port)
    scripts = S.write_scripts(sport)
    st.update({"rev": content["rev"], "logic": logic["sha"], "port": sport, "spot": spot,
               "marks": marks, "models": [s.key for s in specs]})
    S.save_state(st)
    report_progress(4, 5, "done")
    joined = not applied.get("no_client")
    client: dict[str, Any] = {"setup_done": pre["setup_done"], "game_running": pre["game_running"], "joined": joined}
    if joined:
        client["players"] = _players(applied)
        if applied.get("timeout"):
            warn.append("TIMEOUT: the client did not report the new models in time (satk ingame status)")
    nxt = []
    if not pre["setup_done"]:
        nxt.append(f"once, as administrator: {scripts['admin_setup']} (undo: {scripts['admin_rollback']})")
    if not joined:
        nxt.append(f"join: satk ingame play  (or {scripts['play']}; in a running MTA: F8, connect 127.0.0.1 {sport})")
    kinds = sorted({s.kind for s in specs})
    if kinds:
        nxt += ["satk ingame drive" if "vehicle" in kinds else f"satk ingame spawn --kind {kinds[0]}",
                f"satk ingame check --suite {kinds[0]}", "satk ingame reload  (after editing the files)"]
    return obj(
        server={"up": True, "reused": reused, "pid": up.get("pid"), "connect": f"mtasa://127.0.0.1:{sport}",
                "resources": started},
        rev=content["rev"], label=label, models=_model_rows(specs),
        changed=(content["changed"] + content["removed"] + [f"{R.LOGIC}/{c}" for c in logic["changed"]]) or None,
        client=client, play=S.play_info(sport, scripts), next=nxt, warn=warn or None)


@op("ingame.reload", mcp=False, group="view", long_running=True,
    summary="Hot reload: re-read the mod files of 'ingame start', rewrite what changed, restart the content resource "
            "and re-apply the models in the connected client without reconnecting; reports the files and the time.",
    summary_ru="Горячая перезагрузка: перечитать файлы мода, перезапустить ресурс и применить модели в "
               "подключённом клиенте без переподключения; время и изменённые файлы.",
    examples=("satk ingame reload", "satk ingame reload --force"))
def ingame_reload(force: bool = False, timeout: float = 60.0) -> dict:
    """Detect changed files and re-apply them in the running game.

    Args:
        force: restart and re-apply even when no file changed.
        timeout: seconds to wait for the client to report the new models.
    """
    from . import resource as R
    from . import session as S

    st = S.load_state()
    if not st:
        raise SatkError("NOT_READY", "no in-game session yet", hint="satk ingame start --mod <folder>")
    t0 = _time.monotonic()
    specs, warn = _scan_from(st)
    res_dir = S.resources_dir()
    logic = R.install_logic(res_dir)
    content = R.build_content(res_dir, specs, label=st.get("name") or (specs[0].label if specs else "vanilla"),
                              defaults={"spot": st.get("spot") or "grove", "time": "12:00", "weather": 0})
    changed = content["changed"] + content["removed"] + [f"{R.LOGIC}/{c}" for c in logic["changed"]]
    old = st.get("rev")
    if not changed and not force:
        return obj(changed=None, rev=content["rev"], note="nothing changed", seconds=round(_time.monotonic() - t0, 2))
    bridge = _bridge()
    started = S.start_resources(bridge, logic_changed=bool(logic["changed"]),
                                content_changed=bool(content["changed"] or content["removed"]) or force)
    applied = S.wait_applied(bridge, content["rev"], timeout=timeout)
    st.update({"rev": content["rev"], "logic": logic["sha"], "models": [s.key for s in specs]})
    S.save_state(st)
    if applied.get("no_client"):
        warn.append("NOT_READY: no client has joined; the models apply when one joins")
    if applied.get("timeout"):
        warn.append("TIMEOUT: the client did not report the new models in time")
    return obj(changed=changed, rev=content["rev"], old_rev=old, resources=started, players=_players(applied) or None,
               seconds=round(_time.monotonic() - t0, 2), warn=warn or None)


@op("ingame.status", mcp=False, group="view",
    summary="In-game loop state: server, loaded model set and what each client applied (rev, failures), a running "
            "check, and the client preflight (admin setup done, game already running).",
    summary_ru="Состояние игрового цикла: сервер, набор моделей и что применил клиент, идущая проверка, "
               "готовность клиента (настройка администратора, запущенная игра).",
    examples=("satk ingame status",))
def ingame_status() -> dict:
    """Read-only overview (asks the running server and client when they are up)."""
    from ..viewer.backends import mta_lua
    from . import session as S

    st = S.load_state()
    srv = mta_lua.status(probe=True)
    out: dict[str, Any] = {"server": {"up": bool(srv.get("up"))}, "rev": st.get("rev"), "models": st.get("models")}
    warn: list[str] = []
    if srv.get("up"):
        out["server"].update(connect=f"mtasa://127.0.0.1:{srv.get('server_port')}", pid=srv.get("pid"),
                             client=bool(srv.get("client")))
        try:
            b = S.Bridge()
            td = b.server("status", {})
            out["server"]["content"] = td.get("content")
            out["server"]["game_rev"] = td.get("rev")
            out["players"] = _players({"players": td.get("players")}) or None
            if srv.get("client"):
                c = b.client("status", {})
                out["client"] = {"applied": c.get("applied"), "job": c.get("job"),
                                 "models": [m for m in (c.get("models") or []) if isinstance(m, dict)]}
        except SatkError as e:
            warn.append(f"{e.code}: {e.msg}")
    pre = S.preflight()
    out["preflight"] = pre
    if not pre["setup_done"]:
        warn.append("NOT_READY: the one-time MTA client setup is missing: satk ingame start writes admin-setup.ps1")
    return obj(warn=warn or None, **out)


@op("ingame.stop", mcp=False, group="view",
    summary="Stop the in-game test server (the same server as 'view --target game'); the client a launcher "
            "started is closed too.",
    summary_ru="Остановить тестовый сервер (тот же, что у view --target game); запущенный клиент закрывается.",
    examples=("satk ingame stop",))
def ingame_stop() -> dict:
    """Shut the loopback server down."""
    from ..viewer.backends import mta_lua

    return obj(**mta_lua.stop())


def _spawn(model: str, kind: str, at: str | None, heading: float | None, camera: str, time: str | None,
           weather: int | None, drive: bool) -> dict:
    from . import modset as MS
    from . import session as S
    from . import spots as SP

    b = _bridge(need_client=drive or kind == "weapon")
    args: dict[str, Any] = {"camera": camera, "drive": drive, **SP.parse_at(at)}
    if heading is not None:
        args["h"] = float(heading)
    if _time_ok(time):
        args["time"] = _time_ok(time)
    if _weather_ok(weather) is not None:
        args["weather"] = int(weather)
    manifest = b.server("manifest", {})
    keys = {m.get("key") for m in (manifest.get("models") or []) if isinstance(m, dict)}
    if model == "mod" or model in keys:
        args["model"] = model
        if kind != "auto":
            args["kind"] = kind
        if model == "mod" and not keys:
            raise SatkError("NOT_FOUND", "no mod model is loaded", hint="satk ingame start --mod <folder>, or name a "
                            "vanilla model: --model model:411")
    else:
        v = MS.Vanilla(S.load_state().get("profile") or "vanilla").resolve(model)
        k = MS.SEC_KIND.get(str(v["sec"]).lower(), "object") if kind == "auto" else kind
        args.update(model=int(v["id"]), kind=k)
        if k == "weapon":
            wid = MS.Vanilla().weapon_id(int(v["id"]))
            if wid is None:
                raise SatkError("NOT_FOUND", f"model {v['id']} is not a weapon of data/weapon.dat")
            args["weapon"] = wid
    r = b.server("spawn", args)
    return obj(**{k: v for k, v in r.items() if k != "error"}, camera=camera)


@op("ingame.spawn", mcp=False, group="view",
    summary="Spawn the mod model (or a vanilla one) in the game: vehicle, object, ped or weapon (given to the "
            "player) at a test spot (grove, runway, wall, ramp, pad, night, ...) or x,y,z, with a camera preset, "
            "time and weather.",
    summary_ru="Создать модель мода (или ванильную) в игре: транспорт, объект, педа или оружие у тестовой точки "
               "или x,y,z, с пресетом камеры, временем и погодой.",
    examples=("satk ingame spawn", "satk ingame spawn --at pad --camera three_quarter",
              "satk ingame spawn --model model:411 --at wall", "satk ingame spawn --kind ped --at grove --time 22:00"))
def ingame_spawn(model: str = "mod", kind: Kind = "auto", at: str = "here", heading: float | None = None,
                 camera: Camera = "three_quarter", time: str | None = None, weather: int | None = None,
                 drive: bool = False) -> dict:
    """Create a test element through satk-testdrive (server side; removed by the next 'ingame start').

    Args:
        model: "mod" (the first loaded model of the kind), a manifest key, or a vanilla SID/id/name.
        kind: vehicle, object, ped or weapon (auto: from the model).
        at: "here" (in front of the player), a spot name (satk ingame spots) or "x,y,z".
        heading: GTA heading in degrees (0 = north, 90 = west).
        camera: camera preset around the new element (chase = the game camera).
        time: game time HH:MM.
        weather: weather id 0..255.
        drive: put the player into the vehicle.
    """
    return _spawn(model, kind, at, heading, camera, time, weather, drive)


@op("ingame.drive", mcp=False, group="view",
    summary="Put the player into the mod vehicle (or a vanilla one) at a test spot: runway (speed runs), wall "
            "(crash), ramp, grove, night, ...; the game's chase camera.",
    summary_ru="Посадить игрока в транспорт мода (или ванильный) на тестовой точке: полоса, стена, трамплин, ночь.",
    examples=("satk ingame drive", "satk ingame drive --at wall", "satk ingame drive --model model:411 --at ramp"))
def ingame_drive(model: str = "mod", at: str = "runway", camera: Camera = "chase", time: str | None = None,
                 weather: int | None = None) -> dict:
    """Spawn a vehicle and warp the player into it.

    Args:
        model: "mod", a manifest key, or a vanilla SID/id/name.
        at: spot name or "x,y,z" ("here" = where the player is).
        camera: camera preset (chase = the game camera behind the car).
        time: game time HH:MM.
        weather: weather id 0..255.
    """
    return _spawn(model, "vehicle", at, None, camera, time, weather, True)


@op("ingame.check", mcp=False, group="view", long_running=True,
    summary="Scripted behaviour checks in the real game, mod vs the vanilla model under the same conditions, one "
            "frame per check and a verdict: vehicle (wheels, suspension, speed, crash, damage, lights, dirt, LOD), "
            "object (collisions, LOD, night), ped (animations, bones), weapon (held, fire).",
    summary_ru="Скриптовые проверки поведения в настоящей игре: мод против ванильной модели, кадр на проверку и "
               "вердикт (транспорт, объект, пед, оружие).",
    examples=("satk ingame check", "satk ingame check --suite vehicle --only rest speed",
              "satk ingame check --suite object --no-reference"))
def ingame_check(suite: Literal["vehicle", "object", "ped", "weapon"] | None = None, only: list[str] | None = None,
                 model: str = "mod", reference: bool = True, width: int = 960, height: int = 540,
                 timeout: float = 900.0) -> dict:
    """Run a check suite against a loaded model; frames and report.json go to work/out/ingame/<model>/<suite>/.

    Args:
        suite: vehicle, object, ped or weapon (default: the kind of the model).
        only: run only these checks of the suite (satk ingame suites lists them).
        model: "mod" (the first model of the suite's kind) or a manifest key.
        reference: run the vanilla model next to it (or in a second pass) for comparison.
        width: frame width.
        height: frame height.
        timeout: seconds for the whole suite.
    """
    from ..core import paths
    from . import checks as CH

    b = _bridge(need_client=True)
    manifest = b.server("manifest", {})
    models = [m for m in (manifest.get("models") or []) if isinstance(m, dict)]
    spec = None
    for m in models:
        if (model != "mod" and m.get("key") == model) or (model == "mod" and (suite is None or m.get("kind") == suite)):
            spec = m
            break
    if spec is None:
        raise SatkError("NOT_FOUND", f"no loaded model {model!r}" + (f" of kind {suite}" if suite else ""),
                        hint="satk ingame start --mod <folder>",
                        did_you_mean=[str(m.get("key")) for m in models][:5])
    kind = str(spec.get("kind"))
    if suite and suite != kind:
        raise SatkError("BAD_PARAMS", f"model {spec.get('key')} is a {kind}, not a {suite}")
    names = list(CH.SUITES[kind])
    if only:
        bad = [c for c in only if c not in names]
        if bad:
            raise SatkError("BAD_PARAMS", f"unknown {kind} checks: {', '.join(bad)}", data={"checks": names})
        names = [c for c in names if c in only]
    c = b.client("status", {})
    loaded = {m.get("key"): m for m in (c.get("models") or []) if isinstance(m, dict)}
    lm = loaded.get(spec.get("key")) or {}
    if not lm.get("ok"):
        raise SatkError("NOT_READY", f"the client has not loaded {spec.get('key')}: {lm.get('err') or 'not applied'}",
                        hint="satk ingame status; satk ingame logs --source client")
    if isinstance(c.get("job"), dict):
        raise SatkError("BUSY", f"a check is running in the game: {c['job'].get('check')}")
    out_dir = paths.work("out", "ingame", str(spec.get("key")), kind)
    res = CH.run_suite(b, spec, kind=kind, checks=names, reference=reference, out_dir=out_dir, w=width, h=height,
                       timeout=timeout, progress=lambda msg: report_progress(0, None, msg))
    env = table(["check", "verdict", "value", "ref", "expect", "note", "frame"], res["rows"], warn=res["warn"])
    env.update(model=f"{spec.get('key')} ({kind}, {spec.get('mode')}, base {spec.get('base')})",
               counts=res["counts"], report=res["report"])
    return env


@op("ingame.shot", mcp=False, group="view",
    summary="Screenshot of the running game through satk-agent with a camera preset around the player's vehicle, "
            "the last spawned test element or an element id; PNG under work/out/ingame/shots.",
    summary_ru="Снимок игры через satk-agent с пресетом камеры вокруг машины игрока или последнего тестового "
               "элемента; PNG в work/out/ingame/shots.",
    examples=("satk ingame shot", "satk ingame shot --camera side --name side", "satk ingame shot --camera chase"))
def ingame_shot(camera: Literal["chase", "front", "rear", "side", "three_quarter", "rear_quarter", "top", "wheel",
                                "current"] = "three_quarter", target: str = "auto", width: int = 1280, height: int = 720,
                name: str | None = None, time: str | None = None, weather: int | None = None) -> dict:
    """Take one frame (HUD hidden; the camera is restored afterwards).

    Args:
        camera: preset (chase/current = the camera as it is).
        target: auto (the player's vehicle, else the last spawned element, else the player), player, or an
            element id from 'ingame spawn'.
        width: frame width.
        height: frame height.
        name: file name (default: <camera>-<target>).
        time: game time HH:MM for this frame only.
        weather: weather id for this frame only.
    """
    from ..core import paths

    b = _bridge(need_client=True)
    pose = None
    if camera not in ("chase", "current"):
        p = b.client("pose", {"camera": camera, "target": target})
        pose = {"pos": p["pos"], "look": p["look"], "fov_h_deg": p.get("fov_h_deg") or 60}
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or f"{camera}-{target}").strip("._") or "shot"
    prefix = paths.work("out", "ingame", "shots") / stem
    params: dict[str, Any] = {"path_prefix": str(prefix).replace("\\", "/"), "w": int(width), "h": int(height)}
    if pose:
        params["pose"] = pose
    env = {}
    if _time_ok(time):
        env["time"] = _time_ok(time)
    if _weather_ok(weather) is not None:
        env["weather"] = int(weather)
    if env:
        params["env"] = env
    r = b.b.call("capture", params)
    return obj(file=(r.get("files") or {}).get("color"), w=r.get("w"), h=r.get("h"), pose=r.get("pose"))


@op("ingame.logs", mcp=False, group="view",
    summary="Log rows of the in-game loop: the agent's ring (server + client debugscript), server console and "
            "script logs, the client's clientscript/console/engine logs; filter by level, text and 'since start'.",
    summary_ru="Строки логов игрового цикла: агент (debugscript сервера и клиента), логи сервера и клиента; "
               "фильтр по уровню, тексту и 'с момента start'.",
    examples=("satk ingame logs", "satk ingame logs --level warn --since start", "satk ingame logs --source client"))
def ingame_logs(source: Literal["all", "agent", "server", "scripts", "client"] = "all",
                level: Literal["debug", "info", "warn", "error"] = "info", since: Literal["all", "start"] = "all",
                grep: str | None = None, limit: int = 50) -> dict:
    """Read the logs (never writes).

    Args:
        source: which logs.
        level: lowest level to show.
        since: all (the tail of each file) or start (written after the last 'ingame start').
        grep: regular expression on the message or its file:line.
        limit: rows to return (the newest).
    """
    from ..viewer.backends import mta_lua
    from . import logs as L
    from . import session as S

    sources = {"agent", "server", "scripts", "client"} if source == "all" else {source}
    bridge = None
    if "agent" in sources and mta_lua.status(probe=False).get("up"):
        try:
            bridge = S.Bridge()
        except SatkError:
            bridge = None
    marks = S.load_state().get("marks") if since == "start" else None
    try:
        rows, warn = L.collect(sources=sources, level=level, since=marks, grep=grep, limit=max(1, min(limit, 500)),
                               bridge=bridge)
    except re.error as e:
        raise SatkError("BAD_PARAMS", f"grep: {e}") from None
    return table(["t", "src", "level", "msg", "where"], rows, warn=warn)


@op("ingame.spots", mcp=False, group="view",
    summary="Test spots (grove, runway, wall, ramp, pad, night, ...), the check areas on the LS airport runways "
            "and the camera presets of the in-game loop.",
    summary_ru="Тестовые точки, зоны проверок на полосах аэропорта ЛС и пресеты камеры игрового цикла.",
    examples=("satk ingame spots",))
def ingame_spots() -> dict:
    """Static data; no server needed."""
    from . import spots as SP

    return table(["name", "kind", "pos", "h", "note"], SP.rows())


@op("ingame.suites", mcp=False, group="view",
    summary="The behaviour checks of 'ingame check' per suite (vehicle, object, ped, weapon) and what each shows.",
    summary_ru="Проверки 'ingame check' по наборам (транспорт, объект, пед, оружие) и что каждая показывает.",
    examples=("satk ingame suites",))
def ingame_suites() -> dict:
    """Static data; no server needed."""
    from . import checks as CH

    return table(["suite", "check", "shows"], [[k, c, CH.ABOUT.get((k, c), "")] for k, cs in CH.SUITES.items()
                                                for c in cs])


@cli_only("it starts the MTA client (the loader writes HKLM/ProgramData); the user runs it", consent=True)
@op("ingame.play", mcp=False, group="view", long_running=True,
    summary="Start the MTA fork client and join the in-game test server (for the user; needs the one-time "
            "admin-setup.ps1 that 'ingame start' writes).",
    summary_ru="Запустить клиент форка MTA и подключиться к тестовому серверу (для пользователя; нужен "
               "admin-setup.ps1 один раз).",
    examples=("satk ingame play",))
def ingame_play(timeout: float = 180.0) -> dict:
    """Preflight, then the client against mtasa://127.0.0.1:<port>; waits until satk-agent says hello.

    Args:
        timeout: seconds to wait for the client to join.
    """
    from ..viewer.backends import mta_lua
    from . import session as S

    srv = mta_lua.status(probe=False)
    if not srv.get("up"):
        raise SatkError("NOT_READY", "the in-game test server is not running", hint="satk ingame start")
    port = srv.get("server_port")
    pre = S.preflight()
    scripts = S.write_scripts(int(port or S.PORT))
    if not pre["setup_done"]:
        raise SatkError("NOT_READY", "the MTA client needs the one-time setup by an administrator",
                        hint=f"run {scripts['admin_setup']} in PowerShell started as administrator",
                        data={"blockers": pre.get("blockers")})
    if pre["game_running"]:
        raise SatkError("NOT_READY", "GTA:SA (or MTA) is already running",
                        hint=f"in the running MTA press F8 and type: connect 127.0.0.1 {port}")
    return obj(**mta_lua.start_client(timeout=timeout))
