"""Operations of satk.modinspect: what a mod changes and how Mod Loader combines mods (M3 lane C2).

CLI: ``satk mod inspect|conflicts|effective|check``. All CLI-only (``mcp=False``); MCP reaches them
through ``satk_op``. Module-level imports are stdlib-only. The ``satk help --ru`` texts live in
``help_ru.json`` next to this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, obj, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, jpath, work
from ..core.registry import op

__all__ = ["mod_inspect", "mod_conflicts", "mod_effective", "mod_check"]

_RU: dict[str, str] = json.loads(Path(__file__).with_name("help_ru.json").read_text(encoding="utf-8"))

def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    if cursor.isdigit():
        return int(cursor)
    raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page")


def _page(cols: list[str], rows: list[list], limit: int, cursor: str | None, warn: list[str], default: int) -> dict:
    lim = clamp_limit(limit, default=default)
    off = _offset(cursor)
    page = rows[off:off + lim]
    nxt = str(off + lim) if off + lim < len(rows) else None
    return table(cols, page, total=len(rows), next=nxt, warn=list(dict.fromkeys(warn))[:20])


def _priorities(priority: list[str] | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in priority or []:
        name, sep, val = p.rpartition("=")
        if not sep or not name or not val.strip().lstrip("-").isdigit():
            raise SatkError("BAD_PARAMS", f"bad --priority {p!r}", hint="--priority mymod=60 (1..100, 0 = ignore)")
        out[name.strip().lower()] = int(val)
    return out


def _world(profile: str, mods: list[str] | None, with_installed: bool, ml_profile: str | None,
           priority: list[str] | None):
    """``(world, folder or None)``: the mods given, else (or also) the profile's modloader folder."""
    from .base import BaseGame
    from .modloader import mods_from_paths, read_folder
    from .world import World

    base = BaseGame(profile)
    pr = _priorities(priority)
    folder = None
    entries = []
    if not mods or with_installed:
        folder = read_folder(base.root, ml_profile)
        for m in folder.mods:
            if m.lname in pr:
                m.priority = pr[m.lname]
                m.ignored = "priority 0" if m.priority == 0 else ("" if m.ignored == "priority 0" else m.ignored)
        entries += folder.loaded
    if mods:
        given = [m for m in mods_from_paths(mods, pr) if m.priority > 0]
        names = {e.lname for e in entries}
        for g in given:
            if g.lname in names:
                raise SatkError("BAD_PARAMS", f"mod name {g.name!r} is already installed in modloader/",
                                hint="rename the folder/zip you pass, or drop --with-installed")
        entries += given
    w = World(base, entries, file_ignored=folder.file_ignored if folder is not None else None)
    if folder is not None:
        w.warnings += folder.warnings
    return w, folder


@op("mod.inspect", mcp=False, group="asset",
    summary="What a mod changes, read the way Mod Loader loads it (folder, .zip without extracting, .img or one "
            "file): replaced/new DFF TXD COL, data-file records (field old->new), removed records, new and "
            "overridden IDE IDs, readme lines. Table kind/target/change/by/detail.",
    summary_ru=_RU["mod.inspect"],
    examples=("satk mod inspect mymod.zip", "satk mod inspect modloader/nsx --change modify remove",
              "satk mod inspect cars.img --profile installed"))
