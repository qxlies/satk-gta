"""satk.engine — MTA fork tooling: doctor, setup, build (SPEC §4.12, owner WP-11).

Layout (SPEC §2.1)::

    <workspace>\\engine\\
      mtasa\\                  fork of mtasa-blue (branch main), remotes neon/upstream push DISABLED
      Directory.Build.targets  adds shims\\ to ResourceCompile for projects under mtasa\\ only
      shims\\afxres.h          MFC afxres.h -> winres.h (no ATLMFC install needed)
      satk-premake.lua         premake wrapper: serves pinned downloads from deps\\ (offline)
      deps\\DXFiles\\  deps\\cache\\  deps\\net\\<sha256>\\
      deps-lock.json           sha256 of every downloaded build/runtime dependency
      bootstrap.ps1            human entry point (delegates to ``satk engine setup``)

Modules: :mod:`.common` (layout, tools, processes), :mod:`.setup` (git + shims + deps),
:mod:`.build` (premake, MSBuild, rc-test), :mod:`.doctor` (checks, status),
:mod:`.smoke` (loopback server start/stop), :mod:`.ops` (CLI/MCP operations).
Everything here is stdlib only.
"""
