"""``re nodes`` (frame-name tables of the exe), the ``re src`` knowledge-base fallback and ``re limits --summary``.

The tables are read from a synthetic image (invented names and numbers); the real executable is checked by
the ``game``-marked test at the end.
"""

from __future__ import annotations

import struct

import pytest

from satk.core import config as _config
from satk.core.errors import SatkError

_REAL = _config.build()  # captured before satk_home isolates the environment


class FakeImage:
    """``read``/``u32`` over a sparse memory map (what :mod:`satk.re.nodes` uses of ``PeImage``)."""

    def __init__(self) -> None:
        self.mem: dict[int, int] = {}

    def put(self, va: int, data: bytes) -> None:
        for i, b in enumerate(data):
            self.mem[va + i] = b

    def read(self, va: int, n: int) -> bytes:
        out = bytearray()
        for i in range(n):
            if va + i not in self.mem:
                break
            out.append(self.mem[va + i])
        return bytes(out)

    def u32(self, va: int) -> int | None:
        b = self.read(va, 4)
        return struct.unpack("<I", b)[0] if len(b) == 4 else None


def _fake() -> FakeImage:
    from satk.re import nodes as N

    img = FakeImage()
    strings = 0x900000
    tables = 0x910000

    def name(s: str) -> int:
        nonlocal strings
        va = strings
        img.put(va, s.encode() + b"\0")
        strings += len(s) + 1
        return va

    def table(va: int, rows: list[tuple[str, int, int]]) -> None:
        for k, (n, i, f) in enumerate(rows):
            img.put(va + 12 * k, struct.pack("<IiI", name(n), i, f))
        img.put(va + 12 * len(rows), bytes(12))

    car = [("chassis", 1, 0), ("wheel_lf_dummy", 5, 0x24), ("ped_frontseat", 4, 0x9), ("extra1", 0, 0x601)]
    for i, _t in enumerate(N.VEHICLE_TYPES):
        va = tables + 0x400 * i
        img.put(N.DESCS_VA + 4 * i, struct.pack("<I", tables if i == 11 else va))  # trailer shares automobile's
        if i != 11:
            table(va, car[: 1 + i % 4])
    table(N.PED_IDS_VA, [("Smid", 1, 0), ("Shead", 2, 0)])
    return img


@pytest.fixture
def fake_exe(monkeypatch):
    from satk.re import nodes as N

    monkeypatch.setattr(N, "_open", lambda exe: (_fake(), N.default_exe()))
    names = {"eCarNodes": {1: "CAR_CHASSIS", 5: "CAR_WHEEL_LF"}, "eVehicleDummy": {4: "DUMMY_SEAT_FRONT"},
             "ePedNode": {1: "PED_NODE_UPPER_TORSO"}}
    monkeypatch.setattr(N, "_enum_names", lambda owner: names.get(owner, {}))


def test_nodes_overview(satk_home, run_cli, fake_exe):
    env = run_cli(["re", "nodes", "--json"]).json
    assert env["ok"] and env["vehicle_tables"] == 12 and env["total"] == 13
    rows = {r[0]: r for r in env["rows"]}
    assert rows["automobile"][2:6] == [1, 1, 0, 0]
    assert rows["bike"][2:6] == [2, 2, 0, 0]          # index 9 -> 2 rows
    assert rows["trailer"][6] == "automobile"           # same table address
    assert rows["ped"][1] == "0x8A6268" and rows["ped"][3] == 2


def test_nodes_one_type(satk_home, run_cli, fake_exe):
    env = run_cli(["re", "nodes", "--type", "plane", "--json"]).json   # index 4 -> the first row only
    assert env["type"] == "plane" and env["rows"] == [["chassis", 1, "part", "0x0", "-", None]]
    env = run_cli(["re", "nodes", "--type", "quad", "--json"]).json     # index 2 -> 3 rows
    assert env["rows"][2] == ["ped_frontseat", 4, "dummy", "0x9", "struct|dummy", "DUMMY_SEAT_FRONT"]
    trailer = run_cli(["re", "nodes", "--type", "trailer", "--json"]).json   # shares automobile's table
    assert trailer["rows"] == [["chassis", 1, "part", "0x0", "-", None]]   # no eTrailerNodes names here
    heli = run_cli(["re", "nodes", "--type", "heli", "--json"]).json        # index 3 -> 4 rows
    assert heli["rows"][3][:5] == ["extra1", 0, "extra", "0x601", "struct|extra|alpha"]
    ped = run_cli(["re", "nodes", "--type", "ped", "--json"]).json
    assert ped["rows"][0] == ["Smid", 1, "node", "0x0", "-", "PED_NODE_UPPER_TORSO"]
    assert run_cli(["re", "nodes", "--type", "tank", "--json"]).code == 2


