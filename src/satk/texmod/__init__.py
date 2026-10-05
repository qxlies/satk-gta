"""satk.texmod -- texture modding: DXT encoder, TXD writer, pack/replace/extract (owner M2-03).

CLI (``mcp=False``; MCP reaches them through the generic operation tool)::

    satk texture extract txd:bistro                        # -> work/out/texmod/bistro/*.png + texmod.json
    satk texture pack bistro --name bistro_hd              # folder of images -> work/out/mods/bistro_hd/bistro_hd.txd
    satk texture replace txd:bistro Plate=bistro/Marble.png --name bistro_swap   # -> work/out/mods/bistro_swap/bistro.txd

Python API::

    from satk.texmod.encode import encode_level, mip_chain, auto_format     # numpy (h, w, 4) uint8 -> bytes
    from satk.texmod.txdwrite import NativeSpec, native_chunk, txd_chunk, rewrite_txd   # stdlib
    from satk.texmod.api import pack, replace, extract, load_txd

Modules: :mod:`.bc` (BC1/BC2/BC3 block encoder), :mod:`.encode` (formats, mips, PSNR), :mod:`.txdwrite`
(D3D9 natives, dictionaries, in-place rewrite), :mod:`.api` (inputs, outputs, README), :mod:`.ops`.
numpy/Pillow are imported only inside functions (SPEC §2.3).
"""

from __future__ import annotations

__all__: list[str] = []
