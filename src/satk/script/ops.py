"""Operations of satk.script: ``satk script disasm|asm|check|new`` (CLEO/SCM scripts).

All CLI-only (``mcp=False``); MCP clients reach them through ``satk_op``. Writes go under
``work/out/script/`` (or an explicit ``--out``); game files are only read. Module-level imports are
stdlib-only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, obj, table, with_warn
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath, open_ro, work
from ..core.registry import op

__all__ = ["script_disasm", "script_asm", "script_check", "script_new"]

Kind = Literal["auto", "cleo", "external", "main"]
Template = Literal["hello", "cheat", "spawn_car", "teleport", "text", "mission"]
_TEXT_EXTS = (".txt", ".sb", ".src")
_MAX_READ = 64 * 1024 * 1024


# --------------------------------------------------------------------------- helpers


def _out_base() -> Path:
    return Path(os.path.abspath(cfg().paths.work)) / "out" / "script"


def _find_input(file: str, profile: str) -> Path:
    """``file`` as given, else under work/out/script/, the profile's game root, data/script/ and cleo/."""
    p = Path(file)
    if p.is_file():
        return Path(os.path.abspath(p))
    tried = [p]
    if not p.is_absolute():
        cands = [_out_base() / p]
        try:
            from ..core.paths import profile_root

            root = profile_root(profile)
            cands += [root / p, root / "data" / "script" / p, root / "cleo" / p]
        except SatkError:
            pass
        for c in cands:
            tried.append(c)
            if c.is_file():
                return Path(os.path.abspath(c))
    raise SatkError("NOT_FOUND", f"no file {file!r}",
                    hint="give a path; a relative name is also looked up in work/out/script/ and in the game's "
                         "folder, data/script/ and cleo/ (--profile)",
                    data={"tried": [jpath(t) if t.is_absolute() else str(t) for t in tried][:6]})


def _read(p: Path) -> bytes:
    if p.stat().st_size > _MAX_READ:
        raise SatkError("BAD_PARAMS", f"{p.name} is larger than 64 MB: not a script")
    with open_ro(p) as f:
        return f.read()


def _out_path(out: str | None, stem: str, ext: str, force: bool, src: Path | None = None) -> Path:
    """``out`` or ``work/out/script/<stem>/<stem><ext>``; relative paths go under work/out/script/."""
    base = _out_base()
    if out is None:
        dst = work("out", "script", stem, f"{stem}{ext}")
    else:
        q = Path(out)
        if not q.is_absolute():
            q = Path(os.path.abspath(base / q))
            if os.path.commonpath([str(q), str(base)]) != str(base):
                raise SatkError("BAD_PARAMS", f"relative --out must stay under {jpath(base)}: {out!r}")
        if q.is_dir() or out.endswith(("/", "\\")):
            q = q / f"{stem}{ext}"
        dst = ensure_writable(q)
    if src is not None and dst.exists() and os.path.samefile(src, dst):
        raise SatkError("BAD_PARAMS", "the output file is the input file", hint="give another --out")
    w = os.path.normcase(os.path.abspath(cfg().paths.work))
    try:
        inside = os.path.commonpath([os.path.normcase(str(dst)), w]) == w
    except ValueError:
        inside = False
    if dst.exists() and not force and not inside:
        raise SatkError("EXISTS", f"{jpath(dst)} exists", hint="add --force to overwrite it")
    return dst


def _db(opdb: str, warn: list[str]):
    from .opdb import load_db

    return load_db(opdb, warn=warn)


def _is_text(p: Path, data: bytes) -> bool:
    if p.suffix.lower() in _TEXT_EXTS:
        return True
    if b"\0" in data[:4096] or not data:
        return False
    try:
        head = data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return "{$" in head or ":" in head and any(ch.isalpha() for ch in head[:200])


def _detect_kind(p: Path, data: bytes, kind: str) -> tuple[str, str]:
    """(kind, CLEO extension)."""
    from .disasm import CLEO_EXTS, parse_main_header

    ext = p.suffix.lower()
    if kind != "auto":
        return kind, ext if ext in CLEO_EXTS else ".cs"
    if ext in CLEO_EXTS:
        return "cleo", ext
    if ext == ".scm" or p.name.lower() == "main.scm":
        if data[:3] == b"\x02\x00\x01":
            try:
                parse_main_header(data)
                return "main", ".cs"
            except SatkError:
                pass
        return "external", ".cs"
    return "cleo", ".cs"


