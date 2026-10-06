"""Real data: the vanilla main.scm and script.img round-trip bit for bit (read-only; marked game + slow)."""

from __future__ import annotations

import os
import struct

import pytest

from satk.core.errors import SatkError
from satk.core.paths import open_ro
from satk.script.asm import assemble
from satk.script.check import check_program
from satk.script.disasm import decode_program, disassemble, parse_main_header
from satk.script.opdb import load_db

pytestmark = [pytest.mark.game, pytest.mark.slow]


def _skip(reason: str):
    if os.environ.get("SATK_TEST_NO_SKIP") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def full_db():
    for src in ("kb", "cleo-ai"):
        try:
            return load_db(src)
        except SatkError:
            continue
    _skip("no full opcode db: satk kb build, or the cleo-ai clone under src/")


@pytest.fixture(scope="module")
def main_scm(clean_root_module):
    with open_ro(clean_root_module / "data" / "script" / "main.scm") as f:
        return f.read()


@pytest.fixture(scope="module")
def clean_root_module():
    from satk.core import config

    p = config.build().paths.game
    if not (p / "gta_sa.exe").is_file():
        _skip(f"game copy not found: {p}")
    return p


def test_vanilla_main_scm_is_bit_exact(main_scm, full_db):
    d = disassemble(main_scm, "main", full_db)
    prog = d.program
    h = prog.header
    assert len(main_scm) == 3_079_599 and len(h.missions) == 135 and len(h.objects) == 389
    assert len(h.externals) == 79 and h.code_start == 0xDAA8 and h.main_size == 194_146
    assert prog.issues == []                                   # every byte decodes as commands
    assert sum(1 for _ in prog.instrs()) > 350_000
    assert "0053: create_player 0 2488.5623 -1666.8645 12.8757 $2" in d.text
    assert "0213: create_pickup #INFO 3 2027.77 -1420.52 16.49 $669" in d.text
    assert assemble(d.text, full_db).data == main_scm


def test_vanilla_main_scm_has_no_check_errors(main_scm, full_db):
    found = check_program(decode_program(main_scm, "main", full_db))
    assert [f for f in found if f[0] == "error"] == []
    assert not [f for f in found if f[3] == "LOOP_NO_WAIT"]


def test_script_img_entries_are_bit_exact(clean_root_module, main_scm, full_db):
    from satk.formats.img import ImgArchive

    sizes = {raw.split(b"\0", 1)[0].decode().lower(): size for raw, _o, size in parse_main_header(main_scm).externals}
    with ImgArchive.open(clean_root_module / "data" / "script" / "script.img") as a:
        assert len(a.entries) == 79 == len(sizes)
        undecoded = []
        for e in a.entries:
            padded = a.read(e)
            exact = padded[:sizes[e.stem.lower()]]
            for data in (padded, exact):          # sector padding may hold leftover bytes: kept as hex
                d = disassemble(data, "external", full_db)
                assert assemble(d.text, full_db).data == data, e.name
            if d.program.issues and e.stem != "aaa":
                undecoded.append(e.name)
    assert undecoded == []          # the real size decodes fully; aaa.scm is an 8-byte placeholder, not code


def test_core_db_still_round_trips_main_scm(main_scm):
    core = load_db("core")
    d = disassemble(main_scm, "main", core)
    assert d.program.issues                                   # commands outside the subset stay hex
    assert assemble(d.text, core).data == main_scm


def test_installed_cleo_scripts_round_trip(full_db):
    from satk.core import config

    root = config.build().paths.installed
    cleo = root / "cleo"
    files = sorted(cleo.glob("*.cs")) if cleo.is_dir() else []
    if not files:
        pytest.skip(f"no CLEO scripts in {cleo}")
    for p in files:
        with open_ro(p) as f:
            data = f.read()
        d = disassemble(data, "cleo", full_db, name=p.stem)
        assert assemble(d.text, full_db).data == data, p.name
        if data.endswith(b"__SBFTR\x00"):
            n = struct.unpack_from("<I", data, len(data) - 12)[0]
            assert any(o == n for o, _m in d.program.issues), p.name
