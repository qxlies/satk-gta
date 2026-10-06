"""Operations of satk.mcp (WP-06, M2-01, M3 A4): ``satk mcp`` (stdio server), ``satk mcp selftest``,
``satk mcp config`` (any AI client), ``satk agent install-skill`` and the generic access ``satk mcp ops`` /
``satk mcp op`` (served as the MCP tools ``satk_ops`` / ``satk_op``).

Module-level imports are stdlib/satk only; the MCP SDK is imported inside ``satk mcp``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from satk.core.envelope import compact, table
from satk.core.errors import SatkError, require_module
from satk.core.paths import atomic_write, cfg, jpath
from satk.core.registry import op

from . import adapter

#: ``--client`` values (kept equal to ``satk.mcp.clients.CLIENT_NAMES`` by tests/mcp/test_clients.py).
ClientName = Literal["claude-code", "codex", "cursor", "windsurf", "vscode", "claude-desktop", "gemini", "lmstudio",
                     "continue", "generic"]


@op("mcp", summary="Run the satk MCP server on stdio (what .mcp.json starts); stdout carries only JSON-RPC.",
    summary_ru="Запустить MCP-сервер satk на stdio (его запускает .mcp.json); в stdout только JSON-RPC.",
    mcp=False, group="mcp", examples=("satk mcp", "satk mcp --groups core,index"))
def mcp_serve(groups: str | None = None) -> dict:
    """Serve MCP on stdin/stdout until the client disconnects.

    Args:
        groups: comma-separated subset (index,media,view,re,blender,engine,core); default: SATK_MCP_GROUPS or all.
    """
    require_module("mcp", purpose="the MCP server")
    from .server import serve

    code = serve(groups=groups)  # an unknown group: error on stderr only, exit code 2
    raise SystemExit(code)  # never print an envelope after the session: stdout belongs to JSON-RPC


@op("mcp.selftest",
    summary="Start the MCP server as a subprocess and run a real stdio session (initialize, tools/list, "
            "satk_help, satk_status); checks the tool set, the 16 KB tools/list budget and stdout purity.",
    summary_ru="Самопроверка MCP: настоящая stdio-сессия, набор инструментов, бюджет tools/list 16 КБ, чистый stdout.",
    mcp=False, group="mcp", examples=("satk mcp selftest", "satk mcp selftest --groups core"))
def mcp_selftest(groups: str | None = None, timeout: float = 90.0) -> dict:
    """Self-test of the MCP server over a real stdio pipe.

    Args:
        groups: SATK_MCP_GROUPS for the server under test (default: inherited, i.e. all).
        timeout: seconds for the whole session.
    """
    from .selftest import run

    return compact(run(groups=groups, timeout=timeout))


@op("mcp.config",
    summary="Register the satk MCP server in an AI client: --client claude-code|codex|cursor|windsurf|vscode|"
            "claude-desktop|gemini|lmstudio|continue|generic [--scope user|project]; --write edits its config "
            "(backup, no BOM), --print-command, --raw. No --client: <workspace>/.mcp.json.",
    summary_ru="Подключить MCP-сервер satk к ИИ-клиенту (--client ...): --write правит его конфиг (бэкап, без BOM), "
               "--print-command печатает команду клиента, --raw — фрагмент. Без --client — <workspace>/.mcp.json.",
    mcp=False, group="mcp",
    examples=("satk mcp config --client cursor", "satk mcp config --client codex --write",
              "satk mcp config --client claude-code --print-command",
              "satk mcp config --client vscode --scope project --project . --write",
              "satk mcp config --client lmstudio --groups core,index,media --write",
              "satk mcp config --client generic --raw", "satk mcp config --write", "satk mcp config --check"))
def mcp_config(client: ClientName | None = None, scope: Literal["user", "project"] | None = None,
               project: str | None = None, groups: str | None = None, name: str = "satk", path: str | None = None,
               write: bool = False, check: bool = False, raw: bool = False, print_command: bool = False) -> dict:
    """Register the MCP server in an AI client, or (no ``--client``) in the workspace's ``.mcp.json``.

    status: ok | missing (no file or no satk entry) | differs (other entry) | bom (right entry, but the
    file starts with a UTF-8 BOM that most JSON parsers reject). A file that does not parse is never
    rewritten (its other servers would be lost): BAD_PARAMS with the fragment to paste by hand. The
    previous file is kept as ``<file>.satk-backup``; ``diff`` shows the satk entry only.

    With ``--client`` the entry also sets ``OTEL_SDK_DISABLED=true`` and ``SATK_MCP_GROUPS`` (default
    ``auto``: core,index,media plus view/re/blender/engine when this workspace has the viewer, the symbol
    DB, Blender, the MTA fork). Claude Code's user scope is written by its own ``claude mcp add``.

    Args:
        client: AI client; omitted = the workspace .mcp.json for Claude Code (all tools).
        scope: user (global; the default where it exists) or project (one folder).
        project: project folder for --scope project (default: the workspace).
        groups: SATK_MCP_GROUPS of the entry: auto (default), all, or a list like core,index,media.
        name: server name in the client's config.
        path: config file to use instead of the client's default (generic: a JSON file with mcpServers).
        write: write/update the config file (keeps other servers and keys, drops a BOM, backup first).
        check: fail (NOT_FOUND / REVISION) unless the file has exactly this entry.
        raw: print only the pasteable fragment in the client's format (no envelope).
        print_command: print only the client's own registration command (claude, codex, gemini).
    """
    if client is None:
        if any(v is not None for v in (scope, project, groups, path)) or name != "satk" or print_command:
            raise SatkError("BAD_PARAMS", "--scope, --project, --groups, --name, --path and --print-command "
                                          "need --client", hint="satk mcp config --client claude-code --scope project")
        return _workspace_mcp_json(write, check, raw)
    return _client_config(client, scope, project, groups, name, path, write, check, raw, print_command)


def _workspace_mcp_json(write: bool, check: bool, raw: bool) -> dict:
    """The project-scope MCP registration of the workspace for Claude Code (SPEC §4.7)."""
    frag = adapter.mcp_json()
    if raw:
        _print_raw(json.dumps(frag, ensure_ascii=False, indent=2) + "\n")
    entry = frag["mcpServers"][adapter.SERVER_NAME]
    path = Path(cfg().paths.workspace) / ".mcp.json"
    current, problem, bom = adapter.read_mcp_json(path)
    if current is None and problem != "missing":
        raise SatkError("BAD_PARAMS", f"{jpath(path)}: {problem}",
                        hint="fix the file or move it away, then: satk mcp config --write",
                        data={"expected": frag})
    servers_now = (current or {}).get("mcpServers") or {}
    have = servers_now.get(adapter.SERVER_NAME)
    status = "missing" if have is None else ("differs" if have != entry else ("bom" if bom else "ok"))
    written = False
    if write and status != "ok":
        data = dict(current or {})
        servers = dict(data.get("mcpServers") or {})
        servers[adapter.SERVER_NAME] = entry
        data["mcpServers"] = servers
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")  # UTF-8 without BOM
        written, status = True, "ok"
    if check and status != "ok":
        raise SatkError("NOT_FOUND" if status == "missing" else "REVISION",
                        f"{jpath(path)}: satk entry {status}", hint="satk mcp config --write",
                        data={"expected": frag})
    return {"path": jpath(path), "status": status, "written": written, **frag}


def _print_raw(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()
    raise SystemExit(0)  # the text alone (no envelope), as `satk mcp` does for its stream


def _client_config(client_name: str, scope: str | None, project: str | None, groups: str | None, name: str,
                   path: str | None, write: bool, check: bool, raw: bool, print_command: bool) -> dict:
    from . import clients as C

    cl = C.get_client(client_name)
    C.check_name(name)
    if scope is not None and scope not in cl.scopes and path is None:
        raise SatkError("BAD_PARAMS", f"{cl.title} has no {scope} scope"
                        + (f" (scopes: {', '.join(cl.scopes)})" if cl.scopes else " (use --path FILE)"),
                        data={"scopes": list(cl.scopes)})
    sc = scope or cl.default_scope
    if project is not None and sc != "project":
        raise SatkError("BAD_PARAMS", "--project needs --scope project", hint="add --scope project")
    entry = C.server_entry(groups)
    if raw:
        _print_raw(C.fragment_text(cl, entry, name))
    argv = C.command_line(cl, entry, sc, name)
    if print_command:
        if argv is None:
            raise SatkError("UNSUPPORTED", f"{cl.title} has no registration command"
                            + (f" for the {sc} scope" if sc else ""),
                            hint=f"satk mcp config --client {cl.name} " + ("--write" if sc else "--raw"))
        _print_raw(C.quoted(argv) + "\n")
    target = Path(os.path.abspath(path)) if path is not None else C.config_path(cl, sc, project, name)
    if (write or check) and target is None:
        raise SatkError("BAD_PARAMS", f"{cl.title}: no config file to {'write' if write else 'check'}",
                        hint="pass --path FILE (a JSON file with mcpServers), or paste the fragment (--raw)")
    st = C.inspect(cl, target, entry, name)
    out: dict = {"client": cl.name, "scope": sc if path is None else None, "name": name,
                 "path": jpath(target) if target is not None else None, "format": cl.fmt, "status": st.status,
                 "written": False, "groups": entry["env"].get("SATK_MCP_GROUPS", "all")}
    if write:
        out.update(C.apply(cl, sc if path is None else None, target, entry, name))
    if check and out["status"] != "ok":
        raise SatkError("NOT_FOUND" if out["status"] == "missing" else "REVISION",
                        f"{out['path']}: satk entry {out['status']}",
                        hint=f"satk mcp config --client {cl.name}" + (f" --scope {sc}" if sc else "") + " --write")
    if argv is not None:
        out["command"] = C.quoted(argv)
    link = C.install_link(cl, entry, name)
    if link is not None:
        out["link"] = link
    out["fragment"] = C.fragment(cl, entry, name) if cl.fmt == "json" else C.fragment_text(cl, entry, name)
    if cl.note:
        out["note"] = cl.note
    return {k: v for k, v in out.items() if v is not None}


@op("agent.install_skill",
    summary="Install the satk agent skill (SKILL.md) for Claude Code and/or Codex: user scope "
            "(~/.claude/skills, ~/.agents/skills) or one project; --dest DIR for any other skills folder; "
            "--check only compares.",
    summary_ru="Установить skill satk (SKILL.md) для Claude Code и/или Codex: для пользователя "
               "(~/.claude/skills, ~/.agents/skills) или в проект; --dest DIR — другая папка; --check — сверка.",
    mcp=False, group="mcp",
    examples=("satk agent install-skill", "satk agent install-skill --client claude --check",
              "satk agent install-skill --client codex --scope project --project .",
              "satk agent install-skill --dest .claude-plugin/satk/skills"))
def agent_install_skill(client: Literal["claude", "codex", "all"] = "all",
                        scope: Literal["user", "project"] = "user", project: str | None = None,
                        dest: str | None = None, check: bool = False) -> dict:
    """Copy ``docs/agent/SKILL.md`` to ``<skills dir>/satk/SKILL.md`` of the chosen clients.

    Claude Code reads ``~/.claude/skills`` (``$CLAUDE_CONFIG_DIR/skills``) and ``<project>/.claude/skills``;
    Codex reads ``~/.agents/skills`` and ``<project>/.agents/skills``. A different existing copy there is kept
    as ``SKILL.md.satk-backup``. Other AI clients: ``--dest`` (no backup; or reference the file from AGENTS.md).
    The skill's companion files (the style guides ``style/*.md`` and the brief template ``briefs/*.md`` next to
    the source) are copied into the same ``satk`` folder; a copy is current only when they are too.

    Args:
        client: claude, codex or all.
        scope: user (all folders) or project (one folder).
        project: project folder for --scope project (default: the workspace).
        dest: install into DEST/satk/SKILL.md instead (overrides client and scope).
        check: only compare: NOT_FOUND / REVISION unless every copy is current.
    """
    from . import skill as S

    if project is not None and scope != "project":
        raise SatkError("BAD_PARAMS", "--project needs --scope project", hint="add --scope project")
    text = S.source_text()
    if dest is not None:
        targets = [("dest", Path(os.path.abspath(dest)))]
    else:
        names = S.SKILL_CLIENTS if client == "all" else (client,)
        targets = [(c, S.skills_dir(c, scope, project)) for c in names]
    rows = []
    for who, d in targets:
        r = S.install(d, text, check=check, backup=dest is None)
        current, wrote = _install_companions(S.source().parent, d / S.SKILL_NAME, check=check)
        status = "differs" if r["status"] == "ok" and not current and check else r["status"]
        action = "updated" if r["action"] == "none" and wrote else r["action"]
        rows.append([who, r["path"], status, action])
    bad = [r for r in rows if r[2] != "ok"]
    if check and bad:
        raise SatkError("NOT_FOUND" if all(r[2] == "missing" for r in bad) else "REVISION",
                        f"skill not current: {', '.join(r[1] for r in bad)}",
                        hint="satk agent install-skill" + ("" if dest is None else f" --dest {dest}"),
                        data={"cols": ["client", "path", "status"], "rows": [r[:3] for r in rows]})
    return {"source": jpath(S.source()), **table(["client", "path", "status", "action"], rows)}


def _install_companions(src_dir: Path, dst_dir: Path, *, check: bool) -> tuple[bool, bool]:
    """Copy (or with ``check`` only compare) the skill's companion files: ``(were all current, wrote any)``."""
    from satk.docs.sync import skill_files

    current, wrote = True, False
    for rel in skill_files(src_dir):
        data = (src_dir / rel).read_bytes()
        dst = dst_dir / rel
        try:
            same = dst.read_bytes() == data
        except FileNotFoundError:
            same = False
        if not same:
            current = False
            if not check:
                atomic_write(dst, data)
                wrote = True
    return current, wrote


