"""Radar tiles ``radar00.txd`` .. ``radar143.txd`` (``models/gta3.img``): export a mosaic, build tiles from an image.

The radar is a 12 x 12 grid over the 6000 x 6000 m world; tile ``radarN`` (``N = row * 12 + col``, no zero
padding above 99) covers ``x = -3000 + 500 * col .. +500`` and ``y = 3000 - 500 * row .. -500``: row 0 is the
north edge, column 0 the west edge. Each stock TXD holds one texture named like the file, 128 x 128 DXT1,
one mip level, filter 1 (nearest), wrap addressing, RW 3.6.0.3; :func:`tile_txd` writes the same container
(the TXD writer and DXT encoder of ``satk.texmod``, used read-only).

``--from-map`` draws a schematic map instead of reading an image: land, the profile's ``water.dat`` polygons,
outdoor building footprints from the index and car paths from the path database.

Pillow and numpy are imported inside the functions.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["GRID", "TILE", "WORLD", "tile_name", "tile_bounds", "read_tiles", "mosaic", "load_image",
           "tile_txd", "build_tiles", "decode_tile", "draw_schematic", "PALETTE"]

GRID = 12
TILE = 128
WORLD = 3000.0
#: schematic colours (RGB)
PALETTE = {"land": (118, 126, 104), "water": (78, 110, 146), "building": (92, 96, 94), "road": (176, 174, 162)}


def tile_name(i: int) -> str:
    return f"radar{i:02d}"


def tile_bounds(i: int) -> tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)`` world rectangle of tile ``i``."""
    row, col = divmod(i, GRID)
    x0 = -WORLD + 500.0 * col
    y1 = WORLD - 500.0 * row
    return x0, y1 - 500.0, x0 + 500.0, y1


def _np():
    from ..core.errors import require_module

    return require_module("numpy", purpose="radar tiles")


def _pil():
    from ..core.errors import require_module

    return require_module("PIL.Image", purpose="radar images")


def decode_tile(buf: bytes):
    """``(name, (h, w, 4) uint8 array)`` of the first texture of a TXD."""
    from ..formats.dxt import decode_rgba
    from ..formats.txd import mip0_bytes, palette_bytes, parse_txd

    np = _np()
    t = parse_txd(buf)
    if not t.textures:
        raise ValueError("TXD has no texture")
    tx = t.textures[0]
    rgba = decode_rgba(tx, mip0_bytes(buf, tx), palette_bytes(buf, tx))
    return tx.name, np.frombuffer(rgba, dtype=np.uint8).reshape(tx.h, tx.w, 4)


def read_tiles(img_path: Path) -> tuple[list, list[str]]:
    """Decoded tiles 0..143 from an IMG archive (``None`` for a missing tile) and warnings."""
    from ..formats.img import ImgArchive

    warn: list[str] = []
    tiles: list = [None] * (GRID * GRID)
    with ImgArchive.open(img_path) as a:
        names = a.by_name()
        for i in range(GRID * GRID):
            e = names.get(tile_name(i) + ".txd")
            if e is None:
                warn.append(f"MISSING: {tile_name(i)}.txd is not in {img_path.name}")
                continue
            try:
                tiles[i] = decode_tile(a.read(e))[1]
            except Exception as ex:  # noqa: BLE001 - a broken tile is reported, the rest still export
                warn.append(f"BROKEN: {tile_name(i)}.txd: {ex}")
    return tiles, warn


def mosaic(tiles: list, tile: int):
    """One ``(12 * tile)``-square RGBA array from the tiles (missing tiles black; other sizes resampled)."""
    np = _np()
    Image = _pil()
    side = GRID * tile
    out = np.zeros((side, side, 4), dtype=np.uint8)
    out[:, :, 3] = 255
    for i, t in enumerate(tiles):
        if t is None:
            continue
        if t.shape[0] != tile or t.shape[1] != tile:
            t = np.asarray(Image.fromarray(t, "RGBA").resize((tile, tile), Image.LANCZOS))
        r, c = divmod(i, GRID)
        out[r * tile:(r + 1) * tile, c * tile:(c + 1) * tile] = t
    return out


def load_image(path: Path, side: int, warn: list[str]):
    """An image file as a ``side``-square opaque RGBA array (resampled with Lanczos when needed)."""
    np = _np()
    Image = _pil()
    with Image.open(path) as im:
        w, h = im.size
        im = im.convert("RGBA")
        if (w, h) != (side, side):
            if abs(w - h) > max(w, h) * 0.01:
                warn.append(f"ASPECT: the image is {w}x{h}; it is stretched to a square (the radar covers a square)")
            im = im.resize((side, side), Image.LANCZOS)
        arr = np.array(im, dtype=np.uint8)
    if (arr[:, :, 3] < 255).any():
        warn.append("ALPHA: transparent pixels are made opaque (radar tiles are DXT1 without alpha)")
    arr[:, :, 3] = 255
    return arr


def tile_txd(name: str, px, quality: str = "normal") -> bytes:
    """A stock-like radar TXD: one ``name`` texture, DXT1, one level, filter 1, wrap."""
    from ..texmod.bc import encode_dxt
    from ..texmod.txdwrite import NativeSpec, native_chunk, txd_chunk

    h, w = px.shape[:2]
    blob = encode_dxt(px, "DXT1", alpha=False, quality=quality)
    spec = NativeSpec(name=name, fmt="DXT1", w=w, h=h, levels=(blob,), filter=1, addressing=0x11)
    return txd_chunk([native_chunk(spec)])


def build_tiles(arr, tile: int, quality: str = "normal") -> list[bytes]:
    """144 TXDs from a ``(12 * tile)``-square RGBA array."""
    out = []
    for i in range(GRID * GRID):
        r, c = divmod(i, GRID)
        sub = arr[r * tile:(r + 1) * tile, c * tile:(c + 1) * tile]
        out.append(tile_txd(tile_name(i), sub, quality))
    return out


def draw_schematic(side: int, water_polys: list, footprints: list, roads: list, layers: tuple[str, ...]):
    """A ``side``-square RGBA array: land, water polygons, building footprints and road segments.

    Args:
        water_polys: lists of ``(x, y)`` world vertices (quads in file order, triangles).
        footprints: ``(minx, miny, maxx, maxy)`` world rectangles.
        roads: ``(x0, y0, x1, y1)`` world segments.
    """
    np = _np()
    Image = _pil()
    from PIL import ImageDraw

    s = side / (2 * WORLD)

    def px(x: float, y: float) -> tuple[float, float]:
        return (x + WORLD) * s, (WORLD - y) * s

    im = Image.new("RGB", (side, side), PALETTE["land"])
    d = ImageDraw.Draw(im)
    if "water" in layers:
        for poly in water_polys:
            pts = [px(x, y) for x, y in poly]
            if len(pts) == 4:                   # file order BL, BR, TL, TR -> outline order
                pts = [pts[0], pts[1], pts[3], pts[2]]
            d.polygon(pts, fill=PALETTE["water"])
    if "buildings" in layers:
        for x0, y0, x1, y1 in footprints:
            a, b = px(x0, y1)
            c, e = px(x1, y0)
            d.rectangle([a, b, max(a, c - 1), max(b, e - 1)], fill=PALETTE["building"])
    if "roads" in layers:
        width = max(1, round(side / 640))
        for x0, y0, x1, y1 in roads:
            d.line([px(x0, y0), px(x1, y1)], fill=PALETTE["road"], width=width)
    arr = np.array(im.convert("RGBA"), dtype=np.uint8)
    return arr
