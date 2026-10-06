"""``satk mod add``: a Mod Loader folder that adds a new model next to a donor, with every data line it needs.

The donor's lines are copied token by token (only the id, names and handling id change, every other byte of
the line stays), so the new model behaves like the donor until its lines are edited:

=========  ==========================================================================================
kind       files and lines (``<name>.txt`` = Mod Loader readme lines; full copies merge per record)
=========  ==========================================================================================
vehicle    ``<name>.dff/.txd``; readme: vehicles.ide line, handling.cfg lines (standard + ``!`` ``%`` ``$``
           of the donor's handling id, under a new id unless ``--handling donor``), carcols.dat line
           (``car`` or ``car4``), carmods.dat line; ``<name>.fxt`` (GXT key -> shown name);
           ``data/cargrp.dat`` with the name added to groups (``--cargrp``)
ped        ``<name>.dff/.txd``; readme: peds.ide line (donor's ped type, stats, anim group, voices)
weapon     ``<name>.dff/.txd/.col``; ``data/maps/<name>.ide`` (``weap`` line) + readme ``IDE`` line for
           gta.dat; readme weapon.dat lines of the donor's weapon type with the new model
object     ``<name>.dff[/.txd]/.col``; ``data/maps/<name>.ide`` (``objs``/``tobj`` line) + readme ``IDE`` line;
           ``data/object.dat`` with a copy of the donor's physics line (if it has one); with ``--lod-dff`` also
           ``<lod>.dff[/.txd]`` and a second ``objs`` line (draw 800, the vanilla LOD median) with the next free id;
           with ``--place X,Y,Z`` a ``data/maps/<name>.ipl`` whose HD ``inst`` line holds the index of the LOD ``inst``
=========  ==========================================================================================

Mod Loader reads readme lines only for vehicles.ide/peds.ide (``cars``/``peds``), handling.cfg, carcols.dat,
carmods.dat, gta.dat and weapon.dat (gun and melee lines); every generated readme line is checked against
those patterns (ported in :mod:`satk.modinspect.traits`; carmods and weapon.dat below), and a line that would
not be read falls back to a file Mod Loader merges. Data files Mod Loader merges are written as full copies of
the profile's file plus the change (a partial file would drop the records it lacks).

Checks: the donor exists and has the right kind; the id is free in the profile (idmgr, also modloader mods
when the profile index is built); the model, DFF, TXD and COL names are not used by the game or a mod; the
vehicle GXT key (7 characters, the engine keeps 8 bytes) and handling id (13 characters, 210 stock names) are
free. The game is only read.

Stdlib only.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.envelope import table
from ..core.errors import SatkError
from ..core.paths import atomic_write, ensure_removable, jpath, open_ro
from ..formats.rw import FormatError
from ..modinspect.traits import CarcolsTrait, GtaDatTrait, HandlingTrait, IdeTrait, trim_config_line
from . import fields as F
from .data import readme_header
from .game import GameData, parse_cargrp, parse_carmods, tok

__all__ = ["add", "NAME_MAX", "GXT_MAX", "HANDLING_ID_MAX", "readme_carmods_ok", "readme_weapon_ok"]

#: Longest model name: ``<name>.dff`` must fit the 24-byte IMG/stream entry name with its terminator.
NAME_MAX = 19
#: IDE draw distance of a LOD model (the vanilla LOD median; ``data/kit/kinds.json`` building.lod.draw).
LOD_DRAW = 800
#: Vehicle GXT key: ``CVehicleModelInfo::m_szGameName[8]``.
GXT_MAX = 7
#: Handling id: ``VehicleNames[210][14]``.
HANDLING_ID_MAX = 13
#: Models the engine keeps per car group of cargrp.dat (``CPopulation::m_CarGroups[..][23]``); more are ignored.
CARGRP_MAX = 23
_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_MAX_INPUT = 64 << 20

# Mod Loader 0.3.7 std.data readme patterns (MIT, LINK/2012; see satk/modinspect/NOTICE-modloader.txt).
_CARMODS_README = re.compile(
    r"^(\w+)(?:\s+(?:hydralics|stereo|nto_\w+|bnt_\w+|chss_\w+|exh_\w+|bntl_\w+|bntr_\w+|spl_\w+|wg_l_\w+|wg_r_\w+|"
    r"fbb_\w+|bbb_\w+|lgt_\w+|rf_\w+|fbmp_\w+|rbmp_\w+|misc_a_\w+|misc_b_\w+|misc_c_\w+))+\s*$", re.A)
_F = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_D = r"[+-]?\d+"
_X = r"[+-]?(?:0[xX])?[\dA-Fa-f]+"
_S = r"\S+"
_WBASE = rf"^.\s+{_S}\s+(?:MELEE|INSTANT_HIT|PROJECTILE|AREA_EFFECT|CAMERA|USE)\s+{_F}\s+{_F}\s+{_D}\s+{_D}\s+{_D}"
_WMELEE = re.compile(_WBASE + r"\s+(?:UNARMED|BBALLBAT|KNIFE|GOLFCLUB|SWORD|CHAINSAW|DILDO|FLOWERS)"
                     + rf"\s+{_D}\s+{_X}\s+{_S}\s*$", re.A)
_WGUN = re.compile(_WBASE + rf"\s+{_S}\s+{_D}\s+{_D}" + rf"\s+{_F}" * 3 + rf"\s+{_D}\s+{_D}\s+{_F}\s+{_F}"
                   + rf"\s+{_D}" * 7 + rf"\s+{_X}" + rf"(?:\s+{_F})?" * 4 + r"\s*$", re.A)


def readme_carmods_ok(line: str) -> bool:
    """Mod Loader would read this carmods.dat ``mods`` line from a readme."""
    return bool(_CARMODS_README.match(trim_config_line(line)))


def readme_weapon_ok(line: str) -> bool:
    """Mod Loader would read this weapon.dat gun or melee line from a readme."""
    s = trim_config_line(line)
    if not s:
        return False
    if s[0] == "$":
        return bool(_WGUN.match(s))
    if s[0] == "£":
        return bool(_WMELEE.match(s))
    return False


# --------------------------------------------------------------------------- inputs


def _rw_trim(data: bytes) -> bytes:
    """IMG entry bytes without the sector padding: the top-level RW chunks only."""
    import struct

    off = 0
    while off + 12 <= len(data):
        t, size, _lib = struct.unpack_from("<III", data, off)
        if t == 0 or off + 12 + size > len(data):
            break
        off += 12 + size
    return data[:off] if off else data


def _read_input(gd: GameData | None, path: str | None, what: str, ext: str) -> tuple[str, bytes]:
    """``(label, bytes)`` of ``--dff/--txd/--col``: a file, or ``dff:<name>``/``txd:<name>`` from the profile's IMGs."""
    if not path:
        raise SatkError("BAD_PARAMS", f"give --{what} <file{ext}>",
                        hint="satk mod add vehicle --dff a.dff --txd a.txd --like 411 --name mycar")
    head, sep, rest = path.partition(":")
    if sep and head.lower() == what and what in ("dff", "txd") and len(head) > 1 and gd is not None:
        data = gd.base.read_entry(f"{rest}.{what}")
        if data is None:
            raise SatkError("NOT_FOUND", f"--{what} {path}: the IMG archives of profile {gd.profile!r} have no "
                                         f"{rest}.{what}", hint=f"satk asset find {rest} --kind {what}")
        return path, _rw_trim(data)
    p = Path(path)
    if not p.is_file():
        hint = f"a file path, or {what}:<name> to copy the game's {what.upper()}" if what != "col" else None
        raise SatkError("NOT_FOUND", f"--{what}: no file {path!r}", hint=hint)
    if p.stat().st_size > _MAX_INPUT:
        raise SatkError("BAD_PARAMS", f"--{what}: {jpath(p)} is larger than 64 MB")
    with open_ro(p) as f:
        return jpath(p), f.read()


