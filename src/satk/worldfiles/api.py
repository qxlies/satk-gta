"""Implementation of the satk.worldfiles operations (the ``@op`` wrappers are in :mod:`.ops`). Stdlib only at
import time; numpy/Pillow are imported by :mod:`.radar` inside its functions."""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

from ..core.envelope import clamp_limit, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, jpath
from . import common as C

# --------------------------------------------------------------------------- shared


def _install_hint(folder: Path) -> str:
    return (f"install: copy the folder into <game>/modloader/; check first: satk mod inspect {jpath(folder)}")


def _lookup_error(e: LookupError, example: str) -> SatkError:
    msg, cands = e.args[0], (e.args[1] if len(e.args) > 1 else [])
    return SatkError("NOT_FOUND", msg, hint=example, did_you_mean=[str(c) for c in cands][:12])


def _pairs(items: list[str] | None, example: str) -> list[tuple[str, str]]:
    out = []
    for it in items or []:
        k, eq, v = it.partition("=")
        if not eq or not k.strip():
            raise SatkError("BAD_PARAMS", f"expected KEY=text, got {it!r}", hint=example)
        out.append((k.strip(), v))
    return out


# --------------------------------------------------------------------------- GXT

_LANGS = ("american", "french", "german", "italian", "spanish")


def gxt_path(gxt: str | None, profile: str) -> Path:
    g = (gxt or "american").strip()
    if g.lower() in _LANGS:
        return C.game_file(None, profile, f"text/{g.lower()}.gxt", "GXT")
    return C.game_file(g, profile, "text/american.gxt", "GXT")


_CHARSET_HINT = ("the stock fonts have ASCII and the accented letters of the European versions; for a font mod "
                 "drawn in another layout pass --charset cp1251 (Cyrillic) or latin1 (raw bytes)")


def _gxt_error(e: Exception, where: str) -> SatkError:
    return SatkError("BAD_PARAMS", f"{where}: {e}", hint="satk gxt export american --out mytext.json shows the "
                                                          "document format")


def load_gxt_doc(path: Path, charset: str, profile: str | None):
    from . import gxtnames
    from .gxt import GxtError, read_gxt

    try:
        return read_gxt(C.read_bytes(path), gxtnames.names_for(profile), charset)
    except GxtError as e:
        raise SatkError("BAD_PARAMS", f"{jpath(path)}: {e} (offset {e.offset})",
                        hint="only GTA SA GXT files (version 4) are supported") from None


def _text_short(t: str, n: int = 120) -> str:
    return t if len(t) <= n else t[:n - 3] + "..."


def gxt_get(keys: list[str] | None, search: str | None, table_: str | None, gxt: str | None, charset: str,
            profile: str, limit: int, full: bool) -> dict:
    path = gxt_path(gxt, profile)
    doc = load_gxt_doc(path, charset, profile)
    limit = clamp_limit(limit)
    n = None if full else 120
    rows: list[list] = []
    if keys:
        missing = []
        for k in keys:
            try:
                hits = doc.find(k)
            except ValueError as e:
                raise SatkError("BAD_PARAMS", str(e)) from None
            hits = [h for h in hits if not table_ or h[0].upper() == table_.upper()]
            if not hits:
                missing.append(k)
            for tname, i, txt in hits:
                key = doc.table(tname).entries[i][0]
                if key.startswith("0x") and not k.lower().startswith("0x"):
                    key = k.upper()
                rows.append([key, tname, txt if full else _text_short(txt, n)])
        env = table(["key", "table", "text"], rows[:limit], total=len(rows))
        if missing:
            env["missing"] = missing
            env["hint"] = "a missing key can be added: satk gxt patch american KEY=\"text\""
        return env
    if search:
        q = search.lower()
        for t in doc.tables:
            if table_ and t.name.upper() != table_.upper():
                continue
            for k, txt in t.entries:
                if q in txt.lower() or q == k.lower():
                    rows.append([k, t.name, txt if full else _text_short(txt, n)])
        return table(["key", "table", "text"], rows[:limit], total=len(rows))
    for t in doc.tables:
        named = sum(1 for k, _v in t.entries if not k.startswith("0x"))
        rows.append([t.name, len(t.entries), named])
    env = table(["table", "entries", "named"], rows[:limit], total=len(rows))
    env.update({"file": jpath(path), "bits": doc.bits, "entries": doc.count(),
                "named": sum(r[2] for r in rows)})
    return env


def gxt_export(gxt: str | None, out: str | None, fmt: str, charset: str, profile: str) -> dict:
    from .gxt import doc_to_json, format_text_table, write_gxt

    path = gxt_path(gxt, profile)
    raw = C.read_bytes(path)
    doc = load_gxt_doc(path, charset, profile)
    exact = write_gxt(doc) == raw
    stem = path.stem.lower()
    if fmt == "json":
        dst = C.out_file(out, f"{stem}.json")
        atomic_write(dst, json.dumps(doc_to_json(doc), ensure_ascii=False, indent=0).encode("utf-8"))
        files = 1
    else:
        dst = C.out_file(out, stem)
        head = doc_to_json(doc)
        head["tables"] = [t.name for t in doc.tables]
        atomic_write(dst / "gxt.json", json.dumps(head, indent=1).encode("utf-8"))
        for t in doc.tables:
            atomic_write(dst / f"{t.name}.txt", format_text_table(t.entries).encode("utf-8"))
        files = 1 + len(doc.tables)
    named = sum(1 for t in doc.tables for k, _v in t.entries if not k.startswith("0x"))
    env = {"ok": True, "path": jpath(dst), "format": fmt, "files": files, "tables": len(doc.tables),
           "entries": doc.count(), "named": named, "charset": doc.charset, "round_trip": exact}
    if named < doc.count():
        env["hint"] = ("keys without a known name are written as 0x<hash>; they write back unchanged. "
                       "satk gxt keys --scan finds names in a modded game's scripts")
    return env


def _read_doc_source(src: str):
    """A GXT document from a JSON file or a folder (``gxt.json`` + ``<TABLE>.txt``/``.json``)."""
    from .gxt import GxtDoc, GxtError, GxtTable, doc_from_json, parse_text_table

    p = Path(src)
    if not p.exists():
        raise SatkError("NOT_FOUND", f"no file or folder {src!r}",
                        hint="satk gxt export american --out mytext.json makes one to edit")
    try:
        if p.is_file():
            return doc_from_json(json.loads(C.read_bytes(p).decode("utf-8-sig")))
        head = {}
        hp = p / "gxt.json"
        if hp.is_file():
            head = json.loads(C.read_bytes(hp).decode("utf-8-sig"))
        order = [str(x) for x in head.get("tables", []) if isinstance(x, str)]
        found: dict[str, Path] = {}
        for f in sorted(p.iterdir()):
            if f.suffix.lower() in (".txt", ".fxt", ".json") and f.name.lower() != "gxt.json" and f.is_file():
                found.setdefault(f.stem.upper(), f)
        names = [n for n in order if n.upper() in found] + \
            sorted((n for n in found if n not in {o.upper() for o in order}), key=lambda n: (n != "MAIN", n))
        if not names:
            raise SatkError("NOT_FOUND", f"{jpath(p)} has no <TABLE>.txt or <TABLE>.json files",
                            hint="satk gxt export american --format dir --out mytext makes one")
        doc = GxtDoc(bits=int(head.get("bits", 8)), charset=str(head.get("charset", "gta")))
        for n in names:
            f = found[n.upper()]
            text = C.read_bytes(f).decode("utf-8-sig")
            if f.suffix.lower() == ".json":
                sub = doc_from_json(json.loads(text))
                entries = sub.tables[0].entries if sub.tables else []
            else:
                entries = parse_text_table(text, f.name)
            doc.tables.append(GxtTable(n if n in order else f.stem.upper(), entries))
        return doc
    except (ValueError, GxtError) as e:
        if isinstance(e, json.JSONDecodeError):
            raise SatkError("BAD_PARAMS", f"{src}: invalid JSON: {e}") from None
        raise _gxt_error(e, src) from None