def test_nodes_without_kb_warn(satk_home, run_cli, fake_exe, monkeypatch):
    from satk.re import nodes as N

    monkeypatch.setattr(N, "_enum_names", lambda owner: {})
    env = run_cli(["re", "nodes", "--type", "automobile", "--json"]).json
    assert env["warn"][0].startswith("NO_KB") and "named_by" not in env and env["rows"][0][:2] == ["chassis", 1]


def test_nodes_reject_other_layouts(satk_home, tmp_path):
    from satk.re.nodes import nodes

    with pytest.raises(SatkError) as e:
        nodes(None, str(tmp_path / "missing.exe"))
    assert e.value.code == "NOT_FOUND"
    bad = tmp_path / "gta_sa.exe"
    bad.write_bytes(b"MZ" + bytes(200))
    with pytest.raises(SatkError) as e:
        nodes(None, str(bad))
    assert e.value.code == "UNSUPPORTED"


def test_src_falls_back_to_the_kb_location(built, run_cli, monkeypatch):
    import satk.kb.query as Q

    def loc(key):
        if key in ("CDoor::Swing", 0x401090):
            return {"name": "CDoor::Swing", "addr": 0x401090, "sig": "void CDoor::Swing()", "src": "gta-reversed",
                    "path": "source/game_sa/Door2.cpp", "line": 6, "loc": "source/game_sa/Door2.cpp:6", "hook": False,
                    "reversed": None}
        return None

    monkeypatch.setattr(Q, "func_location", loc)
    env = run_cli(["re", "src", "CDoor::Swing", "--context", "2", "--json"]).json
    assert env["ok"] and env["via"] == "kb" and env["file"] == "game_sa/Door2.cpp" and env["line"] == 6
    assert env["status"].startswith("not reversed") and env["note"].endswith("at game_sa/Door2.cpp:6")
    assert env["lines"][0].startswith("3: ") and any("CDoor::Open(float ratio)" in x for x in env["lines"])
    assert env["id"] == "fn:0x401090"
    miss = run_cli(["re", "src", "CDoor::Nothing", "--json"])
    assert miss.code == 1 and miss.json["error"]["code"] == "NOT_FOUND"


