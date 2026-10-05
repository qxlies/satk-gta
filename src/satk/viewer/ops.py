"""Operations of satk.viewer (owner WP-07): ``satk view …`` and MCP ``view_*`` (SPEC §4.6, §4.7).

Also registers the SID providers for ``bm:`` (bookmarks) and ``cap:`` (captures) and the
``viewer`` status provider / doctor check. Heavy work lives in :mod:`satk.viewer.api`.
"""

from __future__ import annotations

from typing import Any, Literal

from ..core.envelope import obj, table
from ..core.errors import SatkError
from ..core.ids import Sid, register_provider
from ..core.registry import doctor_check, op, status_provider

Target = Literal["ariane", "game", "mock"]


# --------------------------------------------------------------------------- control


@op("view.control", summary="Start/stop/status of a viewer target: ariane (offline viewer), mock (test world), game.",
    summary_ru="Запустить, остановить или проверить цель вьювера (ariane, mock, game).",
    examples=("satk view control start --target ariane", "satk view control status --target mock"))
def view_control(action: Literal["status", "start", "stop"] | None, target: Target = "ariane",
                 profile: str = "vanilla", window: str | None = None) -> dict:
    """Control a viewer target.

    Args:
        action: default status.
        profile: game profile for start.
        window: start: WxH[+X+Y].
    """
    from . import api

    return api.control(action, target, profile, window)


@op("view.start", summary="Start a viewer target (alias of 'view control start').",
    summary_ru="Запустить цель вьювера (то же, что view control start).", mcp=False,
    examples=("satk view start --target ariane",))
def view_start(target: Target = "ariane", profile: str = "vanilla", window: str | None = None,
               wait: float | None = None) -> dict:
    """Start Ariane (or the mock endpoint) and wait until it answers.

    Args:
        target: ariane or mock.
        profile: game load profile for Ariane's --game-dir.
        window: WxH[+X+Y] window of Ariane.
        wait: seconds to wait for the first ping (default 90 for Ariane).
    """
    from . import api

    return api.control("start", target, profile, window, wait)


@op("view.stop", summary="Stop a viewer target (alias of 'view control stop').",
    summary_ru="Остановить цель вьювера.", mcp=False, examples=("satk view stop --target ariane",))
def view_stop(target: Target = "ariane") -> dict:
    """Send quit (or WM_CLOSE), wait 10 s, then terminate.

    Args:
        target: ariane or mock.
    """
    from . import api

    return api.control("stop", target)


@op("view.status", summary="Status of a viewer target (alias of 'view control status').",
    summary_ru="Состояние цели вьювера.", mcp=False, examples=("satk view status --json",))
def view_status(target: Target = "ariane") -> dict:
    """Is the target up, which protocol and capabilities does it have.

    Args:
        target: ariane, mock or game.
    """
    from . import api

    return api.control("status", target)


# --------------------------------------------------------------------------- camera / capture


@op("view.goto", summary="Move the camera to a SID (inst:/model: framed, bm:/cap: pose), to pos+look/ypr, or a bookmark.",
    summary_ru="Переместить камеру вьювера: к SID, к позиции или к закладке.",
    examples=("satk view goto --id inst:lae2_stream0#4 --target mock",
              "satk view goto --pos 2495,-1720,60 --look 2495,-1670,15"))
def view_goto(target: Target = "ariane", id: str | None = None, pos: list[float] | None = None,  # noqa: A002
              look: list[float] | None = None, ypr: list[float] | None = None, bm: str | None = None,
              fov: float = 70.0, dist: float | None = None) -> dict:
    """Camera to a SID, a position or a bookmark. SIDs resolve in the game profile the viewer runs.

    Args:
        id: SID to frame.
        ypr: yaw,pitch,roll deg (yaw 0 = north).
        fov: h-FOV deg.
        dist: framing distance m.
    """
    from . import api

    return api.goto(target, id, pos, look, ypr, bm, fov, dist)


@op("view.capture", summary="Capture a frame to PNG (path). marks=N: numbered objects + legend [n,sid,name,share]; "
                            "grid: A-H x 1-6 cells; compare_to: diff + SSIM.",
    summary_ru="Снять кадр вьювера (PNG + sidecar), с метками, сеткой и сравнением.",
    examples=("satk view capture --target mock --marks 4 --grid",
              "satk view capture --pos 2495,-1720,60 --look 2495,-1670,15"))