@op("mcp.ops",
    summary="Find any satk operation, also those without their own tool (formats, game, index build...): "
            "query words -> op, args, summary; one hit adds its JSON schema. Run it with satk_op.",
    summary_ru="Найти любую операцию satk, в том числе без своего MCP-инструмента: слова запроса -> имя, "
               "аргументы, сводка; при одном совпадении — полная JSON-схема (MCP: satk_ops).",
    mcp=False, mcp_group="core", group="mcp",
    examples=("satk mcp ops txd", "satk mcp ops \"index status\"", "satk mcp ops --limit 200"))
def mcp_ops_find(query: str | None, limit: int = 20) -> dict:
    """Search the operations an agent can run through satk_op (MCP tool satk_ops).

    Every word must match the name, CLI words, summaries, parameters, docs or examples; no query
    lists them all. Rows: op (dotted name for satk_op), args (compact signature: ``name:type``
    required, ``name?:type`` optional, ``name:type=default``), summary. Operations satk_op refuses
    (server management, the dev gate, writers of the game copy, ...) are listed in ``cli_only``.
    """
    from . import generic

    return generic.search(query, limit=limit, groups=generic.current_groups())


@op("mcp.op",
    summary="Run any satk operation by name (\"formats.dump\", \"index status\") with JSON args, validated "
            "like the CLI; long ones run in a subprocess. Find names and args with satk_ops.",
    summary_ru="Выполнить любую операцию satk по имени с JSON-аргументами, проверенными как в CLI; долгие — "
               "в отдельном процессе (MCP: satk_op).",
    mcp=False, mcp_group="core", group="mcp",
    examples=("satk mcp op version", "satk mcp op \"index status\"",
              "satk mcp op formats.dump --args '{\"target\": \"data/gta.dat\"}'"))
def mcp_op(op: str, args: dict | None = None) -> dict:  # noqa: A002 - names of the MCP tool satk_op
    """Run one operation the way the MCP tool satk_op does (same resolution, policy and validation).

    ``op`` is a dotted name (``formats.dump``), CLI words (``formats dump``) or an MCP tool name;
    ``args`` is a JSON object of its parameters (see satk_ops / satk_help). In the MCP server a
    long-running operation runs in the worker subprocess (progress, 600 s, process tree); here, in
    the CLI, it runs in this process. Refused: see ``satk help ops``.
    """
    from . import generic

    spec, call_args = generic.prepare(op, args, generic.current_groups())
    return spec.call(call_args)
