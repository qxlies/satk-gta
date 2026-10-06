"""Disassembler: binary SCM/CLEO -> :class:`Program` -> text (stdlib only).

Decoding follows the control flow from every entry point (the start of the code, each mission, each
label parameter), so data embedded in code (machine code blobs, Sanny Builder footers) is not misread
as commands. Bytes no path reaches are decoded linearly when they decode exactly, else kept as a
``hex ... end`` block. Every byte is represented, so :func:`satk.script.asm.assemble` of the text gives
the input back bit for bit.

File kinds:

* ``cleo`` - a CLEO custom script (``.cs .cs4 .cs5 .cm .s``); jumps are negative offsets from the start;
* ``external`` - a streamed script (``script.img`` entry); same encoding, ``{$EXTERNAL}``;
* ``main`` - ``main.scm``: six header segments (globals, objects, missions, streamed scripts, two
  small ones), the main code (absolute jumps) and the missions (negative offsets from the mission start).
"""

from __future__ import annotations

import bisect
import re
import struct
from dataclasses import dataclass, field

from ..core.errors import SatkError
from .opdb import OpcodeDB
from .scm import (ARRAYS, INT_TYPES, RAW8, RAW128, T_FLOAT, T_GARR, T_GARR_S, T_GARR_V, T_GVAR, T_GVAR_S, T_GVAR_V, T_LARR,
                  T_LARR_S, T_LARR_V, T_LVAR, T_LVAR_S, T_LVAR_V, T_STR8, T_STR16, T_STRV, Arg, DecodeError, Instr,
                  decode_at, esc_bytes, fmt_float, min_int_type)

__all__ = ["Blob", "Section", "MainHeader", "Program", "Disasm", "disassemble", "decode_program", "parse_main_header",
           "render", "fmt_arg", "fmt_instr", "sb_footer", "KINDS", "CLEO_EXTS", "label_targets", "SBFTR"]

KINDS = ("cleo", "external", "main")
CLEO_EXTS = (".cs", ".cs3", ".cs4", ".cs5", ".cm", ".s")
_NAME_OK = re.compile(r"^[A-Za-z0-9_]+$")
_SEG_IDS = (0, 1, 2, 3, 4)
SBFTR = b"__SBFTR\0"


@dataclass(slots=True)
class Blob:
    """Raw bytes kept as ``hex ... end``."""

    off: int
    data: bytes
    why: str = ""
    line: int = 0

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class Section:
    kind: str                      # main | mission | script
    start: int
    end: int
    base: int                      # base of negative (relative) jump offsets
    rel: bool
    index: int = 0                 # mission number (first one using this offset)
    prefix: str = ""
    items: list = field(default_factory=list)   # Instr | Blob

    def label_of(self, off: int) -> str:
        return f"{self.prefix}_{off - self.base}"


@dataclass
class MainHeader:
    """The six header segments of an SA ``main.scm`` (everything kept for an exact rebuild)."""

    game: int = 0x73
    globals_size: int = 0
    global_data: bytes = b""
    objects: list[bytes] = field(default_factory=list)       # raw 24-byte names
    main_size: int = 0
    largest_mission: int = 0
    missions: list[int] = field(default_factory=list)        # absolute offsets
    exclusive: int = 0
    mission_locals: int = 0
    largest_external: int = 0
    externals: list[tuple[bytes, int, int]] = field(default_factory=list)   # raw 20-byte name, offset, size
    seg5: int = 0
    seg6_globals: int = 0
    seg6_threads: int = 0
    seg_ids: tuple[int, ...] = _SEG_IDS
    code_start: int = 0

    def size(self) -> int:
        return (8 + self.globals_size + 12 + 24 * len(self.objects) + 24 + 4 * len(self.missions)
                + 16 + 28 * len(self.externals) + 12 + 16)


