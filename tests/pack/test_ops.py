"""The pack operations through the CLI and the registry: arguments, defaults, errors, paging, exit codes."""

from __future__ import annotations

from pathlib import Path

from satk.core.registry import all_ops, invoke
from satk.pack import saepak as P


def test_operations_are_registered_cli_only():
    ops = {o.name: o for o in all_ops() if o.name.startswith("pack.")}
    assert set(ops) == {"pack.build", "pack.verify", "pack.inspect", "pack.dac", "pack.census"}
    assert all(o.mcp is False and len(o.summary) <= 300 and o.summary_ru for o in ops.values())
    assert ops["pack.build"].params[0].name == "inputs" and ops["pack.build"].params[0].positional


def test_build_defaults_to_work_out_pack(satk_home, mod_dir, run_cli):
    r = run_cli(["pack", "build", str(mod_dir), "--namespace", "demo", "--priority", "7", "--mount", "addon",
                 "--title", "My mod", "--local-only", "--json"])
    assert r.code == 0, r.out
    env = r.json
    out = Path(env["out"])
    assert out == satk_home / "work" / "out" / "pack" / "mymod.saepak" and out.is_file()
    assert Path(env["manifest"]) == out.with_name("mymod.saepak.manifest") and Path(env["manifest"]).is_file()
    assert env["names"] == 5 and env["entries"] == 4 and env["dir_slots"] == 5 and env["chunk_kib"] == 64
    assert env["dedup_saved_bytes"] == (mod_dir / "car.dff").stat().st_size and env["namespace"] == "demo"
    assert any(w.startswith("SKIPPED: 1 file") for w in env["warn"])
    with P.Pack.open(out) as pk:
        d = pk.describe()
        assert (d["mount_mode"], d["priority"], d["title"], d["local_only"], d["dedup_ok"]) == (
            "addon", 7, "My mod", True, True)
    # inspect by bare name finds it in work/out/pack
    ins = run_cli(["pack", "inspect", "mymod", "--json"]).json
    assert ins["ok"] and ins["total"] == 5 and ins["cols"][0] == "name"
    assert ins["rows"][0][:3] == ["car.dff", "car.dff", "dff"] and ins["summary"]["mount_mode"] == "addon"


def test_build_names_and_explicit_out(satk_home, mod_dir, tmp_path, run_cli):
    out = tmp_path / "chosen" / "x.saepak"
    r = run_cli(["pack", "build", str(mod_dir / "car.dff"), str(mod_dir / "car.txd"), "--out", str(out), "--json"])
    assert r.code == 0 and out.is_file(), r.out
    assert r.json["names"] == 2 and r.json["bytes"] == out.stat().st_size
    again = run_cli(["pack", "build", str(mod_dir / "car.dff"), "--out", str(out), "--json"])
    assert again.code != 0 and again.json["error"]["code"] == "EXISTS" and "--force" in again.json["error"]["hint"]
    forced = run_cli(["pack", "build", str(mod_dir / "car.dff"), "--out", str(out), "--force", "--json"])
    assert forced.code == 0 and forced.json["names"] == 1
    # a folder as --out, and --name for the file name
    into = run_cli(["pack", "build", str(mod_dir), "--out", str(tmp_path / "chosen") + "/", "--name", "My Mod!",
                    "--no-manifest", "--chunk-kib", "4", "--flat", "--json"])
    assert into.code == 0, into.out
    assert Path(into.json["out"]).name == "my_mod.saepak" and "manifest" not in into.json
    assert into.json["chunk_kib"] == 4 and into.json["names"] == 5          # flat: sub/extra.dff and col/car.col by their file names
    assert not Path(into.json["out"]).with_name("my_mod.saepak.manifest").exists()


def test_build_errors(satk_home, mod_dir, tmp_path, run_cli):
    for argv, code in ((["pack", "build", str(tmp_path / "nope")], "NOT_FOUND"),
                       (["pack", "build", str(mod_dir), "--chunk-kib", "3"], "BAD_PARAMS"),
                       (["pack", "build", str(mod_dir), "--mount", "weird"], "BAD_PARAMS"),
                       (["pack", "build", str(mod_dir), "--fast", "xxh3"], "DEPENDENCY")):
        r = run_cli([*argv, "--json"])
        assert r.code != 0 and r.json["error"]["code"] == code, (argv, r.out)
    clean = satk_home / "gta-sa-clean"
    clean.mkdir()
    r = run_cli(["pack", "build", str(mod_dir), "--out", str(clean / "x.saepak"), "--json"])
    assert r.code != 0 and r.json["error"]["code"] in ("PROTECTED_PATH", "READ_ONLY") and not (clean / "x.saepak").exists()


def test_inspect_views(satk_home, mod_dir, tmp_path):
    out = tmp_path / "v.saepak"
    assert invoke("pack.build", {"inputs": [str(mod_dir)], "out": str(out), "chunk_kib": 2})["ok"]
    names = invoke("pack.inspect", {"target": str(out), "limit": 2})
    assert names["n"] == 2 and names["total"] == 5 and names["next"] == "o2"
    rest = invoke("pack.inspect", {"target": str(out), "limit": 2, "cursor": names["next"]})
    assert [r[0] for r in names["rows"] + rest["rows"]] == ["car.dff", "car.txd", "car_copy.dff",
                                                           "col/car.col"]
    ents = invoke("pack.inspect", {"target": str(out), "show": "entries"})
    assert ents["cols"][0] == "entry" and ents["total"] == 4
    shared = {r[7]: r[6] for r in ents["rows"]}
    assert shared["car.dff"] == 2                                         # two names, one entry
    chunks = invoke("pack.inspect", {"target": str(out), "show": "chunks", "name": "COL/car.col"})
    assert chunks["total"] == 3
    sizes = [r[2] for r in chunks["rows"]]
    assert sizes[:2] == [2048, 2048] and sum(sizes) == (4 + 256 * 20)
    assert invoke("pack.inspect", {"target": str(out), "show": "chunks"})["error"]["code"] == "BAD_PARAMS"
    miss = invoke("pack.inspect", {"target": str(out), "show": "chunks", "name": "nope.dff"})
    assert miss["error"]["code"] == "NOT_FOUND"
    assert invoke("pack.inspect", {"target": str(out), "cursor": "zz"})["error"]["code"] == "BAD_PARAMS"
    # a manifest-only file lists names without stream names; a damaged manifest still opens with a warning
    side = invoke("pack.inspect", {"target": str(out) + ".manifest"})
    assert side["ok"] and side["summary"]["manifest_only"] is True and side["rows"][0][1] == ""
    assert invoke("pack.inspect", {"target": str(tmp_path / "missing.saepak")})["error"]["code"] == "NOT_FOUND"
    junk = tmp_path / "junk.bin"
    junk.write_bytes(bytes(2000))
    assert invoke("pack.inspect", {"target": str(junk)})["error"]["code"] == "UNSUPPORTED"


def test_verify_exit_codes(satk_home, mod_dir, tmp_path, run_cli):
    out = tmp_path / "e.saepak"
    assert invoke("pack.build", {"inputs": [str(mod_dir)], "out": str(out)})["ok"]
    ok = run_cli(["pack", "verify", str(out)])
    assert ok.code == 0
    raw = bytearray(out.read_bytes())
    raw[4096 + 3] ^= 1
    out.write_bytes(raw)
    bad = run_cli(["pack", "verify", str(out), "--json"])
    assert bad.code == 1 and bad.json["error"]["code"] == "CHECK_FAILED"
    assert run_cli(["pack", "verify", str(out), "--no-deep", "--json"]).code == 0
