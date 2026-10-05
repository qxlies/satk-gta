"""``satk init`` (docs/en/install.md) on synthetic games, isolated from this machine."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from satk.core import config as C
from satk.core import detect as D


def _game(root: Path, exe: bytes = b"MZ synthetic") -> Path:
    (root / "models").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "gta_sa.exe").write_bytes(exe)
    (root / "models" / "gta3.img").write_bytes(b"VER2\0\0\0\0")
    (root / "data" / "gta.dat").write_text("# synthetic\n", encoding="utf-8")
    return root


@pytest.fixture
def hermetic(satk_home, monkeypatch, tmp_path):
    """No registry/Steam/Program Files candidates, fixed tool discovery, per-user files in tmp."""
    monkeypatch.setattr(D, "_registry_candidates", lambda: [])
    monkeypatch.setattr(D, "steam_libraries", lambda: [])
    monkeypatch.setattr(D, "_TYPICAL", ())
    blender = tmp_path / "Blender" / "blender.exe"
    blender.parent.mkdir()
    blender.write_bytes(b"MZ")
    monkeypatch.setattr(D, "tools", lambda cache_dir=None, **kw: {
        "blender": D.Hit(blender, "program_files"), "msbuild": D.Hit(None, None), "vcvars": D.Hit(None, None)})
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.chdir(tmp_path)
    return satk_home


def test_init_writes_config_for_a_game(hermetic, tmp_path, run_cli):
    game = _game(tmp_path / "Games" / "GTA SA")
    ws = tmp_path / "new ws"
    r = run_cli(["init", "--game", str(game), "--workspace", str(ws), "--yes"])
    assert r.code == 0, r.out
    j = r.json
    assert j["written"] is True and j["config"] == (ws / "satk.toml").as_posix()
    assert j["profile"] == "game" and j["profiles"]["game"] == game.as_posix()
    assert j["aliases"] == {"vanilla": "game"} and j["game"]["variant"] == "unknown"
    assert any(w.startswith("UNSUPPORTED_EXE") for w in j["warn"])
    assert any(w.startswith("NO_CLEAN_COPY") for w in j["warn"])
    assert j["tools"]["blender"]["source"] == "program_files" and j["tools"]["msbuild"] is None
    data = tomllib.loads((ws / "satk.toml").read_text(encoding="utf-8"))
    assert data["paths"]["game_root"] == str(game) and data["paths"]["workspace"] == str(ws)
    assert data["index"]["default_profile"] == "game" and "msbuild" not in data["paths"]
    assert (ws / "work").is_dir()
    assert not (tmp_path / "roaming" / "satk").exists()  # SATK_HOME/SATK_CONFIG set: no pointer
    c = C.build({"SATK_CONFIG": str(ws)})  # the written file loads back
    assert c.profile("game").root == game and c.profile("vanilla").root == game and c.default_profile == "game"
    # same again: unchanged; another game: EXISTS without --yes, with a diff
    r = run_cli(["init", "--game", str(game), "--workspace", str(ws)])
    assert r.code == 0 and r.json["written"] is False and r.json.get("unchanged") is True
    other = _game(tmp_path / "other")
    r = run_cli(["init", "--game", str(other), "--workspace", str(ws)])
    assert r.code == 1 and r.json["error"]["code"] == "EXISTS"
    assert any(str(other) in line for line in r.json["error"]["data"]["diff"])
    r = run_cli(["init", "--game", str(other), "--workspace", str(ws), "--yes"])
    assert r.code == 0 and r.json["written"] is True


def test_init_candidates_ambiguous_and_dry_run(hermetic, run_cli):
    ws = hermetic
    a = _game(ws / "GTA San Andreas")  # also paths.installed / game_root of this workspace
    b = _game(ws / "copy-b")
    r = run_cli(["init", "--dry-run"])
    assert r.code == 0, r.out
    roots = {c["root"] for c in r.json["candidates"]}
    assert roots == {a.as_posix(), b.as_posix()} and r.json["written"] is False
    assert r.json["next"][0].startswith("satk init --game")
    assert not (ws / "satk.toml").exists()
    r = run_cli(["init"])
    assert r.code == 1 and r.json["error"]["code"] == "AMBIGUOUS"
    assert set(r.json["error"]["did_you_mean"]) == roots
    b_exe = b / "gta_sa.exe"
    b_exe.unlink()  # only one usable game left: taken without asking
    (b / "gta-sa.exe").write_bytes(b"MZ")
    (b / "models" / "gta3.img").unlink()
    r = run_cli(["init", "--yes"])
    assert r.code == 0, r.out
    assert r.json["game"]["root"] == a.as_posix() and (ws / "satk.toml").is_file()


def test_init_refuses_bad_folders(hermetic, tmp_path, run_cli):
    r = run_cli(["init", "--game", str(tmp_path / "missing")])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    half = tmp_path / "half"
    half.mkdir()
    (half / "gta_sa.exe").write_bytes(b"MZ")
    r = run_cli(["init", "--game", str(half)])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"
    assert "no models/gta3.img" in r.json["error"]["data"]["problems"]
    game = _game(tmp_path / "g")
    r = run_cli(["init", "--game", str(game), "--workspace", str(game / "ws")])
    assert r.code == 2 and "inside the game folder" in r.json["error"]["msg"]
    assert not (game / "ws").exists()


def test_init_clean_copy_needs_a_stock_game(hermetic, tmp_path, run_cli):
    game = _game(tmp_path / "g")
    r = run_cli(["init", "--game", str(game), "--workspace", str(tmp_path / "ws2"), "--clean-copy", "--dry-run"])
    assert r.code == 0, r.out
    cc = r.json["clean_copy"]
    assert cc["status"] == "impossible" and "modded" in cc["why"]
    assert not (tmp_path / "ws2").exists()


def test_init_writes_a_pointer_for_an_undiscoverable_workspace(hermetic, tmp_path, run_cli, monkeypatch):
    monkeypatch.delenv("SATK_HOME")
    monkeypatch.delenv("SATK_CONFIG")
    monkeypatch.setattr(C, "REPO_ROOT", tmp_path / "repo")
    monkeypatch.setattr(C, "MAIN_ROOT", None)
    monkeypatch.setattr(C, "_DERIVED", C.Workspace(hermetic, "checkout"))
    C.reset()
    game = _game(tmp_path / "g")
    ws = tmp_path / "elsewhere"
    r = run_cli(["init", "--game", str(game), "--workspace", str(ws)])
    assert r.code == 0, r.out
    uf = tmp_path / "roaming" / "satk" / "satk.toml"
    assert r.json["pointer"] == uf.as_posix() and tomllib.loads(uf.read_text(encoding="utf-8")) == {
        "paths": {"workspace": str(ws)}}
    C.reset()
    c = C.load()  # found again without SATK_HOME: pointer + the workspace's own file
    assert c.paths.workspace == ws and c.profile("game").root == game


def test_render_toml_quotes_any_path():
    from satk.runtime.setup import render_toml

    text = render_toml({"workspace": r"C:\it's here", "game_root": r"D:\G"}, default_profile="game")
    d = tomllib.loads(text)
    assert d["paths"]["workspace"] == r"C:\it's here" and d["paths"]["game_root"] == r"D:\G"


# --------------------------------------------------------------------------- first run (M3 A2)


def _isolated_env(monkeypatch, tmp_path):
    """Windows folders of this test only: no real OneDrive / Program Files / VirtualStore."""
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial", "ProgramW6432"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "Program Files (x86)"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))


def test_init_cyrillic_and_spaces(hermetic, tmp_path, run_cli, monkeypatch):
    _isolated_env(monkeypatch, tmp_path)
    game = _game(tmp_path / "Игры" / "GTA San Andreas (мой)")
    ws = tmp_path / "Мои моды" / "рабочая папка satk"
    r = run_cli(["init", "--game", str(game), "--workspace", str(ws)])
    assert r.code == 0, r.out
    assert r.json["written"] is True and r.json["game"]["root"] == game.as_posix()
    assert not [w for w in r.json.get("warn", []) if w.split(":")[0] in (
        "WORKSPACE_ONEDRIVE", "WORKSPACE_PROGRAM_FILES", "GAME_ONEDRIVE", "GAME_VIRTUALSTORE")]
    data = tomllib.loads((ws / "satk.toml").read_text(encoding="utf-8"))
    assert data["paths"]["game_root"] == str(game) and data["paths"]["workspace"] == str(ws)
    c = C.build({"SATK_CONFIG": str(ws)})
    assert c.profile("game").root == game and c.paths.workspace == ws
    tty = run_cli(["init", "--game", str(game), "--workspace", str(ws)], tty=True)
    assert tty.code == 0 and "Игры" in tty.out  # the table output keeps the Cyrillic path


def test_init_refuses_a_workspace_inside_the_game(hermetic, tmp_path, run_cli, monkeypatch):
    game = _game(tmp_path / "GTA San Andreas")
    r = run_cli(["init", "--game", str(game), "--workspace", str(game / "satk")])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"
    err = r.json["error"]
    assert "inside the game folder" in err["msg"] and "never writes into a game folder" in err["msg"]
    assert err["hint"].startswith("choose a folder outside the game: satk init --workspace ")
    r = run_cli(["init", "--game", str(game), "--workspace", str(game)])
    assert r.code == 2 and "is the game folder" in r.json["error"]["msg"]
    # satk unpacked into the game folder: the derived workspace is refused even without --game
    monkeypatch.setenv("SATK_HOME", str(game / "satk-0.2"))
    C.reset()
    r = run_cli(["init", "--dry-run"])
    assert r.code == 2 and "inside the game folder" in r.json["error"]["msg"]
    assert r.json["error"]["data"]["source"] != "--workspace"
    assert sorted(p.name for p in game.iterdir()) == ["data", "gta_sa.exe", "models"]  # nothing written


def test_init_warns_about_onedrive_program_files_and_virtualstore(hermetic, tmp_path, run_cli, monkeypatch):
    _isolated_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))
    game = _game(tmp_path / "Program Files (x86)" / "Rockstar Games" / "GTA San Andreas")
    vs = tmp_path / "Local" / "VirtualStore" / os.path.splitdrive(str(game))[1].lstrip("\\/") / "data"
    vs.mkdir(parents=True)
    (vs / "gta.dat").write_text("redirected", encoding="utf-8")
    r = run_cli(["init", "--game", str(game), "--workspace", str(tmp_path / "OneDrive" / "satk"), "--dry-run"])
    assert r.code == 0, r.out
    codes = {w.split(":")[0] for w in r.json["warn"]}
    assert {"WORKSPACE_ONEDRIVE", "GAME_VIRTUALSTORE"} <= codes and "GAME_ONEDRIVE" not in codes
    r = run_cli(["init", "--game", str(game), "--workspace", str(tmp_path / "Program Files" / "satk"), "--dry-run"])
    codes = {w.split(":")[0] for w in r.json["warn"]}
    assert "WORKSPACE_PROGRAM_FILES" in codes and "WORKSPACE_ONEDRIVE" not in codes
    od_game = _game(tmp_path / "Users" / "x" / "OneDrive - Contoso" / "GTA SA")  # by folder name, no env var
    r = run_cli(["init", "--game", str(od_game), "--workspace", str(tmp_path / "ws3"), "--dry-run"])
    codes = {w.split(":")[0] for w in r.json["warn"]}
    assert "GAME_ONEDRIVE" in codes and "WORKSPACE_ONEDRIVE" not in codes


def test_init_warns_on_low_disk(hermetic, tmp_path, run_cli, monkeypatch):
    from satk.runtime import location as L

    _isolated_env(monkeypatch, tmp_path)
    monkeypatch.setattr(L, "free_gb", lambda p: 0.4)
    game = _game(tmp_path / "g")
    r = run_cli(["init", "--game", str(game), "--workspace", str(tmp_path / "ws4"), "--dry-run"])
    low = [w for w in r.json["warn"] if w.startswith("LOW_DISK")]
    assert low and "0.40 GB free" in low[0]


def _definitive(root: Path) -> Path:
    exe = root / "Gameface" / "Binaries" / "Win64" / "SanAndreas.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ de")
    return root


def _mobile(root: Path) -> Path:
    (root / "texdb").mkdir(parents=True)
    (root / "models").mkdir()
    (root / "data").mkdir()
    (root / "models" / "gta3.img").write_bytes(b"VER2")
    (root / "data" / "gta.dat").write_text("x", encoding="utf-8")
    return root


def test_init_rejects_definitive_and_mobile_editions(hermetic, tmp_path, run_cli):
    de = _definitive(tmp_path / "GTA San Andreas - Definitive Edition")
    mob = _mobile(tmp_path / "com.rockstargames.gtasa" / "files")
    assert D.edition(de) == "definitive" and D.edition(mob) == "mobile" and D.edition(tmp_path) is None
    for root, word in ((de, "Definitive Edition"), (mob, "mobile port")):
        r = run_cli(["init", "--game", str(root), "--workspace", str(tmp_path / "ws5")])
        assert r.code == 1 and r.json["error"]["code"] == "UNSUPPORTED", r.out
        assert word in r.json["error"]["msg"] and "classic PC game" in r.json["error"]["msg"]
    assert not (tmp_path / "ws5").exists()


def test_discovery_lists_unsupported_editions(hermetic, run_cli):
    ws = hermetic
    _definitive(ws / "GTA San Andreas - Definitive Edition")
    r = run_cli(["init", "--dry-run"])
    assert r.code == 0, r.out
    (cand,) = r.json["candidates"]
    assert cand["edition"] == "definitive" and "not supported" in cand["problems"][0]
    assert any("only unsupported editions" in w for w in r.json["warn"])
    r = run_cli(["init"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND" and "Definitive Edition" in r.json["error"]["msg"]
    classic = _game(ws / "GTA San Andreas")
    r = run_cli(["init", "--yes"])  # the classic game is taken, the DE folder is only listed
    assert r.code == 0 and r.json["game"]["root"] == classic.as_posix()
