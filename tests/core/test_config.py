"""Configuration layering (SPEC §2.4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from satk.core import config as C
from satk.core.errors import SatkError

REPO = Path(__file__).resolve().parents[2]


def _env(**kw) -> dict[str, str]:
    base = {"SATK_CONFIG": "none"}
    base.update(kw)
    return base


def test_defaults_point_at_the_workspace(tmp_path):
    ws = Path(C.DEFAULT_WORKSPACE)
    # outside a <workspace>/tools checkout the default is the per-user data dir, taken from the environment
    c = C.build(_env(**{k: os.environ[k] for k in ("LOCALAPPDATA", "XDG_DATA_HOME") if k in os.environ}))
    assert c.sources == ["defaults"]
    assert c.paths.workspace == ws
    assert c.paths.game == ws / "gta-sa-clean"
    assert c.paths.installed == ws / "GTA San Andreas"
    assert c.paths.game_root == c.paths.installed  # satk init points it at the user's game
    assert c.paths.work == ws / "work"
    assert c.paths.premake == ws / "src" / "mtasa-neon" / "utils" / "premake5.exe"
    assert c.get("index.default_profile") == "vanilla"
    assert c.profile("samp").img_order == "samp" and c.profile("samp").dat == ("data/default.two", "data/gta.two")
    for sub in ("GTA San Andreas", "src", "gta-sa-clean"):
        (tmp_path / sub).mkdir()
    c = C.build(_env(SATK_HOME=str(tmp_path)))
    assert c.profile("vanilla").root == c.paths.game and c.profile("game").root == c.paths.installed
    assert set(c.protected_roots) == {c.paths.installed, c.paths.src, c.paths.game}
    assert c.get("viewer.window") == "1280x720" and c.get("nope.x", 7) == 7


def test_tools_checkout_workspace_layout():
    """A ``<workspace>/tools`` checkout keeps the classic layout: every default path is in that workspace."""
    if C.derived_workspace() is None:
        pytest.skip("not a <workspace>/tools checkout")
    ws = Path(C.DEFAULT_WORKSPACE)
    c = C.build(_env())
    assert c.paths.game == ws / "gta-sa-clean" and c.paths.work == ws / "work"
    assert c.profile("vanilla").root == ws / "gta-sa-clean" and c.aliases == {}


def test_satk_home_moves_derived_paths(tmp_path):
    c = C.build(_env(SATK_HOME=str(tmp_path)))
    assert c.paths.work == tmp_path / "work"
    assert c.profile("installed").root == tmp_path / "GTA San Andreas"
    assert "env:SATK_HOME" in c.sources


def test_env_overrides(tmp_path):
    c = C.build(_env(SATK_PATHS_GAME=str(tmp_path / "g"), SATK_VIEWER_WINDOW="1920x1080",
                     SATK_PROFILES_VANILLA_DAT='["data/x.dat"]'))
    assert c.paths.game == tmp_path / "g" and c.profile("vanilla").root == tmp_path / "g"
    assert c.get("viewer.window") == "1920x1080"
    assert c.profile("vanilla").dat == ("data/x.dat",)
    assert "env:SATK_VIEWER_WINDOW" in c.sources


def test_toml_file_and_interpolation(tmp_path):
    f = tmp_path / "satk.toml"
    f.write_text(
        "[paths]\nworkspace = 'E:\\ws'\nviewer = '${paths.work}/v.exe'\n"
        "[profiles.mods]\nroot = '${paths.installed}'\nimg_order = 'engine'\n",
        encoding="utf-8")
    c = C.build({"SATK_CONFIG": str(f)})
    assert c.paths.work == Path(r"E:\ws\work") and c.paths.viewer == Path(r"E:\ws\work\v.exe")
    assert c.profile("mods").root == Path(r"E:\ws\GTA San Andreas")
    assert c.sources[1].endswith("/satk.toml")
    # env beats the file
    c2 = C.build({"SATK_CONFIG": str(f), "SATK_PATHS_WORKSPACE": r"F:\w"})
    assert c2.paths.game == Path(r"F:\w\gta-sa-clean")


def test_config_errors(tmp_path):
    with pytest.raises(SatkError):
        C.build({"SATK_CONFIG": str(tmp_path / "missing.toml")})
    bad = tmp_path / "bad.toml"
    bad.write_text("[paths\n", encoding="utf-8")
    with pytest.raises(SatkError) as ei:
        C.build({"SATK_CONFIG": str(bad)})
    assert ei.value.code == "BAD_PARAMS"
    ref = tmp_path / "ref.toml"
    ref.write_text("[paths]\ngame = '${paths.nope}'\n", encoding="utf-8")
    with pytest.raises(SatkError, match="unknown reference"):
        C.build({"SATK_CONFIG": str(ref)})
    with pytest.raises(SatkError):
        C.build(_env(SATK_PROFILES_VANILLA_DAT="[1"))


def test_example_file_equals_builtin_defaults():
    ex = REPO / "satk.toml.example"
    from_file = C.build({"SATK_CONFIG": str(ex)}).as_dict()
    builtin = C.build(_env()).as_dict()
    assert from_file == builtin


@pytest.mark.parametrize("manifest", [None, r"relative\MANIFEST.sha256", r"D:\data/manifests\vanilla.sha256"])
def test_manifest_json_path_is_absolute_and_uses_forward_slashes(manifest):
    from satk.core.paths import jpath

    env = _env()
    if manifest is not None:
        env["SATK_LAYERS_VANILLA_MANIFEST"] = manifest
    c = C.build(env)
    raw = c.get("layers.vanilla_manifest")
    result = c.as_dict()
    assert result["layers"]["vanilla_manifest"] == jpath(raw)
    assert c.get("layers.vanilla_manifest") == raw  # serialization does not mutate the config
    assert result["layers"]["rules"] == c.get("layers.rules")


def test_config_show_layers_uses_json_paths(satk_home, run_cli, monkeypatch):
    from satk.core.paths import jpath

    manifest = satk_home / "manifests" / "vanilla.sha256"
    monkeypatch.setenv("SATK_LAYERS_VANILLA_MANIFEST", str(manifest))
    C.reset()
    result = run_cli(["config", "show", "--section", "layers"])
    assert result.code == 0
    assert result.json["layers"]["vanilla_manifest"] == jpath(manifest)


def test_worktree_falls_back_to_main_config(tmp_path):
    env = {"SATK_HOME": str(tmp_path / "ws"), "APPDATA": str(tmp_path / "roaming")}
    files = C.config_files(env)
    assert files[0] == C.REPO_ROOT / "satk.toml"
    want = [C.REPO_ROOT / "satk.toml"]
    if C.MAIN_ROOT is not None and C.MAIN_ROOT != C.REPO_ROOT:
        want.append(C.MAIN_ROOT / "satk.toml")  # a git worktree shares the main checkout's file
    if os.name == "nt":
        want += [tmp_path / "ws" / "satk.toml", tmp_path / "roaming" / "satk" / "satk.toml"]
        assert files == want
    assert C.config_files({"SATK_CONFIG": "none"}) == []


def test_satk_config_may_name_a_directory(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    c = C.build({"SATK_CONFIG": str(empty)})  # no satk.toml there: defaults, no error
    assert c.sources == ["defaults"]
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "satk.toml").write_text("[viewer]\nwindow = '800x600'\n", encoding="utf-8")
    c = C.build({"SATK_CONFIG": str(tmp_path / "cfg")})
    assert c.get("viewer.window") == "800x600" and c.sources[-1].endswith("/cfg/satk.toml")


# --------------------------------------------------------------------------- workspace discovery


def _fake_worktree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """<ws>/tools (main checkout, .git dir) + <x> (worktree: .git file -> gitdir -> commondir)."""
    ws = tmp_path / "ws"
    (ws / "work").mkdir(parents=True)
    main = ws / "tools"
    gitdir = main / ".git" / "worktrees" / "x"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    wt = tmp_path / "elsewhere" / "x"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {gitdir.as_posix()}\n", encoding="utf-8")
    return ws, main, wt


def test_git_common_dir_and_main_checkout(tmp_path):
    ws, main, wt = _fake_worktree(tmp_path)
    assert C.git_common_dir(wt) == main / ".git"
    assert C.main_checkout(wt) == main and C.main_checkout(main) == main
    assert C.main_checkout(tmp_path) is None
    rel = tmp_path / "rel"  # relative gitdir in the .git file
    rel.mkdir()
    (rel / ".git").write_text("gitdir: ../ws/tools/.git/worktrees/x", encoding="utf-8")
    assert C.main_checkout(rel) == main


def test_workspace_derived_from_a_worktree_anywhere(tmp_path, monkeypatch):
    ws, main, wt = _fake_worktree(tmp_path)
    monkeypatch.setattr(C, "REPO_ROOT", wt)
    monkeypatch.setattr(C, "MAIN_ROOT", C.main_checkout(wt))
    assert C.derived_workspace() == C.Workspace(ws, "main-checkout")
    monkeypatch.setattr(C, "REPO_ROOT", main)
    monkeypatch.setattr(C, "MAIN_ROOT", main)
    assert C.derived_workspace() == C.Workspace(ws, "checkout")
    (ws / "work").rmdir()  # a "tools" folder without work/ or satk.toml next to it is not a workspace
    assert C.derived_workspace() is None


def test_this_checkout_finds_its_workspace():
    if C.MAIN_ROOT is None or C.MAIN_ROOT.name.lower() != "tools" or not (C.MAIN_ROOT.parent / "work").is_dir():
        pytest.skip("not running from a <workspace>/tools checkout or its worktree")
    c = C.build({"SATK_CONFIG": "none"})
    assert c.paths.workspace == C.MAIN_ROOT.parent
    assert c.workspace_source in ("checkout", "main-checkout")


def test_user_data_dir_is_the_last_resort(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_DERIVED", None)
    env = {"SATK_CONFIG": "none", "LOCALAPPDATA": str(tmp_path / "local"), "XDG_DATA_HOME": str(tmp_path / "xdg")}
    c = C.build(env)
    want = tmp_path / ("local" if os.name == "nt" else "xdg") / "satk"
    assert c.paths.workspace == want and c.workspace_source == "user"
    assert c.paths.work == want / "work"


def test_user_config_points_to_a_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_DERIVED", None)
    monkeypatch.setattr(C, "REPO_ROOT", tmp_path / "repo")
    monkeypatch.setattr(C, "MAIN_ROOT", None)
    ws = tmp_path / "my ws"
    ws.mkdir()
    (ws / "satk.toml").write_text(f"[paths]\nworkspace = '{ws}'\ngame_root = '{tmp_path / 'game'}'\n",
                                  encoding="utf-8")
    env = {"APPDATA": str(tmp_path / "roaming"), "LOCALAPPDATA": str(tmp_path / "local"),
           "XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    uf = C.user_config_file(env)
    uf.parent.mkdir(parents=True)
    uf.write_text(f"[paths]\nworkspace = '{ws}'\n", encoding="utf-8")
    c = C.build(env)
    assert c.paths.workspace == ws and c.workspace_source == "file"
    assert c.paths.game_root == tmp_path / "game"
    assert [s.rsplit("/", 1)[-1] for s in c.sources[1:]] == ["satk.toml", "satk.toml"]


# --------------------------------------------------------------------------- profiles, protected roots


def test_protected_roots_default_to_existing_ones(tmp_path):
    ws = tmp_path / "ws"
    (ws / "GTA San Andreas").mkdir(parents=True)
    c = C.build(_env(SATK_HOME=str(ws)))
    assert c.protected_roots == (ws / "GTA San Andreas",)  # installed = game_root, once
    extra = tmp_path / "not-yet"
    c = C.build(_env(SATK_HOME=str(ws), SATK_SAFETY_PROTECTED_ROOTS=f'["{extra.as_posix()}"]'))
    assert c.protected_roots == (extra,)  # an explicit list is kept as written


def test_game_profile_and_vanilla_alias(tmp_path):
    ws = tmp_path / "ws"
    game = tmp_path / "my game"
    game.mkdir()
    c = C.build(_env(SATK_HOME=str(ws), SATK_PATHS_GAME_ROOT=str(game)))
    assert c.profile("game").root == game and c.profiles["game"].configured
    assert c.aliases == {"vanilla": "game"} and "vanilla" not in c.profiles
    assert c.profile("vanilla").name == "game" and c.default_profile == "game"
    assert c.warnings and c.warnings[0].startswith("NO_CLEAN_COPY:")
    assert not c.profiles["installed"].configured  # shown as "not configured", not an error
    (ws / "gta-sa-clean").mkdir(parents=True)  # with a clean copy vanilla is itself again
    c = C.build(_env(SATK_HOME=str(ws), SATK_PATHS_GAME_ROOT=str(game)))
    assert c.aliases == {} and c.profile("vanilla").root == ws / "gta-sa-clean" and c.default_profile == "vanilla"
    with pytest.raises(SatkError) as ei:
        c.profile("vanila")
    assert "vanilla" in ei.value.did_you_mean


def test_unset_game_root_is_never_the_current_directory(tmp_path):
    c = C.build(_env(SATK_HOME=str(tmp_path), SATK_PATHS_GAME_ROOT=""))
    assert "game" not in c.profiles and c.paths.get("game_root") is None
    assert all(str(r) not in (".", "") for r in c.protected_roots)


# --------------------------------------------------------------------------- discovered tool paths


def test_tool_paths_are_discovered_unless_configured(tmp_path, monkeypatch):
    from satk.core import detect

    exe = tmp_path / "blender.exe"
    exe.write_bytes(b"MZ")
    calls = []

    def fake_tools(cache_dir=None, **kw):
        calls.append(cache_dir)
        return {"blender": detect.Hit(exe, "program_files"), "msbuild": detect.Hit(None, None),
                "vcvars": detect.Hit(None, None)}

    monkeypatch.setattr(detect, "tools", fake_tools)
    monkeypatch.delenv("SATK_DETECT", raising=False)
    c = C.build(_env(SATK_HOME=str(tmp_path)))
    assert c.paths.blender == exe and c.paths.get("blender") == exe
    assert c.path_source("blender") == "detect:program_files"
    assert c.paths.msbuild is None and c.paths.get("msbuild", Path("x")) == Path("x")
    assert c.path_source("msbuild") == "missing" and c.path_source("work") == "default"
    assert c.as_dict()["paths"]["blender"] == exe.as_posix() and "msbuild" not in c.as_dict()["paths"]
    assert len(calls) == 1 and calls[0] == tmp_path / "work" / "cache"  # once per config
    other = tmp_path / "other.exe"
    c = C.build(_env(SATK_HOME=str(tmp_path), SATK_PATHS_BLENDER=str(other)))
    assert c.paths.blender == other and c.path_source("blender") == "env"
    monkeypatch.setenv("SATK_DETECT", "0")
    c = C.build(_env(SATK_HOME=str(tmp_path)))
    assert c.paths.get("blender") is None and len(calls) == 1


def test_cache_and_reset(satk_home, monkeypatch):
    a = C.load()
    assert C.load() is a
    monkeypatch.setenv("SATK_VIEWER_WINDOW", "800x600")
    assert C.load().get("viewer.window") == "1280x720"  # cached
    C.reset()
    assert C.load().get("viewer.window") == "800x600"
