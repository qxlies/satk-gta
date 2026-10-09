"""Patch-site seed of the sa-engine foundation design (Appendix A) as data: the golden rows and a manifest.

``GOLDEN_ROWS`` lists every row with explicit stock bytes as ``(golden_va, hex bytes)`` (small hex
strings only; they are compared with the real exe by the game-data test). ``manifest_toml`` builds
a schema-1 manifest from the same rows for ``sites-check``. Rows that give only a stock constant
address (``va-2`` rows, readers of arrays) take their window from the exe at test time.
"""

from __future__ import annotations

import struct

ANCHORS = [
    (0x401000, "E9 7B 19 16 01"),
    (0x748ADD, "FF 53"),
    (0x5618D0, "51 A1 2C CB B7 00"),
    (0x733A21, "68 90 01 00 00"),
    (0x5534F2, "89 34 85 F8 48 B7 00"),
    (0x5B8E54, "68 E0 2E 00 00"),
    (0x406BC0, "81 F9 00 08 00 00 77 05 B8 00 00 00 20"),
]

_POOL_TAIL = "8B C8 E8"
# (group, site, operand va, len, kind, golden_va, golden hex)
SITES: list[tuple] = []


def _s(group, site, va, ln, kind, gva, golden, **extra):
    SITES.append((group, site, va, ln, kind, gva, golden, extra))


for name, va, imm in (("ptr_double", 0x550F82, "68 80 0C 00 00"), ("entry_info", 0x550FBA, "68 F4 01 00 00"),
                      ("ped", 0x550FF2, "68 8C 00 00 00"), ("building", 0x55105F, "68 C8 32 00 00"),
                      ("object", 0x551097, "68 5E 01 00 00"), ("dummy", 0x5510CF, "68 C4 09 00 00"),
                      ("colmodel", 0x551107, "68 A6 27 00 00"), ("task", 0x55113F, "68 F4 01 00 00"),
                      ("event", 0x551177, "68 C8 00 00 00"), ("ped_intelligence", 0x551283, "68 8C 00 00 00"),
                      ("quadtree", 0x552C3F, "68 90 01 00 00")):
    _s("cap.pools", name, va, 4, "imm32", va - 1, f"{imm} {_POOL_TAIL}",
       **({"allow": ("trunk:0x552C3F",)} if name == "quadtree" else {}))
for name, va, imm in (("point_route", 0x5511AF, "6A 40"), ("patrol_route", 0x5511E4, "6A 20"),
                      ("node_route", 0x551219, "6A 40"), ("task_allocator", 0x55124E, "6A 10"),
                      ("ped_attractor", 0x5512BC, "6A 40")):
    _s("cap.pools", name, va, 1, "imm8", va - 1, f"{imm} {_POOL_TAIL}")
_VCTOR = "56 57 8B 7C 24 0C 8B C7 69 C0 18 0A 00 00 50"
_s("cap.vehicle_ctor", "count", 0x5504C2, 6, "code", 0x5504C0, _VCTOR)
_s("cap.vehicle_ctor", "imul", 0x5504C8, 6, "code", 0x5504C0, _VCTOR)
_s("cap.lists", "rwobj_alloc", 0x5B8E55, 4, "imm32", 0x5B8E54, "68 E0 2E 00 00", allow=("trunk:0x5B8E55",))
_s("cap.lists", "rwobj_loop", 0x5B8EB0, 4, "imm32", 0x5B8EAF, "B9 E0 2E 00 00", allow=("trunk:0x5B8EB0",))
_s("cap.lists", "matrix", 0x54F3A1, 4, "imm32", 0x54F3A0, "68 84 03 00 00", allow=("trunk:0x54F3A1",))
for name, a, lp, imm in (("atomics", 0x733A22, 0x733A5E, "90 01 00 00"), ("boat", 0x733A87, 0x733AD7, "90 01 00 00"),
                         ("entity", 0x733B05, 0x733B55, "A0 0F 00 00"), ("underwater", 0x733B85, 0x733BD5, "D0 07 00 00"),
                         ("drawlast", 0x733C05, 0x733C55, "E8 03 00 00"), ("weapon_peds", 0x733C85, 0x733CD5, "B0 04 00 00")):
    _s("cap.alpha", f"{name}_alloc", a, 4, "imm32", a - 1, f"68 {imm}")
    _s("cap.alpha", f"{name}_loop", lp, 4, "imm32", lp - 1, f"B9 {imm}")
