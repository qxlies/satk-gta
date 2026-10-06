"""Operations of ``satk.fx2d``: ``satk fx2d dump|apply|copy|check|roundtrip``.

All CLI only in MCP terms (``mcp=False``; agents call them through ``satk_op``). Inputs: ``model:<id|name>`` or
``dff:<name>`` SIDs (through the index of ``--profile``), ``<archive>.img/<entry>``, or a path that is absolute,
relative to the profile's game root (any case), to the current folder, or to ``<work>/out/fx2d/`` (so the output
of one command feeds the next). Outputs are new files under ``<work>/out/fx2d/`` (or an explicit writable path);
game files are only read.

Module-level imports stay stdlib/satk only (SPEC §2.3).
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

from ..core.envelope import clamp_limit, obj, round_pos, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath, open_ro, profile_root, work
from ..core.registry import op, report_progress

Profile = Literal["vanilla", "installed", "samp", "game"]

_DOCS_HINT = "the keys of every type are listed in docs/en/fx2d.md (satk fx2d dump of a vanilla model shows them)"
_MARK = b"\xf8\xf2\x53\x02"          # 2dEffect chunk id 0x253F2F8, little endian: a fast pre-filter


# ============================================================================ errors, paths, inputs


@contextmanager
def _errors(code: str = "BAD_PARAMS", prefix: str = "", hint: str | None = _DOCS_HINT) -> Iterator[None]:
    from .schema import Fx2dError

    try:
        yield
    except Fx2dError as e:
        raise SatkError(code, f"{prefix}{e}", hint=hint, did_you_mean=e.did_you_mean[:8]) from None


def _out_root() -> Path:
    return Path(os.path.abspath(cfg().paths.work)) / "out" / "fx2d"


def _root(profile: str) -> Path | None:
    try:
        return profile_root(profile)
    except SatkError:
        return None


def _label(p: Path, profile: str) -> str:
    root = _root(profile)
    if root is not None:
        try:
            return Path(os.path.abspath(p)).relative_to(os.path.abspath(root)).as_posix()
        except ValueError:
            pass
    return jpath(p)


def _resolve(path: str, profile: str, what: str) -> Path:
    from ..formats.dat import resolve_ci

    p = Path(path)
    if p.is_absolute():
        if p.exists():
            return p
        raise SatkError("NOT_FOUND", f"no such {what}: {jpath(p)}")
    root = _root(profile)
    hit = None
    if root is not None:
        try:
            hit = resolve_ci(root, path)
        except (SatkError, OSError):
            hit = None
    if hit is not None:
        return hit
    for cand in (Path.cwd() / path, _out_root() / path):
        if cand.exists():
            return cand
    raise SatkError("NOT_FOUND", f"no {what} {path!r} under the {profile} game root, the current folder or "
                                 f"{jpath(_out_root())}",
                    hint="give model:<id|name>, dff:<name>, models/gta3.img/<name>.dff or an absolute path")


def _split_entry(path: str) -> tuple[str, str] | None:
    norm = path.replace("\\", "/")
    cut = norm.lower().find(".img/")
    return (norm[:cut + 4], norm[cut + 5:]) if cut > 0 else None


def _load_dff(src: str, profile: str) -> tuple[bytes, str, str]:
    """``(bytes, label, file name)`` of a DFF given as a SID, an IMG entry or a path."""
    s = src.strip()
    low = s.lower()
    if low.startswith(("model:", "dff:")):
        from ..index.api import open_index, read_blob_bytes

        db = open_index(profile)
        if low.startswith("model:"):
            mf = db.model_files(s)
            if mf.dff is None:
                raise SatkError("NOT_FOUND", f"{s} ({mf.name}) has no DFF in the {profile} profile")
            ref = mf.dff
        else:
            ref = db.blob_ref(s)
        where = _label(ref.path, profile)
        where = f"{where}/{ref.name}" if Path(ref.path).suffix.lower() == ".img" else where
        return read_blob_bytes(ref), f"{s} ({where})", ref.name
    if low.startswith("file:"):
        s = s[5:]
    sp = _split_entry(s)
    if sp is not None:
        from ..formats.img import ImgArchive
        from ..formats.rw import FormatError

        arc = _resolve(sp[0], profile, "archive")
        try:
            with ImgArchive.open(arc) as a:
                e = a.find(sp[1])
                if e is None:
                    raise SatkError("NOT_FOUND", f"no entry {sp[1]!r} in {jpath(arc)}",
                                    hint=f"satk formats ls {sp[0]} --name {sp[1].rsplit('.', 1)[0]}")
                return a.read(e), f"{_label(arc, profile)}/{e.name}", e.name
        except FormatError as ex:
            raise SatkError("UNSUPPORTED", f"cannot read {jpath(arc)}: {ex}") from None
    p = _resolve(s, profile, "DFF")
    if p.is_dir():
        raise SatkError("BAD_PARAMS", f"{jpath(p)} is a folder: give one DFF",
                        hint="satk fx2d roundtrip <folder> checks many files")
    with open_ro(p) as f:
        return f.read(), _label(p, profile), p.name


def _is_json_src(src: str) -> bool:
    s = src.strip()
    return s[:1] in ("{", "[") or s.lower().endswith(".json")


def _load_json(src: str, profile: str) -> tuple[Any, str]:
    s = src.strip()
    if s[:1] in ("{", "["):
        text, label = s, "inline JSON"
    else:
        p = _resolve(s, profile, "JSON file")
        with open_ro(p) as f:
            text, label = f.read().decode("utf-8-sig"), jpath(p)
    try:
        return json.loads(text), label
    except json.JSONDecodeError as e:
        raise SatkError("BAD_PARAMS", f"{label}: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}",
                        hint="satk fx2d dump writes a valid document to start from") from None


def _out_path(out: str | None, default_name: str, suffix: str) -> Path:
    """``out``: absolute path, or a name/relative path under ``<work>/out/fx2d/``; ``suffix`` added if missing."""
    name = out or default_name
    if Path(name).suffix.lower() != suffix:
        name += suffix
    p = Path(name)
    if p.is_absolute():
        d = ensure_writable(p)
        d.parent.mkdir(parents=True, exist_ok=True)
        return d
    if ".." in p.parts or not p.parts:
        raise SatkError("BAD_PARAMS", f"--out must be a plain name or a relative path: {out}")
    return work("out", "fx2d", *p.parts)


def _filter(names: list[str] | None) -> set[int] | None:
    from .schema import Fx2dError, type_code

    if not names:
        return None
    out = set()
    for n in names:
        try:
            out.add(type_code(n.strip(), "--filter"))
        except Fx2dError as e:
            raise SatkError("BAD_PARAMS", str(e), did_you_mean=e.did_you_mean[:8]) from None
    return out


def _resources(profile: str, fxp: str | None, txd: str | None) -> tuple[Any, list[str], dict]:
    """Resources for the checks + warnings + ``{"effects.fxp": path, "particle.txd": path}``."""
    from ..formats.dat import resolve_ci
    from .check import load_resources

    def find(explicit: str | None, rel: str) -> Path | None:
        if explicit:
            return _resolve(explicit, profile, rel.rsplit("/", 1)[-1])
        root = _root(profile)
        if root is None:
            return None
        try:
            return resolve_ci(root, rel)
        except (SatkError, OSError):
            return None

    pf, pt = find(fxp, "models/effects.fxp"), find(txd, "models/particle.txd")

    def read(p: Path) -> bytes:
        with open_ro(p) as f:
            return f.read()

    try:
        res = load_resources(pf, pt, read)
    except Exception as e:  # noqa: BLE001 - a broken resource file must not hide the other checks
        raise SatkError("UNSUPPORTED", f"cannot read the check resources: {e}",
                        hint="give working files with --fxp / --txd") from None
    warn, info = [], {}
    for key, p, flag in (("effects.fxp", pf, "--fxp"), ("particle.txd", pt, "--txd")):
        if p is None:
            warn.append(f"RESOURCE_MISSING: no models/{key} under the {profile} game root: names not checked "
                        f"(give {flag})")
        else:
            info[key] = jpath(p)
    return res, warn, info


def _doc_text(doc: dict) -> str:
    """The document as JSON: header keys one per line, one effect per line (stable, diff-friendly)."""
    def d(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False)

    lines = ["{"]
    head = [(k, v) for k, v in doc.items() if k != "geometries"]
    for k, v in head:
        lines.append(f" {d(k)}: {d(v)},")
    lines.append(' "geometries": [')
    gs = doc.get("geometries", [])
    for i, g in enumerate(gs):
        inner = [f"{d(k)}: {d(v)}" for k, v in g.items() if k not in ("effects", "keep")]
        effs = g.get("effects", [])
        lines.append("  {" + ", ".join(inner) + ', "effects": [' + ("" if effs else "]"
                     + (f', "keep": {d(g["keep"])}' if "keep" in g else "") + "}" + ("," if i < len(gs) - 1 else "")))
        for j, e in enumerate(effs):
            lines.append("   " + d(e) + ("," if j < len(effs) - 1 else ""))
        if effs:
            lines.append("  ]" + (f', "keep": {d(g["keep"])}' if "keep" in g else "") + "}"
                         + ("," if i < len(gs) - 1 else ""))
    lines.append(" ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _counts(entries: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in entries:
        k = str(e.get("type"))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def _flat(doc: dict) -> list[tuple[int, int, dict]]:
    return [(g["geometry"], j, e) for g in doc.get("geometries", []) for j, e in enumerate(g.get("effects", []))]


def _row(gi: int, j: int, e: dict) -> list:
    from .schema import summary

    pos = e.get("pos", [])
    return [f"g{gi}#{j}", e.get("type"), round_pos(pos) if all(isinstance(x, (int, float)) for x in pos) else pos,
            summary(e)]


def _check_after(data: bytes, geoms: list[int], profile: str) -> tuple[dict, list[str]]:
    """Check the written geometries; ``({"errors": n, "warnings": n}, warn lines)``."""
    from .check import check_items
    from .doc import read_doc, spheres

    try:
        res, _w, _i = _resources(profile, None, None)
    except SatkError:
        return {}, []
    doc = read_doc(data, keep=False)
    items = [(f"g{gi}#{j}", gi, e) for gi, j, e in _flat(doc) if gi in geoms]
    issues = check_items(items, res, spheres(data))
    errs = sum(1 for i in issues if i.sev == "error")
    lines = [f"{i.code}: {i.where} {i.msg}" for i in sorted(issues, key=lambda i: i.sev != "error")[:10]]
    return {"errors": errs, "warnings": len(issues) - errs}, lines


# ============================================================================ dump


@op("fx2d.dump", summary="Dump the 2dEffect (2DFX) entries (lights/coronas, particles, ped attractors, entry-exits, "
                         "road signs, cover points, escalators, ...) of a DFF or model:<id> to editable JSON under "
                         "<work>/out/fx2d/; table of entries with type, position and a summary.",
    summary_ru="2dEffect модели (свет/короны, частицы, аттракторы, входы-выходы, знаки, укрытия, эскалаторы) "
               "в редактируемый JSON в <work>/out/fx2d/; таблица записей.",
    mcp=False, group="formats",
    examples=("satk fx2d dump model:lamppost1", "satk fx2d dump models/gta3.img/vgseesc01.dff",
              "satk fx2d dump model:1297 --out mylamp --no-keep"))
def fx2d_dump(src: str, out: str | None = None, filter: list[str] | None = None,  # noqa: A002
              keep: bool = True, full: bool = False, limit: int = 20, profile: Profile = "vanilla") -> dict:
    """Write the fx2d document of one DFF.

    Args:
        src: model:<id|name>, dff:<name>, <img>/<entry>.dff or a DFF path.
        out: JSON name under <work>/out/fx2d/ (or an absolute path); default <dff name>.json.
        filter: only show these types in the table (the file always has all entries).
        keep: keep the bytes the game ignores (string garbage, padding) so that apply gives the identical file.
        full: also return the shown entries as JSON objects ("effects", without "keep"), saving a file read.
        limit: table rows (max 500).
        profile: profile for SIDs and relative paths.
    """
    from .doc import read_doc

    data, label, name = _load_dff(src, profile)
    with _errors("UNSUPPORTED", f"{label}: ", None):
        doc = read_doc(data, source=label, keep=keep)
    path = _out_path(out, Path(name).stem, ".json")
    atomic_write(path, _doc_text(doc))
    flat = _flat(doc)
    want = _filter(filter)
    names = None if want is None else {_name(c) for c in want}
    shown = [(gi, j, e) for gi, j, e in flat if names is None or e.get("type") in names]
    rows = [_row(gi, j, e) for gi, j, e in shown]
    lim = clamp_limit(limit)
    warn = [] if flat else [f"NO_2DFX: {label} has no 2dEffect entries (apply can add some)"]
    env = table(["fx", "type", "pos", "info"], rows[:lim], total=len(rows), warn=warn)
    env.update(obj(file=jpath(path), source=label, geometries=len(doc["geometries"]),
                   geometry_count=doc["geometry_count"], entries=len(flat),
                   counts=_counts([e for _g, _j, e in flat])))
    if full:
        env["effects"] = [{"fx": f"g{gi}#{j}", **{k: v for k, v in e.items() if k != "keep"}}
                          for gi, j, e in shown[:lim]]
    return env


def _name(code: int) -> str | int:
    from .schema import type_name

    return type_name(code)


# ============================================================================ apply / copy


def _write_dff(data: bytes, new: bytes, rep, out: str | None, name: str, label: str, profile: str,
               check: bool, extra: dict) -> dict:
    path = _out_path(out, name, ".dff")
    atomic_write(path, new)
    from ..formats.rw import rw_payload_size

    n = rw_payload_size(data) or len(data)
    fields: dict[str, Any] = {"file": jpath(path), "source": label, **extra, "geometries": rep.geometries,
                              "created": rep.created, "removed": rep.removed, "entries": rep.entries,
                              "identical": new == bytes(data[:n]), "bytes": len(new)}
    warn = list(rep.warn)
    if check and (rep.geometries or rep.removed):
        summ, lines = _check_after(new, rep.geometries, profile)
        if summ:
            fields["check"] = summ
        warn += lines
    env = obj(**fields)
    if warn:
        env["warn"] = warn[:20]
    return env


@op("fx2d.apply", summary="Write 2dEffect (2DFX) entries from fx2d JSON (file or inline) into a copy of a DFF: "
                          "replace the listed geometries' entries or add to them. Unchanged JSON from fx2d dump gives the "
                          "identical file. Output under <work>/out/fx2d/; the result is checked.",
    summary_ru="Записать 2dEffect из JSON (файл или строка) в копию DFF: заменить или добавить. Неизменённый "
               "JSON из dump даёт тот же файл. Вывод — <work>/out/fx2d/, с проверкой.",
    mcp=False, group="formats",
    examples=("satk fx2d apply model:lamppost1 lamppost1.json",
              "satk fx2d apply mymodel.dff lights.json --mode add --out mymodel_lit",
              "satk fx2d apply mymodel.dff \"{\\\"effects\\\":[{\\\"type\\\":\\\"light\\\",\\\"pos\\\":[0,0,3]}]}\""))
def fx2d_apply(dff: str, src: str, out: str | None = None, mode: Literal["replace", "add"] = "replace",
               geometry: int | None = None, check: bool = True, profile: Profile = "vanilla") -> dict:
    """Apply an fx2d document to a DFF.

    Args:
        dff: the DFF to change (model:<id|name>, dff:<name>, <img>/<entry>.dff or a path); it is only read.
        src: fx2d JSON: a file (from fx2d dump or hand-written) or the JSON text; a document, {"effects": [...]}
            or a bare list of effects.
        out: output DFF name under <work>/out/fx2d/ (or an absolute path); default the DFF's file name.
        mode: replace (the listed geometries get exactly these entries) or add (append to the existing ones).
        geometry: target geometry for JSON without geometry indexes (default 0).
        check: run fx2d check on the written geometries and report problems as warnings.
        profile: profile for SIDs and relative paths.
    """
    from .doc import apply_doc, normalize

    data, label, name = _load_dff(src=dff, profile=profile)
    spec, jlabel = _load_json(src, profile)
    with _errors(prefix=f"{jlabel}: "):
        items = normalize(spec)
        new, rep = apply_doc(data, items, mode=mode, geometry=geometry)
    return _write_dff(data, new, rep, out, name, label, profile, check, {"json": jlabel, "mode": mode})


@op("fx2d.copy", summary="Copy 2dEffect (2DFX) entries (all, or only some types with --filter light) from one DFF or "
                         "model:<id> into a copy of another DFF, optionally shifted by --offset; adds to (or "
                         "replaces) the target geometry's entries. Output under <work>/out/fx2d/.",
    summary_ru="Скопировать 2dEffect (например, --filter light) из одной модели в копию другой, со сдвигом "
               "--offset; добавить или заменить. Вывод — <work>/out/fx2d/.",
    mcp=False, group="formats",
    examples=("satk fx2d copy model:lamppost1 mylamp.dff --filter light",
              "satk fx2d copy model:lamppost2 mylamp.dff --filter light --offset 0,0,1.5 --mode replace"))
def fx2d_copy(src: str, dst: str, filter: list[str] | None = None, from_geometry: int | None = None,  # noqa: A002
              to_geometry: int = 0, mode: Literal["add", "replace"] = "add", offset: list[float] | None = None,
              out: str | None = None, check: bool = True, profile: Profile = "vanilla") -> dict:
    """Copy entries between models.

    Args:
        src: model with the entries (model:<id|name>, dff:<name>, <img>/<entry>.dff or a DFF path).
        dst: DFF to write a changed copy of (it is only read).
        filter: entry types to copy (light, particle, attractor, enex, roadsign, trigger_point, cover_point,
            escalator, ...); default all.
        from_geometry: copy only from this geometry of src (default all geometries).
        to_geometry: geometry of dst that receives the entries.
        mode: add (append) or replace (dst geometry keeps only the copied entries).
        offset: x,y,z added to every copied position.
        out: output DFF name under <work>/out/fx2d/ (or an absolute path); default the dst file name.
        check: run fx2d check on the written geometry.
        profile: profile for SIDs and relative paths.
    """
    from .doc import Item, apply_doc, read_doc
    from .schema import f32_json

    if offset is not None and len(offset) != 3:
        raise SatkError("BAD_PARAMS", f"--offset wants x,y,z, got {offset}")
    want = _filter(filter)
    sdata, slabel, _sname = _load_dff(src, profile)
    with _errors("UNSUPPORTED", f"{slabel}: ", None):
        sdoc = read_doc(sdata, source=slabel)
    flat = _flat(sdoc)
    if from_geometry is not None:
        flat = [x for x in flat if x[0] == from_geometry]
    picked = [e for _g, _j, e in flat if want is None or e.get("type") in {_name(c) for c in want}]
    if not picked:
        have = _counts([e for _g, _j, e in _flat(sdoc)])
        raise SatkError("NOT_FOUND", f"{slabel} has no matching 2dEffect entries (it has {have or 'none'})",
                        hint=f"satk fx2d dump {src}")
    if offset:
        import struct

        for e in picked:
            if not all(isinstance(p, (int, float)) for p in e["pos"]):
                raise SatkError("BAD_PARAMS", f"cannot shift a NaN/infinite position {e['pos']} with --offset")
            try:
                e["pos"] = [f32_json(struct.pack("<f", float(p) + float(d))) for p, d in zip(e["pos"], offset)]
            except OverflowError:
                raise SatkError("BAD_PARAMS", f"--offset {offset} moves {e['pos']} out of the float32 range") from None
    data, label, name = _load_dff(dst, profile)
    with _errors(prefix=f"{slabel} -> {label}: "):
        new, rep = apply_doc(data, [Item(to_geometry, picked, where="copied")], mode=mode)
    return _write_dff(data, new, rep, out, name, label, profile, check,
                      {"from": slabel, "mode": mode, "copied": len(picked), "types": _counts(picked)})


# ============================================================================ check


@op("fx2d.check", summary="Check 2dEffect (2DFX) entries of a DFF, model:<id> or fx2d JSON: types the game reads, "
                          "sizes, particle names in effects.fxp, corona/shadow textures in particle.txd, name lengths, "
                          "ranges, unit directions. Table of issues (where, severity, code, message).",
    summary_ru="Проверить 2dEffect модели или JSON: типы, размеры, частицы в effects.fxp, текстуры корон в "
               "particle.txd, длины имён, диапазоны. Таблица проблем.",
    mcp=False, group="formats",
    examples=("satk fx2d check model:lamppost1", "satk fx2d check lamppost1.json",
              "satk fx2d check mymod.dff --fxp mymod/effects.fxp --strict"))
def fx2d_check(src: str, fxp: str | None = None, txd: str | None = None, strict: bool = False, limit: int = 20,
               profile: Profile = "vanilla") -> dict:
    """Check entries.

    Args:
        src: fx2d JSON (file ending in .json or JSON text) or a DFF (model:<id|name>, dff:<name>, <img>/<entry>,
            path).
        fxp: effects.fxp to check particle names against (default models/effects.fxp of the profile).
        txd: particle.txd to check corona/shadow textures against (default models/particle.txd of the profile).
        strict: fail with CHECK_FAILED when there is an error.
        limit: table rows (errors first; max 500).
        profile: profile for SIDs, relative paths and the default resources.
    """
    from .check import Issue, check_items
    from .doc import normalize, read_doc, spheres
    from .schema import Fx2dError, decode_entry, encode_entry

    res, warn, info = _resources(profile, fxp, txd)
    items: list[tuple[str, int | None, dict]] = []
    issues: list[Issue] = []
    sph = None
    if _is_json_src(src):
        spec, label = _load_json(src, profile)
        with _errors(prefix=f"{label}: "):
            norm = normalize(spec)
        for it in norm:
            for j, e in enumerate(it.effects):
                w = f"{it.where}[{j}]"
                try:
                    pos, code, data = encode_entry(e, w)
                except Fx2dError as ex:
                    issues.append(Issue(w, "error", "INVALID", str(ex)))
                    continue
                items.append((w, it.geometry, decode_entry(pos, code, data, keep=False)))
    else:
        data, label, _name_ = _load_dff(src, profile)
        with _errors("UNSUPPORTED", f"{label}: ", None):
            doc = read_doc(data, keep=False)
            sph = spheres(data)
        items = [(f"g{gi}#{j}", gi, e) for gi, j, e in _flat(doc)]
    issues += check_items(items, res, sph)
    issues.sort(key=lambda i: i.sev != "error")
    errs = sum(1 for i in issues if i.sev == "error")
    lim = clamp_limit(limit)
    env = table(["where", "sev", "code", "msg"], [i.row() for i in issues[:lim]], total=len(issues), warn=warn)
    env.update(obj(source=label, entries=len(items) + sum(1 for i in issues if i.code == "INVALID"),
                   counts=_counts([e for _w, _g, e in items]), errors=errs, warnings=len(issues) - errs,
                   resources=info))
    if strict and errs:
        raise SatkError("CHECK_FAILED", f"{label}: {errs} error(s) in 2dEffect entries",
                        hint="the rows say what to fix", data={k: v for k, v in env.items() if k != "ok"})
    return env


# ============================================================================ roundtrip


def _iter_target(target: str | None, profile: str) -> Iterator[tuple[str, bytes]]:
    from ..formats.img import ImgArchive
    from ..rw.roundtrip import iter_game

    root = _root(profile)
    if not target:
        if root is None or not (root / "models").is_dir():
            raise SatkError("NOT_FOUND", f"no game root with models/ for profile {profile}",
                            hint="satk status; or give an IMG, a folder or a DFF")
        yield from iter_game(root, "dff")
        return
    p = _resolve(target, profile, "IMG, folder or DFF")
    label = _label(p, profile)
    if p.is_dir() and (p / "models").is_dir():
        yield from iter_game(p, "dff")
    elif p.is_dir():
        for f in sorted((f for f in p.iterdir() if f.is_file() and f.suffix.lower() == ".dff"),
                        key=lambda f: f.name.lower()):
            with open_ro(f) as fh:
                yield _label(f, profile), fh.read()
    elif p.suffix.lower() == ".img":
        with ImgArchive.open(p) as a:
            for e in a.entries:
                if e.ext == "dff":
                    yield f"{label}/{e.name}", a.read(e)
    else:
        with open_ro(p) as fh:
            yield label, fh.read()


@op("fx2d.roundtrip", summary="Measure the exact round trip DFF -> fx2d JSON -> DFF on every DFF with 2dEffect "
                              "entries (whole game, an IMG, a folder or a file): files, bit-identical results, "
                              "entries per type, entries that needed 'keep'.",
    summary_ru="Замер круговой записи DFF -> JSON -> DFF для всех моделей с 2dEffect (игра, IMG, папка, файл): "
               "совпадения бит в бит, записи по типам.",
    mcp=False, group="formats", long_running=True,
    examples=("satk fx2d roundtrip", "satk fx2d roundtrip models/gta_int.img", "satk fx2d roundtrip --no-keep"))
def fx2d_roundtrip(target: str | None, keep: bool = True, examples: int = 3,
                   profile: Profile = "vanilla") -> dict:
    """Round-trip measurement (reads only).

    Args:
        target: IMG archive, folder or DFF (default: every DFF of the profile's game: models/*.img + loose files).
        keep: keep ignored bytes in the JSON (without it, files with string garbage cannot be identical).
        examples: labels of differing or unreadable files to show.
        profile: profile whose game root is used.
    """
    from ..formats.rw import rw_payload_size
    from .doc import apply_doc, normalize, read_doc
    from .schema import Fx2dError

    files = with_fx = exact = 0
    entries: dict[str, int] = {}
    kept = raw = 0
    differ: list[str] = []
    failed: list[str] = []
    for label, data in _iter_target(target, profile):
        files += 1
        if files % 500 == 0:
            report_progress(files, 0, label)
        if _MARK not in data:
            continue
        try:
            doc = read_doc(data, keep=keep)
            if not doc["geometries"]:
                continue
            with_fx += 1
            for _g, _j, e in _flat(doc):
                k = str(e["type"])
                entries[k] = entries.get(k, 0) + 1
                kept += "keep" in e
                raw += "data" in e
            new, _rep = apply_doc(data, normalize(json.loads(_doc_text(doc))))
        except Fx2dError as e:
            failed.append(f"{label}: {str(e)[:80]}")
            continue
        n = rw_payload_size(data) or len(data)
        if new == bytes(data[:n]):
            exact += 1
        else:
            differ.append(label)
    k = max(0, examples)
    return obj(target=target or jpath(_root(profile) or "."), files=files, with_2dfx=with_fx, exact=exact,
               differ=len(differ), failed=len(failed), entries=dict(sorted(entries.items())),
               total_entries=sum(entries.values()), with_keep=kept, raw=raw,
               examples={"differ": differ[:k], "failed": failed[:k]})
