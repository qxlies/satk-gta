"""Shared helpers of satk.engine: layout, tool discovery, processes, hashing, state, locks.

Stdlib only. Nothing here writes into ``src`` or the game: every writer goes through
``satk.core.paths.ensure_writable``; git commands against the donor clone in ``src`` use
``--no-optional-locks`` (no index refresh) and ``GIT_NO_LAZY_FETCH=1`` (no network).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath, tmp, work

__all__ = [
    "UPSTREAM_URL",
    "UPLOADPACK",
    "DONOR_UPSTREAM_REF",
    "LOCAL_UPSTREAM_REF",
    "EXPECTED_BASE",
    "Layout",
    "layout",
    "fork_id_of",
    "fork_profile",
    "same_path",
    "git_raw",
    "find_msbuild",
    "find_premake",
    "find_rc",
    "find_git",
    "RunResult",
    "run",
    "git",
    "donor_git",
    "sha256_file",
    "read_json",
    "write_json",
    "build_env",
    "exclusive",
    "pid_alive",
    "disk_free_gb",
    "file_url",
    "tail",
]

#: GitHub upstream (added as remote ``upstream``; never fetched without consent D4).
UPSTREAM_URL = "https://github.com/multitheftauto/mtasa-blue.git"
#: upload-pack command that lets a blobless local clone serve filters and lazy blobs (V3/V4).
UPLOADPACK = "git -c uploadpack.allowFilter=true -c uploadpack.allowAnySHA1InWant=true upload-pack"
#: Ref in the donor ``src\mtasa-neon`` that tracks upstream mtasa-blue master.
DONOR_UPSTREAM_REF = "refs/remotes/upstream/master"
#: Where the fork keeps the fetched donor ref (``main`` was created from it).
LOCAL_UPSTREAM_REF = "refs/remotes/upstream-local/master"
#: mtasa-blue master at setup time (SPEC §4.12.1, report 28).
EXPECTED_BASE = "715ec056da6a61638bbd51c3d0d3fdc8f528a2a2"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------------------- layout


@dataclass(frozen=True)
class Layout:
    """All engine paths, derived from ``[paths] engine`` (= ``<workspace>/engine/mtasa``).

    ``fork_id`` is empty for the configured fork. A second checkout of the fork (``--fork PATH``,
    usually a worktree under ``work/wt``) shares ``root`` (premake wrapper, ``deps``, shims) and
    ``build_dir`` with it but has its own logs, build state and lock, keyed by ``fork_id``.
    """

    fork: Path
    root: Path
    donor: Path
    build_dir: Path  # work/engine/build (logs, state, rc-test output)
    fork_id: str = ""

    @property
    def is_default(self) -> bool:
        return not self.fork_id

    @property
    def meta(self) -> Path:
        """Registry entry written by ``engine worktree create`` (profile, branch, base)."""
        return self.build_dir.parent / "forks" / f"{self.fork_id}.json"

    @property
    def lock_name(self) -> str:
        """Name of the build lock (``exclusive``): one per fork, so two forks may build side by side."""
        return "build" if self.is_default else f"build-{self.fork_id}"

    @property
    def tmp_id(self) -> str:
        """``paths.tmp`` id of the build (TEMP/TMP of premake and MSBuild)."""
        return "engine-build" if self.is_default else f"engine-build-{self.fork_id}"

    @property
    def shims(self) -> Path:
        return self.root / "shims"

    @property
    def afxres(self) -> Path:
        return self.shims / "afxres.h"

    @property
    def targets(self) -> Path:
        return self.root / "Directory.Build.targets"

    @property
    def wrapper(self) -> Path:
        return self.root / "satk-premake.lua"

    @property
    def bootstrap(self) -> Path:
        return self.root / "bootstrap.ps1"

    @property
    def deps(self) -> Path:
        return self.root / "deps"

    @property
    def cache(self) -> Path:
        return self.deps / "cache"

    @property
    def net(self) -> Path:
        return self.deps / "net"

    @property
    def dxfiles(self) -> Path:
        return self.deps / "DXFiles"

    @property
    def lock(self) -> Path:
        return self.root / "deps-lock.json"

    @property
    def sln(self) -> Path:
        return self.fork / "Build" / "MTASA.sln"

    @property
    def bin(self) -> Path:
        return self.fork / "Bin"

    @property
    def logs(self) -> Path:
        return self.build_dir / "logs" if self.is_default else self.build_dir / "logs" / self.fork_id

    @property
    def state(self) -> Path:
        return self.build_dir / ("state.json" if self.is_default else f"state.{self.fork_id}.json")


def _norm(p: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(p))).rstrip("\\/")


def same_path(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    """True when two spellings name the same directory (case, slashes, trailing separator)."""
    return _norm(a) == _norm(b)


_SAFE_ID = re.compile(r"[^a-z0-9_.-]+")


def fork_id_of(path: str | os.PathLike, wt_root: str | os.PathLike | None = None) -> str:
    """Stable id of a second fork checkout, used in log folders, state and lock names.

    A checkout directly under ``work/wt`` is named by its folder (``work/wt/sae2-srv`` -> ``sae2-srv``);
    any other path gets a short hash of the full path appended, so two folders with one name never
    share logs or build state.
    """
    p = Path(os.path.abspath(os.fspath(path)))
    name = _SAFE_ID.sub("-", p.name.lower()).strip("-.") or "fork"
    if wt_root is not None and same_path(p.parent, wt_root):
        return name
    return f"{name}-{hashlib.sha1(_norm(p).encode('utf-8')).hexdigest()[:6]}"  # noqa: S324 - naming only


def layout(fork: str | os.PathLike | None = None) -> Layout:
    """Current engine layout from the configuration (no directories are created).

    ``fork`` selects another checkout of the same fork (``--fork PATH``); ``None`` or the configured
    path gives the default layout, unchanged. The path is not required to exist here.
    """
    c = cfg()
    base = Path(os.path.abspath(c.paths.engine))
    src = c.paths.get("src") or (Path(c.paths.workspace) / "src")
    work_dir = Path(os.path.abspath(c.paths.work))
    default = Layout(
        fork=base,
        root=base.parent,
        donor=Path(os.path.abspath(src)) / "mtasa-neon",
        build_dir=work_dir / "engine" / "build",
    )
    if fork is None or str(fork).strip() == "":
        return default
    other = Path(os.path.abspath(os.fspath(fork)))
    if same_path(other, base):
        return default
    return Layout(fork=other, root=default.root, donor=default.donor, build_dir=default.build_dir,
                  fork_id=fork_id_of(other, work_dir / "wt"))


def fork_profile(L: Layout) -> str | None:
    """Sparse profile recorded by ``engine worktree create`` for a second fork (``None``: a full checkout,
    including the configured fork)."""
    if L.is_default:
        return None
    try:
        data = read_json(L.meta, {}) or {}
    except SatkError:
        return None
    return data.get("profile") if isinstance(data, dict) else None


def file_url(p: Path) -> str:
    """``file:///C:/ws/src/mtasa-neon`` for a local path."""
    return "file:///" + jpath(p)


