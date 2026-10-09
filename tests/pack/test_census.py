"""The dedup census on the synthetic game (numbers worked out by hand from synth.CENSUS_TXDS) and its files.

Textures (DXT1, 8x8, 4 levels: base 32 bytes, chain 56 bytes), name:seed (same seed = same base level):
boxes wall:1 roof:2 door:3 | shops wall:1 brick:1 sign:4 wall:1 | sheds wall:2 roof:2 | lone unique:9 |
stubs wall:1 with another mip tail. 11 textures, 352 base bytes, 616 chain bytes.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from satk.core.registry import invoke
from satk.pack import census as C


@pytest.fixture
def result(ws):
    return C.run_census("vanilla")


def test_level_bytes_and_chains():
    assert C.level_bytes("DXT1", 8, 8, 0) == 32 and C.level_bytes("DXT1", 8, 8, 1) == 8
    assert C.level_bytes("DXT1", 8, 8, 5) == 8                                   # a level below 4x4 is still a block
    assert C.level_bytes("DXT5", 16, 4, 0) == 64 and C.level_bytes("DXT3", 1, 1, 0) == 16
    assert C.level_bytes("A8R8G8B8", 4, 2, 0) == 32 and C.level_bytes("R5G6B5", 3, 3, 0) == 18
    assert C.level_bytes("PAL4", 3, 3, 0) == 5 and C.level_bytes("L8", 1, 1, 3) == 1
    assert C.level_bytes("MYSTERY", 4, 4, 0) is None
    assert C.chain_bytes("DXT1", 256, 256, 9, 32768) == 32768 + 8192 + 2048 + 512 + 128 + 32 + 8 + 8 + 8
    assert C.chain_bytes("DXT1", 8, 8, 1, 32) == 32 and C.chain_bytes("MYSTERY", 8, 8, 4, 100) == 100


def test_global_numbers(result):
    s = result["summary"]
    assert (s["txds"], s["textures"], s["mip0_bytes"], s["chain_bytes"]) == (5, 11, 352, 616)
    assert s["chain_formula_mismatch"] == 0 and s["exact_mips"] is False
    k = s["keys"]
    assert (k["texel"]["distinct"], k["texel"]["unique_mip0_bytes"], k["texel"]["saved_mip0_bytes"]) == (5, 160, 192)
    assert (k["texel_name"]["distinct"], k["texel_name"]["unique_mip0_bytes"]) == (7, 224)
    assert (k["full"]["distinct"], k["full"]["unique_mip0_bytes"], k["full"]["unique_chain_bytes"]) == (7, 224, 392)
    assert k["full"]["saved_mip0_bytes"] == 128 and k["full"]["saved_chain_bytes"] == 224
    assert k["full"]["saved_pct_mip0"] == round(100 * 128 / 352, 1) and k["full"]["saved_pct_chain"] == round(100 * 224 / 616, 1)
    assert s["name_key_splits"] == {}
    assert s["copies_histogram"] == {"1": 5, "2": 1, "3-5": 1, "6-10": 0, "11+": 0} and s["duplicated_groups"] == 2
    assert s["full_key_within_txd_only"]["saved_chain_bytes"] == 56            # only shops' own duplicate wall
    assert s["by_format"] == {"DXT1": {"textures": 11, "chain_bytes": 616, "unique_chain_bytes": 392}}
    assert len(s["index_content_hash"]) == 64


def test_groups(result):
    g = result["groups"]
    assert [(x["rank"], x["name"], x["copies"], x["txds"], x["saved_chain_bytes"], x["saved_mip0_bytes"]) for x in g] == [
        (1, "wall", 4, 3, 168, 96), (2, "roof", 2, 2, 56, 32)]
    assert g[0]["in"] == ["boxes", "shops", "stubs"] and g[1]["in"] == ["boxes", "sheds"]
    assert (g[0]["fmt"], g[0]["w"], g[0]["h"], g[0]["levels"]) == ("DXT1", 8, 8, 4)
    assert len(g[0]["hash"]) == 24


def test_per_txd(result):
    t = {r["txd"]: r for r in result["txds"]}
    assert set(t) == {"boxes", "shops", "sheds", "lone", "stubs"}
    shops = t["shops"]
    assert (shops["textures"], shops["distinct"], shops["dup_in_txd"], shops["dup_in_txd_bytes"]) == (4, 3, 1, 56)
    assert (shops["shared_keys"], shops["shared_bytes"], shops["exclusive_bytes"]) == (1, 56, 112)
    assert t["boxes"]["shared_keys"] == 2 and t["boxes"]["shared_bytes"] == 112 and t["boxes"]["exclusive_bytes"] == 56
    assert (t["sheds"]["shared_bytes"], t["sheds"]["exclusive_bytes"]) == (56, 56)
    assert (t["lone"]["shared_keys"], t["lone"]["exclusive_bytes"], t["lone"]["shared_pct"]) == (0, 56, 0.0)
    assert (t["stubs"]["shared_bytes"], t["stubs"]["shared_pct"]) == (56, 100.0)
    assert shops["shared_pct"] == round(100 * 56 / (224 - 56), 1)               # of the bytes after the TXD's own copies
    assert sum(r["textures"] for r in result["txds"]) == 11


def test_exact_hashes_see_the_mip_tail(ws):
    r = C.run_census("vanilla", exact=True)
    s = r["summary"]
    assert s["exact_mips"] is True and r["warn"] == []
    assert s["keys"]["texel"]["distinct"] == 6 and s["keys"]["full"]["distinct"] == 8        # wall:1 splits in two
    assert s["keys"]["full"]["unique_mip0_bytes"] == 256
    assert s["exact_vs_index"] == {"split_groups": 1, "lost_mip0_bytes": 32, "lost_chain_bytes": 56}
    g = {x["name"]: x for x in r["groups"]}
    assert g["wall"]["copies"] == 3 and g["wall"]["in"] == ["boxes", "shops"]


def test_op_table_files_and_determinism(ws):
    env = invoke("pack.census", {})
    assert env["ok"] and env["cols"][:3] == ["rank", "name", "fmt"] and env["total"] == 2
    assert env["rows"][0][:2] == [1, "wall"] and env["rows"][0][-1] == "boxes, shops, stubs"
    files = {k: Path(v) for k, v in env["files"].items()}
    assert set(files) == {"census.json", "per_txd.csv", "groups.csv"}
    assert all(p.is_file() and "/work/out/pack/census/vanilla/" in str(p).replace("\\", "/") for p in files.values())
    before = {k: p.read_bytes() for k, p in files.items()}
    mtimes = {k: p.stat().st_mtime_ns for k, p in files.items()}
    again = invoke("pack.census", {})
    assert {k: Path(v).read_bytes() for k, v in again["files"].items()} == before          # deterministic
    assert {k: p.stat().st_mtime_ns for k, p in files.items()} == mtimes                    # and not rewritten
    data = json.loads(before["census.json"])
    assert data["summary"]["textures"] == 11
    rows = list(csv.reader(before["per_txd.csv"].decode("utf-8").splitlines()))
    assert rows[0][:4] == ["txd", "ns", "textures", "mip0_bytes"] and len(rows) == 6
    groups = list(csv.reader(before["groups.csv"].decode("utf-8").splitlines()))
    assert groups[1][1] == "wall" and groups[1][-1] == "boxes;shops;stubs"
    assert b"\r" not in b"".join(before.values())
    txd = invoke("pack.census", {"by": "txd", "limit": 2})
    assert txd["cols"][0] == "txd" and txd["n"] == 2 and txd["total"] == 5 and txd["next"] == "o2"
    assert txd["rows"][0][0] == "boxes"                                    # most shared bytes first (112, tie: chain)
    page2 = invoke("pack.census", {"by": "txd", "limit": 2, "cursor": txd["next"]})
    assert page2["n"] == 2 and page2["rows"][0][0] != "boxes"
    ex = invoke("pack.census", {"exact": True})
    assert "/census/vanilla-exact/" in ex["files"]["census.json"] and ex["summary"]["exact_mips"] is True


def test_cli_prints_the_summary(ws, run_cli):
    r = run_cli(["pack", "census", "--json"])
    assert r.code == 0
    env = r.json
    assert env["summary"]["keys"]["full"]["distinct"] == 7


def test_missing_index_is_a_hinted_error(satk_home):
    env = invoke("pack.census", {"profile": "vanilla"})
    assert env["ok"] is False and env["error"]["code"] == "INDEX_MISSING" and "index build" in env["error"]["hint"]
