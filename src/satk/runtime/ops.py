"""Operations of satk.runtime (WP-06, M2-01): ``help`` (MCP ``satk_help``), ``status`` (MCP ``satk_status``),
``doctor`` and ``dev gen-docs``. Importing this module also registers the environment doctor checks.
"""

from __future__ import annotations

from satk.core.envelope import compact
from satk.core.registry import op

from . import doctor as _doctor  # noqa: F401 - registers the doctor checks
from . import help as _help
from . import status as _status


@op("help", summary="satk docs for agents. topic: start (default), ids, tools, ops, workflows, schema, errors, viewer, "
                    "re, blender, engine, golden, notes, or a tool/op name.",
    summary_ru="Справка satk: темы start, ids, tools, ops, workflows, schema, errors… или имя команды; --ru — "
               "таблица команд по-русски.",
    mcp="satk_help", group="core",
    examples=("satk help", "satk help ids", "satk help asset_find", "satk help --all", "satk help --ru"))
def help_(topic: str | None, ru: bool = False) -> dict:
    """Show a help topic.

    Args:
        topic: topic or tool name.
        ru: Russian command table.
    """
    return _help.render(topic, ru=ru)


@op("status", summary="Workspace overview (game copy, index, viewer, symbols, Blender, engine, notes) with warnings. "
                      "Call it first in a session.",
    summary_ru="Обзор рабочего пространства: копия игры, индекс, вьювер, символы, Blender, движок, заметки.",
    mcp="satk_status", group="core", examples=("satk status", "satk status --deep --table"))
def status(deep: bool = False) -> dict:
    """Collect status sections from all packages.

    Args:
        deep: slower probes (hashes, pings).
    """
    return _status.collect(deep=deep)


@op("doctor", summary="Check the environment: Python, venv, deps, UTF-8, paths, disk, git, Blender, MSBuild, "
                      "premake, .mcp.json, index, ops import, plus checks of every package.",
    summary_ru="Проверка окружения: Python, venv, зависимости, UTF-8, пути, диск, git, Blender, MSBuild, premake…",
    mcp=False, group="core", examples=("satk doctor", "satk doctor --deep", "satk doctor --only disk,index"))
def doctor(deep: bool = False, only: list[str] | None = None) -> dict:
    """Run doctor checks; status is the worst of ok/warn/fail.

    Args:
        deep: slower probes (e.g. start Blender for its version).
        only: run only these checks.
    """
    return compact(_doctor.run(deep=deep, only=only))


@op("init", summary="First-time setup: pick the workspace and the game folder (discovered: registry, Steam, "
                    "Program Files, workspace), detect Blender/MSBuild/Ariane, write <workspace>/satk.toml.",
    summary_ru="Первичная настройка: workspace, папка игры (поиск в реестре, Steam, Program Files), Blender/MSBuild/"
               "Ariane, запись <workspace>/satk.toml.",
    mcp=False, group="core",
    examples=("satk init --dry-run", "satk init --game \"C:/Games/GTA San Andreas\"",
              "satk init --game <game dir> --workspace <dir> --yes", "satk init --clean-copy --dry-run"))
def init(game: str | None = None, workspace: str | None = None, clean_copy: bool = False, yes: bool = False,
         dry_run: bool = False) -> dict:
    """Configure satk for a game folder; the game is only read, satk writes under <workspace>/work.

    Args:
        game: game folder (gta_sa.exe or gta-sa.exe, models/gta3.img, data/gta.dat); default: the only
            discovered one (several -> AMBIGUOUS, or a numbered choice in a terminal).
        workspace: workspace directory (default: the current one, else %LOCALAPPDATA%/satk).
        clean_copy: also build <workspace>/gta-sa-clean with satk game clone (stock 1.0 US games only).
        yes: overwrite an existing different satk.toml (and the per-user pointer); no questions.
        dry_run: only show the plan, candidates and the satk.toml diff; write nothing.
    """
    from .setup import init as _init

    return _init(game=game, workspace=workspace, clean_copy=clean_copy, yes=yes, dry_run=dry_run)


@op("dev.gen_docs", summary="Generate docs/agent/tools.md and docs/agent/schema.md from the registry and schema.sql "
                            "files; --check fails (REVISION) if they are stale.",
    summary_ru="Сгенерировать docs/agent/tools.md и schema.md из реестра и schema.sql; --check — проверить актуальность.",
    mcp=False, group="dev", examples=("satk dev gen-docs", "satk dev gen-docs --check"))
def gen_docs(check: bool = False, root: str | None = None) -> dict:
    """Write or check the generated agent docs.

    Args:
        check: only compare; REVISION error if a file differs.
        root: repository root to write into (default: this checkout).
    """
    from pathlib import Path

    from .gendocs import generate

    return compact(generate(Path(root) if root else None, check=check))
