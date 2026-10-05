"""Discovery of external tools and game installs (``docs/en/install.md``). stdlib only.

Values in ``satk.toml`` (or ``SATK_PATHS_*``) always win; this module only fills what is not
configured (:data:`satk.core.config.DETECTED_PATHS`) and lists game candidates for ``satk init``.
Every finder returns a :class:`Hit` ``(path, source)``; ``path`` is ``None`` when nothing was found.
The Windows registry is read with ``winreg``; on other systems those steps are no-ops.

* :func:`blender` -- ``HKLM\\SOFTWARE\\BlenderFoundation`` (``InstallDir``), then the newest
  ``Program Files\\Blender Foundation\\Blender *\\blender.exe``, then ``PATH``;
* :func:`msbuild` / :func:`vcvars` -- ``vswhere.exe -products * -prerelease -latest`` (standard
  location under ``Program Files (x86)\\Microsoft Visual Studio\\Installer``), then ``PATH``;
* :func:`ariane` -- ``paths.viewer`` when the file exists (the viewer is optional);
* :func:`game_installs` -- candidates from the config, the workspace, the Rockstar registry key,
  Steam libraries (``libraryfolders.vdf``), typical folders and the current directory, each with
  the executable variant from ``data/exe_versions.json`` (:func:`identify_exe`);
* :func:`edition` -- classic PC game, or an unsupported edition (The Definitive Edition, the
  mobile port), which is listed as a candidate with a problem instead of being skipped silently.

:func:`tools` caches blender/msbuild/vcvars in ``<work>/cache/detect.json`` (TTL one day; a hit
whose file disappeared is re-detected at once).
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, NamedTuple

__all__ = [
    "Hit",
    "GameInstall",
    "blender",
    "msbuild",
    "vcvars",
    "ariane",
    "vswhere_exe",
    "tools",
    "game_installs",
    "check_game",
    "edition",
    "EDITION_LABELS",
    "identify_exe",
    "classify_exe",
    "exe_versions",
    "parse_vdf",
    "steam_libraries",
    "EXE_NAMES",
    "CACHE_TTL",
]

#: Executable names of a GTA:SA folder (``gta-sa.exe`` = Steam/newsteam builds, accepted by MTA too).
EXE_NAMES: tuple[str, ...] = ("gta_sa.exe", "gta-sa.exe")
#: Seconds a cached discovery result stays valid.
CACHE_TTL = 24 * 3600
_CACHE_FORMAT = "satk.detect/1"
_STEAM_DIRS = ("Grand Theft Auto San Andreas", "GTA San Andreas - Definitive Edition")
_TYPICAL = (
    ("ProgramFiles(x86)", ("Rockstar Games", "GTA San Andreas")),
    ("ProgramFiles", ("Rockstar Games", "GTA San Andreas")),
    ("ProgramFiles", ("Rockstar Games", "Grand Theft Auto San Andreas")),
    ("ProgramFiles(x86)", ("Steam", "steamapps", "common", "Grand Theft Auto San Andreas")),
    ("ProgramFiles", ("Rockstar Games", "GTA San Andreas - Definitive Edition")),
)
#: Human names of the editions satk does not read (:func:`edition`).
EDITION_LABELS = {"definitive": "GTA San Andreas - The Definitive Edition",
                  "mobile": "the mobile port of GTA San Andreas (Android/iOS data)"}


class Hit(NamedTuple):
    """A discovered path (``None`` if not found) and how: registry|vswhere|program_files|path|config."""

    path: Path | None
    source: str | None


_NONE = Hit(None, None)


# --------------------------------------------------------------------------- Windows registry


def _reg_open(hive_name: str, key: str):
    """Open ``key`` read-only (32-bit and 64-bit views tried); ``None`` if absent or not Windows."""
    if os.name != "nt":
        return None
    import winreg

    hive = getattr(winreg, hive_name)
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            return winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view)
        except OSError:
            continue
    return None


def _reg_value(hive_name: str, key: str, name: str) -> str | None:
    k = _reg_open(hive_name, key)
    if k is None:
        return None
    import winreg

    try:
        with k:
            v, _ = winreg.QueryValueEx(k, name)
            return str(v) if v not in (None, "") else None
    except OSError:
        return None


def _reg_subkeys(hive_name: str, key: str) -> list[str]:
    k = _reg_open(hive_name, key)
    if k is None:
        return []
    import winreg

    out: list[str] = []
    with k:
        i = 0
        while True:
            try:
                out.append(winreg.EnumKey(k, i))
            except OSError:
                return out
            i += 1


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        s = s[1:-1]
    return s.strip()


def _version_key(s: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", s)) or (0,)


# --------------------------------------------------------------------------- tools


def _program_files() -> list[Path]:
    out: list[Path] = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        v = os.environ.get(var)
        if v and Path(v) not in out:
            out.append(Path(v))
    return out


def blender() -> Hit:
    """Blender: registry ``InstallDir``, newest ``Program Files\\Blender Foundation\\Blender *``, ``PATH``."""
    exe = "blender.exe" if os.name == "nt" else "blender"
    cands: list[tuple[tuple, Path]] = []
    for ver in _reg_subkeys("HKEY_LOCAL_MACHINE", r"SOFTWARE\BlenderFoundation\Blender"):
        for name in ("InstallDir", "Install_Dir"):
            d = _reg_value("HKEY_LOCAL_MACHINE", rf"SOFTWARE\BlenderFoundation\Blender\{ver}", name)
            if d and (Path(_unquote(d)) / exe).is_file():
                cands.append((_version_key(ver), Path(_unquote(d)) / exe))
    for name in ("InstallDir", "Install_Dir"):
        d = _reg_value("HKEY_LOCAL_MACHINE", r"SOFTWARE\BlenderFoundation", name)
        if d and (Path(_unquote(d)) / exe).is_file():
            cands.append(((0,), Path(_unquote(d)) / exe))
    if cands:
        return Hit(max(cands, key=lambda c: c[0])[1], "registry")
    found: list[tuple[tuple, Path]] = []
    for pf in _program_files():
        for d in glob.glob(str(pf / "Blender Foundation" / "Blender*")):
            p = Path(d) / exe
            if p.is_file():
                found.append((_version_key(Path(d).name), p))
    if found:
        return Hit(max(found, key=lambda c: c[0])[1], "program_files")
    w = shutil.which("blender")
    return Hit(Path(w), "path") if w else _NONE


def vswhere_exe() -> Path | None:
    """``vswhere.exe`` at its standard location (installed with every VS 2017+ / Build Tools)."""
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        v = os.environ.get(var)
        if v:
            p = Path(v) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
            if p.is_file():
                return p
    return None


def _vswhere(*args: str) -> list[str]:
    exe = vswhere_exe()
    if exe is None:
        return []
    try:
        r = subprocess.run([str(exe), "-products", "*", "-prerelease", "-latest", "-utf8", *args],
                           capture_output=True, timeout=30, encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def msbuild() -> Hit:
    """MSBuild of the newest Visual Studio / Build Tools (``vswhere -find``), else ``PATH``."""
    for line in _vswhere("-requires", "Microsoft.Component.MSBuild", "-find", r"MSBuild\**\Bin\MSBuild.exe"):
        if Path(line).is_file():
            return Hit(Path(line), "vswhere")
    w = shutil.which("msbuild")
    return Hit(Path(w), "path") if w else _NONE


def vcvars() -> Hit:
    """``VC\\Auxiliary\\Build\\vcvarsall.bat`` of the newest installation with the x86/x64 C++ tools."""
    for line in _vswhere("-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                         "-property", "installationPath"):
        p = Path(line) / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
        if p.is_file():
            return Hit(p, "vswhere")
    w = shutil.which("vcvarsall.bat") if os.name == "nt" else None
    return Hit(Path(w), "path") if w else _NONE


def ariane(viewer: str | os.PathLike | None) -> Hit:
    """The Ariane viewer: ``paths.viewer`` if the file exists, else ``(None, None)`` (optional)."""
    if viewer and Path(viewer).is_file():
        return Hit(Path(viewer), "config")
    return _NONE


_FINDERS = {"blender": blender, "msbuild": msbuild, "vcvars": vcvars}


def _read_cache(f: Path, ttl: float) -> dict | None:
    try:
        st = f.stat()
        if time.time() - st.st_mtime > ttl:
            return None
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("format") != _CACHE_FORMAT or not isinstance(d.get("tools"), dict):
        return None
    return d["tools"]


_MEMO: dict[str, Hit] | None = None


def tools(cache_dir: str | os.PathLike | None = None, *, refresh: bool = False,
          ttl: float = CACHE_TTL) -> dict[str, Hit]:
    """``{"blender"|"msbuild"|"vcvars": Hit}``, cached in ``<cache_dir>/detect.json``.

    A cached hit is reused while its file exists and the cache is younger than ``ttl``; a
    cache that cannot be written (read-only work dir) is simply skipped. Without ``cache_dir``
    (or with ``SATK_DETECT=nocache``, as the test suite runs) the result is kept per process only.
    """
    global _MEMO
    if os.environ.get("SATK_DETECT", "").strip().lower() == "nocache":
        cache_dir = None
    if cache_dir is None:
        if _MEMO is None or refresh:
            _MEMO = {name: finder() for name, finder in _FINDERS.items()}
        return dict(_MEMO)
    f = Path(cache_dir) / "detect.json"
    cached = None if refresh else _read_cache(f, ttl)
    out: dict[str, Hit] = {}
    stale = cached is None
    for name, finder in _FINDERS.items():
        ent = (cached or {}).get(name)
        if isinstance(ent, dict) and (ent.get("path") is None or Path(ent["path"]).is_file()):
            out[name] = Hit(Path(ent["path"]) if ent.get("path") else None, ent.get("source"))
        else:
            out[name] = finder()
            stale = True
    if stale:
        payload = {"format": _CACHE_FORMAT,
                   "tools": {k: {"path": str(h.path) if h.path else None, "source": h.source}
                             for k, h in sorted(out.items())}}
        try:
            from .paths import atomic_write

            atomic_write(f, json.dumps(payload, indent=1, sort_keys=True) + "\n")
        except Exception:  # noqa: BLE001 - caching is best effort (read-only work, protected path)
            pass
    return out


# --------------------------------------------------------------------------- executable versions


_EXE_VERSIONS: dict | None = None


def exe_versions() -> dict:
    """``data/exe_versions.json`` (verified hashes, sizes and byte signatures).

    Read through :mod:`satk.core.resources`, so an installed package (``satk/_data``) knows the
    variants too; without the file every executable is ``unknown``.
    """
    global _EXE_VERSIONS
    if _EXE_VERSIONS is None:
        from . import resources
        from .errors import SatkError

        try:
            _EXE_VERSIONS = resources.read_json("exe_versions.json")
        except SatkError:
            _EXE_VERSIONS = {"variants": [], "sizes": [], "signatures": []}
    return _EXE_VERSIONS


def _va_reader(data: bytes):
    """``read_u32(va) -> int | None`` over the PE sections of ``data`` (file content, not memory)."""
    try:
        if data[:2] != b"MZ":
            return None
        lf = struct.unpack_from("<I", data, 0x3C)[0]
        if data[lf:lf + 4] != b"PE\0\0":
            return None
        nsec, = struct.unpack_from("<H", data, lf + 6)
        opt_size, = struct.unpack_from("<H", data, lf + 20)
        opt = lf + 24
        base, = struct.unpack_from("<I", data, opt + 28)
        secs = []
        for i in range(nsec):
            o = opt + opt_size + 40 * i
            vsize, va, rsize, raw = struct.unpack_from("<IIII", data, o + 8)
            secs.append((base + va, max(vsize, rsize), raw, rsize))
    except struct.error:
        return None

    def read(addr: int) -> int | None:
        for start, size, raw, rsize in secs:
            if start <= addr and addr + 4 <= start + size:
                off = raw + (addr - start)
                if addr + 4 > start + rsize or off + 4 > len(data):
                    return None
                return struct.unpack_from("<I", data, off)[0]
        return None

    return read


def identify_exe(path: str | os.PathLike) -> dict:
    """Variant of a GTA:SA executable: exact SHA-256, else byte signature, else size (``heuristic``).

    Returns ``{name, variant, layout, supported, match, heuristic, size, sha256}``; ``match`` is
    ``sha256`` | ``signature`` | ``size`` | ``none``. ``layout`` ``1.0us`` means the addresses of
    the symbol DB (``satk re``) apply; ``supported`` says whether ``satk re`` supports it.
    """
    from .paths import open_ro

    p = Path(path)
    with open_ro(p) as fh:
        data = fh.read(64 * 1024 * 1024)
    return classify_exe(data)


def classify_exe(data: bytes) -> dict:
    """:func:`identify_exe` for the executable's bytes."""
    sha = hashlib.sha256(data).hexdigest()
    db = exe_versions()
    base = {"size": len(data), "sha256": sha}
    for v in db.get("variants", []):
        if v.get("sha256") == sha:
            return {**base, "name": v["name"], "variant": v["variant"], "layout": v.get("layout"),
                    "supported": bool(v.get("supported")), "match": "sha256", "heuristic": False}
    read = _va_reader(data)
    if read is not None:
        for s in db.get("signatures", []):
            if read(int(s["va"], 16)) == int(s["u32"], 16):
                return {**base, "name": s["name"], "variant": s["variant"], "layout": s.get("layout"),
                        "supported": bool(s.get("supported")), "match": "signature", "heuristic": True}
    for s in db.get("sizes", []):
        if s.get("size") == len(data):
            return {**base, "name": s["name"], "variant": s["variant"], "layout": s.get("layout"),
                    "supported": bool(s.get("supported")), "match": "size", "heuristic": True}
    return {**base, "name": "unknown", "variant": "unknown", "layout": None, "supported": False,
            "match": "none", "heuristic": True}


