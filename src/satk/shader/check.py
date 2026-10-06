"""``satk shader check``: compile an MTA effect with ``fxc.exe`` and check what MTA does differently. Stdlib only.

* :func:`find_fxc` - the HLSL compiler of a local Windows SDK (``--fxc``, ``SATK_FXC``, ``DXSDK_DIR``, the Windows Kits
  registry key and folders, the Visual Studio / Build Tools folder satk detected, ``PATH``). Without one the check
  falls back to the static reader of :mod:`satk.shader.fx` and says so.
* :func:`compile_fx` - ``fxc /T fx_2_0`` on the flattened source (includes resolved by MTA's rules, ``#line``
  markers keep file names), with ``IS_DEPTHBUFFER_RAWZ`` defined as MTA does (both values when the file uses it).
  ``fxc`` uses ``D3DCompiler_47``; MTA compiles with D3DX9 at run time, small differences are possible.
* :func:`mta_checks` - techniques and passes, shader models MTA's Direct3D 9 accepts, entry functions, sampler
  textures, state annotations (exact group case, register names, stage prefixes, types; MTA ignores a wrong one
  silently), automatic semantics, ``DEPTHBUFFER``, fog with ``ps_3_0``, ``CUSTOMFLAGS``.
* :func:`lua_checks` - the client Lua of the resource: ``dxSetShaderValue`` names against the parameters (MTA files
  a parameter under its semantic when it has one), textures never set, ``dxCreateShader`` element types, and the
  world-texture patterns against the profile index (a pattern that matches nothing is usually a typo).
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..core.errors import SatkError
from .fx import Effect, Issue, Param

__all__ = ["FxcHit", "find_fxc", "sdk_label", "compile_fx", "mta_facts", "mta_checks", "lua_checks", "LuaRefs",
           "lua_refs", "client_scripts", "strip_lua_comments"]

_FXC_LINE = re.compile(r"^(?P<file>.*?)\((?P<line>\d+)(?:,(?P<col>\d+)(?:-\d+)?)?\)\s*:\s*(?P<sev>error|warning)\s+"
                       r"(?P<code>X\d+)\s*:\s*(?P<msg>.*)$")
#: fxc warnings that only concern fxc itself.
_FXC_NOISE = {"X4717"}   # "Effects deprecated for D3DCompiler_47"


@lru_cache(maxsize=1)
def mta_facts() -> dict:
    from ..core import resources

    return resources.read_json("shader", "mta.json")


# --------------------------------------------------------------------------- fxc


@dataclass
class FxcHit:
    path: Path | None
    source: str | None
    tried: list[str] = field(default_factory=list)


_FXC_MEMO: dict[str, FxcHit] = {}


def _version_key(p: Path) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", p.name)) or (0,)


def _kits_bins(kits: Path) -> list[Path]:
    """``fxc.exe`` candidates of one Windows Kits root, newest SDK first, x64 before x86."""
    out: list[Path] = []
    b = kits / "bin"
    if not b.is_dir():
        return out
    vers = sorted((d for d in b.iterdir() if d.is_dir() and re.match(r"^\d+\.\d+\.\d+\.\d+$", d.name)),
                  key=_version_key, reverse=True)
    for d in vers + [b]:
        for arch in ("x64", "x86"):
            out.append(d / arch / "fxc.exe")
    return out


def _registry_kits() -> list[Path]:
    try:
        import winreg  # noqa: PLC0415 - Windows only
    except ImportError:
        return []
    out: list[Path] = []
    for sub in (r"SOFTWARE\Microsoft\Windows Kits\Installed Roots",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows Kits\Installed Roots"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub) as k:
                for val in ("KitsRoot10", "KitsRoot81"):
                    try:
                        out.append(Path(winreg.QueryValueEx(k, val)[0]))
                    except OSError:
                        pass
        except OSError:
            pass
    return out


def _vs_roots() -> list[Path]:
    """Visual Studio / Build Tools folders from satk's tool discovery (``paths.msbuild``)."""
    roots: list[Path] = []
    try:
        from ..core.paths import cfg

        mb = getattr(cfg().paths, "msbuild", None)
    except Exception:  # noqa: BLE001 - discovery is best effort
        mb = None
    if mb:
        p = Path(mb)
        for parent in p.parents:
            if (parent / "MSBuild").is_dir() and parent.name.lower() != "msbuild":
                roots.append(parent)
                break
    return roots


