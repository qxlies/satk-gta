"""satk.txdopt against the vanilla game copy (read-only; marker ``game``).

* every vanilla TXD rebuilds bit-exact, and the planner leaves every texture alone with ``--dxt keep --no-pot``;
* the DFF string scan finds every material texture/mask name the index knows;
* opaque vanilla DXT3 textures transcode to DXT1 with identical RGB;
* ``--drop-unused`` never drops a texture the index resolves for a model, and keeps weapon TXDs whole;
* the budget of all vanilla models equals the sizes of their IMG blobs through ``model_files``;
* ``bistro.txd`` (26 uncompressed textures) shrinks by more than 85 % at a PSNR around 36 dB.

Outputs go to the isolated ``satk_home`` workspace or ``tmp_path``, never the real ``work``.
"""

from __future__ import annotations

import dataclasses
import os
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from satk.core import config as _config
from satk.formats.dxt import decode_rgba
from satk.formats.img import ImgArchive
from satk.formats.rw import rw_payload_size
from satk.formats.txd import parse_txd
from satk.txdopt.optimize import Encoder, Opts
from satk.txdopt.txdedit import dxt_to_dxt1, level_list, native_spans, rebuild

pytestmark = pytest.mark.game


def _skip(reason: str):
    if os.environ.get("SATK_TEST_NO_SKIP") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def game() -> Path:
    root = Path(_config.build().paths.game)
    if not (root / "models" / "gta3.img").is_file():
        _skip(f"game copy not found: {root}")
    return root


@pytest.fixture(scope="module")
def vanilla_txds(game) -> list[tuple[str, bytes]]:
    out = []
    for img in ("gta3.img", "gta_int.img"):
        with ImgArchive.open(game / "models" / img) as a:
            for e in a.entries:
                if e.name.lower().endswith(".txd"):
                    raw = a.read(e)
                    n = rw_payload_size(raw)
                    out.append((f"{img}/{e.name}", bytes(raw[:n]) if n else bytes(raw)))
    for p in sorted((game / "models").rglob("*.txd")):
        out.append((p.relative_to(game).as_posix(), p.read_bytes()))
    assert len(out) > 3000
    return out


@pytest.fixture(scope="module")
def index():
    from satk.core.errors import SatkError
    from satk.txdopt.usage import index_conn

    try:
        db, con = index_conn("vanilla")
    except SatkError as e:
        _skip(f"no vanilla index: {e.msg}")
    yield db, con
    con.close()


def test_every_vanilla_txd_rebuilds_bit_exact_and_needs_no_change(vanilla_txds):
    enc = Encoder(Opts(dxt="keep", pot=False))
    planned = 0
    for label, data in vanilla_txds:
        try:
            txd = parse_txd(data)
        except Exception:  # noqa: BLE001 - the 38 empty TXDs etc. are still parsable; report anything else
            pytest.fail(f"{label} does not parse")
        assert rebuild(data, {}) == data, label
        assert len(native_spans(data)) == len(txd.textures), label
        for t in txd.textures:
            p = enc.plan(data, t, lambda: "opaque" if not t.alpha else "smooth")
            planned += p is not None
            assert p is None or (t.levels > max(t.w, t.h).bit_length()), (label, t.name, p)
    assert planned < 5


def test_dff_scan_finds_every_indexed_material_name(index):
    from satk.index.api import read_blob_bytes
    from satk.txdopt.usage import dff_strings

    db, con = index
    mats: dict[int, set[str]] = {}
    for did, tex, mask in con.execute("SELECT dff_id, texture, mask FROM dff_mat"):
        s = mats.setdefault(did, set())
        s.update(x.lower() for x in (tex, mask) if x)
    rows = con.execute("SELECT d.id, b.stem FROM dff d JOIN blob b ON b.id = d.blob_id WHERE b.active = 1 "
                       "ORDER BY d.id").fetchall()
    checked = 0
    for did, stem in rows[::5]:
        got = dff_strings(read_blob_bytes(db.blob_ref(f"dff:{stem}")))
        assert mats.get(did, set()) <= got, (stem, sorted(mats[did] - got)[:5])
        checked += 1
    assert checked > 2500


