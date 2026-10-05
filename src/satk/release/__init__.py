"""Portable release of satk (M3 A1): ``satk dev release``.

One command builds, from a git ref of this checkout (never from the working tree):

* ``satk-<ver>-win64.zip`` - the official embeddable CPython 3.12 (``python/``), the runtime wheels
  of ``packaging/release-lock.json`` installed into ``python/Lib/site-packages`` (no pip, no pytest),
  the runtime part of the repository (``src/ data/ vendor/ proto/ mta-resources/ blender/ docs/``)
  and the launchers of ``packaging/portable`` (``satk.cmd``, ``satk-mcp.cmd``, ``Start satk.cmd``,
  ``README-FIRST.txt``, ``portable.txt``);
* ``satk_gta-<ver>-py3-none-any.whl`` and ``satk_gta-<ver>.tar.gz`` (PyPI name ``satk-gta``);
* ``SHA256SUMS.txt`` over the three files.

The zip mirrors a checkout, so every resolver that uses ``REPO_ROOT`` (data, vendored code, the
Blender add-on) works unchanged; ``portable.txt`` makes the unpacked folder the workspace
(:func:`satk.core.config.portable_root`). Modules:

* :mod:`.lock` - the lock file, ``--relock`` (dependency closure, PyPI and python.org hashes);
* :mod:`.fetch` - verified downloads into ``work/cache/release``;
* :mod:`.tree` - export of a git ref;
* :mod:`.wheels` - wheel contents for ``site-packages`` (no pip);
* :mod:`.dist` - wheel and sdist of satk itself (stdlib only, no setuptools);
* :mod:`.build` - the zip, checksums and the smoke run of the unpacked copy;
* :mod:`.sandbox` - the Windows Sandbox check (``.wsb``).
"""
