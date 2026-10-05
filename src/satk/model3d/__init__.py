"""satk.model3d — model export (glTF/OBJ/raw) and software-rendered previews (SPEC §4.4, owner WP-05).

Modules: :mod:`.mesh` (DFF -> scene of frames/parts/meshes), :mod:`.textures` (TXD chain, decoding,
vehicle colours), :mod:`.gltf` / :mod:`.obj` (writers), :mod:`.softrender` (numpy rasterizer),
:mod:`.resolve` (SID -> files via the index), :mod:`.api` (export/image), :mod:`.batch` (thumbnails of
every model), :mod:`.ops` (``satk model image``, ``satk asset export``).
"""