def find_fxc(explicit: str | None = None, *, refresh: bool = False) -> FxcHit:
    """Locate ``fxc.exe``; ``explicit`` = a path or ``none`` (skip compiling)."""
    if explicit:
        if explicit.strip().lower() == "none":
            return FxcHit(None, "disabled", ["--fxc none"])
        p = Path(explicit)
        if p.is_dir():
            p = p / "fxc.exe"
        if not p.is_file():
            raise SatkError("NOT_FOUND", f"--fxc {explicit!r}: no such file",
                            hint="the fxc.exe of a Windows SDK, e.g. <Windows Kits>/10/bin/<version>/x64/fxc.exe; "
                                 "--fxc none skips compiling")
        return FxcHit(p, "argument", [str(p)])
    key = os.environ.get("SATK_FXC", "") + "|" + os.environ.get("DXSDK_DIR", "")
    if not refresh and key in _FXC_MEMO:
        return _FXC_MEMO[key]
    tried: list[str] = []

    def done(p: Path | None, how: str | None) -> FxcHit:
        hit = FxcHit(p, how, tried)
        _FXC_MEMO[key] = hit
        return hit

    env = os.environ.get("SATK_FXC")
    if env:
        tried.append("SATK_FXC")
        if Path(env).is_file():
            return done(Path(env), "SATK_FXC")
    dx = os.environ.get("DXSDK_DIR")
    if dx:
        tried.append("DXSDK_DIR")
        for arch in ("x64", "x86"):
            p = Path(dx) / "Utilities" / "bin" / arch / "fxc.exe"
            if p.is_file():
                return done(p, "DXSDK_DIR")
    kits = _registry_kits()
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(var)
        if base:
            kits += [Path(base) / "Windows Kits" / "10", Path(base) / "Windows Kits" / "8.1"]
    seen: set[str] = set()
    for k in kits:
        ks = str(k).lower()
        if ks in seen:
            continue
        seen.add(ks)
        tried.append(f"Windows Kits {k.name}")
        for p in _kits_bins(k):
            if p.is_file():
                return done(p, "windows_kits")
    for root in _vs_roots():
        tried.append("Visual Studio / Build Tools folder")
        for p in sorted(root.rglob("fxc.exe"), key=lambda q: (("x64" not in q.parts), str(q))):
            return done(p, "visual_studio")
    tried.append("PATH")
    w = shutil.which("fxc")
    if w:
        return done(Path(w), "path")
    return done(None, None)


def sdk_label(p: Path) -> str:
    m = re.search(r"(\d+\.\d+\.\d+\.\d+)", str(p))
    if m:
        return f"Windows SDK {m.group(1)}"
    if "Utilities" in p.parts:
        return "DirectX SDK"
    return "fxc"