def gxt_write(src: str, out: str | None, charset: str | None) -> dict:
    from ..formats.gxt import parse_gxt
    from ..formats.rw import FormatError
    from .gxt import GxtError, write_gxt

    doc = _read_doc_source(src)
    if charset:
        doc.charset = charset
    warn: list[str] = []
    try:
        data = write_gxt(doc, warn)
    except GxtError as e:
        raise _gxt_error(e, src) from None
    try:
        back = parse_gxt(data)
    except FormatError as e:  # pragma: no cover - the writer and the reader disagree
        raise SatkError("INTERNAL", f"written GXT does not parse: {e}") from None
    if sum(len(t) for t in back.tables.values()) != doc.count():
        raise SatkError("INTERNAL", "written GXT lost entries")
    dst = C.out_file(out, "american.gxt")
    atomic_write(dst, data)
    env = {"ok": True, "path": jpath(dst), "size": len(data), "tables": len(doc.tables), "entries": doc.count(),
           "verified": True, "hint": "Mod Loader: put it in a mod folder as text/<language>.gxt (it replaces the "
                                     "game's file); single player without Mod Loader: <game>/text/"}
    if warn:
        env["warn"] = warn
    return env


def gxt_patch(gxt: str, sets: list[str], target: str, table_: str | None, out: str | None, charset: str,
              profile: str, dry_run: bool) -> dict:
    from .fxt import write_fxt
    from .gxt import GxtError, GxtTable, parse_key, write_gxt

    path = gxt_path(gxt, profile)
    doc = load_gxt_doc(path, charset, profile)
    pairs = _pairs(sets, 'satk gxt patch american CRED001="New text" MYKEY="Hello"')
    rows: list[list] = []
    warn: list[str] = []
    entries_fxt: list[tuple[str, str]] = []
    for k, v in pairs:
        try:
            _h, name = parse_key(k)
        except ValueError as e:
            raise SatkError("BAD_PARAMS", str(e)) from None
        hits = doc.find(k)
        tname = table_ or (hits[0][0] if len(hits) == 1 else "MAIN")
        cur = [h for h in hits if h[0].upper() == tname.upper()]
        old = cur[0][2] if cur else None
        if target == "fxt" and name is None:
            raise SatkError("BAD_PARAMS", f"{k}: an FXT needs the key name, not its hash",
                            hint="use --target full to change a key known only by its hash")
        if target == "full":
            t = doc.table(tname)
            if t is None:
                if len(tname) > 7:
                    raise SatkError("BAD_PARAMS", f"table name {tname!r} longer than 7 characters")
                t = GxtTable(tname.upper())
                doc.tables.append(t)
                warn.append(f"NEW_TABLE: table {t.name} added (scripts load it with LOAD_MISSION_TEXT)")
            if cur:
                i = cur[0][1]
                t.entries[i] = (t.entries[i][0], v)
            else:
                t.entries.append((name or k, v))
        else:
            entries_fxt.append((name or k, v))
        rows.append([name or k, tname, "-" if old is None else _text_short(old, 80), _text_short(v, 80)])
    stem = path.stem.lower()
    folder = C.out_folder(out or f"gxt-{stem}-{target}")
    files: dict[str, bytes] = {}
    try:
        if target == "fxt":
            files[f"{folder.name}.fxt"] = write_fxt(entries_fxt, charset,
                                                    f"satk gxt patch: {len(entries_fxt)} keys over {path.name}",
                                                    warn)
        else:
            files[f"text/{path.name.lower()}"] = write_gxt(doc, warn)
    except (ValueError, GxtError) as e:
        raise SatkError("BAD_PARAMS", str(e), hint=_CHARSET_HINT) from None
    env = table(["key", "table", "old", "new"], rows, warn=list(dict.fromkeys(warn)))
    env.update({"target": target, "source": jpath(path), "out": jpath(folder)})
    if dry_run:
        env["dry_run"] = True
        return env
    env["files"] = C.write_files(folder, files)
    if target == "fxt":
        env["hint"] = ("Mod Loader: copy the folder into <game>/modloader/ (the FXT keys override the GXT); CLEO: copy "
                       f"{folder.name}.fxt into <game>/CLEO/CLEO_TEXT/")
    else:
        env["hint"] = _install_hint(folder)
    return env


def gxt_keys(gxt: str | None, scan: bool, profile: str) -> dict:
    from . import gxtnames
    from .gxt import read_gxt

    path = gxt_path(gxt, profile)
    doc = read_gxt(C.read_bytes(path), {}, "latin1")
    want = {int(k, 16) for t in doc.tables for k, _v in t.entries}
    ship = {h for h in gxtnames.shipped() if h in want}
    cache = {h for h in gxtnames.cached(profile) if h in want}
    env = {"ok": True, "file": jpath(path), "keys": len(want), "shipped": len(ship), "cached": len(cache),
           "named": len(ship | cache)}
    if scan:
        from ..core.paths import profile_root

        found = gxtnames.scan(profile_root(profile), want)
        p = gxtnames.save_cache(profile, found)
        env.update({"scanned": len(found), "named": len(set(found) | ship | cache), "cache": jpath(p)})
    elif len(ship | cache) < len(want):
        env["hint"] = f"satk gxt keys --scan --profile {profile} looks for more names in that game's files"
    return env


# --------------------------------------------------------------------------- FXT


def fxt_write(items: list[str], out: str | None, charset: str, table_: str | None) -> dict:
    from .fxt import parse_fxt, write_fxt
    from .gxt import doc_from_json

    entries: list[tuple[str, str]] = []
    sources = []
    for it in items:
        p = Path(it)
        k, eq, v = it.partition("=")
        if eq and re.fullmatch(r"[A-Za-z0-9_\-]+", k.strip()) and not p.is_file():
            entries.append((k.strip(), v))
            continue
        if not p.is_file():
            raise SatkError("NOT_FOUND", f"{it!r} is neither KEY=text nor a file",
                            hint='satk fxt write MYKEY="Hello" or satk fxt write texts.json')
        sources.append(p)
        text = C.read_bytes(p).decode("utf-8-sig")
        if p.suffix.lower() == ".json":
            try:
                doc = doc_from_json(json.loads(text))
            except ValueError as e:
                raise SatkError("BAD_PARAMS", f"{it}: {e}") from None
            t = doc.table(table_ or "MAIN") or (doc.tables[0] if doc.tables else None)
            entries += [(k2, v2) for k2, v2 in (t.entries if t else []) if not k2.startswith("0x")]
        else:
            entries += parse_fxt(text)
    if not entries:
        raise SatkError("BAD_PARAMS", "no entries", hint='satk fxt write MYKEY="Hello" OTHER="World" --out mytext')
    warn: list[str] = []
    # later entries win
    merged: dict[str, tuple[str, str]] = {}
    for k, v in entries:
        merged[k.upper()] = (k, v)
    entries = list(merged.values())
    try:
        data = write_fxt(entries, charset, "satk fxt write", warn)
    except ValueError as e:
        raise SatkError("BAD_PARAMS", str(e), hint=_CHARSET_HINT) from None
    name = out or (sources[0].stem if sources else "text")
    if not name.lower().endswith(".fxt"):
        name += ".fxt"
    dst = C.out_file(name, "text.fxt")
    atomic_write(dst, data)
    env = {"ok": True, "path": jpath(dst), "entries": len(entries),
           "hint": "Mod Loader: any folder of the mod; CLEO: <game>/CLEO/CLEO_TEXT/"}
    if warn:
        env["warn"] = list(dict.fromkeys(warn))
    return env