def test_opaque_dxt3_transcodes_losslessly(vanilla_txds):
    """Vanilla DXT3 colour blocks (their alpha set to 255: no vanilla DXT3 is fully opaque) -> identical RGB."""
    done = 0
    for label, data in vanilla_txds:
        for t in parse_txd(data).textures:
            if t.d3dfmt != "DXT3" or done >= 150:
                continue
            blk = np.frombuffer(level_list(data, t)[0], np.uint8).reshape(-1, 16).copy()
            blk[:, :8] = 255
            level = blk.tobytes()
            px = np.frombuffer(decode_rgba(t, level), np.uint8).reshape(t.h, t.w, 4)
            assert (px[:, :, 3] == 255).all()
            levels = [level]
            t1 = dataclasses.replace(t, d3dfmt="DXT1", alpha=False)
            got = np.frombuffer(decode_rgba(t1, dxt_to_dxt1(levels[0])), np.uint8).reshape(t.h, t.w, 4)
            assert (got[:, :, :3] == px[:, :, :3]).all(), (label, t.name)
            done += 1
    assert done >= 50


def test_drop_unused_never_drops_what_the_index_resolves(index, game):
    from satk.txdopt.inputs import load_bundle
    from satk.txdopt.usage import Usage

    _db, con = index
    resolved = {(r[0].lower(), r[1].lower()) for r in con.execute(
        "SELECT t.name, x.name FROM model_tex mt JOIN texture x ON x.id = mt.texture_id JOIN txd t ON t.id = x.txd_id")}
    stems = [r[0].lower() for r in con.execute(
        "SELECT DISTINCT t.name FROM txd t JOIN blob b ON b.id = t.blob_id JOIN model m ON m.txd = t.name "
        "AND m.active = 1 WHERE b.active = 1 AND b.ns = 'main' ORDER BY t.name")]
    with load_bundle("txd:helimagnet") as b:
        u = Usage(b, "vanilla")
        try:
            v = u.verdict("helimagnet")
            assert v.names is not None and not {"barrel_64hv", "copperoxb64", "redallu"} & v.names
            assert u.verdict("sniper").names is None and "weap" in u.verdict("sniper").why
            dropped = 0
            for stem in stems[::12]:
                v = u.verdict(stem)
                if v.names is None:
                    continue
                for (txd, tex) in resolved:
                    if txd == stem:
                        assert tex in v.names, (stem, tex)
                dropped += 1
            assert dropped > 150
        finally:
            u.close()


def test_budget_of_all_vanilla_models_equals_their_img_blobs(index):
    from satk.index.api import open_index
    from satk.txdopt.budget import budget, index_world, tally

    db = open_index("vanilla")
    w = index_world("vanilla")
    blobs: dict[tuple[str, int], int] = {}
    for mid in w.models:
        f = db.model_files(mid)
        for bref in ([f.dff] if f.dff else []) + list(f.txd_chain):
            if bref.path.suffix.lower() == ".img":
                blobs[(bref.path.name.lower(), bref.offset)] = bref.size
    t = tally(w, Counter({m: 1 for m in w.models}))
    assert sum(v[0] for v in t.values()) == sum(blobs.values()) and len(t) == len(blobs)
    env = budget("vanilla", area=[2495, -1666, 150])
    s = env["summary"]
    assert 5 * 2**20 < s["total"] < 50 * 2**20 and s["delta"] == 0 and s["limit"]["bytes"] == 52_428_800
    assert env["rows"][0][2] >= env["rows"][-1][2] and s["instances"] > 100


def test_bistro_shrinks_with_good_psnr(game, satk_home, run_cli, tmp_path):
    with ImgArchive.open(game / "models" / "gta3.img") as a:
        raw = a.read(a.find("bistro.txd"))
    src = tmp_path / "bistro.txd"
    src.write_bytes(raw[:rw_payload_size(raw)])
    r = run_cli(["texture", "optimize", str(src), "--max", "128", "--json"])
    env = r.json
    assert r.code == 0 and env["ok"], r.out
    s = env["summary"]
    assert s["saved_pct"] > 85 and s["psnr_mean"] >= 35 and s["recompressed"] == 26
    assert env["lint"].get("error", 0) == 0 and env["lint"].get("fatal", 0) == 0
    out = parse_txd((Path(env["out"]) / "bistro.txd").read_bytes())
    assert {t.d3dfmt for t in out.textures} <= {"DXT1", "DXT5"} and len(out.textures) == 26
    a = run_cli(["texture", "audit", str(src), "--json"]).json
    summary = {row[0]: row for row in a["summary"]["rows"]}
    assert summary["uncompressed"][1] == 26 and summary["uncompressed"][3] > 1_500_000
