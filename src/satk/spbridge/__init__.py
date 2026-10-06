"""satk.spbridge - the single-player bridge: agent eyes and hands in the normal SP game.

The modder's day-to-day target is the stock ``gta_sa.exe`` with Mod Loader / ASI / CLEO mods,
which MTA does not load. This package gives an agent the same SAAP/1 methods there as the MTA and
Ariane backends do elsewhere, through a small ASI plugin (``satk_sp.asi``) that serves SAAP/1 over
127.0.0.1.

Modules:

* :mod:`satk.spbridge.addresses` - the 1.0 US engine address table (single source of truth, also
  emitted as the C++ header the ASI compiles);
* :mod:`satk.spbridge.inject` - a tiny, download-free injector (start suspended, ``LoadLibrary`` in
  the target, resume) and process helpers;
* :mod:`satk.spbridge.backend` - a thin SAAP/1 backend selector for ``target=sp`` (the ASI speaks
  ``saap/1`` over TCP, so it reuses :class:`satk.viewer.backends.saap_native.SaapNativeBackend`);
* :mod:`satk.spbridge.ops` - ``satk sp build|addresses|selftest|start|status|stop`` (the last three
  are consent-gated and never launch the game in automated runs).

The native project lives in ``native/sp_bridge`` (MIT C++); the host-side test server there is
compiled and exercised with the existing SAAP conformance tooling, so the protocol is proven
offline without starting the game.
"""

from __future__ import annotations

#: SAAP role the SP ASI registers itself under (discovery file ``work/run/endpoints/sp.json``).
ROLE = "sp"
#: SAAP capabilities the G0 single-player ASI implements (SAAP-v1.md section 7).
CAPS = ("core", "camera", "world.settle", "capture", "capture.size", "pick", "entity.query",
        "entity.inspect", "env", "log", "console")