# --------------------------------------------------------------------------- zones


def zon_path(file: str | None, profile: str) -> Path:
    f = (file or "info").strip()
    if f.lower() in ("info", "map"):
        return C.game_file(None, profile, f"data/{f.lower()}.zon", "zone")
    return C.game_file(f, profile, "data/info.zon", "zone")


def _load_zon(path: Path):
    from .zon import parse_zon_doc

    errs: list = []
    doc = parse_zon_doc(C.read_text(path), errs)
    return doc, errs


def _gxt_main(profile: str) -> dict[int, str] | None:
    try:
        from .gxt import read_gxt

        doc = read_gxt(C.read_bytes(gxt_path(None, profile)), {}, "gta")
    except SatkError:
        return None
    t = doc.table("MAIN")
    return {int(k, 16): v for k, v in t.entries} if t else None


def _box(box: list[float]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    if len(box) == 4:
        box = [box[0], box[1], -100.0, box[2], box[3], 900.0]
    if len(box) != 6:
        raise SatkError("BAD_PARAMS", f"--box needs X0,Y0,Z0,X1,Y1,Z1 (or X0,Y0,X1,Y1), got {len(box)} numbers",
                        hint="--box 2400,-1720,0,2530,-1620,200")
    lo = tuple(min(box[i], box[i + 3]) for i in range(3))
    hi = tuple(max(box[i], box[i + 3]) for i in range(3))
    return lo, hi  # type: ignore[return-value]


def _zrow(i: int, z, texts: dict[int, str] | None) -> list:
    from .gxt import key_hash

    txt = texts.get(key_hash(z.label), "") if texts else ""
    return [i, z.name, z.label, txt, z.type, z.level, _r2(z.min), _r2(z.max)]


def _r2(v) -> list[float]:
    return [round(x, 2) + 0.0 for x in v]


def _mod_fxt_keys(zon: Path) -> set[int]:
    """Hashes of the keys of FXT files in the mod folder of ``<mod>/data/<file>.zon``."""
    from .fxt import parse_fxt
    from .gxt import key_hash

    root = zon.parent.parent if zon.parent.name.lower() == "data" else zon.parent
    out: set[int] = set()
    try:
        files = sorted(root.rglob("*.fxt"))[:50]
    except OSError:
        return out
    for f in files:
        try:
            out |= {key_hash(k) for k, _v in parse_fxt(C.read_bytes(f).decode("latin-1"))}
        except (SatkError, OSError, ValueError):
            continue
    return out


def zone_list(file: str | None, at: list[float] | None, name: str | None, profile: str, limit: int,
              cursor: str | None) -> dict:
    from .zon import zones_at

    path = zon_path(file, profile)
    doc, _errs = _load_zon(path)
    texts = _gxt_main(profile)
    cols = ["idx", "name", "label", "text", "type", "level", "min", "max"]
    idx = {id(z): i for i, z in enumerate(doc.zones)}
    if at:
        if len(at) not in (2, 3):
            raise SatkError("BAD_PARAMS", "--at needs X,Y or X,Y,Z", hint="--at 2495,-1687")
        hits, shown = zones_at(doc.zones, at[0], at[1], at[2] if len(at) == 3 else None)
        rows = [_zrow(idx[id(z)], z, texts) for z in hits]
        env = table(cols, rows)
        env["shown"] = (shown.name if shown else "SAN_AND")
        if shown:
            env["shown_text"] = _zrow(0, shown, texts)[3] or shown.label
        env["file"] = jpath(path)
        return env
    sel = [(i, z) for i, z in enumerate(doc.zones)
           if not name or fnmatch.fnmatchcase(z.name.upper(), name.upper())
           or fnmatch.fnmatchcase(z.label.upper(), name.upper())]
    limit = clamp_limit(limit)
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = sel[start:start + limit]
    nxt = str(start + limit) if start + limit < len(sel) else None
    env = table(cols, [_zrow(i, z, texts) for i, z in page], total=len(sel), next=nxt)
    env["file"] = jpath(path)
    return env


def _zone_counts(path: Path, doc, profile: str) -> tuple[int, int, int]:
    """Navigation zones, map zones, zone-info records of the game (both .zon files, this one replaced)."""
    from .zon import parse_zon_doc

    docs = [doc]
    other = "map" if path.name.lower() == "info.zon" else ("info" if path.name.lower() == "map.zon" else None)
    if other:
        try:
            docs.append(parse_zon_doc(C.read_text(zon_path(other, profile))))
        except SatkError:
            pass
    zs = [z for d in docs for z in d.zones]
    navi = 1 + sum(1 for z in zs if z.type in (0, 1))
    mapz = 1 + sum(1 for z in zs if z.type == 3)
    infos = len({"SAN_AND"} | {z.name.upper() for z in zs if z.type in (0, 1)})
    return navi, mapz, infos


def zone_check(file: str | None, profile: str, limit: int) -> dict:
    from .gxt import key_hash
    from .zon import relations, zone_errors

    path = zon_path(file, profile)
    doc, errs = _load_zon(path)
    texts = _gxt_main(profile)
    if texts is not None:
        texts = dict(texts)
        texts.update({h: "(fxt)" for h in _mod_fxt_keys(path)})
    rows: list[list] = []
    for n, msg in errs:
        rows.append(["error", f"line {n}", msg])
    for i, z in enumerate(doc.zones):
        for e in zone_errors(z):
            rows.append(["error", f"#{i} {z.name}", e])
        if texts is not None and z.type in (0, 1) and key_hash(z.label) not in texts:
            rows.append(["warn", f"#{i} {z.name}", f"label {z.label} is not a key of american.gxt MAIN: the game "
                                                   f"shows no name (add it with an FXT or satk gxt patch)"])
    seen: dict[tuple, int] = {}
    for i, z in enumerate(doc.zones):
        key = (z.name.upper(), z.ibox())
        if key in seen:
            rows.append(["warn", f"#{i} {z.name}", f"same name and box as #{seen[key]}"])
        seen.setdefault(key, i)
    for i, a in enumerate(doc.zones):
        for j in range(i + 1, len(doc.zones)):
            b = doc.zones[j]
            if (a.type in (0, 1)) != (b.type in (0, 1)):
                continue
            if relations(a, b) == "partial":
                rows.append(["info", f"#{i} {a.name} / #{j} {b.name}",
                             "boxes overlap partly: in the shared part the smaller zone (width + height) is shown"])
    navi, mapz, infos = _zone_counts(path, doc, profile)
    from .zon import limits

    warn = limits(navi, mapz, infos)
    order = {"error": 0, "warn": 1, "info": 2}
    rows.sort(key=lambda r: order[r[0]])
    limit = clamp_limit(limit)
    env = table(["sev", "where", "issue"], rows[:limit], total=len(rows), warn=warn)
    env.update({"file": jpath(path), "zones": len(doc.zones),
                "counts": {"navigation": navi, "map": mapz, "zone_info": infos},
                "free": {"navigation": max(0, 380 - navi), "map": max(0, 39 - mapz)}})
    sev = {s: sum(1 for r in rows if r[0] == s) for s in order}
    env["summary"] = {k: v for k, v in sev.items() if v}
    return env


def _zon_out(path: Path, doc, out_name: str, extra: dict[str, bytes], rows, warn, dry_run: bool) -> dict:
    from .zon import parse_zon_doc, write_zon

    data = write_zon(doc)
    back = parse_zon_doc(data.decode("latin-1"))
    if len(back.zones) != len(doc.zones):  # pragma: no cover - writer bug guard
        raise SatkError("INTERNAL", "written zone file does not parse back")
    env = table(["zone", "relation", "box"], rows, warn=list(dict.fromkeys(warn)))
    env["zones"] = len(doc.zones)
    if dry_run:
        env["dry_run"] = True
        return env
    folder = C.out_folder(out_name)
    files = {f"data/{path.name.lower()}": data}
    files.update(extra)
    env.update({"out": jpath(folder), "files": C.write_files(folder, files), "hint": _install_hint(folder)})
    return env


def zone_add(name: str, box: list[float], label: str | None, text: str | None, type_: int | None,
             level: int | None, file: str | None, profile: str, out: str | None, charset: str,
             dry_run: bool) -> dict:
    from .fxt import write_fxt
    from .gxt import key_hash
    from .zon import Zone, limits, relations, zone_errors

    path = zon_path(file, profile)
    doc, _errs = _load_zon(path)
    is_map = path.name.lower() == "map.zon"
    if not box:
        raise SatkError("BAD_PARAMS", "--box is required", hint="--box 2400,-1720,0,2530,-1620,200")
    lo, hi = _box(box)
    t = type_ if type_ is not None else (3 if is_map else 0)
    lv = level if level is not None else (1 if doc.zones and not is_map else 0)
    z = Zone(name.upper(), t, lo, hi, lv, (label or name).upper())
    errs = zone_errors(z)
    if errs:
        raise SatkError("BAD_PARAMS", f"zone {z.name}: " + "; ".join(errs),
                        hint="names and labels: 1..7 letters/digits/_; --type 0 (navigation) or 3 (map)")
    rows: list[list] = []
    warn: list[str] = []
    for i, o in enumerate(doc.zones):
        if (o.type in (0, 1)) != (z.type in (0, 1)):
            continue
        rel = relations(z, o)
        if rel:
            rows.append([f"#{i} {o.name}", rel, [_r2(o.min), _r2(o.max)]])
        if o.name.upper() == z.name.upper():
            warn.append(f"SHARED_INFO: zone name {z.name} exists (#{i}); both rectangles share one zone-info record "
                        f"(population, gangs)")
    rows.sort(key=lambda r: ("same", "partial", "contains", "inside").index(r[1]))
    if any(r[1] == "partial" for r in rows):
        warn.append("PARTIAL_OVERLAP: the new zone partly overlaps others; in shared parts the smaller one is shown")
    if any(abs(v - int(v)) > 1e-9 for v in (*lo, *hi)):
        warn.append("TRUNCATED: the engine truncates zone coordinates to whole numbers (int16)")
    doc.zones.append(z)
    if len(doc.zones) == 1:
        z.before = ["zone"]
    navi, mapz, infos = _zone_counts(path, doc, profile)
    warn += limits(navi, mapz, infos)
    extra: dict[str, bytes] = {}
    out_name = out or f"zone-{z.name.lower()}"
    if text is not None:
        try:
            extra[f"{out_name}.fxt"] = write_fxt([(z.label, text)], charset, f"zone {z.name}: shown name", warn)
        except ValueError as e:
            raise SatkError("BAD_PARAMS", str(e)) from None
    elif z.type in (0, 1):
        texts = _gxt_main(profile)
        if texts is not None and key_hash(z.label) not in texts:
            warn.append(f"NO_TEXT: label {z.label} is not in american.gxt; pass --text \"Shown name\" to add an FXT "
                        f"entry, or use an existing label (satk gxt get --search ...)")
    env = _zon_out(path, doc, out_name, extra, rows, warn, dry_run)
    env["added"] = {"name": z.name, "label": z.label, "type": z.type, "level": z.level, "min": list(lo),
                    "max": list(hi), "line": z.canonical()}
    return env


def zone_remove(names: list[str], file: str | None, profile: str, out: str | None, dry_run: bool) -> dict:
    path = zon_path(file, profile)
    doc, _errs = _load_zon(path)
    kill: set[int] = set()
    for n in names:
        m = re.fullmatch(r"#?(\d+)", n.strip())
        if m and not any(z.name.upper() == n.strip().upper() for z in doc.zones):
            i = int(m.group(1))
            if not 0 <= i < len(doc.zones):
                raise SatkError("NOT_FOUND", f"no zone #{i} (the file has {len(doc.zones)})")
            kill.add(i)
            continue
        hit = [i for i, z in enumerate(doc.zones) if fnmatch.fnmatchcase(z.name.upper(), n.strip().upper())]
        if not hit:
            raise SatkError("NOT_FOUND", f"no zone named {n!r}", hint="satk zone list --name 'GAN*'",
                            did_you_mean=sorted({z.name for z in doc.zones
                                                 if n.strip().upper()[:3] in z.name.upper()})[:8])
        kill.update(hit)
    rows = [[f"#{i} {doc.zones[i].name}", "removed", [_r2(doc.zones[i].min), _r2(doc.zones[i].max)]]
            for i in sorted(kill)]
    keep = [z for i, z in enumerate(doc.zones) if i not in kill]
    if keep and doc.zones and doc.zones[0] not in keep:
        keep[0].before = doc.zones[0].before + keep[0].before
    elif not keep and doc.zones:
        doc.tail = doc.zones[0].before + doc.tail
    doc.zones = keep
    return _zon_out(path, doc, out or "zone-remove", {}, rows, [], dry_run)


def zone_export(file: str | None, out: str | None, profile: str) -> dict:
    from .zon import doc_from_json, doc_to_json, write_zon

    path = zon_path(file, profile)
    raw = C.read_bytes(path)
    doc, errs = _load_zon(path)
    obj = doc_to_json(doc, path.name.lower())
    exact = write_zon(doc_from_json(json.loads(json.dumps(obj)))) == raw
    dst = C.out_file(out, f"{path.stem.lower()}.zon.json")
    atomic_write(dst, json.dumps(obj, indent=1).encode("utf-8"))
    env = {"ok": True, "path": jpath(dst), "zones": len(doc.zones), "round_trip": exact}
    if errs:
        env["warn"] = [f"LINE {n}: {m}" for n, m in errs[:10]]
    return env


def zone_write(src: str, out: str | None) -> dict:
    from .zon import doc_from_json, parse_zon_doc, write_zon, zone_errors

    p = Path(src)
    try:
        doc = doc_from_json(json.loads(C.read_bytes(p).decode("utf-8-sig")))
    except ValueError as e:
        raise SatkError("BAD_PARAMS", f"{src}: {e}", hint="satk zone export info --out info.zon.json shows the format") \
            from None
    errs = [f"#{i} {z.name}: {e}" for i, z in enumerate(doc.zones) for e in zone_errors(z)]
    if errs:
        raise SatkError("BAD_PARAMS", f"{len(errs)} zones the engine cannot take: " + "; ".join(errs[:5]))
    data = write_zon(doc)
    back = parse_zon_doc(data.decode("latin-1"))
    name = out or (p.name[:-len(".json")] if p.name.lower().endswith(".zon.json") else p.stem + ".zon")
    dst = C.out_file(name, "info.zon")
    atomic_write(dst, data)
    return {"ok": True, "path": jpath(dst), "zones": len(back.zones), "verified": len(back.zones) == len(doc.zones)}


# --------------------------------------------------------------------------- water


def water_path(file: str | None, profile: str) -> Path:
    f = (file or "water").strip()
    if f.lower() in ("water", "water1"):
        return C.game_file(None, profile, f"data/{f.lower()}.dat", "water")
    return C.game_file(f, profile, "data/water.dat", "water")


def _load_water(path: Path):
    from .water import parse_water_doc

    errs: list = []
    return parse_water_doc(C.read_text(path), errs), errs


def _wrow(i: int, p) -> list:
    x0, y0, x1, y1 = p.bbox()
    zs = sorted({round(v[2], 2) for v in p.verts})
    fl = p.flags
    tag = "-" if fl is None else ("visible" if fl & 1 else "hidden") + (",shallow" if fl & 2 else "")
    return [i, p.kind, round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2), zs[0] if len(zs) == 1 else zs, tag]