@dataclass
class Program:
    kind: str
    sections: list[Section]
    header: MainHeader | None = None
    ext: str = ".cs"
    size: int = 0
    labels: dict[int, str] = field(default_factory=dict)       # offset -> name
    issues: list[tuple[int, str]] = field(default_factory=list)  # (offset, message) decode problems
    reached: set[int] = field(default_factory=set)              # offsets reached through control flow
    db: OpcodeDB | None = None

    def instrs(self):
        for s in self.sections:
            for it in s.items:
                if isinstance(it, Instr):
                    yield s, it

    def section_at(self, off: int) -> Section | None:
        for s in self.sections:
            if s.start <= off < s.end:
                return s
        return None


@dataclass
class Disasm:
    text: str
    program: Program


# --------------------------------------------------------------------------- main.scm header


def _seg(data: bytes, off: int, n: int) -> tuple[int, int]:
    """(jump target, segment id byte) of the header segment at ``off``."""
    if off + 8 > len(data) or data[off:off + 3] != b"\x02\x00\x01":
        raise SatkError("UNSUPPORTED", f"not a GTA SA main.scm: header segment {n} at 0x{off:X} does not start with "
                        "a 0002 jump", hint="disassemble CLEO and script.img scripts with --kind cleo or external")
    tgt = struct.unpack_from("<i", data, off + 3)[0]
    if not off < tgt <= len(data):
        raise SatkError("UNSUPPORTED", f"main.scm header segment {n}: jump to 0x{tgt:X} outside the file")
    return tgt, data[off + 7]


def parse_main_header(data: bytes) -> MainHeader:
    """Read the six SA header segments; ``UNSUPPORTED`` for other layouts (GTA3/VC, headerless files)."""
    h = MainHeader()
    ids = []
    t1, h.game = _seg(data, 0, 1)
    h.globals_size = t1 - 8
    h.global_data = bytes(data[8:t1])
    t2, sid = _seg(data, t1, 2)
    ids.append(sid)
    n = struct.unpack_from("<I", data, t1 + 8)[0]
    if t1 + 12 + 24 * n != t2:
        raise SatkError("UNSUPPORTED", f"main.scm objects segment: {n} names do not end at the next segment")
    h.objects = [bytes(data[t1 + 12 + 24 * i:t1 + 36 + 24 * i]) for i in range(n)]
    t3, sid = _seg(data, t2, 3)
    ids.append(sid)
    h.main_size, h.largest_mission, nm, h.exclusive, h.mission_locals = struct.unpack_from("<IIHHI", data, t2 + 8)
    if t2 + 24 + 4 * nm != t3:
        raise SatkError("UNSUPPORTED", f"main.scm missions segment: {nm} offsets do not end at the next segment")
    h.missions = list(struct.unpack_from(f"<{nm}I", data, t2 + 24))
    t4, sid = _seg(data, t3, 4)
    ids.append(sid)
    h.largest_external, ne = struct.unpack_from("<II", data, t3 + 8)
    if t3 + 16 + 28 * ne != t4:
        raise SatkError("UNSUPPORTED", f"main.scm streamed scripts segment: {ne} entries do not end at the next segment")
    for i in range(ne):
        name, off, size = struct.unpack_from("<20sII", data, t3 + 16 + 28 * i)
        h.externals.append((bytes(name), off, size))
    t5, sid = _seg(data, t4, 5)
    ids.append(sid)
    if t4 + 12 != t5:
        raise SatkError("UNSUPPORTED", "main.scm segment 5 is not 4 bytes long")
    h.seg5 = struct.unpack_from("<I", data, t4 + 8)[0]
    t6, sid = _seg(data, t5, 6)
    ids.append(sid)
    if t5 + 16 != t6:
        raise SatkError("UNSUPPORTED", "main.scm segment 6 is not 8 bytes long")
    h.seg6_globals, h.seg6_threads = struct.unpack_from("<II", data, t5 + 8)
    h.seg_ids = tuple(ids)
    h.code_start = t6
    if h.size() != t6:  # pragma: no cover - the checks above imply it
        raise SatkError("INTERNAL", "main.scm header size mismatch")
    return h


# --------------------------------------------------------------------------- decoding


