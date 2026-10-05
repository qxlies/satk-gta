"""``satk bug-report`` (M3 A2): redaction, no game files, show before write."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from satk.bugreport import collect as BC
from satk.bugreport import report as BR
from satk.bugreport.redact import Redactor
from satk.core import config as C

USER = "Zaphod Beeblebrox-Тест"  # spaces, a hyphen and Cyrillic, like real Windows accounts
HOST = "HEART-OF-GOLD7"
TOKEN = "f00dfeed" * 8  # a SAAP-like 64-hex token
GAME_MARKER = "SECRET_GAME_BYTES_42"
FILE_MARKER = "secret_mod_name"


def _game(root: Path) -> Path:
    (root / "models").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "gta_sa.exe").write_bytes(b"MZ " + GAME_MARKER.encode() * 4)
    (root / "models" / "gta3.img").write_bytes(b"VER2" + GAME_MARKER.encode())
    (root / "models" / f"{FILE_MARKER}.dff").write_bytes(GAME_MARKER.encode())
    (root / "data" / "gta.dat").write_text(f"# {GAME_MARKER}\n", encoding="utf-8")
    return root


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """A fake user profile holding the workspace and the game; SATK_* point there."""
    home = tmp_path / "Users" / USER
    ws = home / "satk ws"
    game = _game(home / "Games" / "GTA San Andreas")
    (ws / "work" / "logs").mkdir(parents=True)
    for k in list(os.environ):
        if k.startswith("SATK_") and k not in ("SATK_LOG", "SATK_TEST_NO_SKIP", "SATK_DETECT"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SATK_HOME", str(ws))
    monkeypatch.setenv("SATK_CONFIG", "none")
    monkeypatch.setenv("SATK_PATHS_GAME_ROOT", str(game))
    monkeypatch.setenv("SATK_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("USERNAME", USER)
    monkeypatch.setenv("COMPUTERNAME", HOST)
    C.reset()
    log = ws / "work" / "logs" / "errors.log"
    log.write_text(
        "--- 2026-10-01T10:00:00 pid=1 old entry\nTraceback (most recent call last):\nValueError: old\n\n"
        f"--- 2026-10-05T12:00:00 pid=2 index build\nTraceback (most recent call last):\n"
        f'  File "{game}\\data\\gta.dat", line 1\n'
        f'  File "{home.as_posix()}/satk ws/work/x.py", line 2\n'
        f"OSError: token={TOKEN} by {USER} on {HOST} mail zaphod@heart.example.org\n"
        f"Authorization: Bearer abcdefghijklmnop1234\n\n",
        encoding="utf-8")
    yield {"home": home, "ws": ws, "game": game, "tmp": tmp_path}
    C.reset()


def _leaks(text: str, ctx: dict) -> list[str]:
    bad = [USER, USER.split()[0], HOST, TOKEN, "zaphod@heart", "abcdefghijklmnop1234", GAME_MARKER, FILE_MARKER]
    for p in (ctx["home"], ctx["game"], ctx["ws"], ctx["tmp"]):
        bad += [str(p), p.as_posix(), str(p).replace("\\", "\\\\")]
    return [b for b in bad if b.lower() in text.lower()]


def test_written_report_has_no_user_host_tokens_paths_or_game_files(profile, run_cli):
    r = run_cli(["bug-report", "--yes", "--what", f"index build failed for {USER} in {profile['game']}"])
    assert r.code == 0, r.out
    j = r.json
    assert j["written"] is True and "text" not in j
    path = Path(j["path"])
    assert path.parent == profile["ws"] / "work" / "out" / "bugreport" and path.suffix == ".md"
    text = path.read_text(encoding="utf-8")
    assert _leaks(text, profile) == []
    assert "<game>" in text and "<workspace>" in text and "<user>" in text and "<host>" in text
    assert "<redacted>" in text and "<email>" in text
    assert "## satk doctor" in text and "network: none by default" in text
    assert "edition classic" in text and "index build" in text and "old entry" in text
    assert j["redacted"]["game"] >= 1 and j["redacted"]["token"] >= 2 and j["redacted"]["user"] >= 1
    assert [s[0] for s in j["sections"]] == ["What happened", "Environment", "Network", "satk doctor",
                                             "Configuration", "Game", "Operations", "Recent errors"]
    assert _leaks(json.dumps({k: v for k, v in j.items() if k != "path"}, ensure_ascii=False), profile) == []


def test_preview_shows_everything_and_writes_nothing(profile, run_cli):
    r = run_cli(["bug-report", "--logs", "1"])
    assert r.code == 0, r.out
    j = r.json
    assert j["written"] is False and j["text"].startswith("# satk bug report") and "satk bug-report --yes" in j["next"]
    assert not Path(j["path"]).exists() and not (profile["ws"] / "work" / "out").exists()
    assert "old entry" not in j["text"] and "index build" in j["text"]  # --logs 1: only the last entry
    assert _leaks(j["text"], profile) == []  # only "path" (where it would go) names the local folder


def test_interactive_confirmation(profile, monkeypatch):
    monkeypatch.setattr(BR, "_interactive", lambda: True)
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))
    res = BR.bug_report(logs=0)
    assert res["written"] is False and res["cancelled"] is True and not Path(res["path"]).exists()
    shown = err.getvalue()
    assert "# satk bug report" in shown and "[y/N]" in shown and "Nothing is sent anywhere" in shown
    report, _, question = shown.partition("Write this report to ")
    assert _leaks(report, profile) == [] and question.startswith(Path(res["path"]).as_posix())
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    res = BR.bug_report(logs=0, out=str(profile["tmp"] / "out" / "report.md"))
    assert res["written"] is True and (profile["tmp"] / "out" / "report.md").is_file()


def test_out_into_the_game_is_refused(profile, run_cli):
    r = run_cli(["bug-report", "--yes", "--out", str(profile["game"] / "report.md")])
    assert r.code == 1 and r.json["error"]["code"] == "PROTECTED_PATH"
    assert not (profile["game"] / "report.md").exists()
    assert run_cli(["bug-report", "--logs", "99"]).code == 2


def test_redactor_spellings():
    home = r"C:\Users\Иван Петров"
    rd = Redactor({"USERPROFILE": home, "USERNAME": "Иван Петров", "COMPUTERNAME": "IVAN-PC",
                   "GITHUB_TOKEN": "ghp_" + "a" * 36, "SHORT_KEY": "abc"},
                  games=[r"E:\Games\GTA SA"], workspace=r"D:\satk", satk=r"D:\satk\tools")
    text = "\n".join([
        r"C:\Users\Иван Петров\AppData\x.py", "C:/Users/Иван Петров/Desktop", r"c:\\users\\иван петров\\a.json",
        r"C:\Users\Someone Else\file.txt", r"C:\Users\Public\Documents", r"\\?\C:\Users\Other\x",
        r"E:\Games\GTA SA\models\gta3.img", "E:/Games/GTA SA2/keep", r"D:\satk\tools\src\satk\cli.py",
        r"D:\satk\work\out", "user Иван Петров on IVAN-PC", '"token": "0123456789abcdef0123"',
        "export GITHUB_TOKEN=ghp_" + "a" * 36, "sk-ant-api03-" + "x" * 30, "mail me: a.b+c@example.co.uk",
        "max_tokens=20 is fine", "sha256 a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26",
    ])
    out = rd(text).splitlines()
    assert out[0] == r"%USERPROFILE%\AppData\x.py" and out[1] == "%USERPROFILE%/Desktop"
    assert out[2] == r"%USERPROFILE%\\a.json" and out[3] == r"%USERPROFILE%\file.txt"
    assert out[4] == r"C:\Users\Public\Documents" and out[5] == r"\\?\%USERPROFILE%\x"
    assert out[6] == r"<game>\models\gta3.img" and out[7] == "E:/Games/GTA SA2/keep"
    assert out[8] == r"<satk>\src\satk\cli.py" and out[9] == r"<workspace>\work\out"
    assert out[10] == "user <user> on <host>" and out[11] == '"token": "<redacted>"'
    assert out[12] == "export GITHUB_TOKEN=<redacted>" and out[13] == "<redacted>"
    assert out[14] == "mail me: <email>" and out[15] == "max_tokens=20 is fine"
    assert out[16].endswith("0645cbd26")  # hashes are not secrets
    assert rd.counts["user"] >= 5 and rd.counts["token"] >= 3


def test_common_user_names_stay_in_text_but_not_in_paths():
    rd = Redactor({"USERPROFILE": r"C:\Users\Admin", "USERNAME": "Admin"})
    assert rd(r"per-user config in C:\Users\Admin\x for Admin") == r"per-user config in %USERPROFILE%\x for Admin"


def test_error_entries_tail(tmp_path):
    log = tmp_path / "errors.log"
    body = "".join(f"--- 2026-10-0{i}T00:00:00 pid={i} ctx{i}\n" + "".join(f"  line {k}\n" for k in range(60)) + "\n"
                   for i in range(1, 6))
    log.write_text("junk before the first entry\n" + body, encoding="utf-8")
    entries = BC.error_entries(log, 2)
    assert [e[0].split()[-1] for e in entries] == ["ctx4", "ctx5"]
    assert all(len(e) == BC.LOG_ENTRY_LINES and e[1] == "..." and e[-1] == "  line 59" for e in entries)
    assert BC.error_entries(log, 0) == [] and BC.error_entries(tmp_path / "none.log", 3) == []
