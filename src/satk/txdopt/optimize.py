"""``satk texture optimize``: smaller TXDs for a TXD, a mod folder, a .zip or an .img, written to
``<work>/out/txdopt/<out>/`` (the input is only read).

Per texture, in this order:

1. ``drop_unused`` -- drop it when no DFF of the TXD's user models names it (:mod:`.usage`; undecided TXDs and
   :data:`~.usage.ALWAYS_KEEP` / ``keep`` names stay);
2. ``dedupe`` -- drop a later texture with the same name and the same pixels in the same TXD;
3. size -- sides to powers of two (``pot``; the nearest, upscaling at most 30 %) and the long side to ``max``
   (halving keeps the aspect); exact 2^k reductions use the alpha-weighted box filter of the mip builder, other
   ratios Pillow Lanczos on premultiplied RGBA;
4. format (``dxt``) -- uncompressed and paletted textures become DXT1 (opaque), DXT1 with 1-bit alpha (only
   0/255 alpha) or DXT5 (smooth alpha); ``dxt1`` forces DXT1, ``dxt5`` uses DXT5 for any alpha, ``keep`` changes
   no format. Fully opaque DXT3/DXT5 become DXT1 losslessly (:func:`.txdedit.dxt_to_dxt1`). A conversion that
   would not make the texture smaller is skipped;
5. ``mips`` -- a full chain down to 1x1 for textures that have one level (a resized texture with mips gets a full
   chain again). Stored levels that stay valid are copied, so adding mips never touches level 0.

With ``share`` (implies ``dedupe``), textures that come out byte-identical in two or more TXDs of the bundle move
to one new parent TXD; each child TXD gets a ``txdp`` line in a copy of the bundle IDE that defines its models.
Only object TXDs (``objs``/``tobj``/``anim``) whose users are all defined by the bundle's IDEs and that have no
parent or children qualify.

Every written TXD is parsed back, every re-encoded texture decoded: the rows carry the PSNR of mip 0 against the
(resized) source (100 = lossless). The output folder also gets ``txdopt.json`` (all rows) and ``README.txt``.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, jpath, work
from ..formats.rw import FormatError, iter_children, read_chunk, rw_payload_size
from ..formats.txd import TexInfo, Txd, parse_txd
from .analyze import DXT, RAW, alpha_kind, chain_bytes, fmt_label, full_levels, is_pow2
from .inputs import Bundle, Item, stream_size

__all__ = ["DXT_MODES", "Opts", "Row", "target_dims", "optimize", "OUT_NAME"]

DXT_MODES = ("auto", "dxt1", "dxt5", "keep")
OUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REPORT = "txdopt.json"
README = "README.txt"
#: Upscaling to the next power of two is accepted up to this factor (100 -> 128, 1000 -> 1024), else the side halves.
POT_UPSCALE = 1.3
LOW_PSNR = 30.0
#: IDE sections whose TXDs may get a ``txdp`` parent from ``share`` (map objects; vehicles already have one).
SHARE_SECTIONS = frozenset({"objs", "tobj", "anim"})


@dataclass
class Opts:
    max: int = 0
    pot: bool = True
    mips: bool = False
    dxt: str = "auto"
    drop_unused: bool = False
    dedupe: bool = False
    share: bool = False
    keep: tuple[str, ...] = ()
    quality: str = "normal"
    profile: str = "vanilla"
    full: bool = False
    share_name: str = ""


@dataclass
class Row:
    """One texture of the output (or a dropped one)."""

    txd: str
    name: str
    action: str
    before: str
    after: str
    bytes_before: int
    bytes_after: int
    psnr: float | None = None

    @property
    def saved(self) -> int:
        return self.bytes_before - self.bytes_after

    def cells(self) -> list:
        return [self.txd, self.name, self.action, self.before, self.after, self.saved, self.psnr]


COLS = ["txd", "texture", "action", "before", "after", "saved", "psnr"]


# --------------------------------------------------------------------------- sizes


def _pot_side(d: int) -> int:
    if is_pow2(d):
        return d
    up = 1 << max(0, d - 1).bit_length()
    return up if up <= d * POT_UPSCALE else max(1, up // 2)


def target_dims(w: int, h: int, max_side: int = 0, pot: bool = True) -> tuple[int, int]:
    """Output size: powers of two (``pot``) and the long side at most ``max_side`` (0 = no cap)."""
    nw, nh = w, h
    if pot:
        nw, nh = _pot_side(nw), _pot_side(nh)
        while max_side and max(nw, nh) > max_side:
            nw, nh = max(1, nw // 2), max(1, nh // 2)
    elif max_side and max(nw, nh) > max_side:
        k = max_side / max(nw, nh)
        nw, nh = max(1, round(nw * k)), max(1, round(nh * k))
    return nw, nh


def resample(img, w: int, h: int):
    """``(h, w, 4)`` uint8 image at the new size (box filter for 2^k reductions, else Lanczos premultiplied)."""
    from ..texmod.encode import mip_chain

    H, W = img.shape[:2]
    if (W, H) == (w, h):
        return img
    if W % w == 0 and H % h == 0 and W // w == H // h and is_pow2(W // w):
        k = (W // w).bit_length() - 1
        return mip_chain(img, k + 1)[k]
    from ..core.errors import require_module

    np = require_module("numpy")
    Image = require_module("PIL.Image", pip="Pillow", purpose="resizing textures to powers of two")
    im = Image.fromarray(np.ascontiguousarray(img), "RGBA")
    opaque = bool((img[:, :, 3] == 255).all())
    if not opaque:
        im = im.convert("RGBa")
    im = im.resize((w, h), Image.Resampling.LANCZOS)
    if not opaque:
        im = im.convert("RGBA")
    return np.asarray(im, dtype=np.uint8).copy()


# --------------------------------------------------------------------------- one texture


@dataclass
class Plan:
    """What to write for one kept texture (``None`` fields = unchanged)."""

    w: int
    h: int
    fmt: str
    alpha: bool
    levels: int
    actions: list[str] = field(default_factory=list)


def _writable(fmt: str) -> bool:
    from ..texmod.txdwrite import FMT_LAYOUT

    return fmt in FMT_LAYOUT


def _pick_format(t: TexInfo, kind: str, w: int, h: int, mode: str) -> tuple[str, bool]:
    """Output ``(format, alpha flag)`` for texture ``t`` whose decoded alpha is ``kind``."""
    src = t.d3dfmt
    blocks = w % 4 == 0 and h % 4 == 0
    if src == "DXT1":
        return ("DXT1", t.alpha) if blocks else (("A8R8G8B8", True) if t.alpha else ("X8R8G8B8", False))
    if src in ("DXT3", "DXT5"):
        if mode != "keep" and kind == "opaque" and blocks:
            return "DXT1", False
        return (src, True) if blocks else ("A8R8G8B8", True)
    # uncompressed / paletted
    if mode == "keep" or not blocks:
        if _writable(src):
            return src, t.alpha
        return ("X8R8G8B8", False) if kind == "opaque" else ("A8R8G8B8", True)
    if kind == "opaque":
        return "DXT1", False
    if mode == "dxt1":
        return "DXT1", True
    if mode == "dxt5":
        return "DXT5", True
    return ("DXT1", True) if kind == "binary" else ("DXT5", True)


def _levels_out(t: TexInfo, w: int, h: int, resized: bool, want_mips: bool) -> int:
    full = full_levels(w, h)
    if t.levels > 1:
        return full if resized else min(t.levels, full)
    return full if want_mips else 1


@dataclass
class Encoded:
    """A rewritten texture: the levels to store and the PSNR of level 0 (100 = lossless)."""

    plan: Plan
    levels: tuple[bytes, ...]
    psnr: float


class Encoder:
    """Plans and encodes textures; identical inputs are encoded once (cache by pixels + parameters)."""

    def __init__(self, opts: Opts):
        self.o = opts
        self.cache: dict[tuple, Encoded] = {}

    def plan(self, buf: bytes, t: TexInfo, kind_of) -> Plan | None:
        """``None`` = keep the texture byte for byte. ``kind_of()`` decodes the alpha kind lazily."""
        if t.unsupported or t.w == 0 or t.h == 0 or t.levels == 0:
            return None
        src = t.d3dfmt
        if src not in DXT and src not in RAW:
            return None
        w, h = target_dims(t.w, t.h, self.o.max, self.o.pot)
        resized = (w, h) != (t.w, t.h)
        maybe_fmt = resized or src in RAW or (src in ("DXT3", "DXT5") and self.o.dxt != "keep")
        kind = kind_of() if maybe_fmt else ("opaque" if not t.alpha else "smooth")
        if kind is None:
            return None
        fmt, alpha = _pick_format(t, kind, w, h, self.o.dxt)
        n = _levels_out(t, w, h, resized, self.o.mips)
        if not resized and fmt == src and n == t.levels and alpha == t.alpha:
            return None
        if fmt != src and not resized and src in RAW + ("DXT3", "DXT5"):
            if chain_bytes(fmt, w, h, n) >= chain_bytes(src, w, h, n):
                if n == t.levels or not _writable(src):
                    return None
                fmt, alpha = src, t.alpha          # only the mips change
        if fmt == src and not _writable(src) and src not in DXT:
            return None                            # paletted etc. cannot be rewritten in their own format
        p = Plan(w, h, fmt, alpha, n)
        if resized:
            p.actions.append("resize" if self.o.max and max(t.w, t.h) > self.o.max else "pot")
        if fmt != src:
            p.actions.append("dxt1" if src in ("DXT3", "DXT5") else "dxt" if fmt in DXT else "fmt")
        if n > t.levels:
            p.actions.append("mips")
        elif n < t.levels:
            p.actions.append("mips-")
        return p

    def encode(self, buf: bytes, t: TexInfo, p: Plan, key: bytes) -> Encoded:
        ck = (key, t.d3dfmt, t.w, t.h, t.levels, t.alpha, p.w, p.h, p.fmt, p.alpha, p.levels, self.o.quality)
        hit = self.cache.get(ck)
        if hit is not None:
            return Encoded(p, hit.levels, hit.psnr)
        enc = self._encode(buf, t, p)
        self.cache[ck] = enc
        return enc

    def _encode(self, buf: bytes, t: TexInfo, p: Plan) -> Encoded:
        from ..texmod.encode import encode_level, mip_chain, psnr
        from .analyze import decode
        from .txdedit import dxt_to_dxt1, level_list

        resized = (p.w, p.h) != (t.w, t.h)
        stored: list[bytes] = []
        if not resized and p.fmt == t.d3dfmt:
            stored = level_list(buf, t)[:p.levels]
        elif not resized and t.d3dfmt in ("DXT3", "DXT5") and p.fmt == "DXT1":
            stored = [dxt_to_dxt1(lv) for lv in level_list(buf, t)[:p.levels]]
        img = None
        out = list(stored)
        q = 100.0
        if len(out) < p.levels:
            img = decode(buf, t)
            if not p.alpha and not bool((img[:, :, 3] == 255).all()):
                img = img.copy()                   # alpha the output does not keep must not weight the filters
                img[:, :, 3] = 255
            if resized:
                img = resample(img, p.w, p.h)
                if p.fmt == "DXT1" and p.alpha:
                    img = img.copy()               # a cut-out stays a cut-out: no half-transparent edges to lose
                    img[:, :, 3] = (img[:, :, 3] >= 128).astype(img.dtype) * 255
            chain = mip_chain(img, p.levels)
            for i in range(len(out), p.levels):
                out.append(encode_level(chain[i], p.fmt, alpha=p.alpha, quality=self.o.quality))
            if not stored:
                q = psnr(img, _decode_level(out[0], t, p), alpha=p.alpha)
        return Encoded(p, tuple(out), q)


def _decode_level(level: bytes, t: TexInfo, p: Plan):
    import dataclasses

    from ..formats.dxt import decode_rgba
    from ..texmod.encode import to_array

    ti = dataclasses.replace(t, d3dfmt=p.fmt, w=p.w, h=p.h, alpha=p.alpha, levels=1, pal_off=None, pal_size=0)
    return to_array(p.w, p.h, decode_rgba(ti, level))


def native_for(t: TexInfo, enc: Encoded, orig_native: bytes, libid: int) -> bytes:
    """The ``TextureNative`` chunk of the re-encoded texture; the original Extension chunk is kept."""
    from ..texmod.txdwrite import NativeSpec, chunk, mip_filter, native_chunk

    p = enc.plan
    spec = NativeSpec(name=t.name, fmt=p.fmt, w=p.w, h=p.h, levels=enc.levels, alpha=p.alpha, mask=t.mask,
                      filter=mip_filter(t.filter, p.levels), addressing=(t.uaddr << 4) | t.vaddr)
    new = native_chunk(spec, libid)
    st = read_chunk(new, 12)                     # the Struct child of the new native
    struct_raw = new[12:st.end]
    ext = b""
    try:
        top = read_chunk(orig_native, 0)
        for ch in iter_children(orig_native, top.data_off, top.end):
            if ch.type == 0x03:
                ext = bytes(orig_native[ch.data_off - 12:ch.end])
                break
    except FormatError:
        ext = b""
    if not ext:
        ext = chunk(0x03, b"", libid)
    return chunk(0x15, struct_raw + ext, libid)


# --------------------------------------------------------------------------- one TXD


@dataclass
class TxdResult:
    """One processed TXD. ``data`` (the input bytes) is released after processing unless ``share`` needs it;
    ``size`` and ``sig`` (``(hash, bytes, name)`` per output native) stay for the totals and the duplicate hint."""

    item: Item
    data: bytes                     # original TXD bytes (RW payload only)
    txd: Txd | None
    natives: dict[int, bytes | None] = field(default_factory=dict)
    out: bytes | None = None        # new bytes when changed
    rows: list[Row] = field(default_factory=list)
    keep_rows: list[Row] = field(default_factory=list)   # unchanged textures (for the report)
    verdict: str = ""
    size: int = 0
    sig: list[tuple[bytes, int, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.out is not None

    @property
    def size_after(self) -> int:
        return len(self.out) if self.out is not None else self.size

    def seal(self, keep_data: bool) -> None:
        """Record the size and the output signature; drop the input bytes unless ``keep_data``."""
        from .txdedit import native_spans

        if self.out is not None and self.out == self.data:
            self.out = None
        self.size = len(self.data)
        cur = self.out if self.out is not None else self.data
        self.sig = [(hashlib.blake2b(cur[a:b], digest_size=16).digest(), b - a,
                     bytes(cur[a + 32:a + 64]).split(b"\0", 1)[0].decode("latin-1")) for a, b in native_spans(cur)]
        if not keep_data:
            self.data = b""


def _read_txd(item: Item, warn: list[str]) -> tuple[bytes, Txd] | None:
    try:
        raw = item.read()
    except SatkError as e:
        warn.append(f"UNREADABLE: {item.rel}: {e.msg}")
        return None
    n = rw_payload_size(raw)
    data = bytes(raw[:n]) if n else bytes(raw)
    try:
        return data, parse_txd(data)
    except FormatError as e:
        warn.append(f"UNSUPPORTED: {item.rel}: not a parsable TXD ({e})")
        return None


def content_key(data: bytes, t: TexInfo, start: int, end: int) -> bytes:
    """Identity of a texture's pixels: format, size, flags, palette and every stored level (not the name)."""
    from .txdedit import level_list

    h = hashlib.blake2b(digest_size=16)
    if t.unsupported:
        h.update(bytes(data[start:end]))
        return h.digest()
    h.update(f"{t.platform}|{t.d3dfmt}|{t.w}|{t.h}|{t.levels}|{int(t.alpha)}|{t.raster_fmt}".encode("ascii"))
    if t.pal_off is not None:
        h.update(bytes(data[t.pal_off:t.pal_off + t.pal_size]))
    for lv in level_list(data, t):
        h.update(len(lv).to_bytes(4, "little"))
        h.update(lv)
    return h.digest()