def _check_name(name: str | None) -> str:
    if not name:
        raise SatkError("BAD_PARAMS", "give --name <model name>", hint="--name mycar (letters, digits, _; <= 19)")
    if not _NAME.match(name) or len(name) > NAME_MAX:
        raise SatkError("BAD_PARAMS", f"bad --name {name!r}: use letters, digits and _ (at most {NAME_MAX} characters, "
                                      "so that '<name>.dff' fits a 24-byte IMG entry name)")
    return name.lower()


# --------------------------------------------------------------------------- ids and names


def _taken(gd: GameData, profile: str, kind: str, warn: list[str]):
    """``(taken ids, max id, defs-by-name, index db or None)`` from the profile index, else from its IDE files."""
    from ..idmgr.free import open_profiles, taken_ids
    from ..idmgr.ranges import ENGINE_RESERVED, MAX_ID, SAMP_RESERVED, iter_range

    try:
        dbs = open_profiles([profile])
    except SatkError as e:
        if e.code not in ("INDEX_MISSING", "NOT_READY"):
            raise
        warn.append(f"NO_INDEX: profile {profile!r} has no index: ids and names were checked against its IDE files "
                    f"only (modloader mods not seen); satk index build --profile {profile}")
        ids: dict[int, str] = {}
        for m in gd.base.models.values():
            ids.setdefault(m.id, f"{profile}: {m.name} ({m.sec}, {m.ide})")
        for a, b, why in ENGINE_RESERVED + SAMP_RESERVED:
            for i in iter_range(a, b):
                ids.setdefault(i, why)
        return ids, MAX_ID, {}, None
    t = taken_ids(dbs)
    warn += t.hints
    names: dict[str, str] = {}
    for prof, defs in t.defs.items():
        for d in defs:
            names.setdefault(d.name.lower(), f"{prof}: model:{d.id} ({d.owner}, {d.where})")
    return t.ids, t.max_id, names, dbs[0][1]