def compile_fx(eff: Effect, fxc: Path, *, defines: list[str] | None = None, tmp_id: str = "shader") -> dict:
    """Compile ``eff`` (``fx_2_0``); returns ``{"ok", "issues", "seconds", "variants"}``."""
    from ..core.paths import atomic_write, ensure_writable, tmp

    variants = ["0", "1"] if "IS_DEPTHBUFFER_RAWZ" in eff.macros_used else ["0"]
    user: list[str] = []
    for d in defines or []:
        name = d.split("=", 1)[0].strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise SatkError("BAD_PARAMS", f"--define {d!r}: expected NAME or NAME=VALUE", hint="--define QUALITY=2")
        user.append(d.strip())
    issues: list[Issue] = []
    seen: set[tuple] = set()
    ok = True
    t0 = time.perf_counter()
    work = tmp(tmp_id)
    # unique per call: parallel checks of the same source must not share (or delete) each other's files
    digest = hashlib.blake2b(f"{eff.source}|{os.getpid()}|{threading.get_ident()}|{time.perf_counter_ns()}".encode(
        "utf-8"), digest_size=8).hexdigest()
    src = work / f"{digest}.fx"
    atomic_write(src, eff.source)
    try:
        for v in variants:
            out = ensure_writable(work / f"{digest}_{v}.fxo")
            argv = [str(fxc), "/nologo", "/T", "fx_2_0", "/Fo", str(out), "/D", f"IS_DEPTHBUFFER_RAWZ={v}"]
            for d in user:
                argv += ["/D", d]
            argv.append(str(src))
            try:
                p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=120, cwd=str(work))
            except (OSError, subprocess.TimeoutExpired) as e:
                raise SatkError("EXTERNAL_TOOL", f"fxc failed to run: {e}", hint="--fxc none skips compiling") from None
            if p.returncode != 0:
                ok = False
            for line in (p.stdout + "\n" + p.stderr).splitlines():
                m = _FXC_LINE.match(line.strip())
                if not m or m.group("code") in _FXC_NOISE:
                    continue
                f = Path(m.group("file")).name if m.group("file").lower().endswith(f"{digest}.fx") else m.group("file")
                key = (f, m.group("line"), m.group("code"), m.group("msg"))
                if key in seen:
                    continue
                seen.add(key)
                msg = m.group("msg").strip()
                if len(variants) > 1:
                    msg += f" (IS_DEPTHBUFFER_RAWZ={v})"
                issues.append(Issue("error" if m.group("sev") == "error" else "warn", m.group("code"), f,
                                    int(m.group("line")), msg))
            if p.returncode != 0 and not any(i.sev == "error" for i in issues):
                tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or ["no message"]
                issues.append(Issue("error", "FXC", eff.path.name, 0, tail[0][:300]))
            if out.exists():
                out.unlink()
    finally:
        if src.exists():
            src.unlink()
    return {"ok": ok, "issues": issues, "seconds": round(time.perf_counter() - t0, 2), "variants": len(variants)}


# --------------------------------------------------------------------------- MTA checks

_SCALAR_FLOAT = {"float", "half", "double", "min16float", "min10float"}
_SCALAR_INT = {"int", "uint", "dword", "min16int", "min12int", "min16uint"}


def _shape(p: Param) -> str:
    t = p.type
    if p.is_texture:
        return "texture"
    if p.is_sampler:
        return "sampler"
    if t in _SCALAR_FLOAT:
        return "float"
    if t in _SCALAR_INT:
        return "int"
    if t == "bool":
        return "bool"
    if re.match(r"^(float|half|double)[1-4]x[1-4]$", t) or t == "matrix":
        return "matrix"
    if re.match(r"^(float|half|double)[1-4]$", t) or t == "vector":
        return "fvector"
    if re.match(r"^(int|uint|bool)[1-4]$", t):
        return "ivector"
    return t


#: Readable types (MTA ``EReadableAsType``) -> parameter shapes MTA maps (``TypeMappingList``).
_ACCEPTS = {"int": {"int"}, "d3dcolor": {"int", "fvector"}, "ifloat": {"float", "int"}, "float": {"float"},
            "d3dcolorvalue": {"fvector"}, "vector3": {"fvector"}, "matrix": {"matrix"}, "texture": {"texture"}}