def _process_txd(item: Item, data: bytes, txd: Txd, enc: Encoder, usage, keep, o: Opts,
                 warn: list[str]) -> TxdResult:
    from .txdedit import native_spans, rebuild

    res = TxdResult(item, data, txd)
    spans = native_spans(data)
    libid = struct.unpack_from("<I", data, 8)[0]
    names: set[str] | None = None
    if o.drop_unused and usage is not None:
        v = usage.verdict(item.stem)
        names = v.names
        res.verdict = v.why
    seen: dict[tuple[str, bytes], str] = {}
    seen_names: set[str] = set()
    for t in txd.textures:
        start, end = spans[t.idx]
        nbytes = end - start
        label = fmt_label(t.d3dfmt, t.alpha, t.w, t.h, t.levels) if not t.unsupported else t.d3dfmt
        lname = t.name.lower()
        key = content_key(data, t, start, end)
        if names is not None and t.name and lname not in names and not keep(t.name):
            res.natives[t.idx] = None
            res.rows.append(Row(item.rel, t.name, "drop", label, "-", nbytes, 0))
            continue
        if o.dedupe and t.name and (lname, key) in seen:
            res.natives[t.idx] = None
            res.rows.append(Row(item.rel, t.name, "dedupe", label, f"= {seen[(lname, key)]}", nbytes, 0))
            continue
        if o.dedupe and lname in seen_names:
            warn.append(f"DUP_NAME: {item.rel}: two textures named {t.name!r} with different pixels (both kept)")
        seen[(lname, key)] = t.name
        seen_names.add(lname)

        def kind_of(t: TexInfo = t):
            return _alpha_of(data, t, item.rel, warn)

        p = enc.plan(data, t, kind_of)
        if p is None:
            res.keep_rows.append(Row(item.rel, t.name, "", label, label, nbytes, nbytes))
            continue
        try:
            e = enc.encode(data, t, p, key)
            native = native_for(t, e, bytes(data[start:end]), libid)
        except ValueError as ex:
            warn.append(f"UNSUPPORTED: {item.rel}: {t.name}: kept unchanged ({ex})")
            res.keep_rows.append(Row(item.rel, t.name, "", label, label, nbytes, nbytes))
            continue
        res.natives[t.idx] = native
        after = fmt_label(p.fmt, p.alpha, p.w, p.h, p.levels)
        res.rows.append(Row(item.rel, t.name, "+".join(p.actions), label, after, nbytes, len(native), e.psnr))
    if res.natives:
        res.out = rebuild(data, res.natives)
        _check_roundtrip(res)
    return res