# --------------------------------------------------------------------------- game installs


@dataclass
class GameInstall:
    """A folder with a GTA:SA executable (``satk init`` candidate).

    ``edition`` is ``classic`` for the PC game satk reads; ``definitive`` / ``mobile`` candidates
    carry a "not supported" problem (``exe`` is then the edition's marker file or folder).
    """

    root: Path
    source: str
    exe: Path
    sources: list[str] = field(default_factory=list)
    exe_info: dict | None = None
    problems: list[str] = field(default_factory=list)
    edition: str = "classic"

    @property
    def exe_variant(self) -> str | None:
        return (self.exe_info or {}).get("variant")

    def as_dict(self) -> dict:
        from .paths import jpath

        d: dict = {"root": jpath(self.root), "source": self.source, "exe": self.exe.name}
        if self.edition != "classic":
            d["edition"] = self.edition
        if len(self.sources) > 1:
            d["sources"] = list(self.sources)
        if self.exe_info:
            i = self.exe_info
            d.update(variant=i.get("variant"), exe_name=i.get("name"), layout=i.get("layout"),
                     re_supported=i.get("supported"), match=i.get("match"))
            if i.get("heuristic"):
                d["heuristic"] = True
        if self.problems:
            d["problems"] = list(self.problems)
        return d


