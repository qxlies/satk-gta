"""Operations of ``satk.idmgr`` (B4), CLI only (``mcp=False``; MCP reaches them through the generic operation tool):

* ``id.free``      -> ``satk id free --kind vehicle|ped|weapon|object [--count N] [--range A-B]``;
* ``id.conflicts`` -> ``satk id conflicts [MOD ...] [--profile installed]``;
* ``id.remap``     -> ``satk id remap MOD --to A-B`` (a copy under ``work/out/idmgr/``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, dumps, obj, table, with_warn
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath, work
from ..core.registry import op

Kind = Literal["vehicle", "ped", "weapon", "object"]


def _extra_defs(mods: list[str] | None) -> list:
    from .scan import scan_mods

    return scan_mods(list(mods or []))


@op("id.free", mcp=False, group="asset",
    summary="Free model ids for a vehicle/ped/weapon/object across every built profile (vanilla, installed, samp: "
            "loaded IDEs, modloader folders and readmes), engine-reserved and SA-MP ids, fastman92 LA limit; "
            "--target samp-dl for DL custom ids.",
    summary_ru="Свободные ID моделей (машина, пед, оружие, объект) с учётом всех профилей (IDE, папки modloader, "
               "readme), зарезервированных движком и SA-MP ID, лимита fastman92 LA; --target samp-dl — ID DL.",
    examples=("satk id free --kind vehicle", "satk id free --kind object --count 20 --contiguous",
              "satk id free --kind ped --range 20000-20100 --max-id 29999", "satk id free --kind object --target samp-dl"))
def id_free(kind: Kind | None = None, count: int = 1, range: str | None = None, profile: list[str] | None = None,  # noqa: A002
            target: Literal["sp", "samp-dl"] = "sp", samp: bool = True, contiguous: bool = False,
            max_id: int | None = None, mods: list[str] | None = None, artconfig: list[str] | None = None) -> dict:
    """Find free model ids.

    Args:
        kind: vehicle, ped, weapon or object.
        count: how many ids.
        range: where to look: 612-799, 15000..15099, several with commas (default: by kind, up to the id limit).
        profile: profiles to check (default: every profile whose index is built).
        target: sp (the game, modloader) or samp-dl (SA-MP DL / open.mp custom ids: objects -1000..-30000,
            skins 20001..30000).
        samp: also keep clear of SA-MP's ids (client skins, samp.ide skins and objects).
        contiguous: the ids must be one block.
        max_id: last valid model id (default 19999, or the fastman92 LA ini of every checked profile).
        mods: mod folders (not installed yet) whose ids count as taken.
        artconfig: SA-MP DL artconfig.txt files whose ids count as taken (--target samp-dl).
    """
    from .free import artconfig_ids, capacity, free_ids, open_profiles, taken_ids
    from .ranges import DEFAULT_RANGE, LOW_SLOTS, SAMP_DL, VANILLA_VEHICLES, fmt_ranges, fmt_spec, parse_ranges

    if kind is None:
        raise SatkError("BAD_PARAMS", "give --kind vehicle|ped|weapon|object", hint="satk id free --kind vehicle")
    if count < 1 or count > 5000:
        raise SatkError("BAD_PARAMS", "--count must be 1..5000")
    warn: list[str] = []
    if target == "samp-dl":
        if kind not in SAMP_DL:
            raise SatkError("UNSUPPORTED", f"SA-MP DL has no custom {kind} models (only object and ped)",
                            hint="satk id free --kind object --target samp-dl")
        taken = artconfig_ids(artconfig or [])
        ranges = parse_ranges(range) if range else [SAMP_DL[kind]]
        ids, total = free_ids(taken, ranges, count, contiguous)
        env = obj(kind=kind, target=target, ids=ids, blocks=fmt_ranges(ids), count=len(ids),
                  range=fmt_spec(ranges), free_in_range=total, taken_in_range=None,
                  artconfig=[jpath(Path(a)) for a in artconfig or []])
        if not artconfig:
            warn.append("NO_ARTCONFIG: no artconfig.txt given, so no DL id counts as taken (--artconfig FILE)")
    else:
        skipped: list[tuple[str, str]] = []
        dbs = open_profiles(profile, skipped)
        t = taken_ids(dbs, samp=samp, extra=_extra_defs(mods), max_id=max_id)
        warn += t.hints
        warn += _skipped_warnings(skipped, [p for p, _ in dbs])
        ranges = parse_ranges(range) if range else [(DEFAULT_RANGE[kind][0], t.max_id)]
        over = [r for r in ranges if max(r) > t.max_id or min(r) < 0]
        if over:
            warn.append(f"OVER_LIMIT: range {fmt_spec(over)} goes past 0..{t.max_id} ({t.max_from}); those ids "
                        "need a limit adjuster that raises the model count (--max-id)")
        from .ranges import iter_range, kind_reserved

        taken = dict(t.ids)
        for a, b, why in kind_reserved(kind):
            for i in iter_range(a, b):
                taken.setdefault(i, why)
        ids, total = free_ids(taken, ranges, count, contiguous)
        n_range = sum(abs(b - a) + 1 for a, b in ranges)
        cap = capacity(kind, t.used)
        env = obj(kind=kind, target=target, ids=ids, blocks=fmt_ranges(ids), count=len(ids),
                  range=fmt_spec(ranges), free_in_range=total, taken_in_range=n_range - total,
                  profiles=t.profiles, max_id=t.max_id, max_from=t.max_from, capacity=cap)
        if cap["used"] + len(ids) > cap["store"]:
            warn.append(f"STORE_FULL: the stock engine has {cap['store']} {kind} model slots and a profile already "
                        f"defines {cap['used']}: more need a limit adjuster (Open Limit Adjuster or fastman92 LA)")
        elif cap["store"] - cap["used"] < LOW_SLOTS:
            warn.append(f"STORE_LOW: only {cap['store'] - cap['used']} of {cap['store']} stock {kind} model slots are "
                        f"free (a profile defines {cap['used']}); more need a limit adjuster (Open Limit Adjuster or "
                        "fastman92 LA)")
        if kind == "vehicle" and any(not VANILLA_VEHICLES[0] <= i <= VANILLA_VEHICLES[1] for i in ids):
            warn.append("ADDON_VEHICLE: vehicle ids outside 400-611 need fastman92 LA (vehicle models + handling "
                        "and audio patches)")
        if kind == "weapon" and ids:
            warn.append("ADDON_WEAPON: a new weapon type needs fastman92 LA's weapon type loader")
    if len(ids) < count:
        warn.append(f"NOT_ENOUGH: only {len(ids)} of {count} free ids" + (" in one block" if contiguous else "")
                    + f" in {env.get('range')}")
    return with_warn(env, *warn)


@op("id.conflicts", mcp=False, group="asset",
    summary="Model ids and names defined twice: between modloader mods, data mods and the base game of a profile, "
            "plus mod folders given (not installed yet). Table sev/code/id/name/where/other (CLASH, REPLACES, "
            "DUPLICATE, NAME_TWICE...).",
    summary_ru="ID и имена моделей, определённые дважды: между модами modloader, модами data и игрой профиля, "
               "плюс указанные папки модов. Таблица sev/code/id/name/where/other.",
    examples=("satk id conflicts --profile installed", "satk id conflicts downloads/carpack --profile installed",
              "satk id conflicts modA modB --no-profile"))
def id_conflicts(mods: list[str] | None, profile: str | None = None, use_profile: bool = True,
                 all: bool = False, limit: int = 20, cursor: str | None = None) -> dict:  # noqa: A002
    """Find id and name conflicts.

    Args:
        mods: mod folders or files to check (each one is a mod) besides what the profile has installed.
        profile: profile whose game and installed mods are checked (default: installed if built, else the default).
        use_profile: include the profile (--no-use-profile: only compare the given mods with each other).
        all: also list info rows (base-game lines a mod rewrites with the same name, samp vs vanilla).
        limit: rows per page.
        cursor: next page (the 'next' value of the previous answer).
    """
    from .conflicts import COLS, conflicts
    from .scan import profile_defs, scan_mods

    defs: list = []
    used: str | None = None
    if use_profile:
        from ..index.api import open_index

        from ..core.paths import cfg

        cand = [profile] if profile else ["installed", cfg().default_profile]
        for p in cand:
            try:
                db = open_index(p)
            except SatkError as e:
                if profile or e.code not in ("INDEX_MISSING", "BAD_PARAMS"):
                    raise
                continue
            used = p
            defs += profile_defs(db)
            break
    defs += scan_mods(list(mods or []))
    if not defs:
        raise SatkError("BAD_PARAMS", "nothing to check", hint="satk id conflicts <mod folder> or build a profile index")
    rows = conflicts(defs)
    if not all:
        rows = [r for r in rows if r[0] != "info"]
    lim = clamp_limit(limit)
    off = int(cursor[1:]) if cursor and cursor[:1] == "o" and cursor[1:].isdigit() else 0
    page = rows[off:off + lim]
    env = table(COLS, page, total=len(rows), next=f"o{off + lim}" if off + lim < len(rows) else None)
    sev = [r[0] for r in rows]
    env.update({"profile": used, "mods": [jpath(Path(os.path.abspath(m))) for m in mods or []] or None,
                "definitions": len(defs), "errors": sev.count("error"), "warnings": sev.count("warn")})
    return env


@op("id.remap", mcp=False, group="asset",
    summary="Copy a mod with its model ids moved to free ids of a range (or --map old=new): IDE lines, text and "
            "binary IPL placements and car generators, readme lines; writes work/out/idmgr/<mod>/ plus a JSON map.",
    summary_ru="Копия мода с ID моделей, перенесёнными в свободный диапазон (или --map старый=новый): IDE, текстовые "
               "и бинарные IPL, readme; пишет work/out/idmgr/<мод>/ и JSON-карту.",
    examples=("satk id remap carpack --to 15000-15099", "satk id remap mymod --map 1900=15500 --dry-run"))
def id_remap(mod: str, to: str | None = None, map: str | None = None, ids: str | None = None,  # noqa: A002
             profile: list[str] | None = None, samp: bool = True, max_id: int | None = None,
             name: str | None = None, changed_only: bool = False, dry_run: bool = False, force: bool = False,
             limit: int = 20) -> dict:
    """Remap a mod's ids in a copy.

    Args:
        mod: the mod folder (or one IDE/IPL/readme file).
        to: target ranges for the moved ids (612-799, 15000..15099, several with commas).
        map: explicit pairs old=new, comma-separated (win over --to).
        ids: only move these old ids (ranges); default every id the mod adds (same-name replacements of
            base-game models stay).
        profile: profiles whose ids count as taken (default: every built profile).
        samp: keep clear of SA-MP's ids too.
        max_id: last valid model id for the free-id search.
        name: output folder under work/out/idmgr/ (default: the mod folder name).
        changed_only: write only the changed files (default: the whole mod).
        dry_run: plan only; write nothing.
        force: replace an existing output folder.
        limit: rows of the map/changes tables to return (all are in the JSON report).
    """
    import shutil

    from .free import open_profiles, taken_ids
    from .ranges import parse_ranges
    from .remap import parse_pairs, plan, rewrite
    from .scan import scan_mod

    src = Path(os.path.abspath(mod))
    mod_defs = scan_mod(src)
    if not mod_defs:
        raise SatkError("NOT_FOUND", f"{jpath(src)} defines no model ids (no IDE lines, no IDE-like readme lines)")
    explicit = parse_pairs(map)
    if not to and not explicit:
        raise SatkError("BAD_PARAMS", "give --to A-B (target range) or --map old=new",
                        hint=f"satk id remap {mod} --to 15000-15099")
    warn: list[str] = []
    taken: dict[int, str] = {}
    base: dict[int, set[str]] = {}
    profiles: list[str] = []
    try:
        t = taken_ids(open_profiles(profile), samp=samp, max_id=max_id)
        taken, profiles = t.ids, t.profiles
        for defs in t.defs.values():
            for d in defs:
                if d.owner in ("vanilla", "samp"):
                    base.setdefault(d.id, set()).add(d.name.lower())
    except SatkError as e:
        if e.code != "INDEX_MISSING" or not explicit or to:
            raise
        warn.append(f"NO_INDEX: new ids not checked against any profile ({e.msg})")
    p = plan(mod_defs, taken, parse_ranges(to) if to else [], base=base, explicit=explicit,
             only={i for a, b in parse_ranges(ids) for i in (range(a, b + 1) if a <= b else range(b, a + 1))}
             if ids else None)
    warn += [f"MAP: {x}" for x in p.problems]
    folder = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or (src.stem if src.is_file() else src.name)).strip("._") or "mod"
    dst = work("out", "idmgr") / folder
    a, b = os.path.normcase(str(src)), os.path.normcase(str(dst))
    if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
        raise SatkError("BAD_PARAMS", f"the output folder {jpath(dst)} overlaps the mod folder",
                        hint="give --name another")
    if not dry_run and dst.exists() and any(dst.iterdir()):
        if not force:
            raise SatkError("EXISTS", f"{jpath(dst)} exists", hint="add --force to replace it, or --name another")
        shutil.rmtree(ensure_writable(dst))
    rows, w = rewrite(src, dst, p, changed_only=changed_only, dry_run=dry_run)
    warn += w
    pairs = [[old, new, p.names.get(old)] for old, new in sorted(p.mapping.items())]
    kept = [[old, p.names.get(old), why] for old, why in sorted(p.kept.items())]
    report = None
    if not dry_run:
        rep = {"mod": jpath(src), "out": jpath(dst), "profiles": profiles,
               "map": {"cols": ["old", "new", "name"], "rows": pairs},
               "kept": {"cols": ["id", "name", "why"], "rows": kept},
               "files": {"cols": ["file", "kind", "ids"], "rows": rows}}
        rpath = work("out", "idmgr", f"{folder}.remap.json")
        atomic_write(rpath, dumps(rep, pretty=True) + "\n")
        report = jpath(rpath)
    lim = clamp_limit(limit)
    if not p.mapping:
        warn.append("NOTHING_MOVED: every id of the mod is kept (see kept)")
    env = obj(out=None if dry_run else jpath(dst), report=report, mod=jpath(src), moved=len(p.mapping),
              kept=len(p.kept), files_changed=len(rows), ids_changed=sum(r[2] for r in rows),
              map={"cols": ["old", "new", "name"], "rows": pairs[:lim]},
              kept_ids={"cols": ["id", "name", "why"], "rows": kept[:lim]} if kept else None,
              changes={"cols": ["file", "kind", "ids"], "rows": rows[:lim]}, profiles=profiles or None,
              dry_run=True if dry_run else None)
    return with_warn(env, *warn)


def _skipped_warnings(skipped: list[tuple[str, str]], opened: list[str]) -> list[str]:
    """PROFILE_SKIPPED warnings, except for profiles that load exactly like one already read (same root, DAT
    files and archive order, e.g. ``game`` = ``installed``): their ids are already counted."""
    c = cfg()

    def shape(name: str):
        try:
            pr = c.profile(name)
        except SatkError:
            return None
        return (str(pr.root).lower(), tuple(pr.dat), pr.img_order)

    seen = {shape(p) for p in opened}
    return [f"PROFILE_SKIPPED: {p} ({why}); ids taken only there were not considered "
            f"(satk index build --profile {p})" for p, why in skipped if shape(p) not in seen]