def _alpha_of(data: bytes, t: TexInfo, rel: str, warn: list[str]) -> str | None:
    """Alpha kind the output must keep (``None`` = cannot decode: keep the texture)."""
    import dataclasses

    from .analyze import decode

    try:
        img = decode(data, dataclasses.replace(t, alpha=True) if t.d3dfmt == "DXT1" else t)
    except (FormatError, ValueError) as e:
        warn.append(f"UNSUPPORTED: {rel}: {t.name}: cannot decode ({e})")
        return None
    k = alpha_kind(img)
    if t.d3dfmt in ("DXT3", "DXT5"):
        if k == "opaque" and t.levels > 1 and not _levels_opaque(data, t):
            return "binary"                        # lossless DXT1 needs every level opaque
        return k
    return k if t.alpha else "opaque"


def _check_roundtrip(res: TxdResult) -> None:
    """The rebuilt TXD parses and holds every re-encoded texture with the planned format, size and levels."""
    try:
        txd = parse_txd(res.out)
    except FormatError as e:  # pragma: no cover - a writer bug
        raise SatkError("INTERNAL", f"the rebuilt {res.item.rel} does not parse: {e}") from None
    got = [(t.name, fmt_label(t.d3dfmt, t.alpha, t.w, t.h, t.levels)) for t in txd.textures]
    want = [(r.name, r.after) for r in res.rows if r.action not in ("drop", "dedupe", "share")]
    missing = [w for w in want if w not in got]
    if missing or len(txd.textures) != txd.count:  # pragma: no cover - a writer bug
        raise SatkError("INTERNAL", f"round trip of {res.item.rel}: {missing[:3]} not found after the rebuild")


