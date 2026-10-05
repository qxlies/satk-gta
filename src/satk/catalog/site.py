"""Static files of the catalog (owner M2-09): ``index.html`` + ``data/*.js`` chunks.

The page is one self-contained HTML file (CSS and JS inline, no external URLs); data comes as script
chunks ``data/<name>.js`` = ``CAT.add("<name>", <json>);`` because browsers block ``fetch()`` of local
files opened from ``file://``. Layout of ``data/``:

* ``meta.js`` - profile, index hash, counts, lookup tables (sections, formats, archives, IPL names);
* ``models.js`` / ``textures.js`` / ``txds.js`` / ``zones.js`` - the search index (compact rows, loaded at
  start, ~2.5 MB for vanilla);
* ``m/<id >> 7>.js`` - model pages (DFF, COL, IDE fields, textures, placements), 128 ids per chunk;
* ``t/<i >> 8>.js`` - models using each texture; ``z.js`` - most placed models per zone.

JSON is compact and key-sorted, so the same input gives byte-identical files (rewritten only on change).
"""

from __future__ import annotations

import json
from pathlib import Path

from .collect import WORLD, Data
from .files import write_if_changed

__all__ = ["DATA_VERSION", "M_SHIFT", "T_SHIFT", "js", "render_index", "write_data"]

#: Version of the ``data/`` format (read by the page).
DATA_VERSION = 1
#: Model pages: ``data/m/<id >> M_SHIFT>.js``.
M_SHIFT = 7
#: Texture pages: ``data/t/<index >> T_SHIFT>.js``.
T_SHIFT = 8
_TEMPLATES = Path(__file__).with_name("templates")


def js(name: str, payload) -> str:
    """One chunk: ``CAT.add("<name>", <compact json>);``."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f'CAT.add({json.dumps(name)},{body});\n'


def render_index(profile: str) -> str:
    """``index.html`` with the CSS and JS of ``templates/`` inlined."""
    try:
        html = (_TEMPLATES / "catalog.html").read_text(encoding="utf-8")
        css = (_TEMPLATES / "catalog.css").read_text(encoding="utf-8")
        script = (_TEMPLATES / "catalog.js").read_text(encoding="utf-8")
    except OSError as e:  # pragma: no cover - broken installation
        from ..core.errors import SatkError

        raise SatkError("INTERNAL", f"catalog templates missing: {e}",
                        hint="reinstall satk (src/satk/catalog/templates/)") from None
    return (html.replace("{{PROFILE}}", profile.replace("<", "").replace("&", ""))
            .replace("{{CSS}}", css.rstrip()).replace("{{JS}}", script.rstrip()))


def _table(values) -> tuple[list[str], dict[str, int]]:
    lst = sorted({str(v) for v in values})
    return lst, {v: i for i, v in enumerate(lst)}


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v != [] and v != ""}


def write_data(data: Data, root: Path, *, ext: str, tex_ok: set[str], model_keys: dict[int, str],
               map_file: str | None, full_base: str | None, satk_version: str) -> tuple[list[Path], int]:
    """Write every ``data/*.js`` chunk; returns ``(paths, files rewritten)``."""
    d = root / "data"
    out: list[tuple[Path, str]] = []

    secs, sec_i = _table(m["sec"] for m in data.models)
    fmts, fmt_i = _table(x["fmt"] for x in data.textures)
    archives, arch_i = _table(t["archive"] for t in data.txds)
    nss, ns_i = _table(t["ns"] for t in data.txds)
    img_i = {h: i for i, h in enumerate(data.images)}

    meta = {
        "v": DATA_VERSION, "profile": data.profile, "index": data.index_hash[:16], "satk": satk_version,
        "limited": data.limited, "counts": data.counts, "ext": ext, "map": map_file, "world": list(WORLD),
        "full": full_base, "secs": secs, "fmts": fmts, "archives": archives, "nss": nss, "ipls": data.ipls,
        "mchunk": M_SHIFT, "tchunk": T_SHIFT,
    }
    out.append((d / "meta.js", js("meta", meta)))

    models = []
    for m in data.models:
        dff = m["dff"]
        models.append([m["id"], m["name"], sec_i[m["sec"]], m["txd_i"], int(m["id"] in model_keys), m["n_inst"],
                       m["tex_total"], m["tex_missing"], dff[1] if dff else None])
    out.append((d / "models.js", js("models", models)))

    txds = [[t["name"], t["ntex"], t["parent_i"], arch_i[t["archive"]], t["nmodels"], ns_i[t["ns"]]] for t in data.txds]
    out.append((d / "txds.js", js("txds", txds)))

    rows = []
    for x in data.textures:
        row = [x["txd_i"], x["name"], x["w"], x["h"], fmt_i[x["fmt"]], x["alpha"], img_i[x["pix"]], x["nmodels"]]
        if x["mask"]:
            row.append(x["mask"])
        rows.append(row)
    nothumb = [i for i, h in enumerate(data.images) if h not in tex_ok]
    out.append((d / "textures.js", js("textures", {"img": data.images, "nothumb": nothumb, "rows": rows})))

    zones = []
    for z in data.zones:
        b = [round(v, 1) for v in z["box"]]
        zones.append([z["name"], z["label"], z["title"], z["type"], z["level"], *b, z.get("ninst", 0),
                      z.get("nmodels", 0)])
    out.append((d / "zones.js", js("zones", zones)))

    # model pages
    chunks: dict[int, dict] = {}
    for m in data.models:
        page = _drop_none({
            "d": m["dff"], "c": m["col"], "i": m["ide"], "l": m["layer"], "dr": m["draw"], "f": m["flags"],
            "tm": m["time"], "an": m["anim"], "ex": m["extra"], "tx": data.model_tex.get(m["id"]),
            "p": data.insts.get(m["id"]), "tn": m["txd"] if m["txd_i"] < 0 else None,
        })
        chunks.setdefault(m["id"] >> M_SHIFT, {})[str(m["id"])] = page
    for k in sorted(chunks):
        out.append((d / "m" / f"{k}.js", js(f"m/{k}", chunks[k])))

    tchunks: dict[int, dict] = {}
    for ti in sorted(data.tex_models):
        tchunks.setdefault(ti >> T_SHIFT, {})[str(ti)] = data.tex_models[ti]
    for k in sorted(tchunks):
        out.append((d / "t" / f"{k}.js", js(f"t/{k}", tchunks[k])))

    out.append((d / "z.js", js("z", {str(k): v for k, v in sorted(data.zone_models.items())})))

    changed = sum(1 for p, text in out if write_if_changed(p, text))
    return [p for p, _ in out], changed
