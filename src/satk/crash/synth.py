"""Synthetic minidumps and MTA crash logs (for ``satk crash sample`` and the tests).

:class:`DumpBuilder` writes a small but well-formed ``MDMP`` file: system info, modules (with an
RSDS CodeView record), threads with x86 or AMD64 contexts, captured memory, an exception stream,
comment and application (MTA) user streams; :func:`mta_section` appends MTA trailing sections.
Everything is invented: no bytes of a real dump or of the game are used. Deterministic.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

__all__ = ["DumpBuilder", "mta_section", "sample_dump", "sample_log", "SAMPLE", "SP_SAMPLE", "sample_sp_dump",
           "sample_sp_files"]


@dataclass
class _Thread:
    tid: int
    ctx: dict
    stack_va: int
    stack: bytes
    name: str | None = None


@dataclass
class DumpBuilder:
    arch: str = "x86"                                     # x86 | amd64
    timestamp: int = 0x66FF0000
    modules: list[tuple[str, int, int, int, str | None]] = field(default_factory=list)
    threads: list[_Thread] = field(default_factory=list)
    memory: list[tuple[int, bytes]] = field(default_factory=list)
    exception: tuple | None = None                       # (tid, code, address, params, ctx)
    comments: list[str] = field(default_factory=list)
    user: list[tuple[int, bytes]] = field(default_factory=list)
    pid: int | None = 4242

    def add_module(self, path: str, base: int, size: int, *, timestamp: int = 0, pdb: str | None = None) -> None:
        self.modules.append((path, base, size, timestamp, pdb))

    def add_thread(self, tid: int, ctx: dict, stack_va: int, stack: bytes, name: str | None = None) -> None:
        self.threads.append(_Thread(tid, ctx, stack_va, stack, name))

    def add_memory(self, va: int, data: bytes) -> None:
        self.memory.append((va, bytes(data)))

    def set_exception(self, tid: int, code: int, address: int, params: list[int] = (), ctx: dict | None = None) -> None:
        self.exception = (tid, code, address, list(params), ctx)

    def add_user_stream(self, stype: int, data: bytes | str) -> None:
        self.user.append((stype, data.encode("utf-8") + b"\0" if isinstance(data, str) else bytes(data)))

    # -- encoding --------------------------------------------------------------------------

    def _context(self, regs: dict) -> bytes:
        if self.arch == "x86":
            b = bytearray(716)
            struct.pack_into("<I", b, 0, 0x1003F)
            order = ("edi", "esi", "ebx", "edx", "ecx", "eax", "ebp", "eip")
            struct.pack_into("<4I", b, 0x8C, 0x2B, 0x53, 0x2B, 0x2B)
            struct.pack_into("<8I", b, 0x9C, *(regs.get(k, 0) for k in order))
            struct.pack_into("<IIII", b, 0xBC, 0x23, regs.get("eflags", 0x246), regs.get("esp", 0), 0x2B)
            return bytes(b)
        b = bytearray(1232)
        struct.pack_into("<I", b, 0x30, 0x10001F)
        struct.pack_into("<I", b, 0x44, regs.get("eflags", 0x246))
        names = ("rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi", "r8", "r9", "r10", "r11", "r12", "r13",
                 "r14", "r15", "rip")
        struct.pack_into("<17Q", b, 0x78, *(regs.get(k, 0) for k in names))
        return bytes(b)

    def build(self) -> bytes:
        out = bytearray(32)
        streams: list[tuple[int, int, int]] = []

        def put(blob: bytes, align: int = 4) -> int:
            while len(out) % align:
                out.append(0)
            rva = len(out)
            out.extend(blob)
            return rva

        def string(s: str) -> int:
            raw = s.encode("utf-16-le")
            return put(struct.pack("<I", len(raw)) + raw + b"\0\0")

        # system info
        arch = 0 if self.arch == "x86" else 9
        si = struct.pack("<HHHBBIIIII", arch, 6, 0x6100, 8, 1, 10, 0, 26100, 2, 0) + struct.pack("<HH", 0x100, 0)
        si += bytes(24)
        streams.append((7, len(si), put(si)))
        # misc info
        if self.pid is not None:
            mi = struct.pack("<6I", 24, 1, self.pid, 0, 0, 0)
            streams.append((15, len(mi), put(mi)))
        # modules
        recs = []
        for path, base, size, ts, pdb in self.modules:
            name_rva = string(path)
            cv = b""
            if pdb:
                cv = b"RSDS" + struct.pack("<IHH", 0x12345678, 0x9ABC, 0xDEF0) + bytes(range(8)) + struct.pack("<I", 1)
                cv += pdb.encode("utf-8") + b"\0"
            cv_rva = put(cv) if cv else 0
            vi = struct.pack("<13I", 0xFEEF04BD, 0x10000, (1 << 16) | 6, (0 << 16) | 1, 0, 0, 0x3F, 0, 4, 2, 0, 0, 0)
            recs.append(struct.pack("<QIIII", base, size, 0, ts, name_rva) + vi
                        + struct.pack("<IIII", len(cv), cv_rva, 0, 0) + bytes(16))
        ml = struct.pack("<I", len(recs)) + b"".join(recs)
        streams.append((4, len(ml), put(ml)))
        # memory: thread stacks + extra ranges
        mem_desc = []
        stack_desc = {}
        for t in self.threads:
            rva = put(t.stack)
            stack_desc[t.tid] = (t.stack_va, len(t.stack), rva)
            mem_desc.append(struct.pack("<QII", t.stack_va, len(t.stack), rva))
        for va, data in self.memory:
            rva = put(data)
            mem_desc.append(struct.pack("<QII", va, len(data), rva))
        mlst = struct.pack("<I", len(mem_desc)) + b"".join(mem_desc)
        streams.append((5, len(mlst), put(mlst)))
        # threads
        trs = []
        names = []
        for t in self.threads:
            c = self._context(t.ctx)
            crva = put(c)
            sva, ssize, srva = stack_desc[t.tid]
            trs.append(struct.pack("<IIIIQQIIII", t.tid, 0, 0x20, 0, 0x7FFDE000, sva, ssize, srva, len(c), crva))
            if t.name:
                names.append(struct.pack("<IQ", t.tid, string(t.name)))
        tl = struct.pack("<I", len(trs)) + b"".join(trs)
        streams.append((3, len(tl), put(tl)))
        if names:
            tn = struct.pack("<I", len(names)) + b"".join(names)
            streams.append((24, len(tn), put(tn)))
        # exception
        if self.exception is not None:
            tid, code, addr, params, ctx = self.exception
            if ctx is None:
                ctx = next(t.ctx for t in self.threads if t.tid == tid)
            crva = put(self._context(ctx))
            pr = (list(params) + [0] * 15)[:15]
            ex = struct.pack("<IIIIQQII", tid, 0, code, 0, 0, addr, len(params), 0) + struct.pack("<15Q", *pr)
            ex += struct.pack("<II", len(self._context(ctx)), crva)
            streams.append((6, len(ex), put(ex)))
        for c in self.comments:
            raw = c.encode("utf-8") + b"\0"
            streams.append((10, len(raw), put(raw)))
        for stype, raw in self.user:
            streams.append((stype, len(raw), put(raw)))
        dir_rva = put(b"".join(struct.pack("<III", *s) for s in streams))
        struct.pack_into("<IIIIIIQ", out, 0, 0x504D444D, 0xA793, len(streams), dir_rva, 0, self.timestamp, 0x1800)
        return bytes(out)


def mta_section(tag: str, payload: bytes) -> bytes:
    """One MTA trailing section (``tag`` = 3 letters, e.g. ``POL``)."""
    begin = struct.unpack(">I", (tag + "s").encode("ascii"))[0]
    end = struct.unpack(">I", (tag + "e").encode("ascii"))[0]
    return struct.pack("<III", 0, begin, len(payload)) + payload + struct.pack("<III", len(payload), end, 0)


def _wstr(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


# --------------------------------------------------------------------------- the sample crash

#: The sample: CStreaming::Update (gta_sa.exe, real 1.0 US addresses) dereferences NULL+0x10 while
#: called from CGame::Process, called from Game::Idle; core.dll frames sit above (invented offsets).
SAMPLE = {
    "crash_ip": 0x40E6A3,           # inside CStreaming::Update (0x40E670)
    "ret_process": 0x53BF10,        # after "call CStreaming::Update" at 0x53BF0B in CGame::Process
    "ret_idle": 0x53E986,           # after "call CGame::Process" at 0x53E981 in Game::Idle
    "core_base": 0x6C000000,
    "core_ret": 0x6C04A1B2,
    "stale": 0x00B74490,            # a data pointer (pool global) on the stack: not a frame
    "tid": 4120,
}


def _call(site: int, target: int) -> bytes:
    return b"\xE8" + struct.pack("<i", target - (site + 5))


def sample_dump(*, sections: bool = True) -> bytes:
    """A synthetic MTA client dump of the sample crash (x86, MiniDumpNormal-like)."""
    s = SAMPLE
    b = DumpBuilder("x86")
    b.add_module(r"C:\ProgramData\MTA San Andreas All\1.6\GTA San Andreas\gta_sa.exe", 0x400000, 0x1177000,
                 timestamp=0x427101CA)
    b.add_module(r"C:\Program Files (x86)\MTA San Andreas 1.6\mta\core.dll", s["core_base"], 0x200000,
                 timestamp=0x66FE0001, pdb="core.pdb")
    b.add_module(r"C:\Windows\SysWOW64\ntdll.dll", 0x77A00000, 0x1A0000, timestamp=0x5E0A0001, pdb="wntdll.pdb")
    stack_va = 0x0177F000
    stack = bytearray(0x1000)
    esp = stack_va + 0x980
    ebp = stack_va + 0x9C8

    def put(va: int, v: int) -> None:
        struct.pack_into("<I", stack, va - stack_va, v)

    put(esp + 0x00, 0)                      # locals of CStreaming::Update
    put(esp + 0x04, s["stale"])
    put(esp + 0x0C, s["ret_process"])       # return into CGame::Process
    put(esp + 0x20, 0x00C8D4C0)             # another data pointer
    put(ebp, ebp + 0x40)                    # frame-pointer chain: CGame::Process frame
    put(ebp + 4, s["ret_idle"])             # return into Game::Idle
    put(ebp + 0x40, ebp + 0x80)
    put(ebp + 0x44, s["core_ret"])          # return into core.dll (MTA's main loop)
    put(ebp + 0x80, 0)
    regs = {"eax": 0, "ebx": 0xA, "ecx": 0xB6F5F0, "edx": 0x177F3A8, "esi": 0xC8D4C0, "edi": 0, "ebp": ebp,
            "esp": esp, "eip": s["crash_ip"], "eflags": 0x210246}
    b.add_thread(s["tid"], regs, stack_va, bytes(stack), name="MainThread")
    b.add_thread(4188, {"eip": 0x77A72C3C, "esp": 0x0BAFF8F0, "ebp": 0x0BAFF900}, 0x0BAFF800, bytes(0x200))
    # code bytes around the faulting instruction and before each return address (as dbghelp captures them)
    code = bytearray(b"\x90" * 0x40)
    code[0x33:0x36] = b"\x8B\x40\x10"       # mov eax,[eax+10h] at crash_ip
    b.add_memory(s["crash_ip"] - 0x33, bytes(code))
    b.add_memory(0x53BF0B, _call(0x53BF0B, 0x40E670) + b"\x8B\xF0")
    b.add_memory(0x53E981, _call(0x53E981, 0x53BEE0) + b"\x84\xC0")
    b.add_memory(s["core_ret"] - 6, b"\xFF\x15\x00\x10\x00\x6C")     # call dword ptr [6C001000h]
    b.set_exception(s["tid"], 0xC0000005, s["crash_ip"], [0, 0x10])
    b.add_user_stream(0x10000, "Exception Code: 0xC0000005\nException Address: 0x0040E6A3\n"
                               "Description: Access violation reading 0x00000010\nModule: gta_sa.exe\n"
                               "Module Offset: 0x0000E6A3\nThread ID: 4120  Process ID: 4242\n")
    b.add_user_stream(0x10001, "Stack Trace (CrashHandler):\n  #0 gta_sa.exe+0xE6A3 [0x40E6A3]\n"
                               "  #1 gta_sa.exe+0x13BF10 [0x53BF10]\n  #2 gta_sa.exe+0x13E986 [0x53E986]\n"
                               "  #3 core.dll+0x4A1B2 [0x6C04A1B2]\n")
    b.add_user_stream(0x10002, "CPU Registers:\n  EAX=0x00000000 EBX=0x0000000A ECX=0x00B6F5F0 EDX=0x0177F3A8\n")
    b.comments.append("Version = 1.6-custom.00000.0.000\n(sample crash generated by satk)\n")
    data = b.build()
    if sections:
        pools = struct.pack("<ii", 1, 4) + b"".join(struct.pack("<iii", i, c, u) for i, c, u in
                                                     ((0, 13000, 9120), (1, 140, 140), (2, 350, 97), (4, 110, 31)))
        log = struct.pack("<iI", 1, 2) + struct.pack("<I", 1500) + _wstr("info") + _wstr("streaming") \
            + _wstr("RequestModel 7001") + struct.pack("<I", 20) + _wstr("warn") + _wstr("ped") + _wstr("pool full")
        misc = struct.pack("<i", 2) + b"\x8B\x0D" + struct.pack("<I", 0)
        report = b"[2026-10-04 14:30:00] sample report.log line\n"
        data += mta_section("POL", pools) + mta_section("LOG", log) + mta_section("MSC", misc) \
            + mta_section("LOG", b"[14:29:59] sample logfile.txt line\n") + mta_section("REP", report)
    return data


def sample_log() -> str:
    """The same crash as MTA's ``core.log`` writes it (two blocks: an older one and the sample)."""
    s = SAMPLE
    old = ("** -- Unhandled exception -- **\n\nVersion = 1.6-custom.00000.0.000\nTime = Sat Oct  3 21:00:00 2026\n"
           "Module = C:\\Program Files (x86)\\MTA San Andreas 1.6\\mta\\core.dll\nCode = 0xC0000005\n"
           "Offset = 0x00001234\n\n")
    regs = ("EAX=00000000  EBX=0000000A  ECX=00B6F5F0  EDX=0177F3A8  ESI=00C8D4C0\n"
            f"EDI=00000000  EBP=0177F9C8  ESP=0177F980  EIP={s['crash_ip']:08X}  FLG=00210246\n"
            "CS=0023   DS=002B  SS=002B  ES=002B   FS=0053  GS=002B\n\n")
    new = ("** -- Unhandled exception -- **\n\nVersion = 1.6-custom.00000.0.000\nTime = Sun Oct  4 14:30:00 2026\n"
           "Module = C:\\ProgramData\\MTA San Andreas All\\1.6\\GTA San Andreas\\gta_sa.exe\nCode = 0xC0000005\n"
           f"Offset = 0x{s['crash_ip'] - 0x400000:08X}\n\n" + regs
           + "InvalidParameterCount = 0\nCrashZone = 0\nThreadId = 4120\n\n"
           "Exception: Access violation reading 0x00000010\nResolved Module Offset: 0x0000E6A3\n"
           "Stack trace:\n"
           f"  gta_sa.exe+0x{s['crash_ip'] - 0x400000:X} [0x{s['crash_ip']:X}]\n"
           f"  gta_sa.exe+0x{s['ret_process'] - 0x400000:X} [0x{s['ret_process']:X}]\n"
           f"  gta_sa.exe+0x{s['ret_idle'] - 0x400000:X} [0x{s['ret_idle']:X}]\n"
           f"  core.dll+0x4A1B2 [0x{s['core_ret']:X}]\n"
           "  KERNEL32.DLL+0x1FA29 [0x7655FA29]\n\n"
           "Crash dump file: C:\\Program Files (x86)\\MTA San Andreas 1.6\\mta\\dumps\\private\\"
           "client_1.6-custom.00000.0.000_gtasa_0000e6a3_5_CPFMSA16_00000000_0000_001_00000_20261004_1430.dmp\n")
    return old + new


