"""engine sites-check / sites-scan against the real stock exe and the Ghidra research data (read-only).

Skipped without the clean copy (marker ``game``); the research-data tests also skip without the Ghidra export
and the symbol DB.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import appendix_a as A  # noqa: E402
from satk.engine import securom, sites_data  # noqa: E402
from satk.engine.sites_check import check_manifest  # noqa: E402
from satk.engine.sites_manifest import parse_manifest  # noqa: E402
from satk.engine.sites_scan import scan_array  # noqa: E402
from satk.re.pe import PeImage  # noqa: E402

pytestmark = pytest.mark.game


def _hex(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


@pytest.fixture(scope="module")
def image() -> PeImage:
    from satk.core import config

    exe = config.build().paths.game / "gta_sa.exe"
    if not exe.is_file():
        pytest.skip(f"game copy not found: {exe}")
    return PeImage.open(exe)


def test_stock_exe_is_the_expected_one(image):
    assert image.sha256 == securom.STOCK_EXE_SHA256


def test_identity_anchors(image):
    """The seven anchors of the design (section 2.5) hold in the stock exe."""
    for va, hx in A.ANCHORS:
        assert image.read(va, len(_hex(hx))) == _hex(hx), hex(va)


def test_every_appendix_a_row_with_explicit_bytes(image):
    bad = [(hex(va), hx, image.read(va, len(_hex(hx))).hex(" ").upper())
           for va, hx in A.GOLDEN_ROWS if image.read(va, len(_hex(hx))) != _hex(hx)]
    assert not bad, bad
    assert len(A.GOLDEN_ROWS) > 100


def test_securom_key_set_has_256_addresses(image, tmp_path):
    scan = securom.load_keys(image, tmp_path / "keys.json")
    assert len(scan.keys) == len(set(scan.keys)) == securom.EXPECTED_KEY_COUNT == 256
    assert scan.reads == 302 and scan.unresolved == 2
    assert all(0x400000 <= k < 0x2000000 for k in scan.keys)
    # a second call is served from the cache and gives the same set
    assert securom.load_keys(image, tmp_path / "keys.json").keys == scan.keys


# --------------------------------------------------------------------------- research data


def _research_work() -> Path:
    """The workspace ``work`` folder that holds the Ghidra export and the symbol DB.

    The gate runs the game tests with a private ``SATK_PATHS_WORK``; the research data lives in the workspace the
    checkout belongs to, so look there first.
    """
    from satk.core import config

    ws = config.derived_workspace()
    if ws is not None and (ws.path / "work" / "re" / "ghidra").is_dir():
        return ws.path / "work"
    return Path(config.build().paths.work)


@pytest.fixture(scope="module")
def research() -> Path:
    gd = _research_work() / "re" / "ghidra"
    needed = [gd / "symbols" / "hoodlum_map.json", gd / "export" / "functions.jsonl", gd / "export" / "calls.jsonl",
              gd / "export" / "xrefs_data.jsonl", _research_work() / "re" / "symdb.sqlite"]
    missing = [p for p in needed if not p.is_file()]
    if missing:
        pytest.skip(f"research data missing: {missing[0]}")
    return gd


@pytest.fixture(scope="module")
def inputs(image, research, tmp_path_factory):
    return sites_data.build_inputs(image, research, _research_work() / "re" / "symdb.sqlite",
                                   tmp_path_factory.mktemp("keys") / "securom_keys.json")


def test_research_data_counts(inputs):
    assert len(inputs.keys.keys) == 256
    assert len(inputs.stolen) == 745
    assert len(inputs.relocs) == 500
    assert len(inputs.trunk) == 2421
    assert all(r.end > r.entry for r in inputs.relocs)


def _manifest(inputs):
    toml = A.manifest_toml(inputs.image.sha256, lambda va, n: inputs.image.read(va, n))
    man, errs = parse_manifest(toml.encode("utf-8"))
    assert not errs, [e.row() for e in errs]
    return man, toml


def test_sites_check_passes_on_the_design_seed(inputs):
    man, _ = _manifest(inputs)
    res = check_manifest(man, inputs, None, check_header=False)
    assert res.ok, [e.row() for e in res.errors]
    assert not res.warnings, [w.row() for w in res.warnings]
    assert res.groups >= 25 and res.sites > 140


def test_trunk_overlaps_are_found_unless_named(inputs):
    """Four seed sites sit on trunk memputs (the design names them in allow_overlap)."""
    man, _ = _manifest(inputs)
    for s in man.sites():
        s.allow_overlap = ()
    res = check_manifest(man, inputs, None, check_header=False)
    bad = {(e.check, e.where) for e in res.errors}
    assert bad == {("OVERLAP_TRUNK", "cap.lists/rwobj_alloc"), ("OVERLAP_TRUNK", "cap.lists/rwobj_loop"),
                   ("OVERLAP_TRUNK", "cap.lists/matrix"), ("OVERLAP_TRUNK", "cap.pools/quadtree")}


def _probe(inputs, va: int, n: int = 1, kind: str = "imm8"):
    man, _ = _manifest(inputs)
    from satk.engine.sites_manifest import Group, Site

    img = inputs.image
    g = Group("probe.group", 91999, "ctor", sites=[Site("p", va, n, kind, golden_va=va, golden=img.read(va, n),
                                                       group="probe.group")])
    man.groups = [g]
    return check_manifest(man, inputs, None, check_header=False)


def test_real_securom_key_window_is_refused(inputs):
    k = inputs.keys.keys[10]
    for va, n in ((k, 1), (k + 3, 1), (k - 1, 2)):
        codes = {e.check for e in _probe(inputs, va, n, "code").errors}
        assert "OVERLAP_KEY" in codes, hex(va)
    assert "OVERLAP_KEY" not in {e.check for e in _probe(inputs, k - 1, 1).errors}
    assert "OVERLAP_KEY" not in {e.check for e in _probe(inputs, k + 4, 1).errors}


def test_real_stolen_site_is_refused(inputs):
    s = inputs.stolen[0]
    assert "OVERLAP_STOLEN" in {e.check for e in _probe(inputs, s.jmp_at + 1).errors}


def test_real_relocated_function_slot_is_refused(inputs):
    """A byte behind a relocated entry still holds the old code, so the golden check passes, but patching it is useless."""
    r = next(x for x in inputs.relocs if x.end - x.entry > 12)
    res = _probe(inputs, r.entry + 8)
    codes = {e.check for e in res.errors}
    assert "SITE_IN_RELOCATED_FUNCTION" in codes and "GOLDEN_MISMATCH" not in codes


def test_design_seed_sites_are_outside_every_relocated_slot(inputs):
    man, _ = _manifest(inputs)
    res = check_manifest(man, inputs, None, check_header=False)
    assert "SITE_IN_RELOCATED_FUNCTION" not in {e.check for e in res.errors}


# --------------------------------------------------------------------------- sites-scan reproduces the design cases


@pytest.fixture(scope="module")
def scan(image, inputs, research):
    gd = research
    funcs = sites_data.load_functions(gd / "export" / "functions.jsonl")
    callers = sites_data.load_callers(gd / "export" / "calls.jsonl")

    def run(base, size, stride):
        return scan_array(image, gd / "export" / "xrefs_data.jsonl", base, size, stride, funcs=funcs, callers=callers,
                          relocs=inputs.relocs)

    return run


def _by_class(res, cls):
    return sorted(c.va for c in res.candidates if c.cls == cls)


def test_scan_visible_lod_array(scan):
    res = scan(0xB748F8, 4000, 4)
    assert _by_class(res, "operand-in-array") == [0x5534F5, 0x553923, 0x553CB3]
    # the entity list starts where the LOD list ends: its operands must never be patched for the LOD array
    assert _by_class(res, "variable-after-array") == [0x553529, 0x553944, 0x553A53, 0x553B03]
    assert not _by_class(res, "true-end") and not _by_class(res, "end-plus-field")


def test_scan_corona_array(scan):
    res = scan(0xC3E058, 0xF00, 0x3C)
    assert len(_by_class(res, "operand-in-array")) == 40
    assert _by_class(res, "true-end") == [0x6FAEB7]
    end_field = _by_class(res, "end-plus-field")
    assert 0x6FB657 in end_field and 0x6FB9B8 in end_field
    var = _by_class(res, "variable-after-array")
    assert 0x6FAE71 in var and 0x6FAE9B in var
    # the extra end-plus-field hit is the bound of the next object and says so
    other = [c for c in res.candidates if c.cls == "end-plus-field" and c.va not in (0x6FB657, 0x6FB9B8)]
    assert all("no-array-ref-in-function" in c.flags for c in other)


def test_scan_stored_shadows_array(scan):
    res = scan(0xC40430, 0x9C0, 0x34)
    poly = [c for c in res.candidates if c.value == 0xC40DF0]
    assert poly and all(c.cls == "variable-after-array" for c in poly)
    assert 0xC40DF0 not in {c.value for c in res.candidates if c.cls == "operand-in-array"}
    assert len(_by_class(res, "operand-in-array")) >= 15


def test_scan_plant_triangles_array(scan):
    res = scan(0xC03A48, 0x5400, 0x54)
    ops = [c for c in res.candidates if c.cls == "operand-in-array"]
    assert 0x5DD7B2 in {c.va for c in ops}
    reload_ops = [c for c in ops if c.func_name.endswith("CPlantMgr::ReloadConfig")]
    assert len(reload_ops) >= 6
    static = {c.va for c in ops if "static-init" in c.flags}
    assert {0x850952, 0x85672D} <= static
    assert all(c.func_name.endswith("CPlantMgr::ReloadConfig") or c.va in static for c in ops)