# --------------------------------------------------------------------------- tools


def find_git() -> Path | None:
    g = shutil.which("git")
    return Path(g) if g else None


def find_msbuild() -> Path | None:
    """``[paths] msbuild`` if it exists, else ``vswhere -products *`` (Build Tools are not
    found without ``-products *``, report 28 §3)."""
    p = cfg().paths.get("msbuild")
    if p and Path(p).is_file():
        return Path(p)
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(pf86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.is_file():
        try:
            out = subprocess.run(
                [str(vswhere), "-latest", "-products", "*", "-requires", "Microsoft.Component.MSBuild",
                 "-find", r"MSBuild\**\Bin\MSBuild.exe"],
                capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
            ).stdout.strip().splitlines()
            for line in out:
                if line.strip() and Path(line.strip()).is_file():
                    return Path(line.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def find_premake(fork: Path | None = None) -> Path | None:
    """The fork's own ``utils\\premake5.exe`` (same as upstream CI), else ``[paths] premake``."""
    fork = fork or layout().fork
    own = fork / "utils" / "premake5.exe"
    if own.is_file():
        return own
    p = cfg().paths.get("premake")
    return Path(p) if p and Path(p).is_file() else None


def _kits_root() -> Path:
    default = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10"
    try:
        import winreg

        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows Kits\Installed Roots",
                                    0, winreg.KEY_READ | view) as k:
                    v, _ = winreg.QueryValueEx(k, "KitsRoot10")
                    if v and Path(v).is_dir():
                        return Path(v)
            except OSError:
                continue
    except ImportError:  # pragma: no cover - non-Windows
        pass
    return default


def _ver_key(name: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in name.split("."))
    except ValueError:
        return (0,)


@dataclass(frozen=True)
class RcTool:
    exe: Path
    version: str
    includes: tuple[Path, ...]


def find_msvc_include() -> Path | None:
    """``<VS>\\VC\\Tools\\MSVC\\<newest>\\include`` next to the configured MSBuild."""
    m = find_msbuild()
    if m is None:
        return None
    try:
        vs = m.parents[3]  # <VS>\MSBuild\Current\Bin\MSBuild.exe
    except IndexError:
        return None
    msvc = vs / "VC" / "Tools" / "MSVC"
    if not msvc.is_dir():
        return None
    for ver in sorted((d.name for d in msvc.iterdir() if d.is_dir()), key=_ver_key, reverse=True):
        inc = msvc / ver / "include"
        if (inc / "vcruntime.h").is_file():
            return inc
    return None


def find_rc() -> RcTool | None:
    """Newest Windows SDK ``rc.exe`` with the include directories MSBuild would pass
    (SDK ``um``/``shared``/``ucrt`` and the MSVC ``include``: ``Multi Theft Auto.rc`` pulls
    ``string.h`` through ``gameux.h``)."""
    root = _kits_root()
    bindir = root / "bin"
    if not bindir.is_dir():
        return None
    for ver in sorted((d.name for d in bindir.iterdir() if d.is_dir() and d.name[:1].isdigit()),
                      key=_ver_key, reverse=True):
        for arch in ("x64", "x86"):
            exe = bindir / ver / arch / "rc.exe"
            inc = root / "Include" / ver
            if exe.is_file() and (inc / "um" / "winres.h").is_file():
                incs = [inc / "um", inc / "shared", inc / "ucrt"]
                vc = find_msvc_include()
                if vc is not None:
                    incs.append(vc)
                return RcTool(exe, ver, tuple(p for p in incs if p.is_dir()))
    return None


# --------------------------------------------------------------------------- processes


@dataclass
class RunResult:
    cmd: list[str]
    code: int
    seconds: float
    log: Path | None = None
    out: str = ""
    timed_out: bool = False
    extra: dict = field(default_factory=dict)


def tail(text: str, n: int = 30) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=30,
                       creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        pass


def run(cmd: Sequence[str], *, cwd: Path | None = None, env: dict | None = None, log: Path | None = None,
        timeout: float | None = None, input: bytes | None = None, keep: int = 4000) -> RunResult:  # noqa: A002
    """Run a command; stdout+stderr go to ``log`` (if given) and the last ``keep`` chars to ``out``.

    On timeout the whole process tree is killed (``taskkill /T``: MSBuild spawns nodes).
    """
    cmd = [str(c) for c in cmd]
    t0 = time.perf_counter()
    if log is not None:
        ensure_writable(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        fh = open(log, "wb")  # noqa: SIM115
        stdout = fh
    else:
        fh = None
        stdout = subprocess.PIPE
    timed_out = False
    try:
        try:
            p = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env, stdout=stdout,
                                 stderr=subprocess.STDOUT, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                 creationflags=_NO_WINDOW)
        except OSError as e:
            raise SatkError("EXTERNAL_TOOL", f"cannot start {cmd[0]}: {e}") from None
        try:
            data, _ = p.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(p.pid)
            data, _ = p.communicate()
        code = p.returncode
    finally:
        if fh is not None:
            fh.close()
    if log is not None:
        try:
            with open(log, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - keep))
                data = f.read()
        except OSError:
            data = b""
    text = (data or b"").decode("utf-8", errors="replace")[-keep:]
    return RunResult(cmd=cmd, code=code if code is not None else -1, seconds=round(time.perf_counter() - t0, 2),
                     log=log, out=text, timed_out=timed_out)


