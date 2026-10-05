"""satk.core — frozen shared contracts (SPEC §3, WP-00). stdlib only.

Modules:

* ``registry`` — ``@op``, ``doctor_check``, ``status_provider``, ``discover``, ``all_ops``;
* ``ids`` — ``Sid``, ``SidProvider``, ``register_provider``, ``provider_for``;
* ``envelope`` — ``table``, ``obj``, ``error``, rounding helpers, ``dumps``;
* ``errors`` — ``SatkError``, error codes, exit codes;
* ``paths`` — ``cfg``, ``work``, ``tmp``, ``profile_root``, ``ensure_writable``, ``ensure_removable``, ``game_writer``,
  ``open_ro``, ``jpath``, ``atomic_write``;
* ``config`` — ``satk.toml`` + ``SATK_*`` environment, workspace discovery;
* ``detect`` — Blender/MSBuild/vcvars/Ariane and game installs on this machine, exe variants;
* ``cli`` — the ``satk`` command line generated from the registry;
* ``log`` — stderr/file logging (stdout stays clean);
* ``assetguard`` — pre-commit check against committing game assets.
"""

from .envelope import obj, table
from .errors import SatkError
from .ids import Sid
from .registry import doctor_check, op, status_provider

__all__ = ["SatkError", "Sid", "doctor_check", "obj", "op", "status_provider", "table"]
