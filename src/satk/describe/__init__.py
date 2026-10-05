"""satk.describe — model descriptions imported from the gta-scout pack (M2-13).

gta-scout (MIT, Dryxio) publishes reviewed text descriptions of GTA:SA models and textures
(``data/annotations/sa-*.json``, format ``gta-scout-annotations-v1``). Every model entry carries
the SHA-256 of the DFF entry it was made from. This package binds those entries to the models
of one load profile **only** when the DFF bytes of that profile hash to the same SHA-256, and
stores the bound descriptions as notes (``satk.notes``: author ``import:gta-scout``, tag
``desc``), so ``asset find bench --kind note`` and ``note list model:<id>`` find them.

* :mod:`satk.describe.pack` — find, verify and read a pack (stdlib only);
* :mod:`satk.describe.bind` — hash the profile's DFFs and bind pack entries to model SIDs;
* :mod:`satk.describe.store` — idempotent sync of bound descriptions into ``notes.sqlite``;
* :mod:`satk.describe.ops` — ``satk describe import|status|clear``.

Licence of the imported text: MIT, notice in ``data/notices/gta-scout.txt``.
"""
