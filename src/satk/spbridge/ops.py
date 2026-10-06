"""Operations of satk.spbridge: ``satk sp addresses|build|selftest|start|status|stop``.

``addresses`` and ``build``/``selftest`` are offline and safe (the last two need the VS C++ build
tools); ``selftest`` is the package's offline proof - it compiles the host-side SAAP server and runs
the SAAP/1 conformance cases against it over TCP, with no game. ``start``/``stop`` touch a real
game process and are consent-gated and CLI-only; **this package never launches the game in an
automated run** - prepare the command and let a person run it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Literal

from ..core.envelope import obj, table
from ..core.errors import SatkError
from ..core.registry import op
from ..mcp.generic import cli_only

NATIVE_DIR = "native/sp_bridge"
#: Source files of each build target, compiled with cl.exe.
TARGETS = {
    "asi": {"arch": "x86", "ld": True, "srcs": ["dllmain.cpp", "sp_hooks.cpp"], "out": "satk_sp.asi",
            "libs": []},
    "test": {"arch": "x86", "ld": False, "srcs": ["test_server_main.cpp"], "out": "test_server.exe",
             "libs": []},
}


# --------------------------------------------------------------------------- addresses


@op("sp.addresses",
    summary="Show the 1.0 US engine address table the SP bridge ASI uses; --verify cross-checks it "
            "against the knowledge base, --emit-header regenerates the C++ header.",
    summary_ru="Показать таблицу адресов 1.0 US для SP-моста; --verify сверяет с базой знаний, "
               "--emit-header пересобирает заголовок C++.",
    mcp=False, group="dev",
    examples=("satk sp addresses", "satk sp addresses --verify"))
def sp_addresses(verify: bool = False, emit_header: bool = False) -> dict:
    """Engine address table.

    Args:
        verify: cross-check every func/global/hook against the symbol DB / knowledge base.
        emit_header: rewrite native/sp_bridge/sp_addresses.hpp from the table.
    """
    from . import addresses as A

    rows = [[a.name, a.kind, f"0x{a.addr:X}", a.symbol] for a in A.all_symbols()]
    t = table(["name", "kind", "addr", "symbol"], rows, total=len(rows))
    t["game_version"] = A.GAME_VERSION

    if emit_header:
        from ..core import paths
        from ..core.config import REPO_ROOT

        dst = REPO_ROOT / "native" / "sp_bridge" / "sp_addresses.hpp"
        paths.atomic_write(dst, A.cpp_header())
        t["header"] = paths.jpath(dst)

    if verify:
        t["verify"] = _verify_addresses(A)
    return t


def _verify_addresses(A) -> dict:
    """Compare each address with the symbol DB (``re``) / knowledge base (``kb``)."""
    from ..core.registry import get_op, invoke

    mismatches: list[str] = []
    checked = 0
    unknown = 0
    have_re = True
    try:
        get_op("re.find")
    except SatkError:
        have_re = False
    for a in A.all_symbols():
        if a.symbol.startswith("call "):
            continue  # hook sites are byte-checked by the game test, not by symbol lookup
        base = a.symbol
        r = invoke("kb.sym", {"name": base, "limit": 4}) if _has_op("kb.sym") else {"ok": False}
        if not r.get("ok") or not r.get("rows"):
            unknown += 1
            continue
        addrs = set()
        for row in r["rows"]:
            d = dict(zip(r["cols"], row))
            av = d.get("addr")
            if isinstance(av, str) and av.startswith("0x"):
                addrs.add(int(av, 16))
        checked += 1
        if addrs and a.addr not in addrs:
            mismatches.append(f"{a.name} ({a.symbol}): table 0x{a.addr:X} not in KB {{{', '.join(hex(x) for x in sorted(addrs))}}}")
    return {"checked": checked, "unknown": unknown, "mismatches": mismatches, "re_available": have_re,
            "ok": not mismatches}


def _has_op(name: str) -> bool:
    from ..core.registry import get_op

    try:
        get_op(name)
        return True
    except SatkError:
        return False


# --------------------------------------------------------------------------- build / selftest


def _vcvars() -> Path:
    from ..core import paths

    p = paths.cfg().paths.get("vcvars")
    if p is None or not Path(p).is_file():
        raise SatkError("NOT_READY", "vcvarsall.bat not found",
                        hint="install the Visual Studio C++ build tools or set [paths].vcvars in satk.toml")
    return Path(p)


def _msvc_toolset(prefix: str = "14.4") -> str | None:
    root = _vcvars().parents[2] / "Tools" / "MSVC"
    if not root.is_dir():
        return None
    vers = sorted((d.name for d in root.iterdir() if d.is_dir() and d.name.startswith(prefix)),
                  key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")], reverse=True)
    return vers[0] if vers else None


def _compile(target: str, out_dir: Path, *, strict: bool = True) -> dict:
    """Compile one target with cl.exe into ``out_dir``; returns {status, out, detail, seconds}."""
    import time

    from ..core.config import REPO_ROOT

    spec = TARGETS[target]
    vcvars = _vcvars()
    ver = _msvc_toolset()
    if ver is None:
        raise SatkError("NOT_READY", f"no MSVC 14.4.x toolset under {vcvars.parents[2]}")
    src_dir = REPO_ROOT / "native" / "sp_bridge"
    proto_dir = REPO_ROOT / "proto" / "cpp"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / spec["out"]
    srcs = " ".join(f'"{src_dir / s}"' for s in spec["srcs"])
    wx = "/WX " if strict else ""
    ld = "/LD " if spec["ld"] else ""
    link = f'/link /DLL /OUT:"{out_path}"' if spec["ld"] else f'/Fe:"{out_path}"'
    bat = out_dir / "build.cmd"
    bat.write_text(
        "@echo off\r\n"
        f'set "VSCMD_START_DIR={out_dir}"\r\n'
        f'call "{vcvars}" {spec["arch"]} -vcvars_ver={ver} >nul\r\n'
        "if errorlevel 1 exit /b 9\r\n"
        f'cd /d "{out_dir}"\r\n'
        f'cl /nologo {ld}/EHsc /W4 {wx}/Zc:__cplusplus /std:c++14 /D_CRT_SECURE_NO_WARNINGS '
        f'/DWIN32_LEAN_AND_MEAN /I"{src_dir}" /I"{proto_dir}" '
        + (f'{link} {srcs}' if not spec["ld"] else f'/Fe:"{out_path}" {srcs} {link}') + "\r\n"
        "if errorlevel 1 exit /b 1\r\n"
        "echo BUILD_OK\r\n",
        encoding="utf-8")
    env = dict(os.environ)
    env["TEMP"] = env["TMP"] = str(out_dir)
    t0 = time.monotonic()
    p = subprocess.run(["cmd", "/d", "/c", str(bat)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=600)
    lines = (p.stdout + p.stderr).splitlines()
    status = "ok" if p.returncode == 0 else ("compile" if p.returncode == 1 else "toolchain")
    detail = ""
    if p.returncode != 0:
        errs = [l for l in lines if "error" in l.lower()]
        detail = " | ".join(errs[:4])[:600] or (lines[-1] if lines else "")
    return {"status": status, "out": str(out_path), "detail": detail, "seconds": round(time.monotonic() - t0, 1),
            "msvc": ver}


@op("sp.build",
    summary="Compile the SP bridge with cl.exe: 'asi' = satk_sp.asi (x86 DLL), 'test' = the host-side "
            "SAAP test server, 'all' = both. Needs the VS C++ build tools.",
    summary_ru="Собрать SP-мост через cl.exe: 'asi' (satk_sp.asi, x86), 'test' (тестовый SAAP-сервер), "
               "'all'. Нужны инструменты C++ VS.",
    mcp=False, group="dev", long_running=True,
    examples=("satk sp build test", "satk sp build asi"))
def sp_build(target: Literal["asi", "test", "all"] = "all") -> dict:
    """Build the native project.

    Args:
        target: asi, test or all.
    """
    from ..core import paths

    want = ["asi", "test"] if target == "all" else [target]
    rows = []
    failed = []
    for tg in want:
        out_dir = paths.work("build", "sp", tg)
        r = _compile(tg, out_dir)
        rows.append([tg, TARGETS[tg]["arch"], r["status"], r["msvc"], r["seconds"], r["detail"]])
        if r["status"] != "ok":
            failed.append(tg)
    t = table(["target", "arch", "status", "msvc", "seconds", "detail"], rows)
    if failed:
        raise SatkError("EXTERNAL_TOOL", f"build failed for {', '.join(failed)}",
                        data={"cols": t["cols"], "rows": t["rows"]})
    return t


@op("sp.selftest",
    summary="Offline proof: compile the host-side SAAP server (no game) and run the SAAP/1 conformance "
            "cases against it over TCP; reports pass/fail/skip. Needs the VS C++ build tools.",
    summary_ru="Оффлайн-проверка: собрать хостовый SAAP-сервер (без игры) и прогнать тесты SAAP/1 по TCP.",
    mcp=False, group="dev", long_running=True,
    examples=("satk sp selftest",))
def sp_selftest(only: str | None = None, keep: bool = False) -> dict:
    """Build + conformance-test the SAAP server with the mock backend.

    Args:
        only: regular expression on conformance case ids.
        keep: keep the build directory.
    """
    import re as _re
    import secrets
    import time

    from ..core import paths
    from ..saap import conformance as CF

    out_dir = paths.tmp("sp-selftest")
    r = _compile("test", out_dir)
    if r["status"] != "ok":
        raise SatkError("EXTERNAL_TOOL", f"could not build the test server: {r['detail']}",
                        data={"status": r["status"]})
    exe = Path(r["out"])
    token = secrets.token_hex(32)
    proc = subprocess.Popen([str(exe), "--port", "0", "--token", token, "--idle-ms", "30000"],
                            stdout=subprocess.PIPE, text=True, encoding="utf-8")
    port = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith("PORT "):
                port = int(line.split()[1])
                break
        if port is None:
            raise SatkError("EXTERNAL_TOOL", "test server did not report a port")
        drv = CF.SaapDriver("127.0.0.1", port, token)
        rep = CF.run(drv, only=only, token=token)
    finally:
        try:
            from ..saap import client as C

            c = C.SaapClient("127.0.0.1", port or 0, token)
            c.connect()
            c.call("quit", {})
            c.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
        if not keep:
            from ..docs.cleanup import remove_tree

            try:
                remove_tree(out_dir)
            except Exception:  # noqa: BLE001
                pass
    rows = [r2 for r2 in rep["results"] if r2[2] != "pass"]
    t = table(["case", "caps", "result", "ms", "detail"], rows, total=rep["total"])
    t.update({"impl": "satk-sp-mock", "driver": "saap", "pass": rep["pass"], "fail": rep["fail"],
              "skip": rep["skip"], "percent": rep["percent"], "msvc": r["msvc"]})
    if rep["fail"]:
        raise SatkError("CHECK_FAILED", f"{rep['fail']} conformance case(s) failed",
                        data={"cols": t["cols"], "rows": t["rows"], "percent": rep["percent"]})
    return t


# --------------------------------------------------------------------------- status / start / stop


@op("sp.status",
    summary="Status of the single-player bridge endpoint (role 'sp'): discovery files, pid, port, caps.",
    summary_ru="Состояние SP-эндпоинта (роль 'sp'): файлы обнаружения, pid, порт, возможности.",
    mcp=False, group="dev",
    examples=("satk sp status",))
def sp_status() -> dict:
    """Read the SP endpoint discovery files (read-only)."""
    from ..saap import client as C
    from . import ROLE

    ep = C.read_endpoint(ROLE)
    sess = C.read_session(ROLE)
    if ep is None and sess is None:
        return obj(role=ROLE, running=False, note="no SP endpoint discovered (satk_sp.asi not loaded)")
    state, why = C.verify_pid(ep, sess)
    return obj(role=ROLE, running=state in ("ok", "unknown"), state=state, reason=why or None,
               pid=(ep or {}).get("pid"), port=(ep or {}).get("port"), impl=(ep or {}).get("impl"),
               caps=(ep or {}).get("caps"))


@cli_only("it starts a real gta_sa.exe process and injects a DLL; run it yourself against a test copy", consent=True)
@op("sp.start",
    summary="Start gta_sa.exe from a test copy with the SP bridge ASI (suspended + inject, or via the "
            "ASI loader). Consent-gated and CLI only; refuses the original install and the clean copy.",
    summary_ru="Запустить gta_sa.exe из тестовой копии с ASI SP-моста (внедрение или загрузчик ASI). "
               "Требует согласия, только CLI; отказывает для оригинала и чистой копии.",
    mcp=False, group="dev",
    examples=("satk sp start --copy <workspace>/work/mta/sp-test --yes",))
def sp_start(copy: str, inject: bool = True, port: int = 0, yes: bool = False) -> dict:
    """Launch a test copy of the game with the SP bridge.

    Args:
        copy: a test game directory that holds gta_sa.exe (NOT the original install or clean copy).
        inject: inject the ASI into a suspended process (otherwise rely on the ASI loader).
        port: TCP port on 127.0.0.1 (0 = ephemeral).
        yes: confirm the launch.
    """
    import secrets

    from ..core import paths
    from ..saap import client as C
    from . import CAPS, ROLE
    from .addresses import GAME_VERSION

    cfg = paths.cfg()
    copy_dir = Path(copy).resolve()
    exe = copy_dir / "gta_sa.exe"
    # Refuse the original install and the clean copy (clean copy is `satk game ...` only).
    protected = {Path(cfg.paths.game).resolve(), Path(cfg.paths.installed).resolve()}
    if copy_dir in protected or not str(copy_dir).strip():
        raise SatkError("BAD_PARAMS", "refusing to start the original install or the clean copy; "
                        "use a dedicated test copy (satk game clone --to <dir>)",
                        hint="satk game clone --to <workspace>/work/mta/sp-test")
    if not exe.is_file():
        raise SatkError("NOT_FOUND", f"no gta_sa.exe in {copy_dir}")
    if not yes:
        raise SatkError("CONSENT_REQUIRED", "pass --yes to start the game with the SP bridge",
                        data={"exe": paths.jpath(exe), "inject": inject})

    asi = paths.work("build", "sp", "asi") / "satk_sp.asi"
    if not asi.is_file():
        raise SatkError("NOT_READY", "satk_sp.asi is not built", hint="satk sp build asi")

    token = secrets.token_hex(32)
    run_dir = paths.work("run")
    descriptor = paths.work("run", "endpoints", f"{ROLE}.json")
    env = dict(os.environ)
    env.update({"SATK_AGENT_PORT": str(port), "SATK_AGENT_TOKEN": token,
                "SATK_AGENT_OUT_ROOT": str(paths.work()), "SATK_AGENT_DESCRIPTOR": str(descriptor)})

    from .inject import start_suspended, inject as do_inject, resume

    started = start_suspended(exe, cwd=copy_dir)
    try:
        if inject:
            do_inject(started.h_process, asi)
        resume(started)
    except SatkError:
        from .inject import terminate

        terminate(started)
        raise
    C.write_session(ROLE, {"role": ROLE, "pid": started.pid, "token": token, "log": None,
                           "launched_by": "satk sp start", "started_at": _now_iso()})
    return obj(role=ROLE, pid=started.pid, exe=paths.jpath(exe), game_version=GAME_VERSION,
               caps=list(CAPS), note="the ASI listens once the game reaches its main loop")


@cli_only("it signals and may terminate a game process", consent=True)
@op("sp.stop",
    summary="Stop the SP bridge endpoint: send 'quit' and remove its discovery files (and the game "
            "process we started, if any). Consent-gated and CLI only.",
    summary_ru="Остановить SP-эндпоинт: послать 'quit' и убрать файлы обнаружения. Требует согласия, только CLI.",
    mcp=False, group="dev",
    examples=("satk sp stop",))
def sp_stop() -> dict:
    """Stop the SP endpoint (best-effort)."""
    from ..saap import client as C
    from . import ROLE

    ep = C.read_endpoint(ROLE)
    quit_sent = False
    if ep is not None and ep.get("protocol") == "saap/1" and C.endpoint_alive(ep, C.read_session(ROLE)):
        try:
            c = C.connect(ROLE, timeout=5)
            c.call("quit", {})
            c.close()
            quit_sent = True
        except SatkError:
            pass
    C.remove_discovery(ROLE)
    return obj(role=ROLE, quit_sent=quit_sent, note="discovery files removed")


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