def parse_vdf(text: str) -> dict:
    """Minimal Valve KeyValues (``libraryfolders.vdf``) parser: nested dicts of strings."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    root: dict = {}
    stack = [root]
    key: str | None = None
    for s, brace in tokens:
        if brace == "{":
            new: dict = {}
            if key is not None:
                stack[-1][key] = new
            stack.append(new)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            s = s.replace("\\\\", "\\").replace('\\"', '"')
            if key is None:
                key = s
            else:
                stack[-1][key] = s
                key = None
    return root


def steam_libraries() -> list[Path]:
    """Steam library folders: ``SteamPath`` plus every ``path`` of ``steamapps/libraryfolders.vdf``."""
    sp = _reg_value("HKEY_CURRENT_USER", r"Software\Valve\Steam", "SteamPath")
    if not sp:
        return []
    steam = Path(_unquote(sp))
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    try:
        tree = parse_vdf(vdf.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        tree = {}
    folders = tree.get("libraryfolders") or tree.get("LibraryFolders") or {}
    for _, v in sorted(folders.items()):
        p = v.get("path") if isinstance(v, dict) else (v if isinstance(v, str) and ("\\" in v or "/" in v) else None)
        if p and Path(p) not in libs:
            libs.append(Path(p))
    return libs


def _exe_in(root: Path) -> Path | None:
    for n in EXE_NAMES:
        p = root / n
        if p.is_file():
            return p
    return None


def _edition_marker(root: Path) -> tuple[str, Path] | None:
    """``(edition, marker)`` for The Definitive Edition or the mobile port, else ``None``."""
    for rel in (("Gameface", "Binaries", "Win64", "SanAndreas.exe"), ("SanAndreas.exe",), ("Gameface",)):
        p = root.joinpath(*rel)
        if p.exists():
            return "definitive", p
    for rel in (("texdb",), ("libGTASA.so",), ("lib", "arm64-v8a", "libGTASA.so"),
                ("lib", "armeabi-v7a", "libGTASA.so")):
        p = root.joinpath(*rel)
        if p.exists():
            return "mobile", p
    try:
        obb = next((p for p in root.glob("*.obb") if p.is_file()), None)
    except OSError:
        obb = None
    if obb is not None:
        return "mobile", obb
    if any("com.rockstargames.gtasa" in part.lower() for part in root.parts):
        return "mobile", root
    return None


def edition(root: str | os.PathLike) -> str | None:
    """``classic`` (the PC game satk reads), ``definitive``, ``mobile`` or ``None`` (not a game folder).

    The Definitive Edition is recognised by ``Gameface/`` or ``SanAndreas.exe``, the mobile port by
    ``texdb/``, ``libGTASA.so``, an ``.obb`` file or a ``com.rockstargames.gtasa`` folder; a classic
    folder has ``gta_sa.exe``/``gta-sa.exe`` or ``models/gta3.img`` with ``data/gta.dat``.
    """
    r = Path(root)
    try:
        if _exe_in(r) is not None:
            return "classic"
        other = _edition_marker(r)
        if other is not None:
            return other[0]
        if (r / "models" / "gta3.img").is_file() and (r / "data" / "gta.dat").is_file():
            return "classic"
    except OSError:
        return None
    return None


def check_game(root: str | os.PathLike) -> list[str]:
    """What a usable game folder lacks: ``models/gta3.img``, ``data/gta.dat``, the executable."""
    r = Path(root)
    out = []
    if _exe_in(r) is None:
        out.append("no gta_sa.exe / gta-sa.exe")
    for rel in (("models", "gta3.img"), ("data", "gta.dat")):
        if not r.joinpath(*rel).is_file():
            out.append(f"no {'/'.join(rel)}")
    return out


def _registry_candidates() -> Iterable[Path]:
    for hive in ("HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER"):
        for key in (r"SOFTWARE\WOW6432Node\Rockstar Games\GTA San Andreas\Installation",
                    r"SOFTWARE\Rockstar Games\GTA San Andreas\Installation"):
            v = _reg_value(hive, key, "ExePath")
            if v:
                yield Path(_unquote(v)).parent


def game_installs(*, extra: Iterable[tuple[str | os.PathLike, str]] = (),
                  workspace: str | os.PathLike | Iterable[str | os.PathLike] | None = None,
                  cwd: str | os.PathLike | None = None, identify: bool = True) -> list[GameInstall]:
    """Folders with ``gta_sa.exe``/``gta-sa.exe``, de-duplicated, in source order.

    Sources: ``extra`` (``(path, source)``, e.g. configured paths), direct children of the
    ``workspace`` folder(s) (``workspace``), the Rockstar registry key (``registry``), Steam
    libraries (``steam``), typical install folders (``program_files``) and ``cwd`` (``cwd``).
    """
    raw: list[tuple[Path, str]] = [(Path(p), s) for p, s in extra if p]
    wss = [workspace] if isinstance(workspace, (str, os.PathLike)) else list(workspace or [])
    for ws in wss:
        try:
            kids = sorted(Path(ws).iterdir())
        except OSError:
            kids = []
        raw += [(k, "workspace") for k in kids if k.is_dir() and not k.name.startswith(".")]
    raw += [(p, "registry") for p in _registry_candidates()]
    raw += [(lib / "steamapps" / "common" / d, "steam") for lib in steam_libraries() for d in _STEAM_DIRS]
    for var, parts in _TYPICAL:
        v = os.environ.get(var)
        if v:
            raw.append((Path(v).joinpath(*parts), "program_files"))
    raw.append((Path(cwd) if cwd else Path.cwd(), "cwd"))
    out: list[GameInstall] = []
    index: dict[str, GameInstall] = {}
    for root, source in raw:
        other = None
        try:
            exe = _exe_in(root)
            if exe is None and root.is_dir():
                other = _edition_marker(root)
        except OSError:
            exe = None
        if exe is None and other is None:
            continue
        try:
            k = os.path.normcase(os.path.realpath(root))
        except (OSError, ValueError):
            k = os.path.normcase(os.path.abspath(root))
        if k in index:
            if source not in index[k].sources:
                index[k].sources.append(source)
            continue
        if exe is None and other is not None:
            gi = GameInstall(Path(os.path.abspath(root)), source, other[1], [source], edition=other[0],
                             problems=[f"{EDITION_LABELS[other[0]]}: not supported"])
            index[k] = gi
            out.append(gi)
            continue
        gi = GameInstall(Path(os.path.abspath(root)), source, exe, [source])
        gi.problems = check_game(root)
        if identify:
            try:
                gi.exe_info = identify_exe(exe)
            except OSError as e:
                gi.problems.append(f"cannot read {exe.name}: {e}")
        index[k] = gi
        out.append(gi)
    return out