def view_capture(target: Target = "ariane", pose: dict | None = None, pos: list[float] | None = None,
                 look: list[float] | None = None, ypr: list[float] | None = None, fov: float | None = None,
                 bm: str | None = None, width: int = 960, height: int = 540, layers: list[str] | None = None,
                 marks: int = 0, grid: bool = False, settle: bool = True, env: dict | None = None,
                 compare_to: str | None = None, inline: bool = False) -> dict:
    """Capture a frame (stateless when a pose is given: the camera is restored after).

    Args:
        pose: {pos,look|ypr,fov_h_deg}
        ypr: yaw,pitch,roll deg.
        fov: h-FOV deg.
        layers: color, ids, depth.
        marks: number N objects.
        env: {time:"HH:MM", weather}.
        compare_to: cap: SID or PNG.
    """
    from . import api

    p = api.pose_from_args(pose, pos, look, ypr, fov)
    if p is None and fov is not None:
        raise SatkError("BAD_PARAMS", "--fov needs --pos or --pose")
    out = api.capture(target, p, bm, width, height, layers, marks, grid, settle, env, compare_to)
    if inline:
        out["inline"] = [out.get("marks_file") or out.get("grid_file") or out["file"]]
    return out


@op("view.pick", summary="What is at pixels [[px,py]] or grid cells ['D3'] of the window or a capture: "
                         "rows [px,py,id,model,name,x,y,z,dist,link].",
    summary_ru="Что находится в точках кадра: SID, модель, координаты.",
    examples=("satk view pick 400 300 --target mock", "satk view pick --cells D3,E4 --capture cap:20261004-153201-ab12"))
def view_pick(points: list[list[float]] | None, cells: list[str] | None = None, capture: str | None = None,
              target: Target = "ariane") -> dict:
    """Pick entities at pixels (what is drawn when the target can, else the collision ray).

    Args:
        points: [[px, py], ...] (CLI: PX PY ...).
        cells: grid cells, e.g. D3.
        capture: cap: SID the pixels refer to.
    """
    from . import api

    return api.pick(points, cells, capture, target)


@op("view.set", summary="Set time, weather, overlays (col,zones,paths,tcyc), lod, draw distance, hide/highlight "
                        "(inst: SIDs), postfx. UNSUPPORTED if the target lacks it.",
    summary_ru="Изменить вид: время, погода, оверлеи, LOD, скрыть/подсветить объекты.",
    examples=("satk view set --time 21:30 --weather 8 --target mock",))
def view_set(target: Target = "ariane", time: str | None = None, weather: int | None = None,
             overlays: list[str] | None = None, lod: Literal["normal", "hd", "lod"] | None = None,
             draw_dist: float | None = None, hide: list[str] | None = None, highlight: list[str] | None = None,
             postfx: bool | None = None) -> dict:
    """Set environment and view options (SAAP view.set also has wireframe/area: use satk saap call).

    Args:
        time: HH:MM.
        weather: id 0-22.
        draw_dist: multiplier 0.1-10.
    """
    from . import api

    return api.set_view(target, time, weather, overlays, lod, draw_dist, hide, highlight, postfx, None, None)


@op("view.bookmark", summary="Camera bookmarks: list, save NAME (current camera or pose), rm NAME; use as bm:NAME.",
    summary_ru="Закладки камеры: list, save NAME, rm NAME.",
    examples=("satk view bookmark save grove_center --target mock", "satk view bookmark list"))
def view_bookmark(action: Literal["list", "save", "rm"] | None, name: str | None,
                  pose: dict | None = None, note: str | None = None, target: Target = "ariane") -> dict:
    """Manage bookmarks.

    Args:
        action: list (default), save, rm.
        name: a-z 0-9 _ . -
    """
    from . import api

    return api.bookmark(action, name, pose, note, target)


@op("view.replay", summary="Repeat a capture from its sidecar and check the PNG hash (SSIM when it differs).",
    summary_ru="Повторить захват по sidecar и сравнить sha256.", mcp=False,
    examples=("satk view replay <workspace>/work/out/captures/20261004/20261004-153201-ab12.json",))
def view_replay(sidecar: str) -> dict:
    """Replay a capture.

    Args:
        sidecar: path to the sidecar .json (or its PNG) or a cap: SID.
    """
    from . import api

    return api.replay(sidecar)


@op("view.conformance", summary="Run the SAAP/1 conformance cases against a target (filtered by its caps).",
    summary_ru="Прогнать тесты соответствия SAAP/1 против цели.", mcp=False,
    examples=("satk view conformance --target mock --json",))
def view_conformance(target: Target = "mock", only: str | None = None, files: list[str] | None = None,
                     show_all: bool = False) -> dict:
    """Conformance run.

    Args:
        target: mock, ariane or game.
        only: regular expression on case ids.
        files: case files (default proto/conformance/*.jsonl).
        show_all: list passed cases too.
    """
    from . import api

    return api.conformance(target, only, files, "all" if show_all else "failed")


