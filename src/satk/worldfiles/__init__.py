"""Writers for total-conversion world data: GXT/FXT text, zones, water, time cycle, population cycle, radar.

Operations live in :mod:`satk.worldfiles.ops` (CLI only). The modules read the game files through
``satk.formats`` (read-only) and write new files under ``<work>/out/worldfiles/``. Stdlib only at import time.
"""