def _git_env(no_lazy: bool) -> dict:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    if no_lazy:
        env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def git(*args: str, cwd: Path | None = None, check: bool = True, timeout: float = 600,
        no_lazy: bool = False) -> str:
    """``git <args>`` in ``cwd`` (default: the fork). Returns stdout (stripped).

    ``check`` raises ``EXTERNAL_TOOL`` with stderr on a non-zero exit.
    """
    g = find_git()
    if g is None:
        raise SatkError("DEPENDENCY", "git not found on PATH", hint="install Git for Windows")
    cwd = cwd or layout().fork
    try:
        p = subprocess.run([str(g), *args], cwd=str(cwd), capture_output=True, timeout=timeout,
                           env=_git_env(no_lazy), creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise SatkError("TIMEOUT", f"git {' '.join(args[:3])} timed out after {timeout:.0f}s") from None
    out = p.stdout.decode("utf-8", errors="replace").strip()
    if check and p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="replace").strip()
        raise SatkError("EXTERNAL_TOOL", f"git {' '.join(args)} failed ({p.returncode}): {err[-800:]}",
                        data={"cwd": jpath(cwd)})
    return out


def git_raw(*args: str, cwd: Path, input: bytes | None = None, check: bool = True,  # noqa: A002
            timeout: float = 600) -> bytes:
    """``git <args>`` with bytes on stdin and bytes back; always ``GIT_NO_LAZY_FETCH=1`` (never reaches a remote)."""
    g = find_git()
    if g is None:
        raise SatkError("DEPENDENCY", "git not found on PATH", hint="install Git for Windows")
    try:
        p = subprocess.run([str(g), *args], cwd=str(cwd), capture_output=True, timeout=timeout, input=input,
                           env=_git_env(True), creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise SatkError("TIMEOUT", f"git {' '.join(args[:3])} timed out after {timeout:.0f}s") from None
    if check and p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="replace").strip()
        raise SatkError("EXTERNAL_TOOL", f"git {' '.join(args)} failed ({p.returncode}): {err[-800:]}",
                        data={"cwd": jpath(cwd)})
    return p.stdout


