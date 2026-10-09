"""``satk engine worktree``: path rules, the computed sparse profile, patterns, and a real git round trip.

The fork here is a small synthetic repository with premake files shaped like MTA's; the real git (not a mock)
checks that the patterns materialise exactly the wanted files.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.engine import worktree as W
from satk.engine.common import layout

ROOT_PREMAKE = '''-- synthetic root script
premake.path = premake.path..";utils/buildactions"
require "compose_files"

workspace "MTASA"
	includedirs {
		"vendor",
	}
	if os.target() == "windows" then
		group "Client"
		include "Client/core"
		include "Client/mods/deathmatch"

		group "Vendor"
		include "vendor/cegui"
		include "vendor/googletest"

		if MTA_MAETRO then
			include "vendor/maetro32"
		end

		group "Tests"
		include "Tests/client"
	end

	filter {}
		group "Server"
		include "Server/core"
		include "Server/mods/deathmatch"
		include "Server/sdk"

		group "Shared"
		include "Shared"

		group "Vendor"
		include "vendor/zlib"
		include "vendor/lua"
'''

FILES = {
    "premake5.lua": ROOT_PREMAKE,
    "README.md": "readme",
    "docs/note.md": "doc",
    "utils/buildactions/compose_files.lua": "-- x",
    "utils/premake5.exe": "MZ",
    "utils/xgettext.exe": "MZ",
    "utils/breakpad/dump_syms": "bin",
    "Client/core/premake5.lua": 'project "Client Core"\n  files { "*.cpp" }\n',
    "Client/core/CCore.cpp": "x",
    "Client/mods/deathmatch/premake5.lua": 'project "Client Deathmatch"\n',
    "Client/mods/deathmatch/logic.cpp": "x",
    "Client/sdk/core/CCoreInterface.h": "x",
    "Client/loose.txt": "x",
    "Server/core/premake5.lua": 'project "Server Core"\n  includedirs { "../../Shared/sdk", "../sdk", "../../vendor/sparsehash/src/" }\n'
                                '  links { "zlib", "ws2_32" }\n',
    "Server/core/CServer.cpp": "x",
    "Server/mods/deathmatch/premake5.lua": 'project "Deathmatch"\n',
    "Server/mods/deathmatch/acl.xml": "<acl/>",
    "Server/mods/deathmatch/mtaserver.conf": "<config/>",
    "Server/sdk/premake5.lua": 'project "Server SDK"\n',
    "Shared/data/MTA San Andreas/server/mods/deathmatch/libssl-3.dll": "ssl",
    "Shared/data/MTA San Andreas/MTA/client.dat": "client",
    "Server/sdk/a.h": "x",
    "Shared/premake5.lua": 'project "Shared"\n  includedirs { "../vendor/lua/src" }\n',
    "Shared/sdk/SharedUtil.h": "x",
    "Tests/client/premake5.lua": 'project "Tests_Client"\n  includedirs { "../../Client/sdk", "../../vendor/googletest/include", "../../vendor" }\n'
                                 '  links { "gtest", "zlib", "ws2_32" }\n  files { "**.cpp" }\n',
    "Tests/client/main.cpp": '#include "../../Client/core/CCore.h"\n#include <vector>\n#include "local.h"\n',
    "Tests/client/local.h": "x",
    "Client/core/CCore.h": '#include "CCoreSibling.h"\n#include "../sdk/core/CCoreInterface.h"\n',
    "Client/core/CCoreSibling.h": "x",
    "vendor/zlib/premake5.lua": 'project "zlib"\n',
    "vendor/zlib/zlib.h": "x",
    "vendor/lua/premake5.lua": 'project "lua"\n',
    "vendor/lua/src/lua.h": "x",
    "vendor/googletest/premake5.lua": 'project "gtest"\n  links { "gtest_helper" }\n',
    "vendor/googletest/include/gtest.h": "x",
    "vendor/googletest/helper/premake5.lua": 'project "gtest_helper"\n',
    "vendor/googletest/helper/h.cpp": "x",
    "vendor/sparsehash/src/sparse.h": "x",
    "vendor/cegui/premake5.lua": 'project "CEGUI"\n',
    "vendor/cegui/big.cpp": "x",
    "vendor/cegui/src/deep.cpp": "x",
    "vendor/maetro32/premake5.lua": 'project "maetro32"\n',
    "vendor/unused/readme.txt": "x",
}


def tree_of() -> list[str]:
    return sorted(FILES)


# --------------------------------------------------------------------------- premake parsing


def test_includes_split_by_the_windows_block():
    inc = dict(W.parse_includes(ROOT_PREMAKE))
    assert inc["Server/core"] is False and inc["Shared"] is False and inc["vendor/lua"] is False
    assert inc["Client/core"] is True and inc["Tests/client"] is True
    assert inc["vendor/maetro32"] is True  # nested `if ... end` inside the block does not end it early
    assert inc["vendor/cegui"] is True and inc["vendor/googletest"] is True


def test_projects_links_and_roots():
    text = 'project "A"\nproject "B b"\n  links { "x", "y",\n "z" }\n  links {"w"}\n'
    assert W.parse_projects(text) == ["A", "B b"]
    assert W.parse_links(text) == ["x", "y", "z", "w"]
    dirs = {"vendor", "Shared", "Shared/sdk"}
    assert W.include_roots('includedirs {\n "vendor",\n "/opt/x" }\n includedirs { "Shared/sdk" }', dirs) == {"vendor"}


def test_quoted_includes():
    text = '#include "a.h"\n  #  include "../b/c.h"\n#include <vector>\n// #include "no.h"\nint x; /* #include "no2.h" */\n'
    assert W.quoted_includes(text) == ["a.h", "../b/c.h"]


def test_path_refs():
    files = {"vendor/a/x.h", "vendor/a/sub/y.h", "Shared/sdk/s.h", "Client/sdk/c.h", "Shared/f.cpp"}
    dirs = {"vendor", "vendor/a", "vendor/a/sub", "Shared", "Shared/sdk", "Client", "Client/sdk"}
    text = ('x = {"../../vendor/a/sub", "../../Shared/sdk", "%{wks.location}/../Bin", "-flag", "C:/abs", '
            '"../../Client/sdk/**.h", "../../Shared/f.cpp", "../../../outside", "plainword", "../../vendor"}')
    got = W.path_refs(text, "Server/core", files, dirs, skip={"vendor"})
    assert got == {"vendor/a", "Shared/sdk", "Client/sdk", "Shared/f.cpp"}
    # without the skip list, the include root itself would pull in the whole folder
    assert "vendor" in W.path_refs(text, "Server/core", files, dirs)


# --------------------------------------------------------------------------- the profile


def _profile() -> W.SparseProfile:
    return W.compute_server_profile(tree_of(), FILES.get)


def test_server_profile_is_computed_from_the_premake_files():
    p = _profile()
    assert p.full_dirs == ["Client/sdk", "Server/core", "Server/mods/deathmatch", "Server/sdk", "Shared", "Tests/client", "docs",
                           "utils/buildactions", "vendor/googletest", "vendor/lua", "vendor/sparsehash", "vendor/zlib"]
    assert p.projects == ["Deathmatch", "Server Core", "Server SDK", "Shared", "Tests_Client", "gtest", "lua", "zlib"]
    # client projects keep only their premake file, the root include is not a reason for the whole `vendor`
    assert p.files == ["Client/core/CCore.h", "Client/core/CCoreSibling.h", "Client/core/premake5.lua",
                       "Client/mods/deathmatch/premake5.lua", "utils/premake5.exe",
                       "vendor/cegui/premake5.lua", "vendor/maetro32/premake5.lua"]
    # a header reached by "../" from a chosen folder comes alone, with the quoted includes next to it
    assert p.why["Client/core/CCore.h"] == "included by Tests/client/main.cpp"
    assert p.why["Client/core/CCoreSibling.h"] == "included by Client/core/CCore.h"
    assert "Client/core/CCore.cpp" not in p.files
    assert "vendor/googletest" in p.why and p.why["vendor/googletest"].startswith("linked by Tests/client/premake5.lua")
    assert p.why["vendor/sparsehash"] == "named in Server/core/premake5.lua"
    assert p.why["Tests/client"].startswith("project Tests_Client")
    assert "vendor/unused" not in " ".join(p.full_dirs)


def test_link_closure_follows_projects_not_system_libraries():
    p = _profile()
    assert "vendor/googletest" in p.full_dirs  # gtest, linked by Tests_Client; helper lives below it
    assert not any("ws2_32" in d for d in p.full_dirs)


def test_missing_extra_project_is_an_error():
    files = dict(FILES)
    files["Tests/client/premake5.lua"] = 'project "Other"\n'
    with pytest.raises(SatkError) as e:
        W.compute_server_profile(sorted(files), files.get)
    assert e.value.code == "NOT_READY" and "Tests_Client" in e.value.msg


def _foundation_files() -> dict[str, str]:
    files = dict(FILES)
    files["premake5.lua"] = ROOT_PREMAKE.replace('include "Tests/client"', 'include "Tests/client"\n'
                                                '\t\tinclude "Tests/satk"\n\t\tinclude "Tests/opennet"\n'
                                                '\t\tinclude "Shared/opennet"')
    files["Client/core/premake5.lua"] += 'include "satk"\n'
    files.update({
        "Client/core/satk/premake5.lua": 'dofile "copy.lua"\n',
        "Client/core/satk/copy.lua": '-- a nested generation helper\n',
        "Client/core/satk/client_only.cpp": "x",
        "Tests/satk/premake5.lua": 'project "gtest_satk"\nproject "Tests_Satk"\n'
                                   'links { "gtest_satk", "gtest" }\ninclude "support"\n',
        "Tests/satk/test.cpp": "x",
        "Tests/satk/support/premake5.lua": 'project "TestSupport"\ninclude "../../../vendor/helper"\n',
        "Tests/satk/support/support.cpp": "x",
        "vendor/helper/premake5.lua": 'project "Helper"\n',
        "vendor/helper/helper.cpp": "x",
        "Tests/opennet/premake5.lua": 'project "Tests_OpenNet"\nlinks { "opennet", "gtest" }\n',
        "Tests/opennet/test.cpp": "x",
        "Shared/opennet/premake5.lua": 'project "opennet"\n',
        "Shared/opennet/bits.cpp": "x",
    })
    return files


def _covered(profile, path):
    return path in profile.files or any(path == d or path.startswith(d + "/") for d in profile.full_dirs)


def test_foundation_tests_and_nested_generation_scripts_are_covered():
    files = _foundation_files()
    profile = W.compute_server_profile(files, files.get)
    for path in ("Tests/satk/test.cpp", "Tests/satk/support/support.cpp", "Tests/opennet/test.cpp",
                 "Shared/opennet/bits.cpp", "vendor/helper/helper.cpp", "Client/core/satk/premake5.lua",
                 "Client/core/satk/copy.lua"):
        assert _covered(profile, path), path
    assert not _covered(profile, "Client/core/satk/client_only.cpp")
    assert {"Tests_Satk", "Tests_OpenNet", "TestSupport", "opennet", "Helper"} <= set(profile.projects)


def test_future_test_projects_are_discovered_without_a_profile_list_change():
    files = _foundation_files()
    files["Tests/satk/premake5.lua"] += 'include "../future"\n'
    files["Tests/future/premake5.lua"] = 'project "Tests_Future"\nlinks { "CEGUI" }\n'
    files["Tests/future/future.cpp"] = "x"
    profile = W.compute_server_profile(files, files.get)
    assert _covered(profile, "Tests/future/future.cpp")
    assert _covered(profile, "vendor/cegui/big.cpp")  # its linked dependency was previously a stub
    assert "Tests_Future" in profile.projects


def test_windows_server_projects_are_not_treated_as_client_stubs():
    files = dict(FILES)
    files["premake5.lua"] = ROOT_PREMAKE.replace('include "Tests/client"', 'include "Tests/client"\n'
                                                '\t\tinclude "Server/windows"')
    files["Server/windows/premake5.lua"] = 'project "WindowsServer"\n'
    files["Server/windows/server.cpp"] = "x"
    profile = W.compute_server_profile(files, files.get)
    assert _covered(profile, "Server/windows/server.cpp") and "WindowsServer" in profile.projects


def test_include_graph_is_relative_cycle_safe_and_ignores_comments():
    files = _foundation_files()
    files["Tests/satk/support/premake5.lua"] += 'include ".."\n'
    files["premake5.lua"] += '--[[\ninclude "absent"\n]]\n-- include "also_absent"\n'
    files["Client/core/premake5.lua"] += '--[=[\ninclude "absent"\n]=]\n'
    profile = W.compute_server_profile(files, files.get)
    assert "Client/core/satk/copy.lua" in profile.files
    assert _covered(profile, "Tests/satk/support/support.cpp")
    files["Server/core/premake5.lua"] += 'include "missing"\n'
    with pytest.raises(SatkError, match="missing") as e:
        W.compute_server_profile(files, files.get)
    assert e.value.code == "NOT_READY"


def test_patterns_open_partial_folders_level_by_level():
    pats = W.emit_patterns(["Server", "Shared", "vendor/lua"],
                           ["Client/core/premake5.lua", "Client/mods/dm/premake5.lua", "utils/premake5.exe"])
    assert pats == [
        "/*", "!/*/",
        "/Client/", "!/Client/*", "/Client/core/premake5.lua", "/Client/mods/", "!/Client/mods/*",
        "/Client/mods/dm/premake5.lua",
        "/Server/", "/Shared/",
        "/utils/premake5.exe",
        "/vendor/", "!/vendor/*", "/vendor/lua/",
    ]
    assert W.emit_patterns([], []) == ["/*", "!/*/"]


# --------------------------------------------------------------------------- worktree paths


def test_worktree_paths_stay_under_work_wt(satk_home, make_junction, tmp_path):
    root = W.wt_root()
    assert root == satk_home / "work" / "wt"
    ok = W.check_wt_path(root / "sae2-srv")
    assert ok == root / "sae2-srv"
    for bad in (satk_home / "work" / "other", satk_home / "engine" / "x", root, root / "a" / "b", root.parent,
                satk_home / "work" / "wt2" / "x", Path(root / ".." / "x")):
        with pytest.raises(SatkError) as e:
            W.check_wt_path(bad)
        assert e.value.code in ("BAD_PARAMS", "PROTECTED_PATH"), bad
    for name in ("-x", "x y", "a" * 70, "a?b", "x:y"):
        with pytest.raises(SatkError):
            W.check_wt_path(root / name)
    with pytest.raises(SatkError):
        W.check_wt_path(satk_home / "src" / "x")
    with pytest.raises(SatkError) as e:
        W.check_wt_path("")
    assert e.value.code == "BAD_PARAMS"
    # a junction inside wt that leads out of it is refused
    outside = tmp_path / "ws" / "somewhere"
    outside.mkdir(parents=True)
    root.mkdir(parents=True)
    make_junction(root / "jumper", outside)
    with pytest.raises(SatkError) as e:
        W.check_wt_path(root / "jumper")
    assert e.value.code == "PROTECTED_PATH"


def test_create_validates_before_touching_git(satk_home):
    with pytest.raises(SatkError) as e:
        get_op("engine.worktree").call({"action": "create", "path": str(satk_home / "work" / "wt" / "x"),
                                        "branch": "b"})
    assert e.value.code == "NOT_READY"  # no fork at all in this workspace
    with pytest.raises(SatkError) as e:
        W.create(str(satk_home / "work" / "x"), "b")
    assert e.value.code in ("BAD_PARAMS", "NOT_READY")
    with pytest.raises(SatkError) as e:
        W.create(str(satk_home / "work" / "wt" / "x"), "b", profile="tiny")
    assert e.value.code == "BAD_PARAMS" and "server" in e.value.did_you_mean


# --------------------------------------------------------------------------- real git


@pytest.fixture
def synthetic_fork(satk_home):
    if shutil.which("git") is None:
        pytest.skip("git not found")
    fork = layout().fork
    for rel, text in FILES.items():
        p = fork / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    env = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

    def g(*a):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *a],
                       cwd=fork, env=env, check=True, capture_output=True)

    g("init", "-q", "-b", "main")
    g("add", "-A")
    g("commit", "-qm", "base")
    return fork


def _git_out(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True,
                          env=dict(os.environ, GIT_NO_LAZY_FETCH="1")).stdout


def _lay_down_deps(L) -> None:
    import hashlib
    import json

    payload = b"net-x64-binary"
    sha = hashlib.sha256(payload).hexdigest()
    f = L.deps / "net" / sha / "net_64.dll"
    f.parent.mkdir(parents=True)
    f.write_bytes(payload)
    L.lock.write_text(json.dumps({"version": 1, "items": [{"id": "net-x64", "file": f"net/{sha}/net_64.dll",
                                                            "sha256": sha, "size": len(payload)}]}), encoding="utf-8")


def test_create_list_remove_round_trip(synthetic_fork, satk_home):
    L = layout()
    _lay_down_deps(L)
    wt = satk_home / "work" / "wt" / "srv"
    out = W.create(str(wt), "feat/srv-base")
    assert out["ok"] and out["fork_id"] == "srv" and out["profile"] == "server" and out["branch"] == "feat/srv-base"
    have = sorted(p.relative_to(wt).as_posix() for p in wt.rglob("*")
                  if p.is_file() and ".git" not in p.relative_to(wt).parts and "Bin" not in p.relative_to(wt).parts)
    expected = sorted(f for f in FILES if f.startswith(("Server/", "Shared/", "Tests/client/", "docs/", "utils/buildactions/",
                                                         "vendor/googletest/", "vendor/lua/", "vendor/sparsehash/",
                                                         "vendor/zlib/", "Client/sdk/"))
                      ) + ["Client/core/premake5.lua", "Client/mods/deathmatch/premake5.lua", "README.md", "premake5.lua",
                           "utils/premake5.exe", "vendor/cegui/premake5.lua", "vendor/maetro32/premake5.lua",
                           "Client/core/CCore.h", "Client/core/CCoreSibling.h"]
    assert have == sorted(expected)
    assert not (wt / "Client" / "loose.txt").exists() and not (wt / "vendor" / "cegui" / "big.cpp").exists()
    assert (wt / "Bin" / "server" / "x64" / "net.dll").read_bytes() == b"net-x64-binary"
    # the offline part of install_data: server DLLs, configs and the template (no client data)
    run = wt / "Bin" / "server" / "mods" / "deathmatch"
    assert (run / "libssl-3.dll").read_bytes() == b"ssl" and (run / "acl.xml").is_file()
    assert (run / "mtaserver.conf.template").read_text(encoding="utf-8").startswith("<!-- DELETING THIS FILE")
    assert (run / "mtaserver.conf.template").read_text(encoding="utf-8").endswith("<config/>")
    assert not (wt / "Bin" / "MTA").exists()
    # nothing is modified or missing (the real fork ignores Bin/, the synthetic one has no .gitignore)
    assert _git_out(wt, "status", "--short").split() in ([], ["??", "Bin/"])
    assert _git_out(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feat/srv-base"
    # the main checkout is still on main and not sparse
    assert _git_out(synthetic_fork, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert (synthetic_fork / "Client" / "loose.txt").is_file()
    assert layout(wt).meta.is_file()
    lst = W.listing()
    names = {r[0]: r for r in lst["rows"]}
    assert names["srv"][2] == "feat/srv-base" and names["srv"][4] == "server" and names["srv"][5] is True
    assert [r for r in lst["rows"] if not r[5]], "the configured fork is listed as unmanaged"
    assert W.listing(sizes=True)["cols"][-2:] == ["files", "size_mb"]
    # a second create on the same name or branch is refused, nothing is half-made
    with pytest.raises(SatkError) as e:
        W.create(str(wt), "feat/other")
    assert e.value.code == "EXISTS"
    with pytest.raises(SatkError) as e:
        W.create(str(satk_home / "work" / "wt" / "srv2"), "feat/srv-base")
    assert e.value.code == "EXISTS" and not (satk_home / "work" / "wt" / "srv2").exists()
    with pytest.raises(SatkError) as e:
        W.create(str(satk_home / "work" / "wt" / "srv3"), "feat/x", base="no-such-ref")
    assert e.value.code == "NOT_FOUND"
    # remove: refuses the configured fork and paths outside wt, then removes ours and keeps the branch
    with pytest.raises(SatkError):
        W.remove(str(synthetic_fork))
    with pytest.raises(SatkError) as e:
        W.remove(str(satk_home / "work" / "wt" / "ghost"))
    assert e.value.code == "NOT_FOUND"
    (wt / "Server" / "dirty.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SatkError):
        W.remove(str(wt))  # untracked file: git refuses without --force
    res = W.remove(str(wt), force=True)
    assert res["ok"] and res["branch_kept"] == "feat/srv-base" and not wt.exists()
    assert not layout(wt).meta.exists()
    assert "feat/srv-base" in _git_out(synthetic_fork, "branch", "--list")


def test_remove_can_delete_only_the_branch_it_created(synthetic_fork, satk_home):
    wt = satk_home / "work" / "wt" / "one"
    W.create(str(wt), "feat/one", profile="full")
    assert (wt / "Client" / "loose.txt").is_file()  # the full profile is a normal checkout
    res = W.remove(str(wt), delete_branch=True)
    assert res["branch_deleted"] == "feat/one" and "feat/one" not in _git_out(synthetic_fork, "branch", "--list")


def test_failed_create_rolls_back(synthetic_fork, satk_home, monkeypatch):
    def boom(L):
        raise SatkError("REVISION", "pin mismatch")

    monkeypatch.setattr(W, "install_server_deps", boom)
    wt = satk_home / "work" / "wt" / "bad"
    with pytest.raises(SatkError) as e:
        W.create(str(wt), "feat/bad")
    assert e.value.code == "REVISION"
    assert not wt.exists() and "feat/bad" not in _git_out(synthetic_fork, "branch", "--list")
    assert not layout(wt).meta.exists()
    assert "bad" not in _git_out(synthetic_fork, "worktree", "list")
    assert _git_out(synthetic_fork, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_deps_without_a_pin_are_skipped_not_downloaded(synthetic_fork, satk_home):
    out = W.create(str(satk_home / "work" / "wt" / "nodeps"), "feat/nodeps")
    assert out["ok"] and out["rows"][-1][0] == "deps" and "net-x64 is not pinned" in out["rows"][-1][2]
    run = satk_home / "work" / "wt" / "nodeps" / "Bin" / "server"
    assert not (run / "x64" / "net.dll").exists() and (run / "mods" / "deathmatch" / "acl.xml").is_file()


def test_refresh_materialises_new_head_dependencies_and_preserves_local_edits(synthetic_fork, satk_home, run_cli):
    import json

    wt = satk_home / "work/wt/refresh"
    W.create(str(wt), "feat/refresh")
    for rel, text in _foundation_files().items():
        path = synthetic_fork / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git_out(synthetic_fork, "add", "-A")
    _git_out(synthetic_fork, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "foundation")
    _git_out(wt, "merge", "--ff-only", "main")
    assert not (wt / "Tests/satk/test.cpp").exists()
    assert not (wt / "Client/core/satk/premake5.lua").exists()
    dirty = wt / "Server/core/CServer.cpp"
    dirty.write_text("local changes\n", encoding="utf-8")
    untracked = wt / "Server/core/local.txt"
    untracked.write_text("keep\n", encoding="utf-8")
    L = layout(wt)
    W.write_json(L.state, {"gen": {"fingerprint": "old"}, "builds": {"Release|x64": "keep"}})
    before = _git_out(wt, "rev-parse", "HEAD").strip()

    result = run_cli(["engine", "worktree", "refresh", str(wt)])
    assert result.code == 0, result.out
    out = result.json
    assert out["profile"] == "server" and out["head"] == before[:9]
    for path in ("Tests/satk/test.cpp", "Tests/opennet/test.cpp", "Shared/opennet/bits.cpp",
                 "Client/core/satk/premake5.lua", "Client/core/satk/copy.lua", "vendor/helper/helper.cpp"):
        assert (wt / path).is_file(), path
    assert not (wt / "Client/core/satk/client_only.cpp").exists()
    assert dirty.read_text(encoding="utf-8") == "local changes\n"
    assert untracked.read_text(encoding="utf-8") == "keep\n"
    assert _git_out(wt, "rev-parse", "HEAD").strip() == before
    assert _git_out(wt, "branch", "--show-current").strip() == "feat/refresh"
    meta = json.loads(L.meta.read_text(encoding="utf-8"))
    assert meta["branch"] == "feat/refresh" and meta["refreshed_rev"] == before
    assert {"Tests_Satk", "Tests_OpenNet", "opennet"} <= set(meta["projects"])
    assert json.loads(L.state.read_text(encoding="utf-8")) == {"builds": {"Release|x64": "keep"}}
    assert W.refresh(str(wt))["folders"] == out["folders"]
    assert (synthetic_fork / "Client/core/satk/client_only.cpp").is_file()


def test_refresh_full_profile_and_scope_guards(synthetic_fork, satk_home):
    wt = satk_home / "work/wt/full"
    W.create(str(wt), "feat/full", profile="full")
    assert W.refresh(str(wt))["profile"] == "full"
    assert (wt / "Client/loose.txt").is_file()
    with pytest.raises(SatkError) as e:
        W.refresh(str(synthetic_fork))
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        W.refresh(str(satk_home / "work/wt/absent"))
    assert e.value.code == "NOT_FOUND"
    L = layout(wt)
    W.write_json(L.meta, {"profile": "unknown"})
    with pytest.raises(SatkError) as e:
        W.refresh(str(wt))
    assert e.value.code == "NOT_READY"


def test_refresh_failure_does_not_update_registry_or_build_state(synthetic_fork, satk_home, monkeypatch):
    wt = satk_home / "work/wt/failure"
    W.create(str(wt), "feat/failure")
    L = layout(wt)
    W.write_json(L.state, {"gen": {"fingerprint": "old"}})
    meta, state = L.meta.read_bytes(), L.state.read_bytes()
    original = W.git_raw

    def fail(*args, **kwargs):
        if args[0] == "sparse-checkout":
            raise SatkError("EXTERNAL_TOOL", "git could not apply profile")
        return original(*args, **kwargs)

    monkeypatch.setattr(W, "git_raw", fail)
    with pytest.raises(SatkError):
        W.refresh(str(wt))
    assert L.meta.read_bytes() == meta and L.state.read_bytes() == state


def test_refresh_refuses_a_running_build(synthetic_fork, satk_home, monkeypatch):
    from satk.engine import common

    wt = satk_home / "work/wt/busy"
    W.create(str(wt), "feat/busy")
    L = layout(wt)
    lock = L.build_dir / f".{L.lock_name}.lock"
    lock.write_text(f"{os.getpid() + 100000}\n", encoding="utf-8")
    monkeypatch.setattr(common, "pid_alive", lambda pid: True)
    with pytest.raises(SatkError) as e:
        W.refresh(str(wt))
    assert e.value.code == "BUSY"


@pytest.mark.parametrize("protected_part", ["metadata", "sparse_file", "profile"])
def test_refresh_checks_protected_targets_before_sparse_write(synthetic_fork, satk_home, monkeypatch,
                                                            make_junction, protected_part):
    wt = satk_home / "work/wt/guarded"
    W.create(str(wt), "feat/guarded")
    protected = satk_home / "src/protected"
    protected.mkdir(parents=True)
    if protected_part == "metadata":
        original = W.git

        def redirected(*args, **kwargs):
            return str(protected) if args == ("rev-parse", "--git-common-dir") else original(*args, **kwargs)

        monkeypatch.setattr(W, "git", redirected)
    elif protected_part == "sparse_file":
        metadata = Path(W.git("rev-parse", "--absolute-git-dir", cwd=wt))
        assert metadata.resolve().is_relative_to(satk_home.resolve())
        (metadata / "info").rename(metadata / "info-saved")
        make_junction(metadata / "info", protected)
    else:
        make_junction(wt / "Tests/new", protected)
        monkeypatch.setattr(W, "compute_profile", lambda *a: W.SparseProfile("server", ["Tests/new"]))

    def never_write(*args, **kwargs):
        raise AssertionError("sparse write reached")

    monkeypatch.setattr(W, "git_raw", never_write)
    with pytest.raises(SatkError) as e:
        W.refresh(str(wt))
    assert e.value.code == "PROTECTED_PATH"


@pytest.mark.engine
def test_real_server_and_test_premake_includes_are_covered():
    """Guard against future server/test includes silently becoming sparse stubs."""
    from satk.core import config

    config.reset()
    try:
        repo = layout().fork
        if not (repo / "premake5.lua").is_file():
            pytest.skip("MTA fork not found")
        rev = W.git("rev-parse", "HEAD", cwd=repo, no_lazy=True)
        profile = W.compute_profile(repo, rev, "server")
        tree = W.git("ls-tree", "-r", "--name-only", "-z", rev, cwd=repo, no_lazy=True).split("\0")
        scripts = W.read_blobs(repo, rev, [p for p in tree if p.endswith(".lua")])
        for directory, windows in W.parse_includes(scripts["premake5.lua"]):
            if windows and not directory.startswith(("Tests/", "Shared/", "Server/")):
                continue
            missing = [p for p in tree if p.startswith(directory + "/") and not _covered(profile, p)]
            assert not missing, f"include {directory!r} is not covered: {missing[:8]}"
        # Nested client scripts must be present for generation, without pulling in client sources.
        for script in W.included_scripts(tree, scripts.get):
            assert script == "premake5.lua" or _covered(profile, script), script
    finally:
        config.reset()
