"""Operations of ``satk.formats`` (owner WP-02): ``formats selftest|ls|dump`` (CLI only, SPEC §4.6).

``dump`` understands IMG, TXD, DFF, COL, IFP, IDE, IPL (text and ``bnry``), ZON, DAT and falls back to the
raw RW chunk tree.

Paths: an absolute path is used as it is; a relative path is looked up in the current folder first, then
under the profile root (any case); a ``file:`` SID only under the profile root; ``model:``, ``dff:``,
``inst:`` and ``txd:`` SIDs through the profile index. Answers carry ``resolved_from`` (``absolute``, ``cwd``,
``profile`` or ``index``). A ``--level full`` answer pages every list
(rows, geometries, frames, effects) with ``--limit``/``--cursor``; cut lists are named in ``truncated``
(``"frames 20 of 51"``) and ``next`` holds the cursor of the following page.

Module-level imports stay stdlib/satk only (SPEC §2.3).
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, obj, table
from ..core.errors import SatkError
from ..core.ids import Sid
from ..core.paths import jpath, open_ro, profile_root
from ..core.registry import op
from . import selftest as _selftest
from .col import iter_col
from .dat import parse_dat, resolve_ci, split_dos_path
from .dff import FLAG_NAMES, FX_NAMES, find_embedded_col, scan_dff
from .ide import parse_ide
from .ifp import parse_ifp
from .img import ImgArchive
from .ipl import parse_ipl_binary, parse_ipl_text, rz_deg, world_quat
from .rw import FormatError, iter_children, rw_payload_size, rw_version
from .txd import parse_txd, texture_hash
from .zon import parse_zon

Profile = Literal["vanilla", "installed", "samp"]


def _fmt_error(e: FormatError, what: str) -> SatkError:
    return SatkError("UNSUPPORTED", f"cannot parse {what}: {e}", data={"kind": e.kind, "offset": e.offset})


def _resolve_from(path: str, profile: str, *, cwd: bool = True) -> tuple[Path, str, list[str]]:
    """``(path, resolved_from, warnings)``: absolute as given; relative from the current folder first
    (``cwd=True``), then under the profile root (case-insensitive)."""
    p = Path(path)
    if p.is_absolute():
        if p.exists():
            return p, "absolute", []
        raise SatkError("NOT_FOUND", f"no such file: {jpath(p)}")
    try:
        root: Path | None = profile_root(profile)
    except SatkError:
        root = None
    in_root = resolve_ci(root, path) if root is not None else None
    here = resolve_ci(Path.cwd(), path) if cwd else None
    if here is not None:
        warn = []
        if in_root is not None and not _same_file(here, in_root):
            warn.append(f"PATH_SHADOWS: {path} exists in the current folder and under the {profile} root; "
                        "using the current folder (give an absolute path or a file: SID for the game file)")
        return here, "cwd", warn
    if in_root is not None:
        return in_root, "profile", []
    where = f"the current folder or under the {profile} root" if cwd else f"the {profile} root"
    raise SatkError("NOT_FOUND", f"no such file in {where}: {path}",
                    hint=f"give an absolute path; game files: satk formats ls <img> --profile {profile}")


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _resolve(path: str, profile: str) -> Path:
    """Absolute path as given, else from the current folder, else relative to the profile root."""
    return _resolve_from(path, profile)[0]


def _identity(path: Path, entry: str, profile: str) -> dict[str, str]:
    """How a dump names its target (SPEC 3.2: SID keys never hold absolute paths).

    A file under the profile root -> ``{"id": "file:<relpath>[/<entry>]"}``, the canonical SID (lower case,
    forward slashes, relative to the root, real on-disk names), whatever spelling the target used.
    Anything else -> ``{"path": <absolute path>, "entry": <entry>}``.
    """
    try:
        rel = os.path.relpath(path, profile_root(profile))
    except (SatkError, ValueError, OSError):      # other drive, profile not configured
        rel = None
    parts = split_dos_path(rel) if rel else None  # None for "..\\..." (outside the root)
    if parts:
        try:
            return {"id": str(Sid("file", "/".join(parts + [entry] if entry else parts)))}
        except SatkError:                         # e.g. '@' in a file name: not representable as a SID
            pass
    out = {"path": jpath(path)}
    if entry:
        out["entry"] = entry
    return out


@op("formats.selftest", summary="Parse a game root with satk.formats and compare counters with the golden numbers.",
    summary_ru="Самопроверка парсеров форматов по эталонным числам (Приложение A).", mcp=False,
    examples=("satk formats selftest --profile vanilla", "satk formats selftest --root \"<game folder>\" --profile installed"))
def formats_selftest(root: str | None = None, profile: Profile = "vanilla", bench: bool = False,
                     quick: bool = False, geometry: bool = False, dxt: bool = False) -> dict:
    """Self-test of the format parsers.

    Args:
        root: game root (default: the profile root from satk.toml).
        profile: golden set and load order: vanilla|installed|samp.
        bench: add per-stage timings.
        quick: skip the TXD and DFF stages (and the texture-reference check).
        geometry: also decode every gta3/gta_int geometry (full vertex/index arrays).
        dxt: also run spike S2: DXT backend parity on the reference textures + Mpix/s.
    """
    r = Path(root) if root else profile_root(profile)
    if not (r / "data").is_dir():
        raise SatkError("NOT_FOUND", f"not a game root (no data/): {jpath(r)}")
    try:
        res = _selftest.run(r, profile, quick=quick, bench=bench, geometry=geometry, dxt=dxt)
    except FormatError as e:
        raise _fmt_error(e, jpath(r)) from None
    res["root"] = jpath(r)
    if not res["ok"]:
        res["error"] = {"code": "INTERNAL", "msg": f"{len(res['failed'])} counter(s) differ from the golden numbers"}
    return res


@op("formats.ls", summary="List the entries of an IMG archive.", summary_ru="Список записей IMG-архива.",
    mcp=False, examples=("satk formats ls models/gta3.img --name infernus", "satk formats ls models/player.img --ext txd"))
def formats_ls(img: str, name: str | None = None, ext: str | None = None, limit: int = 20, cursor: str | None = None,
               profile: Profile = "vanilla") -> dict:
    """List IMG entries.

    Args:
        img: archive path (absolute, or relative to the profile root, any case).
        name: case-insensitive substring of the entry name.
        ext: only this extension (dff, txd, col, ipl, ifp, ...).
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
        profile: profile whose root resolves relative paths.
    """
    p, where, pwarn = _resolve_from(img, profile)
    lim = clamp_limit(limit)
    try:
        start = int(cursor) if cursor else 0
    except ValueError:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}") from None
    try:
        with ImgArchive.open(p) as a:
            ents = a.entries
            ver = a.version
    except FormatError as e:
        raise _fmt_error(e, jpath(p)) from None
    if name:
        nl = name.lower()
        ents = [e for e in ents if nl in e.name.lower()]
    if ext:
        el = ext.lower().lstrip(".")
        ents = [e for e in ents if e.ext == el]
    page = ents[start:start + lim]
    nxt = str(start + lim) if start + lim < len(ents) else None
    env = table(["idx", "name", "offset", "size", "stream_sectors", "archive_sectors"],
                [[e.idx, e.name, e.abs_offset, e.size, e.stream_sectors, e.archive_sectors] for e in page],
                total=len(ents), next=nxt)
    env["path"] = jpath(p)
    env["version"] = ver
    env["resolved_from"] = where
    if pwarn:
        env["warn"] = pwarn
    return env


_INDEX_KINDS = ("model:", "dff:", "inst:", "txd:")


def _load_indexed(target: str, profile: str) -> tuple[str, bytes, Path, str, str, list[str]]:
    """``model:``/``dff:``/``inst:``/``txd:`` SIDs -> the DFF or TXD blob the profile index names."""
    from ..model3d import resolve as _mr

    low = target.lower()
    db = _mr.open_db(profile)
    if low.startswith("txd:"):
        ref = _mr.blob_ref(db, target)
    else:
        ref = _mr.resolve(target, profile, db=db).dff
        if ref is None:
            raise SatkError("NOT_FOUND", f"{target}: the {profile} profile has no DFF for it",
                            hint=f"satk asset get {target}")
    with open_ro(ref.path) as f:
        f.seek(ref.offset)
        data = f.read(ref.size)
    in_img = ref.path.suffix.lower() == ".img"
    label = f"{ref.path.name}/{ref.name}" if in_img else ref.path.name
    return label, data, ref.path, ref.name if in_img else "", "index", []


def _load_target(target: str, profile: str) -> tuple[str, bytes | None, Path, str, str, list[str]]:
    """-> (label, data or None for IMG itself, file path, entry name or '', resolved_from, warnings)."""
    if target.lower().startswith(_INDEX_KINDS):
        return _load_indexed(target, profile)
    sid = target.lower().startswith("file:")
    t = target[5:] if sid else target
    norm = t.replace("\\", "/")
    low = norm.lower()
    cut = low.find(".img/")
    if cut > 0:
        arc, entry = norm[:cut + 4], norm[cut + 5:]
        p, where, warn = _resolve_from(arc, profile, cwd=not sid)
        try:
            with ImgArchive.open(p) as a:
                e = a.find(entry)
                if e is None:
                    raise SatkError("NOT_FOUND", f"no entry {entry!r} in {jpath(p)}",
                                    hint=f"satk formats ls {arc} --name {entry.rsplit('.', 1)[0]}")
                return f"{arc}/{e.name}", a.read(e), p, e.name, where, warn
        except FormatError as ex:
            raise _fmt_error(ex, jpath(p)) from None
    p, where, warn = _resolve_from(t, profile, cwd=not sid)
    if p.suffix.lower() in (".img", ".dir"):
        return t, None, p, "", where, warn
    with open_ro(p) as f:
        return t, f.read(), p, "", where, warn


def _rw_tree(buf: bytes, limit: int = 1 << 30) -> list[list]:
    rows: list[list] = []
    end = rw_payload_size(buf) or 0

    def walk(start: int, stop: int, depth: int) -> None:
        for ch in iter_children(buf, start, stop, strict=False):
            if len(rows) >= limit:
                return
            rows.append([depth, ch.name, ch.size, f"0x{rw_version(ch.libid):X}", ch.data_off - 12])
            if ch.type in (0x10, 0x0E, 0x1A, 0x0F, 0x08, 0x07, 0x06, 0x14, 0x16, 0x15, 0x03, 0x2B):
                walk(ch.data_off, ch.end, depth + 1)

    walk(0, end, 0)
    return rows


_ALL = 1 << 30   # "every row" for the paging helpers


class _Pager:
    """Pages the lists of one ``--level full`` answer at the same offset and records what was cut."""

    def __init__(self, lim: int, off: int):
        self.lim, self.off = lim, off
        self.cut: list[str] = []
        self.more = False

    def page(self, name: str, seq) -> list:
        seq = list(seq)
        part = seq[self.off:self.off + self.lim]
        if seq and not part:
            self.cut.append(f"{name} none of {len(seq)} (cursor past the end)")
        elif self.off or len(seq) > self.off + self.lim:
            self.cut.append(f"{name} {self.off + 1}-{self.off + len(part)} of {len(seq)}" if self.off
                            else f"{name} {len(part)} of {len(seq)}")
        if len(seq) > self.off + self.lim:
            self.more = True
        return part

    def mark(self, env: dict) -> dict:
        if self.cut:
            env["truncated"] = self.cut
        if self.more:
            env["next"] = str(self.off + self.lim)
            env.setdefault("warn", []).append(
                f"TRUNCATED: {'; '.join(self.cut)}; next page: --cursor {self.off + self.lim} (or a larger --limit, "
                "max 500)")
        return env


@op("formats.dump", summary="Describe one game file or IMG entry (IMG, TXD, DFF, COL, IFP, IDE, IPL, ZON, DAT, RW tree).",
    summary_ru="Разбор одного файла или записи IMG: IMG, TXD, DFF, COL, IFP, IDE, IPL, ZON, DAT, дерево RW.", mcp=False,
    examples=("satk formats dump models/gta3.img/infernus.dff --level full", "satk formats dump models/gta3.img/infernus.txd --level full", "satk formats dump data/maps/la/lae2.ipl"))
def formats_dump(target: str, level: Literal["stats", "full", "tree"] = "stats", limit: int = 20,
                 cursor: str | None = None, profile: Profile = "vanilla") -> dict:
    """Dump a file.

    A relative path is looked up in the current folder first, then under the profile root
    (``resolved_from`` says which). Every list of a full or tree answer is paged by ``limit`` and
    ``cursor``; cut lists are named in ``truncated`` and ``next`` is the cursor of the next page.

    Args:
        target: path (absolute, relative to the current folder or to the profile root), `<img>/<entry>`,
            a `file:` SID (profile root only) or a `model:`/`dff:`/`inst:`/`txd:` SID (through the profile
            index; `resolved_from` is `index`).
        level: stats = counters only; full = also the rows (textures, materials, definitions, placements);
            tree = the raw RenderWare chunk tree (any RW file, also a broken one).
        limit: max rows per list for level=full/tree (max 500).
        cursor: value of `next` from the previous page (row offset).
        profile: profile whose root resolves relative paths.
    """
    lim = clamp_limit(limit)
    try:
        off = int(cursor) if cursor else 0
    except ValueError:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page") \
            from None
    if off < 0:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}")
    label, data, path, entry, where, pwarn = _load_target(target, profile)
    ident = {**_identity(path, entry, profile), "resolved_from": where}
    env = _dump(target, level, lim, off, profile, label, data, path, entry, ident)
    if pwarn:
        env.setdefault("warn", [])[:0] = pwarn
    return env


def _dump(target: str, level: str, lim: int, off: int, profile: str, label: str, data: bytes | None, path: Path,
          entry: str, ident: dict) -> dict:
    ext = (entry or path.name).rsplit(".", 1)[-1].lower()
    full = level == "full"
    pg = _Pager(lim, off)
    try:
        if level == "tree" and data is not None:
            return _dump_tree(data, label, pg, ident)
        if data is None:  # IMG archive itself
            with ImgArchive.open(path) as a:
                exts = Counter(e.ext for e in a.entries)
                return obj(**ident, kind="img", version=a.version, entries=len(a.entries),
                           size=a.file_size, by_ext=dict(exts.most_common()),
                           size_in_archive_nonzero=sum(1 for e in a.entries if e.archive_sectors))
        if data[:4] == b"bnry":
            insts, cars = parse_ipl_binary(data)
            env = obj(**ident, kind="ipl", format="binary", inst=len(insts), cars=len(cars))
            if full:
                env.update(_inst_rows(pg.page("inst", insts)))
            return pg.mark(env)
        if ext == "txd" or (len(data) >= 4 and data[:4] == b"\x16\x00\x00\x00"):
            t = parse_txd(data)
            env = obj(**ident, kind="txd", count=t.count, textures=len(t.textures), device_id=t.device_id,
                      rw_version=f"0x{t.rw_version:X}", rw_size=rw_payload_size(data),
                      by_fmt=dict(Counter(x.d3dfmt for x in t.textures).most_common()))
            if full:
                env["cols"] = ["idx", "name", "mask", "fmt", "w", "h", "levels", "alpha", "platform", "hash"]
                env["rows"] = [[x.idx, x.name, x.mask, x.d3dfmt, x.w, x.h, x.levels, x.alpha, x.platform,
                                texture_hash(data, x).hex()] for x in pg.page("textures", t.textures)]
            return pg.mark(env)
        head = data[:4]
        if ext == "dff" or (len(data) >= 12 and head in (b"\x10\x00\x00\x00", b"\x2b\x00\x00\x00")):
            return pg.mark(_dump_dff(data, ident, full, lim, pg))
        if ext == "col" or head in (b"COLL", b"COL2", b"COL3", b"COL4"):
            return pg.mark(_dump_col(data, ident, full, pg))
        if ext == "ifp" or head in (b"ANP3", b"ANP2", b"ANPK"):
            fmt, pack, anims = parse_ifp(data)
            env = obj(**ident, kind="ifp", format=fmt, pack=pack, anims=len(anims),
                      bones_max=max((a[1] for a in anims), default=0), frames_max=max((a[2] for a in anims), default=0))
            if full:
                env["cols"] = ["idx", "anim", "bones", "frames"]
                env["rows"] = [[pg.off + i, n, b, f] for i, (n, b, f) in enumerate(pg.page("anims", anims))]
            return pg.mark(env)
        if ext == "zon":
            errs: list = []
            zones = parse_zon(data.decode("latin-1"), errors=errs)
            env = obj(**ident, kind="zon", zones=len(zones), by_level=dict(Counter(z["level"] for z in zones)),
                      errors=[f"{n}: {m}" for n, m in errs[:10]])
            if full:
                env["cols"] = ["line", "name", "label", "type", "level", "min", "max"]
                env["rows"] = [[z["line"], z["name"], z["label"], z["type"], z["level"],
                                [round(v, 2) for v in z["min"]], [round(v, 2) for v in z["max"]]]
                               for z in pg.page("zones", zones)]
            return pg.mark(env)
        if ext == "ide":
            errs = []
            defs, txdp, fx = parse_ide(data.decode("latin-1"), errors=errs)
            env = obj(**ident, kind="ide", defs=len(defs), by_sec=dict(Counter(d.sec for d in defs)),
                      txdp=len(txdp), fx2d=len(fx), errors=[f"{n}: {m}" for n, m in errs[:10]])
            if full:
                env["cols"] = ["line", "sec", "id", "name", "txd", "draw", "flags"]
                env["rows"] = [[d.line, d.sec, d.id, d.name, d.txd, d.draw, d.flags] for d in pg.page("defs", defs)]
            return pg.mark(env)
        if ext == "ipl":
            errs = []
            insts, items = parse_ipl_text(data.decode("latin-1"), errors=errs)
            env = obj(**ident, kind="ipl", format="text", inst=len(insts),
                      items={k: len(v) for k, v in items.items()}, errors=[f"{n}: {m}" for n, m in errs[:10]])
            if full:
                env.update(_inst_rows(pg.page("inst", insts)))
            return pg.mark(env)
        if ext in ("dat", "two") and not entry:
            lines = parse_dat(data.decode("latin-1"))
            env = obj(**ident, kind="dat", lines=len(lines), by_key=dict(Counter(x.key for x in lines)))
            if full:
                env["cols"] = ["line", "key", "arg"]
                env["rows"] = [[x.line, x.key, x.arg] for x in pg.page("lines", lines)]
            return pg.mark(env)
        if rw_payload_size(data):
            return _dump_tree(data, label, pg, ident)
    except FormatError as e:
        err = _fmt_error(e, label)
        if rw_payload_size(data or b""):
            err.hint = f"satk formats dump {target} --level tree"
        raise err from None
    raise SatkError("UNSUPPORTED", f"no dumper for {label!r} (img, txd, dff, col, ifp, ide, ipl, zon, dat, RW tree)",
                    data={"magic": data[:4].hex()})


def _r2(v) -> list:
    return [round(x, 2) for x in v]


def _dump_dff(data: bytes, ident: dict, full: bool, lim: int, pg: _Pager) -> dict:
    info = scan_dff(data)
    col = find_embedded_col(data)
    col_name = None
    if col is not None:
        try:
            col_name = next(iter_col(data[col[0]:col[0] + col[1]])).name
        except (FormatError, StopIteration):
            col_name = "?"
    textures = sorted({m.texture for m in info.materials if m.texture}, key=str.lower)
    env = obj(**ident, kind="dff", rw_version=f"0x{info.rw_version:X}", clumps=info.clumps,
              atomics=info.atomics, frames=len(info.frames), geoms=len(info.geoms), materials=len(info.materials),
              verts=info.verts, tris=info.tris, flags=[n for b, n in FLAG_NAMES.items() if info.flags & b],
              effects=len(info.effects), textures=textures[:lim], textures_total=len(textures),
              bbox=_r2(info.bbox) if info.bbox else None, bsphere=_r2(info.bsphere) if info.bsphere else None,
              embedded_col=col_name, plugins=sorted(f"0x{p:x}" for p in info.plugins))
    if full:
        env["cols"] = ["geom", "idx", "texture", "mask", "rgba", "fx", "color_slot"]
        env["rows"] = [[m.geom, m.idx, m.texture, m.mask, f"{m.rgba:08x}",
                        "|".join(n for b, n in FX_NAMES.items() if m.fx & b) or None, m.color_slot]
                       for m in pg.page("materials", info.materials)]
        env["geom_rows"] = [[g.idx, g.verts, g.tris, g.uv_sets, g.strip, g.frame,
                             info.frames[g.frame].name if 0 <= g.frame < len(info.frames) else None]
                            for g in pg.page("geoms", info.geoms)]
        env["geom_cols"] = ["idx", "verts", "tris", "uv_sets", "strip", "frame", "frame_name"]
        env["frame_names"] = [f.name for f in pg.page("frames", info.frames)]
        if info.effects:
            env["effects_list"] = [[e.idx, e.type_name, _r2(e.pos)] for e in pg.page("effects", info.effects)]
    elif len(textures) > lim:
        pg.cut.append(f"textures {lim} of {len(textures)}")
    return env


def _dump_col(data: bytes, ident: dict, full: bool, pg: _Pager) -> dict:
    errs: list = []
    models = list(iter_col(data, errors=errs))
    env = obj(**ident, kind="col", models=len(models),
              by_version=dict(Counter(f"COL{m.version}" if m.version > 1 else "COLL" for m in models)),
              faces=sum(m.faces for m in models), errors=[f"#{i}: {m}" for i, m in errs[:10]])
    if full:
        env["cols"] = ["idx", "name", "version", "spheres", "boxes", "verts", "faces", "shadow_faces", "bbox"]
        env["rows"] = [[m.idx, m.name, m.version, m.spheres, m.boxes, m.verts, m.faces, m.shadow_faces, _r2(m.bbox)]
                       for m in pg.page("models", models)]
    return env


def _dump_tree(data: bytes, label: str, pg: _Pager, ident: dict) -> dict:
    size = rw_payload_size(data)
    if not size:
        raise SatkError("UNSUPPORTED", f"{label!r} is not a RenderWare file", data={"magic": data[:4].hex()})
    rows = _rw_tree(data)
    env = obj(**ident, kind="rw", rw_size=size, top=rows[0][1] if rows else None, chunks=len(rows))
    env["cols"] = ["depth", "chunk", "size", "version", "offset"]
    env["rows"] = pg.page("chunks", rows)
    return pg.mark(env)


def _inst_rows(insts) -> dict:
    rows = []
    for i in insts:
        rz = rz_deg(i.q, i.interior)
        rows.append([i.idx, i.model_id, i.name, i.interior & 0xFF, i.interior >> 8,
                     [round(v, 2) for v in i.pos],
                     round(rz, 1) if rz is not None else [round(v, 4) for v in world_quat(i.q)], i.lod])
    return {"cols": ["idx", "model", "name", "area", "iflags", "pos", "rz_or_world_q", "lod"], "rows": rows}