def _levels_opaque(data: bytes, t: TexInfo) -> bool:
    """Every stored level of a DXT3/DXT5 texture decodes with alpha 255."""
    import dataclasses

    from ..formats.dxt import decode_rgba
    from .txdedit import level_list

    for i, lv in enumerate(level_list(data, t)):
        w, h = max(1, t.w >> i), max(1, t.h >> i)
        if not lv:
            continue
        ti = dataclasses.replace(t, w=w, h=h)
        try:
            px = decode_rgba(ti, lv)
        except FormatError:
            return False
        if px[3::4].count(255) != w * h:
            return False
    return True


# --------------------------------------------------------------------------- share (txdp)


def _shared_stem(base: str, taken) -> str:
    s = re.sub(r"[^a-z0-9_]", "_", base.lower()).strip("_")[:12] or "mod"
    stem = s + "_shared"
    k = 2
    while taken(stem):
        stem = f"{s}_shared{k}"
        k += 1
    return stem


@dataclass
class Share:
    txd: bytes
    stem: str
    moved: dict[str, list[str]]                 # child TXD rel -> moved texture names
    ide: dict[str, list[tuple[str, str]]]       # IDE rel -> [(child, parent)]
    container: str | None = None                # IMG the shared TXD goes into (None = loose)


def _plan_share(results: list[TxdResult], usage, o: Opts, base: str, warn: list[str]) -> Share | None:
    """Move byte-identical output textures of eligible TXDs into one new parent TXD (``txdp``)."""
    from ..texmod.txdwrite import txd_chunk
    from .txdedit import native_spans, rebuild

    if usage is None:
        return None
    eligible: dict[str, TxdResult] = {}
    for r in results:
        stem = r.item.stem
        v = usage.verdict(stem)
        if stem in eligible or not v.models or any(m not in usage.mod_ide_of for m in v.models):
            continue
        if not v.secs or not v.secs <= SHARE_SECTIONS:
            continue                    # weapon icons, vehicle remaps etc. are looked up in the TXD itself
        if usage.parent_of(stem) or usage.index_children(stem) or stem in usage.mod_parent.values():
            continue
        eligible[stem] = r
    if len(eligible) < 2:
        warn.append("SHARE: fewer than two TXDs qualify (every model defined by the bundle's IDEs, no txdp parent "
                    "or children); nothing shared")
        return None
    groups: dict[bytes, list[str]] = {}
    blobs: dict[bytes, tuple[str, bytes]] = {}
    for stem, r in sorted(eligible.items()):
        data = r.out if r.out is not None else r.data
        spans = native_spans(data)
        for t in parse_txd(data).textures:
            nat = bytes(data[slice(*spans[t.idx])])
            k = hashlib.sha256(nat).digest()
            if stem not in groups.setdefault(k, []):
                groups[k].append(stem)
            blobs.setdefault(k, (t.name.lower(), nat))
    shared = {k for k, v in groups.items() if len(v) >= 2}
    if not shared:
        warn.append("SHARE: no texture is byte-identical in two qualifying TXDs; nothing shared")
        return None
    bundle_txds = {r.item.stem for r in results}
    sstem = _shared_stem(o.share_name or base, lambda s: s in bundle_txds or usage.txd_exists(s))
    moved: dict[str, list[str]] = {}
    for stem, r in sorted(eligible.items()):
        data = r.out if r.out is not None else r.data
        spans = native_spans(data)
        txd = parse_txd(data)
        drop: dict[int, bytes | None] = {}
        for t in txd.textures:
            nat = bytes(data[slice(*spans[t.idx])])
            if hashlib.sha256(nat).digest() in shared:
                drop[t.idx] = None
                r.rows.append(Row(r.item.rel, t.name, "share", fmt_label(t.d3dfmt, t.alpha, t.w, t.h, t.levels),
                                  f"-> {sstem}.txd", len(nat), 0))
                moved.setdefault(r.item.rel, []).append(t.name)
        if drop:
            r.out = rebuild(data, drop)
            r.seal(keep_data=True)
    natives = [blobs[k][1] for k in sorted(shared, key=lambda k: (blobs[k][0], k))]
    ide: dict[str, list[tuple[str, str]]] = {}
    for stem, r in sorted(eligible.items()):
        if r.item.rel in moved:
            first = usage.verdict(stem).models[0]
            ide.setdefault(usage.mod_ide_of[first], []).append((stem, sstem))
    conts = {r.item.container for r in eligible.values() if r.item.rel in moved}
    return Share(txd_chunk(natives), sstem, moved, ide, next(iter(conts)) if len(conts) == 1 else None)