for tag, base in (("init", 0x4A992D), ("unload", 0x4A9B2F)):
    # (the unload loop compares edi, not esi: Appendix A says "same four patterns", the exe says 81 FF)
    last = "81 FE 60 EA 00 00" if tag == "init" else "81 FF 60 EA 00 00"
    d = {"init": (0x0, 0x23, 0x2E, 0x5B), "unload": (0x0, 0x27, 0x32, 0x61)}[tag]
    _s("cap.fx", f"{tag}_alloc", base + d[0] + 1, 4, "imm32", base, "68 64 EA 00 00")
    _s("cap.fx", f"{tag}_count1", base + d[1] + 1, 4, "imm32", base + d[1], "68 E8 03 00 00")
    _s("cap.fx", f"{tag}_count2", base + d[2] + 2, 4, "imm32", base + d[2], "C7 00 E8 03 00 00")
    _s("cap.fx", f"{tag}_end", base + d[3] + 2, 4, "imm32", base + d[3], last)
_s("cap.fx", "pool_alloc", 0x4A9C37, 4, "imm32", 0x4A9C36, "68 00 00 10 00")
_s("cap.fx", "pool_size", 0x4A9C3E, 4, "imm32", 0x4A9C3B, "C7 46 04 00 00 10 00")
for _n, _va in (("a", 0x5536D1), ("c", 0x55377C), ("d", 0x5537B1), ("e", 0x553844), ("f", 0x553876), ("g", 0x5558C9)):
    _s("cap.lod_render_list", f"base_{_n}", _va, 4, "imm32", _va - 1, ("exe", _va - 1, 5))  # B8/BE/B9/BF E0 E0 C8 00
_s("cap.lod_render_list", "base_b", 0x5536F6, 4, "imm32", 0x5536F0, "C7 05 D0 45 B7 00 E0 E0 C8 00")
_s("cap.lod_render_list", "dont_a", 0x5536E1, 4, "imm32", 0x5536E0, "B8 C8 00 C9 00")
_s("cap.lod_render_list", "dont_b", 0x553700, 4, "imm32", 0x5536FA, "C7 05 CC 45 B7 00 C8 00 C9 00")
_s("cap.lod_render_list", "dont_c", 0x5558D9, 4, "imm32", 0x5558D3, "C7 05 CC 45 B7 00 C8 00 C9 00")
_s("cap.lod_list_guard", "add", 0x553710, 5, "hook", 0x553710, "A1 D0 45 B7 00 D9 44 24 08")
_s("cap.coronas", "loop_a", 0x6FAAD4, 4, "imm32", 0x6FAAD3, "B9 40 00 00 00")
_s("cap.coronas", "loop_b", 0x6FAF4A, 4, "imm32", 0x6FAF46, "C7 44 24 58 40 00 00 00")
_s("cap.coronas_native", "register_search", 0x6FC2F2, 1, "imm8", 0x6FC2EF, "66 83 F9 40 72 EB")
_s("cap.coronas_native", "register_found", 0x6FC2F8, 1, "imm8", 0x6FC2F5, "66 83 F9 40 B2 01 0F 85")
_s("cap.coronas_native", "update_search", 0x6FC324, 1, "imm8", 0x6FC321, "66 83 F9 40 72 E9")
_s("cap.coronas_native", "update_found", 0x6FC32A, 1, "imm8", 0x6FC327, "66 83 F9 40 0F 84")
_s("cap.shadows_stored", "bound", 0x70739B, 1, "imm8", 0x707398, "66 83 FE 30 0F 83")
_s("cap.plant_tris", "free_list_head", 0x5DD7B2, 4, "absref", 0x5DD7AC, "C7 05 84 39 C0 00 48 3A C0 00",
   array=(0xC03A48, 0x5400, 0x54))