def water_list(file: str | None, at: list[float] | None, profile: str, limit: int, cursor: str | None) -> dict:
    from .water import counts, limit_warnings, poly_issues

    path = water_path(file, profile)
    doc, errs = _load_water(path)
    sel = list(enumerate(doc.polys))
    if at:
        if len(at) == 2:
            at = [at[0], at[1], 0.0]
        if len(at) != 3:
            raise SatkError("BAD_PARAMS", "--at needs X,Y or X,Y,R (R = search radius)", hint="--at 1000,-2000,200")
        r = at[2]
        sel = [(i, p) for i, p in sel if p.bbox()[0] - r <= at[0] <= p.bbox()[2] + r
               and p.bbox()[1] - r <= at[1] <= p.bbox()[3] + r]
    limit = clamp_limit(limit)
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = sel[start:start + limit]
    c = counts(doc.polys)
    warn = limit_warnings(c) + [f"LINE {n}: {m}" for n, m in errs[:5]]
    issues = [(i, x) for i, p in enumerate(doc.polys) for x in poly_issues(p)]
    warn += [f"POLY #{i}: {x}" for i, x in issues[:5]]
    env = table(["idx", "kind", "x0", "y0", "x1", "y1", "z", "flags"], [_wrow(i, p) for i, p in page],
                total=len(sel), next=str(start + limit) if start + limit < len(sel) else None, warn=warn)
    env.update({"file": jpath(path), "counts": c,
                "free": {"quads": max(0, 301 - c["quads"]), "tris": max(0, 6 - c["tris"]),
                         "verts": max(0, 1021 - c["verts"])}})
    return env