# --------------------------------------------------------------------------- output


def _out_dir(name: str) -> Path:
    if not OUT_NAME.match(name or ""):
        raise SatkError("BAD_PARAMS", f"bad output name {name!r}",
                        hint="letters, digits, '_', '-', '.'; at most 64 characters, e.g. --out mymod_small")
    return work("out", "txdopt", name)


def _img_version(bundle: Bundle, container: str) -> int:
    from ..core.paths import open_ro

    p = bundle.path if bundle.kind == "img" else (bundle.path / container if bundle.path else None)
    try:
        with open_ro(p) as f:
            return 2 if f.read(4) == b"VER2" else 1
    except (OSError, TypeError):
        return 0


def _write_img(out: Path, bundle: Bundle, container: str, replaced: dict[str, bytes], extra: dict[str, bytes],
               warn: list[str]) -> Path | None:
    from ..rw.img import ImgBuildError, Source, build_img

    entries = [i for i in bundle.items if i.container == container]
    sources = []
    for it in entries:
        if it.rel in replaced:
            data = replaced[it.rel]
            sources.append(Source(it.name, len(data), lambda d=data: d, f"txdopt:{it.rel}"))
        else:
            sources.append(Source(it.name, it.size, it.read, f"copy:{it.rel}"))
    for name, data in sorted(extra.items()):
        sources.append(Source(name, len(data), lambda d=data: d, f"txdopt:{name}"))
    target = out / container
    try:
        res = build_img(target, sources, version=2)
    except ImgBuildError as e:
        warn.append(f"UNSUPPORTED: {container}: cannot rebuild the archive ({e}); TXDs written as loose files")
        return None
    warn.extend(res.get("warn", []))
    return target