# --------------------------------------------------------------------------- the single-player sample

#: The single-player sample: CPickup::GiveUsAPickUpObject (0x4567E0) reads the model info of pickup
#: model 20101, which no longer has a definition, while CPickups::Update runs; EAX holds the model ID
#: (CrashInfo list entry 0x00456809). Mod "CoolPickups" defines 20101 but modloader.ini ignores it
#: (the user disabled the mod; the save still has its pickup). Real 1.0 US code addresses, invented data.
SP_SAMPLE = {
    "crash_ip": 0x456809,           # CPickup::GiveUsAPickUpObject+0x29
    "ret_update": 0x458E69,         # after "call CPickup::GiveUsAPickUpObject" at 0x458E64 in CPickups::Update
    "ret_process": 0x53C0B2,        # after "call CPickups::Update" at 0x53C0AD in CGame::Process
    "ret_idle": 0x53E986,           # after "call CGame::Process" at 0x53E981 in Game::Idle
    "model": 20101,
    "mod": "CoolPickups",
    "tid": 2412,
}


def sample_sp_dump() -> bytes:
    """A synthetic single-player dump (WER/ProcDump-like, x86) of :data:`SP_SAMPLE`."""
    s = SP_SAMPLE
    b = DumpBuilder("x86")
    b.add_module(r"C:\Games\GTA San Andreas\gta_sa.exe", 0x400000, 0x1177000, timestamp=0x427101CA)
    b.add_module(r"C:\Games\GTA San Andreas\modloader.asi", 0x10000000, 0x80000, timestamp=0x5A5E0001)
    b.add_module(r"C:\Windows\SysWOW64\ntdll.dll", 0x77A00000, 0x1A0000, timestamp=0x5E0A0001, pdb="wntdll.pdb")
    stack_va = 0x0019E000
    stack = bytearray(0x1000)
    esp = stack_va + 0x900
    ebp = stack_va + 0x980

    def put(va: int, v: int) -> None:
        struct.pack_into("<I", stack, va - stack_va, v)

    put(esp + 0x08, s["ret_update"])        # return into CPickups::Update
    put(esp + 0x14, 0x00C8D4C0)             # a data pointer: not a frame
    put(ebp, ebp + 0x40)
    put(ebp + 4, s["ret_process"])          # return into CGame::Process
    put(ebp + 0x40, ebp + 0x80)
    put(ebp + 0x44, s["ret_idle"])          # return into Game::Idle
    regs = {"eax": s["model"], "ebx": 0, "ecx": 0x9788C0, "edx": 0x25, "esi": 0x9788C0, "edi": 0,
            "ebp": ebp, "esp": esp, "eip": s["crash_ip"], "eflags": 0x10246}
    b.add_thread(s["tid"], regs, stack_va, bytes(stack), name="MainThread")
    code = bytearray(b"\x90" * 0x40)
    code[0x29:0x2C] = b"\x8B\x48\x2C"       # mov ecx,[eax+2Ch] at crash_ip
    b.add_memory(s["crash_ip"] - 0x29, bytes(code))
    b.add_memory(0x458E64, _call(0x458E64, 0x4567E0) + b"\x84\xC0")
    b.add_memory(0x53C0AD, _call(0x53C0AD, 0x458DE0) + b"\x8B\xF0")
    b.add_memory(0x53E981, _call(0x53E981, 0x53BEE0) + b"\x84\xC0")
    b.set_exception(s["tid"], 0xC0000005, s["crash_ip"], [0, 0x2C])
    b.comments.append("(single-player sample crash generated by satk)\n")
    return b.build()