def _water_lua(polys) -> str:
    lines = ["-- satk water add: createWater calls (shared function; run it in a server or client script)",
             "local water = {}"]
    for p in polys:
        coords = ", ".join(C.fmt_num(float(v[k]), 2) for v in p.verts for k in range(3))
        shallow = "true" if (p.flags or 0) & 2 else "false"
        lines.append(f"water[#water + 1] = createWater({coords}, {shallow})")
    return "\n".join(lines) + "\n"


def water_add(rects: list[list[float]], z: float, shallow: bool, waves: list[float] | None,
              flow: list[float] | None, invisible: bool, target: str, file: str | None, profile: str,
              out: str | None, dry_run: bool) -> dict:
    from .water import (MTA_QUAD_MAX, MTA_VERT_MAX, counts, limit_warnings, poly_issues, rect_poly,
                        write_water)

    if not rects:
        raise SatkError("BAD_PARAMS", "give at least one --rect X0,Y0,X1,Y1", hint="--rect 100,-200,300,0 --z 5")
    wv = tuple(waves or (0.0, 0.0))
    fl = tuple(flow or (0.0, 0.0))
    if len(wv) != 2 or len(fl) != 2:
        raise SatkError("BAD_PARAMS", "--waves and --flow take two numbers each (BIG,SMALL / X,Y)")
    flags = (0 if invisible else 1) | (2 if shallow else 0)
    path = water_path(file, profile)
    doc, _errs = _load_water(path)
    new = []
    rows: list[list] = []
    warn: list[str] = []
    for r in rects:
        if len(r) != 4:
            raise SatkError("BAD_PARAMS", f"--rect needs X0,Y0,X1,Y1, got {r}", hint="--rect 100,-200,300,0")
        p = rect_poly(r[0], r[1], r[2], r[3], z, fl, wv, flags)
        iss = poly_issues(p)
        if any("rectangle" in x for x in iss) or int(r[0]) == int(r[2]) or int(r[1]) == int(r[3]):
            raise SatkError("BAD_PARAMS", f"--rect {r}: needs a width and a height of at least 1 m")
        if iss:
            warn += [f"RECT {r}: {x}" for x in iss]
        bx = p.bbox()
        over = [i for i, o in enumerate(doc.polys)
                if min(bx[2], o.bbox()[2]) > max(bx[0], o.bbox()[0])
                and min(bx[3], o.bbox()[3]) > max(bx[1], o.bbox()[1])]
        if over:
            warn.append(f"OVERLAP: rect {r} overlaps existing water {', '.join('#' + str(i) for i in over[:8])}"
                        f"{' ...' if len(over) > 8 else ''} (two surfaces at one spot flicker)")
        new.append(p)
        rows.append([len(doc.polys) + len(new) - 1 if target != "mta" else f"mta {len(new)}", "quad",
                     *[round(v, 2) for v in bx], z, "visible" if flags & 1 else "hidden"])
    env_rows = rows
    if target == "mta":
        c = counts(doc.polys + new)
        if c["quads"] > MTA_QUAD_MAX or c["verts"] > MTA_VERT_MAX:
            warn.append(f"LIMIT: {c['quads']} quads / {c['verts']} vertices with the game's water exceed MTA's pools "
                        f"({MTA_QUAD_MAX}/{MTA_VERT_MAX}); createWater returns false for the rest")
        files = {"water.lua": _water_lua(new).encode("utf-8")}
        hint = "MTA: add water.lua to a resource (meta.xml <script src=\"water.lua\" type=\"server\"/>)"
    else:
        doc.polys += new
        c = counts(doc.polys)
        warn += limit_warnings(c)
        files = {f"data/{path.name.lower()}": write_water(doc)}
        hint = None
    env = table(["idx", "kind", "x0", "y0", "x1", "y1", "z", "flags"], env_rows, warn=list(dict.fromkeys(warn)))
    env.update({"target": target, "counts": c})
    if dry_run:
        env["dry_run"] = True
        return env
    folder = C.out_folder(out or f"water-{target}")
    env.update({"out": jpath(folder), "files": C.write_files(folder, files), "hint": hint or _install_hint(folder)})
    return env


def water_remove(idx: list[int], file: str | None, profile: str, out: str | None, dry_run: bool) -> dict:
    from .water import counts, write_water

    path = water_path(file, profile)
    doc, _errs = _load_water(path)
    bad = [i for i in idx if not 0 <= i < len(doc.polys)]
    if bad:
        raise SatkError("NOT_FOUND", f"no water polygon #{bad[0]} (the file has {len(doc.polys)})",
                        hint="satk water list --at X,Y,R shows the numbers")
    kill = set(idx)
    rows = [_wrow(i, doc.polys[i]) for i in sorted(kill)]
    keep = [p for i, p in enumerate(doc.polys) if i not in kill]
    for i in sorted(kill):
        if doc.polys[i].before:
            nxt = next((p for j, p in enumerate(doc.polys) if j > i and j not in kill), None)
            if nxt is not None:
                nxt.before = doc.polys[i].before + nxt.before
            else:
                doc.tail = doc.polys[i].before + doc.tail
    doc.polys = keep
    env = table(["idx", "kind", "x0", "y0", "x1", "y1", "z", "flags"], rows)
    env["counts"] = counts(doc.polys)
    if dry_run:
        env["dry_run"] = True
        return env
    folder = C.out_folder(out or "water-remove")
    env.update({"out": jpath(folder), "files": C.write_files(folder, {f"data/{path.name.lower()}": write_water(doc)}),
                "hint": _install_hint(folder)})
    return env


