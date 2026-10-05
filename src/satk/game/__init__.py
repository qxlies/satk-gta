"""satk.game — the clean dev copy of the game (SPEC §4.1, owner WP-01).

``gta-sa-clean`` already exists and is verified (416 files, 5 029 186 364 bytes, stock
1.0 US HOODLUM ``gta_sa.exe``). This package pins its contents
(``data/manifests/stock-1.0us-hoodlum.json``), checks it quickly (:mod:`.verify`), protects
the IMG archives (:mod:`.protect`), reproduces the copy from the original install
(:mod:`.clone`) and restores executable variants in memory (:mod:`.exe`).

Modules: ``manifests`` (data + selection rules), ``exe``, ``hashing``, ``verify`` (also
``game info``), ``protect``, ``clone``, ``guard`` (write targets: aliases of protected roots,
the clean copy root, ``work/`` only), ``ops`` (CLI operations, ``satk status`` section
``game``, ``satk doctor`` check ``game_copy``). Standard library only.
"""
