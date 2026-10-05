"""Format self-test against the golden numbers of SPEC Appendix A (``satk formats selftest``).

Reads a game root READ-ONLY with the ``satk.formats`` parsers and compares counters with
:data:`GOLDEN`. Stdlib only: also runs from Blender's Python 3.13::

    python -c "import sys; sys.path.insert(0, r'<checkout>\\src'); from satk.formats import selftest; \\
               raise SystemExit(selftest.main(['--root', r'<workspace>\\gta-sa-clean', '--quick']))"

Stages: IMG directories, loose files, TXD headers + pixel hashes, DAT/IDE, text and binary IPL with LOD
links, DFF scan (all clumps, strip-expanded triangle counts), COL, IFP, zones, and the DFF->texture
reference check of report 14 §5.5. ``--quick`` skips the two stages that read most bytes (TXD ~750 MB,
DFF ~420 MB) and the texture-reference check; ``run(skip=...)`` skips any of :data:`STAGES`.
Optional: ``--geometry`` decodes every gta3/gta_int geometry (acceptance 4 of WP-02; from the DFF stage's
chunk walk when that stage runs), ``--dxt`` runs spike S2 (decoder parity of the three backends + speed).

Golden notes (see :data:`GOLDEN_NOTES`): Appendix A.1 lists ``atomic 46 108``; that number is an artefact
of the report-14 prototype, which added the frame index stored in each Light's Struct to the atomic count.
The real number of atomics (= Σ ``numAtomics`` = Atomic chunks) is 19 556.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core.paths import open_ro
from .col import iter_col
from .dat import canon_relpath, parse_dat, read_text, resolve_ci
from .dff import FLAG_NAMES, _decode_walked, _scan_walked, _walk_checked, decode_geometries
from .ide import MODEL_SECTIONS, parse_ide
from .ifp import parse_ifp
from .img import ImgArchive
from .ipl import parse_ipl_binary, parse_ipl_text, stream_base
from .layout import PROFILES, archives, loose_assets
from .rw import FormatError
from .txd import mip0_bytes, palette_bytes, parse_txd, texture_hash
from .zon import parse_zon

__all__ = ["GOLDEN", "GOLDEN_NOTES", "GEOMETRY_GOLDEN", "PENDING", "STAGES", "run", "main", "dxt_spike"]

#: SPEC Appendix A.1 (profile ``vanilla`` = ``gta-sa-clean``).
_VANILLA: dict[str, int] = {
    "img.archives": 8, "img.entries": 21058,
    "img.entries.gta3": 16316, "img.entries.gta_int": 2484, "img.entries.player": 542,
    "img.entries.cutscene": 634, "img.entries.anim": 133, "img.entries.cuts": 444,
    "img.entries.carrec": 426, "img.entries.script": 79,
    "img.gta3.dff": 12964, "img.gta3.txd": 2759, "img.gta3.col": 216, "img.gta3.ipl": 164,
    "img.gta3.ifp": 149, "img.gta3.dat": 64,
    "img.size_in_archive_nonzero": 0, "img.overlaps": 0, "img.out_of_bounds": 0, "img.missing": 0,
    "loose.txd": 63, "loose.dff": 22, "loose.col": 3, "loose.ifp": 1, "loose.total": 89, "blobs": 21147,
    "txd.files": 4046, "txd.errors": 0, "tex.total": 32878,
    "tex.src.gta3": 26004, "tex.src.gta_int": 5133, "tex.src.cutscene": 635, "tex.src.player": 394,
    "tex.src.loose": 712,
    "tex.fmt.DXT1": 29260, "tex.fmt.DXT3": 2265, "tex.fmt.X8R8G8B8": 1061, "tex.fmt.A8R8G8B8": 291,
    "tex.fmt.PAL8": 1, "tex.fmt.DXT5": 0, "tex.d3d8": 23,
    "tex.unique_names": 13757, "tex.unique_hashes": 14560,
    "ide.files": 57, "ide.missing": 0, "ide.errors": 0, "ide.defs": 14832,
    "ide.objs": 14045, "ide.tobj": 160, "ide.anim": 54, "ide.cars": 212, "ide.peds": 276, "ide.weap": 50,
    "ide.hier": 35, "ide.txdp": 23, "ide.2dfx": 97, "ide.max_id": 18630, "ide.dup_ids": 0,
    "ipl.text_files": 50, "ipl.missing": 0, "ipl.errors": 0, "ipl.text_inst": 9268,
    "ipl.bin_files": 190, "ipl.bin_inst": 41667, "ipl.inst": 50935,
    "ipl.lod_links": 6103, "ipl.lod_unresolved": 0, "ipl.bin_orphans": 5, "ipl.bin_orphan_inst": 166,
    # --- F2: DFF (A.1 "DFF" row; atomics corrected, see GOLDEN_NOTES; frames/geoms/materials: report 14 §4.1)
    "dff.files": 15356, "dff.src.gta3": 12964, "dff.src.gta_int": 1901, "dff.src.cutscene": 317,
    "dff.src.player": 152, "dff.src.loose": 22, "dff.clumps": 15660, "dff.atomics": 19556, "dff.rw_0x35000": 16,
    "dff.errors": 0, "dff.frames": 61082, "dff.geoms": 19556, "dff.materials": 78201,
    "geo.geometries": 18181, "geo.verts": 7897315, "geo.strip": 17843, "geo.tris": 5634843,
    # --- F2: COL, IFP (A.1), texture references (A.1 "unresolved DFF->texture", metric of report 14 §5.5)
    "col.files": 254, "col.models": 10169, "col.COL3": 9270, "col.COL2": 885, "col.COLL": 14, "col.COL4": 0,
    "col.errors": 0,
    "ifp.files": 436, "ifp.anims": 4484, "ifp.ANP3": 287, "ifp.ANPK": 149, "ifp.errors": 0,
    "texref.unresolved": 462,
}
#: SPEC Appendix A.2 (profile ``installed`` = the original install, read-only): "as A.1 except".
_INSTALLED: dict[str, int] = dict(
    _VANILLA,
    **{"loose.txd": 66, "loose.total": 92, "blobs": 21150, "txd.files": 4049, "tex.total": 32942,
       "tex.src.loose": 776, "tex.fmt.DXT5": 64, "tex.unique_names": 13784, "tex.unique_hashes": 14624},
)
GOLDEN: dict[str, dict[str, int]] = {"vanilla": _VANILLA, "installed": _INSTALLED}
#: Checked only with ``geometry=True`` (full decode of every gta3/gta_int geometry, A.1 "Геометрия").
GEOMETRY_GOLDEN: dict[str, int] = {"geo.decoded_geometries": 18181, "geo.decoded_verts": 7897315,
                                   "geo.decoded_tris": 5634843, "geo.decode_errors": 0}
#: Where a golden value deliberately differs from SPEC Appendix A, and why.
GOLDEN_NOTES: dict[str, str] = {
    "dff.atomics": "Appendix A.1 says 46 108: the report-14 prototype summed the u32 of EVERY Struct child of "
                   "a Clump, i.e. numAtomics plus the frame index of each Light's Struct (2 203 lights). "
                   "Real atomics = sum(numAtomics) = Atomic chunks = 19 556.",
}
#: Appendix A counters not checked yet (none: F2/F3 complete). Kept for API stability.
PENDING: tuple[str, ...] = ()
#: Optional stages (``run(skip=...)``) -> prefixes of the counters they produce. IMG directories, loose
#: files, DAT/IDE, IPL and zones always run (cheap; the others need them).
STAGES: dict[str, tuple[str, ...]] = {
    "txd": ("txd.", "tex."), "dff": ("dff.", "geo."), "col": ("col.",), "ifp": ("ifp.",), "texref": ("texref.",),
}
#: Stages skipped by ``quick=True``.
_QUICK_SKIP = ("txd", "dff", "texref")


#: Archives whose geometries ``geometry=True`` decodes (A.1 "Геометрия": gta3 + gta_int).
_GEO_SRC = ("gta3", "gta_int")
#: Pixel-hash worker threads and the number of TXDs that may wait for them.
_HASH_THREADS = max(1, min(4, (os.cpu_count() or 2) - 1))
_HASH_WINDOW = 32


def _hash_textures(buf, textures) -> list[bytes]:
    return [texture_hash(buf, x) for x in textures]


def _count_meshes(c: Counter, errors: list[str], label: str, decode) -> None:
    """Run ``decode()`` (-> list of ``Mesh``) and add its geometries/vertices/triangles to ``c``."""
    try:
        meshes = decode()
    except FormatError as ex:
        c["geo.decode_errors"] += 1
        errors.append(f"{label}: {ex}")
        return
    for m in meshes:
        c["geo.decoded_geometries"] += 1
        c["geo.decoded_verts"] += len(m.positions) // 3
        c["geo.decoded_tris"] += len(m.tris) // 3


def _stem(relpath: str) -> str:
    name = relpath.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0]


def run(root: str | os.PathLike, profile: str = "vanilla", *, quick: bool = False, bench: bool = False,
        dat_files: list[str] | None = None, img_order: str | None = None, geometry: bool = False,
        dxt: bool = False, dxt_sample_mpix: float = 64.0, skip: tuple[str, ...] | list[str] = ()) -> dict:
    """Compute the counters for ``root`` and compare them with ``GOLDEN[profile]``.

    ``skip``: names from :data:`STAGES` not to run (their golden counters are not checked); ``quick``
    adds ``txd``, ``dff``, ``texref``. ``geometry``/``dxt`` run independently of ``skip``.
    Returns ``{"ok", "profile", "root", "failed": [[key, got, want]], "checked", "counts",
    "pending", "skipped"?, "warn"?, "timings"?, "dxt"?}``. ``ok`` is false if a golden counter differs.
    """
    bad_stage = sorted(set(skip) - set(STAGES))
    if bad_stage:
        raise ValueError(f"unknown selftest stage(s) {bad_stage}; expected some of {sorted(STAGES)}")
    skipped = set(skip) | (set(_QUICK_SKIP) if quick else set())
    if skipped & {"txd", "dff"}:
        skipped.add("texref")                     # needs both
    t_all = time.perf_counter()
    root = Path(root)
    prof = PROFILES.get(profile, PROFILES["vanilla"])
    dat_files = list(dat_files or prof["dat"])
    img_order = img_order or prof["img_order"]
    c: Counter = Counter()
    timings: dict[str, float] = {}
    warn: list[str] = []
    errors: list[str] = []

    def lap(name: str, t0: float) -> None:
        timings[name] = round(time.perf_counter() - t0, 3)

    # ------------------------------------------------------------ IMG directories
    t0 = time.perf_counter()
    arcs = archives(root, dat_files, img_order)
    opened: list[tuple[str, str, ImgArchive]] = []  # (stem, ns, archive)
    for a in arcs:
        p = resolve_ci(root, a.relpath)
        if p is None:
            c["img.missing"] += 1
            errors.append(f"missing archive {a.relpath}")
            continue
        try:
            img = ImgArchive.open(p)
        except FormatError as e:
            c["img.errors"] += 1
            errors.append(f"{a.relpath}: {e}")
            continue
        stem = _stem(a.relpath)
        opened.append((stem, a.ns, img))
        c["img.archives"] += 1
        c["img.entries"] += len(img.entries)
        c[f"img.entries.{stem}"] += len(img.entries)
        prev_end = 0
        for e in sorted(img.entries, key=lambda e: e.offset_sectors):
            c[f"img.{stem}.{e.ext}"] += 1
            if e.archive_sectors:
                c["img.size_in_archive_nonzero"] += 1
            if e.abs_offset < prev_end:
                c["img.overlaps"] += 1
            prev_end = max(prev_end, e.abs_offset + e.size)
            if e.abs_offset + e.size > img.file_size:
                c["img.out_of_bounds"] += 1
    lap("img", t0)

    # ------------------------------------------------------------ loose files
    t0 = time.perf_counter()
    loose = loose_assets(root)
    for rel in loose:
        c[f"loose.{rel.rsplit('.', 1)[-1]}"] += 1
    c["loose.total"] = len(loose)
    c["blobs"] = c["img.entries"] + len(loose)
    lap("loose", t0)

    def blobs(ext: str):
        """``(src, name, data)`` of every blob with extension ``ext``: archives (offset order), then loose."""
        for stem, _ns, img in opened:
            for e in sorted((e for e in img.entries if e.ext == ext), key=lambda e: e.offset_sectors):
                try:
                    yield stem, e.name, img.read(e)
                except FormatError as ex:
                    errors.append(f"{stem}/{e.name}: {ex}")
                    yield stem, e.name, None
        for rel in loose:
            if rel.endswith("." + ext):
                with open_ro(resolve_ci(root, rel)) as f:
                    yield "loose", rel, f.read()

    # ------------------------------------------------------------ TXD
    tex_by_txd: dict[str, set[str]] = {}
    if "txd" not in skipped:
        t0 = time.perf_counter()
        names: set[str] = set()
        hashes: set[bytes] = set()
        nbytes = 0
        # blake2b releases the GIL: pixel hashes (~600 MB) are computed by worker threads while the main
        # thread reads and parses the next TXDs; at most _HASH_WINDOW TXD buffers are in flight.
        with ThreadPoolExecutor(max_workers=_HASH_THREADS, thread_name_prefix="satk-txd-hash") as pool:
            pending: deque = deque()
            for src, label, buf in blobs("txd"):
                c["txd.files"] += 1
                if buf is None:
                    c["txd.errors"] += 1
                    continue
                nbytes += len(buf)
                try:
                    t = parse_txd(buf)
                except FormatError as e:
                    c["txd.errors"] += 1
                    errors.append(f"{src}/{label}: {e}")
                    continue
                if not t.textures:
                    c["txd.empty"] += 1
                tex_by_txd.setdefault(_stem(label).lower(), {x.name.lower() for x in t.textures})
                for tex in t.textures:
                    c["tex.total"] += 1
                    c[f"tex.src.{src}"] += 1
                    c[f"tex.fmt.{tex.d3dfmt}"] += 1
                    if tex.platform == 8:
                        c["tex.d3d8"] += 1
                    if tex.unsupported:
                        c["tex.unsupported"] += 1
                    names.add(tex.name.lower())
                pending.append(pool.submit(_hash_textures, buf, t.textures))
                while len(pending) > _HASH_WINDOW:
                    hashes.update(pending.popleft().result())
            while pending:
                hashes.update(pending.popleft().result())
        c["tex.unique_names"] = len(names)
        c["tex.unique_hashes"] = len(hashes)
        for k in ("tex.fmt.DXT5", "txd.errors"):
            c[k] += 0
        lap("txd", t0)
        timings["txd_MB"] = round(nbytes / 1e6, 1)

    # ------------------------------------------------------------ DAT / IDE
    t0 = time.perf_counter()
    dat_lines = []
    for d in dat_files:
        p = resolve_ci(root, d)
        if p is None:
            raise FormatError("layout", 0, f"DAT file not found: {d}")
        dat_lines += [(canon_relpath(d), ln) for ln in parse_dat(read_text(p))]
    ids: dict[int, tuple[str, str, str | None]] = {}
    dup_ids = 0
    max_id = -1
    for _d, ln in dat_lines:
        if ln.key != "IDE":
            continue
        c["ide.files"] += 1
        p = resolve_ci(root, ln.path)
        if p is None:
            c["ide.missing"] += 1
            errors.append(f"missing IDE {ln.path}")
            continue
        errs: list = []
        defs, txdp, fx = parse_ide(read_text(p), errors=errs)
        c["ide.errors"] += len(errs)
        errors += [f"{ln.path}:{n}: {m}" for n, m in errs[:5]]
        for r in defs:
            c["ide.defs"] += 1
            c[f"ide.{r.sec}"] += 1
            if r.id in ids:
                dup_ids += 1
            ids[r.id] = (r.sec, r.name, r.txd)
            max_id = max(max_id, r.id)
        c["ide.txdp"] += len(txdp)
        c["ide.2dfx"] += len(fx)
    c["ide.max_id"] = max_id
    c["ide.dup_ids"] = dup_ids
    for k in MODEL_SECTIONS:
        c[f"ide.{k}"] += 0
    lap("ide", t0)

    # ------------------------------------------------------------ IPL
    t0 = time.perf_counter()
    text_inst: dict[str, int] = {}
    zon_files: list[str] = []
    for _d, ln in dat_lines:
        if ln.key != "IPL":
            continue
        if not ln.path.lower().endswith(".ipl"):
            c["ipl.zon_files"] += 1
            zon_files.append(ln.path)
            continue
        c["ipl.text_files"] += 1
        p = resolve_ci(root, ln.path)
        if p is None:
            c["ipl.missing"] += 1
            errors.append(f"missing IPL {ln.path}")
            continue
        errs = []
        insts, _items = parse_ipl_text(read_text(p), errors=errs)
        c["ipl.errors"] += len(errs)
        errors += [f"{ln.path}:{n}: {m}" for n, m in errs[:5]]
        c["ipl.text_inst"] += len(insts)
        text_inst[_stem(canon_relpath(ln.path))] = len(insts)
        for i in insts:
            if i.lod >= 0:
                c["ipl.lod_links"] += 1
                if i.lod >= len(insts):
                    c["ipl.lod_unresolved"] += 1
    for stem, ns, img in opened:
        if ns != "main":
            continue
        for e in img.entries:
            if e.ext != "ipl":
                continue
            try:
                insts, cars = parse_ipl_binary(img.read(e))
            except FormatError as ex:
                c["ipl.errors"] += 1
                errors.append(f"{stem}/{e.name}: {ex}")
                continue
            c["ipl.bin_files"] += 1
            c["ipl.bin_inst"] += len(insts)
            c["ipl.bin_cars"] += len(cars)
            parent = text_inst.get(stream_base(e.name) or "")
            if parent is None:
                c["ipl.bin_orphans"] += 1
                c["ipl.bin_orphan_inst"] += len(insts)
            for i in insts:
                if i.lod >= 0:
                    c["ipl.lod_links"] += 1
                    if parent is None or i.lod >= parent:
                        c["ipl.lod_unresolved"] += 1
    c["ipl.inst"] = c["ipl.text_inst"] + c["ipl.bin_inst"]
    for k in ("ipl.lod_unresolved", "ipl.bin_orphans", "ipl.bin_orphan_inst", "ipl.errors", "ipl.missing",
              "ide.missing", "ide.errors", "ide.dup_ids", "img.missing", "img.overlaps", "img.out_of_bounds",
              "img.size_in_archive_nonzero"):
        c[k] += 0
    lap("ipl", t0)

    # ------------------------------------------------------------ DFF (scan: all clumps, no full geometry)
    # With ``geometry``, the gta3/gta_int geometries are decoded here from the same chunk walk; the
    # ``geometry`` timing is then the decode alone (the walk is counted in ``dff``).
    dff_tex: dict[str, set[str]] = {}
    t_geo = 0.0
    if "dff" not in skipped:
        t0 = time.perf_counter()
        nbytes = 0
        for src, label, buf in blobs("dff"):
            c["dff.files"] += 1
            c[f"dff.src.{src}"] += 1
            decode_here = geometry and src in _GEO_SRC
            if buf is None:
                c["dff.errors"] += 1
                if decode_here:
                    c["geo.decode_errors"] += 1
                continue
            nbytes += len(buf)
            try:
                walked = _walk_checked(buf)
                info = _scan_walked(buf, walked)
            except FormatError as e:
                c["dff.errors"] += 1
                if decode_here:
                    c["geo.decode_errors"] += 1
                errors.append(f"{src}/{label}: {e}")
                continue
            if decode_here:
                t1 = time.perf_counter()
                _count_meshes(c, errors, f"{src}/{label}", lambda: _decode_walked(buf, walked))
                t_geo += time.perf_counter() - t1
            c["dff.clumps"] += info.clumps
            c["dff.atomics"] += info.atomics
            c["dff.frames"] += len(info.frames)
            c["dff.geoms"] += len(info.geoms)
            c["dff.materials"] += len(info.materials)
            c["dff.texrefs"] += sum(1 for m in info.materials if m.texture)
            c["dff.effects"] += len(info.effects)
            c[f"dff.rw_0x{info.rw_version:X}"] += 1
            for bit, nm in FLAG_NAMES.items():
                if info.flags & bit:
                    c[f"dff.flag.{nm}"] += 1
            if src in _GEO_SRC:
                dff_tex.setdefault(_stem(label).lower(), {m.texture.lower() for m in info.materials if m.texture})
                c["geo.geometries"] += len(info.geoms)
                c["geo.verts"] += info.verts
                c["geo.tris"] += info.tris
                c["geo.strip"] += sum(1 for g in info.geoms if g.strip)
        for k in ("dff.errors", "dff.rw_0x35000"):
            c[k] += 0
        lap("dff", t0)
        timings["dff"] = round(timings["dff"] - t_geo, 3)
        timings["dff_MB"] = round(nbytes / 1e6, 1)

    # ------------------------------------------------------------ COL
    t0 = time.perf_counter()
    for src, label, buf in (blobs("col") if "col" not in skipped else ()):
        c["col.files"] += 1
        if buf is None:
            c["col.errors"] += 1
            continue
        body: list = []
        try:
            for m in iter_col(buf, errors=body):
                c["col.models"] += 1
                c[f"col.{('COLL', 'COL2', 'COL3', 'COL4')[m.version - 1]}"] += 1
                c["col.faces"] += m.faces
                c["col.shadow_faces"] += m.shadow_faces
        except FormatError as e:
            c["col.errors"] += 1
            errors.append(f"{src}/{label}: {e}")
        c["col.body_errors"] += len(body)
    if "col" not in skipped:
        for k in ("col.errors", "col.COL4", "col.body_errors"):
            c[k] += 0
        lap("col", t0)

    # ------------------------------------------------------------ IFP
    t0 = time.perf_counter()
    for src, label, buf in (blobs("ifp") if "ifp" not in skipped else ()):
        c["ifp.files"] += 1
        if buf is None:
            c["ifp.errors"] += 1
            continue
        try:
            fmt, _pack, anims = parse_ifp(buf)
        except FormatError as e:
            c["ifp.errors"] += 1
            errors.append(f"{src}/{label}: {e}")
            continue
        c[f"ifp.{fmt}"] += 1
        c["ifp.anims"] += len(anims)
    if "ifp" not in skipped:
        c["ifp.errors"] += 0
        lap("ifp", t0)

    # ------------------------------------------------------------ zones
    t0 = time.perf_counter()
    for z in zon_files:
        p = resolve_ci(root, z)
        if p is None:
            errors.append(f"missing zone file {z}")
            continue
        errs = []
        c["zon.zones"] += len(parse_zon(read_text(p), errors=errs))
        c["zon.errors"] += len(errs)
    lap("zon", t0)

    # ------------------------------------------------------------ DFF -> texture references (report 14 §5.5)
    if "texref" not in skipped:
        t0 = time.perf_counter()
        vehicle = tex_by_txd.get("vehicle", set())
        for sec, name, txd in ids.values():
            refs = dff_tex.get(name.lower())
            if refs is None:
                continue
            have = tex_by_txd.get((txd or "").lower(), set())
            if sec == "cars":
                have = have | vehicle
            bad = len(refs - have)
            c["texref.resolved"] += len(refs) - bad
            c["texref.unresolved"] += bad
            c["texref.models_missing"] += bool(bad)
        lap("texref", t0)

    # ------------------------------------------------------------ full geometry decode (optional)
    if geometry:
        t0 = time.perf_counter()
        if "dff" in skipped:                      # otherwise already decoded in the DFF stage
            for stem, _ns, img in opened:
                if stem not in _GEO_SRC:
                    continue
                for e in sorted((e for e in img.entries if e.ext == "dff"), key=lambda e: e.offset_sectors):
                    _count_meshes(c, errors, f"{stem}/{e.name}", lambda: decode_geometries(img.read(e)))
        c["geo.decode_errors"] += 0
        lap("geometry", t0)
        timings["geometry"] = round(timings["geometry"] + t_geo, 3)

    dxt_res = None
    if dxt:
        t0 = time.perf_counter()
        dxt_res = dxt_spike(root, opened, loose, sample_mpix=dxt_sample_mpix)
        lap("dxt", t0)

    for _stem_, _ns, img in opened:
        img.close()

    # ------------------------------------------------------------ compare
    golden = GOLDEN.get(profile)
    failed: list[list] = []
    checked = 0
    if golden is None:
        warn.append(f"no golden numbers for profile {profile!r}: counters only")
    else:
        skip_prefixes = tuple(pfx for st in skipped for pfx in STAGES[st])
        want_all = dict(golden, **(GEOMETRY_GOLDEN if geometry else {}))
        for k, want in want_all.items():
            if k.startswith(skip_prefixes) and k not in GEOMETRY_GOLDEN:
                continue
            checked += 1
            got = c.get(k, 0)
            if got != want:
                failed.append([k, got, want])
    if dxt_res is not None:
        for key, r in dxt_res["parity"].items():
            checked += 1
            if not r.get("ok"):
                failed.append([f"dxt.parity.{key}", r.get("max_diff"), 1])
    timings["total"] = round(time.perf_counter() - t_all, 3)
    out: dict = {
        "ok": not failed,
        "profile": profile,
        "root": str(root).replace("\\", "/"),
        "img_order": img_order,
        "quick": quick,
        "checked": checked,
        "failed": failed,
        "elapsed_s": timings["total"],
        "counts": dict(sorted(c.items())),
        "pending": list(PENDING),
        "notes": GOLDEN_NOTES,
    }
    if skipped:
        out["skipped"] = sorted(skipped)
    if errors:
        out["errors"] = errors[:20]
    if warn:
        out["warn"] = warn
    if bench or geometry or dxt:
        out["timings"] = timings
    if dxt_res is not None:
        out["dxt"] = dxt_res
    return out


# ============================================================================ spike S2 (DXT backends)
#: The S2 parity set (SPEC §5.3 WP-02 acceptance 5). ``bistro/sw_wallbrick_01`` is named there as DXT1/565
#: but is X8R8G8B8 in the game; ``vgnpwrmainbld/sw_wallbrick_01`` is the DXT1/565 texture of that name.
S2_FIXED = {"dxt1_565": ("vgnpwrmainbld", "sw_wallbrick_01"), "bistro_sw_wallbrick_01": ("bistro", "sw_wallbrick_01"),
            "x8r8g8b8": ("bistro", "vent_64"), "pal8": ("outro", "outro")}
S2_FIRST = {"dxt1_1555": lambda t: t.d3dfmt == "DXT1" and (t.raster_fmt & 0xF00) == 0x100,
            "dxt3": lambda t: t.d3dfmt == "DXT3", "a8r8g8b8": lambda t: t.d3dfmt == "A8R8G8B8"}


def dxt_spike(root: Path, opened: list, loose: list[str], *, sample_mpix: float = 64.0) -> dict:
    """Spike S2: decode the parity set with every available backend and measure speed (Mpix/s).

    Parity: max per-channel difference to the ``python`` reference must be <= 1; DXT1+565 must be all A=255.
    Speed: DXT textures of the first archive in directory order until ``sample_mpix`` (python: 1/32 of it).
    """
    from .dxt import available_backends, decode_rgba, mean_rgba, preview_rgba

    picks: dict[str, tuple] = {}
    want_txd = {txd for txd, _t in S2_FIXED.values()}
    seen_txd: set[str] = set()
    for stem, ns, img in opened:                       # "index order": archive order, directory order
        if ns != "main":
            continue
        for e in img.entries:
            if e.ext != "txd":
                continue
            key = e.stem.lower()
            need_first = any(k not in picks for k in S2_FIRST)
            if not need_first and not (key in want_txd and key not in seen_txd):
                continue
            buf = img.read(e)
            t = parse_txd(buf)
            if key in want_txd and key not in seen_txd:
                seen_txd.add(key)
                for k, (txd, tex) in S2_FIXED.items():
                    if txd == key:
                        for x in t.textures:
                            if x.name.lower() == tex:
                                picks[k] = (f"{stem}.img/{e.name}", x, buf)
            for k, f in S2_FIRST.items():
                if k not in picks:
                    for x in t.textures:
                        if f(x):
                            picks[k] = (f"{stem}.img/{e.name}", x, buf)
                            break
    for rel in loose:                                  # outro.txd lives in models/txd/
        key = _stem(rel).lower()
        if rel.endswith(".txd") and key in want_txd and key not in seen_txd:
            seen_txd.add(key)
            with open_ro(resolve_ci(root, rel)) as f:
                buf = f.read()
            for k, (txd, tex) in S2_FIXED.items():
                if txd == key:
                    for x in parse_txd(buf).textures:
                        if x.name.lower() == tex:
                            picks[k] = (rel, x, buf)
    backends = [b for b in available_backends()]
    parity: dict[str, dict] = {}
    for k in list(S2_FIXED) + list(S2_FIRST):
        if k not in picks:
            parity[k] = {"ok": False, "error": "texture not found"}
            continue
        src, t, buf = picks[k]
        m0, pal = mip0_bytes(buf, t), palette_bytes(buf, t)
        ref = decode_rgba(t, m0, pal, backend="python")
        diffs = {}
        for b in backends:
            out = decode_rgba(t, m0, pal, backend=b)
            diffs[b] = max(map(abs, map(int.__sub__, ref, out))) if len(out) == len(ref) else 999
        mx = max(diffs.values())
        r = {"ok": mx <= 1 and len(ref) == t.w * t.h * 4, "src": src, "tex": t.name, "fmt": t.d3dfmt,
             "raster": f"0x{t.raster_fmt:X}", "w": t.w, "h": t.h, "alpha": t.alpha, "max_diff": mx, "diff": diffs,
             "mean_rgba": f"0x{mean_rgba(t, m0, pal):08x}"}
        if t.d3dfmt == "DXT1" and (t.raster_fmt & 0xF00) == 0x200:
            r["all_a255"] = all(v == 255 for v in ref[3::4])
            r["ok"] = r["ok"] and r["all_a255"]
        parity[k] = r
    # speed sample: DXT textures of the first main archive in directory order
    sample: list = []
    mpix = 0.0
    for _stem_, ns, img in opened[:1]:
        for e in img.entries:
            if e.ext != "txd" or mpix >= sample_mpix:
                continue
            buf = img.read(e)
            for x in parse_txd(buf).textures:
                if x.d3dfmt in ("DXT1", "DXT3", "DXT5"):
                    sample.append((x, mip0_bytes(buf, x)))
                    mpix += x.w * x.h / 1e6
    speed: dict[str, float] = {}
    for b in backends:
        sub = sample if b != "python" else sample[:max(1, len(sample) // 32)]
        mp = sum(x.w * x.h for x, _m in sub) / 1e6
        t0 = time.perf_counter()
        for x, m in sub:
            decode_rgba(x, m, backend=b)
        speed[b] = round(mp / max(time.perf_counter() - t0, 1e-9), 1)
    t0 = time.perf_counter()
    for x, m in sample:
        preview_rgba(x, m)
    speed["preview_stdlib"] = round(mpix / max(time.perf_counter() - t0, 1e-9), 1)
    t0 = time.perf_counter()
    for x, m in sample:
        mean_rgba(x, m)
    speed["mean_stdlib"] = round(mpix / max(time.perf_counter() - t0, 1e-9), 1)
    return {"backends": backends, "parity": parity, "mpix_per_s": speed,
            "sample": {"textures": len(sample), "mpix": round(mpix, 1)}}


def _default_root(profile: str) -> Path:
    from ..core.paths import profile_root  # stdlib-only; reads satk.toml if present

    return profile_root(profile if profile in PROFILES else "vanilla")


def main(argv: list[str] | None = None) -> int:
    """CLI entry usable without the satk CLI (e.g. from Blender). Prints JSON, returns 0/1."""
    ap = argparse.ArgumentParser(prog="satk.formats.selftest", description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", help="game root (default: the profile root from satk.toml)")
    ap.add_argument("--profile", default="vanilla", help="vanilla|installed|samp (default vanilla)")
    ap.add_argument("--bench", action="store_true", help="include per-stage timings")
    ap.add_argument("--quick", action="store_true", help="skip the TXD and DFF stages")
    ap.add_argument("--geometry", action="store_true", help="also decode every gta3/gta_int geometry")
    ap.add_argument("--dxt", action="store_true", help="also run spike S2 (DXT backend parity + speed)")
    ap.add_argument("--json", action="store_true", help="accepted for symmetry; output is always JSON")
    a = ap.parse_args(argv)
    root = Path(a.root) if a.root else _default_root(a.profile)
    try:
        res = run(root, a.profile, quick=a.quick, bench=a.bench, geometry=a.geometry, dxt=a.dxt)
    except FormatError as e:
        res = {"ok": False, "error": {"code": "UNSUPPORTED", "msg": str(e)}}
    sys.stdout.write(json.dumps(res, separators=(",", ":"), ensure_ascii=False) + "\n")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