def _readme(name: str, label: str, o: Opts, summary: dict, files: list[str], share: dict | None) -> str:
    lines = [
        f"{name} -- textures optimised by satk (San Andreas ToolKit): satk texture optimize",
        "",
        f"source: {label}",
        f"settings: max={o.max or 'keep'} pot={o.pot} mips={o.mips} dxt={o.dxt} drop_unused={o.drop_unused} "
        f"dedupe={o.dedupe} share={o.share} keep={','.join(o.keep) or '-'}",
        f"size: {summary['bytes_before']} -> {summary['bytes_after']} bytes ({summary['saved_pct']} % saved); "
        f"psnr min {summary.get('psnr_min', '-')} dB",
        "",
        "Install: replace the mod's files with the files below (same relative paths). With Mod Loader a new",
        "folder in modloader/ with a higher priority works too. Every texture is listed in txdopt.json.",
        "",
        "files:",
        *[f"  {f}" for f in files],
    ]
    if share:
        lines += ["", f"shared parent TXD: {share['txd']} ({share['textures']} textures); the game finds them through",
                  "txdp lines appended to these IDE copies (the game must load the IDE: keep it where the mod had it):",
                  *[f"  {ide}: {', '.join(f'{c} -> {p}' for c, p in pairs)}" for ide, pairs in share["ide"].items()]]
    return "\n".join(lines) + "\n"


def _default_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip("._-")[:64]
    return s or "txdopt"


def optimize(target: str, o: Opts, out: str | None = None, limit: int = 20) -> dict:
    """Run the optimisation (see the module docstring) and return the table envelope."""
    from ..core.registry import report_progress
    from .inputs import load_bundle
    from .usage import Usage, keep_matcher

    if o.share:
        o.dedupe = True
    if out is not None and not OUT_NAME.match(out):
        raise SatkError("BAD_PARAMS", f"bad output name {out!r}",
                        hint="letters, digits, '_', '-', '.'; at most 64 characters, e.g. --out mymod_small")
    with load_bundle(target, o.profile) as bundle:
        warn: list[str] = list(bundle.warn)
        txd_items = bundle.of("txd")
        if not txd_items:
            raise SatkError("NOT_FOUND", f"no TXD in {bundle.label}", hint="give a .txd, a mod folder, .zip or .img")
        usage = Usage(bundle, o.profile) if (o.drop_unused or o.share) else None
        try:
            keep = keep_matcher(list(o.keep))
            enc = Encoder(o)
            results: list[TxdResult] = []
            items = sorted(txd_items, key=lambda x: x.rel.lower())
            for i, it in enumerate(items):
                report_progress(i, len(items), it.rel)
                got = _read_txd(it, warn)
                if got is None:
                    continue
                res = _process_txd(it, got[0], got[1], enc, usage, keep, o, warn)
                res.seal(keep_data=o.share)
                results.append(res)
            name = out or _default_name(bundle.name)
            share = _plan_share(results, usage, o, name, warn) if o.share else None
            if usage is not None:
                warn.extend(usage.warn)
                undecided = sorted({r.item.stem for r in results
                                    if o.drop_unused and usage.verdict(r.item.stem).names is None})
                if undecided:
                    warn.append(f"KEPT_WHOLE: {len(undecided)} TXD(s) without known users keep every texture "
                                f"({usage.verdict(undecided[0]).why}): {', '.join(undecided[:8])}"
                                + (" ..." if len(undecided) > 8 else ""))
            return _finish(bundle, results, share, o, name, limit, warn)
        finally:
            if usage is not None:
                usage.close()


