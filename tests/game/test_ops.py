"""CLI operations of satk.game (registry, envelopes, exit codes) on synthetic data and the real copy."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from satk.core import registry as R
from satk.game import manifests as M

OPS = ("game.verify", "game.info", "game.clone", "game.protect", "game.unprotect", "game.exe")


def test_operations_registered_cli_only():
    names = {o.name: o for o in R.all_ops()}
    for n in OPS:
        assert n in names, n
        assert names[n].mcp_name is None  # SPEC §4.6: no MCP tools for game
        assert names[n].group == "game"
    assert names["game.clone"].param("dry_run").kind == "bool"
    assert names["game.exe"].param("variant").choices == ("stock", "mta")
    assert names["game.verify"].param("against").choices == ("stock", "install")
    assert "game_copy" in R.doctor_checks() and "game" in R.status_providers()


def test_cli_verify_clone_info(fake_game, work, run_cli):
    sj = str(fake_game.stock_json)
    dst = work / "out" / "clean"
    r = run_cli(["game", "clone", "--src", str(fake_game.root), "--dst", str(dst), "--manifest", sj, "--dry-run"])
    assert r.code == 0, r.out
    assert r.json["files"] == fake_game.stock_count and r.json["dry_run"] is True
    assert not dst.parent.exists()
    r = run_cli(["game", "clone", "--src", str(fake_game.root), "--dst", str(dst), "--manifest", sj, "--protect"])
    assert r.code == 0, r.out
    assert r.json["protected"] == "8/8" and r.json["manifest"].endswith("/MANIFEST.sha256")
    r = run_cli(["game", "verify", "--root", str(dst), "--manifest", sj])
    assert r.code == 0, r.out
    j = r.json
    assert (j["files"], j["mismatch"], j["missing"], j["extra"]) == (fake_game.stock_count, 0, 0, 0)
    assert j["bytes"] == fake_game.stock.total_bytes and j["manifest"] == "ok" and j["protected"] == "8/8"
    assert j["mode"] == "fast" and "warn" not in j
    assert run_cli(["game", "verify", "--root", str(dst), "--manifest", sj, "--deep"]).json["mode"] == "deep"
    r = run_cli(["game", "info", "--root", str(dst), "--manifest", sj])
    assert r.code == 0 and r.json["nonstock"] == 0 and r.json["manifest"] is True
    # EXISTS for the now non-empty destination
    r = run_cli(["game", "clone", "--src", str(fake_game.root), "--dst", str(dst), "--manifest", sj])
    assert r.code == 1 and r.json["error"]["code"] == "EXISTS"
    r = run_cli(["game", "unprotect", "--root", str(dst)])
    assert r.code == 0 and r.json["protected"] == "0/8"


def test_cli_verify_reports_revision(fake_game, work, run_cli):
    from satk.game.clone import clone

    dst = work / "c"
    clone(fake_game.root, dst, fake_game.stock)
    M.to_path(dst, "data/gta.dat").write_bytes(b"Z" * fake_game.stock.get("data/gta.dat").size)
    (dst / "extra.txt").write_text("x", encoding="utf-8")
    sj = str(fake_game.stock_json)
    r = run_cli(["game", "verify", "--root", str(dst), "--manifest", sj])
    assert r.code == 1
    err = r.json["error"]
    assert err["code"] == "REVISION"
    assert err["data"]["mismatch"] == 1 and err["data"]["extra"] == 1
    assert err["data"]["rows"] == [["data/gta.dat", "sha256", fake_game.stock.get("data/gta.dat").sha256,
                                    err["data"]["rows"][0][3]]]
    assert any(w.startswith("EXTRA_FILES") for w in err["data"]["warn"])
    r = run_cli(["game", "verify", "--root", str(dst), "--manifest", sj, "--scope", "all"])
    assert r.code == 1 and ["extra.txt", "extra", None, None] in r.json["error"]["data"]["rows"]
    # only the extra file left: a warning, still ok
    M.to_path(dst, "data/gta.dat").write_bytes(M.to_path(fake_game.root, "data/gta.dat").read_bytes())
    r = run_cli(["game", "verify", "--root", str(dst), "--manifest", sj])
    assert r.code == 0 and r.json["extra"] == 1 and r.json["warn"][0].startswith("EXTRA_FILES")


def test_cli_verify_against_install(fake_game, run_cli, satk_home):
    ij = str(fake_game.install_json)
    r = run_cli(["game", "verify", "--root", str(fake_game.root), "--against", "install", "--manifest", ij])
    assert r.code == 0, r.out
    assert (r.json["checked"], r.json["changed"], r.json["missing"]) == (fake_game.stock_count, 0, 0)
    (fake_game.root / "chatlog.txt").write_text("new chat line", encoding="utf-8")
    assert run_cli(["game", "verify", "--root", str(fake_game.root), "--against", "install", "--manifest", ij]).code == 0
    r = run_cli(["game", "verify", "--root", str(fake_game.root), "--against", "install", "--scope", "all",
                 "--manifest", ij])
    assert r.code == 1 and r.json["error"]["data"]["changed"] == 1


def test_cli_exe_refuses_unknown_and_protected(fake_game, work, run_cli):
    r = run_cli(["game", "exe", "--src", str(fake_game.root / "gta_sa.exe"), "--out", str(work / "x.exe")])
    assert r.code == 1 and r.json["error"]["code"] == "UNSUPPORTED"
    assert not (work / "x.exe").exists()


def _refused(r, code="PROTECTED_PATH"):
    assert r.code == (2 if code == "BAD_PARAMS" else 1) and r.json["error"]["code"] == code, r.out
    return r.json["error"]


def test_cli_exe_out_only_under_work(fake_game, work, satk_home, run_cli):
    """Verifier finding: --out D:/Mods/GTA/docs/x.exe was written; now outside work/ is refused."""
    src = ["--src", str(fake_game.root / "gta_sa.exe")]
    docs = satk_home / "docs"
    docs.mkdir()
    repo = work / "wt" / "WP-99"
    (repo / ".git").mkdir(parents=True)  # a git worktree under work/
    for out in (docs / "x.exe", repo / "x.exe", repo / "sub" / "x.exe", satk_home / "gta-sa-clean.exe"):
        err = _refused(run_cli(["game", "exe", *src, "--out", str(out)]))
        assert err["data"]["path"] == str(out).replace("\\", "/")
        assert not out.exists()
    err = _refused(run_cli(["game", "exe", *src, "--out", "\\\\localhost\\D$\\x.exe"]))
    assert "network" in err["msg"]
    _refused(run_cli(["game", "exe", *src, "--out", "\\\\.\\GLOBALROOT\\x.exe"]), "BAD_PARAMS")


def test_cli_writes_refused_behind_aliases(fake_game, satk_home, run_cli):
    """Extended-length spellings of the configured install: clone, protect and exe refuse them."""
    inst = satk_home / "GTA San Andreas"
    (inst / "models").mkdir(parents=True)
    img = inst / "models" / "gta3.img"
    img.write_bytes(b"x")
    alias = "\\\\?\\" + str(inst)
    sj = str(fake_game.stock_json)
    _refused(run_cli(["game", "clone", "--src", str(fake_game.root), "--dst", alias + "\\nested", "--manifest", sj,
                      "--dry-run"]))
    _refused(run_cli(["game", "protect", "--root", alias]))
    _refused(run_cli(["game", "exe", "--src", str(fake_game.root / "gta_sa.exe"), "--out", alias + "\\gta_sa.exe",
                      "--force"]))
    assert not (inst / "nested").exists() and not (inst / "gta_sa.exe").exists()
    assert os.stat(img).st_mode & stat.S_IWRITE  # attribute untouched


def test_cli_protect_defaults_to_configured_copy(fake_game, run_cli, satk_home):
    from satk.game.clone import clone

    root = satk_home / "gta-sa-clean"
    clone(fake_game.root, root, fake_game.stock)
    try:
        r = run_cli(["game", "protect"])
        assert r.code == 0 and r.json["root"] == str(root).replace("\\", "/") and r.json["protected"] == "8/8"
        r = run_cli(["game", "protect", "--root", str(satk_home / "GTA San Andreas")])
        assert r.code in (1,) and r.json["error"]["code"] in ("PROTECTED_PATH", "NOT_FOUND")
    finally:
        for rel in M.PROTECT_IMGS:
            os.chmod(M.to_path(root, rel), stat.S_IREAD | stat.S_IWRITE)


def test_status_and_doctor_without_copy(satk_home):
    from satk.game.ops import _doctor, _status

    s = _status(False)
    assert s["ok"] is False and "error" in s
    d = _doctor()
    assert d["status"] == "fail" and "satk game clone" in d["fix"]


def test_bad_params(run_cli, satk_home):
    r = run_cli(["game", "verify", "--against", "nonsense"])
    assert r.code == 2
    r = run_cli(["game", "verify", "--root", str(satk_home / "missing")])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"


# --------------------------------------------------------------------------- real copy (read-only)


@pytest.mark.game
def test_real_status_and_doctor(clean_root):
    from satk.core import config
    from satk.game.ops import _doctor, _status

    config.reset()
    s = _status(False)
    assert s["ok"] is True and s["exe"] == "hoodlum-stock" and s["files"] == 416
    d = _doctor()
    assert d["status"] in ("ok", "warn")  # warn only if someone ran `satk game unprotect`


@pytest.mark.game
def test_real_cli_verify_and_info(run_cli, clean_root):
    r = run_cli(["game", "verify"])
    assert r.code == 0, r.out
    j = r.json
    assert (j["ok"], j["files"], j["bytes"], j["mismatch"], j["missing"]) == (True, 416, 5_029_186_364, 0, 0)
    r = run_cli(["game", "info"])
    assert r.code == 0
    assert (r.json["exe"], r.json["asi"], r.json["nonstock"]) == ("hoodlum-stock", 0, 0)
    # data/exe_versions.json (PORTABILITY §3): verified hash, 1.0 US layout
    assert (r.json["variant"], r.json["layout"], r.json["re_supported"], r.json["variant_match"]) == \
        ("1.0 US HOODLUM", "1.0us", True, "sha256")
    assert "warn" not in r.json


def test_info_reports_the_variant(fake_game, run_cli):
    r = run_cli(["game", "info", "--root", str(fake_game.root), "--manifest", str(fake_game.stock_json)])
    assert r.code == 0, r.out
    assert r.json["variant"] == "unknown" and r.json["re_supported"] is False
    assert r.json["warn"][0].startswith("UNSUPPORTED_EXE:")


@pytest.mark.game
def test_real_cli_exe_to_tmp(run_cli, clean_root, real_work):
    out = real_work / "gta_sa_mta.exe"
    r = run_cli(["game", "exe", "--variant", "mta", "--out", str(out)])
    assert r.code == 0, r.out
    assert r.json["sha256"] == "f63fa623d14e170d0ae9e7032186669cf3a177793be21c4824043f0279bd506a"
    assert r.json["written"] is True
    r = run_cli(["game", "exe", "--variant", "mta", "--out", str(out)])
    assert r.json["written"] is False  # same content: nothing to do
    r = run_cli(["game", "exe", "--variant", "stock", "--out", str(out)])
    assert r.code == 1 and r.json["error"]["code"] == "EXISTS"
    r = run_cli(["game", "exe", "--variant", "stock", "--out", str(out), "--force"])
    assert r.code == 0 and r.json["variant"] == "hoodlum-stock"
    r = run_cli(["game", "exe", "--out", str(Path(clean_root) / "x.exe")])
    assert r.code == 1 and r.json["error"]["code"] == "PROTECTED_PATH"
    assert not (Path(clean_root) / "x.exe").exists()


@pytest.mark.game
@pytest.mark.parametrize("args", [
    ["game", "clone", "--dst", "\\\\?\\{installed}\\nested", "--dry-run"],
    ["game", "clone", "--dst", "{unc_installed}\\nested", "--dry-run"],
    ["game", "clone", "--dst", "{clean}\\nested", "--dry-run"],
    ["game", "protect", "--root", "\\\\?\\{installed}"],
    ["game", "unprotect", "--root", "\\\\?\\{installed}"],
    ["game", "exe", "--variant", "mta", "--out", "\\\\?\\{installed}\\gta_sa.exe", "--force"],
    ["game", "exe", "--variant", "mta", "--out", "{workspace}\\docs\\x.exe"],
])
def test_real_aliases_refused(run_cli, clean_root, installed_root, args):
    """The verifier's repros against the real roots: all PROTECTED_PATH before anything is written."""
    from satk.core.config import load

    inst, clean = os.path.abspath(installed_root), os.path.abspath(clean_root)
    ws = os.path.abspath(load().paths.workspace)
    unc = "\\\\localhost\\" + inst[0] + "$" + inst[2:]  # the administrative share of the same folder
    r = run_cli([a.format(installed=inst, clean=clean, unc_installed=unc, workspace=ws) for a in args])
    assert r.code == 1 and r.json["error"]["code"] == "PROTECTED_PATH", r.out
    assert not (Path(installed_root) / "nested").exists() and not (Path(clean_root) / "nested").exists()
    assert not Path(ws, "docs", "x.exe").exists()
