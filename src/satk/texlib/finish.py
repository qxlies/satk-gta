"""Periodic colour grading, photo-like grain and a real DXT round trip.

The shader bake supplies the material structure. Finishing adjusts its value,
saturation and grain to measured vanilla role distributions, with wrap-around
filtering throughout. Both the source and the compressed preview must pass the
same judge used by style.texture before a bundle can be published.
"""

from __future__ import annotations

from ..core.errors import SatkError, require_module


def _blur(a):
    import numpy as np

    return (4 * a + np.roll(a, 1, 0) + np.roll(a, -1, 0)
            + np.roll(a, 1, 1) + np.roll(a, -1, 1)) / 8


def _field(rng, size, cells):
    """Periodic smooth noise, sampled at pixel centres."""
    import numpy as np

    grid = rng.normal(0, 1, (cells, cells))
    x = (np.arange(size) + 0.5) * cells / size
    ix = np.floor(x).astype(int)
    f = x - ix
    f = f * f * (3 - 2 * f)
    a = grid[ix[:, None] % cells, ix[None, :] % cells]
    b = grid[ix[:, None] % cells, (ix[None, :] + 1) % cells]
    c = grid[(ix[:, None] + 1) % cells, ix[None, :] % cells]
    d = grid[(ix[:, None] + 1) % cells, (ix[None, :] + 1) % cells]
    out = (a * (1 - f) + b * f) * (1 - f[:, None]) + (c * (1 - f) + d * f) * f[:, None]
    return (out - out.mean()) / max(float(out.std()), 1e-6)


def dxt_roundtrip(rgba, fmt):
    """Encode using satk's TXD writer and decode using the game's format reader."""
    from ..formats.dxt import decode_rgba
    from ..formats.txd import mip0_bytes, parse_txd
    from ..texmod.encode import encode_level, to_array
    from ..texmod.txdwrite import NativeSpec, native_chunk, txd_chunk

    h, w = rgba.shape[:2]
    blocks = encode_level(rgba, fmt, alpha=fmt == "DXT3", quality="normal")
    data = txd_chunk([native_chunk(NativeSpec("preview", fmt, w, h, (blocks,), fmt == "DXT3"))])
    tex = parse_txd(data).textures[0]
    return to_array(w, h, decode_rgba(tex, mip0_bytes(data, tex)))


def seams(rgba) -> dict:
    """Wrap-edge strength / p95 interior line strength; joints can cross tile edges."""
    import numpy as np

    a = rgba.astype(float)
    # Premultiplication makes this meaningful for overlays too.
    a = np.concatenate((a[..., :3] * a[..., 3:4] / 255, a[..., 3:4]), axis=2)
    dx = float(np.quantile(np.abs(a[:, 1:] - a[:, :-1]).mean(axis=(0, 2)), 0.95))
    dy = float(np.quantile(np.abs(a[1:] - a[:-1]).mean(axis=(1, 2)), 0.95))
    return {"x": round(float(np.abs(a[:, 0] - a[:, -1]).mean()) / max(dx, 0.1), 3),
            "y": round(float(np.abs(a[0] - a[-1]).mean()) / max(dy, 0.1), 3)}


def finish(baked: dict, recipe: dict, size: int, seed: int, tint, dist: dict):
    np = require_module("numpy", purpose="procedural texture finishing")
    Image = require_module("PIL.Image", purpose="procedural texture finishing")
    from ..style.texture import judge_texture, texture_stats
    from ..texmod.encode import psnr

    with Image.open(baked["colour"]) as im:
        raw = np.asarray(im.convert("RGB").resize((size, size), Image.Resampling.BOX), dtype=float)
    raw = _blur(raw)
    peer = dist["roles"][recipe["role"]]["metrics"]
    rng = np.random.default_rng(seed)
    lum = raw @ np.array([0.299, 0.587, 0.114])
    structure = (lum - lum.mean()) / max(float(lum.std()), 1.0)
    blotch = _field(rng, size, 8)
    grain = rng.normal(0, 1, (size, size))
    # Keep material-specific colour islands unless the user supplies a hue.
    if tint is not None:
        base = np.array(tint, dtype=float)
        if base.max() == base.min():
            base = np.array(recipe["base"], dtype=float)
        chroma = np.broadcast_to(base / max(float(base.max()), 1e-6) - 1, raw.shape).copy()
    else:
        chroma = raw / np.maximum(raw.max(axis=2, keepdims=True), 1) - 1
    saturation = peer["tex.sat_mean"][1]
    chroma *= saturation / max(float((-chroma.min(axis=2)).mean()), 0.001)
    chroma += _field(rng, size, 5)[..., None] * np.array([0.006, -0.004, 0.003])
    chroma = np.clip(chroma, -0.85, 0)
    target_value = float(recipe.get("value", peer["tex.val_mean"][1])) * 255
    # Contrast belongs in the shader structure; final grain survives low-resolution DXT.
    contrast = peer["tex.lum_std"][1]
    detail = 0.8 * contrast * structure + 0.2 * contrast * blotch
    # Fine pores already carry energy. Soften them before adding photographic
    # grain instead of blindly adding both past a narrow role's HF ceiling.
    for _ in range(8):
        lap = 4 * detail - np.roll(detail, 1, 0) - np.roll(detail, -1, 0) - np.roll(detail, 1, 1) - np.roll(detail, -1, 1)
        if float(np.abs(lap).mean()) <= peer["tex.hf_energy"][1] * 0.55:
            break
        detail = _blur(detail)
    values = target_value + detail
    alpha = np.full((size, size), 255, dtype=np.uint8)
    if recipe.get("alpha"):
        with Image.open(baked["mask"]) as im:
            mask = np.asarray(im.convert("L").resize((size, size), Image.Resampling.BOX), dtype=float)
        alpha = (np.clip(np.rint(_blur(mask) / 17), 0, 15) * 17).astype(np.uint8)
    fmt = "DXT3" if recipe.get("alpha") else "DXT1"
    checks = {}
    # The narrow interior band sometimes needs coarser colour quantisation. Grain
    # adjustments are bounded and deterministic, never a successful unchecked fallback.
    for step, gain in ((1, 1.0), (2, 1.0), (3, 1.1), (4, 1.25)):
        grain_sd = peer["tex.hf_energy"][1] / 3.568 * recipe["grain"] * gain
        value = np.maximum(1, values + grain_sd * grain)
        visible = alpha > 0
        value *= target_value / max(float(value[visible].mean()), 1)
        rgb = np.clip(value[..., None] * (1 + chroma), 0, 255)
        rgb = np.clip(np.rint(rgb / step) * step, 0, 255).astype(np.uint8)
        rgba = np.dstack((rgb, alpha))
        preview = dxt_roundtrip(rgba, fmt)
        stats = {"png": texture_stats(rgba.tobytes(), size, size),
                 "dxt": texture_stats(preview.tobytes(), size, size)}
        checks = {key: judge_texture(recipe["name"], value, dist, recipe["role"]) for key, value in stats.items()}
        if all(c["verdict"] == "in" and len(c["rows"]) == 4 for c in checks.values()):
            return rgba, preview, {"format": fmt, "style": checks, "stats": stats,
                                   "seams": {"png": seams(rgba), "dxt": seams(preview)},
                                   "psnr": psnr(rgba, preview, alpha=fmt == "DXT3")}
    raise SatkError("CHECK_FAILED", f"texlib {recipe['name']}: the finished texture is outside its vanilla role band",
                    hint="inspect the metric rows; choose a different tint or seed", data={"style": checks})
