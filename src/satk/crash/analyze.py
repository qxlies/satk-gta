"""``satk crash analyze``: one short report for a minidump, an MTA crash log or a single-player log.

``gta_sa.exe`` addresses are symbolized through the sa-re symbol DB (:mod:`satk.re.api`: function +
offset, gta-reversed ``file:line``, MTA patch sites); other modules use matching local PDBs, falling
back to the nearest export when the module file of the same build is on disk. Without a symbol DB the report
still works, with a ``NOT_READY`` warning. Single-player crash reports in text logs (modloader.log,
SA-MP, mod_sa, Visual Studio/WER text) are read by :mod:`.sptext`. :mod:`.advise` adds the known
CrashInfo entry with its solution, suspects and the culprit mod. The human rendering stays within 30
lines.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from ..core.envelope import table
from ..core.errors import SatkError
from ..core.paths import jpath
from .images import Images
from .minidump import DumpError, Minidump, Module
from .mta import (_BLOCK, GTA_BASE, GTA_END, USER_STREAMS, LogBlock, decode_section, exception_name, is_gta,
                  parse_log, trailing_sections)
from .stack import Frame, walk

__all__ = ["Symbols", "analyze_file", "load", "FRAME_COLS", "hx", "MAX_WARN", "MAX_LINES"]

FRAME_COLS = ["#", "addr", "at", "fn", "src", "mta", "via"]
MAX_WARN = 4
MAX_LINES = 30
_TEXT_LIMIT = 64 * 1024 * 1024
#: keys dropped first when a report would exceed :data:`MAX_LINES` (frames are cut after them)
_DROP_ORDER = ("mta_stack", "dump_file", "dump", "file", "mta", "logs", "patched", "src")


def hx(v: int | None, width: int = 0) -> str | None:
    if v is None:
        return None
    return f"0x{v:0{width}x}" if width else f"0x{v:x}"


class Symbols:
    """Names for addresses: sa-re for ``gta_sa.exe``, PDBs/exports for other modules."""

    def __init__(self, images: Images):
        self.images = images
        self.db = None
        self.warn = images.warn
        try:
            from ..re.db import open_db

            self.db = open_db(required=False)
            if self.db is None:
                self.warn.append("NOT_READY: symbol DB not built, gta_sa.exe frames stay module+offset (satk re build)")
        except SatkError as e:
            self.warn.append(f"NOT_READY: symbol DB unusable ({e.msg}), gta_sa.exe frames stay module+offset")

    def is_code(self, va: int) -> bool | None:
        """Section kind of a ``gta_sa.exe`` address from the symbol DB (``None`` for other modules)."""
        mod = self.images.module_at(va)
        if self.db is None or (mod is not None and not is_gta(mod.name)):
            return None
        try:
            kind = self.db.amap.locate(GTA_BASE + (va - mod.base) if mod else va).kind
        except Exception:  # noqa: BLE001 - a partial DB must not break the walk
            return None
        return {"code": True, "data": False}.get(kind)

    def gta(self, va: int, *, ret: bool) -> dict:
        """``{fn, src, conf, n_patches}`` for a gta_sa.exe VA (return addresses resolve ``va - 1``)."""
        if self.db is None:
            return {}
        from ..re.api import addr_detail

        try:
            d = addr_detail(self.db, va - 1 if ret else va, patch_limit=0)
        except Exception:  # noqa: BLE001 - never fail a report on one address
            return {}
        out: dict = {"conf": d.get("confidence")}
        if d.get("fn"):
            off = int(d["off"], 16) + (1 if ret else 0) if d.get("off") else (1 if ret else 0)
            out["fn"] = f"{d['fn']}+0x{off:x}" if off else d["fn"]
            out["name"] = d["fn"]
        elif d.get("g") or d.get("vtable"):
            out["fn"] = d.get("g") or f"{d['vtable']}::vftable"
        out["src"] = d.get("src") or d.get("sdk")
        out["n_patches"] = d.get("n_patches")
        return out

    def patches_at(self, lo: int, hi: int) -> list[str]:
        """MTA patch sites covering ``[lo, hi)`` of gta_sa.exe: ``SYMBOL (origin, file:line)``."""
        if self.db is None:
            return []
        try:
            rows = self.db.con.execute(
                "SELECT symbol, origin, kind, src_file, src_line FROM patch WHERE addr < ? AND "
                "addr + max(coalesce(len, 1), 1) > ? ORDER BY CASE origin WHEN 'satk' THEN 0 WHEN 'trunk' THEN 1 "
                "WHEN 'neon' THEN 2 ELSE 3 END, kind, src_file, src_line", (hi, lo)).fetchall()
        except Exception:  # noqa: BLE001
            return []
        out, seen = [], set()
        for r in rows:
            sym = r["symbol"] or r["kind"]
            if sym in seen:
                continue
            seen.add(sym)
            out.append(f"{sym} ({r['origin']}, {r['src_file']}:{r['src_line']})")
        return out

    def describe(self, va: int | None, module: str | None = None, off: int | None = None, *,
                 ret: bool = False, call_len: int = 5) -> dict:
        """``{addr, at, fn, src, mta, name, n_patches}`` for one frame."""
        mod: Module | None = None
        if va is not None and module is None:
            mod = self.images.module_at(va)
            if mod is not None:
                module, off = mod.name, va - mod.base
            elif GTA_BASE <= va < GTA_END and self.images.dump is None:
                module, off = "gta_sa.exe", va - GTA_BASE
        out: dict = {"addr": hx(va)}
        if module is not None and off is not None:
            out["at"] = f"{module}+0x{off:x}"
        elif module is not None:
            out["at"] = module
        elif va is not None:
            out["at"] = hx(va)
        if is_gta(module) and off is not None:
            g = GTA_BASE + off
            out.update(self.gta(g, ret=ret))
            pl = self.patches_at(g - call_len, g) if ret else self.patches_at(g, g + 1)
            if pl:
                out["mta"] = pl[0].split(" (", 1)[0]
                out["patches"] = pl
        elif module is not None and off is not None:
            mod = mod or self.images.named_module(module)
            if mod is not None:
                out.update(self.images.pdb_symbol(mod, off, ret=ret))
            if out.get("fn"):
                return out
            pe = self.images.pe(mod) if mod is not None else None
            e = pe.export_at(off) if pe is not None else None
            if e is not None:
                name, eoff = e
                out["fn"] = f"{name}+0x{eoff:x}" if eoff else name
                out["src"] = "export"
        return out


# --------------------------------------------------------------------------- loading


def load(path: str) -> tuple[str, object]:
    """``("dump", Minidump)`` or ``("text", str)``; ``SatkError`` for missing/unreadable/unknown files."""
    p = Path(path)
    if not p.exists():
        raise SatkError("NOT_FOUND", f"no such file: {jpath(p)}", hint="satk crash list")
    if p.is_dir():
        raise SatkError("BAD_PARAMS", f"{jpath(p)} is a directory", hint="satk crash list --dir DIR")
    from ..core.paths import open_ro

    try:
        with open_ro(p) as fh:
            head = fh.read(8)
    except OSError as e:
        raise SatkError("NOT_FOUND", f"cannot read {jpath(p)}: {e}") from None
    if not head:
        raise SatkError("UNSUPPORTED", f"empty file: {jpath(p)}")
    if head[:4] == b"MDMP":
        try:
            return "dump", Minidump.open(p)
        except DumpError as e:
            raise SatkError("UNSUPPORTED", f"cannot parse minidump {p.name}: {e}",
                            hint="the dump is damaged or truncated; try the matching core.log",
                            data={"offset": e.offset}) from None
        except (OSError, ValueError) as e:
            raise SatkError("UNSUPPORTED", f"cannot read minidump {p.name}: {e}") from None
    size = p.stat().st_size
    with open_ro(p) as fh:
        if size > _TEXT_LIMIT:
            fh.seek(size - _TEXT_LIMIT)
        raw = fh.read(_TEXT_LIMIT)
    text = _decode(raw)
    if text is None:
        raise SatkError("UNSUPPORTED", f"{p.name} is neither a minidump nor a text crash log",
                        data={"magic": head[:4].hex()})
    return "text", text


def _decode(raw: bytes) -> str | None:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", "replace")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", "replace")
    sample = text[:65536]
    if not sample:
        return None
    ok = sum(1 for ch in sample if ch.isprintable() or ch in "\r\n\t")
    return text if ok >= 0.9 * len(sample) else None


# --------------------------------------------------------------------------- reports


def _regs_line(regs: dict[str, int], arch: str) -> str | None:
    if not regs:
        return None
    names = ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp") if arch != "amd64" else \
        ("rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp", "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15")
    w = 16 if arch == "amd64" else 8
    parts = [f"{n}={regs[n]:0{w}x}" for n in names if n in regs]
    return " ".join(parts) or None


def _access(code: int, params: list[int]) -> str:
    if code in (0xC0000005, 0xC0000006) and len(params) >= 2:
        kind = {0: "reading", 1: "writing", 8: "executing"}.get(params[0], f"access {params[0]}")
        return f" {kind} 0x{params[1]:08x}"
    return ""


def _crash_line(code: int | None, at: str | None, extra: str = "", tid: int | None = None) -> str:
    name = exception_name(code) or ("exception" if code is not None else "crash")
    s = (f"0x{code:08X} " if code is not None else "") + name + extra
    if at:
        s += f" at {at}"
    if tid:
        s += f" (thread {tid})"
    return s


def _row(i: int, d: dict, via: str) -> list:
    if d.get("via") == "pdb":
        via += "/pdb"
    return [i, d.get("addr"), d.get("at"), d.get("fn"), d.get("src"), d.get("mta"), via]


def _cap_warn(warns: list[str]) -> list[str]:
    seen, out = set(), []
    for w in warns:
        if w not in seen:
            seen.add(w)
            out.append(w)
    if len(out) > MAX_WARN:
        out = out[:MAX_WARN - 1] + [f"MORE: {len(out) - MAX_WARN + 1} more warnings (satk crash info FILE)"]
    return out


def _hint(fn_name: str | None, path: str) -> str:
    parts = []
    if fn_name and not fn_name.startswith("sub_"):
        parts += [f"satk re src {fn_name}", f"satk re patches --fn {fn_name}"]
    parts.append(f'satk crash info "{path}" --part stack')
    return " | ".join(parts)


def _lines(env: dict) -> int:
    n = len(env.get("rows", [])) + 3
    n += sum(1 for k, v in env.items() if k not in ("ok", "cols", "rows", "n", "total", "next", "warn")
             and v not in (None, "", [], {}))
    return n + len(env.get("warn") or [])


def _fit(env: dict, limit: int) -> dict:
    """Keep the human rendering within :data:`MAX_LINES` (more when ``--limit`` asks for more frames)."""
    budget = MAX_LINES + max(0, limit - 10)
    for k in _DROP_ORDER:
        if _lines(env) <= budget:
            return env
        env.pop(k, None)
    over = _lines(env) - budget
    if over > 0 and len(env.get("rows", [])) > 4:
        env["rows"] = env["rows"][:max(4, len(env["rows"]) - over)]
    return env


def _advise(env: dict, facts, warns: list[str]) -> None:
    from .advise import advise

    try:
        warns += advise(env, facts)
    except SatkError as e:
        warns.append(f"{e.code}: no advice ({e.msg})")


def _game_arg(game: str | None) -> Path | None:
    if not game:
        return None
    g = Path(game)
    if g.is_file():
        g = g.parent
    if not g.is_dir():
        raise SatkError("NOT_FOUND", f"no such game folder: {jpath(g)}",
                        hint="--game <the GTA San Andreas folder that crashed (with gta_sa.exe / modloader)>")
    return g


def _mta_version(texts: list[str]) -> str | None:
    for t in texts:
        m = re.search(r"^\s*Version\s*=\s*(\S+)", t, re.M)
        if m:
            return m.group(1)
    return None


def analyze_dump(dump: Minidump, path: str, *, limit: int, images: Images, thread: int | None = None,
                 scan_bytes: int = 0x10000, game: Path | None = None, profile: str | None = None) -> dict:
    sym = Symbols(images)
    warns = list(dump.warnings)
    if not (dump.modules or dump.threads or dump.exception):
        raise SatkError("UNSUPPORTED", f"minidump {Path(path).name} has no readable module, thread or exception stream",
                        data={"warnings": dump.warnings[:10]})
    exc = dump.exception
    t = None
    if thread is not None:
        t = dump.thread(thread)
        if t is None:
            raise SatkError("NOT_FOUND", f"no thread {thread} in the dump",
                            did_you_mean=[str(x.tid) for x in dump.threads[:8]])
    elif exc is not None:
        t = dump.thread(exc.tid)
    if t is None and thread is None and dump.threads:
        t = dump.threads[0]
        if exc is None:
            warns.append("NO_EXCEPTION: the dump has no exception stream (a hang/manual dump?): showing thread "
                         f"{t.tid}")
    ctx = exc.context if exc is not None and (thread is None or thread == exc.tid) and exc.context else \
        (t.context if t else None)
    frames: list[Frame] = []
    if ctx is not None:
        if ctx.arch not in ("x86", "amd64"):
            warns.append(f"UNSUPPORTED_ARCH: {ctx.arch} stacks are not walked")
            frames = [Frame(ctx.ip, "ip")]
        else:
            frames, _st = walk(dump, ctx, t, images, scan_bytes=scan_bytes, is_code=sym.is_code)
    elif exc is not None:
        frames = [Frame(exc.address, "ip")]
        warns.append("NO_CONTEXT: no thread context: only the exception address is known")
    rows = []
    descr = []
    for i, f in enumerate(frames):
        d = sym.describe(f.va, ret=f.how != "ip", call_len=5)
        descr.append(d)
        if i < limit:
            rows.append(_row(i, d, f.how + ("?" if f.how != "ip" and f.call is None else "")))
    # MTA's own stack trace (user stream 0x10001)
    mta_text = {name: dump.user_streams[k].split(b"\0", 1)[0].decode("utf-8", "replace")
                for k, name in USER_STREAMS.items() if k in dump.user_streams}
    mta_frames = parse_log(mta_text["stack"])[0].frames if "stack" in mta_text else []
    if len(frames) < 2 and mta_frames:
        rows = []
        for i, fr in enumerate(mta_frames[:limit]):
            d = sym.describe(fr.va, fr.module if fr.va is None else None, fr.off, ret=i > 0)
            rows.append(_row(i, d, "mta"))
    # the crash site
    site = descr[0] if descr else {}
    if exc is not None and (not frames or frames[0].va != exc.address):
        site = sym.describe(exc.address)
    code = exc.code if exc is not None else None
    env = table(FRAME_COLS, rows, total=max(len(frames), len(rows)))
    env["crash"] = _crash_line(code, site.get("at"), _access(code, exc.params) if exc else "",
                               exc.tid if exc else (t.tid if t else None))
    if site.get("fn"):
        env["fn"] = site["fn"]
    if site.get("src"):
        env["src"] = site["src"]
    if site.get("patches"):
        env["patched"] = site["patches"][:3]
    elif site.get("n_patches"):
        env["patched"] = f"no patch at the address; {site['n_patches']} MTA patch sites in this function"
    if ctx is not None and _regs_line(ctx.regs, ctx.arch):
        env["regs"] = _regs_line(ctx.regs, ctx.arch)
    ts = _dt.datetime.fromtimestamp(dump.timestamp, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") \
        if dump.timestamp else None
    os_s = f", Windows {dump.sysinfo.os}" if dump.sysinfo else ""
    env["dump"] = f"minidump {dump.arch}{os_s}, {len(dump.modules)} modules, {len(dump.threads)} threads" + \
        (f", {ts}" if ts else "")
    sections, _start = trailing_sections(dump.data)
    ver = _mta_version(dump.comments + list(mta_text.values()))
    if sections or mta_text or ver:
        tags = " ".join(s.tag for s in sections)
        env["mta"] = "; ".join(x for x in (f"MTA {ver}" if ver else None,
                                           f"streams {' '.join(mta_text)}" if mta_text else None,
                                           f"sections {tags}" if tags else None) if x)
    full = _full_pools(sections)
    if full:
        env["pools_full"] = full
    shown = {r[1] for r in rows}
    if mta_frames and rows and rows[0][6].split("/")[0] != "mta" and any(hx(fr.va) not in shown for fr in mta_frames[:6]):
        env["mta_stack"] = [_compact(sym.describe(fr.va, fr.module if fr.va is None else None, fr.off, ret=i > 0))
                            for i, fr in enumerate(mta_frames[:6])]   # MTA's own walk differs from ours
    env["file"] = jpath(path)
    env["hint"] = _hint(site.get("name"), jpath(path))
    warns += sym.warn
    unverified: dict[str, int] = {}
    for f, d in zip(frames, descr):
        if f.how != "ip" and f.call is None:
            m = (d.get("at") or "?").split("+", 1)[0]
            unverified[m] = unverified.get(m, 0) + 1
    if unverified:
        mods = ", ".join(f"{m} {n}" for m, n in sorted(unverified.items(), key=lambda kv: -kv[1])[:3])
        warns.append(f"UNVERIFIED: frames marked '?' were not checked for a CALL ({mods}): no code bytes in the "
                     "dump and no module file of this build (--images DIR)")
    if exc is None and not any(w.startswith("NO_EXCEPTION") for w in warns):
        warns.append("NO_EXCEPTION: the dump has no exception stream")
    from .advise import Facts
    from .culprit import game_dir_of

    is_mta = bool(sections or mta_text or ver) or any(m.name.lower() in ("core.dll", "client.dll")
                                                       for m in dump.modules)
    if not is_mta and isinstance(env.get("patched"), str):
        env.pop("patched")                     # "N MTA patch sites in this function" is noise for a single-player crash
    if game is None and not is_mta:
        game = game_dir_of(Path(path))
        exe = next((m for m in dump.modules if is_gta(m.name)), None)
        if game is None and exe is not None:
            d = Path(exe.path.replace("\\", "/")).parent
            try:
                game = d if (d / "modloader").is_dir() else None
            except OSError:
                game = None
    ip = exc.address if exc is not None else (frames[0].va if frames else None)
    at = site.get("at") or ""
    _advise(env, Facts(ip=ip, module=at.split("+", 1)[0] if "+" in at else None,
                       stack=[f.va for f in frames[1:]] + [fr.va for fr in mta_frames[1:] if fr.va is not None],
                       regs=dict(ctx.regs) if ctx is not None else {}, game=game, profile=profile), warns)
    w = _cap_warn(warns)
    if w:
        env["warn"] = w
    return _fit(_order(env), limit)


def _compact(d: dict) -> str:
    return " ".join(x for x in (d.get("at"), d.get("fn")) if x)


def _full_pools(sections) -> list[str]:
    out = []
    for s in sections:
        if s.tag != "POL":
            continue
        dec = decode_section(s)
        for name, cap, used in dec.get("rows", []):
            if cap and used is not None and used >= 0.95 * cap:
                out.append(f"{name} {used}/{cap}")
    return out


def _pick(n: int, block: int, what: str) -> int:
    """1-based index of ``block`` (``-1`` newest, ``1`` oldest) among ``n``."""
    idx = block if block > 0 else n + block + 1
    if not 1 <= idx <= n:
        raise SatkError("BAD_PARAMS", f"block {block} out of range: the file has {n} {what}",
                        hint="--block 1 is the first, --block -1 the newest")
    return idx


def analyze_sp(crashes: list, path: str, *, limit: int, block: int = 0, game: Path | None = None,
               profile: str | None = None, images: Images | None = None) -> dict:
    """Report for a single-player crash report found by :func:`satk.crash.sptext.parse`."""
    if images is None:
        with Images(None) as module_images:
            return analyze_sp(crashes, path, limit=limit, block=block, game=game, profile=profile, images=module_images)
    from .advise import Facts
    from .culprit import game_dir_of

    n = len(crashes)
    idx = 1 if block == 0 else _pick(n, block, "crash report(s)")
    c = crashes[idx - 1]
    sym = Symbols(images)
    warns: list[str] = []
    module, off = c.module, c.off
    site = sym.describe(c.addr, module if off is not None else None, off)
    rows = []
    frames = list(c.frames)
    if frames and frames[0].va != c.addr and not (frames[0].module == module and frames[0].off == off):
        frames.insert(0, None)                     # the crash site is not the first backtrace line
    for i, fr in enumerate(frames[:limit]):
        if fr is None:
            rows.append(_row(i, site, "ip"))
            continue
        d = sym.describe(fr.va if fr.module is None else None, fr.module, fr.off,
                         ret=i > 0 and fr.va != c.addr)
        if fr.va is not None and d.get("addr") is None:
            d["addr"] = hx(fr.va)
        rows.append(_row(i, d, "ip" if i == 0 else "log"))
    if not rows:
        rows.append(_row(0, site, "ip"))
    env = table(FRAME_COLS, rows, total=max(len(frames), len(rows)))
    env["crash"] = _crash_line(c.code, site.get("at"), f" {c.access}" if c.access else "")
    if site.get("fn"):
        env["fn"] = site["fn"]
    if site.get("src"):
        env["src"] = site["src"]
    if site.get("patches"):
        env["patched"] = site["patches"][:3]
    regs = _regs_line(c.regs, "x86")
    if regs:
        env["regs"] = regs
    env["log"] = f"crash report {idx} of {n} ({c.kind})"
    env["file"] = jpath(path)
    name = site.get("name")
    if name and not name.startswith("sub_"):
        env["hint"] = f"satk re src {name} | satk crash known 0x{c.addr:08X}"
    if n > 1 and block == 0:
        warns.append(f"MORE: {n} crash reports in the file; the first one is usually the real crash, the next "
                     "ones follow it (--block N picks another)")
    if game is None:
        game = game_dir_of(Path(path))
    stack = [fr.va for fr in frames[1:] if fr is not None and fr.va is not None]
    _advise(env, Facts(ip=c.addr, module=module, stack=stack, regs=dict(c.regs), game=game, profile=profile), warns)
    warns += sym.warn
    w = _cap_warn(warns)
    if w:
        env["warn"] = w
    return _fit(_order(env), limit)


def analyze_text(text: str, path: str, *, limit: int, block: int = 0, game: Path | None = None,
                 profile: str | None = None, images: Images | None = None) -> dict:
    if images is None:
        with Images(None) as module_images:
            return analyze_text(text, path, limit=limit, block=block, game=game, profile=profile, images=module_images)
    if not _BLOCK.search(text):
        from .sptext import parse as sp_parse

        crashes = sp_parse(text)
        if crashes:
            return analyze_sp(crashes, path, limit=limit, block=block, game=game, profile=profile, images=images)
    blocks = parse_log(text)
    n = len(blocks)
    idx = _pick(n, block or -1, "crash block(s)")
    b: LogBlock = blocks[idx - 1]
    sym = Symbols(images)
    warns: list[str] = []
    module, off = b.module, b.offset
    va = None
    if module is None and b.reason:
        from .mta import parse_frame_line

        fr = parse_frame_line(b.reason.split(" at ", 1)[-1])
        if fr is not None:
            module, off, va = fr.module, fr.off, fr.va
    if module is not None and is_gta(module):
        module = "gta_sa.exe"
    if module is None and b.regs.get("eip") is not None and GTA_BASE <= b.regs["eip"] < GTA_END:
        module, off = "gta_sa.exe", b.regs["eip"] - GTA_BASE
    if module == "gta_sa.exe" and off is not None:
        va = GTA_BASE + off
    site = sym.describe(va, module, off)
    rows = []
    frames = b.frames
    via = "log"
    if not frames:
        from ..re.crash import parse_text

        items = parse_text(b.text)
        from .mta import LogFrame

        frames = [LogFrame(it.raw, it.module if it.module != "?" else None, it.off, it.addr) for it in items]
        via = "text"
        if not frames and va is None and module is None:
            raise SatkError("BAD_PARAMS", f"no crash address found in {Path(path).name}",
                            hint="give a minidump, MTA core.log or a text with gta_sa.exe+0x... addresses")
    for i, fr in enumerate(frames[:limit]):
        d = sym.describe(fr.va if fr.module is None else None, fr.module, fr.off, ret=i > 0 and via == "log")
        if fr.va is not None and d.get("addr") is None:
            d["addr"] = hx(fr.va)
        if fr.module is not None and fr.off is None and d.get("at") == fr.module:
            d["at"] = fr.raw.split()[-1] if fr.raw else fr.module
        if not d.get("fn") and fr.symbol:
            d["fn"] = fr.symbol
        rows.append(_row(i, d, via))
    env = table(FRAME_COLS, rows, total=len(frames))
    desc = b.fields.get("Exception") or b.fields.get("Description")
    tid = None
    for k in ("ThreadId", "Thread ID"):
        if b.fields.get(k, "").strip().isdigit():
            tid = int(b.fields[k].strip())
            break
    env["crash"] = _crash_line(b.code, site.get("at"), "", tid)
    if desc:
        env["what"] = desc
    if site.get("fn"):
        env["fn"] = site["fn"]
    if site.get("src"):
        env["src"] = site["src"]
    if site.get("patches"):
        env["patched"] = site["patches"][:3]
    elif site.get("n_patches"):
        env["patched"] = f"no patch at the address; {site['n_patches']} MTA patch sites in this function"
    regs = _regs_line(b.regs, "amd64" if "rip" in b.regs else "x86")
    if regs:
        env["regs"] = regs
    env["log"] = f"block {idx} of {n}" + (f", MTA {b.fields['Version']}" if b.fields.get("Version") else "") + \
        (f", {b.fields['Time'].strip()}" if b.fields.get("Time") else "")
    dump_file = b.fields.get("Crash dump file")
    env["file"] = jpath(path)
    if dump_file:
        env["dump_file"] = dump_file
    hints = []
    name = site.get("name")
    if name and not name.startswith("sub_"):
        hints += [f"satk re src {name}", f"satk re patches --fn {name}"]
    if dump_file:
        hints.append(f'satk crash analyze "{dump_file}"')
    if hints:
        env["hint"] = " | ".join(hints)
    from .advise import Facts

    ip = va if va is not None else (frames[0].va if frames else None)
    stack = [fr.va for fr in frames[1:] if fr.va is not None]
    _advise(env, Facts(ip=ip, module=module, stack=stack, regs=dict(b.regs), game=game, profile=profile), warns)
    warns += sym.warn
    w = _cap_warn(warns)
    if w:
        env["warn"] = w
    return _fit(_order(env), limit)


#: key order of a report: what happened, what to do, who, then details
_ORDER = ("cols", "rows", "n", "total", "next", "crash", "what", "fn", "src", "patched", "known", "solution",
          "culprit", "suspects", "regs", "logs", "log", "dump", "mta", "pools_full", "mta_stack", "file", "dump_file",
          "hint", "warn")


def _order(env: dict) -> dict:
    out = {"ok": env.get("ok", True)}
    for k in _ORDER:
        if k in env:
            out[k] = env[k]
    for k, v in env.items():
        if k not in out:
            out[k] = v
    return out


def analyze_file(path: str, *, limit: int = 10, images: list[str] | None = None, thread: int | None = None,
                 block: int = 0, scan_kb: int = 64, game: str | None = None, profile: str | None = None) -> dict:
    """The envelope of ``satk crash analyze`` (``block`` 0 = newest MTA block / first single-player report)."""
    g = _game_arg(game)
    kind, obj_ = load(path)
    if kind == "dump":
        dump: Minidump = obj_  # type: ignore[assignment]
        try:
            with Images(dump, dirs=images) as module_images:
                return analyze_dump(dump, path, limit=limit, images=module_images, thread=thread,
                                    scan_bytes=scan_kb * 1024, game=g, profile=profile)
        finally:
            dump.close()
    with Images(None, dirs=images) as module_images:
        return analyze_text(obj_, path, limit=limit, block=block, game=g, profile=profile,
                            images=module_images)  # type: ignore[arg-type]