def _pick_id(id_: str, kind: str, taken: dict[int, str], max_id: int, warn: list[str]) -> int:
    from ..idmgr.free import free_ids
    from ..idmgr.ranges import DEFAULT_RANGE, VANILLA_VEHICLES

    if str(id_).strip().lower() in ("", "auto"):
        ids, _total = free_ids(taken, [(DEFAULT_RANGE[kind][0], max_id)], 1)
        if not ids:
            raise SatkError("NOT_FOUND", f"no free {kind} id up to {max_id}", hint="satk id free --kind " + kind)
        mid = ids[0]
    else:
        try:
            mid = int(str(id_).strip())
        except ValueError:
            raise SatkError("BAD_PARAMS", f"bad --id {id_!r}", hint="--id auto or a number") from None
        if mid <= 0:
            raise SatkError("BAD_PARAMS", f"--id must be positive, got {mid}")
        if mid in taken:
            raise SatkError("EXISTS", f"model id {mid} is taken: {taken[mid]}", hint=f"satk id free --kind {kind}",
                            data={"id": mid})
        if mid > max_id:
            warn.append(f"OVER_LIMIT: id {mid} is above the model limit {max_id}; it needs a limit adjuster that "
                        "raises the number of model ids (fastman92 LA)")
    if kind == "vehicle" and not VANILLA_VEHICLES[0] <= mid <= VANILLA_VEHICLES[1]:
        warn.append(f"ADDON_VEHICLE: vehicle id {mid} is outside 400-611: the game needs fastman92 LA (or another "
                    "limit adjuster) for add-on vehicles")
    return mid


def _index_files(db, names: list[str]) -> dict[str, str]:
    """IMG entries or loose files (also unloaded ones, e.g. in modloader/) of the profile index with these names."""
    if db is None or not names:
        return {}
    from ..idmgr.scan import rows

    qs = ",".join("?" * len(names))
    likes = " OR ".join("lower(s.relpath) LIKE ?" for _ in names)
    out: dict[str, str] = {}
    try:
        got = rows(db, "SELECT b.name, s.relpath FROM blob b JOIN source s ON s.id = b.source_id "
                       f"WHERE b.name IN ({qs}) ORDER BY s.relpath", names)
        got += rows(db, f"SELECT s.relpath, s.relpath FROM source s WHERE {likes} ORDER BY s.relpath",
                    [f"%{n.lower()}" for n in names])
    except SatkError:
        return {}
    for n, rel in got:
        base = str(n).replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base in names:
            out.setdefault(base, str(rel))
    return out


def _check_names(gd: GameData, name: str, files: list[str], index_names: dict[str, str], db) -> None:
    hit = gd.base.by_name.get(name)
    if hit is not None:
        raise SatkError("EXISTS", f"model name {name!r} is taken by model:{hit.id} ({hit.ide})",
                        hint="pick another --name")
    if name in index_names:
        raise SatkError("EXISTS", f"model name {name!r} is taken: {index_names[name]}", hint="pick another --name")
    idx = _index_files(db, files)
    for f in files:
        img = gd.base.img_names.get(f)
        if img is not None:
            raise SatkError("EXISTS", f"{f} exists in {img[0]}: Mod Loader would replace the game's file",
                            hint="pick another --name")
        if f in idx:
            raise SatkError("EXISTS", f"{f} exists in {idx[f]}: the two files would replace each other",
                            hint="pick another --name")


def _gxt_key(gd: GameData, name: str, warn: list[str]) -> str:
    from ..formats.gxt import key_hash

    used = gd.car_gxt_keys
    main = gd.gxt_main or set()
    base = name.upper()[:GXT_MAX]
    cands = [base] + [base[:GXT_MAX - 1] + str(d) for d in range(1, 10)] + \
            [base[:GXT_MAX - 2] + f"{d:02d}" for d in range(10, 100)]
    for c in cands:
        if c not in used and key_hash(c) not in main:
            if c != base:
                warn.append(f"GXT_KEY: GXT key {base} is used by the game; the vehicle uses {c}")
            return c
    raise SatkError("EXISTS", f"no free GXT key for {name!r}", hint="pick another --name")


# --------------------------------------------------------------------------- lines


def _ide_copy(text: str, mid: int, name: str, txd: str | None) -> tuple[object, str]:
    tl = tok(text)
    tl.set(0, str(mid))
    tl.set(1, name)
    if txd is not None:
        tl.set(2, txd)
    return tl, tl.text(eol=False)


