"""DXT1/DXT3/DXT5 (BC1/BC2/BC3) block encoder in numpy (owner M2-03).

The inverse of :mod:`satk.formats.dxt`: an ``(h, w, 4)`` uint8 RGBA image -> the block stream of one mip
level, bit-exact with the decoder used everywhere in satk (565 expansion ``(v << 3) | (v >> 2)``, integer
interpolation ``(2*p0 + p1) // 3``), so the error the encoder minimises is the error the game shows.

Colour endpoints (all blocks at once, chunks of :data:`CHUNK` blocks):

1. candidates: the principal axis of the block colours (PCA, power iteration), its min/max projections
   and the same segment inset by 1/16;
2. least-squares refinement: for the current indices solve the 2x2 normal equations for both endpoints,
   quantise to 565, re-assign indices, keep the result when the block error went down (``normal``: 2
   rounds, ``high``: 3 rounds);
3. ``high`` only: +-1 steps on each 565 channel of both endpoints (2 passes).

Alpha:

* ``DXT1`` with ``alpha=True`` (1-bit): blocks with a pixel below :data:`ALPHA_CUT` use the 3-colour mode
  (``c0 <= c1``, index 3 = transparent black), their endpoints are fitted to the opaque pixels only;
  other blocks use the 4-colour mode (``c0 > c1``). ``alpha=False`` never emits index 3 in 3-colour mode;
* ``DXT3``: explicit 4-bit alpha, ``round(a * 15 / 255)``;
* ``DXT5``: interpolated alpha. Both block modes are tried -- 8 values (``a0 > a1``, endpoints min/max plus
  one least-squares step) and 6 values plus exact 0/255 (``a0 <= a1``) -- and the smaller error wins.

numpy is imported inside the functions only (SPEC §2.3).
"""

from __future__ import annotations

__all__ = ["QUALITIES", "DXT_FORMATS", "ALPHA_CUT", "CHUNK", "block_count", "dxt_size", "to_blocks", "encode_dxt"]

QUALITIES = ("fast", "normal", "high")
DXT_FORMATS = ("DXT1", "DXT3", "DXT5")
#: DXT1 with 1-bit alpha: pixels with ``a < ALPHA_CUT`` become transparent.
ALPHA_CUT = 128
#: Blocks encoded per numpy pass (bounds the temporary arrays to a few tens of MB).
CHUNK = 16384

_W4 = (1.0, 0.0, 2.0 / 3.0, 1.0 / 3.0)   # weight of endpoint 0 per index, 4-colour mode
_W3 = (1.0, 0.0, 0.5, 0.0)               # 3-colour mode (index 3 = transparent, weight unused)
_SWAP4 = (1, 0, 3, 2)
_SWAP3 = (1, 0, 2, 3)
_A8W = (1.0, 0.0, 6 / 7, 5 / 7, 4 / 7, 3 / 7, 2 / 7, 1 / 7)
_A8SWAP = (1, 0, 7, 6, 5, 4, 3, 2)


def _np():
    from ..core.errors import require_module

    return require_module("numpy", purpose="DXT encoding (satk texture pack/replace)")


def block_count(w: int, h: int) -> int:
    """Number of 4x4 blocks of a ``w x h`` level (at least one)."""
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4)


def dxt_size(fmt: str, w: int, h: int) -> int:
    """Bytes of one ``DXT1|DXT3|DXT5`` level (8 or 16 bytes per block)."""
    return block_count(w, h) * (8 if fmt == "DXT1" else 16)


def to_blocks(img):
    """``(h, w, 4)`` uint8 -> ``(nblocks, 16, 4)`` in block order (rows of blocks, pixel ``4*y + x``).

    Partial blocks (sides not a multiple of 4) are padded by repeating the edge pixels.
    """
    np = _np()
    h, w = img.shape[:2]
    bh, bw = max(1, (h + 3) // 4), max(1, (w + 3) // 4)
    if (bh * 4, bw * 4) != (h, w):
        img = np.pad(img, ((0, bh * 4 - h), (0, bw * 4 - w), (0, 0)), mode="edge")
    return img.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * bw, 16, 4)


# ----------------------------------------------------------------------------- colour helpers


def _quant(np, e):
    """Float RGB endpoints (n, 3) -> 565 components (n, 3) int32."""
    q = np.rint(e * np.array([31 / 255, 63 / 255, 31 / 255], dtype=np.float32))
    return np.clip(q, 0, np.array([31, 63, 31])).astype(np.int32)


