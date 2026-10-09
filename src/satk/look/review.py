"""Close-up review sheets and the light-leak report (contract K8), the satk side.

* :func:`subject_kind` - the kit kind of a preview subject: ``--kind``, the project's ``asset.json``, the index
  row of a game model (or of the ``--like`` model of a file), else a guess from the DFF frame names;
* :func:`region_sheets` - the cells of a region preview -> one JPEG per region (``preview-NNN-<region>.jpg``,
  every file of one run shares ``NNN``) and an index sheet (``preview-NNN-index.jpg``) with one cell per region;
* :func:`leak_report` - the Blender leak result -> the answer rows and ``checks/<stem>.leak.json`` of a package
  (one file per DFF: a building and its LOD in one folder each have their own).

``<stem>.leak.json`` (read by ``asset.check --strict``)::

    {"leak": 1, "subject", "kind", "dff"?, "dff_sha256"?, "clean": bool, "blocking": n,
     "counts": {"gap": n, "inside": n, "see_through": n}, "params": {...},
     "coverage": {"full": bool, "regions": [...], "missing": [...], "views"?: [...], "why"?: "..."},
     "views": [{"view", "region", "state", "gaps"}],
     "gaps": [{"id": "L1", "kind": "gap|inside|see_through", "region", "view", "views": [...], "px",
               "bbox_px": [x0, y0, x1, y1], "area_cm2", "point": [x, y, z], "near", "behind"?, "deep_m"?}],
     "info": [<the same, not blocking: the inside of a shell seen through an opening outside the kind's
               blocking views>], "sheet": path}

``gaps`` are defects (``clean`` = none); ``info`` is for a reviewer. ``coverage.full`` = the run used every leak
region of the kind with the default settings (no ``--views``, no ``--cull`` override, no smaller ``--size``):
``--strict`` accepts only a full run. Every run on a DFF also leaves a record in satk's own store
(``<work>/out/leak/records/<dff_sha256>.json``, :func:`write_record`) that ``--strict`` compares with the package
file, so an edited or hand-written file does not pass.

Stdlib only (Pillow is used by :mod:`.compose`).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, ensure_writable, jpath

__all__ = ["LEAK_FILE", "LEAK_DIR", "LEAK_SUFFIX", "DEFAULT_SIZE", "subject_kind", "guess_kind", "region_sheets",
           "leak_report", "write_leak", "leak_path", "write_record", "read_record", "record_path", "record_mismatch",
           "coverage", "run_number", "lod_sibling_of"]

#: The leak files of a package: ``<package>/checks/<stem>.leak.json`` (one per DFF).
LEAK_DIR = "checks"
LEAK_SUFFIX = ".leak.json"
#: The one shared file of satk before 2026-10 (``checks/leak.json``): read when it names the same DFF, never written.
LEAK_FILE = ("checks", "leak.json")
#: Pixels per camera of a full leak run (``look.leak --size`` default).
DEFAULT_SIZE = 320


def guess_kind(dff: bytes | None) -> str | None:
    """The kit kind of a DFF from its frame names and size (a file without a like model)."""
    if not dff:
        return None
    try:
        from ..formats.dff import scan_dff

        info = scan_dff(dff)
    except Exception:  # noqa: BLE001 - an unreadable file: no guess
        return None
    names = {str(f.name or "").lower() for f in info.frames}
    if {"pelvis", "head"} <= names or "root" in names and "spine" in names:
        return "ped"
    if "gunflash" in names:
        return "weapon"
    if any(n.startswith("bogie_") for n in names):
        return "train"
    if "moving_rotor" in names or "static_rotor" in names:
        return "heli"
    if "rudder" in names or "elevator_l" in names or "aileron_l" in names:
        return "plane"
    if "hookup" in names:
        return "trailer"
    if "chainset" in names:
        return "bmx"
    if "wheel_front" in names and "handlebars" in names:
        return "bike"
    if "wheel_lf_dummy" in names:
        return "quad" if "handlebars" in names else "automobile"
    if "boat_moving" in names or "boat_vlo" in names or ("moving_prop" in names and "chassis" not in names):
        return "boat"
    bb = info.bbox
    size = max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]) if bb else 0.0
    return "building" if size >= 10.0 else "prop"


def _sidecar_kind(dff) -> str | None:
    """The kind of the inventory a kit export wrote beside its DFF (``<stem>.inventory.json``, contract K6)."""
    if not dff:
        return None
    try:
        from ..inventory.api import locate
    except ImportError:
        return None
    side = locate(dff)
    if side is None:
        return None
    try:
        inv = (json.loads(side.read_text(encoding="utf-8")) or {}).get("inventory") or {}
    except (OSError, ValueError):
        return None
    k = str(inv.get("kind") or "")
    if not k:
        return None
    from . import regions as RG

    try:
        RG.load(k)
    except SatkError:
        return None
    return k


def subject_kind(entry, *, kind: str | None = None, asset: dict | None = None, like: str | None = None,
                 profile: str = "vanilla") -> tuple[str, str]:
    """``(kind, how)`` of a preview subject entry (``satk.look.inputs.Entry``)."""
    from . import regions as RG

    if kind:
        RG.load(kind)  # NOT_FOUND with the list
        try:
            from ..kit.kinds import canonical

            return canonical(kind), "--kind"
        except SatkError:
            return str(kind).lower(), "--kind"
    if asset and asset.get("kind"):
        return str(asset["kind"]), "asset.json"
    k = _sidecar_kind(getattr(entry, "path", None))
    if k:
        return k, "inventory sidecar"
    hd = lod_sibling_of(getattr(entry, "path", None))
    if hd:
        return "lod", f"the LOD of {hd} (its inventory sidecar)"
    from . import lineup as LU

    sid = entry.sid if entry.kind == "sid" else (like or entry.sid)
    if sid:
        try:
            k = RG.kind_of_row(LU.model_row(sid, profile), profile=profile)
        except SatkError:
            k = None
        if k:
            return k, f"index row of {sid}"
    k = guess_kind(entry.dff_bytes())
    if k:
        return k, "frame names"
    raise SatkError("BAD_PARAMS", f"cannot tell the asset kind of {entry.label}",
                    hint="give --kind (satk kit kinds lists them) or --like model:<id>")


def run_number(folder: Path, stem: str = "preview") -> int:
    """The next run number in ``folder`` (``<stem>-NNN*.jpg`` files)."""
    nums = []
    for f in folder.glob(f"{stem}-[0-9][0-9][0-9]*"):
        m = re.match(rf"{re.escape(stem)}-(\d{{3}})", f.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) if nums else 0) + 1


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(s).lower()).strip("_") or "region"


def region_sheets(res: dict, regions: list[dict], *, title: str, out_dir: Path, fmt: str = "jpg",
                  cell: int = 384) -> dict:
    """One sheet per region and an index; returns ``{"index", "regions": [[name, file, views, summary]], "n"}``."""
    from . import compose

    views = res.get("views") or []
    cells = res.get("cells") or []
    rows = res.get("rows") or []
    cols = res.get("cols") or []
    by_region: dict[str, list[int]] = {}
    for ci, v in enumerate(views):
        by_region.setdefault(str(v.get("region")), []).append(ci)
    summaries = {r["name"]: r.get("summary", "") for r in regions}
    n = run_number(out_dir)
    made: list[list] = []
    firsts: list[list] = []
    for reg in [r["name"] for r in regions]:
        cis = by_region.get(reg)
        if not cis:
            continue
        mine = [c for c in cells if int(c[1]) in cis]
        if not mine:
            continue
        used_rows = sorted({int(c[0]) for c in mine})
        rmap = {r: i for i, r in enumerate(used_rows)}
        cmap = {c: i for i, c in enumerate(cis)}
        sub = [[rmap[int(r)], cmap[int(c)], p] for r, c, p in mine]
        rlab = [rows[r] if r < len(rows) else str(r) for r in used_rows]
        clab = [cols[c] if c < len(cols) else str(c) for c in cis]
        w, h, grid = compose.layout(len(used_rows), len(cis), cell)
        path = out_dir / f"preview-{n:03d}-{_slug(reg)}.{fmt}"
        info = compose.sheet(sub, rlab, clab, title=f"{title} - {reg}: {summaries.get(reg, '')}"[:160], out=path,
                             fmt=fmt, grid=grid)
        made.append([reg, jpath(Path(info["path"])), len(cis), summaries.get(reg, "")])
        first = min(mine, key=lambda c: (int(c[0]), int(c[1])))
        firsts.append([len(firsts), 0, first[2], reg])
    if not made:
        raise SatkError("EXTERNAL_TOOL", "the region preview rendered no cells")
    # the index: one cell per region (its first view), labelled with the region name, wrapped in a grid
    k = len(firsts)
    gc = min(4, k)
    names = [f[3] for f in firsts]
    idx_cells = [[i, 0, f[2]] for i, f in enumerate(firsts)]
    idx = compose.sheet(idx_cells, names, [""], title=f"{title} - {k} regions", out=out_dir / f"preview-{n:03d}-index.{fmt}",
                        fmt=fmt, grid=gc)
    return {"index": jpath(Path(idx["path"])), "regions": made, "n": n}


def _counts(gaps: list[dict]) -> dict:
    out: dict = {}
    for g in gaps:
        out[g["kind"]] = out.get(g["kind"], 0) + 1
    return out


def leak_report(leak: dict, *, subject: str, kind: str, dff: str | None = None, sheet: str | None = None,
                cover: dict | None = None) -> dict:
    """The ``<stem>.leak.json`` document of a Blender leak result (``cover``: :func:`coverage` of the run)."""
    gaps = list(leak.get("gaps") or [])
    doc: dict = {"leak": 1, "subject": subject, "kind": kind}
    if dff:
        p = Path(dff)
        try:
            doc["dff"] = jpath(p)
            doc["dff_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            pass
    doc.update(clean=not gaps, blocking=len(gaps), counts=_counts(gaps), params=leak.get("params") or {},
               views=[{k: v.get(k) for k in ("view", "region", "state") if v.get(k)} | {"gaps": len(v.get("gaps") or [])}
                      for v in leak.get("views") or []],
               gaps=gaps)
    if cover is not None:
        doc["coverage"] = cover
    if leak.get("info"):
        doc["info"] = list(leak["info"])
    if sheet:
        doc["sheet"] = sheet
    return doc


def coverage(defn: dict, ran: list[str] | None, *, views: list[str] | None = None, cull=None,
             size: int = DEFAULT_SIZE) -> dict:
    """``{"full", "regions", "missing", "views"?, "why"?}``: did a leak run cover every leak region of the kind with
    the default settings? ``ran`` = the regions used (``None`` with plain ``views``)."""
    every = [str(r["name"]) for r in defn.get("regions") or [] if r.get("leak", True)]
    got = [str(r) for r in (ran or [])]
    missing = [r for r in every if r not in got]
    why = []
    if views:
        why.append(f"plain views ({', '.join(views)}) instead of the kind's regions")
    if missing:
        why.append(f"regions not checked: {', '.join(missing)}")
    if cull is not None:
        why.append(f"the back-face rule was overridden (cull={bool(cull)})")
    if int(size) < DEFAULT_SIZE:
        why.append(f"size {int(size)} is below the default {DEFAULT_SIZE}")
    out: dict = {"full": not why, "regions": got, "missing": missing}
    if views:
        out["views"] = list(views)
    if why:
        out["why"] = "; ".join(why)
    return out


def leak_path(package: str | Path, stem: str) -> Path:
    """``<package>/checks/<stem>.leak.json``."""
    return Path(package) / LEAK_DIR / f"{stem}{LEAK_SUFFIX}"


def _stem_of(doc: dict) -> str:
    for k in ("stem", "dff", "subject"):
        v = doc.get(k)
        if v:
            s = str(v)
            if s.lower().startswith("session:"):
                s = s.split(":", 1)[1]
            return re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(s.replace("\\", "/")).stem) or "model"
    return "model"


def write_leak(package: str | Path, doc: dict, stem: str | None = None) -> str:
    """Write ``<package>/checks/<stem>.leak.json`` (the package folder must exist and lie under the work directory;
    ``stem`` default: the checked DFF's)."""
    d = Path(package)
    if not d.is_dir():
        raise SatkError("NOT_FOUND", f"no package folder {jpath(d)}", hint="the folder of the exported DFF (Mod Loader folder)")
    target = ensure_writable(leak_path(d, stem or _stem_of(doc)))
    atomic_write(target, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return jpath(target)


def record_path(sha: str) -> Path:
    """satk's own record of a leak run on the DFF with this SHA-256 (outside every package)."""
    from ..core.paths import work

    if not re.fullmatch(r"[0-9a-f]{64}", str(sha or "")):
        raise SatkError("BAD_PARAMS", f"not a SHA-256: {sha!r}")
    return work("out", "leak", "records") / f"{sha}.json"


def _digest(doc: dict) -> dict:
    gaps = [g for g in doc.get("gaps") or [] if isinstance(g, dict)]
    cov = doc.get("coverage") if isinstance(doc.get("coverage"), dict) else {}
    return {"dff_sha256": doc.get("dff_sha256"), "kind": doc.get("kind"), "clean": bool(doc.get("clean")),
            "gaps": len(doc.get("gaps") or []), "areas": sorted(round(float(g.get("area_cm2") or 0.0), 1) for g in gaps),
            "full": bool(cov.get("full")), "regions": cov.get("regions")}


def write_record(doc: dict, job: str | None = None) -> str | None:
    """Keep satk's record of a leak run (only when the run had a DFF, ``dff_sha256``); returns its path."""
    sha = doc.get("dff_sha256")
    if not sha:
        return None
    import time as _t

    rec = dict(_digest(doc), subject=doc.get("subject"), time=_t.strftime("%Y-%m-%dT%H:%M:%S"), job=job)
    p = record_path(str(sha))
    atomic_write(ensure_writable(p), json.dumps(rec, ensure_ascii=False, indent=1) + "\n")
    return jpath(p)


def read_record(sha: str) -> dict | None:
    try:
        p = record_path(sha)
    except SatkError:
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record_mismatch(doc: dict) -> str | None:
    """Why a package leak file does not match satk's record of the run (``None``: it matches)."""
    sha = doc.get("dff_sha256")
    if not sha:
        return "it names no DFF hash"
    rec = read_record(str(sha))
    if rec is None:
        return "satk has no record of a look.leak run on this export"
    mine = _digest(doc)
    for k in ("kind", "clean", "gaps", "areas", "full"):
        if rec.get(k) != mine.get(k):
            return f"its {k} ({mine.get(k)!r}) differs from satk's record of that run ({rec.get(k)!r})"
    return None


def lod_sibling_of(dff) -> str | None:
    """The stem of the HD model whose kit export names ``dff`` as its LOD (``"lod"`` in a sidecar next to it)."""
    if not dff:
        return None
    p = Path(dff)
    stem = p.stem.lower()
    try:
        sides = list(p.parent.glob("*.inventory.json"))
    except OSError:
        return None
    for f in sides:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and str(doc.get("lod") or "").lower() == stem:
            return str(doc.get("stem") or f.name[: -len(".inventory.json")])
    return None
