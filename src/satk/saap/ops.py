"""Operations of satk.saap (owner WP-07): ``satk saap validate|call|cpp-selftest`` (CLI only)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..core.envelope import obj, table
from ..core.errors import SatkError
from ..core.registry import op

#: toolset -> (MSVC version for vcvarsall -vcvars_ver, /std flag)
CPP_TOOLSETS = {"v143": ("14.4", "c++14"), "v145": ("14.5", "c++latest")}


@op("saap.validate", summary="Validate SAAP/1 conformance case files (.jsonl), the spec document (.md) and envelopes "
                             "(.json) against proto/schema.",
    summary_ru="Проверить файлы SAAP/1 (кейсы, спецификацию, конверты) по схемам.", mcp=False, group="dev",
    examples=("satk saap validate proto/conformance/*.jsonl proto/SAAP-v1.md",))
def saap_validate(files: list[str] | None) -> dict:
    """Validate SAAP files.

    Args:
        files: .jsonl case files, the SAAP-v1.md document or .json envelopes; globs are expanded
            (default: proto/conformance/*.jsonl and proto/SAAP-v1.md).
    """
    import glob
    import json

    from . import conformance as CF
    from . import schema as S
    from . import specdoc

    if not files:
        files = [str(S.PROTO_DIR / "conformance" / "*.jsonl"), str(S.PROTO_DIR / "SAAP-v1.md")]
    expanded: list[Path] = []
    for f in files:
        hits = sorted(glob.glob(f)) if any(c in f for c in "*?[") else [f]
        if not hits:
            raise SatkError("NOT_FOUND", f"no files match {f}")
        expanded.extend(Path(h) for h in hits)
    rows: list[list] = []
    errors: list[str] = []
    jsonl = [p for p in expanded if p.suffix == ".jsonl"]
    if jsonl:
        r = CF.validate_cases(jsonl)
        errors.extend(r["errors"])
        for p in jsonl:
            n = sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
            bad = [e for e in r["errors"] if e.startswith(p.name)]
            rows.append([p.name, "cases", n, len(bad)])
    for p in expanded:
        if p.suffix == ".md":
            d = specdoc.check(p)
            errors.extend(d["errors"])
            rows.append([p.name, "spec", d["methods"], len(d["errors"])])
        elif p.suffix == ".json":
            try:
                env = json.loads(p.read_text(encoding="utf-8"))
            except ValueError as e:
                errors.append(f"{p.name}: not JSON: {e}")
                rows.append([p.name, "envelope", 1, 1])
                continue
            errs = S.validate_request(env) if "method" in env else S.validate_response(env)
            errors.extend(f"{p.name}: {e}" for e in errs)
            rows.append([p.name, "envelope", 1, len(errs)])
        elif p.suffix not in (".jsonl",):
            raise SatkError("BAD_PARAMS", f"unsupported file type: {p}")
    t = table(["file", "kind", "items", "errors"], rows)
    t["valid"] = not errors
    t["schemas"] = len(S.methods())
    if errors:
        raise SatkError("BAD_PARAMS", f"{len(errors)} validation error(s); first: {errors[0]}",
                        data={"errors": errors[:50], "cols": t["cols"], "rows": t["rows"]})
    return t


@op("saap.call", summary="Send one SAAP/1 request to a running endpoint (role from work/run/endpoints) and print the result.",
    summary_ru="Отправить один запрос SAAP/1 эндпоинту по роли.", mcp=False, group="dev",
    examples=("satk saap call mock camera.get", "satk saap call mock env.set '{\"time\":\"21:30\"}'"))
def saap_call(role: str, method: str, params: dict | None, timeout: float = 60.0) -> dict:
    """Raw SAAP call.

    Args:
        role: endpoint role (mock, ariane, game, ...).
        method: SAAP method name, e.g. camera.get.
        params: JSON object with the params (or @file.json).
        timeout: seconds to wait for the response.
    """
    from . import client as C

    c = C.connect(role, timeout=timeout)
    try:
        result = c.call(method, params or {})
        meta = getattr(c, "last_meta", {})
    finally:
        c.close()
    return {"role": role, "method": method, "result": result, "meta": meta}


def _vcvars() -> Path:
    from ..core import paths

    p = paths.cfg().paths.get("vcvars")
    if p is None or not Path(p).is_file():
        where = paths.jpath(p) if p is not None else "(not configured, not found by vswhere)"
        raise SatkError("NOT_READY", f"vcvarsall.bat not found: {where}",
                        hint="install the Visual Studio C++ build tools or set [paths].vcvars in satk.toml")
    return Path(p)


def _msvc_version(prefix: str) -> str | None:
    """Full MSVC tools version (``14.44.35207``) starting with ``prefix`` (``14.4``)."""
    root = _vcvars().parents[2] / "Tools" / "MSVC"  # ...\VC\Tools\MSVC
    if not root.is_dir():
        return None
    vers = [p.name for p in root.iterdir() if p.is_dir() and p.name.startswith(prefix)]
    vers.sort(key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")], reverse=True)
    return vers[0] if vers else None


@op("saap.cpp_selftest", summary="Compile and run the saap_frame.hpp self-test with cl.exe: /std:c++14 (v143) and "
                                 "/std:c++latest (v145).",
    summary_ru="Собрать и запустить самотест saap_frame.hpp (cl.exe v143 и v145).", mcp=False, group="dev",
    examples=("satk saap cpp-selftest",))
def saap_cpp_selftest(toolsets: list[str] | None = None, keep: bool = False) -> dict:
    """C++ header self-test.

    Args:
        toolsets: subset of v143, v145 (default both).
        keep: keep the build directory (work/tmp/saap-cpp/<toolset>).
    """
    import shutil
    import time

    from ..core import paths
    from .schema import PROTO_DIR

    want = toolsets or list(CPP_TOOLSETS)
    bad = [t for t in want if t not in CPP_TOOLSETS]
    if bad:
        raise SatkError("BAD_PARAMS", f"unknown toolset {bad[0]!r}", did_you_mean=list(CPP_TOOLSETS))
    vcvars = _vcvars()
    src = PROTO_DIR / "cpp" / "saap_frame_selftest.cpp"
    rows = []
    failed = []
    for ts in want:
        prefix, std = CPP_TOOLSETS[ts]
        ver = _msvc_version(prefix)
        if ver is None:
            rows.append([ts, std, None, "missing", f"no MSVC {prefix}.x toolset under {vcvars.parents[2]}"])
            failed.append(ts)
            continue
        d = paths.tmp("saap-cpp") / ts
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        exe = d / "saap_frame_selftest.exe"
        bat = d / "build.cmd"
        bat.write_text(
            "@echo off\r\n"
            f'set "VSCMD_START_DIR={d}"\r\n'
            f'call "{vcvars}" x64 -vcvars_ver={ver} >nul\r\n'
            "if errorlevel 1 exit /b 9\r\n"
            f'cd /d "{d}"\r\n'
            f'cl /nologo /EHsc /W4 /WX /Zc:__cplusplus /std:{std} /Fe:"{exe}" "{src}"\r\n'
            "if errorlevel 1 exit /b 1\r\n"
            f'"{exe}" "{d}"\r\n',
            encoding="utf-8")
        env = dict(os.environ)
        env["TEMP"] = env["TMP"] = str(d)
        t0 = time.monotonic()
        p = subprocess.run(["cmd", "/d", "/c", str(bat)], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=600)
        out = (p.stdout + p.stderr).strip().splitlines()
        last = next((l for l in reversed(out) if "selftest" in l), out[-1] if out else "")
        status = "pass" if p.returncode == 0 else ("compile" if p.returncode == 1 else "fail")
        if p.returncode != 0:
            failed.append(ts)
            last = " | ".join(l for l in out if "error" in l.lower() or "FAIL" in l)[:400] or last
        rows.append([ts, std, ver, status, last, round(time.monotonic() - t0, 1)][:5])
        if not keep and p.returncode == 0:
            shutil.rmtree(d, ignore_errors=True)
    t = table(["toolset", "std", "msvc", "result", "detail"], rows)
    if failed:
        raise SatkError("EXTERNAL_TOOL", f"saap_frame.hpp self-test failed for {', '.join(failed)}",
                        data={"cols": t["cols"], "rows": rows})
    return t
