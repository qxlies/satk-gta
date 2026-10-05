"""satk.formats -- GTA:SA file format parsers, stdlib only (SPEC §4.2, owner WP-02).

Modules (import the one you need; nothing heavy is imported here):

=========  ==================================================================  =========
module     what                                                                milestone
=========  ==================================================================  =========
img        IMG v1/v2 archives (``ImgArchive``, ``ImgEntry``)                   F1
rw         RenderWare chunks (``Chunk``, ``iter_children``, ``FormatError``)    F1
txd        texture dictionaries: headers + pixel hash (``parse_txd``)          F1
dat        DAT files + case-insensitive paths (``parse_dat``, ``resolve_ci``)  F1
ide        item definitions (``parse_ide``)                                    F1
ipl        placements, text + ``bnry`` (``parse_ipl_text/binary``)             F1
layout     engine archive order ``engine|samp`` (``archives``)                 F1
pe         PE sections of ``gta_sa.exe``                                       F1
dff        models (``scan_dff``, ``decode_geometry``, ``find_embedded_col``)    F2
col        collisions COLL/COL2/COL3/COL4 (``iter_col``)                       F2
ifp        animation packages ANP3/ANPK (``parse_ifp``)                        F2
zon        zones ``map.zon``/``info.zon`` (``parse_zon``)                      F2
dxt        DXT/raster -> RGBA (pillow/numpy/python backends), previews        F3
selftest   golden-number self-test (``satk formats selftest``)                 F1-F3
=========  ==================================================================  =========

Rules: parse from ``bytes | memoryview`` (+ base offset), never read a whole IMG into memory,
results are frozen dataclasses, malformed input raises :class:`FormatError` (never
``IndexError``/``struct.error``/hangs), names compare case-insensitively, game files are opened
only read-only. Pillow/numpy are optional accelerators imported inside functions only.
"""

from .rw import FormatError

__all__ = ["FormatError"]
