"""satk dev export-public: the exclude list, the audit, the snapshot repository and the release checks.

Synthetic trees and throw-away git repositories only. Strings that the audit must catch (paths of the
development machine, tokens, addresses) are assembled at run time, so this file itself passes the audit.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from satk.core import registry as R
from satk.core.errors import SatkError
from satk.release import public as P
from satk.release.tree import Tree, export

REPO = Path(__file__).resolve().parents[2]
BS = "\\"
#: The development machine's folders, spelled at run time.
DEV_WS = "D:" + BS + "Games" + BS + "GTA"
DEV_TOOLS = "D:" + "/files/tg"
DEV_HOME = "C:" + BS + "Users" + BS + "User"
#: The workspace in Git Bash spelling.
BASH_WS = "/d/" + "Games/GTA"
#: Someone else's account folders (not publishable either: a concrete account name).
BOB = "C:" + "/Users/Bob"
RUNNER = "C:" + "/Users/runneradmin"


def _tree(files: dict[str, bytes | str]) -> Tree:
    return Tree(commit="0" * 40, timestamp=1_760_000_000,
                files={k: v.encode("utf-8") if isinstance(v, str) else v for k, v in files.items()})


def _rules(text: str = "") -> P.Rules:
    return P.parse_rules(text, "test")


def _hits(tree: Tree, rules: P.Rules | None = None, **kw) -> set[tuple[str, str, str]]:
    return {(f.path, f.rule, f.text) for f in P.audit(tree, rules or _rules(), **kw)}


# --------------------------------------------------------------------------- the exclude list


@pytest.mark.parametrize("pattern,yes,no", [
    ("/data/manifests/install-*.json", ["data/manifests/install-2026-10-04.json"],
     ["data/manifests/stock-1.0us-hoodlum.json", "x/data/manifests/install-1.json", "data/manifests/install-1.jsonx"]),
    ("secret.txt", ["secret.txt", "a/b/secret.txt"], ["secret.txt.bak", "a/notsecret.txt"]),
    ("build/", ["build/x", "a/build/y/z"], ["build", "builder/x"]),
    ("docs/**/draft-*.md", ["docs/draft-a.md", "docs/en/x/draft-b.md"], ["docs/en/a.md", "x/docs/draft-a.md"]),
    ("**/notes", ["notes", "a/notes", "a/notes/b.txt"], ["a/notes.txt"]),
    ("/tests/re/*.md", ["tests/re/DEFECT_REVIEW.md"], ["tests/re/data/x.md", "tests/re/a.py"]),
    ("file[0-9].txt", ["file1.txt", "x/file2.txt"], ["filea.txt"]),
    ("a?c", ["abc", "x/a_c"], ["a/c", "ac"]),
])
def test_patterns_follow_gitignore(pattern, yes, no):
    p = P.compile_pattern(pattern)
    for path in yes:
        assert p.match(path), (pattern, path)
    for path in no:
        assert not p.match(path), (pattern, path)


def test_last_match_wins_and_comments():
    rules = _rules("# comment\n\n/data/*.json\n!/data/keep.json\n")
    assert rules.excluded("data/a.json").text == "/data/*.json"
    assert rules.excluded("data/keep.json") is None
    assert rules.excluded("src/a.json") is None


def test_allow_lines_and_errors():
    rules = _rules("@allow email /vendor/** ^someone@\n@allow machine-path /data/x.txt\n")
    assert [a.rule for a in rules.allows] == ["email", "machine-path"]
    assert rules.allowed("email", "vendor/a/b.py", "someone@corp.example")
    assert not rules.allowed("email", "vendor/a/b.py", "other@corp.example")
    assert not rules.allowed("secret", "vendor/a/b.py", "someone@corp.example")
    assert rules.allowed("machine-path", "data/x.txt", "anything")
    for bad in ("@allow nosuchrule /x", "@allow email", "@deny email /x", "@allow email /x ([unclosed"):
        with pytest.raises(SatkError) as e:
            _rules(bad)
        assert e.value.code == "BAD_PARAMS", bad


def test_real_exclude_list():
    rules = P.parse_rules((REPO / P.EXCLUDE_FILE).read_text(encoding="utf-8"))
    for gone in ("data/manifests/install-2026-10-04.json", "docs/agent/workspace-CLAUDE.md"):
        assert rules.excluded(gone) is not None, gone
    for kept in ("data/manifests/stock-1.0us-hoodlum.json", "docs/agent/SKILL.md", "src/satk/game/manifests.py",
                 P.EXCLUDE_FILE, "data/crashlist/gta-sa-10us-en.txt"):
        assert rules.excluded(kept) is None, kept


def test_apply_excludes_and_unused_patterns():
    tree = _tree({"a/x.json": "1", "a/y.txt": "2", "b.md": "3"})
    rules = _rules("/a/*.json\n/nothing/\n")
    kept, dropped = P.apply_excludes(tree, rules)
    assert sorted(kept.files) == ["a/y.txt", "b.md"] and kept.commit == tree.commit
    assert dropped == [("a/x.json", "/a/*.json")]
    assert P.unused_patterns(tree, rules) == ["/nothing/"]


def test_load_rules_prefers_the_commit(tmp_path):
    tree = _tree({P.EXCLUDE_FILE: "/only-in-commit\n"})
    assert [p.text for p in P.load_rules(tree).patterns] == ["/only-in-commit"]
    fallback = tmp_path / "x.txt"
    fallback.write_text("/from-checkout\n", encoding="utf-8")
    assert [p.text for p in P.load_rules(_tree({}), fallback).patterns] == ["/from-checkout"]
    with pytest.raises(SatkError) as e:
        P.load_rules(_tree({}), tmp_path / "missing.txt")
    assert e.value.code == "NOT_FOUND"


# --------------------------------------------------------------------------- audit


def test_machine_paths_everywhere():
    tree = _tree({
        "src/satk/a.py": f'X = r"{DEV_WS}\\tools"\n',
        "tests/b.py": f"p = '{DEV_WS.replace(BS, BS * 2)}'\n",  # an escaped spelling
        "docs/en/c.md": f"see `{DEV_TOOLS}/x` and {BASH_WS}/work and {DEV_HOME}{BS}AppData\n",
        "data/d.json": '{"home": "' + BOB + '/x", "ok": "C:/Users/Public/x", "ph": "C:\\\\Users\\\\<you>\\\\x"}\n',
        "e.txt": "C:" + BS + "Program Files" + BS + "Blender Foundation; E:" + BS + "Games is someone else's\n",
    })
    hits = {(p, t) for p, r, t in _hits(tree) if r == "machine-path"}
    assert ("src/satk/a.py", "D:" + BS + "Games") in hits
    assert ("tests/b.py", "D:" + BS * 2 + "Games") in hits
    assert {("docs/en/c.md", DEV_TOOLS[:8]), ("docs/en/c.md", BASH_WS[:8]), ("docs/en/c.md", DEV_HOME)} <= hits
    assert ("data/d.json", BOB) in hits
    assert not [h for h in hits if h[0] == "e.txt" or "Public" in h[1] or "<you>" in h[1]]


def test_plan_refs_only_in_user_docs():
    text = "SPEC 4.3, WP-12, M2-09, M3 A4, M3-A1, M3 lane B4, docs/design/X.md, M2; the M4 is a rifle\n"
    tree = _tree({"docs/en/x.md": text, "README.md": text, "src/satk/x.py": f"# {text}",
                  "packaging/portable/README-FIRST.txt": "WP-01\n", "vendor/v/README.md": text})
    hits = _hits(tree)
    docs = {t for p, r, t in hits if p == "docs/en/x.md" and r == "plan-ref"}
    assert docs == {"SPEC", "WP-12", "M2-09", "M3 A4", "M3-A1", "M3 lane B4", "docs/design", "M2"}
    assert {p for p, r, _ in hits if r == "plan-ref"} == {"docs/en/x.md", "README.md",
                                                           "packaging/portable/README-FIRST.txt"}


def test_emails_secrets_and_private_repos():
    at = "@"
    tree = _tree({
        "src/a.py": (f"A = 'jane{at}corp.com'\nB = 'qx{at}li.es'\nC = 'x{at}example.org'\nD = 'b{at}t.example.invalid'\n"
                     f"E = 'a{at}b.txd'\nF = 'git{at}github.com:o/r'\nG = 'noreply{at}anthropic.com'\n"),
        "LICENSE": f"Copyright (c) Some One <some{at}one.org>\n",
        "data/notices/x.txt": f"by Another <an{at}other.net>\n",
        "src/b.py": ("t1 = 'gh" + "p_" + "a" * 36 + "'\nt2 = 'sk-" + "ant-api03-" + "b" * 30 + "'\n"
                     "t3 = '-----BEGIN " + "RSA PRIVATE KEY-----'\nt4 = 'ROUT" + "MY_API_KEY'\n"
                     "pool = 'task_allocator'\n"),
        "c.json": '{"home": "https://github.com/qx' + 'lies/satk", "ok": "https://github.com/qx'
                  + 'lies/satk-gta.git", "ok2": "qx' + 'lies/satk-gta"}\n',
    })
    hits = _hits(tree)
    assert {(p, t) for p, r, t in hits if r == "email"} == {("src/a.py", f"jane{at}corp.com")}
    secrets = sorted(t[:6] for p, r, t in hits if r == "secret")
    assert secrets == ["-----B", "ROUT" + "MY", "ghp_aa", "sk-ant"]
    assert {(p, t) for p, r, t in hits if r == "private-repo"} == {("c.json", "qx" + "lies/satk")}


def test_assets_big_files_and_dangling_refs():
    rw = (0x16).to_bytes(4, "little") + (100).to_bytes(4, "little") + (0x1803FFFF).to_bytes(4, "little")
    fixture = b"VER2" + b"\0" * 60
    allow = f"{hashlib.sha256(fixture).hexdigest()} tests/core/data/fake.bin\n"
    tree = _tree({
        "a.bin": rw + b"\0" * 20, "tests/core/data/fake.bin": fixture, ".assetguard-allow": allow,
        "models/x.dff": b"\0", "big.dat": b"\0" * (P.SIZE_LIMIT + 1),
        "docs/en/game.md": "the audit `data/manifests/install-1.json` is ...\n",
        "src/satk/game/m.py": "NAME = 'data/manifests/install-1.json'\n",
    })
    hits = _hits(tree, dropped=["data/manifests/install-1.json"])
    assert {(p, r) for p, r, _ in hits} == {("a.bin", "asset"), ("models/x.dff", "asset"),
                                            ("big.dat", "large-file"), ("docs/en/game.md", "dangling-ref")}


def test_allow_lines_silence_findings():
    tree = _tree({"data/crash.txt": "C:" + BS + "Users" + BS + "Author" + BS + "AppData\n"})
    assert _hits(tree)
    assert not _hits(tree, _rules("@allow machine-path /data/*.txt ^C:.Users.Author$\n"))
    assert _hits(tree, _rules("@allow machine-path /data/*.txt ^C:.Users.Other$\n"))


def test_findings_have_lines_and_groups():
    tree = _tree({"docs/en/a.md": "ok\nWP-1 and WP-2\n\nSPEC\n"})
    found = P.audit(tree, _rules())
    assert [(f.line, f.text) for f in found] == [(2, "WP-1"), (2, "WP-2"), (4, "SPEC")]
    assert found[0].context == "WP-1 and WP-2"
    assert P.group(found) == [["docs/en/a.md", "plan-ref", 3, 2, "WP-1"]]


def test_this_lane_passes_its_own_audit():
    """The export code, its tests and the exclude list carry none of what they look for."""
    files = {p.relative_to(REPO).as_posix(): p.read_bytes()
             for p in [*(REPO / "src/satk/release").glob("*.py"), *(REPO / "tests/release").glob("*.py"),
                       REPO / P.EXCLUDE_FILE, REPO / "src/satk/docs/sync.py"]}
    assert P.audit(_tree(files), P.parse_rules(files[P.EXCLUDE_FILE].decode("utf-8"))) == []


# --------------------------------------------------------------------------- the snapshot repository


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True)
    return p.stdout.decode("utf-8").strip()


@pytest.fixture
def dev_repo(tmp_path) -> Path:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    repo = tmp_path / "dev"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Dev Person")
    _git(repo, "config", "user.email", "dev@example.org")
    _git(repo, "config", "core.autocrlf", "true")
    files = {
        ".gitattributes": "* text=auto eol=lf\n*.cmd text eol=crlf\nvendor/** -text\n",
        "pyproject.toml": '[project]\nname = "satk-gta"\nversion = "1.0.0"\n',
        "src/satk/__init__.py": '__version__ = "1.0.0"\n',
        "satk.cmd": "@echo off\npython -m satk %*\n",
        "run.sh": "#!/bin/sh\necho ok\n",
        "vendor/up/LICENSE": "MIT\r\nupstream bytes\r\n",
        "data/manifests/install-1.json": '{"mine": 1}\n',
        P.EXCLUDE_FILE: "/data/manifests/install-*.json\n",
    }
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(text.encode("utf-8"))
    _git(repo, "add", "-A")
    _git(repo, "update-index", "--chmod=+x", "run.sh")
    _git(repo, "commit", "-q", "-m", "dev 1")
    return repo


def _export(repo: Path) -> tuple[Tree, list]:
    tree = export(repo, "HEAD")
    kept, dropped = P.apply_excludes(tree, P.load_rules(tree))
    return kept, dropped


def test_fresh_snapshot(dev_repo, tmp_path):
    kept, dropped = _export(dev_repo)
    assert [d[0] for d in dropped] == ["data/manifests/install-1.json"]
    dest = tmp_path / "public" / "satk-gta-1.0.0"
    res = P.write_snapshot(kept, dev_repo, dest, version="1.0.0")
    assert res["status"] == "committed" and res["commits"] == 1 and res["tag"] == "v1.0.0"
    assert res["branch"] == "main" and res["author"] == "Dev Person <dev@example.org>"
    assert _git(dest, "log", "-1", "--format=%s|%an <%ae>|%cn|%at") == "satk 1.0.0 public preview|" \
        f"Dev Person <dev@example.org>|Dev Person|{kept.timestamp}"
    assert _git(dest, "tag", "--points-at", "HEAD") == "v1.0.0"
    assert not (dest / "data" / "manifests").exists()
    dev = _git(dev_repo, "ls-tree", "-r", "HEAD").splitlines()
    pub = _git(dest, "ls-tree", "-r", "HEAD").splitlines()
    assert pub == [ln for ln in dev if "install-1.json" not in ln]  # same blobs, same modes (+x kept)
    assert any(ln.startswith("100755") and ln.endswith("run.sh") for ln in pub)
    # the same commit again: the previous snapshot is replaced by an identical one
    again = P.write_snapshot(kept, dev_repo, dest, version="1.0.0")
    assert again["public_commit"] == res["public_commit"], "same commit + same identity = same snapshot commit"


def test_snapshot_refuses_a_foreign_folder(dev_repo, tmp_path):
    kept, _ = _export(dev_repo)
    dest = tmp_path / "busy"
    dest.mkdir()
    (dest / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(SatkError) as e:
        P.write_snapshot(kept, dev_repo, dest, version="1.0.0")
    assert e.value.code == "EXISTS" and (dest / "keep.txt").is_file()


def test_snapshot_on_top_of_the_public_history(dev_repo, tmp_path):
    kept, _ = _export(dev_repo)
    first = tmp_path / "first"
    P.write_snapshot(kept, dev_repo, first, version="1.0.0")
    public = tmp_path / "public.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(first), str(public)], check=True, capture_output=True)
    # the next release: a new version, a deleted file and a new one
    (dev_repo / "run.sh").unlink()
    (dev_repo / "NEW.md").write_text("new\n", encoding="utf-8")
    for rel in ("pyproject.toml", "src/satk/__init__.py"):
        f = dev_repo / rel
        f.write_text(f.read_text(encoding="utf-8").replace("1.0.0", "1.1.0"), encoding="utf-8")
    _git(dev_repo, "add", "-A")
    _git(dev_repo, "commit", "-q", "-m", "dev 2")
    kept2, _ = _export(dev_repo)
    dest = tmp_path / "second"
    res = P.write_snapshot(kept2, dev_repo, dest, version="1.1.0", base=str(public))
    assert res["status"] == "committed" and res["commits"] == 2 and res["base"] == str(public)
    assert _git(dest, "log", "--format=%s").splitlines() == ["satk 1.1.0 public preview", "satk 1.0.0 public preview"]
    files = set(_git(dest, "ls-files").splitlines())
    assert "NEW.md" in files and "run.sh" not in files
    # an existing tag with other files is refused; the same files are fine
    with pytest.raises(SatkError) as e:
        P.write_snapshot(kept2, dev_repo, tmp_path / "third", version="1.0.0", base=str(public))
    assert e.value.code == "EXISTS"
    same = P.write_snapshot(kept, dev_repo, tmp_path / "fourth", version="1.0.0", base=str(first))
    assert same["status"] == "unchanged" and same["commits"] == 1


def test_fresh_env_hides_the_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SATK_HOME", "x")
    monkeypatch.setenv("SATK_PATHS_WORK", "y")
    monkeypatch.setenv("PYTHONPATH", "z")
    env = P.fresh_env(tmp_path / "snap", tmp_path / "scratch")
    assert env["SATK_HOME"] == str(tmp_path / "scratch" / "home") and "SATK_PATHS_WORK" not in env
    assert env["SATK_CONFIG"] == str(tmp_path / "scratch" / "config") and env["SATK_DETECT"] == "0"
    assert env["PYTHONPATH"] == str(tmp_path / "snap" / "src")
    assert not list((tmp_path / "scratch" / "config").iterdir())


def test_run_tests_reports_writes_into_protected_roots(tmp_path):
    """The snapshot's tests run in a clean environment; a write into a watched root fails the run."""
    snap, precious = tmp_path / "snap", tmp_path / "precious"
    (snap / "tests").mkdir(parents=True)
    precious.mkdir()
    (snap / ".gitignore").write_text(".venv/\n__pycache__/\n", encoding="utf-8")
    (snap / "tests" / "test_a.py").write_text(
        "import json, os, pathlib\n"
        "def test_env():\n"
        "    assert not os.listdir(os.environ['SATK_CONFIG'])\n"
        f"    assert json.loads(os.environ['SATK_SAFETY_PROTECTED_ROOTS']) == [{precious.as_posix()!r}]\n"
        "def test_rude():\n"
        f"    pathlib.Path({str(precious)!r}, 'x.txt').write_text('oops')\n", encoding="utf-8")
    _git(snap, "init", "-q")
    res = P.run_tests(snap, tmp_path / "scratch", protect=[precious])
    assert res["summary"].startswith("2 passed"), res
    assert res["ok"] is False and res["touched_protected"][0].endswith("/precious")
    assert not (snap / ".venv").exists() and "left_in_snapshot" in res  # tests/ is untracked here
    (precious / "x.txt").unlink()