def water_export(file: str | None, out: str | None, profile: str) -> dict:
    from .water import doc_from_json, doc_to_json, write_water

    path = water_path(file, profile)
    raw = C.read_bytes(path)
    doc, errs = _load_water(path)
    obj = doc_to_json(doc, path.name.lower())
    exact = write_water(doc_from_json(json.loads(json.dumps(obj)))) == raw
    dst = C.out_file(out, f"{path.stem.lower()}.json")
    atomic_write(dst, json.dumps(obj, indent=0).encode("utf-8"))
    env = {"ok": True, "path": jpath(dst), "polys": len(doc.polys), "round_trip": exact}
    if errs:
        env["warn"] = [f"LINE {n}: {m}" for n, m in errs[:10]]
    return env


def water_write(src: str, out: str | None) -> dict:
    from .water import counts, doc_from_json, limit_warnings, parse_water_doc, poly_issues, write_water

    p = Path(src)
    try:
        doc = doc_from_json(json.loads(C.read_bytes(p).decode("utf-8-sig")))
    except ValueError as e:
        raise SatkError("BAD_PARAMS", f"{src}: {e}", hint="satk water export --out water.json shows the format") \
            from None
    data = write_water(doc)
    back = parse_water_doc(data.decode("latin-1"))
    dst = C.out_file(out or (p.stem if p.stem.lower().startswith("water") else "water") + ".dat", "water.dat")
    atomic_write(dst, data)
    c = counts(doc.polys)
    warn = limit_warnings(c) + [f"POLY #{i}: {x}" for i, q in enumerate(doc.polys) for x in poly_issues(q)][:10]
    env = {"ok": True, "path": jpath(dst), "polys": len(back.polys), "counts": c,
           "verified": len(back.polys) == len(doc.polys)}
    if warn:
        env["warn"] = warn
    return env


# --------------------------------------------------------------------------- timecyc


def timecyc_path(file: str | None, profile: str) -> Path:
    return C.game_file(file, profile, "data/timecyc.dat", "timecyc")


_DEFAULT_TC_FIELDS = ("sky_top", "sky_bot", "amb", "dir", "far_clip", "fog_start", "water")


def _tc_fields(field: list[str] | None) -> list[tuple[str, list[tuple[int, str]]]]:
    from .timecyc import FIELDS, slot_index

    out = []
    for f in field or _DEFAULT_TC_FIELDS:
        try:
            out.append((f, slot_index(f)))
        except KeyError:
            import difflib

            raise SatkError("NOT_FOUND", f"no timecyc field {f!r}", hint="fields: " + " ".join(FIELDS),
                            did_you_mean=difflib.get_close_matches(f, list(FIELDS), n=3, cutoff=0.5)) from None
    return out


def _tc_val(flat: list, slots: list[tuple[int, str]]):
    vals = [flat[s] for s, _n in slots]
    vals = [None if v is None else (round(v, 2) if isinstance(v, float) else v) for v in vals]
    return vals[0] if len(vals) == 1 else vals


def timecyc_get(weather: str | None, hour: str | None, field: list[str] | None, file: str | None, profile: str,
                limit: int, cursor: str | None) -> dict:
    from .timecyc import TimecycFile, select_rows

    path = timecyc_path(file, profile)
    tf = TimecycFile(C.read_text(path))
    try:
        rows = select_rows(tf.rows, weather, hour)
    except LookupError as e:
        raise _lookup_error(e, "satk timecyc get SUNNY_LA 12 --field sky_top far_clip") from None
    flds = _tc_fields(field)
    if weather is None and hour is None and field is None:
        names: dict[int, str] = {}
        for r in tf.rows:
            names.setdefault(r.weather, r.weather_name)
        env = table(["weather", "name"], [[w, n] for w, n in sorted(names.items())])
        env.update({"file": jpath(path), "hours": sorted({r.hour for r in tf.rows}),
                    "fields": [f for f in _all_fields()],
                    "hint": "satk timecyc get SUNNY_LA 12 (all fields of one point) or --field sky_top far_clip"})
        return env
    if len(rows) == 1 and field is None:
        r = rows[0]
        flat = tf.flat(r)
        vals = {f: _tc_val(flat, _tc_fields([f])[0][1]) for f in _all_fields()}
        env = {"ok": True, "weather": r.weather_name, "hour": r.hour, "line": r.line,
               "values": {k: v for k, v in vals.items() if v is not None}}
        if r.nread < 51:
            env["warn"] = [f"SHORT_LINE: line {r.line} has {r.nread} readable values; the engine keeps the previous "
                           f"line's values for the rest (shown)"]
        return env
    limit = clamp_limit(limit)
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = rows[start:start + limit]
    out = []
    for r in page:
        flat = tf.flat(r)
        out.append([r.weather_name, r.hour] + [_tc_val(flat, s) for _f, s in flds])
    env = table(["weather", "hour"] + [f for f, _s in flds], out, total=len(rows),
                next=str(start + limit) if start + limit < len(rows) else None)
    env["file"] = jpath(path)
    return env


def _all_fields() -> list[str]:
    from .timecyc import FIELDS

    return list(FIELDS)


def _apply_op(old: float, op: str, val: float) -> float:
    if op == "=":
        return val
    if op == "*=":
        return old * val
    if op == "+=":
        return old + val
    return old - val


def timecyc_patch(weather: str, hour: str, sets: list[str], file: str | None, profile: str, out: str | None,
                  dry_run: bool, limit: int) -> dict:
    from ..formats.timecyc import SLOTS
    from .timecyc import RANGES, TimecycFile, parse_value, select_rows, slot_index

    path = timecyc_path(file, profile)
    tf = TimecycFile(C.read_text(path))
    try:
        sel = select_rows(tf.rows, weather, hour)
    except LookupError as e:
        raise _lookup_error(e, "satk timecyc patch SUNNY_LA 12 sky_top=30,117,210 far_clip=1200") from None
    ops = C.parse_sets(sets, "satk timecyc patch SUNNY_LA 12 sky_top=30,117,210 far_clip*=1.5")
    parsed = []
    for f, op, v in ops:
        try:
            slots = slot_index(f)
        except KeyError:
            _tc_fields([f])
            raise  # pragma: no cover
        try:
            vals = parse_value(v, len(slots))
        except ValueError as e:
            raise SatkError("BAD_PARAMS", f"{f}: {e}") from None
        parsed.append((f, op, slots, vals))
    rows: list[list] = []
    warn: list[str] = []
    for r in sel:
        flat = tf.flat(r)
        changes: dict[int, float] = {}
        for f, op, slots, vals in parsed:
            for (s, name), val in zip(slots, vals):
                old = changes.get(s, flat[s])
                if old is None:
                    old = 0.0
                new = _apply_op(float(old), op, val)
                g, t = SLOTS[s]
                if t == "d":
                    if abs(new - round(new)) > 1e-9:
                        warn.append(f"ROUNDED: {name} is an integer field; {new:g} -> {round(new)}")
                    new = round(new)
                lo, hi = RANGES[g]
                if not lo <= new <= hi:
                    warn.append(f"RANGE: {name}={new:g} outside {lo:g}..{hi:g} (what the engine stores)")
                if flat[s] is None or float(new) != float(flat[s]):
                    changes[s] = new
        if changes:
            tf.set(r, changes)
            for s, v in sorted(changes.items()):
                rows.append([r.weather_name, r.hour, slot_name(s), flat[s], v])
    for ln in tf.rewritten:
        warn.append(f"REWRITTEN: line {ln} was short (the engine read it shifted); written whole with the values "
                    f"the engine used")
    data = tf.render()
    check = TimecycFile(data.decode("latin-1"))
    by_key = {(r.weather_name, r.hour): check.flat(r) for r in check.rows}
    verified = all(by_key[(w, h)][_slot_of(f)] == v for w, h, f, _o, v in rows)
    limit = clamp_limit(limit)
    env = table(["weather", "hour", "field", "old", "new"], rows[:limit], total=len(rows),
                warn=list(dict.fromkeys(warn)))
    env.update({"lines": len(tf.changed_lines()), "verified": verified})
    if not rows:
        env["hint"] = "nothing changed (the values are already set)"
    if dry_run or not rows:
        if dry_run:
            env["dry_run"] = True
        return env
    folder = C.out_folder(out or "timecyc-patch")
    env.update({"out": jpath(folder), "files": C.write_files(folder, {"data/timecyc.dat": data}),
                "hint": _install_hint(folder) + " (Mod Loader uses the whole timecyc.dat of the mod)"})
    return env


