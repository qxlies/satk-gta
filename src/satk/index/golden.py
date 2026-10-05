"""``satk index verify``: golden numbers of SPEC Appendix A checked against a built index. Owner: WP-03.

Golden files live in ``tools/tests/golden/index_<profile>.json``::

    {"profile": "vanilla", "metrics": {"blobs": 21147, ...}, "examples": [...], "_comment": {...}}

* ``metrics`` - name -> expected integer; every name is computed by :func:`compute_metrics`
  (SQL over the index; definitions in :data:`METRIC_DOCS`);
* ``examples`` - ``{"get": sid, "expect": {field: value}}`` / ``{"refs": sid, "rel": r, "contains": sid}``
  / ``{"find": q, "kind": k, "total": n}`` / ``{"near": {...}, "total": n}`` checks through the
  real query API.

Stdlib only.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..core.config import REPO_ROOT
from ..core.errors import SatkError
from ..core.paths import jpath

__all__ = ["golden_path", "load_golden", "compute_metrics", "verify", "METRIC_DOCS"]

#: Human-readable definitions (also written as comments into the golden files).
METRIC_DOCS: dict[str, str] = {
    "img.archives": "registered IMG archives (source.kind='img' with load_order)",
    "img.entries[.<stem>]": "IMG directory entries (blobs of kind img sources), total and per archive stem",
    "img.<stem>.<ext>": "entries of one archive by extension",
    "img.size_in_archive_nonzero": "VER2 entries with SizeInArchive != 0",
    "loose.<ext> / loose.total": "loose assets under models/ and anim/ (blob.ns='loose')",
    "blobs": "all blobs = IMG entries + loose files",
    "txd.files": "parsed TXD blobs (all versions, shadowed included)",
    "tex.total / tex.src.<stem> / tex.fmt.<fmt> / tex.d3d8": "texture rows by source archive (loose = 'loose'), D3D format, platform 8",
    "tex.unique_names / tex.unique_hashes": "distinct lower(texture.name) / distinct pixel hashes (image rows)",
    "dff.files / dff.src.<stem> / dff.clumps / dff.atomics / dff.rw_0x35000": "parsed DFF blobs and their clump/atomic sums",
    "geo.main.*": "geometries of DFFs in gta3.img + gta_int.img: count, vertices, tristrip meshes, triangles after strip expansion",
    "ide.*": "loaded IDE files, definitions per section, txdp pairs, IDE 2dfx lines, max model ID, IDs defined more than once",
    "ipl.*": "loaded text IPLs (+inst), binary IPLs of active blobs (+inst), LOD links (lod_idx >= 0) and unresolved ones, bnry without a text parent",
    "col.files / col.models / col.v<N>": ".col blobs and their collision models by version (embedded DFF collisions excluded)",
    "ifp.files / ifp.anims": "IFP blobs and animations",
    "texref.unresolved_main": "report 14 sec. 5.5: distinct (model, material texture name) of active models whose DFF "
                              "is in gta3.img/gta_int.img and whose texture is in neither the model's own TXD (by "
                              "name, active blob) nor - for 'cars' - in 'vehicle'",
    "texref.unresolved_all": "the same over every DFF of the main streaming namespace (gta3, gta_int, cutscene ...) "
                             "plus textured materials whose texture name is EMPTY (counted by report 14's SQL "
                             "variant; 5 in vanilla, all in gta_int; cutscene.img adds 0)",
    "texref.missing_main / texref.missing_all": "engine-faithful model_tex rows with via='missing' (own TXD -> txdp "
                                                "parents -> 'vehicle' parent of every car TXD, also for tuning parts)",
    "shadow.gta_int.txd": "TXD entries of gta_int.img shadowed by gta3.img ('first registered archive wins')",
    "q.*": "query checks: textures named ws_rooftarmac1, DFFs referencing it, placements with centre in the Grove box",
    "water.* / water1.polys": "schema v3: data/water.dat polygons (quads, triangles, flags 1 visible / 2 shallow); "
                              "data/water1.dat polygons (configuration 1)",
    "tcyc.*": "schema v3: data/timecyc.dat rows (weather x hour), distinct weathers and hours, lines the engine "
              "reads short (the rest repeats the previous line)",
    "handling.<kind> / handling.cars_linked / handling.unused": "schema v3: data/handling.cfg records per type "
                                                                "(car, bike '!', boat '%', flying '$'); active "
                                                                "'cars' definitions whose handling id has a car "
                                                                "record; car records no definition uses",
    "carcol.*": "schema v3: data/carcols.dat palette entries, models with colours, variations, 'car4' models, "
                "variations naming a colour outside the palette, colour models without an active definition",
    "ped_rel.*": "schema v3: data/ped.dat acquaintance pairs (pedtype, rel, other) and ped types with any",
    "object.*": "schema v3: data/object.dat lines, lines before the '*' terminator (loaded), loaded names that "
                "are active model names",
    "view.*": "schema v3: rows of the views v_vehicle (active cars) and v_ped (active peds)",
}
# SPEC spelling; keep the original dotted keys and their golden values for compatibility.
for _suffix in ("main", "all"):
    METRIC_DOCS[f"unresolved_texrefs_{_suffix}"] = METRIC_DOCS[f"texref.unresolved_{_suffix}"]


def golden_path(profile: str) -> Path:
    return REPO_ROOT / "tests" / "golden" / f"index_{profile}.json"


def load_golden(profile: str, path: Path | None = None) -> dict:
    p = Path(path) if path is not None else golden_path(profile)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"no golden file for profile {profile!r}: {jpath(p)}",
                        hint="golden files: tools/tests/golden/index_<profile>.json") from None
    except ValueError as e:
        raise SatkError("BAD_PARAMS", f"golden file {jpath(p)} is not valid JSON: {e}") from None


def _one(c: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    r = c.execute(sql, params).fetchone()
    return r[0] if r else None


def _stem(relpath: str) -> str:
    return relpath.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _data_metrics(c: sqlite3.Connection, m: dict[str, int]) -> None:
    """Metrics of the schema-v3 data tables (``water.*``, ``tcyc.*``, ``handling.*``, ``carcol.*`` ...)."""
    w = "SELECT count(*) FROM water_quad WHERE config = {} AND {}"
    m["water.polys"] = _one(c, w.format(0, "1"))
    m["water.quads"] = _one(c, w.format(0, "nverts = 4"))
    m["water.tris"] = _one(c, w.format(0, "nverts = 3"))
    m["water.visible"] = _one(c, w.format(0, "flags & 1"))
    m["water.shallow"] = _one(c, w.format(0, "flags & 2"))
    m["water1.polys"] = _one(c, w.format(1, "1"))
    m["tcyc.rows"] = _one(c, "SELECT count(*) FROM timecyc")
    m["tcyc.weathers"] = _one(c, "SELECT count(DISTINCT weather) FROM timecyc")
    m["tcyc.hours"] = _one(c, "SELECT count(DISTINCT hour) FROM timecyc")
    m["tcyc.short_lines"] = _one(c, "SELECT count(*) FROM timecyc WHERE nread < 51")
    for kind in ("car", "bike", "boat", "flying"):
        m[f"handling.{kind}"] = _one(c, "SELECT count(*) FROM handling WHERE kind = ?", (kind,))
    m["handling.cars_linked"] = _one(c, "SELECT count(*) FROM v_vehicle WHERE mass IS NOT NULL")
    m["handling.unused"] = _one(c, "SELECT count(*) FROM handling h WHERE kind = 'car' AND NOT EXISTS (SELECT 1 FROM "
                                   "model m WHERE m.active = 1 AND m.sec = 'cars' "
                                   "AND lower(json_extract(m.extra, '$.handling')) = lower(h.name))")
    m["carcol.palette"] = _one(c, "SELECT count(*) FROM carcol")
    m["carcol.models"] = _one(c, "SELECT count(DISTINCT model) FROM car_color")
    m["carcol.sets"] = _one(c, "SELECT count(*) FROM car_color")
    m["carcol.car4"] = _one(c, "SELECT count(DISTINCT model) FROM car_color WHERE c3 IS NOT NULL")
    pal = "NOT IN (SELECT idx FROM carcol)"
    m["carcol.out_of_palette"] = _one(c, f"SELECT count(*) FROM car_color WHERE c1 {pal} OR c2 {pal} "
                                         f"OR (c3 IS NOT NULL AND c3 {pal}) OR (c4 IS NOT NULL AND c4 {pal})")
    m["carcol.unmatched"] = _one(c, "SELECT count(DISTINCT x.model) FROM car_color x WHERE NOT EXISTS (SELECT 1 FROM "
                                    "model m WHERE m.active = 1 AND m.name = x.model)")
    m["ped_rel.rows"] = _one(c, "SELECT count(*) FROM ped_rel")
    m["ped_rel.pedtypes"] = _one(c, "SELECT count(DISTINCT pedtype) FROM ped_rel")
    m["object.rows"] = _one(c, "SELECT count(*) FROM object_data")
    m["object.loaded"] = _one(c, "SELECT count(*) FROM object_data WHERE loaded = 1")
    m["object.models"] = _one(c, "SELECT count(DISTINCT lower(o.name)) FROM object_data o WHERE o.loaded = 1 AND "
                                 "EXISTS (SELECT 1 FROM model m WHERE m.active = 1 AND m.name = o.name)")
    m["view.v_vehicle"] = _one(c, "SELECT count(*) FROM v_vehicle")
    m["view.v_ped"] = _one(c, "SELECT count(*) FROM v_ped")


def compute_metrics(c: sqlite3.Connection) -> dict[str, int]:  # noqa: C901 - one block per family
    """All metric values of :data:`METRIC_DOCS` for the index behind connection ``c``."""
    m: dict[str, int] = {}
    m["img.archives"] = _one(c, "SELECT count(*) FROM source WHERE kind='img' AND load_order IS NOT NULL")
    m["img.entries"] = 0
    for rel, n in c.execute("SELECT s.relpath, count(*) FROM blob b JOIN source s ON s.id=b.source_id "
                            "WHERE s.kind='img' AND b.ns!='loose' GROUP BY s.relpath"):
        m["img.entries"] += n
        m[f"img.entries.{_stem(rel)}"] = n
    for rel, ext, n in c.execute("SELECT s.relpath, b.ext, count(*) FROM blob b JOIN source s ON s.id=b.source_id "
                                 "WHERE s.kind='img' AND b.ns!='loose' GROUP BY s.relpath, b.ext"):
        m[f"img.{_stem(rel)}.{ext}"] = n
    m["img.size_in_archive_nonzero"] = _one(c, "SELECT count(*) FROM blob WHERE archive_sectors > 0")
    m["loose.total"] = 0
    for ext, n in c.execute("SELECT ext, count(*) FROM blob WHERE ns='loose' GROUP BY ext"):
        m[f"loose.{ext}"] = n
        m["loose.total"] += n
    m["blobs"] = _one(c, "SELECT count(*) FROM blob")
    # textures
    m["txd.files"] = _one(c, "SELECT count(*) FROM txd")
    m["tex.total"] = _one(c, "SELECT count(*) FROM texture")
    for src, n in c.execute("SELECT CASE WHEN b.ns='loose' THEN 'loose' ELSE s.relpath END, count(*) FROM texture x "
                            "JOIN txd t ON t.id=x.txd_id JOIN blob b ON b.id=t.blob_id JOIN source s ON s.id=b.source_id "
                            "GROUP BY 1"):
        m[f"tex.src.{_stem(src) if src != 'loose' else 'loose'}"] = n
    for fmt, n in c.execute("SELECT d3dfmt, count(*) FROM texture GROUP BY d3dfmt"):
        m[f"tex.fmt.{fmt}"] = n
    m["tex.d3d8"] = _one(c, "SELECT count(*) FROM texture WHERE platform=8")
    m["tex.unique_names"] = _one(c, "SELECT count(DISTINCT lower(name)) FROM texture")
    m["tex.unique_hashes"] = _one(c, "SELECT count(*) FROM image")
    # models
    m["dff.files"] = _one(c, "SELECT count(*) FROM dff")
    for src, n in c.execute("SELECT CASE WHEN b.ns='loose' THEN 'loose' ELSE s.relpath END, count(*) FROM dff d "
                            "JOIN blob b ON b.id=d.blob_id JOIN source s ON s.id=b.source_id GROUP BY 1"):
        m[f"dff.src.{_stem(src) if src != 'loose' else 'loose'}"] = n
    m["dff.clumps"] = _one(c, "SELECT coalesce(sum(clumps),0) FROM dff")
    m["dff.atomics"] = _one(c, "SELECT coalesce(sum(atomics),0) FROM dff")
    m["dff.rw_0x35000"] = _one(c, "SELECT count(*) FROM dff WHERE rw_version=0x35000")
    main = ("SELECT {} FROM dff_geom g JOIN dff d ON d.id=g.dff_id JOIN blob b ON b.id=d.blob_id "
            "JOIN source s ON s.id=b.source_id WHERE s.relpath IN ('models/gta3.img','models/gta_int.img')")
    m["geo.main.geoms"] = _one(c, main.format("count(*)"))
    m["geo.main.verts"] = _one(c, main.format("coalesce(sum(g.verts),0)"))
    m["geo.main.tristrip"] = _one(c, main.format("coalesce(sum(g.strip),0)"))
    m["geo.main.tris"] = _one(c, main.format("coalesce(sum(g.tris),0)"))
    # definitions
    m["ide.files"] = _one(c, "SELECT count(*) FROM ide")
    m["ide.defs"] = _one(c, "SELECT count(*) FROM model")
    for sec, n in c.execute("SELECT sec, count(*) FROM model GROUP BY sec"):
        m[f"ide.{sec}"] = n
    stats = json.loads(_one(c, "SELECT value FROM meta WHERE key='stats'") or "{}")
    m["ide.txdp"] = int(stats.get("txdp", 0))
    m["ide.2dfx"] = _one(c, "SELECT count(*) FROM fx2d WHERE origin='ide'")
    m["ide.max_id"] = _one(c, "SELECT coalesce(max(id),-1) FROM model")
    m["ide.dup_ids"] = _one(c, "SELECT count(*) FROM (SELECT id FROM model GROUP BY id HAVING count(*) > 1)")
    # world
    m["ipl.text_files"] = _one(c, "SELECT count(*) FROM ipl WHERE kind='text'")
    m["ipl.text_inst"] = _one(c, "SELECT count(*) FROM inst i JOIN ipl p ON p.id=i.ipl_id WHERE p.kind='text'")
    m["ipl.bin_files"] = _one(c, "SELECT count(*) FROM ipl WHERE kind='binary'")
    m["ipl.bin_inst"] = _one(c, "SELECT count(*) FROM inst i JOIN ipl p ON p.id=i.ipl_id WHERE p.kind='binary'")
    m["ipl.inst"] = _one(c, "SELECT count(*) FROM inst")
    m["ipl.lod_links"] = _one(c, "SELECT count(*) FROM inst WHERE lod_idx >= 0")
    m["ipl.lod_unresolved"] = _one(c, "SELECT count(*) FROM inst WHERE lod_idx >= 0 AND lod_id IS NULL")
    m["ipl.bin_orphans"] = _one(c, "SELECT count(*) FROM ipl WHERE kind='binary' AND parent_id IS NULL")
    m["ipl.bin_orphan_inst"] = _one(c, "SELECT count(*) FROM inst i JOIN ipl p ON p.id=i.ipl_id "
                                       "WHERE p.kind='binary' AND p.parent_id IS NULL")
    m["ipl.is_lod"] = _one(c, "SELECT count(*) FROM inst WHERE is_lod=1")
    m["zone.rows"] = _one(c, "SELECT count(*) FROM zone")
    # collisions / animations
    m["col.files"] = _one(c, "SELECT count(DISTINCT c.blob_id) FROM col c JOIN blob b ON b.id=c.blob_id WHERE b.ext='col'")
    m["col.models"] = _one(c, "SELECT count(*) FROM col c JOIN blob b ON b.id=c.blob_id WHERE b.ext='col'")
    for v, n in c.execute("SELECT c.version, count(*) FROM col c JOIN blob b ON b.id=c.blob_id WHERE b.ext='col' "
                          "GROUP BY c.version"):
        m[f"col.v{v}"] = n
    m["col.embedded"] = _one(c, "SELECT count(*) FROM col c JOIN blob b ON b.id=c.blob_id WHERE b.ext='dff'")
    m["ifp.files"] = _one(c, "SELECT count(*) FROM ifp")
    m["ifp.anims"] = _one(c, "SELECT count(*) FROM anim")
    m["ifp.blobs"] = _one(c, "SELECT count(*) FROM blob WHERE ext='ifp'")
    # links: engine-faithful resolution (model_tex: own TXD -> txdp parents -> 'vehicle')
    q = ("SELECT count(*) FROM model_tex mt JOIN model_link ml ON ml.id=mt.model_id JOIN dff d ON d.id=ml.dff_id "
         "JOIN blob b ON b.id=d.blob_id JOIN source s ON s.id=b.source_id WHERE mt.via='missing' AND {}")
    m["texref.missing_main"] = _one(c, q.format("s.relpath IN ('models/gta3.img','models/gta_int.img')"))
    m["texref.missing_all"] = _one(c, q.format("b.ns='main'"))
    m["texref.total"] = _one(c, "SELECT count(*) FROM model_tex")
    # report 14 sec. 5.5 metric (the golden 462/467): own TXD by name + 'vehicle' for cars only
    have = {(t, x) for t, x in c.execute(
        "SELECT lower(t.name), lower(x.name) FROM texture x JOIN txd t ON t.id=x.txd_id JOIN blob b ON b.id=t.blob_id "
        "WHERE b.active=1")}
    refs = c.execute(
        "SELECT DISTINCT m.id, lower(dm.texture), lower(coalesce(m.txd,'')), m.sec, s.relpath, b.ns "
        "FROM model m JOIN model_link ml ON ml.id=m.id JOIN dff_mat dm ON dm.dff_id=ml.dff_id "
        "JOIN dff d ON d.id=ml.dff_id JOIN blob b ON b.id=d.blob_id JOIN source s ON s.id=b.source_id "
        "WHERE m.active=1 AND dm.texture IS NOT NULL").fetchall()
    miss = [(rel, ns) for _mid, t, txd, sec, rel, ns in refs
            if (txd, t) not in have and not (sec == "cars" and ("vehicle", t) in have)]
    m["texref.unresolved_main"] = sum(1 for rel, _ns in miss if rel in ("models/gta3.img", "models/gta_int.img"))
    empty = {int(k): v for k, v in (stats.get("dff_empty_texname") or {}).items()}
    n_empty = 0
    if empty:
        for (did,) in c.execute("SELECT ml.dff_id FROM model_link ml JOIN dff d ON d.id=ml.dff_id "
                                "JOIN blob b ON b.id=d.blob_id WHERE b.ns='main' AND ml.dff_id IS NOT NULL"):
            n_empty += empty.get(did, 0)
    m["texref.empty_names_main"] = n_empty
    m["texref.unresolved_all"] = sum(1 for _rel, ns in miss if ns == "main") + n_empty
    for suffix in ("main", "all"):
        m[f"unresolved_texrefs_{suffix}"] = m[f"texref.unresolved_{suffix}"]
    m["shadow.gta_int.txd"] = _one(c, "SELECT count(*) FROM blob b JOIN source s ON s.id=b.source_id "
                                      "WHERE s.relpath='models/gta_int.img' AND b.ext='txd' AND b.active=0")
    m["shadow.blobs"] = _one(c, "SELECT count(*) FROM blob WHERE active=0")
    m["model.active"] = _one(c, "SELECT count(*) FROM model WHERE active=1")
    m["q.tex_named.ws_rooftarmac1"] = _one(c, "SELECT count(*) FROM texture WHERE name='ws_rooftarmac1'")
    m["q.dff_refs.ws_rooftarmac1"] = _one(c, "SELECT count(DISTINCT dff_id) FROM dff_mat WHERE texture='ws_rooftarmac1'")
    m["q.box_center_all"] = _one(c, "SELECT count(*) FROM inst WHERE x BETWEEN 2445 AND 2545 AND y BETWEEN -1737 AND -1637")
    for kind, n in c.execute("SELECT kind, count(*) FROM fts_name WHERE fts_name MATCH '\"grove\"' GROUP BY kind"):
        m[f"q.fts.grove.{kind}"] = n
    # zones and their in-game names (schema v2, text/american.gxt)
    m["zone.titled"] = _one(c, "SELECT count(*) FROM zone WHERE title IS NOT NULL")
    m["q.fts.ganton.zone"] = _one(c, "SELECT count(*) FROM fts_name WHERE fts_name MATCH '\"ganton\"' AND kind='zone'")
    # schema v3: exe-loaded data files
    if _one(c, "SELECT count(*) FROM sqlite_master WHERE name='water_quad'"):
        _data_metrics(c, m)
    # layers
    for name, n in c.execute("SELECT l.name, count(*) FROM blob b JOIN source s ON s.id=b.source_id "
                             "JOIN layer l ON l.id=s.layer_id GROUP BY l.name"):
        m[f"layer.{name}.blobs"] = n
    for name, n in c.execute("SELECT l.name, count(*) FROM model m JOIN layer l ON l.id=m.layer_id GROUP BY l.name"):
        m[f"layer.{name}.models"] = n
    return {k: int(v or 0) for k, v in m.items()}


def _check_example(db, ex: dict) -> str | None:
    """None if the example holds, else a failure message."""
    try:
        if "get" in ex:
            obj = db.get(ex["get"])
            for k, want in ex.get("expect", {}).items():
                cur: Any = obj
                for part in k.split("."):
                    cur = cur.get(part) if isinstance(cur, dict) else None
                if cur != want:
                    return f"get {ex['get']}: {k}={cur!r}, want {want!r}"
            return None
        if "refs" in ex:
            env = db.refs(ex["refs"], ex.get("rel"), limit=500)
            ids = [r[0] for r in env["rows"]]
            if ex.get("contains") not in ids:
                return f"refs {ex['refs']} --rel {ex.get('rel')}: {ex.get('contains')} not in {ids[:5]}"
            return None
        if "find" in ex:
            env = db.find(ex["find"], ex.get("kind"), limit=1)
            if env["total"] != ex["total"]:
                return f"find {ex['find']!r} kind={ex.get('kind')}: total {env['total']}, want {ex['total']}"
            return None
        if "near" in ex:
            args = dict(ex["near"])
            if "box" in args:
                args["box"] = tuple(args["box"])
            args.setdefault("x", None)
            args.setdefault("y", None)
            env = db.near(**args)
            if env["total"] != ex["total"]:
                return f"near {ex['near']}: total {env['total']}, want {ex['total']}"
            return None
        if "model_files" in ex:
            mf = db.model_files(ex["model_files"])
            got = {"dff": mf.dff.name if mf.dff else None, "txd_chain": [b.name for b in mf.txd_chain]}
            for k, want in ex.get("expect", {}).items():
                if got.get(k) != want:
                    return f"model_files {ex['model_files']}: {k}={got.get(k)!r}, want {want!r}"
            return None
        if "match_runtime" in ex:
            mid, pos = ex["match_runtime"]
            got = db.match_runtime(mid, tuple(pos))
            if got != ex["expect"]:
                return f"match_runtime {mid} {pos}: {got}, want {ex['expect']}"
            return None
    except SatkError as e:
        return f"{json.dumps(ex)[:80]}: {e.code} {e}"
    return f"unknown example {ex}"


def verify(db, golden: dict) -> dict:
    """Compare the index of ``db`` (an :class:`~satk.index.api.IndexDB`) with ``golden``.

    Returns ``{"ok", "profile", "checked", "failed": [[name, got, want]], "metrics": {...}}``.
    ``unresolved_texrefs_main/all`` also appear at the top level for the default CLI summary;
    they alias the legacy dotted metrics without changing the golden definitions or values.
    """
    conn = db._conn()
    metrics = compute_metrics(conn)
    failed: list[list] = []
    checked = 0
    for k, want in (golden.get("metrics") or {}).items():
        if k.startswith("_"):
            continue
        checked += 1
        got = metrics.get(k, 0)
        if got != want:
            failed.append([k, got, want])
    for ex in golden.get("examples") or []:
        checked += 1
        msg = _check_example(db, ex)
        if msg:
            failed.append(["example", msg, "ok"])
    return {"ok": not failed, "profile": db.profile, "checked": checked, "failed": failed, "metrics": metrics,
            "unresolved_texrefs_main": metrics["unresolved_texrefs_main"],
            "unresolved_texrefs_all": metrics["unresolved_texrefs_all"]}
