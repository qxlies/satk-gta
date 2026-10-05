"""Configuration: defaults < ``satk.toml`` < ``SATK_<SECTION>_<KEY>`` env < CLI flags (SPEC §2.4).

Workspace (``paths.workspace``), first match wins (``docs/en/install.md``):

1. ``SATK_HOME`` (env);
2. ``paths.workspace`` from the ``satk.toml`` that was found (order below); a portable installation
   ignores it (warning ``PORTABLE_WORKSPACE``) unless ``SATK_CONFIG`` names the file;
3. derived from the code location:

   * a portable installation (the release zip: :data:`PORTABLE_MARKER` next to ``src/``) -> its
     folder (source ``portable``);
   * a checkout named ``tools`` whose parent has ``work/`` or ``satk.toml`` -> that parent; a git
     worktree uses its main checkout (``.git`` file -> ``gitdir:`` -> ``commondir``, no ``git``
     subprocess);
4. the per-user data directory: ``%LOCALAPPDATA%\\satk`` (elsewhere ``~/.local/share/satk``).

Lookup of ``satk.toml`` (the first existing file is used):

1. ``SATK_CONFIG`` — explicit file, or a directory holding ``satk.toml`` (no file there = no
   config); ``SATK_CONFIG=none`` (or empty) disables files (tests);
2. ``<repo>/satk.toml`` — the checkout this code runs from (a worktree has none by default);
3. ``<main checkout>/satk.toml`` — so git worktrees share the local config;
4. ``<workspace>/satk.toml`` — what ``satk init`` writes (workspace from ``SATK_HOME``, rule 3 or 4);
5. ``%APPDATA%\\satk\\satk.toml`` (elsewhere ``~/.config/satk/satk.toml``) — the per-user file.
   When it names a ``paths.workspace`` that has its own ``satk.toml``, that file is read on
   top of it (a pointer to a workspace chosen with ``satk init --workspace``).
   A portable installation never reads it: its configuration lives in its own folder.

``SATK_HOME`` sets ``paths.workspace`` (env level). All default paths are derived from the
workspace via ``${paths.workspace}`` interpolation, so ``SATK_HOME=<tmp>`` isolates tests.
String values may reference other values as ``${section.key}``.

Machine-specific tools (``paths.blender``, ``paths.msbuild``, ``paths.vcvars``) have no built-in
default: unless the config or ``SATK_PATHS_<KEY>`` sets them, they are discovered on first use
by :mod:`satk.core.detect` (cached in ``work/cache/detect.json``; ``SATK_DETECT=0`` turns
discovery off). :meth:`Config.path_source` tells where each path came from.

Use :func:`satk.core.paths.cfg` (or :func:`load`) to get the cached :class:`Config`.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, NamedTuple

from .errors import SatkError

__all__ = [
    "REPO_ROOT",
    "SRC_ROOT",
    "MAIN_ROOT",
    "DATA_ROOT",
    "PORTABLE_MARKER",
    "PORTABLE_ROOT",
    "DEFAULT_WORKSPACE",
    "DETECTED_PATHS",
    "Config",
    "Profile",
    "Workspace",
    "defaults",
    "load",
    "reset",
    "build",
    "config_files",
    "git_common_dir",
    "main_checkout",
    "portable_root",
    "derived_workspace",
    "user_data_dir",
    "user_config_file",
    "using",
]

#: Root of the checkout this code runs from (``tools`` or a worktree).
REPO_ROOT: Path = Path(__file__).resolve().parents[3]
#: ``<repo>/src`` (what the shims put on ``PYTHONPATH``).
SRC_ROOT: Path = Path(__file__).resolve().parents[2]
#: Repository data (``tools/data``: manifests, notes, exe versions).
DATA_ROOT: Path = REPO_ROOT / "data"
#: ``[paths]`` keys that are discovered on the machine when not configured (no built-in default).
DETECTED_PATHS: tuple[str, ...] = ("blender", "msbuild", "vcvars")

_REF = re.compile(r"\$\{([A-Za-z0-9_.\-]+)\}")


# --------------------------------------------------------------------------- workspace discovery


def git_common_dir(repo: str | os.PathLike) -> Path | None:
    """The git common directory of checkout ``repo`` without running git.

    ``<repo>/.git`` as a directory is the answer itself; a ``.git`` *file* (git worktree)
    names ``gitdir: <dir>`` whose ``commondir`` file points to the shared ``.git``.
    ``None`` when ``repo`` is not a git checkout or the files are unreadable.
    """
    g = Path(repo) / ".git"
    try:
        if g.is_dir():
            return g
        if not g.is_file():
            return None
        line = g.read_text(encoding="utf-8").strip()
        if not line.startswith("gitdir:"):
            return None
        gitdir = Path(line[7:].strip())
        if not gitdir.is_absolute():
            gitdir = Path(repo) / gitdir
        cfile = gitdir / "commondir"
        if not cfile.is_file():
            return Path(os.path.normpath(gitdir))
        c = Path(cfile.read_text(encoding="utf-8").strip())
        return Path(os.path.normpath(c if c.is_absolute() else gitdir / c))
    except (OSError, ValueError):
        return None


def main_checkout(repo: str | os.PathLike) -> Path | None:
    """Main working tree of the repository ``repo`` belongs to (``repo`` itself for a main checkout)."""
    common = git_common_dir(repo)
    if common is None or common.name.lower() != ".git":
        return None  # not a checkout, or a bare repository
    return common.parent


#: Main checkout of this repository (``None`` when the code does not run from a git checkout).
MAIN_ROOT: Path | None = main_checkout(REPO_ROOT)

#: Marker file of a portable installation (the release zip built by ``satk dev release``).
PORTABLE_MARKER = "portable.txt"


def portable_root(root: str | os.PathLike | None = None) -> Path | None:
    """``root`` (default :data:`REPO_ROOT`) when it holds :data:`PORTABLE_MARKER`, else ``None``.

    The release zip unpacks to ``<folder>/{python,src,data,...}`` plus ``portable.txt``. The code runs
    from ``<folder>/src/satk``, so ``REPO_ROOT`` is that folder, and the folder is the workspace too:
    ``work/`` and ``satk.toml`` are created next to the launchers.
    """
    r = Path(root) if root is not None else REPO_ROOT
    try:
        return r if (r / PORTABLE_MARKER).is_file() else None
    except OSError:
        return None


#: Folder of the portable installation this code runs from (``None`` in a checkout or a package).
PORTABLE_ROOT: Path | None = portable_root()


class Workspace(NamedTuple):
    """A workspace directory and where it came from."""

    path: Path
    source: str  # env:SATK_HOME | file | portable | checkout | main-checkout | user


def _workspace_parent(checkout: Path | None) -> Path | None:
    if checkout is None or checkout.name.lower() != "tools":
        return None
    parent = checkout.parent
    if (parent / "work").is_dir() or (parent / "satk.toml").is_file():
        return parent
    return None


def derived_workspace() -> Workspace | None:
    """Rule 3: the portable folder, else the parent of a ``tools`` checkout (this one or the main one)
    with ``work/`` or ``satk.toml``."""
    if PORTABLE_ROOT is not None:
        return Workspace(PORTABLE_ROOT, "portable")
    for label, co in (("checkout", REPO_ROOT), ("main-checkout", MAIN_ROOT)):
        ws = _workspace_parent(co)
        if ws is not None:
            return Workspace(ws, label)
    return None


def _env(env: dict[str, str] | None) -> dict[str, str]:
    return dict(os.environ) if env is None else env


def user_data_dir(env: dict[str, str] | None = None) -> Path:
    """Rule 4: ``%LOCALAPPDATA%\\satk`` on Windows, ``$XDG_DATA_HOME/satk`` or ``~/.local/share/satk``."""
    e = _env(env)
    if os.name == "nt" and e.get("LOCALAPPDATA"):
        return Path(e["LOCALAPPDATA"]) / "satk"
    if e.get("XDG_DATA_HOME"):
        return Path(e["XDG_DATA_HOME"]) / "satk"
    return Path.home() / ".local" / "share" / "satk"


def user_config_file(env: dict[str, str] | None = None) -> Path:
    """The per-user ``satk.toml``: ``%APPDATA%\\satk\\satk.toml`` or ``~/.config/satk/satk.toml``."""
    e = _env(env)
    if os.name == "nt" and e.get("APPDATA"):
        return Path(e["APPDATA"]) / "satk" / "satk.toml"
    if e.get("XDG_CONFIG_HOME"):
        return Path(e["XDG_CONFIG_HOME"]) / "satk" / "satk.toml"
    return Path.home() / ".config" / "satk" / "satk.toml"


_DERIVED: Workspace | None = derived_workspace()
#: The workspace used when neither ``SATK_HOME`` nor a config file names one (rules 3-4 at import).
DEFAULT_WORKSPACE: str = str(_DERIVED.path if _DERIVED else user_data_dir())


def _default_workspace(env: dict[str, str]) -> Workspace:
    if _DERIVED is not None:
        return _DERIVED
    return Workspace(user_data_dir(env), "user")


def _base_workspace(env: dict[str, str]) -> Workspace:
    """Workspace before any config file is read (``SATK_HOME``, rule 3 or rule 4)."""
    if env.get("SATK_HOME"):
        return Workspace(Path(env["SATK_HOME"]), "env:SATK_HOME")
    return _default_workspace(env)


# --------------------------------------------------------------------------- defaults


def defaults(workspace: str | os.PathLike | None = None) -> dict[str, Any]:
    """Built-in defaults (``satk.toml.example`` shows the same values).

    No machine-specific literals: the workspace comes from discovery (``DEFAULT_WORKSPACE``
    unless ``workspace`` is given) and the tool paths of :data:`DETECTED_PATHS` from
    :mod:`satk.core.detect`. ``safety.protected_roots`` keeps only the roots that exist.
    """
    return {
        "paths": {
            "workspace": str(workspace) if workspace is not None else DEFAULT_WORKSPACE,
            "game": "${paths.workspace}/gta-sa-clean",
            "installed": "${paths.workspace}/GTA San Andreas",
            "game_root": "${paths.installed}",
            "work": "${paths.workspace}/work",
            "src": "${paths.workspace}/src",
            "viewer": "${paths.workspace}/viewer/ariane/bin/ariane.exe",
            "engine": "${paths.workspace}/engine/mtasa",
            "premake": "${paths.src}/mtasa-neon/utils/premake5.exe",
        },
        "safety": {
            "protected_roots": ["${paths.installed}", "${paths.src}", "${paths.game}", "${paths.game_root}"],
        },
        "profiles": {
            "vanilla": {"root": "${paths.game}", "dat": ["data/default.dat", "data/gta.dat"], "img_order": "engine"},
            "installed": {"root": "${paths.installed}", "dat": ["data/default.dat", "data/gta.dat"], "img_order": "engine"},
            "samp": {"root": "${paths.installed}", "dat": ["data/default.two", "data/gta.two"], "img_order": "samp"},
            "game": {"root": "${paths.game_root}", "dat": ["data/default.dat", "data/gta.dat"], "img_order": "engine"},
        },
        "index": {"default_profile": "vanilla"},
        "layers": {
            "vanilla_manifest": "${paths.game}/MANIFEST.sha256",
            "rules": [
                {"glob": "SAMP/**", "layer": "samp"},
                {"glob": "data/*.two", "layer": "samp"},
                {"glob": "modloader/*/**", "layer": "modloader:{1}"},
            ],
            "fallback": "modded",
        },
        "viewer": {"default_target": "ariane", "window": "1280x720"},
    }


@dataclass(frozen=True, slots=True)
class Profile:
    """A load profile (§4.3.1): root directory, DAT files and archive order."""

    name: str
    root: Path
    dat: tuple[str, ...]
    img_order: str

    @property
    def configured(self) -> bool:
        """The game root exists (profiles whose root is missing are "not configured", not errors)."""
        return self.root.is_dir()


class _Paths:
    """Attribute access to ``[paths]`` as ``Path`` objects: ``cfg().paths.game``.

    The keys of :data:`DETECTED_PATHS` fall back to discovery when not configured; they are
    ``None`` when nothing was found. ``items()``/iteration list only configured keys.
    """

    __slots__ = ("_d", "_detect")

    def __init__(self, d: dict[str, Path], detect=None):
        object.__setattr__(self, "_d", d)
        object.__setattr__(self, "_detect", detect)

    def __getattr__(self, name: str) -> Path:
        try:
            return self._d[name]
        except KeyError:
            if name in DETECTED_PATHS:
                return self._detect(name).path if self._detect else None
            raise AttributeError(f"no path {name!r} in [paths]") from None

    def __setattr__(self, name, value):  # pragma: no cover - immutability guard
        raise AttributeError("Config.paths is read-only")

    def __iter__(self) -> Iterator[str]:
        return iter(self._d)

    def items(self):
        return self._d.items()

    def get(self, name: str, default: Path | None = None) -> Path | None:
        if name in self._d:
            return self._d[name]
        if name in DETECTED_PATHS and self._detect is not None:
            hit = self._detect(name).path
            return default if hit is None else hit
        return default


def _as_path(v: Any) -> Path:
    return Path(os.path.normpath(str(v)))


class Config:
    """Merged, interpolated configuration (read-only)."""

    def __init__(self, data: dict[str, Any], sources: list[str], *, origins: dict[str, str] | None = None,
                 workspace_source: str | None = None, vanilla_alias: bool = True):
        self.data = data
        self.sources = list(sources)
        #: ``[paths]`` key -> ``default`` | ``file`` | ``env`` (where the configured value came from).
        self.origins: dict[str, str] = dict(origins or {})
        #: Where ``paths.workspace`` came from (see the module docstring).
        self.workspace_source = workspace_source or self.origins.get("workspace", "default")
        self._detected: dict[str, Any] = {}
        self._detect_lock = threading.Lock()
        self.paths = _Paths({k: _as_path(v) for k, v in data.get("paths", {}).items() if str(v).strip()},
                            self._detect_path)
        roots = data.get("safety", {}).get("protected_roots", [])
        self.protected_roots: tuple[Path, ...] = tuple(_as_path(r) for r in roots if str(r).strip())
        profs: dict[str, Profile] = {}
        for name, p in (data.get("profiles") or {}).items():
            if not isinstance(p, dict) or "root" not in p:
                raise SatkError("BAD_PARAMS", f"config: profile {name!r} needs a 'root'")
            if not str(p["root"]).strip():
                continue  # e.g. game_root = '' : unset, never the current directory
            profs[name] = Profile(
                name=name,
                root=_as_path(p["root"]),
                dat=tuple(str(x) for x in p.get("dat", ())),
                img_order=str(p.get("img_order", "engine")),
            )
        #: Profile aliases: ``{"vanilla": "game"}`` when there is no clean copy (PORTABILITY §4).
        self.aliases: dict[str, str] = {}
        #: Configuration warnings (``"CODE: text"``) for ``config show``, ``doctor``, ``index build``.
        self.warnings: list[str] = []
        van, game = profs.get("vanilla"), profs.get("game")
        if vanilla_alias and van is not None and game is not None and not van.configured and game.configured \
                and os.path.normcase(str(van.root)) != os.path.normcase(str(game.root)):
            del profs["vanilla"]
            self.aliases["vanilla"] = "game"
            self.warnings.append(
                f"NO_CLEAN_COPY: no clean game copy at {van.root}; profile vanilla = game ({game.root}): "
                "the vanilla/modded layers are indistinguishable without a clean copy or manifest "
                "(satk init --clean-copy)")
        self.profiles: dict[str, Profile] = profs

    # ------------------------------------------------------------------ discovery of tool paths

    def _detect_path(self, name: str):
        """``detect.Hit`` for ``name`` (memoized per config; ``SATK_DETECT=0`` disables discovery)."""
        with self._detect_lock:
            if name not in self._detected:
                from . import detect

                if os.environ.get("SATK_DETECT", "").strip().lower() in ("0", "off", "no", "false"):
                    self._detected.update({k: detect.Hit(None, None) for k in DETECTED_PATHS})
                else:
                    work = self.paths._d.get("work")
                    self._detected.update(detect.tools(cache_dir=(work / "cache") if work else None))
            return self._detected[name]

    def path_source(self, name: str) -> str:
        """Where ``paths.<name>`` comes from: ``default``/``file``/``env`` or ``detect:<how>``/``missing``
        (``workspace``: ``env:SATK_HOME``/``file``/``checkout``/``main-checkout``/``user``)."""
        if name == "workspace":
            return self.workspace_source
        if name in self.paths._d:
            return self.origins.get(name, "default")
        if name in DETECTED_PATHS:
            hit = self._detect_path(name)
            return f"detect:{hit.source}" if hit.path is not None else "missing"
        return "missing"

    # ------------------------------------------------------------------ access

    def get(self, dotted: str, default: Any = None) -> Any:
        """Value by dotted key (``"viewer.window"``) or ``default``."""
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def canonical_profile(self, name: str) -> str:
        """``name`` with aliases resolved (``vanilla`` -> ``game`` without a clean copy)."""
        return self.aliases.get(name, name)

    @property
    def default_profile(self) -> str:
        """``[index] default_profile`` with aliases resolved (``vanilla`` here, ``game`` for most users)."""
        name = str(self.get("index.default_profile") or "vanilla")
        return self.canonical_profile(name)

    def profile(self, name: str) -> Profile:
        """Profile by name (aliases resolved), ``BAD_PARAMS`` with suggestions if unknown."""
        try:
            return self.profiles[self.canonical_profile(name)]
        except KeyError:
            import difflib

            known = sorted({*self.profiles, *self.aliases})
            raise SatkError(
                "BAD_PARAMS",
                f"unknown profile {name!r}",
                did_you_mean=difflib.get_close_matches(name, known, n=3, cutoff=0.4),
                data={"profiles": known},
            ) from None

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly copy; paths as absolute forward-slash strings (discovered tools included)."""
        from .paths import jpath

        out = copy.deepcopy(self.data)
        out["paths"] = {k: jpath(v) for k, v in self.paths.items()}
        for name in DETECTED_PATHS:
            if name not in out["paths"] and (p := self.paths.get(name)) is not None:
                out["paths"][name] = jpath(p)
        out.setdefault("safety", {})["protected_roots"] = [jpath(p) for p in self.protected_roots]
        out["profiles"] = {
            n: {"root": jpath(p.root), "dat": list(p.dat), "img_order": p.img_order}
            for n, p in self.profiles.items()
        }
        if self.aliases:
            out["aliases"] = dict(self.aliases)
        if manifest := self.get("layers.vanilla_manifest"):
            out["layers"]["vanilla_manifest"] = jpath(manifest)
        return out