def test_src_without_kb_keeps_the_symdb_answer(built, run_cli, monkeypatch):
    import satk.kb.query as Q

    def not_ready(key):
        raise SatkError("NOT_READY", "knowledge base not built")

    monkeypatch.setattr(Q, "func_location", not_ready)
    assert run_cli(["re", "src", "CFoo::Bar", "--json"]).json["file"] == "game_sa/Foo.cpp"
    r = run_cli(["re", "src", "CDoor::Swing", "--json"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    assert run_cli(["re", "src", "CFoo::Bar", "--context", "401", "--json"]).code == 2


def test_limits_default_page_and_summary(built, run_cli):
    from satk.core.registry import get_op

    assert get_op("re.limits").param("limit").default == 20
    env = run_cli(["re", "limits", "--summary", "--json"]).json
    assert env["ok"] and "rows" not in env and env["total"] >= 1 and env["by_kind"]
    assert sum(env["by_kind"].values()) == env["total"]


@pytest.mark.game
def test_real_exe_tables():
    from satk.re.nodes import nodes

    exe = _REAL.paths.game / "gta_sa.exe"
    if not exe.is_file():
        pytest.skip(f"no {exe}")
    env = nodes(None, str(exe))
    rows = {r[0]: r for r in env["rows"]}
    assert env["vehicle_tables"] == 12 and len(rows) == 13
    assert rows["automobile"][2:6] == [61, 40, 15, 6] and rows["ped"][2] == 12
    car = nodes("automobile", str(exe))
    names = [r[0] for r in car["rows"]]
    assert names[:2] == ["chassis", "wheel_rf_dummy"] and "ped_frontseat" in names and "extra1" in names
    seat = next(r for r in car["rows"] if r[0] == "ped_frontseat")
    assert seat[1:5] == [4, "dummy", "0x9", "struct|dummy"]
    bike = nodes("bike", str(exe))
    assert [r[0] for r in bike["rows"]][:5] == ["chassis_dummy", "forks_front", "forks_rear", "wheel_front",
                                                "wheel_rear"]


def test_src_without_symdb_uses_the_kb_alone(satk_home, run_cli, monkeypatch, tmp_path):
    import satk.kb.query as Q
    from satk.re import api
    from satk.re.gitsrc import DirTree

    src = tmp_path / "gr"
    (src / "source" / "game_sa").mkdir(parents=True)
    (src / "source" / "game_sa" / "Thing.cpp").write_text("// 0x401234\nvoid CThing::Go() {\n    stub();\n}\n",
                                                           encoding="utf-8")
    monkeypatch.setattr(Q, "func_location", lambda key: {
        "name": "CThing::Go", "addr": 0x401234, "sig": None, "src": "gta-reversed", "path": "source/game_sa/Thing.cpp",
        "line": 2, "loc": "source/game_sa/Thing.cpp:2", "hook": False, "reversed": None} if key == "CThing::Go" else None)
    monkeypatch.setattr(api, "_kb_tree", lambda kind: DirTree(src))
    env = run_cli(["re", "src", "CThing::Go", "--json"]).json
    assert env["ok"] and env["via"] == "kb" and env["warn"][0].startswith("NO_SYMDB: ")
    assert env["lines"][:2] == ["1: // 0x401234", "2: void CThing::Go() {"]
    miss = run_cli(["re", "src", "CThing::Stop", "--json"])
    assert miss.code == 3 and miss.json["error"]["code"] == "NOT_READY"


def test_inherited_member_and_kb_rows_in_find(built, run_cli, monkeypatch):
    import satk.kb.query as Q

    monkeypatch.setattr(Q, "class_bases", lambda cls: ["CFoo", "CEntity"] if cls == "CSub" else [])
    monkeypatch.setattr(Q, "func_location", lambda key: {
        "name": "CDoor::Swing", "addr": 0x401090, "sig": None, "src": "gta-reversed", "path": "source/game_sa/Door2.cpp",
        "line": 6, "loc": "source/game_sa/Door2.cpp:6", "hook": False, "reversed": None}
        if key in ("CDoor::Swing", 0x401090) else None)
    env = run_cli(["re", "src", "CSub::Bar", "--json"]).json         # declared in the base class CFoo
    assert env["ok"] and env["fn"] == "CFoo::Bar" and env["inherited"].startswith("CSub::Bar is not declared in CSub")
    found = run_cli(["re", "find", "CSub::Bar", "--json"]).json
    assert found["rows"] == [["fn:0x401000", "func", "CFoo::Bar", "inherited: CSub::Bar is served by CFoo::Bar"]]
    kb = run_cli(["re", "find", "CDoor::Swing", "--json"]).json         # not hooked: the kb names it
    assert kb["rows"] == [["fn:0x401090", "func", "CDoor::Swing", "kb: not reversed at game_sa/Door2.cpp:6"]]
    assert kb["note"].startswith("no symbol DB match")
    assert run_cli(["re", "find", "CSub::Nothing", "--json"]).json["total"] == 0
    assert run_cli(["re", "find", "CDoor::Swing", "--kind", "global", "--json"]).json["total"] == 0


def test_not_found_suggests_similar_names(built, run_cli):
    import sqlite3

    from satk.re.api import _similar_funcs, _words

    assert _words("FindEditableMaterialList") == {"find", "editable", "material", "list"}
    assert _words("SetEditableMaterialsCB") == {"set", "editable", "material", "cb"}

    class Db:
        con = sqlite3.connect(":memory:")

    Db.con.execute("CREATE TABLE func(qual TEXT)")
    Db.con.executemany("INSERT INTO func VALUES (?)", [(q,) for q in (
        "CVehicleModelInfo::SetEditableMaterials", "CVehicleModelInfo::ResetEditableMaterials",
        "CVehicleModelInfo::SetupCommonData", "_rpMaterialListFindMaterialIndex", "CPed::FindEditable")])
    got = _similar_funcs(Db(), "CVehicleModelInfo::FindEditableMaterialList")
    assert got[:2] == ["CVehicleModelInfo::SetEditableMaterials", "CVehicleModelInfo::ResetEditableMaterials"]
    assert "CVehicleModelInfo::SetupCommonData" not in got and _similar_funcs(Db(), "CFoo::Go") == []
    assert "did_you_mean" not in run_cli(["re", "find", "NoSuchThingAnywhere", "--json"]).json
    miss = run_cli(["re", "src", "CFoo::Bax", "--json"]).json         # a member typo: the class's members
    assert miss["error"]["code"] == "NOT_FOUND" and miss["error"]["hint"].startswith("satk ")
    err = run_cli(["re", "src", "CFoo::Bar", "--context", "700", "--json"]).json["error"]
    assert err["code"] == "BAD_PARAMS" and "0..400" in err["msg"] and "400" in err["hint"]