def _sanitize(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", name).upper().strip("_")
    if not s:
        return ""
    return s if not s[0].isdigit() else "S" + s


def _script_name(sec: Section) -> str:
    for it in sec.items:
        if isinstance(it, Instr) and it.op == 0x03A4 and it.args and it.args[0].t in (T_STR8, RAW8, T_STRV, T_STR16):
            return _sanitize(bytes(it.args[0].v).split(b"\0", 1)[0].decode("latin-1"))
    return ""


def label_targets(ins: Instr) -> list[int]:
    """Indices of ``ins.args`` that are label parameters holding an integer."""
    cmd = ins.cmd
    if cmd is None:
        return []
    out = []
    n = cmd.nfixed
    for i, a in enumerate(ins.args[:n]):
        if cmd.params[i].kind == "label" and a.t in INT_TYPES:
            out.append(i)
    return out


def _resolve(v: int, sec: Section, main: Section | None) -> int | None:
    """Absolute target of a jump value ``v`` used in ``sec`` (``None`` if not representable)."""
    if v < 0 and sec.rel:
        t = sec.base - v
        return t if sec.start <= t < sec.end else None
    if v > 0 and main is not None and main.start <= v < main.end:
        return v
    return None


def sb_footer(data: bytes) -> int | None:
    """Code size from a Sanny Builder footer (``<u32 code size> "__SBFTR" 00`` at the end), else ``None``."""
    if len(data) >= 12 and data[-8:] == SBFTR:
        n = struct.unpack_from("<I", data, len(data) - 12)[0]
        if 0 < n <= len(data) - 12:
            return n
    return None


def decode_program(data: bytes, kind: str, db: OpcodeDB, *, ext: str = ".cs", name: str = "") -> Program:
    """Decode ``data`` into sections of instructions and blobs, with labels resolved.

    ``name`` (a file stem) names the labels of a CLEO/streamed script without ``03A4 script_name``.
    """
    if kind not in KINDS:
        raise SatkError("BAD_PARAMS", f"unknown script kind {kind!r}", did_you_mean=list(KINDS))
    header = None
    if kind == "main":
        header = parse_main_header(data)
        starts = sorted(set(header.missions))
        first = starts[0] if starts else len(data)
        if starts and (starts[0] < header.code_start or starts[-1] >= len(data) and len(data) > 0):
            raise SatkError("UNSUPPORTED", "main.scm mission offsets point outside the code",
                            data={"first": starts[0], "last": starts[-1], "size": len(data)})
        secs = [Section("main", header.code_start, first, 0, False, prefix="MAIN")]
        idx_of = {}
        for i, o in enumerate(header.missions):
            idx_of.setdefault(o, i)
        for k, o in enumerate(starts):
            end = starts[k + 1] if k + 1 < len(starts) else len(data)
            secs.append(Section("mission", o, end, o, True, index=idx_of[o]))
    else:
        footer = sb_footer(data)
        secs = [Section("script", 0, footer or len(data), 0, True, prefix=_sanitize(name))]
    prog = Program(kind, secs, header, ext, len(data), db=db)
    main = secs[0] if kind == "main" else None
    sec_starts = [s.start for s in secs]

    def sec_of(p: int) -> Section | None:
        i = bisect.bisect_right(sec_starts, p) - 1
        if i < 0:
            return None
        s = secs[i]
        return s if p < s.end else None

    decoded: dict[int, Instr] = {}
    fails: dict[int, str] = {}
    entries = [s.start for s in secs if s.end > s.start]
    work = list(reversed(entries))
    while work:
        p = work.pop()
        sec = sec_of(p)
        if sec is None:
            continue
        while p < sec.end and p not in decoded and p not in fails:
            try:
                ins = decode_at(data, p, sec.end, db)
            except DecodeError as e:
                fails[p] = e.msg
                break
            decoded[p] = ins
            for i in label_targets(ins):
                t = _resolve(ins.args[i].v, sec, main)
                if t is not None and t not in decoded and t not in fails:
                    work.append(t)
            if ins.cmd is not None and ins.cmd.ends_flow:
                break
            p += ins.size
    prog.reached = set(decoded)
    starts_sorted = sorted(decoded)
    fail_sorted = sorted(fails)
    for sec in secs:
        _layout(data, sec, decoded, starts_sorted, fail_sorted, fails, db, prog)
    _assign_labels(prog, main)
    if kind != "main" and secs[0].end < len(data):
        e = secs[0].end
        why = "Sanny Builder footer (__SBFTR): compiler data after the code"
        secs[0].items.append(Blob(e, bytes(data[e:]), why))
        prog.issues.append((e, f"{len(data) - e} bytes of {why}"))
        secs[0].end = len(data)
    return prog


def _layout(data: bytes, sec: Section, decoded: dict[int, Instr], starts: list[int], fail_pts: list[int],
            fails: dict[int, str], db: OpcodeDB, prog: Program) -> None:
    p = sec.start
    items = sec.items
    while p < sec.end:
        ins = decoded.get(p)
        if ins is not None and p + ins.size <= sec.end:
            items.append(ins)
            p += ins.size
            continue
        i = bisect.bisect_right(starts, p)
        q = starts[i] if i < len(starts) else sec.end
        q = min(q, sec.end)
        # split the gap at failed entry points (labels may point there)
        cuts = [f for f in fail_pts[bisect.bisect_right(fail_pts, p):bisect.bisect_left(fail_pts, q)]]
        bounds = [p, *cuts, q]
        for a, b in zip(bounds, bounds[1:]):
            _gap(data, a, b, db, items, fails, prog)
        p = q


def _gap(data: bytes, a: int, b: int, db: OpcodeDB, items: list, fails: dict[int, str], prog: Program) -> None:
    chunk = data[a:b]
    if b - a >= 8 and not any(chunk):
        items.append(Blob(a, bytes(chunk), "zeros"))
        return
    lin: list[Instr] = []
    p = a
    ok = a not in fails
    while ok and p < b:
        try:
            ins = decode_at(data, p, b, db)
        except DecodeError:
            ok = False
            break
        lin.append(ins)
        p += ins.size
    if ok and p == b:
        items.extend(lin)
        return
    why = fails.get(a) or "not reached by any jump and does not decode as commands"
    prog.issues.append((a, f"{b - a} bytes kept as hex: {why}"))
    items.append(Blob(a, bytes(chunk), why))


def _assign_labels(prog: Program, main: Section | None) -> None:
    used: set[str] = set()
    for s in prog.sections:
        if s.kind == "main":
            base = "MAIN"
        elif s.kind == "mission":
            base = _script_name(s) or f"MISSION{s.index}"
        else:
            base = _script_name(s) or s.prefix or "SCRIPT"
        name, k = base, 2
        while name in used:
            name = f"{base}_M{s.index}" if s.kind == "mission" and k == 2 else f"{base}{k}"
            k += 1
        used.add(name)
        s.prefix = name
    points: dict[int, Section] = {}
    for s in prog.sections:
        for it in s.items:
            points[it.off] = s
    for s in prog.sections:
        for it in s.items:
            if not isinstance(it, Instr):
                continue
            for i in label_targets(it):
                a = it.args[i]
                t = _resolve(a.v, s, main)
                ts = points.get(t) if t is not None else None
                if ts is None:
                    continue
                a.label = ts.label_of(t)
                prog.labels[t] = a.label
    if prog.header is not None:
        for o in prog.header.missions:
            s = points.get(o)
            if s is not None and s.start == o:
                prog.labels[o] = s.label_of(o)


# --------------------------------------------------------------------------- text


def _gvar(off: int, prefix: str = "") -> str:
    return f"{prefix}${off // 4}" if off % 4 == 0 else f"{prefix}&{off}"


def _int_text(v: int) -> str:
    return (f"-0x{-v:X}" if v < 0 else f"0x{v:X}") if abs(v) >= 0x100000 else str(v)


def fmt_arg(a: Arg, kind: str = "any", ptype: str = "", objects: dict[int, str] | None = None) -> str:
    """Text form of one parameter (see :mod:`satk.script.scm`)."""
    if a.label:
        return "@" + a.label
    t, v = a.t, a.v
    if t in INT_TYPES:
        minimal = t == min_int_type(v)
        if minimal:
            if objects and kind == "model" and v < 0 and -v in objects:
                return "#" + objects[-v]
            if ptype == "bool" and v in (0, 1):
                return "true" if v else "false"
            return _int_text(v)
        return f"{_int_text(v)}:i{8 * {4: 1, 5: 2, 1: 4}[t]}"
    if t == T_FLOAT:
        return fmt_float(v)
    if t == T_GVAR:
        return _gvar(v)
    if t == T_LVAR:
        return f"{v}@"
    if t == T_GVAR_S:
        return _gvar(v, "s")
    if t == T_LVAR_S:
        return f"{v}@s"
    if t == T_GVAR_V:
        return _gvar(v, "v")
    if t == T_LVAR_V:
        return f"{v}@v"
    if t in ARRAYS:
        base, idx, size, flags = v
        head = {T_GARR: _gvar(base), T_LARR: f"{base}@", T_GARR_S: _gvar(base, "s"), T_LARR_S: f"{base}@s",
                T_GARR_V: _gvar(base, "v"), T_LARR_V: f"{base}@v"}[t]
        ix = _gvar(idx) if flags & 0x80 else f"{idx}@"
        et = flags & 0x7F
        return f"{head}({ix},{size}{'ifsv'[et] if et < 4 else f't{et}'})"
    if t == T_STR8:
        return esc_bytes(bytes(v).rstrip(b"\0"), "'")
    if t == T_STR16:
        return "v" + esc_bytes(bytes(v).rstrip(b"\0"), "'")
    if t == RAW8 or t == RAW128:
        return "r" + esc_bytes(bytes(v).rstrip(b"\0"), "'")
    if t == T_STRV:
        return esc_bytes(bytes(v), '"')
    raise ValueError(f"unknown parameter type {t}")  # pragma: no cover


def fmt_instr(ins: Instr, objects: dict[int, str] | None = None, annotate: bool = False) -> str:
    cmd = ins.cmd
    parts = [f"{ins.opw:04X}:"]
    if ins.negated:
        parts.append("not")
    if cmd is not None:
        parts.append(cmd.name.lower())
    params = cmd.params if cmd is not None else ()
    nfix = cmd.nfixed if cmd is not None else len(ins.args)
    for i, a in enumerate(ins.args):
        p = params[i] if i < nfix and i < len(params) else None
        if annotate and p is not None and p.name:
            parts.append("{" + p.name + "}")
        parts.append(fmt_arg(a, p.kind if p else "any", p.type if p else "", objects))
    return " ".join(parts)


def _hex_lines(data: bytes, per: int = 16) -> list[str]:
    toks: list[str] = []
    i = 0
    n = len(data)
    while i < n:
        j = i
        while j < n and data[j] == data[i]:
            j += 1
        if j - i >= 8:
            toks.append(f"{data[i]:02X}*{j - i}")
        else:
            toks.extend(f"{b:02X}" for b in data[i:j])
        i = j
    return ["  " + " ".join(toks[k:k + per]) for k in range(0, len(toks), per)]


def _obj_name(raw: bytes) -> str:
    s = raw.rstrip(b"\0")
    if not s:
        return "(noname)"
    if b"\0" not in s and _NAME_OK.match(s.decode("latin-1")):
        return s.decode("latin-1")
    return esc_bytes(s, "'")


def _header_lines(h: MainHeader, prog: Program) -> list[str]:
    out = [f"DEFINE GLOBALS_SIZE {h.globals_size}"]
    if h.game != 0x73:
        out.append(f"DEFINE TARGET_GAME 0x{h.game:02X}")
    if h.seg_ids != _SEG_IDS:
        out.append("DEFINE SEGMENT_IDS " + " ".join(str(x) for x in h.seg_ids))
    if any(h.global_data):
        k = 0
        data = h.global_data
        while k < len(data):
            if not data[k]:
                k += 1
                continue
            e = k
            while e < len(data) and (any(data[e:e + 8])):
                e += 1
            out.append(f"DEFINE GLOBAL_BYTES {k} {data[k:e].hex().upper()}")
            k = e
    out.append(f"DEFINE OBJECTS {len(h.objects)}")
    out += [f"DEFINE OBJECT {_obj_name(o)}" for o in h.objects]
    out.append(f"DEFINE MISSIONS {len(h.missions)}")
    for i, o in enumerate(h.missions):
        out.append(f"DEFINE MISSION {i} AT @{prog.labels[o]}")
    out.append(f"DEFINE EXCLUSIVE_MISSIONS {h.exclusive}")
    out.append(f"DEFINE MISSION_LOCALS {h.mission_locals}")
    out.append(f"DEFINE EXTERNAL_SCRIPTS {len(h.externals)}")
    out.append(f"DEFINE LARGEST_EXTERNAL_SIZE {h.largest_external}")
    for name, off, size in h.externals:
        out.append(f"DEFINE SCRIPT {_obj_name(name)} OFFSET {off} SIZE {size}")
    out.append(f"DEFINE UNKNOWN_EMPTY_SEGMENT {h.seg5}")
    if h.seg6_globals != h.globals_size:
        out.append(f"DEFINE GLOBAL_VARS_SIZE {h.seg6_globals}")
    out.append(f"DEFINE UNKNOWN_THREADS_MEMORY {h.seg6_threads}")
    # derived values: written only when the file disagrees with what the assembler computes
    secs = prog.sections
    main_end = secs[1].start if len(secs) > 1 else prog.size
    if h.main_size != main_end:
        out.append(f"DEFINE MAIN_SIZE {h.main_size}")
    largest = max((s.end - s.start for s in secs[1:]), default=0)
    if h.largest_mission != largest:
        out.append(f"DEFINE LARGEST_MISSION_SIZE {h.largest_mission}")
    return out


def render(prog: Program, *, objects: bool = True, annotate: bool = False, source: str = "") -> str:
    """The text form of ``prog`` (``objects``: model ids of main.scm as ``#NAME``)."""
    lines: list[str] = []
    what = {"main": "main.scm", "external": "streamed script", "cleo": "CLEO script"}[prog.kind]
    lines.append(f"// {what}{' ' + source if source else ''}: {prog.size} bytes; satk script disasm"
                 + (f", opcode db {prog.db.source}" if prog.db is not None else ""))
    if prog.kind == "cleo":
        lines.append(f"{{$CLEO {prog.ext}}}")
    elif prog.kind == "external":
        lines.append("{$EXTERNAL}")
    else:
        lines.append("{$MAIN}")
    if prog.db is not None and prog.db.prefer:
        lines.append(f"{{$USE {' '.join(prog.db.prefer)}}}")
    objmap = None
    if prog.header is not None:
        lines += _header_lines(prog.header, prog)
        if objects:
            names = {}
            seen: dict[str, int] = {}
            for i, raw in enumerate(prog.header.objects):
                s = raw.split(b"\0", 1)[0].decode("latin-1")
                if i and s and _NAME_OK.match(s) and raw.rstrip(b"\0") == raw.split(b"\0", 1)[0]:
                    seen[s.upper()] = seen.get(s.upper(), 0) + 1
                    names[i] = s
            objmap = {i: s for i, s in names.items() if seen[s.upper()] == 1}
    for s in prog.sections:
        if s.kind == "mission":
            lines += ["", f"//========== mission {s.index}: {s.prefix} ==========", "{$MISSION}"]
        elif s.kind == "main":
            lines += ["", "//========== main =========="]
        for it in s.items:
            lab = prog.labels.get(it.off)
            if lab is not None:
                lines.append(f":{lab}")
            if isinstance(it, Instr):
                lines.append(fmt_instr(it, objmap, annotate))
            else:
                lines.append(f"hex // {len(it.data)} bytes" + (f": {it.why}" if it.why and it.why != "zeros" else ""))
                lines += _hex_lines(it.data)
                lines.append("end")
    lines.append("")
    return "\n".join(lines)


def disassemble(data: bytes, kind: str, db: OpcodeDB, *, ext: str = ".cs", annotate: bool = False,
                source: str = "", name: str = "") -> Disasm:
    prog = decode_program(data, kind, db, ext=ext, name=name)
    return Disasm(render(prog, annotate=annotate, source=source), prog)
