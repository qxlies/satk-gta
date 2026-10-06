"""satk.kb — knowledge base about GTA:SA internals (owner M2-02).

A rebuildable SQLite file ``work/kb/kb.sqlite`` with full-text search over the read-only donor
clones (gta-reversed, plugin-sdk, MTA upstream and Neon, the cleo-ai opcode reference), struct
layouts with offsets, the opcode reference and curated, machine-checked engine facts.
Code from the sources is only shown, never written into the repository. Stdlib only.

Modules: :mod:`.cxx` (C++ scanning), :mod:`.layout` (MSVC x86 struct layouts), :mod:`.opcodes`
(cleo-ai reference), :mod:`.facts` (curated facts), :mod:`.build` (the builder), :mod:`.query`
(read side), :mod:`.ops` (CLI operations); scripting API references: :mod:`.scriptapi` (MTA Lua and Pawn
parsers), :mod:`.scriptapi_build`, :mod:`.scriptapi_query`, :mod:`.scriptapi_ops` (``kb mta``, ``kb native``).
"""