def _img_entry(p: Path, entry: str | None) -> tuple[bytes, str, int]:
    """(bytes, entry stem, trimmed padding) of a script.img entry; the size comes from main.scm when present."""
    import struct

    from ..formats.img import ImgArchive
    from ..formats.rw import FormatError

    try:
        a = ImgArchive.open(p)
    except FormatError as e:
        raise SatkError("BAD_PARAMS", f"{p.name}: {e}") from None
    with a:
        names = [e.name for e in a.entries]
        if not entry:
            raise SatkError("BAD_PARAMS", f"{p.name} holds {len(names)} scripts: pick one with --entry",
                            did_you_mean=names[:8], hint=f"satk script disasm {p.name} --entry {names[0] if names else 'x.scm'}")
        e = a.find(entry) or a.find(entry + ".scm")
        if e is None:
            import difflib

            raise SatkError("NOT_FOUND", f"no entry {entry!r} in {p.name}",
                            did_you_mean=difflib.get_close_matches(entry.lower(), [n.lower() for n in names], 5, 0.5))
        data = a.read(e)
    size = None
    main = p.with_name("main.scm")
    if main.is_file():
        try:
            from .disasm import parse_main_header

            with open_ro(main) as f:
                head = f.read(256 * 1024)
            # the header ends before the code; the streamed-script table names each entry's real size
            for raw, _off, sz in parse_main_header(head).externals:
                if raw.split(b"\0", 1)[0].decode("latin-1").lower() == e.stem.lower():
                    size = sz
                    break
        except (SatkError, OSError, struct.error):
            size = None
    trimmed = 0
    if size is not None and 0 < size <= len(data) and not any(data[size:]):
        trimmed = len(data) - size
        data = data[:size]
    return data, e.stem, trimmed


