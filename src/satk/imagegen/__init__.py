"""Image generation for UI assets: an HTTP client for an OpenAI-style image endpoint, chroma keying,
a Pillow-drawn placeholder set and icon-set runs from a TOML spec.

Modules (``Pillow`` and ``numpy`` are imported inside functions only):

* :mod:`~satk.imagegen.client`      stdlib HTTP client; the key comes only from ``SATK_IMAGEGEN_API_KEY``;
* :mod:`~satk.imagegen.provenance`  sidecar and ``PROVENANCE.md`` writers (key scrubbing);
* :mod:`~satk.imagegen.keying`      magenta chroma key, trim, resize and validation;
* :mod:`~satk.imagegen.placeholder` Pillow-drawn icons and status dots;
* :mod:`~satk.imagegen.spec`        icon-set specs (``specs/*.toml``);
* :mod:`~satk.imagegen.iconset`     generate -> key -> contact sheet -> finalize;
* :mod:`~satk.imagegen.ops`         the ``imagegen.*`` operations.

Page: ``docs/en/imagegen.md``.
"""
