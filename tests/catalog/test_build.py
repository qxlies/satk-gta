"""satk catalog build over the fake index (M2-09): files, data chunks, thumbnails, repeat, cleanup, limits."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from satk.core import registry as R
from satk.core.errors import SatkError


def _build(**kw):
    from satk.catalog.build import build

    kw.setdefault("jobs", 1)
    return build(**kw)


def _data(root: Path, ch) -> dict:
    out = {}
    for p in sorted((root / "data").rglob("*.js")):
        name, payload = ch.chunk(p)
        assert name == p.relative_to(root / "data").with_suffix("").as_posix()
        out[name] = payload
    return out


def test_op_registered():
    R.discover()
    spec = R.get_op("catalog.build")
    assert spec.mcp_name is None and spec.cli == "catalog build" and spec.long_running
    assert {p.name for p in spec.params} == {"out", "limit", "jobs", "thumbs", "fmt", "profile"}


def test_build_writes_a_self_contained_site(cat_db, ch):
    env = _build()
    assert env["ok"] and not env.get("warn"), env
    root = Path(env["dir"])
    assert root.name == "catalog" and env["file"].endswith("/catalog/index.html")
    assert env["counts"] == {"models": 4, "textures": 30, "images": 30, "txds": 6, "zones": 3, "placements": 2,
                             "exterior": 2}
    html = (root / "index.html").read_text(encoding="utf-8")
    # no external URLs: no CDN, fonts or remote scripts (data and thumbnails are relative paths)
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html, re.I)
    assert "@import" not in html and "url(http" not in html
    assert "{{" not in html and 'CAT.add' in html and '"data/"' in html

    d = _data(root, ch)
    assert {"meta", "models", "textures", "txds", "zones", "z"} <= set(d)
    meta = d["meta"]
    assert meta["ext"] == env["format"] == "webp" and meta["map"] == "map.webp" and meta["profile"] == "vanilla"
    assert meta["full"] == "../../cache/tex/"
    models = {r[0]: r for r in d["models"]}
    assert set(models) == {300, 411, 17613, 17858}
    assert models[411][1] == "infernus" and meta["secs"][models[411][2]] == "cars" and models[411][4] == 1
    assert models[300][4] == 0  # cutobj01 has no DFF -> no thumbnail
    txds = [r[0] for r in d["txds"]]
    assert txds == sorted(txds, key=str.lower)
    # every image has a thumbnail; model 411 page lists its 13 material textures and model 17613 its placement
    tex = d["textures"]
    assert len(tex["rows"]) == 30 and len(tex["img"]) == 30 and tex["nothumb"] == []
    for h in tex["img"]:
        assert (root / "t" / "x" / h[:2] / f"{h}.webp").is_file()
    page = d[f"m/{411 >> 7}"]["411"]
    assert len(page["tx"]) == 13 and {e[1] for e in page["tx"]} == {"own", "vehicle"}
    assert all(e[3] >= 0 for e in page["tx"])
    p17613 = d[f"m/{17613 >> 7}"]["17613"]
    assert p17613["p"] == [[meta["ipls"].index("lae2_stream0"), 4, 2489.3, -1668.5, 12.2, 0, 0]] or \
        p17613["p"][0][:2] == [meta["ipls"].index("lae2_stream0"), 4]
    # texture -> models (inverse of model_tex)
    ti = next(i for i, r in enumerate(tex["rows"]) if r[1] == "plaintarmac1")
    assert d[f"t/{ti >> 8}"][str(ti)] == [[17613, 1]]
    # zones: Ganton holds the one HD street placement, the LOD is not counted
    zones = {r[0]: r for r in d["zones"]}
    assert zones["GAN1"][2] == "Ganton" and zones["GAN1"][11] == 1 and zones["SF01"][11] == 0
    assert d["z"][str(d["zones"].index(zones["GAN1"]))] == [[17613, 1]]
    # model thumbnails: one per model with a DFF
    for mid in (411, 17613, 17858):
        assert (root / "t" / "m" / str(mid >> 7) / f"{mid}.webp").is_file()
    assert sorted(cat_db.renders) == [411, 17613, 17858]
    man = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    assert man["ext"] == "webp" and set(man["models"]["keys"]) == {"411", "17613", "17858"}


def test_search_grove_style_lookup_in_data(cat_db, ch):
    """The page searches 'txd/name' substrings; the same lookup over the data finds the textures."""
    root = Path(_build()["dir"])
    d = _data(root, ch)
    names = [(d["txds"][r[0]][0] + "/" + r[1]).lower() for r in d["textures"]["rows"]]
    assert [n for n in names if "sidewgrass" in n] == ["lae2roadshub/sidewgrass2", "lae2roadshub/sidewgrass3",
                                                         "lae2roadshub/sidewgrass1"]
    assert [n for n in names if "vent" in n] == ["bistro/vent_64"]


def test_repeat_rewrites_nothing(cat_db):
    env1 = _build()
    root = Path(env1["dir"])
    stamp = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    n = len(cat_db.renders)
    env2 = _build()
    assert env2["changed"] == 0 and env2["removed"] == 0
    assert env2["thumbs"]["textures"]["kept"] == 30 and env2["thumbs"]["textures"]["written"] == 0
    assert env2["thumbs"]["models"]["kept"] == 3 and env2["thumbs"]["models"]["converted"] == 0
    assert len(cat_db.renders) == n  # same index + renderer: the model cache is not even consulted
    assert {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()} == stamp


def test_stale_files_are_removed(cat_db):
    root = Path(_build()["dir"])
    stray = [root / "t" / "x" / "zz" / "zz0000000000000000000000.webp", root / "data" / "m" / "999.js",
             root / "t" / "m" / "9" / "1234.webp", root / "map.png"]
    for p in stray:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    keep = root / "notes.txt"
    keep.write_text("mine", encoding="utf-8")
    env = _build()
    assert env["removed"] == 4 and not any(p.exists() for p in stray)
    assert not (root / "t" / "x" / "zz").exists() and keep.is_file()


def test_limit_out_and_png(cat_db, ch):
    env = _build(limit=1, out="catalog-demo", fmt="png")
    root = Path(env["dir"])
    assert root.name == "catalog-demo" and env["format"] == "png"
    d = _data(root, ch)
    assert [r[0] for r in d["models"]] == [300] and d["meta"]["limited"] is True
    assert env["counts"]["models"] == 1
    # cutobj01 uses the 'generic' TXD, absent in the fake: no textures, no thumbnails, still a valid site
    assert d["textures"]["rows"] == [] and not list((root / "t").rglob("*.webp"))
    env = _build(limit=2)
    assert Path(env["dir"]).name == "catalog-sample"
    d = _data(Path(env["dir"]), ch)
    assert [r[0] for r in d["models"]] == [300, 411]
    assert {d["txds"][r[0]][0] for r in d["textures"]["rows"]} == {"infernus", "vehicle"}
    for h in d["textures"]["img"]:
        assert (Path(env["dir"]) / "t" / "x" / h[:2] / f"{h}.webp").is_file()


def test_no_thumbs_reuses_existing(cat_db, ch):
    env = _build(thumbs=False)
    assert env["thumbs"]["textures"] == {"kept": 0, "skipped": 30} and cat_db.renders == []
    d = _data(Path(env["dir"]), ch)
    assert len(d["textures"]["nothumb"]) == 30 and all(r[4] == 0 for r in d["models"])
    _build()
    env = _build(thumbs=False)
    d = _data(Path(env["dir"]), ch)
    assert d["textures"]["nothumb"] == [] and sum(r[4] for r in d["models"]) == 3


def test_out_validation(cat_db, satk_home):
    from satk.core.paths import cfg

    with pytest.raises(SatkError) as e:
        _build(out=str(Path(cfg().paths.work).parent / "elsewhere"))
    assert e.value.code == "BAD_PARAMS"
    foreign = Path(cfg().paths.work) / "out" / "models"
    foreign.mkdir(parents=True)
    (foreign / "infernus.glb").write_bytes(b"glb")
    with pytest.raises(SatkError) as e:
        _build(out="models")
    assert e.value.code == "BAD_PARAMS" and "not a catalog" in e.value.msg
    assert (foreign / "infernus.glb").is_file()
    with pytest.raises(SatkError) as e:
        _build(limit=0)
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError):
        _build(jobs=-1)


def test_cli(cat_db, run_cli):
    r = run_cli(["catalog", "build", "--limit", "2", "--jobs", "1", "--json"])
    assert r.code == 0, r.out + r.err
    env = r.json
    assert env["ok"] and env["counts"]["models"] == 2 and env["file"].endswith("/catalog-sample/index.html")
    r = run_cli(["catalog", "build", "--fmt", "gif"])
    assert r.code == 2


def test_missing_index(satk_home):
    env = R.invoke("catalog.build", {"limit": 1})
    assert env["error"]["code"] == "INDEX_MISSING"


def test_page_script_parses(tmp_path):
    """The inlined app is valid JavaScript (node --check, when node is installed)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    from satk.catalog.site import render_index

    html = render_index("vanilla")
    script = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
    js = tmp_path / "catalog.js"
    js.write_text(script, encoding="utf-8")
    p = subprocess.run([node, "--check", str(js)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
