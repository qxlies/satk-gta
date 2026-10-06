"""satk.studio: a live, agent-driven Blender session (SAAP role ``blender``, capability ``author``).

An agent models inside Blender step by step: each step is one *method* (``mesh.primitive``,
``scene.info``, ``python`` ...) called through ``author.call``; the reply carries the method's own
result, per-object stats and optionally a snapshot path. Methods are plug-ins: any Blender-side module
with a ``METHODS`` dict (contract K3, :mod:`satk.studio.core`).

* :mod:`satk.studio.core` - stdlib-only dispatch shared by the Blender world and the mock: method
  discovery, journal, reply shaping (also imported from Blender's Python);
* :mod:`satk.studio.api` - the MIT client (contract K4): ``call(method, params, session=..., blend=...)``
  uses a running session or falls back to a one-shot cold Blender run of the same world;
* :mod:`satk.studio.launcher` - start/status/stop of named sessions (``work/run/endpoints/blender-<name>.json``);
* :mod:`satk.studio.mock` - a pure-Python world with the same methods for tests and conformance.

The Blender side (GPL) lives in ``blender/satk_blender/studio``.
"""
