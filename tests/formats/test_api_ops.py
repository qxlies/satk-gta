"""F1 contract surface of satk.formats (SPEC §4.2), import hygiene and the CLI operations."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
from pathlib import Path

import pytest

from satk.core.ids import Sid
from satk.core.paths import jpath, profile_root

SRC = Path(__file__).resolve().parents[2] / "src" / "satk" / "formats"

#: SPEC §4.2: module -> public names that must exist (F1 freezes the whole API).
API = {
    "img": ["ImgArchive", "ImgEntry"],
    "rw": ["Chunk", "iter_children", "rw_version", "rw_payload_size", "FormatError"],
    "txd": ["TexInfo", "Txd", "parse_txd", "texture_hash"],
    "dxt": ["decode_rgba", "preview_rgba", "mean_rgba"],
    "dff": ["Material", "Frame", "GeomInfo", "Effect2D", "DffInfo", "scan_dff", "Mesh", "decode_geometry",
            "decode_geometries"],
    "col": ["ColModel", "iter_col"],
    "ide": ["IdeRec", "parse_ide"],
    "ipl": ["Inst", "parse_ipl_text", "parse_ipl_binary"],
    "dat": ["DatLine", "parse_dat", "resolve_ci"],
    "ifp": ["parse_ifp"],
    "zon": ["parse_zon"],
    "layout": ["ArchiveSpec", "archives"],
    "pe": ["pe_sections", "va_to_off"],
    "selftest": ["main"],
}
FIELDS = {
    ("img", "ImgEntry"): ["idx", "name", "stem", "ext", "offset_sectors", "stream_sectors", "archive_sectors"],
    ("rw", "Chunk"): ["type", "size", "libid", "data_off", "end"],
    ("txd", "TexInfo"): ["idx", "name", "mask", "platform", "filter", "uaddr", "vaddr", "raster_fmt", "d3dfmt", "w",
                         "h", "depth", "levels", "alpha", "mip0_off", "mip0_size", "pal_off", "pal_size",
                         "unsupported"],
    ("txd", "Txd"): ["count", "device_id", "rw_version", "textures"],
    ("dff", "Material"): ["geom", "idx", "rgba", "texture", "mask", "fx", "color_slot", "effects"],
    ("dff", "Frame"): ["idx", "parent", "name", "atomic", "matrix"],
    ("dff", "GeomInfo"): ["idx", "rw_flags", "verts", "tris", "uv_sets", "strip", "frame", "bsphere", "geom_off",
                          "bbox"],
    ("dff", "Effect2D"): ["idx", "type", "type_name", "pos", "data"],
    ("dff", "DffInfo"): ["rw_version", "clumps", "atomics", "frames", "geoms", "materials", "verts", "tris", "flags",
                         "plugins", "effects", "bbox", "bsphere"],
    ("dff", "Mesh"): ["positions", "normals", "uv", "prelit", "night", "tris", "mat_ids", "frame"],
    ("col", "ColModel"): ["idx", "version", "name", "hdr_model_id", "size", "bbox", "bsphere", "spheres", "boxes",
                          "verts", "faces", "shadow_faces", "surfaces"],
    ("ide", "IdeRec"): ["sec", "line", "id", "name", "txd", "draw", "flags", "time_on", "time_off", "anim", "extra"],
    ("ipl", "Inst"): ["idx", "model_id", "name", "interior", "pos", "q", "lod"],
    ("dat", "DatLine"): ["key", "arg", "line"],
    ("layout", "ArchiveSpec"): ["relpath", "ns", "load_ref", "order"],
}


@pytest.mark.parametrize("mod", sorted(API))
def test_api_names(mod):
    m = importlib.import_module(f"satk.formats.{mod}")
    for name in API[mod]:
        assert hasattr(m, name), f"satk.formats.{mod}.{name}"


@pytest.mark.parametrize("key", sorted(FIELDS), ids=lambda k: f"{k[0]}.{k[1]}")
def test_dataclass_fields_frozen_slots(key):
    cls = getattr(importlib.import_module(f"satk.formats.{key[0]}"), key[1])
    assert dataclasses.is_dataclass(cls)
    assert [f.name for f in dataclasses.fields(cls)] == FIELDS[key]
    params = cls.__dataclass_params__
    assert params.frozen and hasattr(cls, "__slots__")


def test_signatures():
    from satk.formats import dxt, layout, txd

    assert list(inspect.signature(txd.parse_txd).parameters) == ["buf", "base"]
    assert list(inspect.signature(txd.texture_hash).parameters) == ["buf", "t", "base"]
    assert list(inspect.signature(dxt.decode_rgba).parameters) == ["t", "mip0", "palette", "backend"]
    assert list(inspect.signature(layout.archives).parameters) == ["root", "dat_files", "img_order"]


def test_signatures_f2_f3():
    """F2/F3 implementations keep the F1 positional contract (extra parameters are keyword-only/defaulted)."""
    from satk.formats import col, dff, dxt, ifp, zon

    def pos(f):
        return [n for n, p in inspect.signature(f).parameters.items()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty]

    assert pos(dff.scan_dff) == ["buf"] and pos(dff.decode_geometries) == ["buf"]
    assert pos(dff.decode_geometry) == ["buf", "geom_off"]
    assert pos(col.iter_col) == ["buf"] and pos(ifp.parse_ifp) == ["buf"] and pos(zon.parse_zon) == ["text"]
    assert pos(dxt.preview_rgba) == ["t", "mip0"] and pos(dxt.mean_rgba) == ["t", "mip0"]
    assert dxt.BACKENDS == ("auto", "pillow", "numpy", "python")


def test_no_stubs_left():
    for f in SRC.glob("*.py"):
        assert "NotImplementedError" not in f.read_text(encoding="utf-8"), f.name


def _import_time_nodes(tree: ast.AST):
    """Every node executed at import time: module level including ``try``/``if``/``with`` blocks and
    class bodies, but not function bodies or lambdas (decorators and defaults do run at import)."""
    todo = [tree]
    while todo:
        node = todo.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            todo += node.decorator_list + node.args.defaults + [d for d in node.args.kw_defaults if d]
            continue
        if isinstance(node, ast.Lambda):
            continue
        todo += ast.iter_child_nodes(node)


def _heavy_imports(source: str) -> list[str]:
    heavy = ("PIL", "numpy")
    out = []
    for node in _import_time_nodes(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if fname in ("__import__", "import_module", "require_module") and isinstance(node.args[0].value, str):
                names = [node.args[0].value]
        out += [n for n in names if n.split(".")[0] in heavy]
    return out


def test_no_heavy_imports_at_module_level():
    """Acceptance 7: no PIL/numpy import at module level in src/satk/formats/*.py (also nested in
    ``try``/``if`` blocks or class bodies, or via ``importlib``/``__import__``)."""
    bad = [f"{f.name}: {n}" for f in sorted(SRC.glob("*.py")) for n in _heavy_imports(f.read_text(encoding="utf-8"))]
    assert bad == []


def test_heavy_import_detector_sees_nested_imports():
    nested = """
try:
    import numpy as np
except ImportError:
    np = None
if True:
    from PIL import Image
class K:
    import numpy.linalg
X = __import__("PIL.Image")
import importlib
Y = importlib.import_module("numpy")
def ok():
    import numpy
    from PIL import Image
F = lambda: __import__("numpy")
"""
    assert sorted(_heavy_imports(nested)) == ["PIL", "PIL.Image", "numpy", "numpy", "numpy.linalg"]


def test_pe_on_synthetic_exe():
    import struct

    from satk.formats.pe import image_base, pe_sections, va_to_off
    from satk.formats.rw import FormatError

    buf = bytearray(0x400)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, 0x80)
    buf[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", buf, 0x84, 0x14C, 2)          # machine, sections
    struct.pack_into("<H", buf, 0x80 + 20, 0xE0)         # optional header size
    struct.pack_into("<HI", buf, 0x98, 0x10B, 0)          # PE32 magic
    struct.pack_into("<I", buf, 0x98 + 28, 0x400000)      # image base
    tbl = 0x98 + 0xE0
    struct.pack_into("<8sIIII", buf, tbl, b".text", 0x100, 0x1000, 0x100, 0x200)
    struct.pack_into("<8sIIII", buf, tbl + 40, b".data", 0x300, 0x2000, 0x100, 0x300)
    secs = pe_sections(bytes(buf))
    assert secs == [(".text", 0x1000, 0x100, 0x200, 0x100), (".data", 0x2000, 0x300, 0x300, 0x100)]
    assert image_base(bytes(buf)) == 0x400000
    assert va_to_off(secs, 0x401010) == 0x210
    assert va_to_off(secs, 0x402200) is None      # bss part of .data (beyond raw size)
    assert va_to_off(secs, 0x500000) is None
    with pytest.raises(FormatError):
        pe_sections(b"MZ" + b"\0" * 10)


# ----------------------------------------------------------------------------- CLI
@pytest.fixture
def img_file(tmp_path, b):
    tx = b.txd([b.native("a"), b.native("b")])
    p = tmp_path / "t.img"
    p.write_bytes(b.ver2([(f"m{i}.dff", b.chunk(0x10, b"\0" * 4)) for i in range(25)] + [("a.txd", tx)]))
    return p


def test_cli_ls(run_cli, img_file):
    r = run_cli(["formats", "ls", str(img_file)])
    env = r.json
    assert r.code == 0 and env["n"] == 20 and env["total"] == 26 and env["next"] == "20" and env["version"] == 2
    r2 = run_cli(["formats", "ls", str(img_file), "--cursor", "20"]).json
    assert r2["n"] == 6 and r2["next"] is None
    r3 = run_cli(["formats", "ls", str(img_file), "--ext", "txd"]).json
    assert r3["rows"][0][1] == "a.txd"
    r4 = run_cli(["formats", "ls", str(img_file), "--name", "M1"]).json
    assert r4["total"] == 11  # m1, m10..m19


def test_cli_dump_entry_and_errors(run_cli, img_file):
    r = run_cli(["formats", "dump", f"{img_file}/a.txd", "--level", "full"])
    env = r.json
    assert r.code == 0 and env["kind"] == "txd" and env["textures"] == 2 and len(env["rows"]) == 2
    assert run_cli(["formats", "dump", str(img_file)]).json["entries"] == 26
    miss = run_cli(["formats", "dump", f"{img_file}/nope.dff"])
    assert miss.code == 1 and miss.json["error"]["code"] == "NOT_FOUND"
    broken = run_cli(["formats", "dump", f"{img_file}/m3.dff"])           # a Clump without frames/geometry
    err = broken.json["error"]
    assert broken.code == 1 and err["code"] == "UNSUPPORTED" and err["hint"].endswith("--level tree")
    rw = run_cli(["formats", "dump", f"{img_file}/m3.dff", "--level", "tree"]).json
    assert rw["kind"] == "rw" and rw["top"] == "Clump" and rw["rows"][0][:2] == [0, "Clump"]


def test_cli_dump_text_files(run_cli, tmp_path, b):
    (tmp_path / "x.ipl").write_bytes(b"inst\n1, a, 0, 1, 2, 3, 0, 0, 0, 1, -1\nend\n")
    (tmp_path / "x.ide").write_bytes(b"objs\n1, a, b, 10, 0\nend\n")
    (tmp_path / "b.ipl").write_bytes(b.bnry([(1, 2, 3, 0, 0, 0, 1, 7, 0, -1)]))
    assert run_cli(["formats", "dump", str(tmp_path / "x.ipl")]).json["inst"] == 1
    assert run_cli(["formats", "dump", str(tmp_path / "x.ide")]).json["by_sec"] == {"objs": 1}
    env = run_cli(["formats", "dump", str(tmp_path / "b.ipl"), "--level", "full"]).json
    assert env["format"] == "binary" and env["rows"][0][1] == 7


def test_cli_dump_dff_col_ifp_zon(run_cli, tmp_path, b):
    col = b.col_v23("car_col", 3, faces=[(0, 1, 2, 63)], verts=[(0, 0, 0), (128, 0, 0), (0, 128, 0)])
    g = b.geometry([(0, 0, 0), (1, 0, 0), (0, 1, 0)], lists=[(0, [0, 1, 2])], mats=[b.material(tex="paint")])
    (tmp_path / "car.dff").write_bytes(b.clump([g], col=col))
    env = run_cli(["formats", "dump", str(tmp_path / "car.dff"), "--level", "full"]).json
    assert env["kind"] == "dff" and env["tris"] == 1 and env["textures"] == ["paint"]
    assert env["embedded_col"] == "car_col" and "embedded_col" in env["flags"]
    assert env["rows"][0][2] == "paint" and env["geom_rows"][0][:3] == [0, 3, 1]
    (tmp_path / "x.col").write_bytes(b.col_v1("a") + col)
    env = run_cli(["formats", "dump", str(tmp_path / "x.col"), "--level", "full"]).json
    assert env["kind"] == "col" and env["models"] == 2 and env["by_version"] == {"COLL": 1, "COL3": 1}
    assert [r[1] for r in env["rows"]] == ["a", "car_col"]
    (tmp_path / "p.ifp").write_bytes(b.anp3("ped", [("walk", [("root", 4, 3)])]))
    env = run_cli(["formats", "dump", str(tmp_path / "p.ifp"), "--level", "full"]).json
    assert (env["format"], env["pack"], env["anims"], env["rows"]) == ("ANP3", "ped", 1, [[0, "walk", 1, 3]])
    (tmp_path / "m.zon").write_bytes(b"zone\nLA01, 3, 1, 2, 3, 4, 5, 6, 1, UNUSED\nend\n")
    env = run_cli(["formats", "dump", str(tmp_path / "m.zon"), "--level", "full"]).json
    assert env["zones"] == 1 and env["rows"][0][1] == "LA01"
    bad = run_cli(["formats", "dump", str(tmp_path / "x.col").replace("x.col", "bad.dff")])
    assert bad.code == 1 and bad.json["error"]["code"] == "NOT_FOUND"
    (tmp_path / "bad.dff").write_bytes(b.chunk(0x10, bytes(3)))
    bad = run_cli(["formats", "dump", str(tmp_path / "bad.dff")])
    assert bad.code == 1 and bad.json["error"]["code"] == "UNSUPPORTED"


@pytest.fixture
def dump_samples(b):
    txd = b.txd([b.native("a")])
    geom = b.geometry([(0, 0, 0), (1, 0, 0), (0, 1, 0)], lists=[(0, [0, 1, 2])])
    return {
        "img": ("img", b.ver2([("a.txd", txd)])),
        "txd": ("txd", txd),
        "dff": ("dff", b.clump([geom])),
        "col": ("col", b.col_v1("a")),
        "ifp": ("ifp", b.anp3("ped", [("walk", [("root", 4, 1)])])),
        "ide": ("ide", b"objs\n1, a, b, 10, 0\nend\n"),
        "ipl": ("ipl", b"inst\n1, a, 0, 1, 2, 3, 0, 0, 0, 1, -1\nend\n"),
        "bnry": ("ipl", b.bnry([(1, 2, 3, 0, 0, 0, 1, 7, 0, -1)])),
        "dat": ("dat", b"IMG MODELS\\GTA3.IMG\n"),
        "zon": ("zon", b"zone\nLA01, 3, 1, 2, 3, 4, 5, 6, 1, UNUSED\nend\n"),
        "rw": ("rw", b.chunk(0x01, b"abcd")),
        "tree": ("txd", txd),
    }


@pytest.mark.parametrize("kind", ["img", "txd", "dff", "col", "ifp", "ide", "ipl", "bnry", "dat", "zon", "rw", "tree"])
@pytest.mark.parametrize("inside", [True, False])
def test_cli_dump_absolute_path_identity(run_cli, satk_home, dump_samples, kind, inside):
    root = profile_root("vanilla")
    parent = root if inside else root.with_name(root.name + "-external")
    ext, data = dump_samples[kind]
    path = parent / "Data" / f"MiXeD.{ext.upper()}"
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    level = "tree" if kind == "tree" else "full"
    r = run_cli(["formats", "dump", str(path), "--level", level])
    env = r.json
    assert r.code == 0 and env["kind"] == {"bnry": "ipl", "tree": "rw"}.get(kind, kind), env
    if inside:
        assert env["id"] == f"file:data/mixed.{ext}"
        assert str(Sid.parse(env["id"])) == env["id"]
    else:
        assert "id" not in env
        assert env["path"] == jpath(path)


@pytest.mark.parametrize("profile", ["vanilla", "installed", "samp"])
@pytest.mark.parametrize("target", [r"DATA\.\MiXeD.TXD", "FILE:DATA//MIXED.TXD"])
def test_cli_dump_relative_and_sid_identity(run_cli, satk_home, b, profile, target):
    path = profile_root(profile) / "Data" / "Mixed.txd"
    path.parent.mkdir(parents=True)
    path.write_bytes(b.txd([b.native("a")]))
    r = run_cli(["formats", "dump", target, "--profile", profile])
    assert r.code == 0, r.json
    assert r.json["id"] == "file:data/mixed.txd"
    assert str(Sid.parse(r.json["id"])) == r.json["id"]


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("inside", [True, False])
@pytest.mark.parametrize("level", ["stats", "tree"])
def test_cli_dump_img_entry_identity(run_cli, satk_home, b, version, inside, level):
    root = profile_root("vanilla")
    parent = root if inside else root.with_name(root.name + "-external")
    path = parent / "Models" / "Mixed.IMG"
    path.parent.mkdir(parents=True)
    files = [("MiXeD.TXD", b.txd([b.native("a")]))]
    if version == 1:
        directory, data = b.v1(files)
        path.with_suffix(".dir").write_bytes(directory)
    else:
        data = b.ver2(files)
    path.write_bytes(data)
    r = run_cli(["formats", "dump", f"{path}/MIXED.TXD", "--level", level])
    env = r.json
    assert r.code == 0 and env["kind"] == ("rw" if level == "tree" else "txd"), env
    if inside:
        assert env["id"] == "file:models/mixed.img/mixed.txd"
        assert str(Sid.parse(env["id"])) == env["id"]
    else:
        assert "id" not in env
        assert (env["path"], env["entry"]) == (jpath(path), "MiXeD.TXD")


def test_cli_dump_absolute_path_uses_active_profile(run_cli, satk_home, b):
    path = profile_root("installed") / "Data" / "Mixed.txd"
    path.parent.mkdir(parents=True)
    path.write_bytes(b.txd([]))
    installed = run_cli(["formats", "dump", str(path), "--profile", "installed"]).json
    vanilla = run_cli(["formats", "dump", str(path), "--profile", "vanilla"]).json
    assert installed["id"] == "file:data/mixed.txd"
    assert "id" not in vanilla and vanilla["path"] == jpath(path)


def test_cli_dump_unrepresentable_sid_key_uses_path(run_cli, satk_home, b):
    path = profile_root("vanilla") / "Data" / "a@b.txd"
    path.parent.mkdir(parents=True)
    path.write_bytes(b.txd([]))
    r = run_cli(["formats", "dump", str(path)])
    assert r.code == 0, r.json
    assert "id" not in r.json and r.json["path"] == jpath(path)


def test_cli_selftest_bad_root(run_cli, tmp_path):
    r = run_cli(["formats", "selftest", "--root", str(tmp_path)])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"


def test_cli_selftest_mismatch_exit_code(run_cli, mini_root):
    r = run_cli(["formats", "selftest", "--root", str(mini_root), "--profile", "vanilla", "--quick"])
    env = r.json
    assert r.code == 1 and not env["ok"] and env["failed"] and env["error"]["code"] == "INTERNAL"


def test_cli_dump_canonical_file_sids(run_cli, satk_home, b, tmp_path):
    """Review defect: dump echoed the target (``file:DATA\\\\MAPS\\\\LA\\\\LAe2.IPL``, absolute paths).
    Now any spelling of a file under the profile root gives the canonical SID; other files get ``path``."""
    from satk.core.ids import Sid
    from satk.core.paths import profile_root

    root = profile_root("vanilla")
    (root / "Data" / "Maps" / "LA").mkdir(parents=True)
    (root / "Data" / "Maps" / "LA" / "LAe2.IPL").write_bytes(b"inst\n1, a, 0, 1, 2, 3, 0, 0, 0, 1, -1\nend\n")
    (root / "Models").mkdir()
    dff = b.clump([b.geometry([(0, 0, 0), (1, 0, 0), (0, 1, 0)], lists=[(0, [0, 1, 2])])])
    (root / "Models" / "GTA3.IMG").write_bytes(b.ver2([("Infernus.DFF", dff)]))
    cases = {
        "DATA\\MAPS\\LA\\LAe2.IPL": "file:data/maps/la/lae2.ipl",
        "data/maps/la/lae2.ipl": "file:data/maps/la/lae2.ipl",
        str(root / "Data" / "Maps" / "LA" / "LAe2.IPL"): "file:data/maps/la/lae2.ipl",
        "MODELS\\GTA3.IMG\\Infernus.DFF": "file:models/gta3.img/infernus.dff",
        "file:models/gta3.img/INFERNUS.dff": "file:models/gta3.img/infernus.dff",
        f"{root / 'Models' / 'GTA3.IMG'}/infernus.dff": "file:models/gta3.img/infernus.dff",
        "models/gta3.img": "file:models/gta3.img",
    }
    for target, want in cases.items():
        env = run_cli(["formats", "dump", target]).json
        assert env["id"] == want and str(Sid.parse(env["id"])) == want and "path" not in env, (target, env)
        assert run_cli(["formats", "dump", target, "--level", "tree"]).json.get("id", want) == want
    outside = tmp_path / "x.ide"
    outside.write_bytes(b"objs\n1, a, b, 10, 0\nend\n")
    env = run_cli(["formats", "dump", str(outside)]).json
    assert "id" not in env and env["path"] == outside.as_posix() and env["kind"] == "ide"
    (tmp_path / "o.img").write_bytes(b.ver2([("Infernus.DFF", dff)]))
    env = run_cli(["formats", "dump", f"{tmp_path / 'o.img'}/infernus.dff"]).json
    assert "id" not in env and env["path"].endswith("/o.img") and env["entry"] == "Infernus.DFF"


# --------------------------------------------------------------------------- A1-L7: cwd paths, paging markers


def _ide(n: int) -> bytes:
    return ("objs\n" + "".join(f"{1000 + i}, obj{i}, objtxd, 100, 0\n" for i in range(n)) + "end\n").encode()


def test_cli_dump_relative_path_from_cwd(run_cli, satk_home, tmp_path, monkeypatch):
    here = tmp_path / "proj"
    (here / "data").mkdir(parents=True)
    (here / "data" / "mine.ide").write_bytes(_ide(2))
    monkeypatch.chdir(here)
    env = run_cli(["formats", "dump", "data/mine.ide"]).json
    assert env["ok"] and env["resolved_from"] == "cwd" and env["defs"] == 2 and "warn" not in env
    assert env["path"] == (here / "data" / "mine.ide").as_posix()
    root = profile_root("vanilla")
    (root / "data").mkdir(parents=True)
    (root / "data" / "mine.ide").write_bytes(_ide(3))
    (root / "data" / "game.ide").write_bytes(_ide(4))
    shadow = run_cli(["formats", "dump", "data/mine.ide"]).json        # both exist: the current folder wins
    assert shadow["defs"] == 2 and shadow["warn"][0].startswith("PATH_SHADOWS: data/mine.ide")
    sid = run_cli(["formats", "dump", "file:data/mine.ide"]).json       # a SID is always the game file
    assert sid["defs"] == 3 and sid["resolved_from"] == "profile" and sid["id"] == "file:data/mine.ide"
    game = run_cli(["formats", "dump", "data/game.ide"]).json
    assert game["resolved_from"] == "profile" and game["id"] == "file:data/game.ide"
    miss = run_cli(["formats", "dump", "data/nope.ide"])
    assert miss.code == 1 and "current folder" in miss.json["error"]["msg"]


def test_cli_dump_full_marks_truncation_and_pages(run_cli, satk_home, tmp_path):
    p = tmp_path / "big.ide"
    p.write_bytes(_ide(30))
    first = run_cli(["formats", "dump", str(p), "--level", "full"]).json
    assert len(first["rows"]) == 20 and first["truncated"] == ["defs 20 of 30"] and first["next"] == "20"
    assert first["warn"][0].startswith("TRUNCATED: defs 20 of 30; next page: --cursor 20")
    second = run_cli(["formats", "dump", str(p), "--level", "full", "--cursor", first["next"]]).json
    assert [r[3] for r in second["rows"]] == [f"obj{i}" for i in range(20, 30)]
    assert second["truncated"] == ["defs 21-30 of 30"] and "next" not in second
    whole = run_cli(["formats", "dump", str(p), "--level", "full", "--limit", "50"]).json
    assert len(whole["rows"]) == 30 and "truncated" not in whole and "warn" not in whole
    past = run_cli(["formats", "dump", str(p), "--level", "full", "--cursor", "40"]).json
    assert past["rows"] == [] and past["truncated"] == ["defs none of 30 (cursor past the end)"]
    assert run_cli(["formats", "dump", str(p), "--level", "full", "--cursor", "x"]).code == 2


def test_cli_dump_dff_lists_are_paged_together(run_cli, satk_home, b, tmp_path):
    geoms = [b.geometry([(0, 0, 0), (1, 0, 0), (0, 1, 0)], lists=[(0, [0, 1, 2])]) for _ in range(3)]
    p = tmp_path / "three.dff"
    p.write_bytes(b.clump(geoms))
    env = run_cli(["formats", "dump", str(p), "--level", "full", "--limit", "2"]).json
    assert env["geoms"] == 3 and len(env["geom_rows"]) == 2 and env["next"] == "2"
    assert "geoms 2 of 3" in env["truncated"]
    nxt = run_cli(["formats", "dump", str(p), "--level", "full", "--limit", "2", "--cursor", "2"]).json
    assert [g[0] for g in nxt["geom_rows"]] == [2] and "next" not in nxt


@pytest.mark.game
def test_full_dump_of_premier_is_marked_truncated():
    from satk.core import config as _config
    from satk.formats.ops import formats_dump

    game = _config.build().paths.game
    if not (game / "models" / "gta3.img").is_file():
        pytest.skip(f"no clean copy at {game}")
    env = formats_dump(f"{game.as_posix()}/models/gta3.img/premier.dff", level="full")
    assert env["frames"] == 51 and len(env["frame_names"]) == 20 and env["next"] == "20"
    assert "frames 20 of 51" in env["truncated"] and env["warn"][0].startswith("TRUNCATED: ")
    rest = formats_dump(f"{game.as_posix()}/models/gta3.img/premier.dff", level="full", limit=40, cursor="20")
    assert len(rest["frame_names"]) == 31 and "next" in rest          # materials (107) still have more


@pytest.mark.game
def test_dump_by_index_sid():
    from satk.core.errors import SatkError
    from satk.formats.ops import formats_dump
    from satk.index import api

    api.clear_cache()
    try:
        try:
            env = formats_dump("model:426")
        except SatkError as e:
            if e.code in ("INDEX_MISSING", "NOT_READY"):
                pytest.skip(f"no vanilla index: {e.code}")
            raise
        assert env["resolved_from"] == "index" and env["id"] == "file:models/gta3.img/premier.dff"
        assert env["frames"] == 51 and env["kind"] == "dff"
        assert formats_dump("dff:premier")["id"] == env["id"]
        txd = formats_dump("txd:premier")
        assert txd["kind"] == "txd" and txd["id"] == "file:models/gta3.img/premier.txd"
        with pytest.raises(SatkError) as e:
            formats_dump("model:no_such_model_name")
        assert e.value.code == "NOT_FOUND"
    finally:
        api.clear_cache()      # the real index must not leak into later isolated tests