def _slot_of(name: str) -> int:
    from .timecyc import slot_index

    return slot_index(name)[0][0]


def slot_name(s: int) -> str:
    from .timecyc import FIELDS, comps

    for g, (first, k, _t) in FIELDS.items():
        if first <= s < first + k:
            return g if k == 1 else f"{g}.{comps(g)[s - first]}"
    return str(s)  # pragma: no cover


def timecyc_diff(a: str, b: str | None, field: list[str] | None, profile: str, limit: int,
                 cursor: str | None) -> dict:
    from .timecyc import TimecycFile, slot_index

    def load(spec: str | None, default_profile: str) -> tuple[Path, TimecycFile]:
        if spec and spec.lower() in ("vanilla", "installed", "samp", "game"):
            p = timecyc_path(None, spec.lower())
        else:
            p = timecyc_path(spec, default_profile) if spec else timecyc_path(None, default_profile)
        return p, TimecycFile(C.read_text(p))

    pa, ta = load(a, profile)
    pb, tb = load(b or "vanilla", profile)
    want: set[int] | None = None
    if field:
        want = set()
        for f in field:
            try:
                want |= {s for s, _n in slot_index(f)}
            except KeyError:
                _tc_fields([f])
    rows: list[list] = []
    per_field: dict[str, int] = {}
    if len(ta.rows) != len(tb.rows):
        raise SatkError("BAD_PARAMS", f"the files have {len(ta.rows)} and {len(tb.rows)} data lines; only files with "
                                      f"the same layout can be compared")
    for ra, rb in zip(ta.rows, tb.rows):
        fa, fb = ta.flat(ra), tb.flat(rb)
        for s, (x, y) in enumerate(zip(fa, fb)):
            if want is not None and s not in want:
                continue
            if x != y:
                n = slot_name(s)
                rows.append([ra.weather_name, ra.hour, n, x, y])
                g = n.split(".")[0]
                per_field[g] = per_field.get(g, 0) + 1
    limit = clamp_limit(limit)
    start = int(cursor) if cursor and cursor.isdigit() else 0
    env = table(["weather", "hour", "field", "a", "b"], rows[start:start + limit], total=len(rows),
                next=str(start + limit) if start + limit < len(rows) else None)
    env.update({"a": jpath(pa), "b": jpath(pb),
                "by_field": dict(sorted(per_field.items(), key=lambda kv: -kv[1]))})
    if not rows:
        env["same"] = True
    return env


# --------------------------------------------------------------------------- popcycle


def popcycle_path(file: str | None, profile: str) -> Path:
    return C.game_file(file, profile, "data/popcycle.dat", "popcycle")


def popcycle_get(zone: str | None, day: str | None, hour: str | None, field: list[str] | None, file: str | None,
                 profile: str, limit: int, cursor: str | None) -> dict:
    from .popcycle import FIELDS, ZONE_TYPES, PopcycleFile

    path = popcycle_path(file, profile)
    pf = PopcycleFile(C.read_text(path))
    if zone is None and day is None and hour is None and field is None:
        env = table(["idx", "zone_type"], [[i, z] for i, z in enumerate(ZONE_TYPES)])
        env.update({"file": jpath(path), "days": ["weekday", "weekend"], "fields": list(FIELDS),
                    "hint": "satk popcycle get GANGLAND weekday 20 (the 2-hour slot holding 20:00)"})
        return env
    try:
        sel = pf.select(zone, day, hour)
    except LookupError as e:
        raise _lookup_error(e, "satk popcycle get GANGLAND weekday 20 --field max_peds max_cars") from None
    flds = list(field) if field else ["max_peds", "max_cars", "dealers", "gang", "cops", "other"]
    bad = [f for f in flds if f.lower() not in FIELDS]
    if bad:
        import difflib

        raise SatkError("NOT_FOUND", f"no popcycle field {bad[0]!r}", hint="fields: " + " ".join(FIELDS),
                        did_you_mean=difflib.get_close_matches(bad[0].lower(), list(FIELDS), n=3, cutoff=0.5))
    if len(sel) == 1 and field is None:
        r = sel[0]
        return {"ok": True, "zone_type": ZONE_TYPES[r.zone], "day": ("weekday", "weekend")[r.day],
                "hours": r.hours, "line": r.line, "values": r.as_dict()}
    limit = clamp_limit(limit)
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = sel[start:start + limit]
    rows = [[ZONE_TYPES[r.zone], ("weekday", "weekend")[r.day], r.hours] +
            [r.values[FIELDS.index(f.lower())] for f in flds] for r in page]
    env = table(["zone_type", "day", "hours"] + [f.lower() for f in flds], rows, total=len(sel),
                next=str(start + limit) if start + limit < len(sel) else None)
    env["file"] = jpath(path)
    return env


def popcycle_patch(zone: str, day: str, hour: str, sets: list[str], file: str | None, profile: str,
                   out: str | None, dry_run: bool, limit: int) -> dict:
    from .popcycle import FIELDS, GROUPS, ZONE_TYPES, PopcycleFile

    path = popcycle_path(file, profile)
    pf = PopcycleFile(C.read_text(path))
    try:
        sel = pf.select(zone, day, hour)
    except LookupError as e:
        raise _lookup_error(e, "satk popcycle patch GANGLAND all all max_peds*=1.5") from None
    ops = C.parse_sets(sets, "satk popcycle patch GANGLAND weekday 20 max_peds=25 gang=80")
    for f, _op, _v in ops:
        if f.lower() not in FIELDS:
            import difflib

            raise SatkError("NOT_FOUND", f"no popcycle field {f!r}", hint="fields: " + " ".join(FIELDS),
                            did_you_mean=difflib.get_close_matches(f.lower(), list(FIELDS), n=3, cutoff=0.5))
    rows: list[list] = []
    warn: list[str] = []
    for r in sel:
        changes: dict[int, int] = {}
        for f, op, v in ops:
            s = FIELDS.index(f.lower())
            try:
                val = float(v)
            except ValueError:
                raise SatkError("BAD_PARAMS", f"{f}: {v!r} is not a number") from None
            new = _apply_op(float(changes.get(s, r.values[s])), op, val)
            newi = int(round(new))
            if not 0 <= newi <= 255:
                warn.append(f"CLAMPED: {f}={newi} -> {max(0, min(255, newi))} (the engine reads bytes)")
                newi = max(0, min(255, newi))
            if newi != r.values[s]:
                changes[s] = newi
        if changes:
            pf.set(r, changes)
            for s, v in sorted(changes.items()):
                rows.append([ZONE_TYPES[r.zone], ("weekday", "weekend")[r.day], r.hours, FIELDS[s], r.values[s], v])
            vals = list(r.values)
            for s, v in changes.items():
                vals[s] = v
            if sum(vals[6:6 + len(GROUPS)]) < 100:
                warn.append(f"GROUP_SUM: the ped groups of {ZONE_TYPES[r.zone]} {r.hours} add up to "
                            f"{sum(vals[6:6 + len(GROUPS)])} (< 100): the engine rescales them")
    data = pf.render()
    back = PopcycleFile(data.decode("latin-1"))
    verified = all(back.rows[i].values == pf_vals for i, pf_vals in _expected(pf, rows))
    limit = clamp_limit(limit)
    env = table(["zone_type", "day", "hours", "field", "old", "new"], rows[:limit], total=len(rows),
                warn=list(dict.fromkeys(warn))[:10])
    env.update({"lines": len(pf.changed_lines()), "verified": verified})
    if not rows:
        env["hint"] = "nothing changed (the values are already set)"
    if dry_run or not rows:
        if dry_run:
            env["dry_run"] = True
        return env
    folder = C.out_folder(out or "popcycle-patch")
    env.update({"out": jpath(folder), "files": C.write_files(folder, {"data/popcycle.dat": data}),
                "hint": _install_hint(folder) + " (Mod Loader uses the whole popcycle.dat of the mod)"})
    return env