# --------------------------------------------------------------------------- loading


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _leaves(d: dict, prefix: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _leaves(v, prefix + (k,))
        else:
            yield prefix + (k,), v


def _set(d: dict, path: tuple[str, ...], value: Any) -> None:
    cur = d
    for p in path[:-1]:
        cur = cur.setdefault(p, {})
    cur[path[-1]] = value


def _coerce_env(raw: str, like: Any, name: str) -> Any:
    try:
        if isinstance(like, bool):
            low = raw.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True
            if low in ("0", "false", "no", "off", ""):
                return False
            raise ValueError(raw)
        if isinstance(like, int):
            return int(raw)
        if isinstance(like, float):
            return float(raw)
        if isinstance(like, list):
            s = raw.strip()
            if s.startswith("["):
                v = json.loads(s)
                if not isinstance(v, list):
                    raise ValueError(raw)
                return v
            return [x for x in s.split(os.pathsep) if x]
    except (ValueError, json.JSONDecodeError):
        raise SatkError("BAD_PARAMS", f"bad value in environment variable {name}: {raw!r}") from None
    return raw


def _env_name(path: tuple[str, ...]) -> str:
    return "SATK_" + "_".join(p.upper().replace("-", "_") for p in path)


def _explicit(env: dict[str, str]) -> tuple[bool, Path | None]:
    """(``SATK_CONFIG`` is set, the file it selects or ``None`` for "no file")."""
    raw = env.get("SATK_CONFIG")
    if raw is None:
        return False, None
    if raw.strip().lower() in ("", "none", "-"):
        return True, None
    p = Path(raw)
    return True, (p / "satk.toml" if p.is_dir() else p)


def config_files(env: dict[str, str] | None = None) -> list[Path]:
    """Config files that would be read, in order (at most one is used: the first existing)."""
    env = os.environ if env is None else env
    explicit, f = _explicit(env)
    if explicit:
        return [f] if f is not None else []
    cands = [REPO_ROOT / "satk.toml"]
    if MAIN_ROOT is not None:
        cands.append(MAIN_ROOT / "satk.toml")
    cands.append(_base_workspace(env).path / "satk.toml")
    if PORTABLE_ROOT is None:  # a portable installation keeps its configuration in its own folder
        cands.append(user_config_file(env))
    seen: list[Path] = []
    keys: set[str] = set()
    for c in cands:
        k = os.path.normcase(os.path.abspath(c))
        if k not in keys:
            keys.add(k)
            seen.append(c)
    return seen


def _read_toml(path: Path) -> dict:
    import tomllib

    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise SatkError("BAD_PARAMS", f"cannot parse {path}: {e}", hint="fix or delete the file") from None


def _interpolate(data: dict) -> dict:
    flat = {".".join(p): v for p, v in _leaves(data)}

    def resolve(value: Any, depth: int = 0) -> Any:
        if isinstance(value, str):
            if depth > 10:
                raise SatkError("BAD_PARAMS", f"config: interpolation too deep in {value!r}")

            def sub(m: re.Match) -> str:
                ref = m.group(1)
                if ref not in flat or not isinstance(flat[ref], str):
                    raise SatkError("BAD_PARAMS", f"config: unknown reference ${{{ref}}}")
                return str(resolve(flat[ref], depth + 1))

            return _REF.sub(sub, value)
        if isinstance(value, list):
            return [resolve(v, depth) for v in value]
        if isinstance(value, dict):
            return {k: resolve(v, depth) for k, v in value.items()}
        return value

    return resolve(data)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _file_layers(env: dict[str, str]) -> list[tuple[Path, dict]]:
    """The config file(s) to merge, lowest priority first (one file, or a pointer + its workspace file)."""
    explicit, _ = _explicit(env)
    raw = env.get("SATK_CONFIG")
    for f in config_files(env):
        if f.is_file():
            layers = [(f, _read_toml(f))]
            if not explicit and _same_file(f, user_config_file(env)):
                ws = (layers[0][1].get("paths") or {}).get("workspace")
                if isinstance(ws, str) and ws.strip() and "${" not in ws:
                    wf = Path(ws) / "satk.toml"
                    if wf.is_file() and not _same_file(wf, f):
                        layers.append((wf, _read_toml(wf)))
            return layers
        if explicit and raw is not None and not Path(raw).is_dir():
            raise SatkError("BAD_PARAMS", f"SATK_CONFIG points to a missing file: {f}")
    return []


def build(env: dict[str, str] | None = None) -> Config:
    """Build a fresh :class:`Config` from defaults, file and environment (no caching)."""
    env = dict(os.environ) if env is None else env
    base = _default_workspace(env)
    data = defaults(base.path)
    sources = ["defaults"]
    origins = {k: "default" for k in data["paths"]}
    ws_source = base.source
    file_roots = False
    vanilla_set = False  # vanilla's root configured explicitly: never aliased to game
    for f, fdata in _file_layers(env):
        data = _deep_merge(data, fdata)
        sources.append(str(f).replace("\\", "/"))
        fpaths = fdata.get("paths") if isinstance(fdata.get("paths"), dict) else {}
        for k in fpaths:
            origins[k] = "file"
        if "workspace" in fpaths:
            ws_source = "file"
        if isinstance(fdata.get("safety"), dict) and "protected_roots" in fdata["safety"]:
            file_roots = True
        if "root" in ((fdata.get("profiles") or {}).get("vanilla") or {}):
            vanilla_set = True
    portable_note = None
    if PORTABLE_ROOT is not None and not _explicit(env)[0] and not env.get("SATK_HOME") \
            and "SATK_PATHS_WORKSPACE" not in env:
        # A portable installation is its own workspace even when its satk.toml names another folder
        # (written before the folder was moved, or copied from an older version's folder).
        named = data["paths"].get("workspace")
        if isinstance(named, str) and named.strip() and "${" not in named \
                and os.path.normcase(os.path.abspath(named)) != os.path.normcase(str(PORTABLE_ROOT)):
            portable_note = (f"PORTABLE_WORKSPACE: satk.toml names the workspace {named}; this portable "
                             f"installation always uses its own folder {PORTABLE_ROOT} (delete that line)")
        data["paths"]["workspace"] = str(PORTABLE_ROOT)
        origins["workspace"] = "default"
        ws_source = "portable"
    if env.get("SATK_HOME") and "SATK_PATHS_WORKSPACE" not in env:
        data["paths"]["workspace"] = env["SATK_HOME"]
        sources.append("env:SATK_HOME")
        origins["workspace"] = "env"
        ws_source = "env:SATK_HOME"
    env_roots = False
    leaves = list(_leaves(data))
    leaves += [(("paths", k), "") for k in DETECTED_PATHS if k not in data["paths"]]
    for path, value in leaves:
        name = _env_name(path)
        if name in env:
            _set(data, path, _coerce_env(env[name], value, name))
            sources.append(f"env:{name}")
            if path[0] == "paths":
                origins[path[1]] = "env"
                if path[1] == "workspace":
                    ws_source = f"env:{name}"
            if path == ("safety", "protected_roots"):
                env_roots = True
            if path == ("profiles", "vanilla", "root"):
                vanilla_set = True
    data = _interpolate(data)
    if not (file_roots or env_roots):
        # Built-in default: only the roots that exist, without duplicates (PORTABILITY §5).
        # The write guard (satk.core.paths) still protects installed/src/game_root/game themselves.
        roots: list[str] = []
        keys: set[str] = set()
        for r in data["safety"]["protected_roots"]:
            k = os.path.normcase(os.path.normpath(str(r)))
            if str(r).strip() and k not in keys and Path(r).exists():
                keys.add(k)
                roots.append(r)
        data["safety"]["protected_roots"] = roots
    # Only the default clean copy (<workspace>/gta-sa-clean) falls back to the game profile.
    alias = not vanilla_set and origins.get("game") == "default"
    c = Config(data, sources, origins=origins, workspace_source=ws_source, vanilla_alias=alias)
    if portable_note:
        c.warnings.append(portable_note)
    return c


_lock = threading.Lock()
_cached: Config | None = None


def load() -> Config:
    """Cached configuration (call :func:`reset` after changing env/files)."""
    global _cached
    with _lock:
        if _cached is None:
            _cached = build()
        return _cached


def reset() -> None:
    """Drop the cached configuration (tests, long-running servers after edits)."""
    global _cached
    with _lock:
        _cached = None


@contextlib.contextmanager
def using(c: Config) -> Iterator[Config]:
    """Make :func:`load` return ``c`` inside the block (``satk init`` acting on a new workspace)."""
    global _cached
    with _lock:
        prev, _cached = _cached, c
    try:
        yield c
    finally:
        with _lock:
            _cached = prev