def _vehicle(gd: GameData, donor, ml, mid: int, name: str, handling: str, cargrp: list[str] | None,
             game_name: str, warn: list[str]) -> tuple[list[tuple[str, str]], dict[str, bytes], list[list]]:
    """Readme sections, extra files and table rows of a vehicle."""
    readme: list[tuple[str, str]] = []
    files: dict[str, bytes] = {}
    rows: list[list] = []
    rel_h, hf = gd.handling
    donor_hid = ml.rec.extra.get("handling")
    donor_hid = str(donor_hid) if donor_hid is not None else ""
    if handling == "own":
        hid = name.upper()
        if len(hid) > HANDLING_ID_MAX:
            raise SatkError("BAD_PARAMS", f"handling id {hid} is longer than {HANDLING_ID_MAX} characters",
                            hint="a shorter --name, or --handling donor")
        if hf.get(hid) is not None:
            raise SatkError("EXISTS", f"handling id {hid} already exists in {rel_h}",
                            hint="pick another --name, or --handling donor")
        warn.append(f"NEW_HANDLING_ID: the stock engine knows only its 210 handling ids; {hid} needs a limit adjuster "
                    "that accepts new handling names (fastman92 LA), or use --handling donor")
    else:
        hid = donor_hid
    gxt = _gxt_key(gd, name, warn)
    tl = tok(ml.text)
    for i, v in ((0, str(mid)), (1, name), (2, name), (4, hid), (5, gxt)):
        tl.set(i, v)
    ide_line = tl.text(eol=False)
    if IdeTrait().readme_section(trim_config_line(ide_line)) != "cars":
        raise SatkError("UNSUPPORTED", f"the donor's vehicles.ide line would not be read by Mod Loader from a readme: "
                                       f"{ml.text.strip()!r}", hint="pick another donor")
    readme.append(("vehicles.ide", ide_line))
    rows.append(["vehicles.ide", ide_line])
    # handling.cfg
    if handling == "own":
        recs = hf.kinds_of(donor_hid)
        if not recs:
            raise SatkError("NOT_FOUND", f"{rel_h} has no line for the donor's handling id {donor_hid}",
                            hint="--handling donor")
        hl = []
        for r in recs:
            text = r.patched({}, new_id=hid).text
            if HandlingTrait().readme_rec(trim_config_line(text), 0, frozenset()) is None:
                hl = None
                break
            hl.append(text)
        if hl is None:
            texts = [r.patched({}, new_id=hid).text for r in recs]
            new = hf.render()
            files["data/handling.cfg"] = _insert_before_end(new, texts, hf.end).encode("latin-1")
            warn.append("README_FALLBACK: a donor handling line does not match Mod Loader's readme pattern: wrote "
                        "data/handling.cfg (the game's file plus the new lines) instead")
            rows += [["data/handling.cfg", t] for t in texts]
        else:
            readme += [("handling.cfg", t) for t in hl]
            rows += [["handling.cfg", t] for t in hl]
    # carcols.dat
    rel_c, cc, ctext = gd.carcols
    if cc is not None and donor.name.lower() in cc.cars:
        entry = cc.cars[donor.name.lower()]
        raw = ctext.splitlines()[entry.line - 1]
        ctl = tok(raw)
        ctl.set(0, name)
        line = ctl.text(eol=False).rstrip()
        if CarcolsTrait().readme_rec(trim_config_line(line), 0, frozenset({name})) is None:
            line = f"{name}, " + ", ".join(",".join(map(str, s)) for s in entry.sets)
        readme.append(("carcols.dat", line))
        rows.append(["carcols.dat", line])
    else:
        warn.append(f"NO_COLOURS: carcols.dat has no line for {donor.name}: the vehicle gets the default colours")
    # carmods.dat
    got = gd.data("carmods.dat", required=False)
    if got is not None:
        mods = parse_carmods(got[2])
        if donor.name.lower() in mods:
            n, _ups = mods[donor.name.lower()]
            mtl = tok(got[2].splitlines()[n - 1])
            mtl.set(0, name)
            line = mtl.text(eol=False).rstrip()
            if readme_carmods_ok(line):
                readme.append(("carmods.dat", line))
                rows.append(["carmods.dat", line])
            else:
                warn.append("README_SKIPPED: the donor's carmods.dat line does not match Mod Loader's readme pattern")
    # cargrp.dat
    if cargrp:
        got = gd.data("cargrp.dat")
        rel_g, _p, gtext = got
        groups = parse_cargrp(gtext)
        sel = _select_groups(groups, cargrp, donor.name)
        lines = gtext.splitlines(keepends=True)
        for g in sel:
            if len(g.models) >= CARGRP_MAX:
                warn.append(f"CARGRP_FULL: car group {g.idx} already lists {len(g.models)} models; the engine reads "
                            f"only the first {CARGRP_MAX} of a group, so {name} (added at the end) is not used there")
            gtl = tok(lines[g.line - 1])
            gtl.insert_after(len(gtl) - 1, ", ", name)
            lines[g.line - 1] = gtl.text()
            rows.append([f"data/cargrp.dat:{g.line}", f"group {g.idx}" + (f" ({g.label})" if g.label else "")])
        files["data/cargrp.dat"] = "".join(lines).encode("latin-1")
    # FXT
    text = game_name.replace("\r", " ").replace("\n", " ").strip() or name
    enc = text.encode("latin-1", errors="replace")
    if b"?" in enc and "?" not in text:
        warn.append("GAME_NAME: characters outside latin-1 were replaced by '?'")
    files[f"{name}.fxt"] = b"# satk add-on: GXT key -> name shown in game\r\n" + gxt.encode() + b" " + enc + b"\r\n"
    rows.append([f"{name}.fxt", f"{gxt} {text}"])
    return readme, files, rows