def _state_annotation(p: Param, group: str, value: str, facts: dict) -> list[Issue]:
    out: list[Issue] = []
    info = facts["state_groups"][group]
    stage_part, sep, name = value.rpartition(",")
    name = name.strip()
    where = (p.file, p.line)
    if sep:
        if not info["stage"]:
            out.append(Issue("error", "STATE_STAGE", *where,
                             f"{p.name}: {group}={value!r}: this group takes no 'stage,' prefix; MTA ignores the "
                             "annotation and the parameter stays at its default"))
            return out
        st = stage_part.strip()
        if not st.isdigit() or int(st) > 7:
            out.append(Issue("error", "STATE_STAGE", *where,
                             f"{p.name}: {group}={value!r}: the stage must be 0..7; MTA ignores the annotation"))
            return out
    regs = {k.upper(): (k, v) for k, v in info["registers"].items()}
    hit = regs.get(name.upper())
    if hit is None:
        close = difflib.get_close_matches(name.upper(), list(regs), n=3, cutoff=0.6)
        out.append(Issue("error", "STATE_NAME", *where,
                         f"{p.name}: {group}={value!r}: no register {name!r} in {group}; MTA ignores the annotation"
                         + (f" (did you mean {', '.join(regs[c][0] for c in close)}?)" if close else "")))
        return out
    readable = hit[1]
    shape = _shape(p)
    if shape not in _ACCEPTS.get(readable, {shape}):
        want = {"int": "int", "d3dcolor": "int or float4", "ifloat": "float or int", "float": "float",
                "d3dcolorvalue": "float4", "vector3": "float3", "matrix": "row_major float4x4",
                "texture": "texture"}[readable]
        out.append(Issue("error", "STATE_TYPE", *where,
                         f"{p.name}: {group}={hit[0]} is read as {want}, the parameter is {p.type}; MTA "
                         "ignores the annotation"))
    elif readable == "matrix" and not p.row_major:
        out.append(Issue("warn", "STATE_TYPE", *where,
                         f"{p.name}: {group}={hit[0]}: MTA maps matrices of row-major class only; declare it "
                         "'row_major float4x4' or use a semantic (WORLD, VIEW, PROJECTION ...)"))
    return out


