"""``satk texture audit`` on synthetic TXDs: every issue kind, the summary table, filters and paging."""

from __future__ import annotations

from pathlib import Path


def _audit(run_cli, *argv: str) -> dict:
    r = run_cli(["texture", "audit", *argv, "--json"])
    env = r.json
    assert r.code == 0 and env["ok"], r.out
    return env


def _odd_mod(tmp: Path, synth) -> Path:
    d = tmp / "oddmod"
    (d / "models").mkdir(parents=True)
    img = synth.smooth(64, 64, 1)
    (d / "models" / "a.txd").write_bytes(synth.txd([
        synth.native("big", synth.smooth(256, 256, 2), "DXT1"),                  # oversized with --max 128
        synth.native("raw", synth.smooth(64, 64, 3), "X8R8G8B8", alpha=False),  # uncompressed, no mips
        synth.native("npot", synth.smooth(12, 8, 4), "A8R8G8B8"),               # npot (+ uncompressed)
        synth.native("opaq5", synth.smooth(32, 32, 5), "DXT5"),                 # alpha unused, DXT5 -> DXT1
        synth.dxt1_holes_native("holes"),                                       # black holes
        synth.native("dup", img, "DXT1"),
        synth.native("dup", img, "DXT1"),                                       # duplicate + dup_name
        synth.ps2_native("ps2"),                                                # platform
        synth.native("lonely", synth.smooth(16, 16, 6), "DXT1"),                # unused (model 'thing' does not use it)
    ]))
    (d / "models" / "b.txd").write_bytes(synth.txd([synth.native("copy", img, "DXT1")]))   # duplicate across TXDs
    (d / "thing.ide").write_text("objs\n3000, thing, a, 100, 0\nend\n", encoding="latin-1")
    (d / "models" / "thing.dff").write_bytes(synth.dff(["big", "raw", "npot", "opaq5", "holes", "dup"]))
    return d


def test_every_issue_kind_is_found(ws, run_cli, synth, tmp_path):
    d = _odd_mod(tmp_path, synth)
    env = _audit(run_cli, str(d), "--max", "128", "--unused", "--limit", "100")
    found = {(r[1], r[2]) for r in env["rows"]}
    want = {("big", "oversized"), ("raw", "uncompressed"), ("raw", "no_mips"), ("npot", "npot"),
            ("opaq5", "alpha_unused"), ("holes", "dxt1_holes"), ("dup", "duplicate"), ("dup", "dup_name"),
            ("ps2", "platform"), ("lonely", "unused"), ("copy", "duplicate"), ("big", "no_mips")}
    assert want <= found, want - found
    assert ("dup", "unused") not in found and ("big", "unused") not in found
    summary = {r[0]: r for r in env["summary"]["rows"]}
    assert set(summary) == {r[2] for r in env["rows"]}
    assert summary["oversized"][3] > 0 and summary["no_mips"][3] < 0
    assert summary["alpha_unused"][3] == 32 * 32 // 2 and "lossless" in summary["alpha_unused"][4]
    assert env["txds"] == 2 and env["textures"] == 10 and env["summary"]["cols"] == ["issue", "count", "bytes",
                                                                                      "saving", "fix"]
    # rows are sorted by saving, then bytes
    savings = [r[5] for r in env["rows"]]
    assert savings == sorted(savings, reverse=True)


def test_filter_paging_and_errors(ws, run_cli, synth, tmp_path):
    d = _odd_mod(tmp_path, synth)
    env = _audit(run_cli, str(d), "--issue", "duplicate", "npot", "--limit", "1")
    assert env["n"] == 1 and env["total"] == 3 and env["next"] == "1"
    assert {r[2] for r in env["rows"]} <= {"duplicate", "npot"} and len(env["summary"]["rows"]) > 2
    nxt = _audit(run_cli, str(d), "--issue", "duplicate", "npot", "--limit", "1", "--cursor", "1")
    assert nxt["rows"] != env["rows"]
    r = run_cli(["texture", "audit", str(d), "--issue", "bogus", "--json"])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "oversized" in r.json["error"]["hint"]
    r = run_cli(["texture", "audit", str(d), "--cursor", "x", "--json"])
    assert r.json["error"]["code"] == "BAD_PARAMS"


def test_clean_txd_and_game_txd(ws, run_cli):
    env = _audit(run_cli, "txd:boxes", "--max", "16")
    assert {r[2] for r in env["rows"]} == set() and env["hint"] == "no issue found"
    env = _audit(run_cli, "txd:boxes", "--unused")
    assert [(r[1], r[2]) for r in env["rows"]] == [("oldtex", "unused")]


def test_bloated_mod_summary(ws, mod, run_cli):
    env = _audit(run_cli, str(mod))
    summary = {r[0]: r for r in env["summary"]["rows"]}
    assert summary["uncompressed"][1] == 9 and summary["duplicate"][1] == 3 and summary["npot"][1] == 1
    assert env["bytes"] > 25_000_000


def test_no_mips_skips_one_level_classes(ws, run_cli, synth, tmp_path):
    """Vanilla vehicle, ped and weapon textures have one level: their TXDs get no no_mips rows (counted instead)."""
    d = tmp_path / "carmod"
    (d / "models").mkdir(parents=True)
    for stem in ("mycar", "myped", "mygun", "mybox"):
        (d / "models" / f"{stem}.txd").write_bytes(synth.txd([synth.native("body", synth.smooth(128, 128, 7), "DXT1")]))
    (d / "my.ide").write_text(
        "cars\n9000, mycar, mycar, car, MYCAR, MYCAR, null, normal, 10, 0, 0, -1, 0.7, 0.7, -1\nend\n"
        "peds\n9001, myped, myped, CIVMALE, STAT_COWARD, man, 1983, 0, null, 9, 9, PED_TYPE_GEN, VOICE_GEN_MALE, "
        "VOICE_GEN_MALE\nend\n"
        "weap\n9002, mygun, mygun, colt45, 1, 50, 0\nend\n"
        "objs\n9003, mybox, mybox, 100, 0\nend\n", encoding="latin-1")
    env = _audit(run_cli, str(d), "--issue", "no_mips")
    assert [r[0].rsplit("/", 1)[-1] for r in env["rows"]] == ["mybox.txd"], env["rows"]
    assert env["one_level"] == {"ped": 1, "vehicle": 1, "weapon": 1}