def donor_git(*args: str, check: bool = True) -> str:
    """Read-only git in the donor ``src\\mtasa-neon``: no optional locks, no lazy fetch."""
    return git("--no-optional-locks", *args, cwd=layout().donor, check=check, no_lazy=True)


# --------------------------------------------------------------------------- files


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as e:
        raise SatkError("BAD_PARAMS", f"cannot read {jpath(path)}: {e}", hint="fix or delete the file") from None


def write_json(path: Path, data) -> Path:
    return atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n")


def disk_free_gb(path: Path) -> float:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    return round(shutil.disk_usage(p).free / 1e9, 1)


def build_env(extra: dict | None = None, L: Layout | None = None) -> dict:
    """Environment for premake/MSBuild: TEMP/TMP on D: (R17), no MSBuild node reuse,
    ``DXSDK_DIR`` = ``engine\\deps\\DXFiles\\`` (premake bakes it into the projects).

    ``L`` (a second fork) gives the build its own TEMP/TMP folder; without it nothing changes."""
    env = os.environ.copy()
    t = str(tmp("engine-build" if L is None else L.tmp_id))
    env["TEMP"] = t
    env["TMP"] = t
    env["MSBUILDDISABLENODEREUSE"] = "1"
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    env["DXSDK_DIR"] = str((layout() if L is None else L).dxfiles) + "\\"
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


# --------------------------------------------------------------------------- locks


def pid_alive(pid: int) -> bool:
    """True if a process with this id is running (Windows: OpenProcess + exit code)."""
    if pid <= 0:
        return False
    if os.name != "nt":  # pragma: no cover
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


@contextmanager
def exclusive(name: str) -> Iterator[Path]:
    """Inter-process lock ``work/engine/build/.<name>.lock``; ``BUSY`` if a live process holds it."""
    d = work("engine", "build")
    lockf = d / f".{name}.lock"
    for _ in range(2):
        try:
            fd = os.open(lockf, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid = int(lockf.read_text(encoding="utf-8").split()[0])
            except (OSError, ValueError, IndexError):
                pid = -1
            if pid != os.getpid() and pid_alive(pid):
                raise SatkError("BUSY", f"another engine {name} is running (pid {pid})",
                                hint=f"wait for it or delete {jpath(lockf)} if that process is gone",
                                data={"pid": pid}) from None
            try:
                lockf.unlink()
            except OSError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        try:
            yield lockf
        finally:
            try:
                lockf.unlink()
            except OSError:
                pass
        return
    raise SatkError("BUSY", f"cannot take lock {jpath(lockf)}")


_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_name(s: str) -> str:
    """File-name friendly form of a project/target name (``Game SA`` -> ``Game_SA``)."""
    return _SAFE.sub("_", s).strip("_") or "x"


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")
