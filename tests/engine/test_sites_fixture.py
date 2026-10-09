"""engine sites-check / sites-gen / sites-scan on synthetic data (fake PE, fake maps; no game files)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine_fakepe as fx  # noqa: E402
from satk.core.errors import SatkError
from satk.engine import securom, sites_run
from satk.engine.sites_check import check_manifest, parse_allow
from satk.engine.sites_data import FuncInfo, RelocFunc, TrunkPatch, load_hoodlum_map, reloc_extents
from satk.engine.sites_gen import generate_header, header_first_line
from satk.engine.sites_manifest import parse_manifest, text_sha256
from satk.engine.sites_scan import classify, scan_array
from satk.engine.x86 import decode


@pytest.fixture(scope="module")
def image():
    return fx.fixture_image()


def _run(toml: str, image, *, trunk=None, header="fresh"):
    man, errs = parse_manifest(toml.encode("utf-8"))
    assert man is not None
    inputs = fx.fixture_inputs(image, trunk)
    text = generate_header(man) if header == "fresh" else header
    res = check_manifest(man, inputs, text)
    return errs, res


def _codes(toml: str, image, **kw) -> set[str]:
    errs, res = _run(toml, image, **kw)
    return {e.check for e in errs} | {e.check for e in res.errors}


def _good(image) -> str:
    return fx.manifest_text(image.sha256)


# --------------------------------------------------------------------------- SecuROM keys


def test_key_search_finds_the_key_of_the_stub(image):
    scan = securom.find_keys(image)
    assert scan.keys == (fx.KEY_VA,)
    assert scan.reads == 1 and scan.unresolved == 0
    assert scan.windows() == [(fx.KEY_VA, fx.KEY_VA + securom.KEY_WINDOW)]


def test_key_cache_round_trip(image, tmp_path, monkeypatch):
    cache = tmp_path / "keys.json"
    a = securom.load_keys(image, cache)
    doc = json.loads(cache.read_text(encoding="utf-8"))
    assert doc["exe_sha256"] == image.sha256 and doc["keys"] == [f"0x{fx.KEY_VA:X}"] and doc["key_window"] == 4
    monkeypatch.setattr(securom, "find_keys", lambda *a, **k: pytest.fail("cache not used"))
    b = securom.load_keys(image, cache)
    assert b.keys == a.keys
    # another exe hash recomputes
    monkeypatch.undo()
    other = fx.build_pe([(".text", fx.TEXT_VA, bytes(fx.fixture_text()) + b"\x00", 0x1000)])
    from satk.re.pe import PeImage

    assert securom.load_keys(PeImage(other), cache).keys == (fx.KEY_VA,)


def test_stock_key_count_is_asserted():
    with pytest.raises(SatkError) as e:
        securom._assert_count(securom.KeyScan((1, 2, 3), 3, 0), True)
    assert e.value.code == "CHECK_FAILED" and "256" in e.value.msg
    securom._assert_count(securom.KeyScan(tuple(range(securom.EXPECTED_KEY_COUNT)), 256, 0), True)
    securom._assert_count(securom.KeyScan((1,), 1, 0), False)  # fixtures are not asserted


# --------------------------------------------------------------------------- sites-check


def test_good_manifest_passes(image):
    errs, res = _run(_good(image), image)
    assert not errs and res.ok, [e.row() for e in res.errors]
    assert res.groups == 2 and res.sites == 3 and res.written_sites == 2  # anchors are read-only
    assert not res.warnings


def test_toml_syntax_error(image):
    man, errs = parse_manifest(b"schema = = 1")
    assert man is None and errs[0].check == "TOML_PARSE"


def _edit(toml: str, old: str, new: str) -> str:
    assert old in toml
    return toml.replace(old, new, 1)


def test_schema_errors(image):
    t = _good(image)
    for bad, needle in (
        (_edit(t, "schema = 1", "schema = 2"), "schema must be 1"),
        (_edit(t, 'phase = "ctor"', 'phase = "late"'), "phase must be"),
        (_edit(t, 'kind = "imm32"', 'kind = "imm64"'), "kind must be"),
        (_edit(t, "len = 4", 'len = "four"'), "len must be an integer"),
        (_edit(t, 'note = "push 140"', 'note = "push 140"\n  colour = "red"'), "unknown site key"),
        (_edit(t, 'lane = "E2"', 'lane = "E2"\n  colour = 1'), "unknown group key"),
        (_edit(t, f'exe_sha256 = "{image.sha256}"', 'exe_sha256 = "XYZ"'), "exe_sha256 must be"),
    ):
        man, errs = parse_manifest(bad.encode("utf-8"))
        assert any(e.check == "SCHEMA" and needle in e.msg for e in errs), (needle, [e.row() for e in errs])


def test_hex_golden_tokens(image):
    t = _edit(_good(image), 'golden = "6A 40 8B C8 E8"', 'golden = "6A 4 8B C8 E8"')
    _, errs = parse_manifest(t.encode("utf-8"))
    assert any(e.check == "SCHEMA" and "golden" in e.msg for e in errs)
    t = _edit(_good(image), 'golden = "6A 40 8B C8 E8"', 'golden = "6A xx 8B C8 E8"')
    _, errs = parse_manifest(t.encode("utf-8"))
    assert any("only allowed in alt" in e.msg for e in errs)


def test_unique_ids_and_report_ids(image):
    t = _good(image)
    dup_group = t + '\n[[group]]\nid = "cap.pools"\nreport_id = 91999\nphase = "ctor"\n  [[group.site]]\n  id = "x"\n  va = 0x401060\n  len = 1\n  kind = "imm8"\n  golden_va = 0x401060\n  golden = "56"\n'
    assert "DUP_GROUP_ID" in _codes(dup_group, image)
    dup_report = t + '\n[[group]]\nid = "cap.other"\nreport_id = 91101\nphase = "ctor"\n  [[group.site]]\n  id = "x"\n  va = 0x401060\n  len = 1\n  kind = "imm8"\n  golden_va = 0x401060\n  golden = "56"\n'
    assert "DUP_REPORT_ID" in _codes(dup_report, image)
    dup_site = t + '\n  [[group.site]]\n  id = "pool.ped"\n  va = 0x401060\n  len = 1\n  kind = "imm8"\n  golden_va = 0x401060\n  golden = "56"\n'
    assert "DUP_SITE_ID" in _codes(dup_site, image)
    # dots and underscores collide as C++ identifiers
    clash = t + '\n  [[group.site]]\n  id = "pool_ped"\n  va = 0x401060\n  len = 1\n  kind = "imm8"\n  golden_va = 0x401060\n  golden = "56"\n'
    assert "DUP_SITE_ID" in _codes(clash, image)


def test_id_syntax_and_report_range(image):
    t = _good(image)
    assert "BAD_ID" in _codes(_edit(t, 'id = "pool.ped"', 'id = "Pool-Ped"'), image)
    assert "BAD_ID" in _codes(_edit(t, 'id = "pool.ped"', 'id = "9ped"'), image)  # not a C++ identifier
    assert "BAD_ID" in _codes(_edit(t, 'id = "pool.route"', 'id = "new"'), image)  # C++ keyword
    assert "REPORT_ID_RANGE" in _codes(_edit(t, "report_id = 91101", "report_id = 9101"), image)


def test_golden_window_must_cover_the_written_range(image):
    t = _good(image)
    # window starts after the written byte
    assert "GOLDEN_WINDOW" in _codes(_edit(t, "golden_va = 0x401040", "golden_va = 0x401042"), image)
    # window too short
    assert "GOLDEN_WINDOW" in _codes(_edit(t, 'golden = "68 8C 00 00 00 8B C8 E8"', 'golden = "68 8C 00"'), image)
    # no golden at all
    nog = _edit(_edit(t, "  golden_va = 0x401050\n", ""), '  golden = "6A 40 8B C8 E8"\n', "")
    assert "GOLDEN_WINDOW" in _codes(nog, image)
    # more than 24 bytes
    long = _edit(t, 'golden = "6A 40 8B C8 E8"', 'golden = "' + " ".join(["6A"] * 25) + '"')
    assert "GOLDEN_WINDOW" in _codes(long, image)


def test_golden_bytes_equal_the_exe(image):
    t = _good(image)
    errs, res = _run(_edit(t, '"68 8C 00 00 00 8B C8 E8"', '"68 8D 00 00 00 8B C8 E8"'), image)
    bad = [e for e in res.errors if e.check == "GOLDEN_MISMATCH"]
    assert len(bad) == 1 and "68 8D" in bad[0].msg and "68 8C" in bad[0].msg and bad[0].where == "cap.pools/pool.ped"
    # a window outside the file-backed bytes
    far = _edit(_edit(t, "golden_va = 0x401050", "golden_va = 0x900050"), "va = 0x401051", "va = 0x900051")
    assert "GOLDEN_UNBACKED" in _codes(far, image)


def test_kind_length_consistency(image):
    t = _good(image)
    assert "KIND_LEN" in _codes(_edit(t, "len = 1\n  kind = \"imm8\"", "len = 2\n  kind = \"imm8\""), image)
    assert "KIND_LEN" in _codes(_edit(t, "len = 4\n  kind = \"imm32\"", "len = 2\n  kind = \"imm32\""), image)
    assert "KIND_LEN" not in _codes(_edit(t, 'kind = "imm32"', 'kind = "f32"'), image)  # f32 is 4 bytes too
    hook4 = t + '\n  [[group.site]]\n  id = "h"\n  va = 0x401060\n  len = 4\n  kind = "hook"\n  golden_va = 0x401060\n  golden = "56 57 8B 7C"\n'
    assert "KIND_LEN" in _codes(hook4, image)
    hook5 = t + '\n  [[group.site]]\n  id = "h"\n  va = 0x401060\n  len = 5\n  kind = "hook"\n  golden_va = 0x401060\n  golden = "56 57 8B 7C 24"\n'
    assert "KIND_LEN" not in _codes(hook5, image)
    f32 = t + '\n  [[group.site]]\n  id = "f"\n  va = 0x401060\n  len = 2\n  kind = "f32"\n  golden_va = 0x401060\n  golden = "56 57"\n'
    assert "KIND_LEN" in _codes(f32, image)


def test_alt_array_and_accept_fields(image):
    t = _good(image)
    assert "ALT_BAD" in _codes(_edit(t, 'alt = "68 xx xx xx xx 8B C8 E8"', 'alt = "68 xx xx"'), image)
    # the window is [base, base + size + stride]: end + stride is legal, one byte more is not
    for off, expect in ((0x60, False), (0x70, True)):
        va = fx.REFS_VA + off
        site = t + _site_toml(va + 1, 4, "absref", va, _exe_hex(image, va, 5),
                              f"  array = {{ base = 0x{fx.ARRAY_BASE:X}, size = 0x{fx.ARRAY_SIZE:X}, "
                              f"stride = 0x{fx.ARRAY_STRIDE:X} }}\n")
        assert ("ARRAY_RANGE" in _codes(site, image)) is expect, hex(va)
    assert "ARRAY_BAD" in _codes(_edit(t, 'kind = "imm32"', 'kind = "imm32"\n  array = { base = 1, size = 1, stride = 1 }'), image)
    assert "ACCEPT_BAD" in _codes(_edit(t, 'kind = "imm32"', 'kind = "imm32"\n  accept_mask = 1'), image)
    d32 = t + '\n  [[group.site]]\n  id = "var"\n  va = 0x401070\n  len = 4\n  kind = "data32"\n'
    assert "ACCEPT_BAD" in _codes(d32, image)
    d32ok = d32 + "  accept_mask = 0x9FFFFFFF\n  accept_value = 0\n"
    assert "ACCEPT_BAD" not in _codes(d32ok, image)


def _site_toml(va: int, ln: int, kind: str, golden_va: int, golden: str, extra: str = "") -> str:
    return (f'\n  [[group.site]]\n  id = "probe"\n  va = 0x{va:X}\n  len = {ln}\n  kind = "{kind}"\n'
            f'  golden_va = 0x{golden_va:X}\n  golden = "{golden}"\n{extra}')


def _exe_hex(image, va: int, n: int) -> str:
    return " ".join(f"{b:02X}" for b in image.read(va, n))


def test_overlap_with_securom_key_window(image):
    for va in (fx.KEY_VA - 3, fx.KEY_VA, fx.KEY_VA + 3):  # any overlap with the 4-byte window
        t = _good(image) + _site_toml(va, 4, "imm32", va, _exe_hex(image, va, 4))
        assert "OVERLAP_KEY" in _codes(t, image), hex(va)
    for va in (fx.KEY_VA - 4, fx.KEY_VA + 4):  # adjacent bytes are fine
        t = _good(image) + _site_toml(va, 4, "imm32", va, _exe_hex(image, va, 4))
        assert "OVERLAP_KEY" not in _codes(t, image), hex(va)
    allowed = _good(image) + _site_toml(fx.KEY_VA, 4, "imm32", fx.KEY_VA, _exe_hex(image, fx.KEY_VA, 4),
                                        f'  allow_overlap = ["key:0x{fx.KEY_VA:X}"]\n')
    assert "OVERLAP_KEY" not in _codes(allowed, image)


def test_overlap_with_stolen_instruction_site(image):
    va = fx.STOLEN_VA + 5  # inside the nop padding of the 7-byte stolen site
    t = _good(image) + _site_toml(va, 1, "imm8", va, _exe_hex(image, va, 2))
    assert "OVERLAP_STOLEN" in _codes(t, image)
    ok = _good(image) + _site_toml(fx.STOLEN_VA + 7, 1, "imm8", fx.STOLEN_VA + 7, _exe_hex(image, fx.STOLEN_VA + 7, 2))
    assert "OVERLAP_STOLEN" not in _codes(ok, image)


def test_site_in_relocated_function(image):
    # golden bytes match the exe (the dead leftover still holds the old bytes) but a patch there has no effect
    for va in (fx.RELOC_ENTRY + 2, 0x401010, fx.NEXT_FUNC - 1):
        t = _good(image) + _site_toml(va, 1, "imm8", va, _exe_hex(image, va, 1))
        errs, res = _run(t, image)
        bad = [e for e in res.errors if e.check == "SITE_IN_RELOCATED_FUNCTION"]
        assert len(bad) == 1, hex(va)
        assert "FakeRelocated" in bad[0].msg and f"0x{fx.RELOC_ENTRY:X}" in bad[0].msg
        assert not [e for e in res.errors if e.check == "GOLDEN_MISMATCH"]
    ok = _good(image) + _site_toml(fx.NEXT_FUNC + 0x30, 1, "imm8", fx.NEXT_FUNC + 0x30, _exe_hex(image, fx.NEXT_FUNC + 0x30, 1))
    assert "SITE_IN_RELOCATED_FUNCTION" not in _codes(ok, image)
    # the read-only anchors group may sit on the relocated entry stub itself
    assert "SITE_IN_RELOCATED_FUNCTION" not in _codes(_good(image), image)


def test_overlap_with_trunk_patches_and_allow_overlap(image):
    trunk = [TrunkPatch(0x401043, 6, "hookpos", "HOOKPOS_Something", "Client/x.cpp:12")]
    errs, res = _run(_good(image), image, trunk=trunk)
    bad = [e for e in res.errors if e.check == "OVERLAP_TRUNK"]
    assert len(bad) == 1 and "HOOKPOS_Something" in bad[0].msg and "trunk:0x401043" in bad[0].msg
    allowed = _edit(_good(image), 'note = "push 140"', 'note = "push 140"\n  allow_overlap = ["trunk:0x401043"]')
    errs, res = _run(allowed, image, trunk=trunk)
    assert res.ok and not res.warnings
    # an allowance that matches nothing is reported (stale)
    errs, res = _run(allowed, image, trunk=[])
    assert res.ok and [w.check for w in res.warnings] == ["ALLOW_UNUSED"]
    # adjacent patch (ends where the site starts) does not overlap
    assert "OVERLAP_TRUNK" not in _codes(_good(image), image, trunk=[TrunkPatch(0x40103D, 4, "memput", "", "a.cpp:1")])
    # unknown length counts as one byte
    assert "OVERLAP_TRUNK" in _codes(_good(image), image, trunk=[TrunkPatch(0x401044, 0, "memput", "", "a.cpp:1")])
    assert "OVERLAP_TRUNK" not in _codes(_good(image), image, trunk=[TrunkPatch(0x401045, 0, "memput", "", "a.cpp:1")])


def test_bad_allow_overlap_token(image):
    t = _edit(_good(image), 'note = "push 140"', 'note = "push 140"\n  allow_overlap = ["maybe:0x1"]')
    assert "SCHEMA" in _codes(t, image)
    with pytest.raises(ValueError):
        parse_allow(["trunk:5534F9"])
    assert parse_allow(["trunk:0x5534F9", "site:a.b/c"]) == [("trunk", 0x5534F9), ("site", "a.b/c")]


def test_overlap_between_groups_and_within_a_group(image):
    other = ('\n[[group]]\nid = "cap.second"\nreport_id = 91102\nphase = "ctor"\n  [[group.site]]\n  id = "clash"\n'
             '  va = 0x401043\n  len = 4\n  kind = "imm32"\n  golden_va = 0x401040\n  golden = "68 8C 00 00 00 8B C8 E8"\n')
    errs, res = _run(_good(image) + other, image)
    bad = [e for e in res.errors if e.check == "OVERLAP_SITE"]
    assert len(bad) == 1 and "cap.second/clash" in bad[0].msg + bad[0].where
    inner = _good(image) + _site_toml(0x401043, 2, "code", 0x401040, "68 8C 00 00 00 8B C8 E8")
    assert "OVERLAP_SITE" in _codes(inner, image)
    named = other.replace("kind = \"imm32\"", "kind = \"imm32\"\n  allow_overlap = [\"site:cap.pools/pool.ped\"]")
    assert "OVERLAP_SITE" not in _codes(_good(image) + named, image)


def test_manifest_hash_must_match_the_exe(image):
    t = _edit(_good(image), image.sha256, "0" * 64)
    assert "EXE_SHA" in _codes(t, image)


def test_header_missing_and_stale(image):
    t = _good(image)
    assert "HEADER_MISSING" in _codes(t, image, header=None)
    assert "HEADER_STALE" in _codes(t, image, header="// something else\n")
    man, _ = parse_manifest(t.encode("utf-8"))
    stale = generate_header(man).replace("0x401041", "0x401042")
    assert "HEADER_STALE" in _codes(t, image, header=stale)
    # changing the TOML (even a comment) makes the header stale: the hash is part of the first line
    t2 = t + "\n# a comment\n"
    assert "HEADER_STALE" in _codes(t2, image, header=generate_header(man))
    # CRLF checkouts hash the same
    assert text_sha256(t.encode("utf-8")) == text_sha256(t.replace("\n", "\r\n").encode("utf-8"))
    errs, res = _run(t, image, header=generate_header(man).replace("\n", "\r\n"))
    assert res.ok


def test_readonly_group_is_not_write_checked(image):
    # id.anchors sits on a relocated entry and on a stolen site range, yet passes; the same site in another group fails
    t = _good(image)
    assert _codes(t, image) == set()
    moved = _edit(t, 'id = "id.anchors"', 'id = "cap.entry"')
    assert "SITE_IN_RELOCATED_FUNCTION" in _codes(moved, image)
    marked = moved.replace('id = "entry"', 'id = "entry"\n  readonly = true')
    assert "SITE_IN_RELOCATED_FUNCTION" not in _codes(marked, image)


# --------------------------------------------------------------------------- sites-gen


def test_header_format_and_determinism(image):
    t = _good(image)
    man, _ = parse_manifest(t.encode("utf-8"))
    h = generate_header(man)
    lines = h.split("\n")
    assert lines[0] == header_first_line(man.sha256) and man.sha256 in lines[0]
    assert lines[0].startswith("// generated by satk engine sites-gen from docs/sae/patch-sites.toml sha256=")
    assert lines[0].endswith("; do not edit") and lines[1] == "#pragma once"
    assert h.endswith("\n}\n") and "\r" not in h
    assert "namespace sae::sites\n{" in h
    assert f'inline constexpr const char* kManifestSha256 = "{man.sha256}";' in h
    assert f'inline constexpr const char* kExeSha256 = "{image.sha256}";' in h
    assert ("    // group cap.pools: report 91101, phase ctor, lane E2\n    enum class cap_pools : std::uint16_t\n    {\n"
            "        pool_ped,\n        pool_route,\n        COUNT\n    };") in h
    assert ('        {"pool.ped", 0x401041, 4, sae::SiteKind::Imm32, 0x401040, 8, '
            '{0x68, 0x8C, 0x00, 0x00, 0x00, 0x8B, 0xC8, 0xE8}, 8, {0x68, 0x00, 0x00, 0x00, 0x00, 0x8B, 0xC8, 0xE8}, '
            '{0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF}, 0x0, 0x0, 0x0, 0x00000000, 0x00000000},') in h
    assert ('        {"pool.route", 0x401051, 1, sae::SiteKind::Imm8, 0x401050, 5, {0x6A, 0x40, 0x8B, 0xC8, 0xE8}, '
            '0, {}, {}, 0x0, 0x0, 0x0, 0x00000000, 0x00000000},') in h
    assert ('    inline constexpr sae::GroupDef kGroups[] = {\n'
            '        {"id.anchors", 91001, sae::Phase::Ctor, kSites_id_anchors, 1},\n'
            '        {"cap.pools", 91101, sae::Phase::Ctor, kSites_cap_pools, 2},\n    };') in h
    assert "inline constexpr std::size_t kGroupCount = sizeof(kGroups) / sizeof(kGroups[0]);" in h
    assert ("    static_assert(std::size_t(cap_pools::COUNT) == sizeof(kSites_cap_pools) / sizeof(kSites_cap_pools[0]));\n}\n"
            in h)
    # groups keep the manifest order (the author decides where a group goes)
    assert h.index("kSites_id_anchors[]") < h.index("kSites_cap_pools[]")
    a, b = t.split('[[group]]\nid = "cap.pools"')
    swapped = '[[group]]\nid = "cap.pools"' + b + "\n[[group]]" + a.split("[[group]]", 1)[1]
    man2, errs = parse_manifest((a.split("[[group]]", 1)[0] + swapped).encode("utf-8"))
    assert not errs
    h2 = generate_header(man2)
    assert h2.index("kSites_cap_pools[]") < h2.index("kSites_id_anchors[]")
    assert generate_header(man) == h  # byte-stable


def test_array_and_accept_fields_in_the_header(image):
    t = _good(image) + (
        '\n  [[group.site]]\n  id = "ref"\n  va = 0x401303\n  len = 4\n  kind = "absref"\n  golden_va = 0x401300\n'
        '  golden = "8B 04 85 00 00 70 00"\n  array = { base = 0x700000, size = 0x100, stride = 0x10 }\n'
        '\n  [[group.site]]\n  id = "var"\n  va = 0x401070\n  len = 4\n  kind = "data32"\n'
        '  accept_mask = 0x9FFFFFFF\n  accept_value = 0x0\n')
    man, errs = parse_manifest(t.encode("utf-8"))
    assert not errs
    h = generate_header(man)
    assert "sae::SiteKind::AbsRef, 0x401300, 7" in h and "0x700000, 0x100, 0x10, 0x00000000, 0x00000000}" in h
    assert "sae::SiteKind::Data32, 0x0, 0, {}, 0, {}, {}, 0x0, 0x0, 0x0, 0x9FFFFFFF, 0x00000000}" in h


# --------------------------------------------------------------------------- ops (check / gen) on a temp fork


def _fork(tmp_path: Path, toml: str) -> Path:
    fork = tmp_path / "fork"
    (fork / "docs" / "sae").mkdir(parents=True)
    (fork / "docs" / "sae" / "patch-sites.toml").write_text(toml, encoding="utf-8", newline="\n")
    return fork


def test_gen_then_check_cycle(satk_home, tmp_path, image):
    fork = _fork(tmp_path, _good(image))
    inputs = fx.fixture_inputs(image)
    # no header yet
    with pytest.raises(SatkError) as e:
        sites_run.run_check(str(fork), None, inputs=inputs)
    assert e.value.code == "CHECK_FAILED" and e.value.data["rows"][0][0] == "HEADER_MISSING"
    g = sites_run.run_gen(str(fork))
    assert g["changed"] is True and g["groups"] == 2 and g["sites"] == 3
    hp = fork / "Shared" / "sdk" / "satk" / "generated" / "SaeSites.gen.h"
    assert hp.read_text(encoding="utf-8").startswith("// generated by satk engine sites-gen from docs/sae/patch-sites.toml sha256=")
    assert sites_run.run_gen(str(fork))["changed"] is False  # idempotent
    env = sites_run.run_check(str(fork), None, inputs=inputs)
    assert env["ok"] and env["n"] == 2 and env["cols"][0] == "group" and env["securom_keys"] == 1
    assert env["written"] == 2 and env["relocated_functions"] == 1 and env["stolen_sites"] == 1
    # a manifest edit makes the header stale until sites-gen runs again
    mp = fork / "docs" / "sae" / "patch-sites.toml"
    mp.write_text(mp.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8", newline="\n")
    with pytest.raises(SatkError) as e:
        sites_run.run_check(str(fork), None, inputs=inputs)
    assert [r[0] for r in e.value.data["rows"]] == ["HEADER_STALE"]
    assert sites_run.run_gen(str(fork))["changed"] is True
    assert sites_run.run_check(str(fork), None, inputs=inputs)["ok"]


def test_check_failure_envelope_has_rows(satk_home, tmp_path, image):
    bad = _edit(_good(image), '"68 8C 00 00 00 8B C8 E8"', '"68 8D 00 00 00 8B C8 E8"')
    fork = _fork(tmp_path, bad)
    man, _ = parse_manifest(bad.encode("utf-8"))
    hp = fork / "Shared" / "sdk" / "satk" / "generated" / "SaeSites.gen.h"
    hp.parent.mkdir(parents=True)
    hp.write_text(generate_header(man), encoding="utf-8", newline="\n")
    with pytest.raises(SatkError) as e:
        sites_run.run_check(str(fork), None, inputs=fx.fixture_inputs(image))
    env = e.value.to_dict()
    assert env["ok"] is False and env["error"]["code"] == "CHECK_FAILED"
    data = env["error"]["data"]
    assert data["cols"] == ["check", "where", "va", "msg"] and data["rows"][0][0] == "GOLDEN_MISMATCH"
    assert data["rows"][0][2] == "0x401041"


def test_missing_manifest_is_not_found(satk_home, tmp_path):
    with pytest.raises(SatkError) as e:
        sites_run.run_check(str(tmp_path / "nofork"), None)
    assert e.value.code == "NOT_FOUND" and "patch-sites.toml" in e.value.msg


def test_gen_refuses_an_invalid_manifest(satk_home, tmp_path, image):
    fork = _fork(tmp_path, _edit(_good(image), "report_id = 91101", "report_id = 5"))
    with pytest.raises(SatkError) as e:
        sites_run.run_gen(str(fork))
    assert e.value.code == "CHECK_FAILED"
    assert not (fork / "Shared").exists()


def test_cli_registration():
    from satk.core.registry import get_op

    assert get_op("engine.sites_check").cli == "engine sites-check"
    assert get_op("engine.sites_gen").cli == "engine sites-gen"
    assert get_op("engine.sites_scan").cli == "engine sites-scan"
    assert get_op("engine.test").cli == "engine test"
    for n in ("engine.sites_check", "engine.sites_gen", "engine.sites_scan", "engine.test"):
        assert get_op(n).mcp_name is None and len(get_op(n).summary) <= 300


# --------------------------------------------------------------------------- relocated-function extents


def test_reloc_extents_skip_pseudo_functions(tmp_path):
    p = tmp_path / "hoodlum_map.json"
    p.write_text(json.dumps({"reloc": [{"entry": "0x401000", "body": "0x1556100"}, {"entry": "0x401100", "body": "0x1556200"}],
                             "sites": [{"site": "0x401500", "stub": "0x401020", "kind": "moved-plain", "jmp_at": "0x401500",
                                        "nop_pad": 2}]}), encoding="utf-8")
    relocs, stolen = load_hoodlum_map(p)
    assert relocs == [(0x401000, 0x1556100), (0x401100, 0x1556200)]
    assert stolen[0].jmp_at == 0x401500 and stolen[0].length == 7 and stolen[0].stub == 0x401020
    funcs = {
        0x401000: FuncInfo(0x401000, 0x401004, "thunk", "ghidra"),
        0x401020: FuncInfo(0x401020, 0x401030, "FUN_00401020", "ghidra"),  # pseudo function made from a stub: skipped
        0x401040: FuncInfo(0x401040, 0x401090, "Named", "gta-reversed"),  # real: named from the sources
        0x401100: FuncInfo(0x401100, 0x401104, "thunk2", "ghidra"),
        0x401180: FuncInfo(0x401180, 0x4011A0, "FUN_00401180", "ghidra"),  # real: has a call edge
    }
    out = reloc_extents(relocs, funcs, {0x401180: {"call"}})
    assert [(r.entry, r.end) for r in out] == [(0x401000, 0x401040), (0x401100, 0x401180)]
    # a function that is only the target of a jump is still a pseudo function
    out = reloc_extents(relocs, funcs, {0x401180: {"jump"}})
    assert out[1].end == 0x857000


# --------------------------------------------------------------------------- sites-scan


def _refs():
    r = fx.REFS_VA
    return [
        (fx.ARRAY_BASE, r, "InArray"), (fx.ARRAY_BASE + 4, r + 0x50, "InArray"),
        (fx.ARRAY_BASE + fx.ARRAY_SIZE, r + 0x10, "InArray"),  # cmp esi,end
        (fx.ARRAY_BASE + fx.ARRAY_SIZE + 8, r + 0x20, "InArray"),
        (fx.ARRAY_BASE + fx.ARRAY_SIZE, r + 0x30, "Other"),
        (fx.ARRAY_BASE + fx.ARRAY_SIZE, r + 0x40, "Other"),
    ]


def test_scan_classifies_by_instruction(image, tmp_path):
    xr = tmp_path / "xrefs.jsonl"
    fx.write_xrefs(xr, _refs())
    res = scan_array(image, xr, fx.ARRAY_BASE, fx.ARRAY_SIZE, fx.ARRAY_STRIDE)
    by_va = {c.insn: c for c in res.candidates}
    r = fx.REFS_VA
    assert by_va[r].cls == "operand-in-array" and by_va[r].va == r + 3 and by_va[r].form == "mem reg+disp32"
    assert by_va[r + 0x50].cls == "operand-in-array" and by_va[r + 0x50].form == "lea"
    assert by_va[r + 0x10].cls == "true-end" and by_va[r + 0x10].va == r + 0x12
    assert by_va[r + 0x20].cls == "end-plus-field" and by_va[r + 0x20].va == r + 0x21
    assert by_va[r + 0x30].cls == "variable-after-array" and by_va[r + 0x30].form == "mem abs"
    assert by_va[r + 0x40].cls == "variable-after-array" and by_va[r + 0x40].form == "imm32 (mov/push/op)"
    assert res.counts() == {"operand-in-array": 2, "true-end": 1, "end-plus-field": 1, "variable-after-array": 2}
    assert res.end == fx.ARRAY_BASE + fx.ARRAY_SIZE


def test_scan_ignores_hits_without_a_ghidra_xref(image, tmp_path):
    xr = tmp_path / "xrefs.jsonl"
    fx.write_xrefs(xr, _refs()[:1])  # only the first instruction has an xref
    res = scan_array(image, xr, fx.ARRAY_BASE, fx.ARRAY_SIZE, fx.ARRAY_STRIDE)
    assert [c.insn for c in res.candidates] == [fx.REFS_VA]


def test_scan_window_is_one_stride_longer_than_the_array(image, tmp_path):
    xr = tmp_path / "xrefs.jsonl"
    fx.write_xrefs(xr, _refs())
    small = scan_array(image, xr, fx.ARRAY_BASE, fx.ARRAY_SIZE, 4)  # end+8 is beyond end+stride(4)
    assert fx.REFS_VA + 0x20 not in {c.insn for c in small.candidates}
    big = scan_array(image, xr, fx.ARRAY_BASE, fx.ARRAY_SIZE, 8)  # end+8 == end+stride: still inside
    assert fx.REFS_VA + 0x20 in {c.insn for c in big.candidates}


def test_scan_flags(image, tmp_path):
    xr = tmp_path / "xrefs.jsonl"
    fx.write_xrefs(xr, _refs())
    # function 0x401300 has no caller -> static-init; relocs table marks the first instructions
    res = scan_array(image, xr, fx.ARRAY_BASE, fx.ARRAY_SIZE, fx.ARRAY_STRIDE, callers={},
                     relocs=[RelocFunc(fx.REFS_VA, fx.REFS_VA + 0x20, 0, "R")])
    flags = {c.insn: c.flags for c in res.candidates}
    assert "static-init" in flags[fx.REFS_VA] and "in-relocated-function" in flags[fx.REFS_VA + 0x10]
    assert "in-relocated-function" not in flags[fx.REFS_VA + 0x20]
    res = scan_array(image, xr, fx.ARRAY_BASE, fx.ARRAY_SIZE, fx.ARRAY_STRIDE, callers={0x401300: {"call"}})
    assert all("static-init" not in c.flags for c in res.candidates)
    # a bound in a function with no reference to the array itself is called out
    only_bound = [(fx.ARRAY_BASE + fx.ARRAY_SIZE, fx.REFS_VA + 0x10, "Lonely")]
    fx.write_xrefs(xr, only_bound)
    res = scan_array(image, xr, fx.ARRAY_BASE, fx.ARRAY_SIZE, fx.ARRAY_STRIDE)
    assert res.candidates[0].cls == "true-end" and "no-array-ref-in-function" in res.candidates[0].flags


def test_scan_in_hoodlum_flag(tmp_path):
    t = bytearray(fx.fixture_text())
    hood = bytearray(0x400)
    hood[0x10:0x10 + 7] = b"\x8b\x04\x85" + fx.i32(fx.ARRAY_BASE)
    from satk.re.pe import PeImage

    img = PeImage(fx.build_pe([(".text", fx.TEXT_VA, bytes(t), 0x1000), (".HOODLUM", fx.HOOD_VA, bytes(hood), 0x2000)]))
    xr = tmp_path / "xrefs.jsonl"
    fx.write_xrefs(xr, [(fx.ARRAY_BASE, fx.HOOD_VA + 0x10, None)])
    res = scan_array(img, xr, fx.ARRAY_BASE, fx.ARRAY_SIZE, fx.ARRAY_STRIDE)
    assert len(res.candidates) == 1 and res.candidates[0].flags == ("in-hoodlum",)


def test_classify_table():
    end = 0x1000
    ins = lambda hexs: decode(bytes.fromhex(hexs) + b"\xcc" * 8, 0)  # noqa: E731
    assert classify(end, end, ins("81FE00100000"), 2) == ("true-end", "cmp imm32")
    assert classify(end + 4, end, ins("3D04100000"), 1) == ("end-plus-field", "cmp imm32")
    assert classify(end, end, ins("A100100000"), 1) == ("variable-after-array", "mem abs")
    assert classify(end - 4, end, ins("A1FC0F0000"), 1)[0] == "operand-in-array"
    assert classify(end, end, ins("8D8100100000"), 2) == ("true-end", "lea")
    assert classify(end, end, None, 1)[0] == "variable-after-array"  # unknown instruction: never patch


def test_scan_op_filters_and_counts(satk_home, image, tmp_path):
    xr = tmp_path / "xrefs.jsonl"
    fx.write_xrefs(xr, _refs())
    env = sites_run.run_scan(fx.ARRAY_BASE, fx.ARRAY_SIZE, fx.ARRAY_STRIDE, image=image, xrefs=xr)
    assert env["ok"] and env["total"] == 6 and env["counts"]["variable-after-array"] == 2
    assert env["cols"][:5] == ["operand", "insn", "value", "off", "class"]
    only = sites_run.run_scan(fx.ARRAY_BASE, fx.ARRAY_SIZE, fx.ARRAY_STRIDE, only="operand-in-array", image=image, xrefs=xr)
    assert [r[0] for r in only["rows"]] == [f"0x{fx.REFS_VA + 3:X}", f"0x{fx.REFS_VA + 0x50 + 2:X}"]
    page = sites_run.run_scan(fx.ARRAY_BASE, fx.ARRAY_SIZE, fx.ARRAY_STRIDE, limit=4, image=image, xrefs=xr)
    assert page["n"] == 4 and page["next"] == "4"
    rest = sites_run.run_scan(fx.ARRAY_BASE, fx.ARRAY_SIZE, fx.ARRAY_STRIDE, limit=4, offset=4, image=image, xrefs=xr)
    assert rest["n"] == 2 and rest["next"] is None
    with pytest.raises(SatkError) as e:
        sites_run.run_scan(None, 1, 1)
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        sites_run.run_scan(1, 1, 1, only="nope", image=image, xrefs=xr)
    assert e.value.code == "BAD_PARAMS"