_s("perf.timer", "entry", 0x5618D0, 6, "hook", 0x5618D0, "51 A1 2C CB B7 00")
_s("perf.img_init", "flag", 0x406BC6, 1, "code", 0x406BC0, "81 F9 00 08 00 00 77 05 B8 00 00 00 20")
_s("perf.img_flags", "var", 0x8E3FE0, 4, "data32", 0, "", accept=(0x9FFFFFFF, 0))
_s("perf.present_vsync", "op", 0x745241, 4, "absref", 0x745240, "A0 94 67 BA 00 84 C0 75 0D")
_s("dd.dnorm", "setup_a", 0x5545E8, 4, "absref", 0x5545E6, "D8 1D D8 8F 85 00")
_s("dd.dnorm", "setup_b", 0x554602, 4, "absref", 0x554600, "D9 05 D8 8F 85 00")
_s("dd.dnorm", "setup_c", 0x55462C, 4, "absref", 0x55462A, "D8 25 D8 8F 85 00")
for i, va in enumerate((0x555174, 0x55519A, 0x5551BD, 0x555230, 0x55523A, 0x555244, 0x5552F6, 0x555300, 0x55530A,
                        0x555364, 0x55537C, 0x55538A)):
    _s("dd.dnorm", f"scan_{i}", va, 4, "absref", va - 2, "D8 0D D8 8F 85 00")
for i, va in enumerate((0x5B526C, 0x5B3091)):
    _s("dd.big", f"fld_{i}", va, 4, "absref", va - 2, "D9 05 18 F1 B6 00")
for i, va in enumerate((0x5B527C, 0x5B309A)):
    _s("dd.big", f"cmp_{i}", va, 4, "absref", va - 2, "D8 1D D8 8F 85 00")
_s("dd.occluder", "range", 0x71E691, 4, "absref", 0x71E68F, "D8 1D D8 8F 85 00")
for i, va in enumerate((0x554048, 0x55407C, 0x554143)):
    _s("dd.fade", f"op_{i}", va, 4, "absref", va - 2, "D8 25 A4 8B 85 00")
_s("dd.fade", "op_3", 0x732581, 4, "absref", 0x73257F, "D8 05 A4 8B 85 00")
_s("dd.fade", "imm", 0x73250F, 4, "f32", 0x73250B, "C7 44 24 00 00 00 A0 41")
_s("dd.lodfade", "k_0", 0x554003, 4, "absref", 0x554001, "D8 0D 0C 3E 86 00")
_s("dd.lodfade", "k_1", 0x73255F, 4, "absref", 0x73255D, "D8 0D 0C 3E 86 00")
for i, va in enumerate((0x5DC0D2, 0x5DC105, 0x5DC132, 0x5DC15F, 0x5DC18C, 0x5DC1B5, 0x5DC1DE, 0x5DCB8A, 0x5DCBBF,
                        0x5DCBEC, 0x5DCC19, 0x5DCC42, 0x5DCC6B, 0x5DCC94)):
    _s("dd.grass", f"radius_{i}", va, 4, "absref", va - 2, "D8 1D A4 9A 85 00")
for name, va, b in (("det_radius", 0x554897, "D8 1D A4 9A 85 00"), ("det_angle", 0x5548C8, "D8 1D 10 3E 86 00"),
                    ("lod_radius", 0x554B94, "D8 1D 14 3E 86 00"), ("lod_angle", 0x554BC5, "D8 1D B0 8C 85 00")):
    _s("dd.cones", name, va, 4, "absref", va - 2, b)
