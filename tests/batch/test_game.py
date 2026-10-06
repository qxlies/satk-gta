"""Batch and recipes on the vanilla game copy (read-only) and the shipped recipes end to end."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import synth
from satk.batch.recipe import run_recipe
from satk.batch.runner import run_batch
from satk.core.errors import SatkError
from satk.core.paths import jpath


def _lines(p: Path) -> list[dict]:
    return [json.loads(s) for s in p.read_text(encoding="utf-8").splitlines() if s.strip()]


@pytest.mark.game
def test_batch_over_vanilla_img_entries(clean_root, tmp_path):
    from satk.formats.img import ImgArchive

    with ImgArchive.open(clean_root / "models" / "gta3.img") as a:
        names = [e.name for e in a.entries]
        want = sorted((n for n in names if n.lower().startswith("ch") and n.lower().endswith(".dff")), key=str.lower)
    assert len(want) > 4
    out = tmp_path / "dump.jsonl"
    env = run_batch("formats.dump", "models/gta3.img/ch*.dff", arg=["level=stats"], jobs=4, out=str(out))
    assert env["counts"] == {"ok": len(want), "fail": 0} and env["source"] == "img" and env["jobs"] == 4
    assert any(w.startswith("OVER: matched under the vanilla game root") for w in env.get("warn", [])) or \
        Path("models/gta3.img").is_file()
    recs = _lines(out)
    assert [r["item"].rsplit("/", 1)[1] for r in recs] == want
    assert all(r["result"]["ok"] for r in recs)
    # asset lint of the same entries: the vanilla game has no fatal finding
    lint = run_batch("asset.lint", "models/gta3.img/infernus.*", arg=["fail_on=fatal"], jobs=2,
                     out=str(tmp_path / "lint.jsonl"))
    assert lint["counts"]["fail"] == 0 and lint["items"] >= 2 and lint["totals"]["fatal"] == 0


RECIPE = """
name: vanilla-look
summary: list, dump and lint the models whose names start with a prefix (read-only)
vars:
  name: {required: true, help: model name prefix}
  out_dir: {required: true}
steps:
  - id: ls
    op: formats.ls
    input: models/gta3.img
    args: {name: "${name}", ext: dff, limit: 5}
  - id: dump
    op: formats.dump
    input: "models/gta3.img/${steps.ls.col.name.0}"
    args: {level: full, limit: 3}
  - id: lint
    op: batch
    args: {op: asset.lint, over: "models/gta3.img/${name}*", out: "${out_dir}/lint.jsonl", jobs: 2,
           arg: ["fail_on=fatal"]}
  - id: report
    op: batch.report
    input: ${steps.lint.out}
    args: {field: [summary.fatal]}
  - id: model
    op: asset.find
    input: ${name}
    args: {kind: model, limit: 3}
    when: "${steps.ls.total}"