def _insert_before_end(text: str, new: list[str], end_line: int | None) -> str:
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if text.endswith("\r\n") or "\r\n" in text[:4096] else "\n"
    at = (end_line - 1) if end_line else len(lines)
    if at == len(lines) and lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += eol
    lines[at:at] = [t + eol for t in new]
    return "".join(lines)


def _select_groups(groups, wanted: list[str], donor: str):
    def label_key(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

    out = []
    for w in wanted:
        w = w.strip()
        if w.lower() == "donor":
            hits = [g for g in groups if donor.lower() in (m.lower() for m in g.models)]
            if not hits:
                raise SatkError("NOT_FOUND", f"the donor {donor} is in no car group of cargrp.dat",
                                hint="give group numbers or labels: --cargrp 0 workers")
        elif w.isdigit():
            hits = [g for g in groups if g.idx == int(w)]
            if not hits:
                raise SatkError("NOT_FOUND", f"cargrp.dat has {len(groups)} groups (0-{len(groups) - 1}), not {w}")
        else:
            k = label_key(w)
            keys = {g.idx: label_key(g.label) for g in groups}
            hits = [g for g in groups if keys[g.idx] in (k, f"popcycle_group_{k}")]
            if not hits:
                hits = [g for g in groups if keys[g.idx].startswith(k) or keys[g.idx].startswith(f"popcycle_group_{k}")]
            if len(hits) != 1:
                raise SatkError("AMBIGUOUS" if hits else "NOT_FOUND", f"car group {w!r} matches {len(hits)} groups",
                                did_you_mean=[f"{g.idx}: {g.label}" for g in (hits or groups)][:8])
        out += [g for g in hits if g not in out]
    return sorted(out, key=lambda g: g.idx)


def _ide_file(name: str, sec: str, line: str, extra: list[tuple[str, str]] | None = None) -> tuple[str, bytes, str]:
    """``data/maps/<name>.ide`` with ``line`` in section ``sec``; ``extra`` = more ``(section, line)`` (the LOD line)."""
    rel = f"data/maps/{name}.ide"
    parts = [f"# satk add-on {name}", sec, line, "end"]
    for esec, eline in extra or []:
        parts += [esec, eline, "end"]
    body = ("\r\n".join(parts) + "\r\n").encode("latin-1")
    dat = f"IDE DATA\\MAPS\\{name}.ide"
    if GtaDatTrait().readme_rec(trim_config_line(dat), 0, frozenset()) is None:  # pragma: no cover - fixed form
        raise SatkError("INTERNAL", f"gta.dat readme line {dat!r} does not match Mod Loader's pattern")
    return rel, body, dat


def _fmt(v: float) -> str:
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _ipl_file(name: str, hd: tuple[int, str], lod: tuple[int, str] | None, at: list[float]) -> tuple[str, bytes, str]:
    """``data/maps/<name>.ipl``: the HD ``inst`` (and the LOD ``inst`` it points to) at ``at``, and the readme line."""
    x, y, z = (_fmt(v) for v in at)
    rows = [f"{hd[0]}, {hd[1]}, 0, {x}, {y}, {z}, 0, 0, 0, 1, {1 if lod else -1}"]
    if lod:
        rows.append(f"{lod[0]}, {lod[1]}, 0, {x}, {y}, {z}, 0, 0, 0, 1, -1")
    body = ("\r\n".join([f"# satk add-on {name}", "inst", *rows, "end"]) + "\r\n").encode("latin-1")
    dat = f"IPL DATA\\MAPS\\{name}.ipl"
    if GtaDatTrait().readme_rec(trim_config_line(dat), 0, frozenset()) is None:
        raise SatkError("INTERNAL", f"gta.dat readme line {dat!r} does not match Mod Loader's pattern")
    return f"data/maps/{name}.ipl", body, dat


def _check_lod_name(name: str | None, hd: str) -> str:
    n = str(name or "").strip().lower()
    if not n or not _NAME.match(n) or len(n) > NAME_MAX:
        raise SatkError("BAD_PARAMS", f"bad LOD name {name!r}: use letters, digits and _ (at most {NAME_MAX} characters)",
                        hint="--lod-name lod_mybin (default: the stem of --lod-dff)")
    if n == hd:
        raise SatkError("BAD_PARAMS", f"the LOD model needs a name of its own, not {hd!r}",
                        hint="name the file lod<name> (the vanilla form) or pass --lod-name")
    return n


def _weapon(gd: GameData, donor, mid: int, name: str, weapon_type: str | None, warn: list[str]
            ) -> tuple[list[tuple[str, str]], list[list]]:
    readme: list[tuple[str, str]] = []
    rows: list[list] = []
    wt = (weapon_type or "donor").strip()
    if wt.lower() == "none":
        return readme, rows
    rel_w, wf = gd.weapons
    recs = [r for r in wf.recs.values()
            if r.kind != "aim" and donor.id in (r.values.get("model1"), r.values.get("model2"))]
    recs.sort(key=lambda r: r.line)
    if not recs:
        warn.append(f"NO_WEAPON_LINES: {rel_w} has no line that uses model:{donor.id} {donor.name}: only the model is "
                    "registered")
        return readme, rows
    new_type = None
    if wt.lower() != "donor":
        new_type = wt.upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,30}", new_type):
            raise SatkError("BAD_PARAMS", f"bad --weapon-type {weapon_type!r}", hint="donor, none or a NEW_NAME")
        if wf.by_type(new_type):
            raise SatkError("EXISTS", f"weapon type {new_type} exists in {rel_w}",
                            hint="--weapon-type donor gives the existing type the new model")
        warn.append(f"NEW_WEAPON_TYPE: the stock game has a fixed list of weapon types; {new_type} needs fastman92 "
                    "LA's weapon type loader")
    names = sorted({r.name.upper() for r in recs})
    if new_type is None:
        warn.append(f"WEAPON_TYPE: {', '.join(names)} {'now uses' if len(names) == 1 else 'now use'} model {mid} "
                    f"instead of {donor.id} {donor.name} "
                    "(the original model stays defined); --weapon-type none keeps weapon.dat unchanged")
    flist = {k: [f["key"] for f in F.table("weapon")[k]] for k in ("gun", "melee")}
    for r in recs:
        ch = {}
        for k in ("model1", "model2"):
            if r.values.get(k) == donor.id:
                ch[k] = (mid, None)
        new = r.patched(ch)
        if new_type is not None:
            new.tl.set(new.first + flist[r.kind].index("type"), new_type)
        text = new.text
        if not readme_weapon_ok(text):
            raise SatkError("UNSUPPORTED", f"weapon.dat line {r.line} would not be read by Mod Loader from a readme",
                            hint="--weapon-type none, then edit weapon.dat by hand")
        readme.append(("weapon.dat", text))
        rows.append(["weapon.dat", text])
    return readme, rows