_s("dd.ipl", "r200_0", 0x406124, 4, "absref", 0x406122, "D8 25 48 8A 85 00")
for i, va in enumerate((0x406130, 0x40613C, 0x406148), 1):
    _s("dd.ipl", f"r200_{i}", va, 4, "absref", va, "48 8A 85 00")
_s("dd.ipl", "r350_0", 0x4060F8, 4, "absref", 0x4060F6, "D8 25 4C 8A 85 00")
for i, va in enumerate((0x406104, 0x406110, 0x40611C), 1):
    _s("dd.ipl", f"r350_{i}", va, 4, "absref", va, "4C 8A 85 00")
_s("dd.scene", "outdoor", 0x40D404, 4, "f32", 0x40D400, "C7 44 24 10 00 00 A0 42")
_s("dd.scene", "interior", 0x40D40E, 4, "f32", 0x40D40A, "C7 44 24 10 00 00 20 42")
_s("dd.shadows", "ped_dist", 0x8D5240, 4, "data32", 0x8D5240, "00 00 70 41")
_s("dd.shadows", "ped_dist_sq", 0xC4B6B0, 4, "data32", 0, "", accept=(0xFFFFFFFF, 0x43610000))
for va, v in zip((0x872730, 0x872734, 0x872738, 0x87273C), (324.0, 288.0, 82944.0, 20736.0)):
    _s("dd.shadows", f"veh_sq_{va & 0xF:x}", va, 4, "data32", va, " ".join(f"{b:02X}" for b in struct.pack("<f", v)))
_s("dd.shadows", "car", 0x70BEB6, 4, "absref", 0x70BEB4, "D9 05 08 90 85 00")
_s("dd.shadows", "heli", 0x70BE88, 4, "absref", 0x70BE86, "D9 05 24 C8 86 00")
_s("dd.water", "detail", 0x8D37D0, 4, "data32", 0x8D37D0, "30 00 00 00")
_s("dd.clouds", "max_a", 0x713638, 4, "absref", 0x713636, "D9 05 DC 8E 85 00")
_s("dd.clouds", "max_b", 0x713648, 4, "absref", 0x713646, "D8 1D DC 8E 85 00")
_s("dd.traffic", "corona", 0x49DCF4, 4, "f32", 0x49DCF3, "68 00 00 48 42")
_s("qol.ped_shadow", "raster", 0x7064C2, 1, "imm8", 0x7064C1, "6A 07 8B CF E8")
_s("qol.ped_shadow", "blur", 0x7064F9, 1, "imm8", 0x7064F8, "6A 06 8D 4E 14")
_s("qol.ped_shadow", "mgr_a", 0x706825, 1, "imm8", 0x706824, "6A 06 8D 4F 44")
_s("qol.ped_shadow", "mgr_b", 0x706832, 1, "imm8", 0x706831, "6A 06 8B CE E8")
_MIRROR = "68 00 02 00 00 68 00 04 00 00"
_s("qol.mirror", "colour_h", 0x7230CC, 4, "imm32", 0x7230CB, _MIRROR)
_s("qol.mirror", "colour_w", 0x7230D1, 4, "imm32", 0x7230CB, _MIRROR)
_s("qol.mirror", "z_h", 0x7230EA, 4, "imm32", 0x7230E9, _MIRROR)
_s("qol.mirror", "z_w", 0x7230EF, 4, "imm32", 0x7230E9, _MIRROR)

#: Reader sites whose window the design leaves open (golden taken from the exe at test time).
OPEN_WINDOW_SITES = [
    ("cap.visible_lists", "lod_0", 0x5534F5, (0xB748F8, 4000, 4)),
    ("cap.visible_lists", "lod_1", 0x553923, (0xB748F8, 4000, 4)),
    ("cap.visible_lists", "lod_2", 0x553CB3, (0xB748F8, 4000, 4)),
    ("cap.visible_lists", "ent_0", 0x553529, (0xB75898, 4000, 4)),
    ("cap.visible_lists", "ent_1", 0x553944, (0xB75898, 4000, 4)),
    ("cap.visible_lists", "ent_2", 0x553A53, (0xB75898, 4000, 4)),
    ("cap.visible_lists", "ent_3", 0x553B03, (0xB75898, 4000, 4)),
]

