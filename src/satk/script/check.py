"""Static checks over a decoded or assembled :class:`~satk.script.disasm.Program` (stdlib only).

Findings are ``(severity, offset, line, code, message)``; ``line`` is 0 for binary input. Codes:

* ``UNDECODED`` - bytes kept as ``hex`` (unknown opcode, broken parameters, data);
* ``JUMP_OUTSIDE`` / ``JUMP_MISALIGNED`` / ``JUMP_ABSOLUTE`` / ``JUMP_ZERO`` - a label parameter that leaves its
  script, lands inside an instruction, is a positive (main.scm) offset in a CLEO script, or encodes 0;
* ``NO_TERMINATE`` / ``FALLS_INTO_DATA`` - execution can run past the last command or into data
  (a CLEO script must end with ``0A93 terminate_this_custom_script``, a jump or a return);
* ``LOOP_NO_WAIT`` - a backward jump whose loop body has no ``0001 wait`` and no call (freezes the game);
* ``IF_COUNT`` - ``00D6 if N`` followed by a different number of conditions before ``004D``;
* ``NOT_CONDITION`` - the NOT bit on a command that sets no condition;
* ``SCRIPT_NAME`` - a ``03A4 script_name`` longer than 7 characters.
"""

from __future__ import annotations

import bisect

from .disasm import Blob, Program, Section, _resolve, label_targets
from .scm import INT_TYPES, RAW8, T_STR8, T_STRV, Instr

__all__ = ["Finding", "check_program", "SEVERITIES"]

SEVERITIES = ("error", "warn", "info")
_PREDICATE = ("IS_", "HAS_", "ARE_", "CAN_", "DOES_", "LOCATE_", "WAS_", "DID_", "TEST_", "IF_")
_CALLS = {0x0050, 0x0AB1}
_JUMPS = {0x0002, 0x004D}

Finding = tuple[str, int, int, str, str]


def _is_condition(ins: Instr) -> bool:
    c = ins.cmd
    return c is not None and (c.condition or c.name.startswith(_PREDICATE))


def check_program(prog: Program, lines: dict[int, int] | None = None) -> list[Finding]:
    """All findings, sorted by position."""
    lines = lines or {}
    out: list[Finding] = []
    main = prog.sections[0] if prog.kind == "main" else None

    def add(sev: str, off: int, code: str, msg: str) -> None:
        out.append((sev, off, lines.get(off, 0), code, msg))

    for off, msg in prog.issues:
        sev = "info" if "Sanny Builder" in msg else "warn"
        add(sev, off, "UNDECODED", msg)
    reached = prog.reached or None
    for sec in prog.sections:
        items = sec.items
        starts = [it.off for it in items]
        for k, it in enumerate(items):
            if not isinstance(it, Instr):
                continue
            _check_labels(prog, sec, main, it, starts, add)
            nxt = items[k + 1] if k + 1 < len(items) else None
            live = reached is None or it.off in reached
            if live and it.cmd is not None and not it.cmd.ends_flow:
                if nxt is None:
                    what = {"cleo": "0A93: terminate_this_custom_script", "external": "004E: terminate_this_script",
                            "main": "004E: terminate_this_script"}[prog.kind]
                    add("warn", it.off, "NO_TERMINATE",
                        f"{it.cmd.name.lower()} is the last command and execution continues past the end; "
                        f"end with {what} or a goto")
                elif isinstance(nxt, Blob):
                    add("warn", it.off, "FALLS_INTO_DATA",
                        f"execution continues from {it.cmd.name.lower()} into {nxt.size} bytes of data")
            if it.negated and it.cmd is not None and not _is_condition(it):
                add("info", it.off, "NOT_CONDITION", f"'not' on {it.cmd.name.lower()}, which sets no condition")
            if it.op == 0x03A4 and it.args and it.args[0].t in (T_STR8, T_STRV, RAW8):
                name = bytes(it.args[0].v).split(b"\0", 1)[0]
                if len(name) > 7:
                    add("warn", it.off, "SCRIPT_NAME", f"script name '{name.decode('latin-1')}' is longer than "
                                                      "7 characters (the game keeps 7)")
            if it.op == 0x00D6 and it.args and it.args[0].t in INT_TYPES:
                _check_if(it, items, k, add)
        _check_loops(sec, add, main)
    out.sort(key=lambda f: (f[1], SEVERITIES.index(f[0]), f[3]))
    return out