def _col(col_bytes: bytes | None, name: str, kind: str, warn: list[str], donor: str = "") -> bytes | None:
    from ..formats.col import iter_col

    if col_bytes is None:
        if kind == "object":
            warn.append("NO_COL: no --col: the object has no collision (it is not solid; give a COL whose model is "
                        f"named {name})")
        elif kind == "weapon":
            warn.append("NO_COL: no --col: a dropped weapon or pickup of this model has no collision")
        return None
    errs: list = []
    try:
        models = list(iter_col(col_bytes, errors=errs))
    except FormatError as e:
        raise SatkError("BAD_PARAMS", f"--col is not a valid COL file: {e}") from None
    if not models:
        raise SatkError("BAD_PARAMS", "--col holds no collision model")
    offs, off = [], 0
    for m in models:
        offs.append(off)
        off += m.size
    pick = next((i for i, m in enumerate(models) if m.name.lower() == name), None)
    how = ""
    if pick is None and len(models) == 1:
        pick, how = 0, "renamed"
    if pick is None and donor:
        pick = next((i for i, m in enumerate(models) if m.name.lower() == donor.lower()), None)
        how = "extracted and renamed"
    if pick is None:
        raise SatkError("BAD_PARAMS", f"--col has {len(models)} models and none is named {name!r} or {donor!r}",
                        hint="give a COL with one model (it is renamed) or with a model of the new or the donor's name")
    m = models[pick]
    one = bytearray(col_bytes[offs[pick]:offs[pick] + m.size])
    if m.name.lower() != name:
        if len(name) > 21:
            raise SatkError("BAD_PARAMS", f"COL model names have at most 21 characters; {name!r} is longer")
        one[8:30] = name.encode("ascii").ljust(22, b"\0")
        warn.append(f"COL_RENAMED: the COL model {m.name!r} was {how} to {name!r} (models find their collision "
                    "by name)")
    elif len(models) > 1:
        warn.append(f"COL_EXTRACTED: only the model {name!r} of the {len(models)} in --col was kept")
    return bytes(one)


def _check_dff(dff_bytes: bytes, txd_names: set[str] | None, kind: str, gd: GameData, warn: list[str]) -> None:
    from ..formats.dff import scan_dff

    try:
        info = scan_dff(dff_bytes)
    except FormatError as e:
        raise SatkError("BAD_PARAMS", f"--dff is not a valid DFF: {e}", hint="satk asset lint <file.dff>") from None
    if kind == "vehicle" and not info.flags & 64:
        warn.append("NO_EMBEDDED_COL: vehicle DFFs carry their collision inside; this one has none")
    if kind == "ped" and not info.flags & 1:
        warn.append("NO_SKIN: ped models are skinned; this DFF has no skin")
    if txd_names is None:
        return
    used = {m.texture.lower() for m in info.materials if m.texture}
    missing = used - txd_names
    if missing and kind == "vehicle":
        from ..formats.dat import resolve_ci
        from ..formats.txd import parse_txd

        p = resolve_ci(gd.base.root, "models/generic/vehicle.txd")
        if p is not None:
            try:
                with open_ro(p) as f:
                    missing -= {t.name.lower() for t in parse_txd(f.read()).textures}
            except (FormatError, OSError):
                pass
    if missing:
        names = sorted(missing)
        warn.append(f"MISSING_TEXTURES: {len(names)} texture(s) the DFF uses are not in the TXD: "
                    f"{', '.join(names[:6])}")