"""


@pytest.mark.game
def test_recipe_with_a_variable_on_the_vanilla_copy(clean_root, tmp_path, run_cli):
    f = tmp_path / "vanilla-look.yaml"
    f.write_text(RECIPE, encoding="utf-8")
    out = tmp_path / "report.json"
    stamp = (clean_root / "models" / "gta3.img").stat().st_mtime_ns
    r = run_cli(["recipe", "run", str(f), "--var", "name=infernus", "--var", f"out_dir={tmp_path.as_posix()}",
                 "--out", str(out)])
    assert r.code == 0, r.out
    env = r.json
    assert [row[2] for row in env["rows"]] == ["ok"] * 5
    doc = json.loads(out.read_text(encoding="utf-8"))
    steps = {s["id"]: s for s in doc["steps"]}
    assert steps["ls"]["result"]["rows"][0][1].lower() == "infernus.dff"
    assert steps["dump"]["args"]["target"] == "models/gta3.img/infernus.dff"
    assert steps["lint"]["result"]["counts"]["fail"] == 0
    assert steps["model"]["result"]["rows"][0][0] == "model:411"
    assert (clean_root / "models" / "gta3.img").stat().st_mtime_ns == stamp
    assert sorted(p.name for p in tmp_path.iterdir()) == ["lint.jsonl", "report.json", "vanilla-look.yaml"]


def _mod(root: Path, broken: bool) -> Path:
    d = root / ("bad" if broken else "good")
    d.mkdir()
    (d / "gm.ide").write_text("objs\n18000, gm_box, gm_txd, 100, 0\nend\n", encoding="latin-1")
    (d / "gm_box.dff").write_bytes(synth.broken_dff(0) if broken else synth.dff())
    (d / "gm_txd.txd").write_bytes(synth.txd(("gm_wall",)))
    return d


@pytest.mark.game
def test_check_mod_before_release(tmp_path):
    good = run_recipe("check-mod-before-release", var=[f"mod={_mod(tmp_path, False).as_posix()}"],
                      out=str(tmp_path / "good.json"))
    rows = {r[0]: r for r in good["rows"]}
    assert rows["inspect"][2] == "ok" and rows["check"][2] == "ok" and rows["ids"][2] == "ok"
    assert rows["textures"][2] in ("skipped", "ok", "fail")  # another package may add a texture audit
    with pytest.raises(SatkError) as ei:
        run_recipe("check-mod-before-release", var=[f"mod={_mod(tmp_path, True).as_posix()}"],
                   out=str(tmp_path / "bad.json"))
    assert ei.value.code == "CHECK_FAILED"
    rows = {r[0]: r for r in ei.value.data["rows"]}
    assert rows["check"][2] == "fail" and "fail_if ${steps.check.summary.fatal" in rows["check"][3]
    assert rows["ids"][2] == "ok"  # on_error continue: the recipe went on


@pytest.mark.game
def test_many_cars_lint_and_pack(tmp_path):
    cars = tmp_path / "cars"
    for i, name in enumerate(("alpha", "beta", "gamma")):
        (cars / name).mkdir(parents=True)
        (cars / name / f"{name}.dff").write_bytes(synth.broken_dff(1) if i == 1 else synth.dff())
        (cars / name / f"{name}.txd").write_bytes(synth.txd())
    out_dir = tmp_path / "out"
    with pytest.raises(SatkError) as ei:
        run_recipe("many-cars-lint-and-pack", var=[f"cars={cars.as_posix()}", f"out_dir={out_dir.as_posix()}",
                                                    "img=mycars.img"], out=str(tmp_path / "r.json"))
    rows = {r[0]: r for r in ei.value.data["rows"]}
    assert rows["lint_dff"][2] == "fail" and "1 of 3 input(s) failed" in rows["lint_dff"][3]
    assert rows["lint_txd"][2] == "ok" and rows["pack"][2] == "ok" and rows["verify"][2] == "ok"
    assert "6 row(s)" in rows["verify"][3]
    assert sorted(p.name for p in out_dir.iterdir()) == ["lint-dff.jsonl", "lint-txd.jsonl", "mycars.img"]
    fails = [r for r in _lines(out_dir / "lint-dff.jsonl") if r["status"] == "fail"]
    assert [r["item"].rsplit("/", 1)[1] for r in fails] == ["beta.dff"]


def test_export_all_textures_of_a_mod(tmp_path, satk_home):
    pytest.importorskip("PIL")
    mod = tmp_path / "mymod"
    (mod / "sub").mkdir(parents=True)
    (mod / "a.txd").write_bytes(synth.txd(("wall", "roof")))
    (mod / "sub" / "b.txd").write_bytes(synth.txd(("door",)))
    (mod / "pack.img").write_bytes(synth.img([("c.txd", synth.txd(("sign",))), ("x.dff", synth.dff())]))
    env = run_recipe("export-all-textures-of-a-mod", var=[f"mod={mod.as_posix()}"])
    assert [r[2] for r in env["rows"]] == ["skipped", "ok", "ok"]
    base = satk_home / "work" / "out" / "texmod" / "mymod"
    assert sorted(p.name for p in base.iterdir()) == ["a", "b", "c"]
    assert sorted(p.name for p in (base / "a").glob("*.png")) == ["roof.png", "wall.png"]
    assert (base / "c" / "sign.png").is_file() and (base / "c" / "texmod.json").is_file()
    single = run_recipe("export-all-textures-of-a-mod", var=[f"mod={(mod / 'a.txd').as_posix()}"])
    assert [r[2] for r in single["rows"]] == ["ok", "skipped", "skipped"]
    assert (satk_home / "work" / "out" / "texmod" / "a" / "wall.png").is_file()
    assert jpath(satk_home / "work" / "out" / "recipes" / "export-all-textures-of-a-mod.json") == single["out"]