def mta_checks(eff: Effect) -> tuple[list[Issue], dict]:
    """MTA-specific findings and a summary of who sets each parameter."""
    facts = mta_facts()
    groups = facts["state_groups"]
    lower_groups = {g.lower(): g for g in groups}
    semantics = set(facts["semantics"])
    profiles = facts["profiles"]
    out: list[Issue] = []
    main = eff.path.name
    by_name = {p.name: p for p in eff.params}
    mta_set: list[str] = []
    lua_tex: list[str] = []
    tunable: list[str] = []

    # ---- techniques
    if not eff.techniques and not any(i.code == "SYNTAX" for i in eff.issues):   # else a brace swallowed them
        out.append(Issue("error", "NO_TECHNIQUE", main, 0, "no technique: dxCreateShader fails with "
                                                           "'No valid technique'"))
    used_ps3 = False
    shader_techs = 0
    for t in eff.techniques:
        if t.keyword != "technique":
            out.append(Issue("error", "D3D10", t.file, t.line,
                             f"{t.keyword} {t.name}: MTA renders with Direct3D 9; use 'technique'"))
        if not t.passes:
            out.append(Issue("warn", "NO_PASS", t.file, t.line, f"technique {t.name!r} has no pass: nothing is drawn"))
        uses_shaders = False
        for ps in t.passes:
            for stage, target in (("vs", ps.vs), ("ps", ps.ps)):
                if not target:
                    continue
                uses_shaders = True
                prof, fn = target
                if prof not in profiles[stage]:
                    out.append(Issue("error", "PROFILE", t.file, ps.line,
                                     f"{t.name}/{ps.name}: {prof} is not a Direct3D 9 {stage} profile (MTA accepts "
                                     f"{', '.join(profiles[stage][1:5])} ...)"))
                elif prof in ("ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4", "vs_1_1"):
                    out.append(Issue("warn", "PROFILE", t.file, ps.line,
                                     f"{t.name}/{ps.name}: {prof} is a legacy model; use {stage}_2_0"))
                if prof.startswith("ps_3"):
                    used_ps3 = True
                if fn not in eff.functions:
                    close = difflib.get_close_matches(fn, list(eff.functions), n=2)
                    out.append(Issue("error", "UNKNOWN_FUNCTION", t.file, ps.line,
                                     f"{t.name}/{ps.name}: compile {prof} {fn}(): no such function"
                                     + (f" (did you mean {', '.join(close)}?)" if close else "")))
            for key, val, line in ps.states:
                if re.match(r"(?i)^texture(\[\d+\])?$", key):
                    name = re.sub(r"[()<>\s]", "", val)
                    p = by_name.get(name)
                    if name.lower() not in ("null", "0") and (p is None or not p.is_texture):
                        out.append(Issue("error", "TEXTURE_UNDECLARED", t.file, line,
                                         f"{t.name}/{ps.name}: {key} = {val}: {name!r} is not a declared texture"))
        shader_techs += uses_shaders
    if eff.techniques and shader_techs == len(eff.techniques) and shader_techs:
        out.append(Issue("info", "NO_FALLBACK", main, 0,
                         "every technique uses shaders: add a last 'technique fallback { pass P0 {} }' so GPUs that "
                         "cannot run them fall back instead of failing dxCreateShader"))

    # ---- parameters
    has_fog_param = False
    for p in eff.params:
        if p.is_sampler:
            m = re.search(r"(?i)\btexture\s*=\s*[(<]?\s*([A-Za-z_]\w*)", p.init or "")
            if m:
                tp = by_name.get(m.group(1))
                if tp is None or not tp.is_texture:
                    out.append(Issue("error", "TEXTURE_UNDECLARED", p.file, p.line,
                                     f"sampler {p.name}: Texture = {m.group(1)} is not a declared texture"))
            continue
        auto = False
        if p.key == "CUSTOMFLAGS":
            for k, v in p.annotations.items():
                if k not in ("createNormals", "skipUnusedParameters"):
                    out.append(Issue("warn", "CUSTOMFLAGS", p.file, p.line,
                                     f"CUSTOMFLAGS: unknown flag {k!r} (MTA knows createNormals, "
                                     "skipUnusedParameters)"))
                elif v not in ("yes", "no"):
                    out.append(Issue("warn", "CUSTOMFLAGS", p.file, p.line, f"CUSTOMFLAGS {k}={v!r}: use \"yes\""))
            continue
        for ak, av in p.annotations.items():
            if ak in groups:
                found = _state_annotation(p, ak, av, facts)
                auto = auto or not any(i.sev == "error" for i in found)
                out += found
                if ak == "renderState" and av.upper() in ("FOGCOLOR", "FOGENABLE"):
                    has_fog_param = True
            elif ak.lower() in lower_groups:
                out.append(Issue("error", "STATE_GROUP", p.file, p.line,
                                 f"{p.name}: annotation {ak!r}: MTA matches the group name exactly, write "
                                 f"{lower_groups[ak.lower()]!r}; as written the parameter is never set"))
            elif ak == "renderTarget":
                auto = True
        if p.key in semantics:
            auto = True
            if p.key == "DEPTHBUFFER":
                out.append(Issue("warn", "DEPTHBUFFER", p.file, p.line,
                                 f"{p.name}: DEPTHBUFFER needs a readable depth buffer, which some GPUs lack: MTA "
                                 "then rejects the technique; keep a fallback technique without it"))
        elif p.semantic and p.semantic.upper() not in semantics:
            close = difflib.get_close_matches(p.semantic.upper(), list(semantics), n=1, cutoff=0.8)
            if close:
                out.append(Issue("warn", "SEMANTIC", p.file, p.line,
                                 f"{p.name} : {p.semantic}: not an MTA semantic, MTA will not set it (did you mean "
                                 f"{close[0]}?)"))
        if auto:
            mta_set.append(p.name)
        elif p.static:
            continue   # not an effect parameter
        elif p.is_texture:
            lua_tex.append(p.name)
        else:
            tunable.append(p.name)
    if used_ps3 and not has_fog_param:
        out.append(Issue("warn", "SM3_FOG", main, 0,
                         "ps_3_0 gets no fixed-function fog from Direct3D 9: blend fog in the pixel shader "
                         "(renderState FOGENABLE, FOGCOLOR, FOGSTART, FOGEND) or the shader ignores the game's fog"))
    summary = {"mta": mta_set, "lua_textures": lua_tex, "values": tunable}
    return out, summary