def _write_share(d: Path, bundle: Bundle, share: Share, img_repl: dict, img_extra: dict, written: list[str]) -> dict:
    fname = f"{share.stem}.txd"
    if share.container is not None:
        img_extra.setdefault(share.container, {})[fname] = share.txd
        img_repl.setdefault(share.container, {})
    else:
        dirs = {rel.rsplit("/", 1)[0] if "/" in rel else "" for rel in share.moved}
        rel = (dirs.pop() + "/" if len(dirs) == 1 and "" not in dirs else "") + fname
        atomic_write(d / rel, share.txd)
        written.append(rel)
        fname = rel
    for ide_rel, pairs in sorted(share.ide.items()):
        it = next(i for i in bundle.items if i.rel == ide_rel)
        text = it.read().decode("latin-1")
        nl = "\r\n" if "\r\n" in text else "\n"
        if text and not text.endswith(("\n", "\r")):
            text += nl
        add = nl.join(["# txdp added by satk texture optimize --share", "txdp",
                       *[f"{c}, {p}" for c, p in pairs], "end"]) + nl
        atomic_write(d / ide_rel, text + add, encoding="latin-1")
        written.append(ide_rel)
    return {"txd": fname, "textures": len(parse_txd(share.txd).textures), "bytes": len(share.txd),
            "ide": {k: [list(p) for p in v] for k, v in sorted(share.ide.items())}}


