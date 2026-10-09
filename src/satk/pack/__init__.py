"""satk.pack -- the ``.saepak`` content container, the DAC (derived-asset cache) format and the texture dedup census.

CLI (all ``mcp=False``; MCP reaches them through ``satk_ops`` / ``satk_op``)::

    satk pack build mymod --out mymod.saepak        # a folder or a list of DFF/TXD/COL/IFP/IPL files -> one pack
    satk pack verify mymod.saepak                   # structure, IMG directory, SHA-256 of every entry and chunk
    satk pack inspect mymod.saepak                  # header, mount metadata, names, sizes (also .dac files)
    satk pack dac mymod/car.dff                     # derive normals, night colours, tangents, lights -> .dac
    satk pack census --profile vanilla              # name-preserving texture dedup statistics -> work/out/pack/

Modules: :mod:`.layout` (record tables and constants, the single source of the documented byte layout),
:mod:`.saepak` (writer, reader, verifier), :mod:`.sources` (input files), :mod:`.dac` (the DAC format and the DFF
derivation), :mod:`.census`, :mod:`.ops`. Stdlib only (the DAC derivation reads DFFs through :mod:`satk.rw`).
"""

from __future__ import annotations

__all__: list[str] = []