def _check_labels(prog: Program, sec: Section, main: Section | None, it: Instr, starts: list[int], add) -> None:
    for i in label_targets(it):
        a = it.args[i]
        v = a.v
        name = it.cmd.name.lower()
        if v == 0:
            if sec.rel:
                add("warn", it.off, "JUMP_ZERO", f"{name}: offset 0 jumps to the start of main.scm, not of this "
                                                 "script (a label on the first byte; put a command before it)")
            continue
        if v > 0 and main is None:
            add("error", it.off, "JUMP_ABSOLUTE", f"{name}: positive offset {v} is a main.scm address; jumps inside "
                                                  "this script are negative")
            continue
        t = _resolve(v, sec, main)
        if t is None:
            add("error", it.off, "JUMP_OUTSIDE", f"{name}: offset {v} lands outside the script")
            continue
        tsec = sec if sec.start <= t < sec.end else main
        tstarts = starts if tsec is sec else [x.off for x in tsec.items]
        j = bisect.bisect_left(tstarts, t)
        if j >= len(tstarts) or tstarts[j] != t:
            add("error", it.off, "JUMP_MISALIGNED", f"{name}: offset {v} lands inside an instruction")


def _check_if(it: Instr, items: list, k: int, add) -> None:
    n = it.args[0].v
    want = 1 if n == 0 else n + 1 if 1 <= n <= 7 else n - 19 if 21 <= n <= 27 else None
    if want is None:
        add("warn", it.off, "IF_COUNT", f"if {n}: use 0 (one condition), 1-7 (and, 2-8 conditions) or 21-27 (or)")
        return
    got = 0
    for nxt in items[k + 1:k + 11]:
        if not isinstance(nxt, Instr):
            return
        if nxt.op == 0x004D:
            break
        got += 1
    else:
        return
    if got != want:
        add("warn", it.off, "IF_COUNT", f"if {n} expects {want} condition(s) before goto_if_false, found {got}")


def _check_loops(sec: Section, add, main: Section | None) -> None:
    instrs = [it for it in sec.items if isinstance(it, Instr)]
    offs = [it.off for it in instrs]
    waits = [it.off for it in instrs if it.op == 0x0001 or it.op in _CALLS]
    for it in instrs:
        if it.op not in _JUMPS:
            continue
        for i in label_targets(it):
            t = _resolve(it.args[i].v, sec, main)
            if t is None or t > it.off or not (sec.start <= t < sec.end):
                continue
            j = bisect.bisect_left(waits, t)
            if j < len(waits) and waits[j] <= it.off:
                continue
            a = bisect.bisect_left(offs, t)
            b = bisect.bisect_right(offs, it.off)
            body = instrs[a:b]
            exits = it.op == 0x004D or _jumps_out(body, t, it.off, sec, main)
            if not exits:
                why = "has no wait and no way out: the game freezes for good"
            elif not any(_writes(x) for x in body):
                why = "has no wait and changes no variable, so its condition cannot change: the game freezes"
            else:
                continue          # a counter loop (it changes variables and can leave): fine without wait
            add("warn", it.off, "LOOP_NO_WAIT", f"loop of {b - a} commands back to @{it.args[i].label or t} {why} "
                                                "(add 0001: wait 0)")


#: Commands that write a number variable: set, +=, -=, *=, /=, var-to-var, constants, CLEO int math.
_WRITES = (frozenset(range(0x0004, 0x0018)) | frozenset(range(0x0058, 0x0068)) | frozenset(range(0x0084, 0x009A))
           | frozenset({0x04AE, 0x04AF}) | frozenset(range(0x0A8E, 0x0A92)))


def _writes(x: Instr) -> bool:
    if x.op in _WRITES:
        return True
    c = x.cmd
    if c is None:
        return False
    return any(p.out for p in c.params[:len(x.args)]) or c.variadic


def _jumps_out(body: list[Instr], t: int, end: int, sec: Section, main: Section | None) -> bool:
    for x in body:
        if x.op in _JUMPS or x.op in (0x0051, 0x004E, 0x0A93, 0x0AB2):
            if x.op in (0x0051, 0x004E, 0x0A93, 0x0AB2):
                return True
            for i in label_targets(x):
                tt = _resolve(x.args[i].v, sec, main)
                if tt is None or not (t <= tt <= end):
                    return True
    return False