def _finish(bundle: Bundle, results: list[TxdResult], share: Share | None, o: Opts, name: str, limit: int,
            warn: list[str]) -> dict:
    from ..core.envelope import table

    d = _out_dir(name)
    written: list[str] = []
    img_repl: dict[str, dict[str, bytes]] = {}
    img_extra: dict[str, dict[str, bytes]] = {}
    for r in results:
        if not r.changed:
            continue
        _verify(r.out, r.item.rel)
        if r.item.container is not None:
            img_repl.setdefault(r.item.container, {})[r.item.rel] = r.out
        else:
            atomic_write(d / r.item.rel, r.out)
            written.append(r.item.rel)
    share_info = None
    rows = [row for r in results for row in r.rows]
    if share is not None:
        _verify(share.txd, share.stem)
        share_info = _write_share(d, bundle, share, img_repl, img_extra, written)
        rows.append(Row(share_info["txd"], f"({share_info['textures']} shared)", "share-parent", "-",
                        f"{share_info['textures']} textures", 0, len(share.txd)))
    for cont in sorted(img_repl):
        if _img_version(bundle, cont) != 2:
            warn.append(f"UNSUPPORTED: {cont}: not a VER2 archive; its TXDs are written as loose files")
            for rel, data in sorted(img_repl[cont].items()):
                p = d / Path(cont).with_suffix("").name / rel.rsplit("/", 1)[-1]
                atomic_write(p, data)
                written.append(p.relative_to(d).as_posix())
            for fname, data in sorted(img_extra.get(cont, {}).items()):
                atomic_write(d / fname, data)
                written.append(fname)
            continue
        if _write_img(d, bundle, cont, img_repl[cont], img_extra.get(cont, {}), warn) is not None:
            written.append(cont)
    if o.full:
        done = set(written)
        for it in bundle.items:
            if it.container is not None or it.rel in done:
                continue
            try:
                atomic_write(d / it.rel, it.read())
                written.append(it.rel)
            except SatkError as e:
                warn.append(f"UNREADABLE: {it.rel}: {e.msg}")
    kept_rows = [row for r in results for row in r.keep_rows]
    before = sum(r.size for r in results)
    after = sum(r.size_after for r in results)
    sbefore = sum(stream_size(r.size) for r in results)
    safter = sum(stream_size(r.size_after) for r in results)
    if share_info:
        after += share_info["bytes"]
        safter += stream_size(share_info["bytes"])
    psnrs = [r.psnr for r in rows if r.psnr is not None and r.psnr < 100.0]
    acts = [a for r in rows for a in r.action.split("+")]
    summary = {
        "txds": len(results), "changed_txds": sum(1 for r in results if r.changed),
        "textures": sum(len(r.txd.textures) for r in results if r.txd is not None),
        "dropped": acts.count("drop"), "deduped": acts.count("dedupe"), "shared": acts.count("share"),
        "recompressed": acts.count("dxt") + acts.count("dxt1") + acts.count("fmt"),
        "resized": acts.count("resize") + acts.count("pot"), "mips_added": acts.count("mips"),
        "bytes_before": before, "bytes_after": after, "saved": before - after,
        "saved_pct": round(100.0 * (before - after) / before, 1) if before else 0.0,
        "stream_before": sbefore, "stream_after": safter,
    }
    if psnrs:
        summary["psnr_min"] = min(psnrs)
        summary["psnr_mean"] = round(sum(psnrs) / len(psnrs), 2)
    if share is None:
        cross = _cross_duplicates(results)
        if cross:
            n, b, names = cross
            warn.append(f"SHAREABLE: {n} texture(s) are byte-identical in several TXDs ({', '.join(names[:5])}): "
                        f"--share would save about {b} bytes through a txdp parent TXD")
    low = sorted((r for r in rows if r.psnr is not None and r.psnr < LOW_PSNR), key=lambda r: r.psnr)
    for r in low[:5]:
        warn.append(f"LOW_PSNR: {r.txd}: {r.name}: {r.psnr} dB after {r.action} (--quality high, a larger --max "
                    "or --dxt keep)")
    lint = _lint(d, written)
    rows.sort(key=lambda r: (-r.saved, r.txd.lower(), r.name.lower()))
    if not rows:
        tips = [t for t, on in (("--max 512", o.max), ("--mips", o.mips), ("--drop-unused", o.drop_unused),
                                ("--dedupe", o.dedupe)) if not on]
        warn.append("NOTHING: every texture is already optimal for these settings"
                    + (f" (try {', '.join(tips)})" if tips else ""))
    report = {"source": bundle.label, "kind": bundle.kind, "settings": _settings(o), "summary": summary,
              "share": share_info, "lint": lint, "files": sorted(written), "cols": COLS,
              "rows": [r.cells() for r in rows], "unchanged": [[r.txd, r.name, r.before] for r in kept_rows],
              "warn": warn}
    rp = atomic_write(d / REPORT, json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    atomic_write(d / README, _readme(name, bundle.label, o, summary, sorted(written), share_info))
    mine = set(written) | {REPORT, README}
    stale = sorted(q for q in (p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()) if q not in mine)
    if stale:
        warn.append(f"STALE: {len(stale)} file(s) in {jpath(d)} are from earlier runs: {', '.join(stale[:5])}")
    lim = max(1, int(limit))
    env = table(COLS, [r.cells() for r in rows[:lim]], total=len(rows), warn=list(dict.fromkeys(warn))[:30])
    env.update(out=jpath(d), report=jpath(rp), source=bundle.label, summary=summary)
    if lint is not None:
        env["lint"] = lint
    if share_info:
        env["share"] = share_info
    return env


def _cross_duplicates(results: list[TxdResult]) -> tuple[int, int, list[str]] | None:
    """``(textures, bytes saved by sharing, names)`` of output natives identical in two or more TXDs."""
    seen: dict[bytes, list] = {}
    for r in results:
        for k, n, name in r.sig:
            seen.setdefault(k, [set(), n, name])[0].add(r.item.stem)
    multi = [v for v in seen.values() if len(v[0]) > 1]
    if not multi:
        return None
    return len(multi), sum((len(v[0]) - 1) * v[1] for v in multi), sorted({v[2] for v in multi})


def _verify(data: bytes, rel: str) -> Txd:
    try:
        return parse_txd(data)
    except FormatError as e:  # pragma: no cover - a writer bug
        raise SatkError("INTERNAL", f"the written TXD {rel} does not parse: {e}") from None


def _settings(o: Opts) -> dict:
    return {"max": o.max, "pot": o.pot, "mips": o.mips, "dxt": o.dxt, "drop_unused": o.drop_unused,
            "dedupe": o.dedupe, "share": o.share, "keep": list(o.keep), "quality": o.quality, "profile": o.profile}


def _lint(d: Path, written: list[str]) -> dict | None:
    """``txd.*`` lint summary of the written TXDs/IMGs (fatal/error/warn/info counts)."""
    targets = [w for w in written if w.lower().endswith((".txd", ".img"))]
    if not targets:
        return None
    try:
        from ..lint.runner import lint

        total: dict[str, int] = {}
        for t in targets:
            rep = lint(str(d / t), only=["txd"], use_index=False)
            for k, v in rep.summary.items():
                total[k] = total.get(k, 0) + v
        return {k: v for k, v in total.items() if v} or {"findings": 0}
    except Exception as e:  # noqa: BLE001 - lint is a check, never a reason to fail the write
        return {"error": f"{type(e).__name__}: {e}"}
