"""Operations of ``satk.rw`` (M3-B1): ``rw patch|roundtrip``, ``col write|export``, ``img build|diff``.

All CLI only (``mcp=False``). Inputs are paths that are absolute, relative to the profile's game root
(any case; ``<archive>.img/<entry>`` addresses an IMG entry), relative to the current folder, or names
under ``<work>/out/rw/`` (so ``rw patch --out x`` chains into ``img build x``). Outputs are new files
under ``<work>/out/rw/`` (or an explicit writable path); game files are only read.

Module-level imports stay stdlib/satk only (SPEC §2.3).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import struct
from pathlib import Path
from typing import Callable, Literal

from ..core.envelope import clamp_limit, obj, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath, open_ro, profile_root, work
from ..core.registry import op

Profile = Literal["vanilla", "installed", "samp", "game"]


# ============================================================================ input/output paths


def _out_root() -> Path:
    return Path(os.path.abspath(cfg().paths.work)) / "out" / "rw"


def _find(path: str, profile: str, *, what: str = "file") -> Path:
    """Resolve an input path (see the module doc)."""
    from ..formats.dat import resolve_ci

    s = path[5:] if path.lower().startswith("file:") else path
    p = Path(s)
    if p.is_absolute():
        if p.exists():
            return p
        raise SatkError("NOT_FOUND", f"no such {what}: {jpath(p)}")
    try:
        hit = resolve_ci(profile_root(profile), s)
    except SatkError:
        hit = None
    if hit is not None:
        return hit
    for cand in (Path.cwd() / s, _out_root() / s):
        if cand.exists():
            return cand
    raise SatkError("NOT_FOUND", f"no {what} {path!r} under the {profile} game root, the current folder or "
                                 f"{jpath(_out_root())}", hint="give an absolute path or one relative to the game root")


def _split_entry(path: str) -> tuple[str, str] | None:
    norm = path.replace("\\", "/")
    cut = norm.lower().find(".img/")
    if cut > 0:
        return norm[:cut + 4], norm[cut + 5:]
    return None


def _out_path(out: str | None, default_name: str, suffix: str) -> Path:
    """``out``: absolute path, or a name/relative path under ``<work>/out/rw/``; ``suffix`` is appended
    when missing."""
    name = out or default_name
    if Path(name).suffix.lower() != suffix:
        name += suffix
    p = Path(name)
    if p.is_absolute():
        d = ensure_writable(p)
        d.parent.mkdir(parents=True, exist_ok=True)
        return d
    if ".." in p.parts or not p.parts:
        raise SatkError("BAD_PARAMS", f"--out must be a plain name or relative path: {out}")
    return work("out", "rw", *p.parts)


def _out_dir(out: str) -> Path:
    p = Path(out)
    if p.is_absolute():
        d = ensure_writable(p)
    else:
        if ".." in p.parts or not p.parts:
            raise SatkError("BAD_PARAMS", f"bad output folder {out!r}")
        d = ensure_writable(_out_root() / p)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_entries(inputs: list[str], profile: str, ext: str,
                  stack: contextlib.ExitStack | None = None) -> list[tuple[str, str, Callable[[], bytes]]]:
    """``(label, name, read)`` for every ``ext`` file named by ``inputs`` (files, folders, IMGs, entries).

    Data is read lazily (``read()``); archives stay open in ``stack`` (or are read eagerly without one)."""
    from ..formats.img import ImgArchive
    from ..formats.rw import FormatError

    def file_reader(f: Path) -> Callable[[], bytes]:
        def read() -> bytes:
            with open_ro(f) as fh:
                return fh.read()
        return read

    def entry_reader(a, e) -> Callable[[], bytes]:
        if stack is None:
            data = a.read(e)
            return lambda: data
        return lambda: a.read(e)

    out: list[tuple[str, str, Callable[[], bytes]]] = []
    for item in inputs:
        sp = _split_entry(item)
        try:
            if sp is not None or _find(item, profile).suffix.lower() == ".img":
                arc = _find(sp[0] if sp else item, profile, what="archive")
                a = ImgArchive.open(arc)
                if stack is not None:
                    stack.enter_context(a)
                try:
                    if sp is not None:
                        e = a.find(sp[1])
                        if e is None:
                            raise SatkError("NOT_FOUND", f"no entry {sp[1]!r} in {jpath(arc)}",
                                            hint=f"satk formats ls {sp[0]} --name {sp[1].rsplit('.', 1)[0]}")
                        out.append((f"{sp[0]}/{e.name}", e.name, entry_reader(a, e)))
                    else:
                        out += [(f"{item}/{e.name}", e.name, entry_reader(a, e)) for e in a.entries if e.ext == ext]
                finally:
                    if stack is None:
                        a.close()
                continue
            p = _find(item, profile)
            if p.is_dir():
                files = sorted((f for f in p.iterdir() if f.is_file() and f.suffix.lower() == "." + ext),
                               key=lambda f: f.name.lower())
                out += [(jpath(f), f.name, file_reader(f)) for f in files]
            else:
                out.append((jpath(p), p.name, file_reader(p)))
        except FormatError as e:
            raise SatkError("UNSUPPORTED", f"cannot read {item}: {e}") from None
    if not out:
        raise SatkError("NOT_FOUND", f"no .{ext} files in {', '.join(inputs)}")
    return out


# ============================================================================ rw patch


def _parse_renames(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items or []:
        if it.count("=") != 1:
            raise SatkError("BAD_PARAMS", f"--rename-tex wants old=new, got {it!r}")
        old, new = (x.strip() for x in it.split("="))
        if not old or not new:
            raise SatkError("BAD_PARAMS", f"--rename-tex wants old=new, got {it!r}")
        if old.lower() in out and out[old.lower()] != new:
            raise SatkError("BAD_PARAMS", f"--rename-tex: {old!r} renamed twice")
        out[old.lower()] = new
    return out


_COLOR = re.compile(r"^\s*(?:(\d+)\s*:\s*)?(\d+)\s*=\s*(.+?)\s*$")


def _parse_colors(items: list[str] | None) -> list[tuple[int | None, int, tuple[int, int, int, int]]]:
    """``N=r,g,b[,a]`` (formats dump row) or ``G:I=r,g,b[,a]``; also ``#rrggbb[aa]``. The CLI splits list
    values at commas, so the pieces after a ``=`` token are joined back."""
    joined: list[str] = []
    for it in items or []:
        if "=" in it or not joined:
            joined.append(it)
        else:
            joined[-1] += "," + it
    out = []
    for spec in joined:
        m = _COLOR.match(spec)
        if not m:
            raise SatkError("BAD_PARAMS", f"--material-color wants N=r,g,b[,a] or G:I=r,g,b[,a], got {spec!r}")
        geom = int(m.group(1)) if m.group(1) is not None else None
        idx = int(m.group(2))
        val = m.group(3).strip()
        if val.startswith("#") and len(val) in (7, 9):
            try:
                comps = [int(val[i:i + 2], 16) for i in range(1, len(val), 2)]
            except ValueError:
                comps = []
        else:
            try:
                comps = [int(x) for x in val.split(",")]
            except ValueError:
                comps = []
        if len(comps) == 3:
            comps.append(255)
        if len(comps) != 4 or not all(0 <= c <= 255 for c in comps):
            raise SatkError("BAD_PARAMS", f"--material-color {spec!r}: colour must be r,g,b[,a] with 0..255")
        out.append((geom, idx, tuple(comps)))
    return out


@op("rw.patch", summary="Patch DFF models without Blender: rename textures, set RW version (iii/vc/sa), night "
                        "colours, material colours, seam-aware smooth normals (--smooth-normals), frame-local "
                        "bounding spheres (--recalc-bsphere). Writes copies under <work>/out/rw/<out>/.",
    summary_ru="Правка DFF без Blender: текстуры, версия RW, ночные цвета, цвет материала, сглаживание нормалей "
               "со швами, ограничивающие сферы. Копии — в <work>/out/rw/<out>/.",
    mcp=False, group="formats",
    examples=("satk rw patch models/gta3.img/infernus.dff --rename-tex vehiclelights128=mylights128",
              "satk rw patch models/gta3.img/lae2_roads04.dff --rw-version vc --night-colors remove",
              "satk rw patch mycar/premier.dff --smooth-normals --recalc-bsphere --out mycar_fixed",
              "satk rw patch mydir --material-color 0=255,0,0 --out fixed"))
def rw_patch(dff: list[str], rename_tex: list[str] | None = None, rw_version: Literal["iii", "vc", "sa"] | None = None,
             night_colors: Literal["add", "remove"] | None = None, recalc_normals: bool = False,
             material_color: list[str] | None = None, out: str = "patch", dry_run: bool = False, limit: int = 20,
             profile: Profile = "vanilla", smooth_normals: bool = False, angle: float = 45.0, weld: float = 0.001,
             split_at: list[str] | None = None, recalc_bsphere: bool = False) -> dict:
    """Patch DFF files.

    Args:
        dff: DFF files, folders (their *.dff), IMG archives (all DFF entries) or <img>/<entry>.
        rename_tex: old=new texture renames (case-insensitive; diffuse, mask, MatFX and specular textures).
        rw_version: re-stamp to GTA III (3.1.0.1), Vice City (3.3.0.2) or San Andreas (3.6.0.3).
        night_colors: add (copy of the prelit day colours where missing) or remove the night vertex colours.
        recalc_normals: rebuild normals per vertex record from its own triangles; it does NOT weld, so a
            flat-shaded export (every face with its own vertices) stays flat: use --smooth-normals.
        material_color: N=r,g,b[,a] (N = row of formats dump --level full) or G:I=r,g,b[,a] (geometry G, slot I).
        out: output folder under <work>/out/rw/ (or an absolute path).
        dry_run: only report what would change.
        limit: rows to show (changed files first; max 500).
        profile: profile whose game root resolves relative paths.
        smooth_normals: seam-aware smooth normals: vertices at one position (--weld) share the normals of faces
            within --angle of each other, except across the --split-at seams; vertex records, UVs and skin stay
            as they are; a geometry whose shading would get flatter keeps its normals.
        angle: largest angle (degrees) between faces that are smoothed together (vanilla rule: 45).
        weld: position grid (metres) for treating split vertices as one position.
        split_at: seams that stay hard: material (default) and/or uv; none = only the angle decides.
        recalc_bsphere: recompute geometry bounding spheres that do not enclose their vertices or are
            oversized (DragonFF writes world-space spheres; lint dff.bsphere).
    """
    renames = _parse_renames(rename_tex)
    colors = _parse_colors(material_color)
    if not (renames or rw_version or night_colors or recalc_normals or colors or smooth_normals or recalc_bsphere):
        raise SatkError("BAD_PARAMS", "nothing to do: give --rename-tex, --rw-version, --night-colors, "
                                      "--recalc-normals, --smooth-normals, --recalc-bsphere or --material-color",
                        hint="satk rw patch -h")
    if recalc_normals and smooth_normals:
        raise SatkError("BAD_PARAMS", "--recalc-normals and --smooth-normals both rebuild the normals; pick one",
                        hint="--smooth-normals welds split vertices (what a flat-shaded export needs)")
    if not 0 < angle <= 180 or weld < 0:
        raise SatkError("BAD_PARAMS", f"--angle must be in (0, 180] and --weld >= 0 (got {angle}, {weld})")
    bad = [x for x in split_at or [] if x not in ("material", "uv", "none")]
    if bad:
        raise SatkError("BAD_PARAMS", f"--split-at takes material, uv or none, got {', '.join(bad)}")
    seams = tuple(x for x in (split_at or ["material"]) if x != "none")
    smooth = (angle, weld, seams) if smooth_normals else None
    with contextlib.ExitStack() as stack:
        return _patch(_load_entries(dff, profile, "dff", stack), renames, colors, rw_version, night_colors,
                      recalc_normals, out, dry_run, limit, smooth, recalc_bsphere)


def _hd_bend(buf: bytes) -> float | None:
    """Pooled ``shade.normal_bend`` (degrees) of the HD geometries of a DFF (no ``_dam``/``_vlo``), or None."""
    from ..formats.dff import decode_geometries
    from ..formats.rw import FormatError
    from ..lint.anat import read_frames
    from ..lint.metrics import model_shading

    try:
        sh = model_shading(decode_geometries(buf), {f.idx: f.name for f in read_frames(buf)})
    except (FormatError, ValueError, IndexError, struct.error):
        return None
    return None if sh is None else float(sh["shade.normal_bend"])


def _patch(items, renames, colors, rw_version, night_colors, recalc_normals, out, dry_run, limit, smooth=None,
           recalc_bsphere=False) -> dict:
    from .chunk import GAME_VERSIONS
    from .dff import DffDoc, PatchError

    seen: dict[str, str] = {}
    for label, name, _d in items:
        k = name.lower()
        if k in seen:
            raise SatkError("AMBIGUOUS", f"two inputs write {name}: {seen[k]} and {label}",
                            hint="patch them in separate runs with different --out folders")
        seen[k] = label
    odir = None if dry_run else _out_dir(out)
    rows: list[list] = []
    warn: list[str] = []
    stats = {"written": 0, "unchanged": 0, "failed": 0}
    for label, name, read in items:
        try:
            doc = DffDoc.parse(read())
            before = doc.to_bytes()
            changes: list[tuple[str, int]] = []
            w: list[str] = []
            if rw_version:
                changes += doc.restamp(GAME_VERSIONS[rw_version])
            if renames:
                changes += doc.rename_textures(renames)
            if colors:
                changes += doc.set_material_color(colors)
            if night_colors:
                changes += doc.night_colors(night_colors, w)
            if recalc_normals:
                changes += doc.recalc_normals(w)
            if smooth is not None:
                changes += doc.smooth_normals(angle=smooth[0], weld=smooth[1], split_at=smooth[2], warn=w)
            if recalc_bsphere:
                changes += doc.recalc_bsphere()
            after = doc.to_bytes()
            if (smooth is not None or recalc_normals) and after != before:
                b0, b1 = _hd_bend(before), _hd_bend(after)
                if b0 is not None and b1 is not None:
                    changes.append(("bend", f"{b0:.2f}->{b1:.2f}"))
                    if recalc_normals and b1 < b0 - 0.01:
                        w.append(f"FLATTER: --recalc-normals lowered the shading bend {b0:.2f}->{b1:.2f} deg "
                                 "(it does not weld); use --smooth-normals")
        except PatchError as e:
            if len(items) == 1:
                raise SatkError("UNSUPPORTED", f"{label}: {e}") from None
            stats["failed"] += 1
            rows.append([name, "failed", str(e)[:120], None])
            continue
        warn += [f"{x} ({name})" for x in w]
        desc = ", ".join(f"{k}:{v}" for k, v in changes)
        if after == before:
            stats["unchanged"] += 1
            rows.append([name, "unchanged", desc or None, None])
            continue
        if dry_run:
            stats["written"] += 1
            rows.append([name, "would-write", desc, None])
            continue
        target = odir / name
        atomic_write(target, after)
        stats["written"] += 1
        rows.append([name, "written", desc, jpath(target)])
    if len(items) == 1 and stats["unchanged"] == 1 and renames:
        warn.append(f"NO_MATCH: none of {', '.join(sorted(renames))} is used by {items[0][1]}")
    order = {"written": 0, "would-write": 0, "failed": 1, "unchanged": 2}
    rows.sort(key=lambda r: order[r[1]])
    lim = clamp_limit(limit)
    env = table(["name", "status", "changes", "out"], rows[:lim], total=len(rows), warn=warn[:20])
    env.update(stats)
    if odir is not None:
        env["out_dir"] = jpath(odir)
    return env


# ============================================================================ rw roundtrip


@op("rw.roundtrip", summary="Measure bit-for-bit round trips of the satk RW writers (or of the vendored rwfury) on "
                            "DFF/COL files: the whole game, one IMG or one file; reports exact shares and "
                            "mismatch classes.",
    summary_ru="Замер круговой записи DFF/COL бит в бит (писатели satk или rwfury): вся игра, IMG или файл.",
    mcp=False, group="formats", long_running=True,
    examples=("satk rw roundtrip models/gta_int.img", "satk rw roundtrip --kind col",
              "satk rw roundtrip models/gta3.img --kind dff --engine rwfury --step 20"))
def rw_roundtrip(target: str | None, kind: Literal["dff", "col", "all"] = "all",
                 engine: Literal["satk", "rwfury"] = "satk", restamp: bool = True, step: int = 1,
                 examples: int = 3, profile: Profile = "vanilla") -> dict:
    """Round-trip measurement.

    Args:
        target: IMG archive, DFF/COL file or folder (default: the whole game root of the profile).
        kind: dff, col or all.
        engine: satk (the writers of this package) or rwfury (vendored 0.6.1, the baseline).
        restamp: also run SA -> Vice City -> SA on every DFF (satk engine).
        step: measure every step-th file only.
        examples: example file names per mismatch class.
        profile: profile whose game root is used.
    """
    from . import roundtrip as RT

    if step < 1:
        raise SatkError("BAD_PARAMS", "--step must be >= 1")
    root = profile_root(profile)
    if target:
        p = _find(target, profile)
        label = target
    else:
        p, label = root, jpath(root)
        if not (root / "models").is_dir():
            raise SatkError("NOT_FOUND", f"not a game root (no models/): {jpath(root)}")

    def source(k: str):
        from ..formats.img import ImgArchive

        if p.is_dir() and (p / "models").is_dir():
            it = RT.iter_game(p, k)
        elif p.is_dir():
            it = ((jpath(f), f.read_bytes()) for f in sorted(p.iterdir(), key=lambda f: f.name.lower())
                  if f.is_file() and f.suffix.lower() == "." + k)
        elif p.suffix.lower() == ".img":
            def gen():
                with ImgArchive.open(p) as a:
                    for e in a.entries:
                        if e.ext == k:
                            yield f"{label}/{e.name}", a.read(e)
            it = gen()
        elif p.suffix.lower() == "." + k:
            it = iter([(label, p.read_bytes())])
        else:
            it = iter(())
        for i, x in enumerate(it):
            if i % step == 0:
                yield x

    if engine == "rwfury":
        from .vendor import load

        if load() is None:
            raise SatkError("DEPENDENCY", "vendor/rwfury is not available in this installation")
        res = RT.measure_rwfury(source, kind)
    else:
        res = RT.measure(source, kind, restamp=restamp, examples=examples)
    return obj(target=label, engine=engine, **res)


# ============================================================================ col write / export


@op("col.write", summary="Write a collision file (COL3 by default) from JSON: spheres, boxes, mesh, face groups, "
                         "shadow mesh, surfaces as IDs or names. Missing bounds, flags and face groups are computed "
                         "like Rockstar's files. Output under <work>/out/rw/.",
    summary_ru="COL (по умолчанию COL3) из JSON: сферы, боксы, меш, группы граней, тень, поверхности. "
               "Вывод — <work>/out/rw/.",
    mcp=False, group="formats",
    examples=("satk col write mybox.json", "satk col write box.json --out props.col --version 3",
              "satk col export models/gta3.img/countn2_1.col --model sw_bigburnt"))
def col_write(src: str, out: str | None = None, version: Literal[1, 2, 3, 4] | None = None,
              profile: Profile = "vanilla") -> dict:
    """Build a .col from JSON.

    Args:
        src: JSON file (see docs/en/rw.md) or the JSON text itself (starting with "{").
        out: output file name under <work>/out/rw/ (or an absolute path); default: <json stem>.col.
        version: collision version for models without "version" (1=COLL, 2, 3=SA PC default, 4).
        profile: profile whose game root resolves a relative JSON path.
    """
    from ..formats.col import iter_col
    from . import col as COL
    from .vendor import surface_names

    if src.lstrip().startswith("{"):
        text, stem = src, None
    else:
        p = _find(src, profile, what="JSON file")
        text, stem = p.read_text(encoding="utf-8-sig"), p.stem
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise SatkError("BAD_PARAMS", f"invalid JSON: {e}") from None
    models_js = doc.get("models") if isinstance(doc, dict) and "models" in doc else [doc]
    if not isinstance(models_js, list) or not models_js:
        raise SatkError("BAD_PARAMS", "JSON must be {\"models\": [...]} or one model object")
    dv = version or (doc.get("version") if isinstance(doc, dict) and isinstance(doc.get("version"), int)
                     and "models" in doc else None) or 3
    names = surface_names()
    warn: list[str] = []
    models = []
    seen: set[str] = set()
    for i, mj in enumerate(models_js):
        try:
            m = COL.model_from_json(mj, surfaces=names, default_version=dv, warn=warn)
        except COL.ColError as e:
            raise SatkError("BAD_PARAMS", f"model {i}: {e}") from None
        except (TypeError, ValueError, AttributeError, KeyError) as e:
            raise SatkError("BAD_PARAMS", f"model {i}: malformed JSON ({type(e).__name__}: {e})") from None
        if m.name.lower() in seen:
            warn.append(f"DUPLICATE_NAME: {m.name} appears twice (the game matches collision by name)")
        seen.add(m.name.lower())
        models.append(m)
    try:
        blob = COL.join_models(models)
    except (COL.ColError, struct.error, OverflowError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"cannot encode: {e}") from None
    target = _out_path(out, stem or models[0].name, ".col")
    atomic_write(target, blob)
    errs: list = []
    check = list(iter_col(blob, errors=errs))           # independent reader (satk.formats)
    rows = [[m.name, m.version, len(m.spheres), len(m.boxes), len(m.vertices), len(m.faces), len(m.groups),
             len(m.shadow_faces), m.flags] for m in models]
    env = table(["name", "version", "spheres", "boxes", "verts", "faces", "face_groups", "shadow_faces", "flags"],
                rows, warn=warn)
    env.update(path=jpath(target), size=len(blob), models=len(models),
               verified=len(check) == len(models) and not errs)
    return env


@op("col.export", summary="Export collision models (a .col, an IMG entry or a vehicle DFF's embedded COL) to the "
                          "JSON that 'col write' accepts (exact round trip).",
    summary_ru="Экспорт COL (файл, запись IMG или встроенная COL машины) в JSON для col write.",
    mcp=False, group="formats",
    examples=("satk col export models/gta3.img/countn2_1.col --model sw_bigburnt", "satk col export models/gta3.img/infernus.dff"))
def col_export(target: str, model: str | None = None, out: str | None = None, raw: bool = True,
               limit: int = 20, profile: Profile = "vanilla") -> dict:
    """Collision -> JSON.

    Args:
        target: .col file, <img>/<entry>.col or a .dff with embedded collision.
        model: only this model (name, case-insensitive).
        out: output file under <work>/out/rw/ (or an absolute path); default: <target stem>.json.
        raw: keep Rockstar's garbage bytes (name padding, vertex padding) so that col write is bit-exact.
        limit: rows to show (max 500).
        profile: profile whose game root resolves relative paths.
    """
    from ..formats.dff import find_embedded_col
    from ..formats.rw import rw_payload_size
    from . import col as COL

    items = _load_entries([target], profile, "dff" if target.lower().endswith(".dff") else "col")
    if len(items) != 1:
        raise SatkError("BAD_PARAMS", f"{target} names {len(items)} files; export one .col/.dff at a time")
    label, name, read = items[0]
    data = read()
    if name.lower().endswith(".dff"):
        r = find_embedded_col(data[:rw_payload_size(data) or 0])
        if r is None:
            raise SatkError("NOT_FOUND", f"{label} has no embedded collision")
        data = data[r[0]:r[0] + r[1]]
    try:
        recs, _tail = COL.split_models(data)
        models = [COL.decode_model(x) for x in recs]
    except COL.ColError as e:
        raise SatkError("UNSUPPORTED", f"cannot decode {label}: {e}") from None
    if model:
        models = [m for m in models if m.name.lower() == model.lower()]
        if not models:
            raise SatkError("NOT_FOUND", f"no model {model!r} in {label}",
                            hint=f"satk formats dump {target} --level full")
    doc = {"format": "satk-col", "source": label, "models": [COL.model_to_json(m, raw=raw) for m in models]}
    stem = (model or name.rsplit(".", 1)[0])
    path = _out_path(out, stem, ".json")
    atomic_write(path, json.dumps(doc, ensure_ascii=True, separators=(",", ":")) + "\n")
    rows = [[m.name, m.version, len(m.spheres), len(m.boxes), len(m.vertices), len(m.faces), len(m.groups),
             len(m.shadow_faces)] for m in models]
    lim = clamp_limit(limit)
    env = table(["name", "version", "spheres", "boxes", "verts", "faces", "face_groups", "shadow_faces"],
                rows[:lim], total=len(rows))
    env.update(path=jpath(path), models=len(models), source=label)
    return env


# ============================================================================ img build / diff


@op("img.build", summary="Build a NEW IMG archive (VER2 for SA, or VER1 .img+.dir) from folders/files and optionally "
                         "the entries of an existing archive (--base, filtered by --include/--remove). Never modifies "
                         "an existing IMG; output under <work>/out/rw/.",
    summary_ru="Новый IMG (VER2 или VER1 .img+.dir) из папок/файлов и записей другого архива; существующие IMG "
               "не меняются. Вывод — <work>/out/rw/.",
    mcp=False, group="formats",
    examples=("satk img build patch --out mymod.img", "satk img build --base models/gta3.img --include infernus.* --out infernus.img",
              "satk img build mydir --base models/gta_int.img --remove gen_int1.dff --out gta_int_mod.img"))
def img_build(src: list[str] | None, out: str | None = None, base: str | None = None,
              include: list[str] | None = None, remove: list[str] | None = None, version: Literal[1, 2] = 2,
              recursive: bool = False, overwrite: bool = False, profile: Profile = "vanilla") -> dict:
    """Build an IMG archive.

    Args:
        src: folders (their files) and files to put into the archive; with --base they replace entries of the same name.
        out: new archive: a name under <work>/out/rw/ or an absolute path (required).
        base: existing archive whose entries are copied first (read only).
        include: fnmatch patterns: copy only matching --base entries.
        remove: --base entry names (or patterns) to leave out.
        version: 2 = SA VER2 (one file), 1 = III/VC (.img + .dir).
        recursive: also take files from subfolders of src folders (names must stay unique).
        overwrite: replace an existing output file (never the base archive or game files).
        profile: profile whose game root resolves relative paths.
    """
    import fnmatch

    from ..formats.img import ImgArchive
    from ..formats.rw import FormatError
    from .img import ImgBuildError, Source, base_sources, build_img, dir_sources

    if not out:
        raise SatkError("BAD_PARAMS", "--out is required (the new archive)", hint="satk img build <folder> --out new.img")
    if not src and not base:
        raise SatkError("BAD_PARAMS", "nothing to build: give source folders/files and/or --base")
    target = _out_path(out, out, ".img")
    added: list[Source] = []
    for s in src or []:
        p = _find(s, profile)
        try:
            if p.is_dir():
                added += dir_sources(p, recursive=recursive)
            else:
                added.append(Source(p.name, p.stat().st_size, lambda p=p: p.read_bytes(), f"file:{p}"))
        except ImgBuildError as e:
            raise SatkError("BAD_PARAMS", str(e)) from None
    arc = None
    try:
        sources: list[Source] = []
        replaced = removed = copied = 0
        if base:
            bp = _find(base, profile, what="archive")
            if os.path.normcase(os.path.abspath(bp)) == os.path.normcase(os.path.abspath(target)):
                raise SatkError("BAD_PARAMS", "--out must be a new file, not the --base archive")
            try:
                arc = ImgArchive.open(bp)
            except FormatError as e:
                raise SatkError("UNSUPPORTED", f"cannot read the base archive: {e}") from None
            rem = [r.lower() for r in remove or []]
            new_names = {s.name.lower(): s for s in added}
            for s in base_sources(arc, include or ()):
                low = s.name.lower()
                if any(fnmatch.fnmatchcase(low, r) for r in rem):
                    removed += 1
                elif low in new_names:
                    sources.append(new_names.pop(low))
                    replaced += 1
                else:
                    sources.append(s)
                    copied += 1
            added = [s for s in added if s.name.lower() in new_names]
        sources += sorted(added, key=lambda s: (s.name.lower(), s.name))
        if not sources:
            raise SatkError("BAD_PARAMS", "the archive would be empty")
        if target.exists() and not overwrite:
            raise SatkError("EXISTS", f"{jpath(target)} exists", hint="pass --overwrite or choose another --out")
        try:
            res = build_img(target, sources, version=version)
        except ImgBuildError as e:
            raise SatkError("BAD_PARAMS", str(e)) from None
        except FormatError as e:
            raise SatkError("UNSUPPORTED", f"cannot read the base archive: {e}") from None
    finally:
        if arc is not None:
            arc.close()
    warn = res.pop("warn", [])
    env = obj(**{k: (jpath(v) if isinstance(v, Path) else v) for k, v in res.items()},
              added=len(added), copied=copied, replaced=replaced, removed=removed)
    if warn:
        env["warn"] = warn[:20] + ([f"... {len(warn) - 20} more"] if len(warn) > 20 else [])
    return env


@op("img.diff", summary="Compare two IMG archives by entry name and content: added, removed and changed entries "
                        "(trailing zero padding ignored).",
    summary_ru="Сравнить два IMG: добавленные, удалённые и изменённые записи.",
    mcp=False, group="formats",
    examples=("satk img diff models/gta3.img mymod.img", "satk img diff models/gta_int.img gta_int_mod.img --change changed"))
def img_diff(a: str, b: str, content: bool = True, change: Literal["added", "removed", "changed"] | None = None,
             limit: int = 20, cursor: str | None = None, profile: Profile = "vanilla") -> dict:
    """Diff two archives.

    Args:
        a: first archive (path rules as everywhere in satk rw).
        b: second archive.
        content: compare contents (false: sizes only, much faster).
        change: show only added, removed or changed rows.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
        profile: profile whose game root resolves relative paths.
    """
    from ..formats.img import ImgArchive
    from ..formats.rw import FormatError
    from .img import diff_img

    pa, pb = _find(a, profile, what="archive"), _find(b, profile, what="archive")
    try:
        with ImgArchive.open(pa) as xa, ImgArchive.open(pb) as xb:
            counts, rows = diff_img(xa, xb, content=content)
            va, vb, na, nb = xa.version, xb.version, len(xa.entries), len(xb.entries)
    except FormatError as e:
        raise SatkError("UNSUPPORTED", f"cannot read archive: {e}") from None
    if change:
        rows = [r for r in rows if r[1] == change]
    lim = clamp_limit(limit)
    try:
        start = int(cursor) if cursor else 0
    except ValueError:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}") from None
    page = rows[start:start + lim]
    env = table(["name", "change", "size_a", "size_b"], page, total=len(rows),
                next=str(start + lim) if start + lim < len(rows) else None)
    env.update(a=jpath(pa), b=jpath(pb), version_a=va, version_b=vb, entries_a=na, entries_b=nb, **counts)
    env["identical"] = counts["changed"] == counts["added"] == counts["removed"] == 0
    return env
