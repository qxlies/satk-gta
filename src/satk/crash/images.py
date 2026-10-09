"""Module images for the stack heuristic: code bytes, executable ranges and export names.

Bytes come from the dump's captured memory first, else from the module file on disk when it is the
same build (PE ``TimeDateStamp`` and ``SizeOfImage`` equal the module record): the path recorded in
the dump, ``--images`` directories, the configured fork's Bin, and for ``gta_sa.exe`` the game copies. Files are only
read (``paths.open_ro``). Standard library only; PE32 and PE32+.
"""

from __future__ import annotations

import bisect
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .minidump import Minidump, Module, _codeview
from .mta import is_gta

__all__ = ["PeFile", "Images", "call_before"]

_SCN_CODE = 0x20
_SCN_EXEC = 0x20000000
_MAX_EXPORTS = 200_000
_PAGE = 4096


@dataclass(slots=True)
class PeFile:
    """Header facts of a PE file plus lazy reads (the file is opened per read, never kept open)."""

    path: Path
    timestamp: int
    size_of_image: int
    image_base: int
    is64: bool
    sections: list[tuple[int, int, int, int, int]]        # (rva, vsize, raw_off, raw_size, flags)
    export_dir: tuple[int, int] = (0, 0)
    debug_dir: tuple[int, int] = (0, 0)
    _cv: tuple[str | None, str | None] | None = None
    _exports: list[tuple[int, str]] | None = None
    _export_rvas: list[int] = field(default_factory=list)
    _pages: dict[int, bytes | None] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "PeFile | None":
        from ..core.paths import open_ro

        try:
            with open_ro(path) as fh:
                head = fh.read(0x1000)
                if head[:2] != b"MZ" or len(head) < 0x40:
                    return None
                (lf,) = struct.unpack_from("<I", head, 0x3C)
                if lf + 0x108 > len(head):
                    fh.seek(0)
                    head = fh.read(lf + 0x400)
                if head[lf:lf + 4] != b"PE\0\0":
                    return None
                nsec, ts = struct.unpack_from("<HI", head, lf + 6)
                (opt_size,) = struct.unpack_from("<H", head, lf + 20)
                opt = lf + 24
                (magic,) = struct.unpack_from("<H", head, opt)
                if magic == 0x10B:
                    (base,) = struct.unpack_from("<I", head, opt + 28)
                    dd = opt + 96
                elif magic == 0x20B:
                    (base,) = struct.unpack_from("<Q", head, opt + 24)
                    dd = opt + 112
                else:
                    return None
                (soi,) = struct.unpack_from("<I", head, opt + 56)
                exp = struct.unpack_from("<II", head, dd) if dd + 8 <= len(head) else (0, 0)
                debug = (0, 0)
                if dd + 7 * 8 <= min(len(head), opt + opt_size):
                    (ndirs,) = struct.unpack_from("<I", head, dd - 4)
                    if ndirs >= 7:
                        debug = struct.unpack_from("<II", head, dd + 6 * 8)
                tbl = opt + opt_size
                if nsec > 96:
                    return None
                if tbl + 40 * nsec > len(head):
                    fh.seek(0)
                    head = fh.read(tbl + 40 * nsec)
                secs = []
                for i in range(nsec):
                    vsize, va, rsize, roff = struct.unpack_from("<IIII", head, tbl + 40 * i + 8)
                    (flags,) = struct.unpack_from("<I", head, tbl + 40 * i + 36)
                    secs.append((va, vsize, roff, rsize, flags))
        except (OSError, struct.error):
            return None
        return cls(Path(path), ts, soi, base, magic == 0x20B, secs, exp, debug)

    def matches(self, mod: Module) -> bool:
        return self.timestamp == mod.timestamp and self.size_of_image == mod.size

    def codeview(self) -> tuple[str | None, str | None]:
        """PDB filename and GUID/signature + age from the image's debug directory."""
        if self._cv is None:
            self._cv = (None, None)
            rva, size = self.debug_dir
            raw = self.read(rva, size) if rva and 28 <= size <= 28 * 1024 else None
            if raw is not None:
                for off in range(0, len(raw) - 27, 28):
                    kind, count, data_rva, data_off = struct.unpack_from("<IIII", raw, off + 12)
                    if kind != 2 or not 16 <= count <= 65536:
                        continue
                    data = self._read_file(data_off, count) if data_off else self.read(data_rva, count)
                    if data:
                        cv = _codeview(data)
                        if cv[1]:
                            self._cv = cv
                            break
        return self._cv

    def section_of(self, rva: int) -> tuple[int, int, int, int, int] | None:
        for s in self.sections:
            if s[0] <= rva < s[0] + max(s[1], s[3]):
                return s
        return None

    def executable(self, rva: int) -> bool:
        s = self.section_of(rva)
        return bool(s and s[4] & (_SCN_CODE | _SCN_EXEC))

    def _off(self, rva: int) -> int | None:
        s = self.section_of(rva)
        if s is None:
            return rva if rva < 0x1000 else None
        d = rva - s[0]
        return s[2] + d if d < s[3] else None

    def read(self, rva: int, n: int) -> bytes | None:
        off = self._off(rva)
        end = self._off(rva + n - 1) if n > 0 else off
        if off is None or end is None or end != off + n - 1:
            return None
        if n > _PAGE:
            return self._read_file(off, n)
        out = b""
        cur = off
        while len(out) < n:                      # small reads (CALL checks) go through a page cache
            page = self._page(cur // _PAGE)
            if page is None:
                return None
            chunk = page[cur % _PAGE:cur % _PAGE + (n - len(out))]
            if not chunk:
                return None
            out += chunk
            cur += len(chunk)
        return out

    def _page(self, idx: int) -> bytes | None:
        if idx not in self._pages:
            if len(self._pages) >= 512:
                self._pages.clear()
            self._pages[idx] = self._read_file(idx * _PAGE, _PAGE, exact=False)
        return self._pages[idx]

    def _read_file(self, off: int, n: int, *, exact: bool = True) -> bytes | None:
        from ..core.paths import open_ro

        try:
            with open_ro(self.path) as fh:
                fh.seek(off)
                b = fh.read(n)
        except OSError:
            return None
        if exact and len(b) != n:
            return None
        return b

    def exports(self) -> list[tuple[int, str]]:
        """``[(rva, name)]`` sorted by rva (forwarders and data exports included as listed)."""
        if self._exports is not None:
            return self._exports
        out: list[tuple[int, str]] = []
        rva, size = self.export_dir
        hdr = self.read(rva, 40) if rva and size else None
        if hdr is not None:
            try:
                n_funcs, n_names, a_funcs, a_names, a_ords = struct.unpack_from("<IIIII", hdr, 20)
                if n_names <= _MAX_EXPORTS and n_funcs <= _MAX_EXPORTS:
                    funcs = self.read(a_funcs, 4 * n_funcs) or b""
                    names = self.read(a_names, 4 * n_names) or b""
                    ords = self.read(a_ords, 2 * n_names) or b""
                    blob_lo, blob = rva, self.read(rva, size) or b""
                    for i in range(min(n_names, len(names) // 4, len(ords) // 2)):
                        (nrva,) = struct.unpack_from("<I", names, 4 * i)
                        (o,) = struct.unpack_from("<H", ords, 2 * i)
                        if 4 * o + 4 > len(funcs):
                            continue
                        (frva,) = struct.unpack_from("<I", funcs, 4 * o)
                        if blob_lo <= nrva < blob_lo + len(blob):
                            raw = blob[nrva - blob_lo:nrva - blob_lo + 256]
                        else:
                            raw = self.read(nrva, 128) or b""
                        name = raw.split(b"\0", 1)[0].decode("ascii", "replace")
                        if name and not (rva <= frva < rva + size):        # skip forwarders
                            out.append((frva, name))
            except struct.error:
                out = []
        out.sort()
        self._exports = out
        self._export_rvas = [r for r, _ in out]
        return out

    def export_at(self, rva: int, max_off: int = 0x4000) -> tuple[str, int] | None:
        ex = self.exports()
        i = bisect.bisect_right(self._export_rvas, rva) - 1
        if i < 0:
            return None
        frva, name = ex[i]
        if rva - frva > max_off or not self.executable(frva):
            return None
        return name, rva - frva


class Images:
    """Resolve code bytes and names for addresses of a dump's modules."""

    def __init__(self, dump: Minidump | None, *, dirs: list[str | os.PathLike] | None = None,
                 game_exes: list[Path] | None = None):
        self.dump = dump
        self.dirs = [Path(d) for d in dirs or []]
        self.game_exes = game_exes if game_exes is not None else _game_exes()
        self._pe: dict[Module, PeFile | None] = {}
        self._named: dict[str, Module | None] = {}
        self._pdb = None
        self.warn: list[str] = []
        self.used: dict[str, str] = {}                         # module name -> file used

    def __enter__(self) -> "Images":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._pdb is not None:
            self._pdb.close()
            self._pdb = None

    def search_dirs(self) -> list[Path]:
        """Explicit image directories, then the known module folders of the configured fork."""
        roots = list(self.dirs)
        from ..core.paths import cfg

        engine = cfg().paths.get("engine")
        if engine:
            roots.append(Path(engine) / "Bin")
        out = []
        for root in roots:
            for rel in ("", "mta", "mods/deathmatch", "server", "server/x64", "server/mods/deathmatch",
                        "server/x64/mods/deathmatch"):
                p = root / rel
                if p not in out:
                    out.append(p)
        return out

    def _candidates(self, mod: Module) -> list[Path]:
        out = [Path(mod.path.replace("\\", "/"))] if mod.path else []
        out += [d / mod.name for d in self.search_dirs()]
        if is_gta(mod.name):
            out += self.game_exes
        return out

    def pe(self, mod: Module) -> PeFile | None:
        key = mod
        if key not in self._pe:
            self._pe[key] = None
            for p in self._candidates(mod):
                try:
                    if not p.is_file():
                        continue
                except OSError:
                    continue
                pe = PeFile.load(p)
                if pe is not None and pe.matches(mod):
                    image_id = pe.codeview()[1] if mod.pdb_id else None
                    if image_id and mod.pdb_id.upper() != image_id.upper():
                        self.warn.append(f"REVISION: {mod.name} image PDB id {image_id} does not match the dump's "
                                         f"{mod.pdb_id} (GUID/signature + age); image ignored")
                        continue
                    self._pe[key] = pe
                    self.used[mod.name] = p.as_posix()
                    break
        return self._pe[key]

    def named_module(self, name: str) -> Module | None:
        """Module+RVA frames in logs may have no load address; use a recorded module or a local image."""
        key = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if self.dump is not None:
            return next((m for m in self.dump.modules if m.name.lower() == key), None)
        if key not in self._named:
            self._named[key] = None
            for path in self._candidates(Module(0, 0, name)):
                pe = PeFile.load(path)
                if pe is not None:
                    mod = Module(pe.image_base, pe.size_of_image, str(path), pe.timestamp)
                    self._pe[mod] = pe
                    self._named[key] = mod
                    self.used[mod.name] = path.as_posix()
                    break
        return self._named[key]

    def pdb_candidates(self, mod: Module, pe: PeFile | None) -> list[Path]:
        names = [n for n in (pe.codeview()[0] if pe else None, mod.pdb, Path(mod.name).with_suffix(".pdb").name) if n]
        directories = ([pe.path.parent] if pe else []) + [Path(mod.path.replace("\\", "/")).parent, *self.search_dirs()]
        out = []
        for directory in directories:
            for name in names:
                path = directory / name.replace("\\", "/").rsplit("/", 1)[-1]
                if path not in out:
                    try:
                        if path.is_file():
                            out.append(path)
                    except OSError as e:
                        self.warn.append(f"EXTERNAL_TOOL: cannot access PDB {path.as_posix()}: {e}")
        return out

    def pdb_symbol(self, mod: Module, off: int, *, ret: bool = False) -> dict:
        from .pdb import PdbSymbols

        if self._pdb is None:
            self._pdb = PdbSymbols(self)
        return self._pdb.resolve(mod, off, ret=ret)

    def module_at(self, va: int) -> Module | None:
        return self.dump.module_at(va) if self.dump is not None else None

    def code(self, va: int, n: int) -> bytes | None:
        """``n`` bytes at ``va``: captured memory first, then the matching module file."""
        if self.dump is not None:
            b = self.dump.read(va, n)
            if b is not None:
                return b
        mod = self.module_at(va)
        if mod is None:
            return None
        pe = self.pe(mod)
        return pe.read(va - mod.base, n) if pe is not None else None

    def executable(self, va: int) -> bool | None:
        """True/False when the module's section table is known (file or captured header), else ``None``."""
        mod = self.module_at(va)
        if mod is None:
            return None
        pe = self.pe(mod)
        if pe is not None:
            return pe.executable(va - mod.base)
        secs = self._mem_sections(mod)
        if secs is None:
            return None
        rva = va - mod.base
        return any(s[0] <= rva < s[0] + max(s[1], s[3]) and s[4] & (_SCN_CODE | _SCN_EXEC) for s in secs)

    def _mem_sections(self, mod: Module) -> list[tuple] | None:
        """Section table from a PE header captured in the dump (full dumps)."""
        if self.dump is None:
            return None
        head = self.dump.read(mod.base, 0x400)
        if head is None or head[:2] != b"MZ":
            return None
        try:
            (lf,) = struct.unpack_from("<I", head, 0x3C)
            nsec, = struct.unpack_from("<H", head, lf + 6)
            (opt_size,) = struct.unpack_from("<H", head, lf + 20)
            tbl = lf + 24 + opt_size
            raw = self.dump.read(mod.base + tbl, 40 * nsec) if nsec <= 96 else None
            if raw is None:
                return None
            out = []
            for i in range(nsec):
                vsize, va, rsize, roff = struct.unpack_from("<IIII", raw, 40 * i + 8)
                (flags,) = struct.unpack_from("<I", raw, 40 * i + 36)
                out.append((va, vsize, roff, rsize, flags))
            return out
        except struct.error:
            return None

    def export_name(self, va: int) -> tuple[str, int] | None:
        mod = self.module_at(va)
        if mod is None or is_gta(mod.name):
            return None
        pe = self.pe(mod)
        return pe.export_at(va - mod.base) if pe is not None else None


def _game_exes() -> list[Path]:
    """``gta_sa.exe`` of the configured game copies (clean copy, the game root, the original install)."""
    try:
        from ..core.paths import cfg

        c = cfg().paths
        roots = [c.get("game"), c.get("game_root"), c.get("installed")]
    except Exception:  # noqa: BLE001 - analysis works without a configuration
        return []
    out: list[Path] = []
    for r in roots:
        if r is None:
            continue
        for n in ("gta_sa.exe", "gta-sa.exe"):
            p = Path(r) / n
            if p not in out:
                out.append(p)
    return out


def call_before(code: bytes, ptr_size: int = 4) -> tuple[int, int | None] | None:
    """Is there a CALL that ends right at the end of ``code`` (the 8 bytes before a return address)?

    Returns ``(length, direct target displacement or None)`` or ``None``. ``code`` must hold at least
    the 7 bytes preceding the return address. Recognised: ``E8 rel32``, ``FF /2`` with register,
    ``[reg]``, ``[reg+disp8/32]``, SIB and ``[disp32]``/``[rip+disp32]`` forms, and REX-prefixed
    ``call r/m`` on x64.
    """
    n = len(code)
    if n >= 5 and code[n - 5] == 0xE8:
        (rel,) = struct.unpack_from("<i", code, n - 4)
        return 5, rel
    if ptr_size == 8 and n >= 3 and 0x40 <= code[n - 3] <= 0x4F and code[n - 2] == 0xFF \
            and (code[n - 1] & 0xF8) in (0xD0, 0x10):
        return 3, None                                             # call r8-r15 / [r8-r15]
    if n >= 2 and code[n - 2] == 0xFF:
        modrm = code[n - 1]
        if (modrm & 0x38) == 0x10 and ((modrm & 0xC0) == 0xC0 or ((modrm & 0xC0) == 0 and modrm & 7 not in (4, 5))):
            return 2, None                                         # call reg / call [reg]
    if n >= 3 and code[n - 3] == 0xFF:
        modrm = code[n - 2]
        if (modrm & 0x38) == 0x10 and (((modrm & 0xC0) == 0x40 and modrm & 7 != 4) or modrm == 0x14):
            return 3, None                                         # call [reg+d8] / call [sib]
    if n >= 4 and code[n - 4] == 0xFF and (code[n - 3] & 0xF8) == 0x50 and code[n - 3] & 0x38 == 0x10 \
            and code[n - 3] & 7 == 4:
        return 4, None                                             # call [sib+d8]
    if n >= 6 and code[n - 6] == 0xFF:
        modrm = code[n - 5]
        if modrm == 0x15 or ((modrm & 0xC0) == 0x80 and (modrm & 0x38) == 0x10 and modrm & 7 != 4):
            return 6, None                                         # call [disp32] / call [reg+d32]
    if n >= 7 and code[n - 7] == 0xFF and code[n - 6] == 0x94:
        return 7, None                                             # call [sib+d32]
    return None
