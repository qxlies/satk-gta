"""Car, boat and pedestrian path networks of GTA:SA (``nodes0.dat`` ... ``nodes63.dat``). Owner: M2-05.

``satk paths import`` reads the 64 ``nodes*.dat`` regions of a load profile from its IMG archives
(read-only, through :mod:`satk.formats.layout` and :mod:`satk.formats.img`; the first registered
archive wins, like the engine) and stores them in a separate SQLite database
``work/index/paths-<profile>.sqlite``. ``near``/``node``/``export`` query it, ``compile`` rebuilds the
regions (optionally with node edits) through the vendored gta-flow compiler and proves the unchanged
network byte-identical.

Modules:

* :mod:`satk.paths.vendor` - loader of the vendored gta-flow core (``vendor/gtaflow``, MIT);
* :mod:`satk.paths.records` - field layout of the 28-byte path node and the 14-byte navi record;
* :mod:`satk.paths.db` - import, the database schema and read queries;
* :mod:`satk.paths.build` - ``compile`` (round trip and edits) and ``export`` (JSON overlay);
* :mod:`satk.paths.ops` - the ``satk paths ...`` operations (CLI; MCP through the generic tool).

Stdlib only.
"""