def _head(text: str, limit: int) -> list[str]:
    """The first ``limit`` code lines (header DEFINEs and comments skipped)."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("//") or s.upper().startswith("DEFINE "):
            continue
        out.append(ln)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- operations


@op("script.disasm", mcp=False, group="formats",
    summary="Disassemble a GTA SA script to text close to Sanny Builder's opcode syntax ('0001: wait 0', @labels, "
            "typed literals): CLEO .cs/.cm/.s, main.scm with its headers, script.img entries. Writes "
            "work/out/script/<name>/<name>.txt; 'script asm' of it gives the same bytes.",
    summary_ru="Дизассемблировать скрипт SA (CLEO .cs/.cm/.s, main.scm с заголовками, записи script.img) в текст "
               "в духе Sanny Builder ('0001: wait 0', метки, типизированные литералы); обратная сборка бит в бит.",
    examples=("satk script disasm mymod.cs", "satk script disasm main.scm",
              "satk script disasm script.img --entry trains.scm", "satk script disasm hello/hello.cs --annotate"))
def script_disasm(file: str, kind: Kind = "auto", entry: str | None = None, out: str | None = None,
                  opdb: str = "auto", annotate: bool = False, verify: bool | None = None, limit: int = 20,
                  profile: str = "vanilla", force: bool = False) -> dict:
    """Disassemble a script into the satk text form.

    Args:
        file: .cs/.cm/.s CLEO script, main.scm, a streamed .scm, or script.img (with --entry); relative names are
            also looked up in work/out/script/ and the game's data/script/ and cleo/ folders.
        kind: auto (by name and header), cleo, external (script.img entry) or main (main.scm).
        entry: the script inside script.img (trains.scm or trains).
        out: output text file; relative paths and the default go under work/out/script/<name>/.
        opdb: opcode database: auto (kb, else the cleo-ai clone, else the bundled core subset), kb, cleo-ai, core
            or a JSON file.
        annotate: add Sanny Builder style {name} hints before parameters (comments; still assembles the same).
        verify: assemble the text again and compare (default: files up to 1 MB).
        limit: code lines shown in 'head'.
        profile: game whose folders resolve relative names.
        force: overwrite an existing file outside work/.
    """
    from .disasm import disassemble

    warn: list[str] = []
    p = _find_input(file, profile)
    trimmed = 0
    if p.suffix.lower() == ".img":
        data, stem, trimmed = _img_entry(p, entry)
        k, ext = ("external", ".cs") if kind == "auto" else (kind, ".cs")
        label = f"{p.name}:{stem}.scm"
    else:
        if entry:
            warn.append("UNUSED: --entry only applies to script.img")
        data = _read(p)
        if _is_text(p, data):
            raise SatkError("BAD_PARAMS", f"{p.name} is a text file", hint=f"satk script asm {file}")
        k, ext = _detect_kind(p, data, kind)
        stem = p.stem
        label = p.name
    db = _db(opdb, warn)
    d = disassemble(data, k, db, ext=ext, annotate=annotate, source=label, name=stem)
    prog = d.program
    dst = _out_path(out, stem, ".txt", force, p)
    if out is None and not force and dst.is_file() and dst.read_bytes() != d.text.encode("utf-8"):
        # never clobber a source the user (or 'script new') wrote next to the binary
        alt = dst.with_name(f"{stem}.disasm.txt")
        warn.append(f"KEPT: {dst.name} exists and differs (your source?); wrote {alt.name} (--force overwrites)")
        dst = alt
    atomic_write(dst, d.text)
    exact = None
    if verify or (verify is None and len(data) <= 1 << 20):
        from .asm import assemble

        try:
            exact = assemble(d.text, db).data == data
        except SatkError as e:
            exact = False
            warn.append(f"NOT_EXACT: the text does not assemble: {e.msg}")
        if exact is False and not any(w.startswith("NOT_EXACT") for w in warn):
            warn.append("NOT_EXACT: assembling the text does not give the same bytes (please report)")
    ninstr = sum(1 for _ in prog.instrs())
    hexb = sum(it.size for s in prog.sections for it in s.items if not hasattr(it, "opw"))
    for off, msg in prog.issues[:10]:
        warn.append(f"UNDECODED: 0x{off:X}: {msg}")
    if len(prog.issues) > 10:
        warn.append(f"UNDECODED: {len(prog.issues) - 10} more blocks kept as hex")
    env = obj(None, file=jpath(dst), src=label, kind=k, bytes=len(data), commands=ninstr, labels=len(prog.labels),
              missions=len(prog.header.missions) if prog.header else None, hex_bytes=hexb or None,
              padding_trimmed=trimmed or None, opdb=db.describe(), exact=exact,
              lines=d.text.count("\n"), head=_head(d.text, clamp_limit(limit)))
    return with_warn(env, *warn)


@op("script.asm", mcp=False, group="formats",
    summary="Assemble satk script text ('0001: wait 0' or 'wait 0', :labels, {$CLEO .cs}, {$MAIN} with DEFINEs) to a "
            "CLEO script, streamed .scm or main.scm. Checks parameter counts and types against the opcode db; errors "
            "carry line numbers and did_you_mean. --compare FILE checks byte for byte.",
    summary_ru="Собрать текст скрипта (опкоды или имена команд, метки, {$CLEO .cs}, {$MAIN}) в CLEO .cs, .scm или "
               "main.scm; проверка числа и типов параметров, ошибки с номерами строк; --compare сверяет бит в бит.",
    examples=("satk script asm hello/hello.txt", "satk script asm mymod.txt --out mymod/mymod.cs",
              "satk script asm main/main.txt --compare main.scm"))
def script_asm(file: str, out: str | None = None, kind: Kind = "auto", opdb: str = "auto",
               compare: str | None = None, profile: str = "vanilla", force: bool = False) -> dict:
    """Assemble a text script.

    Args:
        file: text file (.txt); relative names are also looked up in work/out/script/.
        out: output file; relative paths and the default go under work/out/script/<name>/ (extension from
            {$CLEO .ext}, else .scm).
        kind: auto (the {$CLEO}/{$EXTERNAL}/{$MAIN} directive, else CLEO .cs), cleo, external or main.
        opdb: opcode database (auto, kb, cleo-ai, core or a JSON file).
        compare: a binary script to compare the result with byte for byte (e.g. the original).
        profile: game for #MODEL names (index) and relative --compare names.
        force: overwrite an existing file outside work/.
    """
    from .asm import assemble

    warn: list[str] = []
    p = _find_input(file, profile)
    raw = _read(p)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SatkError("BAD_PARAMS", f"{p.name} is not UTF-8 text", hint=f"satk script disasm {file}") from None
    if "\0" in text[:4096]:
        raise SatkError("BAD_PARAMS", f"{p.name} is a binary file", hint=f"satk script disasm {file}")
    db = _db(opdb, warn)
    r = assemble(text, db, kind=None if kind == "auto" else kind, profile=profile, source=p.name)
    warn += r.warnings[:30]
    if len(r.warnings) > 30:
        warn.append(f"SOURCE: {len(r.warnings) - 30} more warnings")
    stem = p.stem.removesuffix(".disasm")
    dst = _out_path(out, stem, r.ext, force, p)
    other = None
    if compare:
        c = _find_input(compare, profile)
        other = _read(c)                 # read before writing: --compare may name the output file itself
    atomic_write(dst, r.data)
    env = obj(None, file=jpath(dst), src=p.name, kind=r.kind, bytes=len(r.data),
              commands=sum(1 for _ in r.program.instrs()), labels=len(r.program.labels), opdb=db.describe())
    if other is not None:
        same = other == r.data
        env["same"] = same
        if not same:
            n = min(len(other), len(r.data))
            first = next((i for i in range(n) if other[i] != r.data[i]), n)
            env["first_diff"] = f"0x{first:X}"
            env["compare_bytes"] = len(other)
            warn.append(f"DIFFERS: {c.name} differs from the result at 0x{first:X}")
    if r.kind == "cleo":
        env["install"] = f"copy {dst.name} into the game's cleo folder (CLEO 4 or 5); satk never writes into the game"
    return with_warn(env, *warn)


@op("script.check", mcp=False, group="formats",
    summary="Static checks of a script (text or binary): unknown opcodes and names, wrong parameter counts/types "
            "(with line numbers), jumps outside the script or into an instruction, a CLEO script that runs past its "
            "end (no 0A93), loops without wait, wrong 'if' condition counts. Table sev/at/code/msg.",
    summary_ru="Статическая проверка скрипта (текст или бинарник): неизвестные опкоды, число и типы параметров, "
               "прыжки за пределы, нет 0A93 в конце, циклы без wait, неверное число условий if.",
    examples=("satk script check hello/hello.txt", "satk script check mymod.cs", "satk script check main.scm --severity warn"))
def script_check(file: str, kind: Kind = "auto", entry: str | None = None, opdb: str = "auto",
                 severity: Literal["error", "warn", "info"] = "info", limit: int = 20, cursor: str | None = None,
                 profile: str = "vanilla") -> dict:
    """Check a script.

    Args:
        file: text (.txt) or binary script (.cs/.cm/.s/.scm/main.scm, script.img with --entry).
        kind: auto, cleo, external or main.
        entry: the script inside script.img.
        opdb: opcode database (auto, kb, cleo-ai, core or a JSON file).
        severity: lowest severity shown (error, warn, info).
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
        profile: game whose folders resolve relative names.
    """
    from .check import SEVERITIES, check_program

    warn: list[str] = []
    p = _find_input(file, profile)
    db = _db(opdb, warn)
    rows: list[list] = []
    if p.suffix.lower() == ".img":
        data, stem, _trim = _img_entry(p, entry)
        text = None
        k = "external" if kind == "auto" else kind
    else:
        data = _read(p)
        text = None
        if _is_text(p, data):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise SatkError("BAD_PARAMS", f"{p.name} is not UTF-8 text") from None
        k = None
    if text is not None:
        from .asm import assemble

        r = assemble(text, db, kind=None if kind == "auto" else kind, profile=profile, source=p.name,
                     raise_errors=False)
        for e in r.errors:
            rows.append(["error", f"line {e.line}" if e.line else "-", "ASM", e.msg
                         + (f" (did you mean: {', '.join(e.did_you_mean[:3])})" if e.did_you_mean else "")])
        for w in r.warnings:
            code, _, msg = w.partition(": ")
            rows.append(["warn", _line_of(msg), code, msg.split(": ", 1)[-1] if msg.startswith("line ") else msg])
        if not r.errors:
            for sev, off, line, code, msg in check_program(r.program, r.lines):
                rows.append([sev, f"line {line}" if line else f"0x{off:X}", code, msg])
        kind_out = r.kind
    else:
        from .disasm import decode_program

        if k is None:
            k, ext = _detect_kind(p, data, kind)
        prog = decode_program(data, k, db, name=p.stem)
        for sev, off, _line, code, msg in check_program(prog):
            rows.append([sev, f"0x{off:X}", code, msg])
        kind_out = k
    seen: set[tuple[str, str]] = set()
    uniq = []
    for r in rows:                   # the assembler and the checks may both report JUMP_ZERO
        if r[2] == "ASM" or (r[1], r[2]) not in seen:
            seen.add((r[1], r[2]))
            uniq.append(r)
    rows = uniq
    counts = {s: sum(1 for r in rows if r[0] == s) for s in SEVERITIES}
    keep = SEVERITIES[:SEVERITIES.index(severity) + 1]
    rows = [r for r in rows if r[0] in keep]
    lim = clamp_limit(limit)
    off = int(cursor) if cursor and cursor.isdigit() else 0
    if cursor and not cursor.isdigit():
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page")
    env = table(["sev", "at", "code", "msg"], rows[off:off + lim], total=len(rows),
                next=str(off + lim) if off + lim < len(rows) else None, warn=warn)
    env["src"] = p.name
    env["kind"] = kind_out
    env["counts"] = {s: n for s, n in counts.items() if n}
    env["clean"] = counts["error"] == 0 and counts["warn"] == 0
    env["opdb"] = db.describe()
    return env


def _line_of(msg: str) -> str:
    if msg.startswith("line "):
        return msg.split(":", 1)[0]
    return "-"


@op("script.new", mcp=False, group="formats",
    summary="Create a starter CLEO script from a template (hello, cheat, spawn_car, teleport, text, mission): writes "
            "work/out/script/<name>/<name>.txt and the assembled <name>.cs, checked, ready to copy into the game's "
            "cleo folder (satk never writes into the game).",
    summary_ru="Создать CLEO-скрипт по шаблону (hello, cheat, spawn_car, teleport, text, mission): текст и собранный "
               ".cs в work/out/script/<имя>/, готовые для папки cleo.",
    examples=("satk script new hello --name hello", "satk script new spawn_car --name mycar --model 522 --cheat BIKE",
              "satk script new teleport --name tpbeach --pos 369.5 -2030.2 7.7"))
def script_new(template: Template, name: str | None = None, cheat: str | None = None, key: int | None = None,
               model: int | None = None, pos: list[float] | None = None, text: str | None = None,
               opdb: str = "auto") -> dict:
    """Make a script from a template.

    Args:
        template: hello (message + key press), cheat (cheat code: money, health, armour), spawn_car (cheat code
            spawns a vehicle), teleport (cheat code teleports), text (key toggles live text), mission (reach a
            marker in time).
        name: script and folder name (letters, digits, _ -); default: the template name.
        cheat: cheat code to type in the game (cheat, spawn_car, teleport, mission).
        key: Windows virtual key code (hello, text; 116 = F5).
        model: vehicle model id (spawn_car; 411 = infernus).
        pos: x y z (teleport, mission).
        text: message (hello, cheat, spawn_car, teleport, mission) or printf format (text).
        opdb: opcode database (auto, kb, cleo-ai, core or a JSON file).
    """
    from .asm import assemble
    from .check import check_program
    from .templates import render

    warn: list[str] = []
    nm = name or template
    src = render(template, nm, cheat=cheat, key=key, model=model, pos=pos, text=text, warn=warn)
    db = _db(opdb, warn)
    r = assemble(src, db, source=f"{nm}.txt")
    warn += r.warnings
    txt = work("out", "script", nm, f"{nm}.txt")
    atomic_write(txt, src)
    cs = work("out", "script", nm, f"{nm}{r.ext}")
    atomic_write(cs, r.data)
    found = check_program(r.program, r.lines)
    for sev, _off, line, code, msg in found:
        if sev != "info":
            warn.append(f"{code}: line {line}: {msg}")
    return with_warn(obj(None, file=jpath(cs), text=jpath(txt), template=template, bytes=len(r.data),
                         commands=sum(1 for _ in r.program.instrs()), opdb=db.describe(),
                         install=f"copy {cs.name} into the game's cleo folder (CLEO 4 or 5); satk never writes into "
                                 "the game",
                         edit=f"change {txt.name}, then: satk script asm {nm}/{nm}.txt"), *warn)