#: Every row with explicit stock bytes: (golden va, hex). Rows that are windows of the sites above come from SITES.
GOLDEN_ROWS: list[tuple[int, str]] = list(ANCHORS)
GOLDEN_ROWS += [(g, h) for (_, _, _, _, _, g, h, _) in SITES if isinstance(h, str) and h]
GOLDEN_ROWS += [(0x5534F2, "89 34 85 F8 48 B7 00"), (0x553526, "89 34 85 98 58 B7 00"),
                (0x5536F0, "C7 05 D0 45 B7 00 E0 E0 C8 00"), (0x8D132C, "00 00 20 41")]
GOLDEN_ROWS = sorted(set(GOLDEN_ROWS))


def _hexb(raw: bytes) -> str:
    return " ".join(f"{b:02X}" for b in raw)


def manifest_toml(exe_sha: str, read) -> str:
    """Schema-1 manifest text for the seed; ``read(va, n)`` returns stock bytes for the open windows."""
    out = [f'schema = 1\nexe_sha256 = "{exe_sha}"\n']
    groups: dict[str, list[str]] = {}
    order: list[str] = []

    def add(group: str, text: str) -> None:
        if group not in groups:
            groups[group] = []
            order.append(group)
        groups[group].append(text)

    for g, s, va, ln, kind, gva, golden, extra in SITES:
        t = [f'\n  [[group.site]]\n  id = "{s}"\n  va = 0x{va:X}\n  len = {ln}\n  kind = "{kind}"\n']
        if isinstance(golden, tuple):
            golden = _hexb(read(golden[1], golden[2]))
        if golden:
            t.append(f"  golden_va = 0x{gva:X}\n  golden = \"{golden}\"\n")
        if "array" in extra:
            b, sz, st = extra["array"]
            t.append(f"  array = {{ base = 0x{b:X}, size = {sz}, stride = {st} }}\n")
        if "accept" in extra:
            m, v = extra["accept"]
            t.append(f"  accept_mask = 0x{m:X}\n  accept_value = 0x{v:X}\n")
        if "allow" in extra:
            t.append("  allow_overlap = [" + ", ".join(f'"{a}"' for a in extra["allow"]) + "]\n")
        add(g, "".join(t))
    for g, s, va, (b, sz, st) in OPEN_WINDOW_SITES:
        gva = va - 3
        t = (f'\n  [[group.site]]\n  id = "{s}"\n  va = 0x{va:X}\n  len = 4\n  kind = "absref"\n  golden_va = 0x{gva:X}\n'
             f'  golden = "{_hexb(read(gva, 7))}"\n  array = {{ base = 0x{b:X}, size = {sz}, stride = {st} }}\n')
        add(g, t)
    # base_a / base_b of cap.lod_render_list: windows from the exe
    anchors = "\n[[group]]\nid = \"id.anchors\"\nreport_id = 91001\nphase = \"ctor\"\n"
    for i, (va, h) in enumerate(ANCHORS):
        anchors += (f'\n  [[group.site]]\n  id = "a{i}"\n  va = 0x{va:X}\n  len = {len(h.split())}\n  kind = "code"\n'
                    f'  golden_va = 0x{va:X}\n  golden = "{h}"\n')
    out.append(anchors)
    for n, g in enumerate(sorted(order), 1):
        out.append(f'\n[[group]]\nid = "{g}"\nreport_id = {91100 + n}\nphase = "ctor"\n')
        out.extend(groups[g])
    return "".join(out)
