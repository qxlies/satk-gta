"""``compile`` (round trip and node edits) and ``export`` (JSON overlay) of a path network. Stdlib only.

Compile: the imported region bytes go through the vendored gta-flow pipeline exactly as upstream
uses it - ``document.import_files`` -> (optional ``document.apply`` of node edits) ->
``compiler.compile_document`` -> independent ``oracle.check`` - and every compiled region is
compared with its source bytes. An unchanged network must come back byte-identical (64 of 64 for
vanilla, sector padding included). Output: ``work/out/paths/<profile>/<name>/`` with
``manifest.json`` and the ``nodes<N>.dat`` files selected by ``write``; such files replace the
``gta3.img`` entries of the same name (for example as loose files of a modloader mod).

Export: nodes inside a 2D box plus every road segment touching them, with lane counts from the
navis, as one JSON file ``work/out/paths/<profile>/<name>.json`` (format ``satk.paths.overlay/1``,
exact coordinates, sorted, deterministic) for viewers, Blender scripts or plots.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
import time
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, ensure_removable, ensure_writable, jpath, tmp, work
from . import records as R
from .db import PathsDB, open_paths

__all__ = ["KIND_FILTERS", "OVERLAY_FORMAT", "COMPILE_FORMAT", "parse_edits", "compile_network",
           "export_overlay", "segment_lanes"]

#: ``kind`` parameter values -> node kinds.
KIND_FILTERS: dict[str, tuple[str, ...] | None] = {
    "all": None, "vehicle": ("car", "boat"), "car": ("car",), "boat": ("boat",), "ped": ("ped",)}
OVERLAY_FORMAT = "satk.paths.overlay/1"
COMPILE_FORMAT = "satk.paths.compile/1"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
_EDIT_KEYS = {"pos", "width", "spawn", "behaviour"}


def _check_name(name: str) -> str:
    if not _NAME_RE.match(str(name)) or str(name).endswith("."):
        raise SatkError("BAD_PARAMS", f"bad output name {name!r} (letters, digits, '_', '-', '.'; max 64)")
    return str(name)


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise SatkError("BAD_PARAMS", f"{what}: expected a finite number, got {v!r}")
    return float(v)


def _nibble(v, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 15:
        raise SatkError("BAD_PARAMS", f"{what}: expected an integer 0..15, got {v!r}")
    return v


def parse_edits(edits: dict | None) -> dict[tuple[int, int], dict]:
    """``{"15:6": [x, y, z] | {"pos": [x, y(, z)], "width": w, "spawn": 0-15, "behaviour": 0-15}}``
    -> ``{(15, 6): {"pos": [...]|None, ...}}`` (validated; ``BAD_PARAMS`` otherwise)."""
    out: dict[tuple[int, int], dict] = {}
    for key, val in (edits or {}).items():
        try:
            a, i = R.parse_addr(key)
        except ValueError as e:
            raise SatkError("BAD_PARAMS", f"edits: {e}") from None
        if isinstance(val, list):
            val = {"pos": val}
        if not isinstance(val, dict) or not val:
            raise SatkError("BAD_PARAMS", f"edits[{key}]: expected [x, y, z] or an object with "
                                          f"{', '.join(sorted(_EDIT_KEYS))}")
        unknown = sorted(set(val) - _EDIT_KEYS)
        if unknown:
            raise SatkError("BAD_PARAMS", f"edits[{key}]: unknown field(s) {', '.join(unknown)}",
                            data={"fields": sorted(_EDIT_KEYS)})
        e: dict = {}
        if "pos" in val:
            p = val["pos"]
            if not isinstance(p, list) or len(p) not in (2, 3):
                raise SatkError("BAD_PARAMS", f"edits[{key}].pos: expected [x, y] or [x, y, z]")
            e["pos"] = [_num(c, f"edits[{key}].pos") for c in p]
        if "width" in val:
            w = _num(val["width"], f"edits[{key}].width")
            if not 0 <= w <= 255 / 16:
                raise SatkError("BAD_PARAMS", f"edits[{key}].width: expected 0..15.94, got {w}")
            e["width"] = w
        if "spawn" in val:
            e["spawn"] = _nibble(val["spawn"], f"edits[{key}].spawn")
        if "behaviour" in val:
            e["behaviour"] = _nibble(val["behaviour"], f"edits[{key}].behaviour")
        out[(a, i)] = e
    return out


def _gf_document(blobs: dict[int, bytes]):
    """gta-flow document of the regions (``import_files`` needs files: a private temp dir)."""
    from .vendor import gtaflow

    gf = gtaflow()
    d = Path(tempfile.mkdtemp(prefix="compile-", dir=ensure_writable(tmp("paths"))))
    try:
        files = []
        for a, b in sorted(blobs.items()):
            p = ensure_writable(d / f"nodes{a}.dat")
            p.write_bytes(b)
            files.append(p)
        return gf.document.import_files(files)
    finally:
        shutil.rmtree(ensure_removable(d), ignore_errors=False)


def _apply_edits(doc: dict, edits: dict[tuple[int, int], dict]) -> tuple[dict, list[dict]]:
    from .vendor import gtaflow

    gf = gtaflow()
    by_origin = {tuple(n["origin"]): n for n in doc["nodes"]}
    ops: list[dict] = []
    report: list[dict] = []
    for (a, i), e in sorted(edits.items()):
        n = by_origin.get((a, i))
        if n is None:
            raise SatkError("NOT_FOUND", f"edits: no path node {a}:{i}", hint="satk paths near <x> <y>")
        changes: dict = {}
        item: dict = {"node": R.addr(a, i)}
        if "pos" in e:
            p = e["pos"]
            changes["position"] = [p[0], p[1], p[2] if len(p) == 3 else n["position"][2]]
            item.update({"from": n["position"], "pos": changes["position"]})
        if "width" in e:
            changes["width"] = item["width"] = e["width"]
        if "spawn" in e:
            changes["spawn_probability"] = item["spawn"] = e["spawn"]
        if "behaviour" in e:
            changes["behaviour"] = item["behaviour"] = e["behaviour"]
        ops.append({"op": "update", "collection": "nodes", "id": n["id"], "changes": changes})
        report.append(item)
    if not ops:
        return doc, []
    try:
        doc = gf.document.apply(doc, {"base_revision": gf.document.fingerprint(doc), "operations": ops})
    except gf.codec.TrafficError as e:
        raise SatkError("BAD_PARAMS", f"edits rejected by gta-flow: {e}") from None
    return doc, report


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def compile_network(profile: str = "vanilla", edits: dict | None = None, write: str = "changed",
                    name: str = "compiled") -> dict:
    """Recompile the imported network of ``profile`` (see the module docstring).

    Args:
        edits: node edits (:func:`parse_edits`); ``None`` = pure round trip.
        write: ``changed`` (regions that differ from the source), ``all`` or ``none``.
        name: output directory ``work/out/paths/<profile>/<name>/``.

    Returns a dict: ``areas``, ``identical``, ``changed`` (regions), ``oracle``, ``out``, ``files``,
    ``edited``, ``seconds`` (+ ``warn``).
    """
    from .vendor import gtaflow

    t0 = time.perf_counter()
    if write not in ("changed", "all", "none"):
        raise SatkError("BAD_PARAMS", f"write: {write!r} is not one of changed, all, none")
    _check_name(name)
    wanted = parse_edits(edits)
    db, warns = open_paths(profile)
    with db:
        blobs = db.area_blobs()
        prof = db.meta.get("profile", profile)
    gf = gtaflow()
    doc = _gf_document(blobs)
    doc, edited = _apply_edits(doc, wanted)
    try:
        outputs, manifest = gf.compiler.compile_document(doc)
    except (gf.codec.TrafficError, KeyError, TypeError, OverflowError) as e:
        code = "BAD_PARAMS" if wanted else "UNSUPPORTED"
        raise SatkError(code, f"gta-flow cannot compile the network: {type(e).__name__}: {e}") from None
    oracle = gf.oracle.check(outputs)
    if not oracle["valid"]:
        warns.append(f"ORACLE: {len(oracle['errors'])} topology error(s), first: {oracle['errors'][0]}")
    regions = []
    for a in sorted(set(outputs) | set(blobs)):
        b = outputs.get(a)
        src = blobs.get(a)
        status = "missing" if b is None else "new" if src is None else "identical" if b == src else "changed"
        regions.append({"area": a, "status": status, "bytes": len(b) if b is not None else None,
                        "sha256": _sha(b) if b is not None else None,
                        "source_sha256": _sha(src) if src is not None else None})
    changed = [r["area"] for r in regions if r["status"] != "identical"]
    out_dir = work("out", "paths", prof, name)
    for old in sorted([*out_dir.glob("nodes*.dat"), out_dir / "manifest.json"]):
        if old.is_file():
            ensure_removable(old).unlink()
    files = []
    for r in regions:
        a = r["area"]
        if outputs.get(a) is None or write == "none" or (write == "changed" and r["status"] == "identical"):
            continue
        p = atomic_write(out_dir / f"nodes{a}.dat", outputs[a])
        r["file"] = p.name
        files.append(p.name)
    man = {"format": COMPILE_FORMAT, "profile": prof, "network_sha256": db.meta.get("network_sha256"),
           "edits": {R.addr(*k): v for k, v in sorted(wanted.items())}, "write": write,
           "identical": sum(r["status"] == "identical" for r in regions), "changed": changed,
           "oracle": {"valid": oracle["valid"], "errors": oracle["errors"][:50],
                      "warnings": oracle["warnings"][:50], "scope": oracle["scope"]},
           "gtaflow_changed_regions": manifest.get("changed_regions", []),
           "regions": regions}
    atomic_write(out_dir / "manifest.json", json.dumps(man, indent=1, sort_keys=True) + "\n")
    res = {"profile": prof, "areas": len(regions), "identical": man["identical"], "changed": changed,
           "byte_identical": not changed, "edited": edited,
           "oracle": f"{'valid' if oracle['valid'] else 'INVALID'}: {len(oracle['errors'])} errors, "
                     f"{len(oracle['warnings'])} warnings",
           "out": jpath(out_dir), "written": len(files), "files": files if len(files) <= 8 else None,
           "seconds": round(time.perf_counter() - t0, 2)}
    if warns:
        res["warn"] = warns
    return res


# --------------------------------------------------------------------------- export


def segment_lanes(navi, a: tuple[int, int], b: tuple[int, int]) -> list[int] | None:
    """Lanes for travel ``a -> b`` and ``b -> a`` of a navi row (``None`` for exceptional navis).

    The low three bits of the lanes byte count the lanes toward the attached node.
    """
    if navi is None:
        return None
    attached = (navi["at_area"], navi["at_idx"])
    if attached == b:
        return [navi["lanes_to"], navi["lanes_away"]]
    if attached == a:
        return [navi["lanes_away"], navi["lanes_to"]]
    return None


def _node_json(r) -> dict:
    d = {"id": R.addr(r["area"], r["idx"]), "kind": r["kind"], "pos": [r["x"], r["y"], r["z"]],
         "width": r["width"], "flood": r["flood"]}
    if r["spawn"]:
        d["spawn"] = r["spawn"]
    if r["behaviour"]:
        d["behaviour"] = r["behaviour"]
    flags = R.flag_names(r["flags"])
    if flags:
        d["flags"] = flags
    return d


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def export_overlay(profile: str = "vanilla", box: list[float] | None = None, kind: str = "all",
                   name: str | None = None, navis: bool = True) -> dict:
    """Write the JSON overlay of the nodes in ``box`` (``[minx, miny, maxx, maxy]``; ``None`` = whole map).

    Returns a dict: ``path``, ``nodes``, ``segments``, ``navis``, ``bytes`` (+ ``warn``).
    """
    if kind not in KIND_FILTERS:
        raise SatkError("BAD_PARAMS", f"kind: {kind!r} is not one of {', '.join(KIND_FILTERS)}")
    if box is not None:
        if len(box) != 4 or not all(math.isfinite(v) for v in box):
            raise SatkError("BAD_PARAMS", "area: expected four numbers minx,miny,maxx,maxy",
                            hint="satk paths export --area 2400,-1750,2600,-1600")
        minx, miny, maxx, maxy = (float(v) for v in box)
        if minx > maxx or miny > maxy:
            raise SatkError("BAD_PARAMS", f"area: min > max in {box}", hint="order: minx,miny,maxx,maxy")
    else:
        minx = miny = -1e9
        maxx = maxy = 1e9
    if name is None:
        name = f"{kind}_" + ("_".join(_fmt(v) for v in (minx, miny, maxx, maxy)) if box is not None else "map")
    _check_name(name)
    db, warns = open_paths(profile)
    with db:
        prof = db.meta.get("profile", profile)
        kinds = KIND_FILTERS[kind]
        inside = {(r["area"], r["idx"]): r for r in db.nodes_in_box(minx, miny, maxx, maxy, kinds)}
        segs = _segments(db, inside, kinds)
        navi_rows = {}
        if navis:
            for s in segs:
                key = s.pop("_navi", None)
                if key is not None and key not in navi_rows:
                    nv = db.navi(*key)
                    if nv is not None:
                        navi_rows[key] = nv
        for s in segs:
            s.pop("_navi", None)
    doc = {
        "format": OVERLAY_FORMAT, "profile": prof, "box": [minx, miny, maxx, maxy] if box is not None else None,
        "kind": kind, "network_sha256": db.meta.get("network_sha256"),
        "counts": {"nodes": len(inside), "segments": len(segs), "navis": len(navi_rows)},
        "nodes": [_node_json(inside[k]) for k in sorted(inside)],
        "segments": segs,
        "navis": [{"id": R.addr(*k), "pos": [v["x"], v["y"]], "dir": [v["dx"], v["dy"]],
                   "attached": R.addr(v["at_area"], v["at_idx"]), "lanes_to": v["lanes_to"],
                   "lanes_away": v["lanes_away"], "width": v["width"],
                   **({"light": v["light"]} if v["light"] else {})}
                  for k, v in sorted(navi_rows.items())],
    }
    text = json.dumps(doc, separators=(",", ":"), sort_keys=False) + "\n"
    path = atomic_write(work("out", "paths", prof, f"{name}.json"), text)
    res = {"path": jpath(path), "nodes": len(inside), "segments": len(segs), "navis": len(navi_rows),
           "bytes": len(text.encode("utf-8"))}
    if warns:
        res["warn"] = warns
    return res


def _segments(db: PathsDB, inside: dict, kinds) -> list[dict]:
    """Undirected segments with at least one endpoint in ``inside`` (both endpoints of an allowed kind)."""
    areas = sorted({a for a, _ in inside})
    by_node: dict[tuple[int, int], list] = {}
    for a in areas:
        cur = db.conn.execute("SELECT * FROM link WHERE area = ? ORDER BY k", (a,))
        cols = [c[0] for c in cur.description]
        for row in cur:
            r = dict(zip(cols, row))
            if (a, r["node"]) in inside:
                by_node.setdefault((a, r["node"]), []).append(r)
    outside: dict[tuple[int, int], object] = {}
    seen: set[tuple] = set()
    segs: list[dict] = []
    for key in sorted(by_node):
        na = inside[key]
        for ln in by_node[key]:
            tk = (ln["to_area"], ln["to_idx"])
            pair = (key, tk) if key <= tk else (tk, key)
            if pair in seen:
                continue
            seen.add(pair)
            nb = inside.get(tk)
            if nb is None:
                if tk not in outside:
                    outside[tk] = db.node(*tk)
                nb = outside[tk]
            if nb is None or (kinds is not None and nb["kind"] not in kinds):
                continue
            a_key, b_key = pair
            ra, rb = (na, nb) if a_key == key else (nb, na)
            s = {"a": R.addr(*a_key), "b": R.addr(*b_key), "pa": [ra["x"], ra["y"], ra["z"]],
                 "pb": [rb["x"], rb["y"], rb["z"]], "kind": na["kind"], "dist": ln["dist"]}
            if ln["navi_area"] is not None:
                nk = (ln["navi_area"], ln["navi_idx"])
                lanes = segment_lanes(db.navi(*nk), a_key, b_key)
                s["navi"] = R.addr(*nk)
                if lanes is not None:
                    s["lanes"] = lanes
                s["_navi"] = nk
            if ln["inter"]:
                s["inter"] = ln["inter"]
            segs.append(s)
    segs.sort(key=lambda s: (R.parse_addr(s["a"]), R.parse_addr(s["b"])))
    return segs