# --------------------------------------------------------------------------- main


def add(kind: str, *, dff: str | None, txd: str | None, col: str | None, like: str | None, name: str | None,
        id_: str = "auto", game_name: str | None = None, handling: str = "own", cargrp: list[str] | None = None,
        weapon_type: str | None = None, profile: str = "installed", out: str | None = None, force: bool = False,
        dry_run: bool = False, lod_dff: str | None = None, lod_txd: str | None = None, lod_name: str | None = None,
        place: list[float] | None = None) -> dict:
    from ..formats.txd import parse_txd
    from .data import out_dir

    warn: list[str] = []
    if not like:
        raise SatkError("BAD_PARAMS", "give --like <donor model>", hint="--like 411 (model:411, infernus)")
    nm = _check_name(name)
    gd = GameData(profile)
    _l, dff_bytes = _read_input(gd, dff, "dff", ".dff")
    txd_bytes = None
    txd_names = None
    if txd or kind != "object":
        _l, txd_bytes = _read_input(gd, txd, "txd", ".txd")
        try:
            txd_names = {t.name.lower() for t in parse_txd(txd_bytes).textures}
        except FormatError as e:
            raise SatkError("BAD_PARAMS", f"--txd is not a valid TXD: {e}") from None
    col_bytes = None
    if col:
        if kind in ("vehicle", "ped"):
            warn.append("COL_IGNORED: vehicles keep their collision inside the DFF and peds have none: --col ignored")
        else:
            col_bytes = _read_input(gd, col, "col", ".col")[1]
    if kind not in ("vehicle", "weapon") and weapon_type:
        warn.append("WEAPON_TYPE_IGNORED: --weapon-type is for weapons")
    if kind != "vehicle" and (cargrp or game_name):
        warn.append("VEHICLE_ONLY: --cargrp and --game-name are for vehicles")
    lod_bytes = lod_txd_bytes = lod_nm = lod_txd_names = None
    if lod_dff:
        if kind != "object":
            raise SatkError("BAD_PARAMS", "--lod-dff is for objects: only a map object has a LOD model",
                            hint="satk mod add object --dff hd.dff --lod-dff lodhd.dff --like <donor> --name hd")
        _l, lod_bytes = _read_input(gd, lod_dff, "dff", ".dff")
        lod_nm = _check_lod_name(lod_name or Path(lod_dff.partition(":")[2] if lod_dff.lower().startswith("dff:")
                                                  else lod_dff).stem, nm)
        if lod_txd:
            _l, lod_txd_bytes = _read_input(gd, lod_txd, "txd", ".txd")
            try:
                lod_txd_names = {t.name.lower() for t in parse_txd(lod_txd_bytes).textures}
            except FormatError as e:
                raise SatkError("BAD_PARAMS", f"--lod-txd is not a valid TXD: {e}") from None
    elif lod_txd or lod_name:
        raise SatkError("BAD_PARAMS", "--lod-txd and --lod-name need --lod-dff")
    if place is not None:
        if kind != "object":
            raise SatkError("BAD_PARAMS", "--place is for objects")
        if len(place) != 3:
            raise SatkError("BAD_PARAMS", "--place takes X,Y,Z (the world position of the object)")

    donor = gd.model(like, kind)
    ml = gd.model_line(donor)
    taken, max_id, index_names, db = _taken(gd, profile, kind, warn)
    mid = _pick_id(id_, kind, taken, max_id, warn)
    txd_name = nm if txd_bytes is not None else (donor.txd or nm)
    files_new = [f"{nm}.dff"] + ([f"{nm}.txd"] if txd_bytes is not None else []) + \
                ([f"{nm}.col"] if col_bytes is not None else [])
    _check_names(gd, nm, files_new, index_names, db)
    _check_dff(dff_bytes, txd_names, kind, gd, warn)
    lod_id = None
    lod_txd_name = txd_name
    if lod_bytes is not None:
        lod_txd_name = lod_nm if lod_txd_bytes is not None else txd_name
        lod_files = [f"{lod_nm}.dff"] + ([f"{lod_nm}.txd"] if lod_txd_bytes is not None else [])
        _check_names(gd, lod_nm, lod_files, index_names, db)
        _check_dff(lod_bytes, lod_txd_names if lod_txd_names is not None else txd_names, kind, gd, warn)
        lod_id = _pick_id("auto" if str(id_).strip().lower() in ("", "auto") else str(mid + 1), kind,
                          {**taken, mid: "this add-on"}, max_id, warn)
    col_out = _col(col_bytes, nm, kind, warn, donor.name) if kind in ("weapon", "object") else None

    readme: list[tuple[str, str]] = []
    extra: dict[str, bytes] = {}
    rows: list[list] = []
    if kind == "vehicle":
        readme, extra, rows = _vehicle(gd, donor, ml, mid, nm, handling, cargrp, game_name or (name or nm), warn)
    elif kind == "ped":
        _tl, line = _ide_copy(ml.text, mid, nm, txd_name)
        if IdeTrait().readme_section(trim_config_line(line)) == "peds":
            readme.append(("peds.ide", line))
            rows.append(["peds.ide", line])
        else:
            rel, body, dat = _ide_file(nm, "peds", line)
            extra[rel] = body
            readme.append(("gta.dat", dat))
            rows += [[rel, line], ["gta.dat", dat]]
            warn.append("README_FALLBACK: the donor's peds.ide line does not match Mod Loader's readme pattern: wrote "
                        f"{rel} and a gta.dat line")
    else:
        _tl, line = _ide_copy(ml.text, mid, nm, txd_name)
        lod_line = f"{lod_id}, {lod_nm}, {lod_txd_name}, {LOD_DRAW}, 0" if lod_id is not None else None
        more: list[tuple[str, str]] = []
        if lod_line and donor.sec == "objs":
            line = line + "\r\n" + lod_line
        elif lod_line:
            more.append(("objs", lod_line))
        rel, body, dat = _ide_file(nm, donor.sec, line, more)
        extra[rel] = body
        readme.append(("gta.dat", dat))
        rows += [[rel, line.replace("\r\n", " | ")], ["gta.dat", dat]]
        if lod_line and more:
            rows.append([rel, lod_line])
        if kind == "weapon":
            r2, rows2 = _weapon(gd, donor, mid, nm, weapon_type, warn)
            readme += r2
            rows += rows2
        else:
            _object_dat(gd, donor.name, nm, extra, rows)
            if place is not None:
                irel, ibody, idat = _ipl_file(nm, (mid, nm), (lod_id, lod_nm) if lod_id is not None else None, place)
                extra[irel] = ibody
                readme.append(("gta.dat", idat))
                rows += [[irel, f"{mid} {nm}" + (f" -> lod {lod_id} {lod_nm}" if lod_id is not None else "")],
                         ["gta.dat", idat]]

    # write
    folder = out_dir(out or nm)
    payload: dict[str, bytes] = {f"{nm}.dff": dff_bytes}
    if txd_bytes is not None:
        payload[f"{nm}.txd"] = txd_bytes
    if col_out is not None:
        payload[f"{nm}.col"] = col_out
    if lod_bytes is not None:
        payload[f"{lod_nm}.dff"] = lod_bytes
        if lod_txd_bytes is not None:
            payload[f"{lod_nm}.txd"] = lod_txd_bytes
    payload.update(extra)
    if readme:
        title = f"satk add-on: {kind} {nm} (model {mid}), like model:{donor.id} {donor.name}"
        lines = readme_header(title)
        last = None
        for target, text in readme:
            if target != last:
                lines.append(f"# {target}")
                last = target
            lines.append(text.rstrip())
        payload[f"{nm}.txt"] = ("\r\n".join(lines) + "\r\n").encode("latin-1", errors="replace")
    if not dry_run:
        if folder.exists() and any(folder.iterdir()):
            if not force:
                raise SatkError("EXISTS", f"{jpath(folder)} exists",
                                hint="add --force to replace it, or --out another name")
            from ..docs.cleanup import remove_tree

            ensure_removable(folder)
            left = remove_tree(folder)
            if left:
                raise SatkError("BUSY", f"cannot clear {jpath(folder)}: {', '.join(left[:5])} still in use")
        for rel, data in sorted(payload.items()):
            atomic_write(folder.joinpath(*rel.split("/")), data)
    env = table(["file", "line"], [[f, str(line).rstrip()] for f, line in rows], warn=list(dict.fromkeys(warn)))
    if lod_id is not None:
        env["lod"] = {"id": f"model:{lod_id}", "name": lod_nm, "txd": lod_txd_name, "draw": LOD_DRAW}
    env.update({"id": f"model:{mid}", "kind": kind, "name": nm, "txd": txd_name,
                "donor": f"model:{donor.id} {donor.name}",
                "profile": profile, "out": jpath(folder), "files": sorted(payload)})
    if dry_run:
        env["dry_run"] = True
    else:
        env["hint"] = (f"check: satk mod inspect {jpath(folder)} --profile {profile}; satk asset lint {jpath(folder)}; "
                       f"install: copy the folder into <game>/modloader/{folder.name}/")
    return env


def _object_dat(gd: GameData, donor: str, name: str, extra: dict[str, bytes], rows: list[list]) -> None:
    from ..formats.objectdat import parse_object_dat

    got = gd.data("object.dat", required=False)
    if got is None:
        return
    _rel, _p, text = got
    hit = [o for o in parse_object_dat(text) if o.name.lower() == donor.lower() and o.loaded]
    if not hit:
        return
    o = hit[-1]
    lines = text.splitlines(keepends=True)
    otl = tok(lines[o.line - 1])
    otl.set(0, name)
    new = otl.text()
    if not new.endswith(("\n", "\r")):
        new += "\r\n"
    lines.insert(o.line, new)
    extra["data/object.dat"] = "".join(lines).encode("latin-1")
    rows.append(["data/object.dat", new.rstrip("\r\n")])