def _expand(np, q):
    out = np.empty_like(q)
    out[:, 0] = (q[:, 0] << 3) | (q[:, 0] >> 2)
    out[:, 1] = (q[:, 1] << 2) | (q[:, 1] >> 4)
    out[:, 2] = (q[:, 2] << 3) | (q[:, 2] >> 2)
    return out


def _pack565(q):
    return (q[:, 0] << 11) | (q[:, 1] << 5) | q[:, 2]


def _palette(np, q0, q1, three):
    p0, p1 = _expand(np, q0), _expand(np, q1)
    t = three[:, None]
    pal = np.empty((q0.shape[0], 4, 3), dtype=np.int32)
    pal[:, 0] = p0
    pal[:, 1] = p1
    pal[:, 2] = np.where(t, (p0 + p1) // 2, (2 * p0 + p1) // 3)
    pal[:, 3] = np.where(t, 0, (p0 + 2 * p1) // 3)
    return pal


def _assign(np, X, W, q0, q1, three):
    """Nearest palette entry per pixel -> (idx (n, 16), weighted squared error (n,))."""
    pal = _palette(np, q0, q1, three).astype(np.float32)
    d = ((X[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)          # (n, 16, 4)
    d[:, :, 3] = np.where(three[:, None], np.float32(np.inf), d[:, :, 3])
    idx = d.argmin(-1)
    err = (np.take_along_axis(d, idx[..., None], -1)[..., 0] * W).sum(-1)
    return idx, err


def _keep_better(np, best, cand):
    """Per block, take ``cand`` where its error is lower. Both are (q0, q1, idx, err)."""
    better = cand[3] < best[3]
    if not better.any():
        return best
    b1 = better[:, None]
    return (np.where(b1, cand[0], best[0]), np.where(b1, cand[1], best[1]),
            np.where(b1, cand[2], best[2]), np.where(better, cand[3], best[3]))


def _pca(np, X, W):
    """Endpoints of the principal axis segment covering the weighted pixels -> (hi, lo) float (n, 3)."""
    sw = W.sum(1)
    has = sw > 0
    mean = (X * W[..., None]).sum(1) / np.where(has, sw, 1)[:, None]
    D = (X - mean[:, None, :]) * W[..., None]
    C = np.einsum("nki,nkj->nij", D, D)
    ch = np.argmax(np.einsum("nii->ni", C), axis=1)
    v = np.take_along_axis(C, ch[:, None, None].repeat(3, axis=2), axis=1)[:, 0, :]
    for _ in range(6):
        v = np.einsum("nij,nj->ni", C, v)
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), np.float32(1e-12))
    t = ((X - mean[:, None, :]) * v[:, None, :]).sum(-1)
    big = np.float32(1e9)
    tmin = np.where(W > 0, t, big).min(1)
    tmax = np.where(W > 0, t, -big).max(1)
    tmin = np.where(has, tmin, 0)[:, None]
    tmax = np.where(has, tmax, 0)[:, None]
    hi = np.clip(mean + tmax * v, 0, 255)
    lo = np.clip(mean + tmin * v, 0, 255)
    return hi, lo


def _ls(np, X, W, idx, three, q0, q1):
    """Least-squares endpoints for fixed indices (float (n, 3) each); degenerate blocks keep q0/q1."""
    w4 = np.asarray(_W4, dtype=np.float32)[idx]
    w3 = np.asarray(_W3, dtype=np.float32)[idx]
    a = np.where(three[:, None], w3, w4) * W
    b = (1 - np.where(three[:, None], w3, w4)) * W
    saa, sbb, sab = (a * a).sum(1), (b * b).sum(1), (a * b).sum(1)
    sax = (a[..., None] * X).sum(1)
    sbx = (b[..., None] * X).sum(1)
    det = saa * sbb - sab * sab
    ok = np.abs(det) > 1e-4
    d = np.where(ok, det, 1)[:, None]
    e0 = (sax * sbb[:, None] - sbx * sab[:, None]) / d
    e1 = (sbx * saa[:, None] - sax * sab[:, None]) / d
    e0 = np.where(ok[:, None], np.clip(e0, 0, 255), _expand(np, q0))
    e1 = np.where(ok[:, None], np.clip(e1, 0, 255), _expand(np, q1))
    return e0, e1


def _colour(np, X, W, three, quality: str):
    """Best (q0, q1, idx) for the colour part of each block."""
    hi, lo = _pca(np, X, W)

    def ev(e0, e1):
        q0, q1 = _quant(np, e0), _quant(np, e1)
        idx, err = _assign(np, X, W, q0, q1, three)
        return q0, q1, idx, err

    best = ev(hi, lo)
    if quality == "fast":
        return best[:3]
    inset = (hi - lo) / 16
    best = _keep_better(np, best, ev(hi - inset, lo + inset))
    for _ in range(3 if quality == "high" else 2):
        e0, e1 = _ls(np, X, W, best[2], three, best[0], best[1])
        best = _keep_better(np, best, ev(e0, e1))
    if quality == "high":
        lim = np.array([31, 63, 31])
        for _ in range(2):
            for which in (0, 1):
                for c in range(3):
                    for step in (-1, 1):
                        q = best[which].copy()
                        q[:, c] = np.clip(q[:, c] + step, 0, lim[c])
                        q0, q1 = (q, best[1]) if which == 0 else (best[0], q)
                        idx, err = _assign(np, X, W, q0, q1, three)
                        best = _keep_better(np, best, (q0, q1, idx, err))
    return best[:3]


def _colour_blocks(np, B, *, punch: bool, quality: str):
    """(n, 16, 4) uint8 blocks -> (n, 8) uint8 BC1 colour blocks."""
    n = B.shape[0]
    X = B[:, :, :3].astype(np.float32)
    if punch:
        transparent = B[:, :, 3] < ALPHA_CUT
        three = transparent.any(1)
        W = (~transparent).astype(np.float32)
    else:
        transparent = None
        three = np.zeros(n, dtype=bool)
        W = np.ones((n, 16), dtype=np.float32)
    q0, q1, idx = _colour(np, X, W, three, quality)
    v0, v1 = _pack565(q0), _pack565(q1)
    # 4-colour blocks need c0 > c1, 3-colour blocks c0 <= c1: swap endpoints and remap the indices.
    swap = np.where(three, v0 > v1, v0 < v1)
    idx = np.where(swap[:, None], np.where(three[:, None], np.asarray(_SWAP3)[idx], np.asarray(_SWAP4)[idx]), idx)
    v0, v1 = np.where(swap, v1, v0), np.where(swap, v0, v1)
    idx = np.where(((v0 == v1) & ~three)[:, None], 0, idx)
    if transparent is not None:
        idx = np.where(transparent, 3, idx)
        full = transparent.all(1)                     # nothing opaque: c0 = c1 = 0, all transparent
        v0, v1 = np.where(full, 0, v0), np.where(full, 0, v1)
    bits = (idx.astype(np.uint32) << (2 * np.arange(16, dtype=np.uint32))).sum(1, dtype=np.uint32)
    out = np.empty((n, 4), dtype="<u2")
    out[:, 0], out[:, 1] = v0, v1
    out[:, 2], out[:, 3] = bits & 0xFFFF, bits >> 16
    return out.view(np.uint8).reshape(n, 8)


# ----------------------------------------------------------------------------- alpha


def _alpha_dxt3(np, A):
    n = A.shape[0]
    a4 = ((A.astype(np.int32) * 15 + 127) // 255).astype(np.uint64)
    bits = (a4 << (np.uint64(4) * np.arange(16, dtype=np.uint64))).sum(1, dtype=np.uint64)
    return bits.astype("<u8").view(np.uint8).reshape(n, 8)


def _apal(np, a0, a1, six):
    """Alpha palettes (n, 8): 8-value mode, or 6 values + 0/255 where ``six``."""
    a0 = a0.astype(np.int32)[:, None]
    a1 = a1.astype(np.int32)[:, None]
    i7 = np.arange(1, 7, dtype=np.int32)
    i5 = np.arange(1, 5, dtype=np.int32)
    seven = ((7 - i7) * a0 + i7 * a1) // 7
    five = ((5 - i5) * a0 + i5 * a1) // 5
    n = a0.shape[0]
    pal = np.empty((n, 8), dtype=np.int32)
    pal[:, 0:1], pal[:, 1:2] = a0, a1
    s = six[:, None]
    pal[:, 2:6] = np.where(s, five, seven[:, :4])
    pal[:, 6] = np.where(six, 0, seven[:, 4])
    pal[:, 7] = np.where(six, 255, seven[:, 5])
    return pal


def _aassign(np, A, pal):
    d = (A[:, :, None] - pal[:, None, :]) ** 2
    idx = d.argmin(-1)
    return idx, np.take_along_axis(d, idx[..., None], -1)[..., 0].sum(-1)


def _alpha_dxt5(np, A, quality: str):
    n = A.shape[0]
    A = A.astype(np.int32)
    amax, amin = A.max(1), A.min(1)
    no6 = np.zeros(n, dtype=bool)
    i8, e8 = _aassign(np, A, _apal(np, amax, amin, no6))
    a0, a1 = amax, amin
    if quality != "fast":                            # one least-squares step on the 8-value mode
        w = np.asarray(_A8W, dtype=np.float64)[i8]
        u = 1 - w
        saa, sbb, sab = (w * w).sum(1), (u * u).sum(1), (w * u).sum(1)
        sax, sbx = (w * A).sum(1), (u * A).sum(1)
        det = saa * sbb - sab * sab
        ok = np.abs(det) > 1e-6
        d = np.where(ok, det, 1)
        r0 = np.clip(np.rint((sax * sbb - sbx * sab) / d), 0, 255).astype(np.int32)
        r1 = np.clip(np.rint((sbx * saa - sax * sab) / d), 0, 255).astype(np.int32)
        n0, n1 = np.maximum(r0, r1), np.minimum(r0, r1)
        ok &= n0 > n1
        j8, f8 = _aassign(np, A, _apal(np, n0, n1, no6))
        take = ok & (f8 < e8)
        a0, a1 = np.where(take, n0, a0), np.where(take, n1, a1)
        i8, e8 = np.where(take[:, None], j8, i8), np.where(take, f8, e8)
    inner = (A > 0) & (A < 255)
    has = inner.any(1)
    lo = np.where(has, np.where(inner, A, 255).min(1), 0)
    hi = np.where(has, np.where(inner, A, 0).max(1), 0)
    six = np.ones(n, dtype=bool)
    i6, e6 = _aassign(np, A, _apal(np, lo, hi, six))
    use6 = e6 < e8
    a0, a1 = np.where(use6, lo, a0), np.where(use6, hi, a1)
    idx = np.where(use6[:, None], i6, i8)
    bits = (idx.astype(np.uint64) << (np.uint64(3) * np.arange(16, dtype=np.uint64))).sum(1, dtype=np.uint64)
    out = np.empty((n, 8), dtype=np.uint8)
    out[:, 0], out[:, 1] = a0, a1
    out[:, 2:8] = bits.astype("<u8").view(np.uint8).reshape(n, 8)[:, :6]
    return out


# ----------------------------------------------------------------------------- public


def encode_dxt(img, fmt: str, *, alpha: bool = False, quality: str = "normal") -> bytes:
    """Encode one level.

    Args:
        img: ``(h, w, 4)`` uint8 RGBA (numpy array, C order).
        fmt: ``DXT1``, ``DXT3`` or ``DXT5``.
        alpha: DXT1 only: 1-bit alpha (``a < ALPHA_CUT`` -> transparent); ignored otherwise.
        quality: ``fast`` (PCA only), ``normal`` (+ least squares), ``high`` (+ endpoint search).

    Returns the raw block stream (:func:`dxt_size` bytes).
    """
    np = _np()
    if fmt not in DXT_FORMATS:
        raise ValueError(f"not a DXT format: {fmt!r}")
    if quality not in QUALITIES:
        raise ValueError(f"unknown quality {quality!r}; expected one of {QUALITIES}")
    blocks = to_blocks(np.ascontiguousarray(img, dtype=np.uint8))
    parts = []
    for k in range(0, blocks.shape[0], CHUNK):
        B = blocks[k:k + CHUNK]
        col = _colour_blocks(np, B, punch=(fmt == "DXT1" and alpha), quality=quality)
        if fmt == "DXT1":
            parts.append(col)
        elif fmt == "DXT3":
            parts.append(np.concatenate([_alpha_dxt3(np, B[:, :, 3]), col], axis=1))
        else:
            parts.append(np.concatenate([_alpha_dxt5(np, B[:, :, 3], quality), col], axis=1))
    return b"".join(p.tobytes() for p in parts)
