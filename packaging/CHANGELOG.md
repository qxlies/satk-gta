# Changelog

Notable changes of satk. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/); before 1.0 anything may change.
The release zip ships this file as `CHANGELOG.md`.

## [Unreleased]

## [0.2.1] - 2026-10-05

Public preview: the first version published at https://github.com/qxlies/satk-gta, with the portable zip, the
wheel, the sdist and `SHA256SUMS.txt` attached to the GitHub release.

### Added

- GitHub issue and pull request templates and a CI workflow on GitHub Actions.
- Public export: the public repository gets one snapshot commit per release, built from the development
  repository without its history.
- `tests/docs/test_public_text.py`: the READMEs, `NOTICE.md`, this changelog, the user and agent docs, the
  protocol text, the packaging texts and the contributor guide carry no machine-specific paths and no
  references to internal planning documents.

### Changed

- Documentation cleanup for the public preview: example paths are written as `<workspace>\…` or relative to
  the workspace, internal plan, work-package and research-report references are gone, and the README and the
  install guide point to the GitHub repository and its Releases page.
- `CLAUDE.md` is now a general contributor guide (development setup with placeholders, the same rules).
- `NOTICE.md` describes the reference-only sources without local paths.
- The test suite runs in any checkout (CI included): write-protection tests use an isolated workspace and
  sample paths are neutral. Messages and generated file headers no longer name internal work items.
- `satk dev gate` skips the agent-docs check in a workspace where no agent docs were ever published.
- `satk game verify --against install` without `--manifest` reports `NOT_FOUND` with a hint when the install
  audit is not part of this copy of satk (it describes the maintainers' own install and is not published).

## [0.2.0] - 2026-10-05

Private preview.

### Added

- Portable Windows release: `satk dev release` builds `satk-<version>-win64.zip` (official embeddable
  CPython 3.12, pinned runtime wheels, launchers, `README-FIRST.txt`), the `satk-gta` wheel and sdist,
  and `SHA256SUMS.txt`, then smoke-tests the unpacked zip in a path with Cyrillic letters and spaces.
- Portable mode: a `portable.txt` next to the launchers makes that folder the workspace; the per-user
  configuration is not read, and a `workspace` line in the folder's `satk.toml` cannot point elsewhere.
- `satk doctor` check `portable`: the folder is writable, not blocked by Windows (Mark of the Web), and
  the code matches `RELEASE.json`.
- `satk doctor` check `app_control`: reports when Smart App Control blocks the unsigned extension modules
  of numpy, Pillow and pydantic-core.

### Changed

- The package name for PyPI is `satk-gta` (the command stays `satk`).

## [0.1.0] - internal

First internal version, used from a git checkout.

### Added

- Asset index of a game installation (IMG, DFF, TXD, COL, IDE, IPL and data files) with stable IDs and
  layers (vanilla, SA-MP, modloader mods, other changes).
- Texture and model previews, map images, control of the offline viewer, crash address lookup in
  `gta_sa.exe`, Blender import and export, helpers for the MTA fork.
- MCP server for AI agents, `satk init`, `satk doctor`, `satk help`.
- Texture packing (DXT, TXD writer), map converters (SA-MP Pawn, MTA `.map`, IPL), path nodes, asset
  linter, crash dumps, game-ready export from Blender, offline HTML catalog, extended index (water,
  timecyc, handling and more), model descriptions, knowledge base of engine sources, in-game agent for MTA.

[Unreleased]: https://github.com/qxlies/satk-gta/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/qxlies/satk-gta/releases/tag/v0.2.1