def _expected(pf, rows: list[list]) -> list[tuple[int, list[int]]]:
    """(row index, expected values) of every row a popcycle patch changed."""
    from .popcycle import FIELDS, ZONE_TYPES

    idx = {(ZONE_TYPES[r.zone], ("weekday", "weekend")[r.day], r.hours): k for k, r in enumerate(pf.rows)}
    exp: dict[int, list[int]] = {}
    for zt, day, hours, field, _old, new in rows:
        k = idx[(zt, day, hours)]
        exp.setdefault(k, list(pf.rows[k].values))[FIELDS.index(field)] = new
    return sorted(exp.items())


# --------------------------------------------------------------------------- radar


def _gta3(profile: str) -> Path:
    return C.game_file(None, profile, "models/gta3.img", "IMG")


def radar_export(out: str | None, tile: int, profile: str) -> dict:
    from ..media import png as P
    from . import radar as R

    if tile not in (32, 64, 128, 256, 512):
        raise SatkError("BAD_PARAMS", f"--tile must be 32, 64, 128, 256 or 512, got {tile}")
    img = _gta3(profile)
    tiles, warn = R.read_tiles(img)
    arr = R.mosaic(tiles, tile)
    dst = C.out_file(out, f"radar-{profile}.png")
    side = arr.shape[0]
    atomic_write(dst, P.encode(side, side, arr.tobytes(), opaque=True))
    env = {"ok": True, "path": jpath(dst), "size": side, "tiles": sum(1 for t in tiles if t is not None),
           "source": jpath(img), "grid": "12x12, row 0 = north (y 3000), column 0 = west (x -3000), 500 m per tile",
           "hint": f"edit it, then: satk radar build {jpath(dst)} --out myradar"}
    if warn:
        env["warn"] = warn[:10]
    return env


def _schematic(profile: str, side: int, layers: tuple[str, ...], warn: list[str]):
    from . import radar as R

    water_polys: list = []
    if "water" in layers:
        try:
            doc, _e = _load_water(water_path(None, profile))
            water_polys = [[(v[0], v[1]) for v in p.verts] for p in doc.polys if (p.flags is None or p.flags & 1)]
        except SatkError as e:
            warn.append(f"NO_WATER: {e.msg}")
    footprints: list = []
    if "buildings" in layers:
        try:
            from ..index.api import open_index

            db = open_index(profile)
            for r in db.insts(box=(-3000.0, -3000.0, 3000.0, 3000.0), area=0, lod="hd", match="aabb"):
                a = r.aabb
                m2 = (a[3] - a[0]) * (a[4] - a[1])
                if 30.0 <= m2 <= 1500.0 and 5.0 < a[5] - a[2] < 120.0:
                    footprints.append((a[0], a[1], a[3], a[4]))
        except SatkError as e:
            warn.append(f"NO_BUILDINGS: {e.msg} ({e.hint or 'satk index build'})")
    roads: list = []
    if "roads" in layers:
        try:
            from ..paths.db import open_paths

            pdb, w = open_paths(profile)
            warn += [x for x in w if not x.startswith("IMPORTED")]
            try:
                cur = pdb.conn.execute(
                    "SELECT a.x, a.y, b.x, b.y FROM link l JOIN node a ON a.area = l.area AND a.idx = l.node "
                    "JOIN node b ON b.area = l.to_area AND b.idx = l.to_idx WHERE a.kind = 'car' AND b.kind = 'car' "
                    "AND (l.area < l.to_area OR (l.area = l.to_area AND l.node < l.to_idx))")
                roads = [tuple(r) for r in cur]
            finally:
                pdb.close()
        except SatkError as e:
            warn.append(f"NO_ROADS: {e.msg}")
    return R.draw_schematic(side, water_polys, footprints, roads, layers), \
        {"water": len(water_polys), "buildings": len(footprints), "roads": len(roads)}


def radar_build(image: str | None, from_map: bool, out: str | None, tile: int, quality: str,
                layers: list[str] | None, profile: str) -> dict:
    from ..texmod.encode import psnr
    from . import radar as R

    if tile not in (32, 64, 128, 256, 512):
        raise SatkError("BAD_PARAMS", f"--tile must be 32, 64, 128, 256 or 512, got {tile}")
    if bool(image) == bool(from_map):
        raise SatkError("BAD_PARAMS", "give an image file or --from-map (one of them)",
                        hint="satk radar build myradar.png --out myradar | satk radar build --from-map")
    side = R.GRID * tile
    warn: list[str] = []
    drawn = None
    if image:
        p = Path(image)
        if not p.is_file():
            raise SatkError("NOT_FOUND", f"no image {image!r}", hint="satk radar export makes the stock radar as PNG")
        arr = R.load_image(p, side, warn)
    else:
        lay = tuple(x.lower() for x in (layers or ["water", "buildings", "roads"]))
        bad = [x for x in lay if x not in ("water", "buildings", "roads")]
        if bad:
            raise SatkError("BAD_PARAMS", f"unknown layer {bad[0]!r}", hint="--layers water,buildings,roads")
        arr, drawn = _schematic(profile, side, lay, warn)
    txds = R.build_tiles(arr, tile, quality)
    back = [R.decode_tile(b) for b in txds]
    names_ok = all(n == R.tile_name(i) for i, (n, _a) in enumerate(back))
    mos = R.mosaic([a for _n, a in back], tile)
    score = psnr(arr, mos, alpha=False)
    worst = min(psnr(arr[r * tile:(r + 1) * tile, c * tile:(c + 1) * tile],
                     mos[r * tile:(r + 1) * tile, c * tile:(c + 1) * tile], alpha=False)
                for r in range(R.GRID) for c in range(R.GRID))
    folder = C.out_folder(out or "radar")
    files = {f"radar/{R.tile_name(i)}.txd": b for i, b in enumerate(txds)}
    from ..media import png as P

    prev = R.mosaic([a for _n, a in back], max(16, min(tile, 64)))
    files["preview.png"] = P.encode(prev.shape[0], prev.shape[0], prev.tobytes(), opaque=True)
    C.write_files(folder, files)
    tiles_rel = [f"radar/{R.tile_name(i)}.txd" for i in range(len(txds))]
    env = {"ok": True, "out": jpath(folder), "tiles": len(txds), "tile": tile, "format": "DXT1",
           "psnr": score, "psnr_min_tile": worst, "verified": names_ok and score >= 30.0,
           "files": [tiles_rel[0], "...", tiles_rel[-1], "preview.png"],
           "preview": jpath(folder / "preview.png"),
           "hint": "Mod Loader: copy the folder into <game>/modloader/ (the loose radarNN.txd replace the gta3.img "
                   "entries); without Mod Loader replace them inside models/gta3.img with an IMG tool"}
    if tile != R.TILE:
        warn.append(f"TILE_SIZE: stock tiles are {R.TILE} px; other sizes work but cost (or save) video memory")
    if drawn is not None:
        env["drawn"] = drawn
    if warn:
        env["warn"] = list(dict.fromkeys(warn))
    return env