def test_checkout_venv_sees_the_host_packages(tmp_path):
    py = P.checkout_venv(tmp_path)
    assert py.is_file() and tmp_path / ".venv" in py.parents
    code = "import sys, pytest; print(sys.prefix != sys.base_prefix)"
    p = subprocess.run([str(py), "-c", code], capture_output=True, text=True, timeout=120,
                       env={k: v for k, v in os.environ.items() if not k.startswith("PYTHON")})
    assert p.returncode == 0 and p.stdout.strip() == "True", p.stderr


# --------------------------------------------------------------------------- built release files


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for k, v in entries.items():
            zf.writestr(k, v)
    return path


def test_check_release_files(tmp_path):
    rules = _rules("/data/manifests/install-*.json\n")
    leak = ("x " + DEV_WS + "\n").encode()
    z = _zip(tmp_path / "satk-1-win64.zip", {
        "satk-1-win64/data/manifests/install-1.json": b"{}", "satk-1-win64/docs/en/a.md": leak,
        "satk-1-win64/python/Lib/site-packages/numpy/config.py": (RUNNER + "/build\n").encode(),
        "satk-1-win64/python/Lib/site-packages/other/x.py": ("p = r'" + DEV_HOME + "'\n").encode(),
    })
    w = _zip(tmp_path / "satk_gta-1-py3-none-any.whl", {"satk/_data/manifests/install-1.json": b"{}",
                                                        "satk/__init__.py": b""})
    sd = tmp_path / "satk_gta-1.tar.gz"
    with tarfile.open(sd, "w:gz") as tf:
        data = b"{}"
        ti = tarfile.TarInfo("satk_gta-1/data/manifests/install-1.json")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    loose = P.check_release_files([z, w, sd], rules, strict=False)
    assert {(f.path.split("!")[0], f.rule) for f in loose} == {(z.name, "excluded"), (w.name, "excluded"),
                                                                (sd.name, "excluded")}
    strict = P.check_release_files([z, w, sd], rules, strict=True)
    paths = {f.path.split("!")[1] for f in strict if f.rule == "machine-path"}
    assert paths == {"satk-1-win64/docs/en/a.md", "satk-1-win64/python/Lib/site-packages/other/x.py"}


