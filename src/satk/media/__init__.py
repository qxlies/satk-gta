"""satk.media — texture PNGs, contact sheets, top-down map images (SPEC §4.4, owner WP-04).

Python API (used by ``view``, ``blender``, MCP)::

    from satk.media import texture_png, contact_sheet, map_image, export_all

    texture_png("tex:bistro/vent_64")                     # -> work/cache/tex/<hh>/<pix>.png
    contact_sheet(["txd:lawest1"], cols=4)                 # -> (work/out/sheets/<slug>-<key>.png, legend)
    map_image(2495, -1687, span=300)                       # -> (work/out/maps/map_...png, legend, m_per_px)
    export_all(jobs=12)                                    # -> {"written", "skipped", "seconds", "backend", ...}

Modules: :mod:`.texture` (decode + content-addressed cache), :mod:`.sheet`, :mod:`.mapplot`,
:mod:`.png` (Pillow or stdlib zlib), :mod:`.raster` (drawing), :mod:`.font` (built-in bitmap font).
Pillow/numpy are imported only inside functions (SPEC §2.3).
"""

from __future__ import annotations

__all__ = ["texture_png", "contact_sheet", "map_image", "export_all"]


def __getattr__(name: str):  # lazy: keep `import satk.media` cheap
    if name in ("texture_png", "export_all"):
        from . import texture

        return getattr(texture, name)
    if name == "contact_sheet":
        from .sheet import contact_sheet

        return contact_sheet
    if name == "map_image":
        from .mapplot import map_image

        return map_image
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
