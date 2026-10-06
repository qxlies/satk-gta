"""CLI operations in an isolated workspace (bundled core opcode db; synthetic files only)."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from satk.script.asm import assemble  # noqa: E402
from satk.script.opdb import load_db  # noqa: E402


def _ok(res):
    assert res.code == 0, res.out + res.err
    return res.json


def test_new_check_disasm_asm_cycle(satk_home, run_cli):
    env = _ok(run_cli(["script", "new", "spawn_car", "--name", "mycar", "--model", "522", "--opdb", "core"]))
    cs, txt = Path(env["file"]), Path(env["text"])
    out = satk_home / "work" / "out" / "script" / "mycar"
    assert cs == out / "mycar.cs" and txt == out / "mycar.txt" and cs.is_file() and txt.is_file()
    assert env["commands"] > 10 and "cleo folder" in env["install"] and "warn" not in env
    chk = _ok(run_cli(["script", "check", "mycar/mycar.txt", "--opdb", "core"]))
    assert chk["clean"] is True and chk["rows"] == [] and chk["kind"] == "cleo"
    chk = _ok(run_cli(["script", "check", "mycar/mycar.cs", "--opdb", "core"]))
    assert chk["clean"] is True
    # disassembling next to the user's source keeps the source
    dis = _ok(run_cli(["script", "disasm", "mycar/mycar.cs", "--opdb", "core", "--limit", "3"]))
    assert dis["file"].endswith("mycar/mycar.disasm.txt") and dis["exact"] is True and len(dis["head"]) == 3
    assert any(w.startswith("KEPT:") for w in dis["warn"])
    assert txt.read_text(encoding="utf-8").startswith("// mycar: spawn vehicle 522")
    back = _ok(run_cli(["script", "asm", "mycar/mycar.disasm.txt", "--opdb", "core", "--compare", "mycar/mycar.cs"]))
    assert back["same"] is True and back["file"].endswith("mycar/mycar.cs")


def test_asm_errors_and_check_rows(satk_home, run_cli, tmp_path):
    src = tmp_path / "bad.txt"
    src.write_text("{$CLEO .cs}\n03A4: script_name 'BAD'\nwiat 0\n0001: wait 1.5\n", encoding="utf-8")
    res = run_cli(["script", "asm", str(src), "--opdb", "core"])
    assert res.code == 2
    err = res.json["error"]
    assert err["code"] == "BAD_PARAMS" and err["data"]["errors"][0].startswith("line 3: unknown command 'wiat'")
    assert "wait" in err["did_you_mean"]
    rows = _ok(run_cli(["script", "check", str(src), "--opdb", "core"]))["rows"]
    assert [r[:3] for r in rows] == [["error", "line 3", "ASM"], ["error", "line 4", "ASM"]]
    loop = tmp_path / "loop.txt"
    loop.write_text("{$CLEO}\n03A4: script_name 'L'\n:A\n0256: is_player_playing $2\n0002: goto @A\n", encoding="utf-8")
    env = _ok(run_cli(["script", "check", str(loop), "--opdb", "core"]))
    assert env["clean"] is False and env["rows"][0][1:3] == ["line 5", "LOOP_NO_WAIT"]


def test_disasm_refuses_text_and_unknown_files(satk_home, run_cli, tmp_path):
    t = tmp_path / "a.txt"
    t.write_text("{$CLEO}\n0001: wait 0\n", encoding="utf-8")
    assert run_cli(["script", "disasm", str(t), "--opdb", "core"]).json["error"]["hint"].startswith("satk script asm")
    res = run_cli(["script", "disasm", "nope.cs", "--opdb", "core"])
    assert res.json["error"]["code"] == "NOT_FOUND"


def test_out_outside_work_needs_force(satk_home, run_cli, tmp_path):
    db = load_db("core")
    cs = tmp_path / "m.cs"
    cs.write_bytes(assemble("{$CLEO}\n0001: wait 0\n0A93: terminate_this_custom_script\n", db).data)
    dst = tmp_path / "m.txt"
    dst.write_text("mine", encoding="utf-8")
    res = run_cli(["script", "disasm", str(cs), "--out", str(dst), "--opdb", "core"])
    assert res.json["error"]["code"] == "EXISTS"
    _ok(run_cli(["script", "disasm", str(cs), "--out", str(dst), "--opdb", "core", "--force"]))
    assert dst.read_text(encoding="utf-8").startswith("// CLEO script")


def _img(entries: dict[str, bytes]) -> bytes:
    """A VER2 archive with the given entries (each padded to 2048-byte sectors)."""
    names = sorted(entries)
    first = 1
    head = bytearray(b"VER2" + struct.pack("<I", len(names)))
    body = bytearray()
    sec = first
    for n in names:
        d = entries[n]
        k = (len(d) + 2047) // 2048
        head += struct.pack("<IHH24s", sec, k, 0, n.encode())
        body += d.ljust(k * 2048, b"\0")
        sec += k
    return bytes(head.ljust(first * 2048, b"\0") + body)


def test_script_img_entry_trimmed_by_main_scm(satk_home, run_cli, tmp_path):
    db = load_db("core")
    ext = assemble("{$EXTERNAL}\n03A4: script_name 'EXT'\n:L\n0001: wait 0\n0002: goto @L\n", db).data
    main = assemble("{$MAIN}\nDEFINE GLOBALS_SIZE 16\nDEFINE OBJECTS 1\nDEFINE OBJECT (noname)\nDEFINE MISSIONS 0\n"
                    "DEFINE EXCLUSIVE_MISSIONS 0\nDEFINE MISSION_LOCALS 0\nDEFINE EXTERNAL_SCRIPTS 1\n"
                    f"DEFINE LARGEST_EXTERNAL_SIZE {len(ext)}\nDEFINE SCRIPT EXT OFFSET 999 SIZE {len(ext)}\n"
                    "DEFINE UNKNOWN_EMPTY_SEGMENT 0\nDEFINE UNKNOWN_THREADS_MEMORY 0\n"
                    "03A4: script_name 'MAIN'\n:M\n0001: wait 0\n0002: goto @M\n", db).data
    (tmp_path / "script.img").write_bytes(_img({"ext.scm": ext}))
    (tmp_path / "main.scm").write_bytes(main)
    img = str(tmp_path / "script.img")
    res = run_cli(["script", "disasm", img, "--opdb", "core"])
    assert res.json["error"]["did_you_mean"] == ["ext.scm"]
    env = _ok(run_cli(["script", "disasm", img, "--entry", "ext", "--opdb", "core"]))
    assert env["kind"] == "external" and env["padding_trimmed"] == 2048 - len(ext) and env["bytes"] == len(ext)
    assert env["exact"] is True and env["head"][0] == "{$EXTERNAL}"
    m = _ok(run_cli(["script", "disasm", str(tmp_path / "main.scm"), "--opdb", "core"]))
    assert m["kind"] == "main" and m["exact"] is True
    chk = _ok(run_cli(["script", "check", img, "--entry", "ext.scm", "--opdb", "core"]))
    assert chk["clean"] is True and chk["kind"] == "external"