def sample_sp_files() -> dict[str, bytes]:
    """A small single-player game folder for :data:`SP_SAMPLE` (relative path -> bytes).

    The crash report in ``modloader.log`` and the ``scrlog.log`` lines follow the shapes
    :mod:`satk.crash.sptext` and :mod:`satk.crash.gamelogs` read; they are illustrations, not copies
    of real files.
    """
    s = SP_SAMPLE
    m = s["mod"]
    ini = ("[Folder.Config]\nProfile = Default\n\n[Profiles.Default.Config]\nIgnoreAllMods  = false\n"
           "ExcludeAllMods = false\n\n[Profiles.Default.Priority]\n\n[Profiles.Default.IgnoreFiles]\n\n"
           f"[Profiles.Default.IgnoreMods]\n_ignore\n{m}\n\n[Profiles.Default.IncludeMods]\n\n"
           "[Profiles.Default.ExclusiveMods]\n")
    mods = ["BetterHUD", "RoadSigns", "SkinPack", "Weather"]
    log = ["========================== Mod Loader 0.3.7 ==========================", "",
           "Game version: GTA SA 1.0 US", "Reading profile named \"Default\" at \"modloader.ini\".",
           "Using profile named \"Default\".", "", "Scanning mods at \"modloader\\\"..."]
    for name in mods:
        log.append(f"Installing file \"modloader\\{name}\\{name.lower()}.txt\"")
    log += ["", "Mod Loader has started up!", "", "Loading save game \"GTASAsf1.b\"", "",
            "========================== Unhandled Exception ==========================",
            f"Exception Address: 0x{s['crash_ip']:08X}",
            "Exception Code: 0xC0000005 (EXCEPTION_ACCESS_VIOLATION)",
            "Module: C:\\Games\\GTA San Andreas\\gta_sa.exe",
            "Access violation reading location 0x0000002C",
            "Register dump:",
            f"  EAX: 0x{s['model']:08X}   EBX: 0x00000000   ECX: 0x009788C0   EDX: 0x00000025",
            "  ESI: 0x009788C0   EDI: 0x00000000   EBP: 0x0019E980   ESP: 0x0019E900",
            "Backtrace:",
            f"  => 0x{s['crash_ip']:08X} gta_sa.exe+0x{s['crash_ip'] - 0x400000:X}",
            f"     0x{s['ret_update']:08X} gta_sa.exe+0x{s['ret_update'] - 0x400000:X}",
            f"     0x{s['ret_process']:08X} gta_sa.exe+0x{s['ret_process'] - 0x400000:X}",
            f"     0x{s['ret_idle']:08X} gta_sa.exe+0x{s['ret_idle'] - 0x400000:X}", ""]
    ide = f"# {m}: pickup models (sample)\nobjs\n{s['model']}, cp_trophy, cp_trophy, 100, 0\n" \
          f"{s['model'] + 1}, cp_badge, cp_trophy, 100, 0\nend\n"
    scr = ["script cpick", "[0001] WAIT 250", "[00D6] ANDOR 0", "[0038] $COOL_ON == 1", "script main",
           "[0001] WAIT 0", "script cpick", f"[0247] REQUEST_MODEL {s['model']}", "[038B] LOAD_ALL_MODELS_NOW",
           f"[0213] CREATE_PICKUP {s['model']} 3 2490.5 -1668.2 13.3 $COOL_PICKUP", ""]
    cleo = ["05/10/2026 21:40:01.100 Log started.", "05/10/2026 21:40:01.101 Started on game of version: SA 1.0 us",
            "05/10/2026 21:40:01.102 Loading plugin cleo/IniFiles.cleo", "05/10/2026 21:40:04.500 Searching for cleo scripts",
            "05/10/2026 21:40:04.501 Loading custom script cpick.cs...",
            "05/10/2026 21:40:04.502 Registering custom script named cpick",
            "05/10/2026 21:40:09.010 [ERROR] Script cpick.cs: opcode [0213] model 20101 is not available", ""]
    files: dict[str, bytes] = {
        "modloader/modloader.ini": ini.encode(),
        "modloader/modloader.log": "\n".join(log).encode(),
        f"modloader/{m}/coolpickups.ide": ide.encode(),
        f"modloader/{m}/readme.txt": b"CoolPickups (sample mod): adds collectable trophies.\n",
        "scrlog.log": "\n".join(scr).encode(),
        "cleo.log": "\n".join(cleo).encode(),
        "gta_sa_sample.dmp": sample_sp_dump(),
    }
    for name in mods:
        files[f"modloader/{name}/{name.lower()}.txt"] = f"{name} (sample mod)\n".encode()
    return files
