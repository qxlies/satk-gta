"""``satk init``: first-time setup on any machine (``docs/en/install.md``).

1. workspace: ``--workspace``, else the current one (``SATK_HOME``, ``satk.toml``, the checkout
   location), else ``%LOCALAPPDATA%\\satk``;
2. game: ``--game``, else the discovered installs (:func:`satk.core.detect.game_installs`):
   one candidate is taken, several are offered in a terminal and are ``AMBIGUOUS`` otherwise;
3. checks the game (``models/gta3.img``, ``data/gta.dat``, the executable variant) and where things
   live (:mod:`satk.runtime.location`): a workspace inside a game folder is refused, The Definitive
   Edition and the mobile port are ``UNSUPPORTED``, OneDrive / Program Files / VirtualStore and less
   than 1 GB free on the workspace drive give warnings;
4. discovers Blender, MSBuild/vcvars and the Ariane viewer and writes what was found;
5. writes ``<workspace>/satk.toml`` (an existing different file only with ``--yes``; the result
   shows a diff); when that workspace would not be found again without ``SATK_HOME``, a pointer
   ``[paths] workspace`` goes into the per-user ``satk.toml`` (``%APPDATA%\\satk``);
6. ``--clean-copy``: ``satk game clone`` into ``<workspace>/gta-sa-clean``, only for a game that
   matches the stock 1.0 US manifest; otherwise it says why and the layers stay ``modded``;
7. suggests the next steps.

The game folder is only read. Nothing is written with ``--dry-run``.
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import tomllib
from pathlib import Path

from satk.core import config as _config
from satk.core import detect
from satk.core.envelope import with_warn
from satk.core.errors import SatkError
from satk.core.paths import atomic_write, cfg, ensure_writable, jpath

from . import location

__all__ = ["init", "render_toml"]

_HEADER = ("# satk configuration written by `satk init` (docs/en/install.md).\n"
           "# Values here win over discovery; see satk.toml.example for every key.\n")


def _abs(p: str | os.PathLike) -> Path:
    return Path(os.path.abspath(os.path.expandvars(os.path.expanduser(os.fspath(p)))))


def _same(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _toml_str(s: str) -> str:
    """A TOML string: literal ``'...'`` when possible (Windows paths), else a basic string."""
    if "'" not in s and "\n" not in s and "\r" not in s:
        return f"'{s}'"
    return json.dumps(s, ensure_ascii=False)  # JSON escapes are valid TOML basic-string escapes


def render_toml(paths: dict[str, str], *, default_profile: str | None, notes: dict[str, str] | None = None) -> str:
    """The ``satk.toml`` text written by ``init`` (deterministic; parsed back as a check)."""
    notes = notes or {}
    width = max(len(k) for k in paths) if paths else 0
    lines = [_HEADER, "[paths]"]
    for k, v in paths.items():
        line = f"{k.ljust(width)} = {_toml_str(v)}"
        if notes.get(k):
            line += f"  # {notes[k]}"
        lines.append(line)
    if default_profile:
        lines += ["", "[index]", f"default_profile = {_toml_str(default_profile)}"]
    lines.append("")
    text = "\n".join(lines)
    tomllib.loads(text)  # never write something satk cannot read back
    return text


def _choose(cands: list[detect.GameInstall]) -> Path | None:
    """Interactive choice on stderr/stdin (only in a terminal)."""
    print("satk init: several GTA San Andreas folders found:", file=sys.stderr)
    for i, c in enumerate(cands, 1):
        print(f"  {i}. {jpath(c.root)}  [{c.exe_variant or '?'}; {c.source}]", file=sys.stderr)
    print("number (empty = cancel): ", end="", file=sys.stderr, flush=True)
    ans = sys.stdin.readline().strip()
    if ans.isdigit() and 1 <= int(ans) <= len(cands):
        return cands[int(ans) - 1].root
    return None


def _interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def _discoverable(ws: Path, env: dict[str, str]) -> bool:
    """Would ``<ws>/satk.toml`` be found without ``SATK_HOME``/``SATK_CONFIG``?"""
    clean = {k: v for k, v in env.items() if k not in ("SATK_HOME", "SATK_CONFIG")}
    for f in _config.config_files(clean):
        if f.is_file() and not _same(f, ws / "satk.toml"):
            return False  # another config file wins
        if _same(f.parent, ws):
            return True
    return False


def _clean_copy(game: Path, exe_info: dict | None, ws_cfg: _config.Config, dry_run: bool) -> dict:
    """``--clean-copy``: clone only a game that matches the stock 1.0 US manifest."""
    from satk.game import exe as X
    from satk.game import manifests as M
    from satk.game.clone import clone, plan

    dst = ws_cfg.paths.game
    if (dst / "gta_sa.exe").is_file():
        return {"status": "exists", "path": jpath(dst)}
    sha = (exe_info or {}).get("sha256")
    v = X.KNOWN_EXE.get(sha or "")
    if v is None or not v.derivable:
        return {"status": "impossible", "path": jpath(dst),
                "why": f"gta_sa.exe is {(exe_info or {}).get('variant', 'unknown')}, not a 1.0 US HOODLUM image "
                       "satk can restore to stock; layers stay 'modded' (profile vanilla = game)"}
    stock = M.load_stock()
    with _config.using(ws_cfg):
        try:
            rep = plan(game, dst, stock)
        except SatkError as e:
            return {"status": "impossible", "path": jpath(dst), "why": f"{e.code}: {e.msg}"}
        if dry_run:
            return {"status": "planned", "path": jpath(dst), "files": rep.get("files"), "bytes": rep.get("bytes")}
        res = clone(game, dst, stock)
    return {"status": "created", "path": res.get("dst", jpath(dst)), "files": res.get("files")}


def init(game: str | None = None, workspace: str | None = None, clean_copy: bool = False, yes: bool = False,
         dry_run: bool = False) -> dict:
    """See the module docstring. Returns the plan/result as a JSON object."""
    env = dict(os.environ)
    cur = cfg()
    warns: list[str] = []
    # 1. workspace
    if workspace:
        ws, ws_source = _abs(workspace), "--workspace"
    else:
        ws, ws_source = Path(os.path.abspath(cur.paths.workspace)), cur.workspace_source
    # the workspace must not be inside a game folder (checked before anything is discovered or written)
    bad = location.workspace_error(ws)
    if bad is not None:
        raise SatkError("BAD_PARAMS", bad[0], hint=bad[1], data={"workspace": jpath(ws), "source": ws_source})
    # 2. game candidates (configured paths of the current config first)
    extra = [(cur.paths.get(k), "config") for k in ("game_root", "installed", "game")]
    wss = [ws] + ([cur.paths.workspace] if not _same(cur.paths.workspace, ws) else [])
    cands = detect.game_installs(extra=[(p, s) for p, s in extra if p is not None], workspace=wss)
    usable = [c for c in cands if not c.problems]
    chosen: Path | None = None
    if game:
        chosen = _abs(game)
        if not chosen.is_dir():
            raise SatkError("NOT_FOUND", f"game folder not found: {jpath(chosen)}",
                            hint="satk init --game <folder with gta_sa.exe>")
        ed = detect.edition(chosen)
        if ed in detect.EDITION_LABELS:
            raise SatkError("UNSUPPORTED", f"{jpath(chosen)} holds {detect.EDITION_LABELS[ed]}, which satk does not "
                            "read: satk works with the classic PC game (gta_sa.exe, models/gta3.img, data/gta.dat)",
                            hint="satk init --game <classic GTA San Andreas folder>",
                            data={"root": jpath(chosen), "edition": ed})
        problems = detect.check_game(chosen)
        if problems:
            raise SatkError("BAD_PARAMS", f"{jpath(chosen)} is not a GTA San Andreas folder: {', '.join(problems)}",
                            hint="pass the folder that holds gta_sa.exe, models/gta3.img and data/gta.dat",
                            data={"root": jpath(chosen), "problems": problems})
    elif len(usable) == 1:
        chosen = usable[0].root
    elif not usable:
        other = [c for c in cands if c.edition != "classic"]
        seen = ("; found only unsupported editions: " + ", ".join(
            f"{jpath(c.root)} ({detect.EDITION_LABELS[c.edition]})" for c in other)) if other else ""
        if not dry_run:
            raise SatkError("NOT_FOUND", "no GTA San Andreas folder found (registry, Steam, Program Files, "
                            "workspace, current directory)" + seen, hint="satk init --game <folder with gta_sa.exe>",
                            data={"candidates": [c.as_dict() for c in cands]})
        warns.append("NOT_FOUND: no GTA San Andreas folder found" + seen + "; pass --game <folder>")
    elif not dry_run and not yes and _interactive():
        chosen = _choose(usable)
        if chosen is None:
            raise SatkError("BAD_PARAMS", "cancelled: no game folder chosen", hint="satk init --game <folder>")
    elif not dry_run:
        raise SatkError("AMBIGUOUS", f"{len(usable)} GTA San Andreas folders found; choose one with --game",
                        hint="satk init --game <folder> (satk init --dry-run lists them)",
                        did_you_mean=[jpath(c.root) for c in usable],
                        data={"candidates": [c.as_dict() for c in usable]})
    out: dict = {"workspace": jpath(ws), "workspace_source": ws_source, "dry_run": dry_run,
                 "candidates": [c.as_dict() for c in cands]}
    # 3. the chosen game
    exe_info = None
    if chosen is not None:
        bad = location.workspace_error(ws, chosen)
        if bad is not None:
            raise SatkError("BAD_PARAMS", bad[0], hint=bad[1], data={"workspace": jpath(ws), "game": jpath(chosen)})
        warns += location.game_warnings(chosen)
        exe = next((chosen / n for n in detect.EXE_NAMES if (chosen / n).is_file()), None)
        try:
            exe_info = detect.identify_exe(exe) if exe else None
        except OSError as e:
            warns.append(f"EXE_UNREADABLE: {e}")
        g = {"root": jpath(chosen), "exe": exe.name if exe else None}
        if exe_info:
            g.update(variant=exe_info["variant"], exe_name=exe_info["name"], layout=exe_info.get("layout"),
                     re_supported=exe_info["supported"], match=exe_info["match"])
            if exe_info["heuristic"]:
                g["heuristic"] = True
            if not exe_info["supported"]:
                warns.append(f"UNSUPPORTED_EXE: {exe.name} is {exe_info['variant']}: assets, index, textures, "
                             "models and Blender work; satk re needs the 1.0 US address layout")
        out["game"] = g
    warns += location.workspace_warnings(ws)
    # 4. tools
    tools = detect.tools(refresh=True)
    viewer_default = ws / "viewer" / "ariane" / "bin" / "ariane.exe"
    ar = detect.ariane(viewer_default)
    out["tools"] = {k: ({"path": jpath(h.path), "source": h.source} if h.path else None)
                    for k, h in [*tools.items(), ("ariane", ar)]}
    # 5. satk.toml
    paths: dict[str, str] = {"workspace": str(ws)}
    notes: dict[str, str] = {}
    if chosen is not None:
        paths["game_root"] = str(chosen)
        notes["game_root"] = "your game: read only"
    for k, h in tools.items():
        if h.path is not None:
            paths[k] = str(h.path)
            notes[k] = f"found via {h.source}"
    clean = ws / "gta-sa-clean"
    has_clean = (clean / "gta_sa.exe").is_file()
    default_profile = "vanilla" if has_clean else "game"
    target = ws / "satk.toml"
    out["config"] = jpath(target)
    text = render_toml(paths, default_profile=default_profile, notes=notes)
    old = target.read_text(encoding="utf-8") if target.is_file() else None
    if old is not None and old != text:
        out["diff"] = list(difflib.unified_diff(old.splitlines(), text.splitlines(), "satk.toml (current)",
                                                "satk.toml (new)", lineterm="", n=0))
    if chosen is None and not dry_run:  # pragma: no cover - guarded above
        raise SatkError("INTERNAL", "no game chosen")
    written = False
    if old is not None and old != text and not yes and not dry_run:
        raise SatkError("EXISTS", f"{jpath(target)} exists with other content", hint="--yes to overwrite (see diff)",
                        data={"diff": out.get("diff", [])})
    if not dry_run and old != text:
        ensure_writable(ws / "work")
        (ws / "work").mkdir(parents=True, exist_ok=True)
        atomic_write(target, text)
        written = True
    out["written"] = written
    # pointer in the per-user config when this workspace would not be found again
    if not (env.get("SATK_HOME") or env.get("SATK_CONFIG")) and not _discoverable(ws, env) and chosen is not None:
        uf = _config.user_config_file(env)
        pointer = render_toml({"workspace": str(ws)}, default_profile=None)
        cur_ptr = uf.read_text(encoding="utf-8") if uf.is_file() else None
        if cur_ptr == pointer:
            pass
        elif cur_ptr is not None and not yes:
            warns.append(f"POINTER_EXISTS: {jpath(uf)} names another workspace; set SATK_HOME={ws} or rerun with --yes")
        elif dry_run:
            out["pointer"] = jpath(uf) + " (would be written)"
        else:
            atomic_write(uf, pointer)
            out["pointer"] = jpath(uf)
    # profiles of the new workspace
    new_env = {**env, "SATK_CONFIG": str(target)} if target.is_file() else None
    try:
        if new_env is not None and not dry_run:
            ws_cfg = _config.build(new_env)
        else:
            ws_cfg = _config.build({**env, "SATK_CONFIG": "none", "SATK_HOME": str(ws),
                                    **({"SATK_PATHS_GAME_ROOT": str(chosen)} if chosen else {})})
    except SatkError as e:  # pragma: no cover - render_toml already parsed the text
        raise SatkError("INTERNAL", f"the written satk.toml does not load: {e.msg}") from None
    out["profile"] = ws_cfg.default_profile if chosen is not None else None
    out["profiles"] = {n: jpath(p.root) for n, p in ws_cfg.profiles.items() if p.configured}
    if ws_cfg.aliases:
        out["aliases"] = dict(ws_cfg.aliases)
        warns += [w for w in ws_cfg.warnings if w not in warns]
    # 6. clean copy
    if clean_copy and chosen is not None:
        cc = _clean_copy(chosen, exe_info, ws_cfg, dry_run)
        out["clean_copy"] = cc
        if cc["status"] == "created" and written and default_profile != "vanilla":
            atomic_write(target, render_toml(paths, default_profile="vanilla", notes=notes))
            out["profile"] = "vanilla"
    # 7. next steps
    nxt = []
    if chosen is None:
        out["next"] = ["satk init --game <folder> (one of candidates)" if usable else "satk init --game <folder>"]
        return with_warn(out, *warns)
    if env.get("SATK_HOME") and not _same(env["SATK_HOME"], ws):
        nxt.append(f"set SATK_HOME={ws} (it points elsewhere now)")
    elif env.get("SATK_CONFIG"):
        nxt.append(f"set SATK_CONFIG={target} or unset it")
    nxt += [f"satk index build --profile {out.get('profile') or 'game'}", "satk mcp config --write"]
    if not dry_run and not written and old == text:
        out["unchanged"] = True
    out["next"] = nxt
    return with_warn(out, *warns)