# --------------------------------------------------------------------------- Lua


def strip_lua_comments(text: str) -> str:
    """Lua source with comments blanked (line breaks kept); strings stay."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("--", i):
            m = re.match(r"--\[(=*)\[", text[i:])
            if m:
                close = "]" + m.group(1) + "]"
                j = text.find(close, i)
                j = n if j < 0 else j + len(close)
            else:
                j = text.find("\n", i)
                j = n if j < 0 else j
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        elif c == "[" and re.match(r"\[(=*)\[", text[i:]):
            m = re.match(r"\[(=*)\[", text[i:])
            close = "]" + m.group(1) + "]"
            j = text.find(close, i)
            j = n if j < 0 else j + len(close)
            out.append(text[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


_STR = r"""["']([^"'\n]*)["']"""
_STR_ESC = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\'')
_CREATE = re.compile(r"(?:local\s+)?([A-Za-z_][\w.]*)\s*=\s*(?:dxCreateShader|DxShader)\s*\(\s*"
                     r"(?:" + _STR + r"|[^,)\n]*)([^)\n]*)\)")
_CREATE_ANY = re.compile(r"\b(?:dxCreateShader|DxShader)\s*\(")
_FIRST_ARG = re.compile(r"(?:dxCreateShader|DxShader)\s*\(\s*([A-Za-z_]\w*)")
_CONST = re.compile(r"\blocal\s+([A-Za-z_]\w*)\s*=\s*" + _STR)
_SETVAL = re.compile(r"\bdxSetShaderValue\s*\(\s*([A-Za-z_][\w.]*)\s*,\s*" + _STR)
_SETVAL_OOP = re.compile(r"\b([A-Za-z_][\w.]*)\s*:\s*setValue\s*\(\s*" + _STR)
_APPLY = re.compile(r"\bengine(Apply|Remove)Shader(?:To|From)WorldTexture\s*\(\s*([A-Za-z_][\w.]*)\s*,\s*" + _STR)
_APPLY_OOP = re.compile(r"\b([A-Za-z_][\w.]*)\s*:\s*(apply|remove)(?:To|From)WorldTexture\s*\(\s*" + _STR)
_APPLY_LOOP = re.compile(r"\bengine(Apply|Remove)Shader(?:To|From)WorldTexture\s*\(\s*([A-Za-z_][\w.]*)\s*,\s*"
                         r"([A-Za-z_]\w*)\s*[,)]")


@dataclass
class LuaRefs:
    files: list[Path] = field(default_factory=list)
    shaders: dict[str, list[tuple[str, str, int, str]]] = field(default_factory=dict)  # var -> (fx, file, line, args)
    values: list[tuple[str, str, str, int]] = field(default_factory=list)              # (var, name, file, line)
    patterns: list[tuple[str, str, str, str, int]] = field(default_factory=list)       # (op, var, pattern, file, line)
    creates: int = 0


def client_scripts(root: Path, fx: Path) -> list[Path]:
    """Client/shared scripts of the resource (``meta.xml``), else the ``.lua`` files next to ``fx``."""
    meta = root / "meta.xml"
    if meta.is_file():
        try:
            import xml.etree.ElementTree as ET

            tree = ET.parse(meta)
            out = []
            for s in tree.getroot().iter("script"):
                if (s.get("type") or "server").lower() in ("client", "shared") and s.get("src"):
                    p = (root / s.get("src")).resolve()
                    if p.is_file():
                        out.append(p)
            return out
        except Exception:  # noqa: BLE001 - a broken meta.xml falls back to the folder
            pass
    return sorted(fx.parent.glob("*.lua"))


def _tables(text: str) -> dict[str, list[str]]:
    """``local NAME = { "a", "b" }`` string tables (pattern lists of a loop)."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"(?:local\s+)?([A-Za-z_]\w*)\s*=\s*\{([^{}]*)\}", text):
        body = m.group(2)
        items = [re.sub(r"\\(.)", r"\1", a or b) for a, b in _STR_ESC.findall(body)]
        if items and not _STR_ESC.sub("", body).replace(",", "").strip():
            out[m.group(1)] = items
    return out


