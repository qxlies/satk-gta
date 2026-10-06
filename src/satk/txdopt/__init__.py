"""satk.txdopt -- TXD optimisation and the streaming memory budget.

CLI (``mcp=False``; MCP reaches them through the generic operation tool)::

    satk texture audit models/gta3.img/bistro.txd       # oversized, uncompressed, NPOT, no mips, alpha, duplicates
    satk texture optimize mymod --max 512 --drop-unused --dedupe   # -> work/out/txdopt/mymod/
    satk texture budget --area 2495 -1666 150            # streaming bytes of DFF + TXD near a point vs the limit

Modules: :mod:`.inputs` (a TXD, folder, .zip or .img as a bundle of files), :mod:`.usage` (which textures the
models of a TXD use: mod IDEs + the profile index + DFF bytes), :mod:`.txdedit` (TXD rebuild, lossless DXT3/5 ->
DXT1), :mod:`.analyze` (per-texture facts), :mod:`.optimize`, :mod:`.audit`, :mod:`.budget`, :mod:`.ops`.
numpy/Pillow are imported only inside functions (fast imports).
"""

from __future__ import annotations

__all__: list[str] = []