@op("view.mock", summary="Run the SAAP/1 mock endpoint (synthetic world) until quit/Ctrl+C.",
    summary_ru="Запустить mock-эндпоинт SAAP/1 (синтетический мир).", mcp=False,
    examples=("satk view mock --port 0",))
def view_mock(port: int = 0, insecure: bool = False) -> dict:
    """Serve the mock endpoint; writes work/run/endpoints/mock.json and sessions/mock.json.

    Args:
        port: TCP port on 127.0.0.1 (0 = ephemeral).
        insecure: accept any token (tests only; also SATK_AGENT_INSECURE=1).
    """
    import os
    import sys

    from ..saap import mock

    if insecure:
        os.environ["SATK_AGENT_INSECURE"] = "1"

    def ready(srv) -> None:
        sys.stderr.write(f"satk mock endpoint on 127.0.0.1:{srv.port} (pid {os.getpid()})\n")
        sys.stderr.flush()

    return mock.serve(port, on_ready=ready)


# --------------------------------------------------------------------------- SID providers


class _BookmarkProvider:
    kinds = ("bm",)

    def get(self, sid: Sid, fields: list[str] | None, profile: str) -> dict:
        from . import store

        b = store.get(sid.key)
        return obj(f"bm:{b['name']}", pose=b["pose"], env=b.get("env"), note=b.get("note"), created=b.get("created_at"))

    def find(self, q: str, kind: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        from . import store

        ql = (q or "").lower()
        rows = [[f"bm:{b['name']}", "bm", b["name"], b.get("note") or ""] for b in store.list_all()
                if ql in b["name"] or ql in (b.get("note") or "").lower()]
        return table(["id", "kind", "name", "info"], rows[:limit], total=len(rows))

    def refs(self, sid: Sid, rel: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        return table(["id", "rel"], [])


class _CaptureProvider:
    kinds = ("cap",)

    def get(self, sid: Sid, fields: list[str] | None, profile: str) -> dict:
        from . import sidecar as SC

        d = SC.load(str(sid))
        return obj(d["id"], target=d.get("target"), file=(d.get("files") or {}).get("color"), sidecar=d["_path"],
                   pose=d.get("pose"), env=d.get("env"), w=d.get("w"), h=d.get("h"), settled=d.get("settled"),
                   legend=d.get("legend"), files=d.get("files"), time=d.get("time"), build=d.get("build"))

    def find(self, q: str, kind: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        from . import sidecar as SC

        rows = [[d["id"], "cap", d.get("target"), f"{d.get('w')}x{d.get('h')} {d.get('time')}"]
                for d in SC.find_sidecars(q, limit)]
        return table(["id", "kind", "name", "info"], rows)

    def refs(self, sid: Sid, rel: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        from . import sidecar as SC

        d = SC.load(str(sid))
        rows = [[row[1], "legend", row[0], row[2]] for row in d.get("legend") or [] if row[1]]
        return table(["id", "rel", "n", "name"], rows[:limit], total=len(rows))


_BM = _BookmarkProvider()
_CAP = _CaptureProvider()
for _p in (_BM, _CAP):
    try:
        register_provider(_p)
    except ValueError:  # already registered (module reloaded in tests)
        register_provider(_p, replace=True)


# --------------------------------------------------------------------------- status / doctor


@status_provider("viewer")
def _viewer_status(deep: bool) -> dict:
    from ..saap import client as C

    out: dict[str, Any] = {}
    for ep in C.list_endpoints():
        role = ep.get("role")
        if role not in ("ariane", "mock", "game", "client2", "server"):
            continue
        d = {"up": bool(ep.get("alive")), "proto": ep.get("protocol"), "impl": ep.get("impl"),
             "caps": ep.get("caps") or [], "pid": ep.get("pid")}
        if deep and d["up"]:
            try:
                from . import launcher

                st = launcher.status(role)
                d["up"] = st.get("up", d["up"])
                if st.get("window"):
                    d["window"] = st["window"]
            except SatkError as e:
                d["error"] = e.code
        out[role] = d
    out.setdefault("ariane", {"up": False})
    return out


@doctor_check("viewer")
def _viewer_doctor() -> dict:
    from pathlib import Path

    from ..core import paths

    exe = Path(paths.cfg().paths.viewer)
    if not exe.is_file():
        return {"status": "warn", "msg": f"Ariane viewer not built: {paths.jpath(exe)}",
                "fix": "powershell -ExecutionPolicy Bypass -File "
                       + str(Path(paths.cfg().paths.workspace) / "viewer" / "ariane" / "satk" / "build.ps1")}
    return {"status": "ok", "msg": f"Ariane viewer: {paths.jpath(exe)}", "fix": None}