def lua_refs(files: list[Path]) -> LuaRefs:
    refs = LuaRefs(files=list(files))
    for f in files:
        try:
            text = strip_lua_comments(f.read_bytes().decode("utf-8", errors="replace"))
        except OSError:
            continue
        name = f.name

        def ln(pos: int) -> int:
            return text.count("\n", 0, pos) + 1

        refs.creates += len(_CREATE_ANY.findall(text))
        consts = dict(_CONST.findall(text))
        for m in _CREATE.finditer(text):
            fx_name = m.group(2)
            if fx_name is None:   # dxCreateShader(FX_FILE, ...) with local FX_FILE = "x.fx"
                arg = _FIRST_ARG.search(text, m.start())
                fx_name = consts.get(arg.group(1), "") if arg else ""
            # local string constants in the other arguments (ELEMENT_TYPES = "world,object") read as literals
            args = re.sub(r"\b([A-Za-z_]\w*)\b", lambda a: f'"{consts[a.group(1)]}"' if a.group(1) in consts
                          else a.group(1), m.group(3) or "")
            refs.shaders.setdefault(m.group(1), []).append((fx_name, name, ln(m.start()), args))
        for m in _SETVAL.finditer(text):
            refs.values.append((m.group(1), m.group(2), name, ln(m.start())))
        for m in _SETVAL_OOP.finditer(text):
            refs.values.append((m.group(1), m.group(2), name, ln(m.start())))
        found: list[tuple[str, str, str, str, int]] = []
        for m in _APPLY.finditer(text):
            found.append((m.group(1).lower(), m.group(2), m.group(3), name, ln(m.start())))
        for m in _APPLY_OOP.finditer(text):
            found.append((m.group(2).lower(), m.group(1), m.group(3), name, ln(m.start())))
        tables = _tables(text)
        for m in _APPLY_LOOP.finditer(text):
            # for _, p in ipairs(LIST) do engineApplyShaderToWorldTexture(shader, p) end
            var = m.group(3)
            loops = list(re.finditer(r"for\s+[\w\s,]*\b" + re.escape(var) +
                                     r"\s+in\s+i?pairs\s*\(\s*([A-Za-z_]\w*)\s*\)",
                                     text[max(0, m.start() - 300):m.start()]))
            loop = loops[-1] if loops else None   # the nearest enclosing loop
            if loop and loop.group(1) in tables:
                for pat in tables[loop.group(1)]:
                    found.append((m.group(1).lower(), m.group(2), pat, name, ln(m.start())))
        refs.patterns += sorted(found, key=lambda r: r[4])   # call order (stable: table order within a loop)
    return refs


def _fx_matches(created: str, eff: Effect) -> bool:
    c = created.replace("\\", "/").lstrip(":@").split("/")[-1].lower()
    return c == eff.path.name.lower()