# --------------------------------------------------------------------------- operations and sync


def test_export_op_is_cli_only():
    spec = R.get_op("dev.export_public")
    assert spec.mcp is False and spec.group == "dev" and spec.long_running
    params = {p.name: p for p in spec.params}
    assert set(params) == {"ref", "out", "base", "check_only", "tests", "limit"}
    assert all(p.has_default for p in params.values()) and params["tests"].default is True
    from satk.mcp.generic import denial

    assert denial(spec)[0] == "UNSUPPORTED"


def test_sync_tolerates_a_missing_workspace_claude(tmp_path):
    from satk.docs import sync

    (tmp_path / "docs" / "agent").mkdir(parents=True)
    (tmp_path / "docs" / "agent" / "SKILL.md").write_text("skill", encoding="utf-8")
    plan = sync.plan(tmp_path, tmp_path / "ws")
    assert [p.src.name for p in plan] == ["SKILL.md"] and plan[0].state == "missing"
    (tmp_path / "docs" / "agent" / "workspace-CLAUDE.md").write_text("ws", encoding="utf-8")
    assert [p.src.name for p in sync.plan(tmp_path, tmp_path / "ws")] == ["SKILL.md", "workspace-CLAUDE.md"]
    has = (REPO / "docs" / "agent" / "workspace-CLAUDE.md").is_file()  # the public snapshot has no such file
    assert (("docs/agent/workspace-CLAUDE.md", "CLAUDE.md") in sync.PAIRS) == has
