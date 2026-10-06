"""Operations of ``satk.colgen``, CLI only (``mcp=False``; agents reach them through ``satk_ops``/``satk_op``):

* ``col.gen``     -> ``satk col gen <dff|sid|dir|img> --mode auto|box|boxes|hull|mesh|spheres|bounds``;
* ``col.check``   -> ``satk col check <col|dir|img|dff|model:ID>``;
* ``col.surface`` -> ``satk col surface [TEXTURE ...] [--txd NAME]`` (what ``--surface auto`` picks);
* ``col.derive``  -> ``satk col derive`` (rebuild the texture -> surface table from a profile's collisions).

Outputs are new files under ``<work>/out/colgen/`` (or an explicit writable path); game files are only read.
Module-level imports stay stdlib/satk only (numpy is imported inside the functions).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath, work
from ..core.registry import op, report_progress

Mode = Literal["auto", "box", "boxes", "hull", "mesh", "spheres", "bounds"]
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _out_root() -> Path:
    return Path(os.path.abspath(cfg().paths.work)) / "out" / "colgen"


def _out_dir(out: str | None) -> Path:
    if not out:
        d = work("out", "colgen")
    else:
        p = Path(out)
        if p.is_absolute():
            d = ensure_writable(p)
        else:
            if ".." in p.parts or not p.parts:
                raise SatkError("BAD_PARAMS", f"--out must be a plain name or relative path: {out}")
            d = ensure_writable(_out_root() / p)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_rules(items: list[str] | None, st) -> list[tuple[str, int]]:
    out = []
    for it in items or []:
        if it.count("=") != 1:
            raise SatkError("BAD_PARAMS", f"--surface-rule wants <texture glob>=<surface>, got {it!r}",
                            hint='--surface-rule "*grass*=GRASS_SHORT_DRY"')
        pat, s = (x.strip() for x in it.split("="))
        if not pat:
            raise SatkError("BAD_PARAMS", f"empty texture pattern in {it!r}")
        out.append((pat.lower(), st.id_of(s)))
    return out


def _fit_text(mode: str, fit: dict) -> str:
    parts = []
    if "outside" in fit:
        parts.append(f"out {fit['outside']:.2f}")
    if "dev" in fit:
        parts.append(f"dev {fit['dev']:.2f}")
    if "coverage" in fit:
        parts.append(f"cover {round(100 * fit['coverage'])}%")
    return " ".join(parts) or "-"


@op("col.gen", summary="Generate collision models (COL3, or COL2) from DFFs: box, boxes, convex hull, decimated mesh, "
                       "vehicle-style spheres or bounds only; surfaces from textures (table derived from vanilla), "
                       "lighting from prelit colours, optional closed shadow mesh. Output under <work>/out/colgen/.",
    summary_ru="Коллизия (COL3/COL2) из DFF: бокс, боксы, выпуклая оболочка, упрощённый меш, сферы как у машин или "
               "только границы; поверхности по текстурам (таблица из ванили), освещение из prelit, закрытая тень.",
    mcp=False, group="formats", long_running=True,
    examples=("satk col gen model:1337 --mode hull", "satk col gen mymod/barrel.dff --mode boxes --max-prims 4",
              "satk col gen model:411 --mode spheres --shadow", "satk col gen mymod --archive mymod.col",
              "satk col gen lae2_roads89 --mode mesh --max-faces 400 --surface-rule \"*grass*=GRASS_SHORT_DRY\""))
def col_gen(target: str, mode: Mode = "auto", surface: str = "auto", surface_rule: list[str] | None = None,
            shadow: bool | None = None, max_faces: int | None = None, max_prims: int | None = None,
            shadow_faces: int | None = None, version: Literal[2, 3] = 3, exclude: list[str] | None = None,
            txd: str | None = None, lighting: bool = True, resolution: int | None = None, archive: str | None = None,
            out: str | None = None, limit: int = 20, profile: str = "vanilla") -> dict:
    """Generate collision for one or many models.

    Args:
        target: .dff file, folder of .dff files, .img, <img>/<entry>.dff or SID (model:1337, model:infernus,
            dff:<name>, inst:<ipl>#<n>; a bare model name works too).
        mode: auto (vehicles -> spheres + shadow, > 25 m or > 2000 triangles -> mesh, else hull), box (bounding
            box), boxes (voxel box decomposition), hull (outer convex hull, contains the mesh), mesh (render mesh
            welded + decimated), spheres (vehicle style), bounds (no primitives, like vanilla LODs).
        surface: auto (texture -> surface table derived from vanilla, keyword fallbacks, DEFAULT) or one surface
            for everything (name like TARMAC or id 0..178).
        surface_rule: <texture glob>=<surface> rules applied before auto, e.g. "*grass*=GRASS_SHORT_DRY".
        shadow: add a closed COL3 shadow mesh (default: vehicles in auto mode).
        max_faces: face budget for hull (64) and mesh (1000); 12..20000.
        max_prims: box budget for boxes (8) / sphere budget for spheres (20); 1..1000.
        shadow_faces: face budget of the shadow mesh (300); 12..20000.
        version: 3 (SA, default) or 2.
        exclude: globs of part or texture names to leave out (e.g. "*leaf*" "*_lod").
        txd: TXD name for (TXD, texture) surface context (default: from the index for SIDs and known names).
        lighting: face lighting bytes from prelit/night colours (false: 255 = full bright).
        resolution: voxel cells along the longest axis (default: boxes 32, spheres 24, shadow 40).
        archive: write all models into one .col with this name (under --out) instead of one file each.
        out: output folder under <work>/out/colgen/ (or an absolute path); a single model may name a .col file.
        limit: rows to show (max 500).
        profile: profile whose index and game root resolve SIDs and relative paths.
    """
    from ..formats.col import iter_col
    from ..formats.rw import FormatError
    from ..rw import col as COL
    from .build import GenOptions, generate
    from .sources import resolve_sources
    from .surface import SurfaceTable

    t0 = time.monotonic()
    for v, nm, lo, hi in ((max_faces, "max_faces", 12, 20000), (max_prims, "max_prims", 1, 1000),
                          (shadow_faces, "shadow_faces", 12, 20000)):
        if v is not None and not lo <= v <= hi:
            raise SatkError("BAD_PARAMS", f"--{nm.replace('_', '-')} must be in {lo}..{hi}, got {v}")
    if resolution is not None and not 8 <= resolution <= 128:
        raise SatkError("BAD_PARAMS", f"--resolution must be in 8..128, got {resolution}")
    st = SurfaceTable.load()
    rules = _parse_rules(surface_rule, st)
    if surface.strip().lower() != "auto":
        st.id_of(surface)                                   # validate early
    opts = GenOptions(mode=mode, surface=surface, rules=rules, shadow=shadow, max_faces=max_faces,
                      max_prims=max_prims, shadow_faces=shadow_faces, version=int(version),
                      exclude=[e.lower() for e in exclude or []], lighting=lighting, resolution=resolution)
    sources = resolve_sources(target, profile)
    single_file = None
    if out and out.lower().endswith(".col") and len(sources) == 1 and not archive:
        single_file = out
        out_dir = _out_dir(str(Path(out).parent) if str(Path(out).parent) not in ("", ".") else None)
    else:
        out_dir = _out_dir(out)
    rows: list[list] = []
    warn: list[str] = []
    models = []
    encoded: list[bytes] = []
    files: list[str] = []
    used_names: set[str] = set()
    how_total: dict[str, int] = {}
    for i, src in enumerate(sources):
        report_progress(i, len(sources), src.name)
        try:
            res = generate(src.read(), src.name, sec=src.sec, txd=(txd or src.txd), opts=opts, table=st)
        except FormatError as e:
            if len(sources) == 1:
                raise SatkError("UNSUPPORTED", f"cannot generate collision for {src.label}: {e}",
                                hint="satk asset lint " + src.label) from None
            warn.append(f"SKIPPED: {src.name}: {e}")
            continue
        m = res.model
        try:
            rec = COL.encode_model(m)
        except (COL.ColError, OverflowError, ValueError) as e:
            raise SatkError("BAD_PARAMS", f"{src.name}: cannot encode the collision: {e}") from None
        models.append((src, res))
        encoded.append(rec)
        for k, n in res.how.items():
            how_total[k] = how_total.get(k, 0) + n
        for note in res.notes:
            warn.append(f"NOTE: {src.name}: {note}")
        path = ""
        if not archive:
            stem = _SAFE.sub("_", m.name) or "model"
            base = stem
            k = 2
            while base.lower() in used_names:
                base = f"{stem}_{k}"
                k += 1
            used_names.add(base.lower())
            target_path = (out_dir / Path(single_file).name) if single_file else out_dir / f"{base}.col"
            atomic_write(target_path, rec)
            path = jpath(target_path)
            files.append(path)
        top = ", ".join(f"{st.name(s)}:{n}" for s, n in res.surfaces.most_common(3))
        rows.append([m.name, res.mode, len(m.spheres), len(m.boxes), len(m.faces), len(m.shadow_faces), top or "-",
                     _fit_text(res.mode, res.fit), path or "-"])
    if not models:
        raise SatkError("NOT_FOUND", f"no collision could be generated from {target}", data={"warn": warn[:20]})
    env_extra: dict = {}
    blob_all = b"".join(encoded)
    if archive:
        name = archive if archive.lower().endswith(".col") else archive + ".col"
        p = Path(name)
        apath = ensure_writable(p) if p.is_absolute() else out_dir / p.name
        apath.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(apath, blob_all)
        env_extra["path"] = jpath(apath)
        env_extra["size"] = len(blob_all)
        names = [m.name.lower() for _s, r in models for m in (r.model,)]
        dups = sorted({n for n in names if names.count(n) > 1})
        if dups:
            warn.append(f"DUPLICATE_NAME: {', '.join(dups[:5])} (the game matches collision by name: the first wins)")
    else:
        env_extra["dir"] = jpath(out_dir)
    errs: list = []
    back = list(iter_col(blob_all, errors=errs))           # independent reader (satk.formats)
    verified = len(back) == len(models) and not errs and all(
        b.faces == len(r.model.faces) and b.spheres == len(r.model.spheres) and b.boxes == len(r.model.boxes)
        for b, (_s, r) in zip(back, models))
    total = sum(how_total.values())
    lim = clamp_limit(limit)
    cols = ["name", "mode", "spheres", "boxes", "faces", "shadow", "surfaces", "fit", "file"]
    env = table(cols, rows[:lim], total=len(rows), warn=warn[:20])
    env.update(env_extra)
    env["models"] = len(models)
    env["verified"] = verified
    if total:
        env["surface_from"] = {k: f"{round(100 * v / total)}%" for k, v in sorted(how_total.items(), key=lambda kv: -kv[1])}
    env["seconds"] = round(time.monotonic() - t0, 2)
    if len(warn) > 20:
        env["warn_total"] = len(warn)
    return env


# ============================================================================ col check


def _col_blobs(target: str, profile: str) -> list[tuple[str, bytes]]:
    """``(label, collision bytes)`` for a .col, folder, .img, <img>/<entry>, .dff or model SID."""
    from ..formats.dat import resolve_ci
    from ..formats.dff import find_embedded_col
    from ..formats.img import ImgArchive
    from ..formats.rw import FormatError, rw_payload_size
    from ..core.paths import open_ro, profile_root

    def embedded(label: str, data: bytes) -> tuple[str, bytes] | None:
        r = find_embedded_col(data[:rw_payload_size(data) or len(data)])
        return (label, data[r[0]:r[0] + r[1]]) if r else None

    t = target.strip()
    head, sep, _rest = t.partition(":")
    if sep and head.lower() == "model":
        from ..index.api import open_index, read_blob_bytes
        from ..model3d.resolve import model_files

        mf = model_files(open_index(profile), _rest)
        if mf.col is None:
            raise SatkError("NOT_FOUND", f"{t} has no collision", hint=f"satk col gen {t}")
        data = read_blob_bytes(mf.col.blob)
        if mf.col.via == "embedded":
            hit = embedded(mf.col.blob.sid, data)
            if hit is None:
                raise SatkError("NOT_FOUND", f"{t}: embedded collision not found")
            return [hit]
        from ..rw import col as COL

        recs, _tail = COL.split_models(data)
        return [(f"{mf.col.blob.sid}#{mf.col.idx}", recs[mf.col.idx])]

    def find(path: str) -> Path | None:
        p = Path(path)
        if p.is_absolute():
            return p if p.exists() else None
        if (Path.cwd() / p).exists():
            return Path.cwd() / p
        if (_out_root() / p).exists():
            return _out_root() / p
        try:
            return resolve_ci(profile_root(profile), path)
        except SatkError:
            return None

    norm = t.replace("\\", "/")
    cut = norm.lower().find(".img/")
    out: list[tuple[str, bytes]] = []
    try:
        if cut > 0:
            arc = find(norm[:cut + 4])
            if arc is None:
                raise SatkError("NOT_FOUND", f"no archive {norm[:cut + 4]!r}")
            with ImgArchive.open(arc) as a:
                e = a.find(norm[cut + 5:])
                if e is None:
                    raise SatkError("NOT_FOUND", f"no entry {norm[cut + 5:]!r} in {jpath(arc)}")
                data = a.read(e)
            label = f"{norm[:cut + 4]}/{e.name}"
            if e.ext == "dff":
                hit = embedded(label, data)
                return [hit] if hit else []
            return [(label, data)]
        p = find(t)
        if p is None:
            raise SatkError("NOT_FOUND", f"no file, folder or SID {target!r}",
                            hint="a .col, a folder of .col files, an .img, <img>/<entry>.col, a vehicle .dff or model:<id>")
        if p.is_dir():
            for f in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                if f.is_file() and f.suffix.lower() == ".col":
                    with open_ro(f) as fh:
                        out.append((jpath(f), fh.read()))
        elif p.suffix.lower() == ".img":
            with ImgArchive.open(p) as a:
                for e in a.entries:
                    if e.ext == "col":
                        out.append((f"{jpath(p)}/{e.name}", a.read(e)))
        else:
            with open_ro(p) as fh:
                data = fh.read()
            if p.suffix.lower() == ".dff":
                hit = embedded(jpath(p), data)
                if hit is None:
                    raise SatkError("NOT_FOUND", f"{jpath(p)} has no embedded collision")
                out.append(hit)
            else:
                out.append((jpath(p), data))
    except FormatError as e:
        raise SatkError("UNSUPPORTED", f"cannot read {target}: {e}") from None
    if not out:
        raise SatkError("NOT_FOUND", f"no collision files in {target}")
    return out


@op("col.check", summary="Check collision files: face/vertex limits, degenerate faces, bounds and bounding sphere "
                         "containing all geometry, valid surface ids, face groups, flags, closed and correctly wound "
                         "shadow meshes. Target: .col, folder, .img, <img>/<entry>, vehicle .dff or model:ID.",
    summary_ru="Проверка COL: лимиты граней/вершин, вырожденные грани, границы и сфера охватывают геометрию, "
               "id поверхностей, группы граней, флаги, закрытая и правильно ориентированная тень.",
    mcp=False, group="formats",
    examples=("satk col check models/coll/countn2.col", "satk col check model:411",
              "satk col check props.col --sev info", "satk col check mymod --fail-on error"))
def col_check(target: str, sev: Literal["info", "warn", "error"] = "warn",
              fail_on: Literal["never", "warn", "error"] = "never", limit: int = 20, cursor: str | None = None,
              profile: str = "vanilla") -> dict:
    """Check collision models.

    Args:
        target: .col file, folder of .col files, .img, <img>/<entry>.col, vehicle .dff (embedded COL) or model:<id>.
        sev: lowest severity shown in the table (the summary counts all): info|warn|error.
        fail_on: return CHECK_FAILED when a finding of this severity or higher exists.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
        profile: profile whose game root and index resolve the target.
    """
    from .check import SEVERITIES, check_blob

    blobs = _col_blobs(target, profile)
    findings = []
    warn: list[str] = []
    n_models = 0
    worst: dict[str, int] = {"ok": 0, "info": 0, "warn": 0, "error": 0}
    by_check: dict[str, int] = {}
    for label, data in blobs:
        reps, problems = check_blob(data)
        if not reps and problems:
            findings.append([label, "error", "parse", problems[0]])
            worst["error"] += 1
            continue
        for p in problems:
            warn.append(f"{p} ({label})")
        for r in reps:
            n_models += 1
            worst[r.worst()] += 1
            for f in r.findings:
                by_check[f.check] = by_check.get(f.check, 0) + 1
                row = f.row()
                if len(blobs) > 1:
                    row[0] = f"{label.rsplit('/', 1)[-1]}:{row[0]}"
                findings.append(row)
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    findings.sort(key=lambda r: (-rank[r[1]], r[2], r[0]))
    shown = [r for r in findings if rank[r[1]] >= rank[sev]]
    start = 0
    if cursor:
        if not cursor.isdigit():
            raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}")
        start = int(cursor)
    lim = clamp_limit(limit)
    page = shown[start:start + lim]
    nxt = str(start + lim) if start + lim < len(shown) else None
    env = table(["model", "sev", "check", "msg"], page, total=len(shown), next=nxt, warn=warn[:20])
    env["files"] = len(blobs)
    env["models"] = n_models
    env["summary"] = {k: v for k, v in worst.items() if v}
    if by_check:
        env["by_check"] = dict(sorted(by_check.items(), key=lambda kv: -kv[1])[:20])
    if fail_on != "never":
        bad = [r for r in findings if rank[r[1]] >= rank[fail_on]]
        if bad:
            raise SatkError("CHECK_FAILED", f"{len(bad)} collision finding(s) at {fail_on} or above in {target}",
                            hint=f"satk col check {target} --sev {fail_on}",
                            data={"cols": ["model", "sev", "check", "msg"], "rows": bad[:lim]})
    return env


# ============================================================================ col surface / derive


@op("col.surface", summary="What --surface auto picks for texture names (TXD context optional): surface id, name, "
                           "decided by txd/table/keyword/default, vanilla share and votes. No texture: list all "
                           "179 surface ids and names.",
    summary_ru="Какую поверхность выберет --surface auto для текстур (с учётом TXD): id, имя, источник, доля и "
               "голоса в ванили. Без текстур — список всех 179 поверхностей.",
    mcp=False, group="formats",
    examples=("satk col surface grass_128hv dirt64 --txd lae2_ground", "satk col surface --limit 200"))
def col_surface(texture: list[str] | None, txd: str | None = None, limit: int = 20) -> dict:
    """Look up surfaces.

    Args:
        texture: texture names (case-insensitive); none = list the surface ids.
        txd: TXD name for the (TXD, texture) context.
        limit: rows to show (max 500).
    """
    from .surface import SurfaceTable

    st = SurfaceTable.load()
    lim = clamp_limit(limit)
    if not texture:
        rows = [[i, n] for i, n in enumerate(st.names)]
        return table(["id", "name"], rows[:lim], total=len(rows))
    rows = []
    for t in texture:
        sid, how = st.lookup(t, txd)
        row = (st.txd.get((txd or "").lower()) or {}).get(t.lower()) if how == "txd" else st.textures.get(t.lower())
        rows.append([t, sid, st.name(sid), how, f"{row[1]}%" if row else "-", row[2] if row else 0])
    return table(["texture", "id", "surface", "how", "share", "votes"], rows[:lim], total=len(rows))


@op("col.derive", summary="Rebuild the texture -> surface table from a profile's own collisions (faces matched to "
                          "the nearest render triangle) with a hold-out accuracy report and the prelit lighting fit. "
                          "Writes <work>/out/colgen/tex_surface.json (the package copy is data/colgen).",
    summary_ru="Пересобрать таблицу текстура -> поверхность по коллизиям профиля (грань -> ближайший треугольник), "
               "с отчётом точности на отложенных моделях и подгонкой освещения. Пишет <work>/out/colgen/.",
    mcp=False, group="formats", long_running=True,
    examples=("satk col derive", "satk col derive --holdout 0 --profile installed"))
def col_derive(profile: str = "vanilla", holdout: int = 10, max_models: int | None = None, out: str | None = None,
               limit: int = 20) -> dict:
    """Derive the surface table.

    Args:
        profile: index profile whose models (DFF + collision) vote.
        holdout: every Nth model (id % N == 3) is held out to measure accuracy (0 = no evaluation).
        max_models: only the first N models (a quick try).
        out: output file under <work>/out/colgen/ (or an absolute path); default tex_surface.json.
        limit: surface rows to show (max 500).
    """
    from .derive import derive, table_text

    if holdout < 0 or holdout == 1:
        raise SatkError("BAD_PARAMS", "--holdout must be 0 (off) or >= 2")
    res = derive(profile, holdout=holdout, limit=max_models,
                 progress=lambda i, n: report_progress(i, n, "models"))
    name = out or "tex_surface.json"
    p = Path(name)
    path = ensure_writable(p) if p.is_absolute() else _out_dir(None) / p.name
    atomic_write(path, table_text(res))
    lim = clamp_limit(limit)
    env = table(["id", "surface", "votes"], res.rows[:lim], total=len(res.rows))
    env["path"] = jpath(path)
    env.update({k: v for k, v in res.report.items() if k != "profile"})
    env["lighting"] = {k: res.lighting[k] for k in ("day", "night", "day_mae") if k in res.lighting}
    return env