def lua_checks(eff: Effect, refs: LuaRefs, *, universe: list[str] | None = None) -> tuple[list[Issue], dict]:
    """Findings from the resource's Lua and a summary (linked shader variables, values set, patterns)."""
    from . import wild

    facts = mta_facts()
    out: list[Issue] = []
    vars_ = {v for v, defs in refs.shaders.items() if any(_fx_matches(d[0], eff) for d in defs)}
    linked = "file name"
    if not vars_ and refs.creates == 1 and len(refs.shaders) == 1:
        vars_ = set(refs.shaders)
        linked = "the only dxCreateShader"
    groups = facts["state_groups"]
    # Annotation-mapped parameters and CUSTOMFLAGS never reach MTA's name maps: dxSetShaderValue cannot set them.
    mapped = {p.name.upper(): p for p in eff.params if any(a in groups for a in p.annotations)}
    keys = {k: p for k, p in eff.keys().items() if p.name.upper() not in mapped and p.key != "CUSTOMFLAGS"}
    by_name = {p.name.upper(): p for p in eff.params}
    semantics = set(facts["semantics"])
    set_names: set[str] = set()
    if not vars_:
        return out, {"linked": "none: no dxCreateShader of this file in the client scripts"}
    for v in sorted(vars_):
        for fxname, f, line, args in refs.shaders[v]:
            m = re.search(r",\s*" + _STR + r"\s*$", args or "")
            if m and _fx_matches(fxname, eff):
                types = [t.strip() for t in m.group(1).split(",") if t.strip()]
                bad = [t for t in types if t not in facts["element_types"]]
                if bad:
                    out.append(Issue("error", "LUA_ELEMENT_TYPES", f, line,
                                     f"dxCreateShader element types {', '.join(bad)} unknown (MTA: "
                                     f"{', '.join(facts['element_types'])})"))
    shown: dict[str, str] = {}
    for var, name, f, line in refs.values:
        if var not in vars_:
            continue
        k = name.upper()
        set_names.add(k)
        shown.setdefault(k, name)
        if k in keys:
            p = keys[k]
            if p.key in semantics:
                out.append(Issue("warn", "LUA_OVERRIDES_MTA", f, line,
                                 f'dxSetShaderValue(..., "{name}"): MTA sets {p.name} ({p.key}) itself before every '
                                 "draw, the value is overwritten"))
            continue
        p = by_name.get(k)
        if k in mapped:
            ann = next(f"{a}={v!r}" for a, v in mapped[k].annotations.items() if a in groups)
            out.append(Issue("error", "LUA_UNKNOWN_PARAM", f, line,
                             f'dxSetShaderValue(..., "{name}"): {mapped[k].name} is filled by MTA from {ann} and is '
                             "not settable (the call returns false); drop the annotation to set it from Lua"))
        elif p is not None and p.semantic:
            out.append(Issue("error", "LUA_UNKNOWN_PARAM", f, line,
                             f'dxSetShaderValue(..., "{name}"): {p.name} has the semantic {p.semantic}, and MTA '
                             f'looks parameters up by their semantic: use "{p.semantic}" or drop the semantic'))
        else:
            close = difflib.get_close_matches(k, list(keys), n=2, cutoff=0.6)
            out.append(Issue("error", "LUA_UNKNOWN_PARAM", f, line,
                             f'dxSetShaderValue(..., "{name}"): the effect has no such parameter, the call returns '
                             "false" + (f" (did you mean {', '.join(keys[c].semantic or keys[c].name for c in close)}?)"
                                        if close else "")))
    for p in eff.params:
        if p.is_texture and p.key not in semantics and not p.annotations and p.key not in set_names:
            used = any(re.search(r"(?i)\btexture\s*=\s*[(<]?\s*" + re.escape(p.name) + r"\b", s.init or "")
                       for s in eff.params if s.is_sampler) or any(
                re.sub(r"[()<>\s]", "", val) == p.name for t in eff.techniques for ps in t.passes
                for key, val, _l in ps.states)
            if used:
                out.append(Issue("warn", "LUA_TEXTURE_NOT_SET", p.file, p.line,
                                 f"texture {p.name} is used but never set with dxSetShaderValue: it samples as black"))
    pats = [(op, pat, f, line) for op, var, pat, f, line in refs.patterns if var in vars_]
    prow: list[list] = []
    if universe is not None:
        for op, pat, f, line in pats:
            n = len(wild.match_names(pat, universe))
            prow.append([op, pat, n])
            if n == 0:
                out.append(Issue("warn", "NO_MATCH", f, line,
                                 f'{op} "{pat}": matches no texture name of the profile (typo?)'))
    else:
        prow = [[op, pat, None] for op, pat, _f, _l in pats]
    info = {"linked": f"{', '.join(sorted(vars_))} ({linked})", "values_set": sorted(shown.values()),
            "patterns": prow[:40]}
    if len(prow) > 40:
        info["patterns_total"] = len(prow)
    if universe is not None and pats:
        # what the shader finally covers: MTA's last-match-wins chain over every texture name of the profile
        info["textures"] = len(wild.resolve([(op, pat) for op, pat, _f, _l in pats], universe))
    return out, info
