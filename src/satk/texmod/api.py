"""Texture modding: TXD -> PNGs, PNGs -> TXD, replace textures in a TXD (owner M2-03).

* :func:`extract` -- every texture of a TXD as ``work/out/texmod/<txd>/<name>.png`` plus ``texmod.json``
  (original names, formats, mip counts); edited PNGs are not overwritten without ``force``;
* :func:`pack` -- a folder of images -> ``work/out/mods/<name>/<file>.txd``;
* :func:`replace` -- a copy of a TXD with some textures swapped (or added) ->
  ``work/out/mods/<name>/<txd file>.txd``; untouched textures are copied byte for byte.

The mod folder follows the modloader layout (``modloader/<name>/<file>.txd`` replaces the game file of the
same name) and gets a ``README.txt`` with one section per TXD: the base TXD, every texture written and the
image (path + sha256) it came from. Every written TXD is parsed back with :mod:`satk.formats.txd` and each
new texture decoded with :mod:`satk.formats.dxt`; the result rows carry the PSNR of mip 0 against the
source image (100 = lossless).

Inputs: a TXD is ``txd:<name>`` (index), ``file:<relpath>``, an absolute path, ``<img>/<entry>`` or a path
relative to the profile root (``models/gta3.img/bistro.txd``). Image files and folders are absolute,
relative to the current folder, or relative to ``work/out/texmod`` (where :func:`extract` writes).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath, open_ro, profile_root, work
from ..formats.rw import FormatError, rw_payload_size
from ..formats.txd import TexInfo, Txd, mip0_bytes, palette_bytes, parse_txd
from .txdwrite import FILTER_TRILINEAR, ADDR_WRAP, MAX_NAME, NativeSpec, mip_filter, native_chunk, rewrite_txd, \
    txd_chunk

__all__ = ["FORMAT_CHOICES", "IMAGE_EXTS", "MANIFEST", "README", "CLASS_PRESETS", "TxdSource", "Built", "load_txd",
           "find_input", "load_image", "prepare", "build_texture", "extract", "pack", "replace", "texmod_root",
           "mod_dir", "class_format"]

#: ``--format`` values (``auto`` picks per image, see :func:`satk.texmod.encode.auto_format`).
FORMAT_CHOICES = ("auto", "dxt1", "dxt3", "dxt5", "a8r8g8b8", "x8r8g8b8", "r5g6b5", "a1r5g5b5", "a4r4g4b4")
#: Image files :func:`pack` picks up (all but ``.png`` need Pillow).
IMAGE_EXTS = (".png", ".bmp", ".tga", ".jpg", ".jpeg", ".dds", ".webp", ".tif", ".tiff", ".gif")
MANIFEST = "texmod.json"
README = "README.txt"
#: Largest side :func:`prepare` lets through without a ``BIG`` warning (D3D9 cards of the SA era).
BIG_SIDE = 2048
#: ``texture pack --asset-class``: formats and mip levels as in the vanilla game (index of the 1.0 US game,
#: 2026-10-05). ``opaque``/``binary``/``smooth`` = format by the image's alpha; ``mips_from`` = smallest side
#: that gets a full mip chain (0 = never); ``max_side`` = the largest vanilla texture of the class.
CLASS_PRESETS: dict[str, dict] = {
    # 593 vehicle textures: DXT1 467 + DXT1 1-bit 20 + DXT3 86, none with mipmaps, at most 256 px
    "vehicle": {"opaque": "dxt1", "binary": "dxt1", "smooth": "dxt3", "mips_from": 0, "max_side": 256},
    # 289 ped textures: X8R8G8B8 253 + A8R8G8B8 20 (+16 DXT), none with mipmaps, at most 256 px
    "ped": {"opaque": "x8r8g8b8", "binary": "a8r8g8b8", "smooth": "a8r8g8b8", "mips_from": 0, "max_side": 256},
    # 114 weapon textures: DXT1 51 + DXT3 63, no mipmaps, at most 128 px
    "weapon": {"opaque": "dxt1", "binary": "dxt1", "smooth": "dxt3", "mips_from": 0, "max_side": 128},
    # map textures: DXT1/DXT3; mipmaps on 57 % of the 256 px and 77 % of the 512 px ones, 3-7 % below
    "map": {"opaque": "dxt1", "binary": "dxt1", "smooth": "dxt3", "mips_from": 256, "max_side": 512},
    # LOD textures: DXT1 5,007 of 5,374 with one level, at most 512 px
    "lod": {"opaque": "dxt1", "binary": "dxt1", "smooth": "dxt3", "mips_from": 0, "max_side": 512},
}
_MOD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_FILE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\.txd$", re.I)
_SECTION = re.compile(r"^== (.+) ==$")


# --------------------------------------------------------------------------- inputs


@dataclass
class TxdSource:
    """A TXD read from the game, a file or an IMG entry (``data`` = the TXD chunk only)."""

    label: str          # SID or path, as shown to the user
    data: bytes
    file: str           # file name with extension (``bistro.txd``)
    txd: Txd

    @property
    def stem(self) -> str:
        return self.file.rsplit(".", 1)[0]


def texmod_root() -> Path:
    """``<work>/out/texmod`` (not created)."""
    return Path(os.path.abspath(cfg().paths.work)) / "out" / "texmod"


def _profile_label(p: Path, entry: str, profile: str) -> str:
    try:
        rel = os.path.relpath(p, profile_root(profile))
    except (SatkError, ValueError, OSError):
        rel = None
    if rel and not rel.startswith(".."):
        return "file:" + "/".join([*Path(rel).parts, *([entry] if entry else [])]).lower()
    return jpath(p) + (f"/{entry}" if entry else "")


def _find_file(path: str, profile: str, *, cwd: bool = True) -> Path:
    from ..formats.dat import resolve_ci

    p = Path(path)
    if p.is_absolute():
        if p.is_file():
            return p
        raise SatkError("NOT_FOUND", f"no such file: {jpath(p)}")
    if cwd and (Path.cwd() / p).is_file():
        return Path.cwd() / p
    hit = resolve_ci(profile_root(profile), path)
    if hit is None or not hit.is_file():
        raise SatkError("NOT_FOUND", f"no such file (current folder or the {profile} root): {path}",
                        hint="give an absolute path, txd:<name> or models/gta3.img/<name>.txd")
    return hit


def load_txd(src: str, profile: str = "vanilla") -> TxdSource:
    """Read a TXD from ``txd:<name>``, ``file:<relpath>``, ``<img>/<entry>`` or a path."""
    s = str(src).strip()
    low = s.lower()
    if low.startswith("txd:"):
        from ..index.api import open_index, read_blob_bytes

        ref = open_index(profile).blob_ref(s)
        data, label, fname = read_blob_bytes(ref), str(ref.sid), ref.name
    else:
        is_sid = low.startswith("file:")
        path = (s[5:] if is_sid else s).replace("\\", "/")
        cut = path.lower().find(".img/")
        if cut > 0:
            from ..formats.img import ImgArchive

            arc, entry = path[:cut + 4], path[cut + 5:]
            p = _find_file(arc, profile, cwd=not is_sid)
            try:
                with ImgArchive.open(p) as a:
                    e = a.find(entry)
                    if e is None:
                        raise SatkError("NOT_FOUND", f"no entry {entry!r} in {jpath(p)}",
                                        hint=f"satk formats ls {arc} --name {entry.rsplit('.', 1)[0]}")
                    data, fname = a.read(e), e.name
            except FormatError as ex:
                raise SatkError("UNSUPPORTED", f"cannot read {jpath(p)}: {ex}") from None
            label = _profile_label(p, fname, profile)
        else:
            p = _find_file(path, profile, cwd=not is_sid)
            with open_ro(p) as f:
                data = f.read()
            label, fname = _profile_label(p, "", profile), p.name
    size = rw_payload_size(data)
    data = bytes(data[:size]) if size else bytes(data)
    try:
        txd = parse_txd(data)
    except FormatError as e:
        raise SatkError("UNSUPPORTED", f"cannot parse {label} as a TXD: {e}",
                        data={"kind": e.kind, "offset": e.offset}) from None
    if not fname.lower().endswith(".txd"):
        fname = fname.rsplit(".", 1)[0] + ".txd" if "." in fname else fname + ".txd"
    return TxdSource(label, data, fname, txd)


def find_input(path: str, *, folder: bool = False) -> Path:
    """An image file or folder: absolute, relative to the current folder, or under ``work/out/texmod``."""
    p = Path(str(path))
    cands = [p] if p.is_absolute() else [Path.cwd() / p, texmod_root() / p]
    for c in cands:
        if (c.is_dir() if folder else c.is_file()):
            return Path(os.path.abspath(c))
    what = "folder" if folder else "image"
    raise SatkError("NOT_FOUND", f"no such {what}: {path}",
                    hint="absolute path, relative to the current folder or to work/out/texmod "
                         "(satk texture extract txd:<name> writes there)",
                    data={"tried": [jpath(c) for c in cands]})


def load_image(path: Path):
    """Image file -> ``(h, w, 4)`` uint8 RGBA array (Pillow for every format, stdlib for PNG)."""
    from ..media import png as _png
    from .encode import to_array

    try:
        w, h, rgba = _png.load_rgba(path)
    except SatkError:
        raise
    except Exception as e:  # noqa: BLE001 - Pillow/zlib raise many types for broken files
        hint = None if _png.have_pillow() else "only 8-bit PNG without Pillow; " + _bootstrap()
        raise SatkError("UNSUPPORTED", f"cannot read image {jpath(path)}: {type(e).__name__}: {e}", hint=hint) from None
    return to_array(w, h, rgba)


def _bootstrap() -> str:
    from ..core.errors import bootstrap_hint

    return bootstrap_hint()


# --------------------------------------------------------------------------- building


def _pot(d: int) -> int:
    import math

    return 1 << max(0, round(math.log2(max(1, d))))


def prepare(img, name: str, *, pot: bool = True, max_size: int = 0) -> tuple[object, list[str]]:
    """Resize to power-of-two sides (``pot``) and/or to at most ``max_size`` (Pillow, Lanczos)."""
    h, w = img.shape[:2]
    nw, nh = w, h
    if max_size and max(w, h) > max_size:
        k = max_size / max(w, h)
        nw, nh = max(1, round(w * k)), max(1, round(h * k))
    if pot:
        nw, nh = _pot(nw), _pot(nh)
        while max_size and max(nw, nh) > max_size:
            nw, nh = max(1, nw // 2), max(1, nh // 2)
    warn: list[str] = []
    if (nw, nh) != (w, h):
        from ..core.errors import require_module

        np = require_module("numpy")
        Image = require_module("PIL.Image", pip="Pillow", purpose="resizing textures")
        im = Image.fromarray(np.ascontiguousarray(img), "RGBA").resize((nw, nh), Image.Resampling.LANCZOS)
        img = np.asarray(im, dtype=np.uint8).copy()
        warn.append(f"RESIZED: {name}: {w}x{h} -> {nw}x{nh}" + (" (power of two)" if pot else ""))
    if max(nw, nh) > BIG_SIDE:
        warn.append(f"BIG: {name}: {nw}x{nh}; SA-era cards take up to {BIG_SIDE} px (use --max-size)")
    return img, warn


@dataclass
class Built:
    """One encoded texture: the ``TextureNative`` chunk plus what the result rows and README need."""

    name: str
    fmt: str
    alpha: bool
    w: int
    h: int
    levels: int
    native: bytes
    img: object                     # level 0 that was encoded (numpy array)
    source: str = ""                # image path (jpath)
    sha: str = ""                   # sha256 of the image file, 16 hex
    psnr: float | None = None
    warn: list[str] = field(default_factory=list)


def _choose(img, choice: str, base_fmt: str | None, name: str) -> tuple[str, bool, list[str]]:
    from .encode import ALPHA_FORMATS, DXT_FORMATS, alpha_kind, auto_format

    if choice == "auto":
        fmt, alpha = auto_format(img, base_fmt)
        return fmt, alpha, []
    fmt = choice.upper()
    kind = alpha_kind(img)
    warn = []
    h, w = img.shape[:2]
    if fmt in DXT_FORMATS and (w % 4 or h % 4):
        raise SatkError("BAD_PARAMS", f"{name}: {w}x{h} is not a multiple of 4, {fmt} needs whole 4x4 blocks",
                        hint="keep --pot (the default) or use --format a8r8g8b8")
    if fmt == "DXT1":
        alpha = kind != "opaque"
        if kind == "smooth":
            warn.append(f"ALPHA: {name}: smooth alpha cut to 1 bit by DXT1 (dxt5 keeps it)")
    elif fmt in ALPHA_FORMATS:
        alpha = True
        if fmt == "A1R5G5B5" and kind == "smooth":
            warn.append(f"ALPHA: {name}: smooth alpha cut to 1 bit by A1R5G5B5")
    else:
        alpha = False
        if kind != "opaque":
            warn.append(f"ALPHA: {name}: alpha dropped by {fmt}")
    return fmt, alpha, warn


def build_texture(name: str, img, *, choice: str = "auto", base: TexInfo | None = None,
                  base_fmt: str | None = None, base_levels: int | None = None, mips: int | None = None,
                  quality: str = "normal") -> Built:
    """Encode ``img`` as texture ``name``.

    ``base`` = the texture it replaces (format family, mask, filter and addressing are kept); ``base_fmt`` /
    ``base_levels`` = the same hints from an extract manifest. ``mips``: ``None`` = full chain for a new
    texture, otherwise like the base (full chain if it had mips, else one level); ``0``/``1`` = no mips;
    ``N`` = at most ``N`` levels.
    """
    from .encode import encode_level, full_levels, mip_chain

    try:
        from .txdwrite import check_name

        check_name(name)
    except ValueError as e:
        raise SatkError("BAD_PARAMS", str(e), hint=f"texture names are ASCII, at most {MAX_NAME} characters") from None
    if base is not None:
        base_fmt, base_levels = base.d3dfmt, base.levels
    fmt, alpha, warn = _choose(img, choice, base_fmt, name)
    h, w = img.shape[:2]
    full = full_levels(w, h)
    if mips is None:
        n = full if base_levels is None or base_levels > 1 else 1
    elif mips < 0:
        raise SatkError("BAD_PARAMS", f"mips must be >= 0, got {mips}")
    else:
        n = max(1, min(mips, full))
    chain = mip_chain(img, n)
    levels = tuple(encode_level(lv, fmt, alpha=alpha, quality=quality) for lv in chain)
    filt = base.filter if base is not None else FILTER_TRILINEAR
    addr = ((base.uaddr << 4) | base.vaddr) if base is not None else ADDR_WRAP
    spec = NativeSpec(name=name, fmt=fmt, w=w, h=h, levels=levels, alpha=alpha,
                      mask=base.mask if base is not None else "", filter=mip_filter(filt, n), addressing=addr)
    try:
        native = native_chunk(spec)
    except ValueError as e:
        raise SatkError("BAD_PARAMS", f"{name}: {e}") from None
    return Built(name, fmt, alpha, w, h, n, native, img, warn=warn)


def _verify(data: bytes, built: list[Built]) -> None:
    """Parse ``data`` back and decode every built texture; fills ``Built.psnr``."""
    from ..formats.dxt import decode_rgba
    from .encode import psnr, to_array

    try:
        txd = parse_txd(data)
    except FormatError as e:  # pragma: no cover - would be a writer bug
        raise SatkError("INTERNAL", f"the written TXD does not parse: {e}") from None
    by_name: dict[str, TexInfo] = {}
    for t in txd.textures:
        by_name.setdefault(t.name.lower(), t)
    for b in built:
        t = by_name.get(b.name.lower())
        if t is None or (t.d3dfmt, t.w, t.h, t.levels) != (b.fmt, b.w, b.h, b.levels):
            got = None if t is None else (t.d3dfmt, t.w, t.h, t.levels)
            raise SatkError("INTERNAL", f"round trip of {b.name!r}: wrote {(b.fmt, b.w, b.h, b.levels)}, read {got}")
        dec = to_array(t.w, t.h, decode_rgba(t, mip0_bytes(data, t), palette_bytes(data, t)))
        b.psnr = psnr(b.img, dec, alpha=t.alpha)


# --------------------------------------------------------------------------- output


def _check_mod(name: str) -> str:
    if not _MOD_NAME.match(name or ""):
        raise SatkError("BAD_PARAMS", f"bad mod name {name!r}",
                        hint="letters, digits, '_', '-', '.'; at most 64 characters, e.g. --name bistro_hd")
    return name


def mod_dir(name: str) -> Path:
    """``work/out/mods/<name>`` (created); ``name`` is a plain folder name."""
    return work("out", "mods", _check_mod(name))


def _check_file(file: str) -> str:
    f = file if file.lower().endswith(".txd") else file + ".txd"
    if not _FILE_NAME.match(f):
        raise SatkError("BAD_PARAMS", f"bad TXD file name {file!r}", hint="e.g. --file bistro.txd")
    return f


def _readme_header(name: str) -> list[str]:
    return [
        f"{name} -- GTA San Andreas texture mod built by satk (San Andreas ToolKit)",
        "",
        f"Install with modloader: copy this folder to <GTA San Andreas>/modloader/{name}/ .",
        "Each .txd replaces the game TXD of the same file name (for example models/gta3.img/<file>.txd).",
        "Without modloader: put the .txd into the IMG archive with an IMG editor (keep a backup).",
        "",
        "Sections below: one per TXD of this folder -- what it was built from.",
        "",
    ]


def _write_readme(d: Path, file: str, section: list[str]) -> Path:
    """Update the section of ``file`` in ``d/README.txt``; sections of TXDs no longer in ``d`` are dropped."""
    path = d / README
    sections: dict[str, list[str]] = {}
    try:
        old = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        old = []
    cur: str | None = None
    for line in old:
        m = _SECTION.match(line)
        if m:
            cur = m.group(1)
            sections[cur] = [line]
        elif cur is not None:
            sections[cur].append(line)
    sections[file] = [f"== {file} ==", *section, ""]
    present = {p.name.lower() for p in d.iterdir() if p.is_file()}
    lines = _readme_header(d.name)
    for k in sorted(sections, key=str.lower):
        if k.lower() in present:
            body = sections[k]
            while body and not body[-1].strip():
                body = body[:-1]
            lines += body + [""]
    return atomic_write(path, "\n".join(lines).rstrip("\n") + "\n")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()[:16]


def _rows(built: list[Built]) -> list[list]:
    return [[b.name, b.fmt + ("+a" if b.fmt == "DXT1" and b.alpha else ""), f"{b.w}x{b.h}", b.levels, b.psnr,
             b.source] for b in built]


_COLS = ["name", "fmt", "size", "mips", "psnr", "source"]


def _tex_lines(built: list[Built]) -> list[str]:
    wn = max([4] + [len(b.name) for b in built])
    out = []
    for b in built:
        fmt = b.fmt + ("+a" if b.fmt == "DXT1" and b.alpha else "")
        out.append(f"  {b.name:<{wn}}  {fmt:<8}  {f'{b.w}x{b.h}':>9}  mips {b.levels:<2}  psnr {b.psnr:6.2f}  "
                   f"<- {b.source} (sha256 {b.sha})")
    return out


def _finish(mod: str, file: str, data: bytes, built: list[Built], head: list[str], warn: list[str]) -> dict:
    from ..core.envelope import table

    _verify(data, built)
    d = mod_dir(mod)
    out = atomic_write(d / file, data)
    section = [*head, f"size: {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()[:16]}",
               "textures written (psnr of mip 0 against the image; 100 = lossless):", *_tex_lines(built)]
    readme = _write_readme(d, file, section)
    others = sorted(p.name for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".txd" and p.name != file)
    if others:
        warn.append(f"OTHER_FILES: {d.name} also holds {', '.join(others[:5])} (earlier runs; delete if stale)")
    for b in built:
        warn.extend(b.warn)
    env = table(_COLS, _rows(built), warn=warn)
    env.update(file=jpath(out), mod=jpath(d), readme=jpath(readme), bytes=len(data))
    return env


# --------------------------------------------------------------------------- operations


def _manifest(d: Path) -> dict:
    p = d / MANIFEST
    if not p.is_file():
        return {}
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"broken {jpath(p)}: {e}", hint=f"delete {MANIFEST} or re-run texture extract")
    return m if isinstance(m, dict) else {}


def class_format(img, asset_class: str) -> tuple[str, int]:
    """``(format choice, mip levels)`` of an image for a :data:`CLASS_PRESETS` class (99 = full chain)."""
    from .encode import alpha_kind

    pre = CLASS_PRESETS[asset_class]
    fmt = pre[alpha_kind(img)]
    h, w = img.shape[:2]
    levels = 99 if pre["mips_from"] and max(w, h) >= pre["mips_from"] else 1
    return fmt, levels


def pack(src_dir: str, name: str | None = None, file: str | None = None, choice: str = "auto",
         mips: int | None = None, quality: str = "normal", pot: bool = True, max_size: int = 0,
         asset_class: str | None = None, out: str | None = None) -> dict:
    """Images of a folder -> ``work/out/mods/<name>/<file>.txd`` (one texture per image, named by its file).

    ``asset_class`` picks formats and mip levels like the vanilla game (:data:`CLASS_PRESETS`; an explicit
    ``choice``/``mips`` still wins); ``out`` writes ``<out>/<file>.txd`` into any writable folder (a mod
    folder) instead, without a README."""
    from ..core.registry import report_progress

    if asset_class is not None and asset_class not in CLASS_PRESETS:
        raise SatkError("BAD_PARAMS", f"unknown asset class {asset_class!r}",
                        did_you_mean=difflib.get_close_matches(asset_class, list(CLASS_PRESETS), n=3, cutoff=0.4))
    d = find_input(src_dir, folder=True)
    mod = _check_mod(name or d.name)
    fname = _check_file(file or mod)
    man = _manifest(d)
    known = {str(t.get("file", "")).lower(): t for t in man.get("textures", []) if isinstance(t, dict)}
    imgs = sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS), key=lambda p: p.name.lower())
    if not imgs:
        raise SatkError("NOT_FOUND", f"no images in {jpath(d)}", hint=f"put PNGs there ({', '.join(IMAGE_EXTS[:3])} ...)")
    order = {str(t.get("file", "")).lower(): i for i, t in enumerate(man.get("textures", [])) if isinstance(t, dict)}
    imgs.sort(key=lambda p: (order.get(p.name.lower(), len(order)), p.name.lower()))
    seen: dict[str, str] = {}
    built: list[Built] = []
    warn: list[str] = []
    for i, p in enumerate(imgs):
        meta = known.get(p.name.lower(), {})
        tname = str(meta.get("name") or p.stem)
        if tname.lower() in seen:
            raise SatkError("BAD_PARAMS", f"two images give texture {tname!r}: {seen[tname.lower()]} and {p.name}",
                            hint="texture names are case-insensitive; rename one file")
        seen[tname.lower()] = p.name
        report_progress(i, len(imgs), p.name)
        img, w1 = prepare(load_image(p), tname, pot=pot, max_size=max_size)
        c_choice, c_mips, base_fmt, base_levels = choice, mips, meta.get("format"), meta.get("levels")
        if asset_class is not None:
            cf, cl = class_format(img, asset_class)
            c_choice = choice if choice != "auto" else cf
            c_mips = mips if mips is not None else cl
            base_fmt = base_levels = None
            side = CLASS_PRESETS[asset_class]["max_side"]
            if max(img.shape[:2]) > side:
                w1.append(f"CLASS_SIZE: {tname}: {img.shape[1]}x{img.shape[0]} is above the largest vanilla "
                          f"{asset_class} texture ({side} px; tier sa_plus allows about 2x)")
        b = build_texture(tname, img, choice=c_choice, base_fmt=base_fmt, base_levels=base_levels, mips=c_mips,
                          quality=quality)
        b.source, b.sha, b.warn = jpath(p), _sha(p), w1 + b.warn
        built.append(b)
    data = txd_chunk([b.native for b in built])
    head = ["built by: satk texture pack" + (f" --asset-class {asset_class}" if asset_class else ""),
            f"images: {jpath(d)} ({len(built)} files)"]
    if man.get("source"):
        head.append(f"extracted from: {man['source']}")
    if out:
        return _finish_out(out, fname, data, built, warn, asset_class)
    env = _finish(mod, fname, data, built, head, warn)
    if asset_class:
        env["asset_class"] = asset_class
    return env


def _finish_out(out: str, file: str, data: bytes, built: list[Built], warn: list[str], asset_class: str | None) -> dict:
    """Verify and write ``data`` as ``<out>/<file>`` (any writable folder, e.g. a modloader mod folder)."""
    from ..core.envelope import table

    _verify(data, built)
    p = Path(out)
    d = ensure_writable(p if p.is_absolute() else Path.cwd() / p)
    d.mkdir(parents=True, exist_ok=True)
    target = atomic_write(d / file, data)
    for b in built:
        warn.extend(b.warn)
    env = table(_COLS, _rows(built), warn=warn)
    env.update(file=jpath(target), bytes=len(data))
    if asset_class:
        env["asset_class"] = asset_class
    return env


def _parse_swaps(swaps: list[str]) -> list[tuple[str, str]]:
    out = []
    for s in swaps or []:
        name, sep, path = str(s).partition("=")
        if not sep or not name.strip() or not path.strip():
            raise SatkError("BAD_PARAMS", f"bad replacement {s!r}: expected <texture>=<image>",
                            hint="satk texture replace txd:bistro Plate=Marble.png")
        out.append((name.strip(), path.strip()))
    if not out:
        raise SatkError("BAD_PARAMS", "no replacements given", hint="satk texture replace txd:bistro Plate=Marble.png")
    return out


def replace(txd: str, swaps: list[str], name: str | None = None, file: str | None = None, choice: str = "auto",
            mips: int | None = None, quality: str = "normal", pot: bool = True, max_size: int = 0, add: bool = False,
            profile: str = "vanilla") -> dict:
    """Copy of a TXD with ``<texture>=<image>`` swaps -> ``work/out/mods/<name>/<txd file>``."""
    from ..core.registry import report_progress

    pairs = _parse_swaps(swaps)
    src = load_txd(txd, profile)
    mod = _check_mod(name or src.stem)
    fname = _check_file(file or src.file)
    by_name: dict[str, list[TexInfo]] = {}
    for t in src.txd.textures:
        by_name.setdefault(t.name.lower(), []).append(t)
    done: set[str] = set()
    repl: dict[int, bytes] = {}
    append: list[bytes] = []
    built: list[Built] = []
    warn: list[str] = []
    for i, (tname, path) in enumerate(pairs):
        key = tname.lower()
        if key in done:
            raise SatkError("BAD_PARAMS", f"texture {tname!r} is replaced twice")
        done.add(key)
        hits = by_name.get(key, [])
        if not hits and not add:
            names = [t.name for t in src.txd.textures]
            raise SatkError("NOT_FOUND", f"no texture {tname!r} in {src.label} ({len(names)} textures)",
                            did_you_mean=difflib.get_close_matches(tname, names, n=3, cutoff=0.5),
                            hint=f"satk formats dump {src.label} --level full  (or --add to append a new texture)")
        if len(hits) > 1:
            warn.append(f"DUPLICATE: {src.label} has {len(hits)} textures named {tname!r}; the first one is replaced")
        base = hits[0] if hits else None
        p = find_input(path)
        report_progress(i, len(pairs), p.name)
        img, w1 = prepare(load_image(p), tname, pot=pot, max_size=max_size)
        b = build_texture(base.name if base else tname, img, choice=choice, base=base, mips=mips, quality=quality)
        b.source, b.sha, b.warn = jpath(p), _sha(p), w1 + b.warn
        if base is not None:
            repl[base.idx] = b.native
        else:
            append.append(b.native)
        built.append(b)
    try:
        data = rewrite_txd(src.data, repl, append)
    except (FormatError, ValueError) as e:
        raise SatkError("UNSUPPORTED", f"cannot rewrite {src.label}: {e}") from None
    added = len(append)
    head = ["built by: satk texture replace", f"base TXD: {src.label} ({len(src.txd.textures)} textures, "
            f"{len(built) - added} replaced, {added} added; the others are copied unchanged)"]
    return _finish(mod, fname, data, built, head, warn)


def _safe(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip(".") or "tex"
    return s


def extract(txd: str, out: str | None = None, force: bool = False, profile: str = "vanilla") -> dict:
    """Every texture of a TXD (mip 0) as a PNG + ``texmod.json``; edited PNGs are kept unless ``force``."""
    from ..core.envelope import table
    from ..formats.dxt import decode_rgba
    from ..media import png as _png

    src = load_txd(txd, profile)
    if out:
        d = ensure_writable(Path(out) if Path(out).is_absolute() else Path.cwd() / out)
        d.mkdir(parents=True, exist_ok=True)
    else:
        d = work("out", "texmod", _safe(src.stem.lower()))
    used: set[str] = set()
    rows, entries, warn = [], [], []
    kept = 0
    for t in src.txd.textures:
        if t.unsupported:
            warn.append(f"UNSUPPORTED: {t.name or t.idx}: platform 0x{t.platform:X} skipped")
            continue
        fn = _safe(t.name) + ".png"
        if fn.lower() in used:
            fn = f"{_safe(t.name)}~{t.idx}.png"
        used.add(fn.lower())
        try:
            rgba = decode_rgba(t, mip0_bytes(src.data, t), palette_bytes(src.data, t))
        except FormatError as e:
            warn.append(f"UNSUPPORTED: {t.name}: {e}")
            continue
        png = _png.encode(t.w, t.h, rgba, opaque=not t.alpha)
        p = d / fn
        state = "written"
        if p.is_file():
            cur = p.read_bytes()
            if cur == png:
                state = "same"
            elif not force:
                state, kept = "kept", kept + 1
        if state == "written":
            _png.write_file(p, png)
        rows.append([t.name, fn, t.d3dfmt, f"{t.w}x{t.h}", t.levels, state])
        entries.append({"name": t.name, "file": fn, "format": t.d3dfmt, "w": t.w, "h": t.h, "levels": t.levels,
                        "alpha": t.alpha})
    man = {"source": src.label, "txd": src.file, "textures": entries}
    atomic_write(d / MANIFEST, json.dumps(man, ensure_ascii=False, indent=1) + "\n")
    if kept:
        warn.append(f"KEPT: {kept} edited PNG(s) differ from the TXD and were not overwritten (--force)")
    env = table(["name", "file", "fmt", "size", "mips", "state"], rows, warn=warn)
    env.update(dir=jpath(d), source=src.label, txd=src.file)
    return env