def mod_inspect(path: str, profile: str = "vanilla", change: list[str] | None = None,
                kind: list[str] | None = None, limit: int = 50, cursor: str | None = None) -> dict:
    """Inspect one mod against a profile's game files.

    Args:
        path: mod folder, .zip (read in place), .img (its entries) or a single file (handling.cfg, x.dff, readme.txt).
        profile: game the mod is compared with (vanilla = the clean copy).
        change: show only these changes (replace add modify remove new-id override-id load ignored).
        kind: show only these kinds (dff txd col ifp img ide data ipl zon asi cleo script other ...).
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .base import BaseGame
    from .inspect import CHANGES, COLS, inspect_world
    from .modloader import ModEntry
    from .world import World

    bad = [c for c in change or [] if c not in CHANGES]
    if bad:
        raise SatkError("BAD_PARAMS", f"unknown change {bad[0]!r}", data={"changes": list(CHANGES)})
    base = BaseGame(profile)
    p = Path(path)
    if not p.exists() and not p.is_absolute():
        alt = base.root / p
        if alt.exists():
            p = alt
    entry = ModEntry(p.stem if p.is_file() else p.name, p)
    with World(base, [entry]) as w:
        if not w.sources or (not w.sources[0].files and w.warnings and w.warnings[0].startswith("MOD:")):
            raise SatkError("NOT_FOUND", w.warnings[0].split(": ", 2)[-1] if w.warnings else f"cannot open {path}",
                            hint="give a mod folder, a .zip, an .img or a single mod file")
        rows, summary = inspect_world(w)
        src = w.sources[0]
        warn = w.warnings + base.warnings
        files = len([f for f in src.files if f.container is None or src.kind == "img"])
    if change:
        rows = [r for r in rows if r[2] in change]
    if kind:
        ks = {k.lower() for k in kind}
        rows = [r for r in rows if r[0] in ks]
    env = _page(COLS, rows, limit, cursor, warn, 50)
    env["mod"] = src.name
    env["source"] = f"{src.kind} {jpath(src.path)}"
    env["files"] = files
    env.update(summary)
    if not rows and not change and not kind:
        env["hint"] = "nothing Mod Loader would load differs from the game"
    return env


@op("mod.conflicts", mcp=False, group="asset",
    summary="Conflicts between Mod Loader mods (the profile's modloader/ folder, or given folders/zips): the same "
            "file replaced twice (winner by priority, then name) and the same data record changed twice (winner "
            "by Mod Loader's merge rules). Table target/kind/winner/losers/rule.",
    summary_ru=_RU["mod.conflicts"],
    examples=("satk mod conflicts", "satk mod conflicts --profile installed --ml-profile Default",
              "satk mod conflicts newcar.zip --with-installed", "satk mod conflicts a b --priority b=60"))
def mod_conflicts(mods: list[str] | None, profile: str = "installed", with_installed: bool = False,
                  ml_profile: str | None = None, priority: list[str] | None = None, limit: int = 50,
                  cursor: str | None = None) -> dict:
    """Conflicts between mods.

    Args:
        mods: mod folders or zips to combine (default: the mods in the profile's modloader/ folder).
        profile: game root whose files are under the mods (and whose modloader/ is read).
        with_installed: add the profile's installed modloader mods to the given ones.
        ml_profile: Mod Loader profile of modloader.ini to use (default: the one it selects).
        priority: priorities as name=N (1..100, 0 = ignored); default 50 or modloader.ini's.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .conflicts import COLS, conflict_rows

    w, folder = _world(profile, mods, with_installed, ml_profile, priority)
    with w:
        rows, cwarn = conflict_rows(w)
        warn = cwarn + w.warnings + w.base.warnings
        loaded = [f"{m.name}({m.priority})" for m in w.mods]
    env = _page(COLS, rows, limit, cursor, warn, 50)
    env["mods"] = loaded
    if folder is not None:
        env["modloader"] = jpath(folder.path)
        env["ml_profile"] = folder.profile
        ign = [f"{m.name}: {m.ignored}" for m in folder.mods if m.ignored]
        if ign:
            env["ignored_mods"] = ign
    if not loaded:
        env["hint"] = "no mods: give mod folders/zips or use a profile whose root has modloader/ mods"
    elif not rows:
        env["hint"] = "no two mods touch the same file or record"
    return env


@op("mod.effective", mcp=False, group="asset",
    summary="The data line the game gets after Mod Loader merges the mods: handling (by id, model ID or name), "
            "carcols, ide (model ID), gta.dat, object.dat; source, rule and every candidate. Without a key: "
            "per-file summary, --save writes the merged file.",
    summary_ru=_RU["mod.effective"],
    examples=("satk mod effective handling 411", "satk mod effective handling INFERNUS --mod my_handling",
              "satk mod effective ide 411", "satk mod effective handling --save"))
def mod_effective(file: str, key: str | None, profile: str = "installed", mod: list[str] | None = None,
                  with_installed: bool = False, ml_profile: str | None = None, priority: list[str] | None = None,
                  save: bool = False) -> dict:
    """Effective record or merged file.

    Args:
        file: handling | carcols | ide | gta | default | object, a data file name (water.dat) or an IDE (vehicles.ide).
        key: record: handling id (INFERNUS, case-sensitive) or model ID/name; model ID for ide; model name or
            colour index for carcols; path for gta.dat. Omit for a whole-file summary.
        profile: game root under the mods (its modloader/ folder is read unless --mod is given).
        mod: mod folders/zips to use instead of the installed ones.
        with_installed: use the installed mods as well as --mod.
        ml_profile: Mod Loader profile of modloader.ini to use.
        priority: priorities as name=N.
        save: (no key) write each merged file to work/out/mods/effective/<profile>/.
    """
    from .effective import effective_file, effective_key

    w, _folder = _world(profile, mod, with_installed, ml_profile, priority)
    with w:
        if key is not None:
            out = effective_key(w, file, key)
            warn = w.warnings + w.base.warnings
            env = obj(None, **out)
            if warn:
                env["warn"] = list(dict.fromkeys(warn))[:20]
            return env
        rows, info, rendered = effective_file(w, file)
        warn = w.warnings + w.base.warnings
    env = table(["file", "mode", "kept", "changed", "added", "removed", "sources"], rows,
                warn=list(dict.fromkeys(warn))[:20])
    env.update(info)
    if save:
        saved = []
        for name, text in rendered:
            dest = work("out", "mods", "effective", profile, *name.split("/"))
            saved.append(jpath(atomic_write(dest, text.encode("latin-1", errors="replace"))))
        env["saved"] = saved
    return env


@op("mod.check", mcp=False, group="asset",
    summary="Problems of a mod: satk's checks (ID collisions, files Mod Loader skips, dropped game records, IDs "
            "over the limit, asset lint) or the external INU Check if the user installed it (--engine inu; "
            "never downloaded). Table rule/sev/file/msg/sid.",
    summary_ru=_RU["mod.check"],
    examples=("satk mod check mymod.zip", "satk mod check modloader/nsx --sev warn",
              "satk mod check mymod --engine inu"))
def mod_check(path: str, engine: Literal["satk", "inu"] = "satk", exe: str | None = None, profile: str = "vanilla",
              sev: Literal["info", "warn", "error", "fatal"] = "info", lint: bool = True, timeout: float = 300.0,
              limit: int = 50, cursor: str | None = None) -> dict:
    """Check a mod.

    Args:
        path: mod folder, .zip, .img or single file.
        engine: satk (built-in) or inu (external INU Check; optional, found via --exe / SATK_INU_CHECK / PATH).
        exe: INU Check executable (engine inu).
        profile: game the mod is compared with.
        sev: lowest severity shown.
        lint: also run asset lint on folders, IMGs and single files (engine satk).
        timeout: seconds INU Check may run.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .check import COLS, SEV_ORDER, lint_issues, run_inu, satk_issues

    p = Path(path)
    if not p.exists():
        raise SatkError("NOT_FOUND", f"no such file or folder: {path}")
    extra: dict = {}
    if engine == "inu":
        rows, info, warn = run_inu(p.resolve(), exe, timeout)
        extra.update(info)
    else:
        from .base import BaseGame
        from .inspect import inspect_world
        from .modloader import ModEntry
        from .world import World

        base = BaseGame(profile)
        with World(base, [ModEntry(p.stem if p.is_file() else p.name, p)]) as w:
            irows, summary = inspect_world(w)
            rows = satk_issues(irows, summary, w)
            warn = w.warnings + base.warnings
            kind = w.sources[0].kind if w.sources else "dir"
        if lint:
            lrows, lwarn = lint_issues(p.resolve(), kind, profile)
            rows += lrows
            warn += lwarn
        extra["engine"] = "satk"
    rank = SEV_ORDER[sev]
    rows = [r for r in rows if SEV_ORDER.get(r[1], 1) >= rank]
    rows.sort(key=lambda r: (-SEV_ORDER.get(r[1], 1), str(r[2]).lower(), r[0]))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r[1]] = counts.get(r[1], 0) + 1
    env = _page(COLS, rows, limit, cursor, warn, 50)
    env["summary"] = counts
    env.update(extra)
    return env
