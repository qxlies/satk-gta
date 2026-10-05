"""Step 4b of ``index build`` (schema v3): the data files ``gta_sa.exe`` loads by name. Owner: M2-10.

========================  =======================  ==========================================
file (case-insensitive)   tables                   parser
========================  =======================  ==========================================
``data/water.dat``        ``water_quad`` config 0  :func:`satk.formats.water.parse_water`
``data/water1.dat``       ``water_quad`` config 1  (same; used after a script switches water)
``data/timecyc.dat``      ``timecyc``              :func:`satk.formats.timecyc.parse_timecyc`
``data/carcols.dat``      ``carcol``, ``car_color``  :func:`satk.formats.carcols.parse_carcols`
``data/handling.cfg``     ``handling``             :func:`satk.formats.handling.parse_handling`
``data/ped.dat``          ``ped_rel``              :func:`parse_ped_dat` (here)
``data/object.dat``       ``object_data``          :func:`satk.formats.objectdat.parse_object_dat`
========================  =======================  ==========================================

A profile loaded through SA-MP's ``.two`` DAT files (``default.two``/``gta.two``) reads ``<stem>.two`` when
it exists, as the SA-MP client does (``HANDLING.two``, ``timecyc.two``; both equal the stock files in the
audited install, report 13).

The ``source`` rows of these files stay as discovery made them (kind ``other``); the files join the
freshness signature (``meta.sources_sig``) instead. A missing file is not an error (synthetic roots):
its name goes to ``meta.stats.data_missing``. Parser messages (engine quirks, skipped lines) go to
``meta.notes``. Everything is inserted in file order, so the content hash stays deterministic.

The peds IDE fields (``pedtype``, ``stat``, voices ...) are already in ``model.extra``; the view
``v_ped`` exposes them as columns and ``v_vehicle`` joins the ``cars`` fields with ``handling`` and
``car_color``.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from ..formats.carcols import parse_carcols
from ..formats.dat import canon_relpath, read_text, resolve_ci
from ..formats.handling import parse_handling
from ..formats.objectdat import parse_object_dat
from ..formats.timecyc import parse_timecyc
from ..formats.water import parse_water

__all__ = ["DATA_FILES", "load", "fts_rows", "parse_ped_dat", "PED_RELS", "rgb", "rgba"]

#: key -> path under the profile root.
DATA_FILES: dict[str, str] = {
    "water": "data/water.dat", "water1": "data/water1.dat", "timecyc": "data/timecyc.dat",
    "carcols": "data/carcols.dat", "handling": "data/handling.cfg", "ped": "data/ped.dat",
    "object": "data/object.dat",
}
#: Acquaintance keywords of ``ped.dat`` (the engine compares the first 4/7 characters).
PED_RELS = ("hate", "dislike", "like", "respect")


def parse_ped_dat(text: str) -> list[tuple[str, str, list[str], int]]:
    """``ped.dat`` -> ``[(pedtype, rel, [other types], line)]`` in file order (``CPedType::LoadPedData``).

    A line whose first word is not ``Hate``/``Dislike``/``Like``/``Respect`` starts a ped type block;
    an acquaintance line replaces the earlier line of the same kind in that block (later wins).
    ``#`` starts a comment line; commas and tabs separate names.
    """
    out: list[tuple[str, str, list[str], int]] = []
    cur: str | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.replace(",", " ").split()
        rel = next((r for r in PED_RELS if toks[0].lower().startswith(r)), None)
        if rel is None:
            cur = toks[0]
            continue
        if cur is None:
            continue  # acquaintance before any ped type: the engine applies it to MISSION8; not indexed
        out = [o for o in out if not (o[0].lower() == cur.lower() and o[1] == rel)]
        out.append((cur, rel, toks[1:], n))
    return out


def rgb(v) -> int | None:
    """``[r, g, b]`` -> ``0xRRGGBB`` (components clamped to 0..255)."""
    if not v or any(c is None for c in v[:3]):
        return None
    r, g, b = (max(0, min(255, int(c))) for c in v[:3])
    return (r << 16) | (g << 8) | b


def rgba(v) -> int | None:
    """``[r, g, b, a]`` -> ``0xRRGGBBAA``."""
    if not v or len(v) < 4 or any(c is None for c in v[:4]):
        return None
    r, g, b, a = (max(0, min(255, int(c))) for c in v[:4])
    return (r << 24) | (g << 16) | (b << 8) | a


def _dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def _find(root: Path, rel: str, two: bool = False) -> tuple[Path, str] | None:
    for cand in ((rel.rsplit(".", 1)[0] + ".two",) if two else ()) + (rel,):
        p = resolve_ci(root, cand)
        if p is not None and p.is_file():
            return p, canon_relpath(os.path.relpath(p, root))
    return None


def load(conn: sqlite3.Connection, root: Path, source_id: dict[str, int], *, notes: list[str],
         stats: dict, two: bool = False) -> list[str]:
    """Fill the v3 tables from the files under ``root``. Returns the relpaths that were read.

    Args:
        two: the profile loads SA-MP ``.two`` DAT files: prefer ``data/<stem>.two`` over each data file.
    """
    read: list[str] = []
    missing: list[str] = []
    files: dict[str, tuple[Path, str, int]] = {}
    for key, rel in DATA_FILES.items():
        f = _find(root, rel, two)
        if f is None or f[1] not in source_id:
            missing.append(rel)
            continue
        files[key] = (f[0], f[1], source_id[f[1]])
        read.append(f[1])

    def text_of(key: str) -> tuple[str, str, int] | None:
        if key not in files:
            return None
        path, rel, sid = files[key]
        return read_text(path), rel, sid

    def note(rel: str, errs: list) -> None:
        for ln, msg in errs:
            notes.append(f"{rel}:{ln}: {msg}")
        if errs:
            stats["data_issues"] = stats.get("data_issues", 0) + len(errs)

    # ---- water
    wrows = []
    for key, config in (("water", 0), ("water1", 1)):
        t = text_of(key)
        if t is None:
            continue
        text, rel, sid = t
        errs: list = []
        for p in parse_water(text, errors=errs):
            bb = p.bbox
            wrows.append((sid, config, p.idx, p.line, len(p.verts), p.flags, *bb, _dumps([v.as_list() for v in p.verts])))
        note(rel, errs)
    conn.executemany("INSERT INTO water_quad(source_id,config,idx,line,nverts,flags,minx,miny,minz,maxx,maxy,maxz,"
                     "verts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", wrows)
    conn.execute("INSERT INTO water_rtree SELECT id, minx, maxx, miny, maxy, minz, maxz FROM water_quad ORDER BY id")

    # ---- time cycle
    t = text_of("timecyc")
    trows = []
    if t is not None:
        text, rel, sid = t
        errs = []
        for r in parse_timecyc(text, errors=errs):
            v = r.values
            trows.append((sid, r.weather, r.weather_name, r.hour, r.line, r.nread,
                          rgb(v["amb"]), rgb(v["amb_obj"]), rgb(v["dir"]), rgb(v["sky_top"]), rgb(v["sky_bot"]),
                          rgb(v["sun_core"]), rgb(v["sun_corona"]), rgb(v["low_clouds"]), rgb(v["bottom_clouds"]),
                          rgba(v["water"]), v["sun_size"], v["sprite_size"], v["sprite_bright"], v["shadow"],
                          v["light_shadow"], v["pole_shadow"], v["far_clip"], v["fog_start"], v["light_on_ground"],
                          _dumps(v)))
        note(rel, errs)
    conn.executemany("INSERT INTO timecyc(source_id,weather,weather_name,hour,line,nread,amb,amb_obj,dir,sky_top,"
                     "sky_bot,sun_core,sun_corona,low_clouds,bottom_clouds,water,sun_size,sprite_size,sprite_bright,"
                     "shadow,light_shadow,pole_shadow,far_clip,fog_start,light_on_ground,data) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", trows)

    # ---- car colours
    t = text_of("carcols")
    if t is not None:
        text, rel, sid = t
        errs = []
        cc = parse_carcols(text, errors=errs)
        conn.executemany("INSERT INTO carcol(idx,rgb,name,radio,source_id,line) VALUES (?,?,?,?,?,?)",
                         [(p.idx, p.rgb, p.name, p.radio, sid, p.line) for p in cc.palette])
        crows = []
        for c in sorted(cc.cars.values(), key=lambda c: c.line):
            for i, s in enumerate(c.sets):
                crows.append((c.name, i, s[0], s[1], s[2] if c.four else None, s[3] if c.four else None, sid, c.line))
        conn.executemany("INSERT INTO car_color(model,idx,c1,c2,c3,c4,source_id,line) VALUES (?,?,?,?,?,?,?,?)", crows)
        if cc.duplicates:
            errs.append((0, f"carcols: {cc.duplicates} model(s) listed twice; the later line wins"))
        note(rel, errs)

    # ---- handling
    t = text_of("handling")
    if t is not None:
        text, rel, sid = t
        errs = []
        h = parse_handling(text, errors=errs)
        hrows = []
        for r in sorted(h, key=lambda r: r.line):
            v = r.values
            car = r.kind == "car"
            hrows.append((sid, r.name, r.kind, r.line,
                          *((v["mass"], v["turn_mass"], v["drag"], v["max_vel"], v["accel"], v["gears"], v["drive"],
                             v["engine"], v["brake"], v["steer_lock"], v["value"], v["model_flags"],
                             v["handling_flags"]) if car else (None,) * 13), _dumps(v)))
        conn.executemany("INSERT INTO handling(source_id,name,kind,line,mass,turn_mass,drag,max_vel,accel,gears,drive,"
                         "engine,brake,steer_lock,value,model_flags,handling_flags,data) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", hrows)
        stats["handling_anim_groups"] = h.anim_groups
        if h.duplicates:
            errs.append((0, f"handling: {h.duplicates} line(s) replaced by a later line with the same id"))
        note(rel, errs)

    # ---- ped types
    t = text_of("ped")
    if t is not None:
        text, rel, sid = t
        prow: dict[tuple[str, str, str], tuple] = {}
        for ptype, relname, others, ln in parse_ped_dat(text):
            for o in others:
                prow.setdefault((ptype.lower(), relname, o.lower()), (ptype, relname, o, sid, ln))
        conn.executemany("INSERT INTO ped_rel(pedtype,rel,other,source_id,line) VALUES (?,?,?,?,?)",
                         sorted(prow.values(), key=lambda r: (r[4], r[0].lower(), r[2].lower())))

    # ---- object.dat
    t = text_of("object")
    if t is not None:
        text, rel, sid = t
        errs = []
        orows = []
        for o in parse_object_dat(text, errors=errs):
            v = o.values
            orows.append((sid, o.name, o.line, int(o.loaded), v.get("mass"), v.get("turn_mass"), v.get("air_res"),
                          v.get("elasticity"), v.get("submerged"), v.get("uproot"), v.get("dmg_mult"),
                          v.get("dmg_effect"), v.get("col_response"), v.get("cam_avoid"), v.get("explodes"),
                          v.get("fx_type"), v.get("fx_name"), _dumps(v)))
        conn.executemany("INSERT INTO object_data(source_id,name,line,loaded,mass,turn_mass,air_res,elasticity,"
                         "submerged,uproot,dmg_mult,dmg_effect,col_response,cam_avoid,explodes,fx_type,fx_name,data) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", orows)
        note(rel, errs)

    if missing:
        stats["data_missing"] = missing
    for table in ("water_quad", "timecyc", "carcol", "car_color", "handling", "ped_rel", "object_data"):
        stats[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return read


def fts_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Search rows ``(sid, kind, name)``: one ``handling:<id>`` per handling id, one ``tcyc:<weather>`` per weather."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for (name,) in conn.execute("SELECT name FROM handling ORDER BY line"):
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append((f"handling:{name.lower()}", "handling", name))
    for (wname,) in conn.execute("SELECT weather_name FROM timecyc WHERE hour = (SELECT min(hour) FROM timecyc) "
                                 "ORDER BY weather"):
        out.append((f"tcyc:{wname.lower()}", "tcyc", wname))
    return out
