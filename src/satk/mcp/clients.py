"""Register the satk MCP server in AI clients (``satk mcp config --client``, M3 A4). Stdlib only.

One table, :data:`CLIENTS`, says for every client where its MCP configuration lives, in which format
and under which top-level key, and which command line registers a server:

========================  ===========================================  ==========================  ======
client                    user scope                                   project scope               format
========================  ===========================================  ==========================  ======
``claude-code``           ``~/.claude.json`` (written by ``claude``)   ``.mcp.json``               json
``codex``                 ``$CODEX_HOME/config.toml``                  ``.codex/config.toml``      toml
``cursor``                ``~/.cursor/mcp.json``                       ``.cursor/mcp.json``        json
``windsurf``              ``~/.codeium/windsurf/mcp_config.json``      --                          json
``vscode``                ``<VS Code user dir>/mcp.json`` (``servers``) ``.vscode/mcp.json``       json
``claude-desktop``        ``<appdata>/Claude/claude_desktop_config.json`` --                       json
``gemini``                ``~/.gemini/settings.json``                  ``.gemini/settings.json``   json
``lmstudio``              ``~/.lmstudio/mcp.json``                     --                          json
``continue``              --                                           ``.continue/mcpServers/<name>.yaml`` yaml
``generic``               ``--path`` only                              ``--path`` only             json
========================  ===========================================  ==========================  ======

Writing (:func:`apply`) never loses the user's data:

* a file that does not parse is never rewritten (``BAD_PARAMS`` with the fragment to paste by hand);
* other servers and keys are kept; for TOML the edit is verified by parsing the result again;
* the previous file is copied to ``<file>.satk-backup`` before it is replaced; the write is atomic and
  without a UTF-8 BOM (PowerShell 5.1 writes one, several clients reject it);
* the reported ``diff`` covers only the satk entry, never the rest of the file (other servers' API keys).

Claude Code's user scope lives in ``~/.claude.json``, a large state file the running client rewrites:
satk does not edit it, ``--write`` runs the official ``claude mcp add`` instead.
"""

from __future__ import annotations

import base64
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from satk.core.errors import SatkError
from satk.core.paths import atomic_write, cfg, ensure_writable, jpath

from . import adapter

__all__ = [
    "Client",
    "CLIENTS",
    "CLIENT_NAMES",
    "SCOPES",
    "BACKUP_SUFFIX",
    "State",
    "auto_groups",
    "server_entry",
    "config_path",
    "fragment",
    "fragment_text",
    "command_line",
    "install_link",
    "inspect",
    "apply",
    "run_client_cli",
    "check_name",
    "get_client",
    "quoted",
]

SCOPES = ("user", "project")
BACKUP_SUFFIX = ".satk-backup"
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_ARG = re.compile(r"^[A-Za-z0-9_\-./:=,@+%]+$")


@dataclass(frozen=True)
class Client:
    """One AI client: where and how its MCP servers are configured."""

    name: str
    title: str
    fmt: str  # json | toml | yaml
    key: str  # top-level key of the servers table
    user: bool  # has a user-scope (global) config
    project: str | None  # project-relative config path ("{name}" = server name); None = no project scope
    cli: str | None = None  # executable of the client's own registration command
    stdio_type: bool = False  # the entry carries "type": "stdio" (VS Code)
    note: str = ""

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(s for s in SCOPES if (s == "user" and self.user) or (s == "project" and self.project))

    @property
    def default_scope(self) -> str | None:
        return self.scopes[0] if self.scopes else None


CLIENTS: dict[str, Client] = {c.name: c for c in (
    Client("claude-code", "Claude Code", "json", "mcpServers", True, ".mcp.json", cli="claude",
           note="user scope: --write runs `claude mcp add` (Claude Code owns ~/.claude.json); "
                "project scope: approve the server when Claude Code asks"),
    Client("codex", "Codex (CLI, IDE extension, app: one config)", "toml", "mcp_servers", True,
           ".codex/config.toml", cli="codex", note="a project .codex/config.toml is read only in trusted projects"),
    Client("cursor", "Cursor", "json", "mcpServers", True, ".cursor/mcp.json",
           note="or open the cursor:// install link (Cursor shows the command before installing)"),
    Client("windsurf", "Windsurf", "json", "mcpServers", True, None, note="refresh the MCP list in Cascade"),
    Client("vscode", "VS Code (Copilot agent mode)", "json", "servers", True, ".vscode/mcp.json", stdio_type=True,
           note="the top-level key is 'servers', not 'mcpServers'; or open the vscode: install link"),
    Client("claude-desktop", "Claude Desktop", "json", "mcpServers", True, None,
           note="restart Claude Desktop after writing"),
    Client("gemini", "Gemini CLI", "json", "mcpServers", True, ".gemini/settings.json", cli="gemini"),
    Client("lmstudio", "LM Studio (local models, fully offline)", "json", "mcpServers", True, None,
           note="LM Studio reloads mcp.json on save; the model needs tool use"),
    Client("continue", "Continue", "yaml", "mcpServers", False, ".continue/mcpServers/{name}.yaml",
           note="a dedicated block file in the workspace; Continue picks it up in agent mode"),
    Client("generic", "Any other MCP client (stdio)", "json", "mcpServers", False, None,
           note="paste the fragment into the client's MCP settings, or --path FILE --write for a JSON file"),
)}
CLIENT_NAMES = tuple(CLIENTS)


# --------------------------------------------------------------------------- the entry


def auto_groups() -> list[str]:
    """MCP tool groups worth registering here: what this workspace can actually serve.

    ``core``, ``index`` and ``media`` work with a game copy alone; ``view`` needs the viewer exe,
    ``re`` a built symbol DB, ``blender`` a Blender exe, ``engine`` the MTA fork checkout. Fewer groups
    = a shorter ``tools/list`` (fewer tokens, less confusion for the model).
    """
    c = cfg()
    out = ["core", "index", "media"]

    def is_file(key: str) -> bool:
        v = c.paths.get(key)
        return bool(v) and Path(v).is_file()

    if is_file("viewer"):
        out.append("view")
    try:
        from satk.re.db import db_path

        if db_path().is_file():
            out.append("re")
    except SatkError:  # pragma: no cover - a broken work path: leave re out
        pass
    if is_file("blender"):
        out.append("blender")
    eng = c.paths.get("engine")
    if eng and (Path(eng) / ".git").exists():
        out.append("engine")
    return [g for g in adapter.GROUP_ORDER if g in out]


def _groups_value(groups: str | None) -> str | None:
    """``--groups`` -> the ``SATK_MCP_GROUPS`` value (``None`` = all tools, variable not set)."""
    if groups is None or groups.strip().lower() == "auto":
        chosen = auto_groups()
    else:
        sel = adapter.resolve_groups(groups)  # BAD_PARAMS for unknown names
        if sel is None:
            return None
        chosen = [g for g in adapter.GROUP_ORDER if g in sel]
    return None if set(chosen) >= set(adapter.GROUP_ORDER) else ",".join(chosen)


def server_entry(groups: str | None = None, workspace: str | os.PathLike | None = None,
                 checkout: str | os.PathLike | None = None) -> dict:
    """The stdio server entry for external clients: the SPEC §4.7 entry plus ``SATK_MCP_GROUPS``
    (``auto`` by default, see :func:`auto_groups`) and ``OTEL_SDK_DISABLED=true``.

    ``OTEL_SDK_DISABLED`` is insurance: the MCP SDK depends on ``opentelemetry-api``; should the user
    have an OpenTelemetry SDK installed globally, nothing is exported from the satk server.
    """
    base = adapter.mcp_json(workspace, checkout)["mcpServers"][adapter.SERVER_NAME]
    env = dict(base["env"])
    value = _groups_value(groups)
    if value is not None:
        env["SATK_MCP_GROUPS"] = value
    env["OTEL_SDK_DISABLED"] = "true"
    return {"command": base["command"], "args": list(base["args"]), "env": env}


def _client_entry(client: Client, entry: dict) -> dict:
    return {"type": "stdio", **entry} if client.stdio_type else dict(entry)


# --------------------------------------------------------------------------- locations


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _appdata() -> Path:
    """Roaming application data (Windows), the macOS/Linux equivalents elsewhere."""
    if os.name == "nt":
        v = os.environ.get("APPDATA")
        return Path(v) if v else _home() / "AppData" / "Roaming"
    if sys.platform == "darwin":  # pragma: no cover - Windows-first project
        return _home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config")  # pragma: no cover


def _claude_desktop_dir() -> Path:
    classic = _appdata() / "Claude"
    if os.name == "nt" and not classic.exists():
        local = os.environ.get("LOCALAPPDATA")
        if local:  # the Microsoft Store (MSIX) build keeps its roaming data in a package folder
            for pkg in sorted(Path(local, "Packages").glob("Claude_*")):
                d = pkg / "LocalCache" / "Roaming" / "Claude"
                if d.is_dir():
                    return d
    return classic


def _user_path(client: Client) -> Path:
    n = client.name
    if n == "claude-code":
        d = os.environ.get("CLAUDE_CONFIG_DIR")
        return Path(d) / ".claude.json" if d else _home() / ".claude.json"
    if n == "codex":
        d = os.environ.get("CODEX_HOME")
        return (Path(d) if d else _home() / ".codex") / "config.toml"
    if n == "cursor":
        return _home() / ".cursor" / "mcp.json"
    if n == "windsurf":
        return _home() / ".codeium" / "windsurf" / "mcp_config.json"
    if n == "vscode":
        return _appdata() / "Code" / "User" / "mcp.json"
    if n == "claude-desktop":
        return _claude_desktop_dir() / "claude_desktop_config.json"
    if n == "gemini":
        return _home() / ".gemini" / "settings.json"
    if n == "lmstudio":
        return _home() / ".lmstudio" / "mcp.json"
    raise SatkError("BAD_PARAMS", f"client {n!r} has no user-scope config")  # pragma: no cover


def config_path(client: Client, scope: str | None, project: str | os.PathLike | None = None,
                name: str = adapter.SERVER_NAME) -> Path | None:
    """The config file of ``client`` for ``scope`` (``None`` for ``generic``)."""
    if client.name == "generic":
        return None
    if scope == "user":
        return _user_path(client)
    root = Path(project) if project is not None else Path(cfg().paths.workspace)
    return Path(os.path.abspath(root / client.project.format(name=name)))  # type: ignore[union-attr]


# --------------------------------------------------------------------------- rendering


def _toml_key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)


def _toml_str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(_toml_str(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)  # a JSON string is a valid TOML basic string


def _toml_block(name: str, entry: dict) -> str:
    head = f"mcp_servers.{_toml_key(name)}"
    lines = [f"[{head}]"]
    lines += [f"{_toml_key(k)} = {_toml_str(v)}" for k, v in entry.items() if k != "env"]
    if entry.get("env"):
        lines += ["", f"[{head}.env]"]
        lines += [f"{_toml_key(k)} = {_toml_str(v)}" for k, v in entry["env"].items()]
    return "\n".join(lines) + "\n"


def _yaml_block(name: str, entry: dict) -> str:
    q = lambda s: json.dumps(str(s), ensure_ascii=False)  # noqa: E731 - a JSON string is a YAML scalar
    lines = [f"name: {q(name)}", "version: 0.0.1", "schema: v1", "mcpServers:", f"  - name: {q(name)}",
             f"    command: {q(entry['command'])}", "    args:"]
    lines += [f"      - {q(a)}" for a in entry.get("args", [])]
    if entry.get("env"):
        lines.append("    env:")
        lines += [f"      {k}: {q(v)}" for k, v in entry["env"].items()]
    return "\n".join(lines) + "\n"


def _json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def fragment(client: Client, entry: dict, name: str = adapter.SERVER_NAME) -> dict:
    """The JSON object a JSON client's file must contain (``{key: {name: entry}}``)."""
    return {client.key: {name: _client_entry(client, entry)}}


def fragment_text(client: Client, entry: dict, name: str = adapter.SERVER_NAME) -> str:
    """The pasteable text in the client's own format."""
    if client.fmt == "toml":
        return _toml_block(name, entry)
    if client.fmt == "yaml":
        return _yaml_block(name, entry)
    return _json_text(fragment(client, entry, name))


def _q(arg: str) -> str:
    """Quote one argument for cmd.exe, PowerShell and POSIX shells alike (double quotes)."""
    return arg if _SAFE_ARG.match(arg) else '"' + arg.replace('"', '\\"') + '"'


def command_line(client: Client, entry: dict, scope: str | None, name: str = adapter.SERVER_NAME) -> list[str] | None:
    """argv of the client's own registration command, ``None`` when it has none for this scope."""
    env = [f"{k}={v}" for k, v in entry.get("env", {}).items()]
    cmd, args = entry["command"], list(entry.get("args", []))
    if client.name == "claude-code":
        return ["claude", "mcp", "add", name, "--scope", scope or "user",
                *[x for e in env for x in ("-e", e)], "--", cmd, *args]
    if client.name == "codex" and scope == "user":
        return ["codex", "mcp", "add", name, *[x for e in env for x in ("--env", e)], "--", cmd, *args]
    if client.name == "gemini":
        return ["gemini", "mcp", "add", name, cmd, "--scope", scope or "user",
                *[x for e in env for x in ("-e", e)], "--", *args]
    return None


def install_link(client: Client, entry: dict, name: str = adapter.SERVER_NAME) -> str | None:
    """One-click install link (Cursor, VS Code); the client shows the command before installing."""
    if client.name == "cursor":
        raw = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return ("cursor://anysphere.cursor-deeplink/mcp/install?name=" + urllib.parse.quote(name)
                + "&config=" + urllib.parse.quote(base64.b64encode(raw).decode("ascii")))
    if client.name == "vscode":
        obj = {"name": name, **_client_entry(client, entry)}
        return "vscode:mcp/install?" + urllib.parse.quote(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    return None


# --------------------------------------------------------------------------- reading and writing


@dataclass
class State:
    """What a config file holds now (``inspect``)."""

    path: Path | None
    exists: bool
    status: str  # ok | missing | differs | bom | n/a
    text: str | None = None  # current file text (decoded, BOM stripped)
    data: Any = None  # parsed content (json/toml)
    current: Any = None  # the current satk entry (or block text for yaml)
    bom: bool = False


def _read_text(path: Path) -> tuple[str | None, bool]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, False
    except OSError as e:
        raise SatkError("BAD_PARAMS", f"{jpath(path)}: unreadable: {e}") from None
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        return raw.decode("utf-8-sig"), bom
    except UnicodeDecodeError as e:
        raise SatkError("BAD_PARAMS", f"{jpath(path)}: not UTF-8 ({e})") from None


def _unparsable(path: Path, problem: str, client: Client, entry: dict, name: str) -> SatkError:
    return SatkError("BAD_PARAMS", f"{jpath(path)}: {problem}",
                     hint="the file is never rewritten when it does not parse: fix it, or add the fragment "
                          "by hand (satk mcp config --client " + client.name + " --raw)",
                     data={"fragment": fragment_text(client, entry, name)})


def inspect(client: Client, path: Path | None, entry: dict, name: str = adapter.SERVER_NAME) -> State:
    """Read ``path`` and compare its satk entry with ``entry``. Never writes."""
    if path is None:
        return State(None, False, "n/a")
    text, bom = _read_text(path)
    if text is None:
        return State(path, False, "missing")
    want = _client_entry(client, entry)
    if client.fmt == "yaml":
        cur = text
        status = "ok" if text == _yaml_block(name, entry) else "differs"
        return State(path, True, "bom" if bom and status == "ok" else status, text, None, cur, bom)
    if client.fmt == "toml":
        import tomllib

        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise _unparsable(path, f"not valid TOML: {e}", client, entry, name) from None
        servers = data.get(client.key)
        if servers is not None and not isinstance(servers, dict):
            raise _unparsable(path, f"{client.key} is not a table", client, entry, name)
    else:
        try:
            data = json.loads(text) if text.strip() else {}
        except ValueError as e:
            raise _unparsable(path, f"not valid JSON: {e}", client, entry, name) from None
        if not isinstance(data, dict):
            raise _unparsable(path, f"top level is {type(data).__name__}, not an object", client, entry, name)
        servers = data.get(client.key)
        if servers is not None and not isinstance(servers, dict):
            raise _unparsable(path, f"{client.key} is not an object", client, entry, name)
    cur = (servers or {}).get(name)
    status = "missing" if cur is None else ("differs" if not _same(cur, want) else ("bom" if bom else "ok"))
    return State(path, True, status, text, data, cur, bom)


def _same(cur: Any, want: dict) -> bool:
    """Equal entries; ``"type": "stdio"`` (added by ``claude mcp add``) counts as the default."""
    if isinstance(cur, dict) and cur.get("type") == "stdio" and "type" not in want:
        cur = {k: v for k, v in cur.items() if k != "type"}
    return cur == want


_TOML_HEADER = re.compile(r"^\s*\[\[?\s*(.*?)\s*\]\]?\s*(?:#.*)?$")


def _toml_header_path(line: str) -> list[str] | None:
    m = _TOML_HEADER.match(line)
    if not m:
        return None
    parts = [p.strip() for p in re.findall(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'|[^.]+', m.group(1))]
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


def _toml_without(text: str, key: str, name: str) -> str:
    """``text`` without the tables ``[key.name]`` and ``[key.name.*]`` (whole blocks)."""
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        hp = _toml_header_path(line)
        if hp is not None:
            skipping = len(hp) >= 2 and hp[0] == key and hp[1] == name
        if not skipping:
            out.append(line)
    return "".join(out)


def _new_text(client: Client, st: State, entry: dict, name: str) -> str:
    """The whole new file text with the satk entry in place (other content kept)."""
    if client.fmt == "yaml":
        return _yaml_block(name, entry)
    if client.fmt == "toml":
        import tomllib

        base = _toml_without(st.text or "", client.key, name).rstrip()
        new = (base + "\n\n" if base else "") + _toml_block(name, entry)
        try:
            after = tomllib.loads(new)
        except tomllib.TOMLDecodeError as e:  # e.g. the entry is also an inline table elsewhere
            raise _unparsable(st.path, f"satk cannot edit safely: the TOML would not parse ({e})",  # type: ignore[arg-type]
                              client, entry, name) from None
        before = dict(st.data or {})
        rest_before = dict(before.get(client.key) or {})
        rest_before.pop(name, None)
        rest_after = dict(after.get(client.key) or {})
        got = rest_after.pop(name, None)
        others_same = {k: v for k, v in before.items() if k != client.key} == \
                      {k: v for k, v in after.items() if k != client.key}
        if got != entry or rest_after != rest_before or not others_same:
            raise _unparsable(st.path, "satk cannot edit safely: the entry is defined as an inline table "  # type: ignore[arg-type]
                                       "or with dotted keys", client, entry, name)
        return new
    data = dict(st.data or {})
    servers = dict(data.get(client.key) or {})
    servers[name] = _client_entry(client, entry)
    data[client.key] = servers
    return _json_text(data)


def _entry_diff(client: Client, st: State, entry: dict, name: str) -> list[str]:
    """Unified diff of the satk entry only (never of other servers: they may hold API keys)."""
    if client.fmt == "yaml":
        old = st.text or ""
        new = _yaml_block(name, entry)
    elif client.fmt == "toml":
        old = _toml_block(name, st.current) if isinstance(st.current, dict) else ""
        new = _toml_block(name, entry)
    else:
        want = _client_entry(client, entry)
        cur = st.current
        if isinstance(cur, dict) and cur.get("type") == "stdio" and "type" not in want:
            cur = {k: v for k, v in cur.items() if k != "type"}
        old = _json_text({name: cur}) if cur is not None else ""
        new = _json_text({name: want})
    return [ln.rstrip("\n") for ln in difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                             "before", "after", n=1)][:80]


def _run(argv: list[str], timeout: float = 120.0) -> tuple[int, str]:
    """Run a client's own CLI (list argv, no shell). ``NOT_FOUND`` when it is not on PATH."""
    exe = shutil.which(argv[0])
    if exe is None:
        raise SatkError("NOT_FOUND", f"{argv[0]!r} is not on PATH",
                        hint="install the client, or run the printed command in its own terminal")
    try:
        p = subprocess.run([exe, *argv[1:]], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise SatkError("TIMEOUT", f"{' '.join(argv[:3])} took longer than {timeout:.0f} s") from None
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def run_client_cli(argv: list[str]) -> tuple[int, str]:
    """Indirection for tests (they replace it)."""
    return _run(argv)


def apply(client: Client, scope: str | None, path: Path | None, entry: dict, name: str = adapter.SERVER_NAME) -> dict:
    """Write the entry (backup, atomic, no BOM). Returns ``{written, backup?, diff, status}``."""
    st = inspect(client, path, entry, name)
    if st.status == "ok":
        return {"written": False, "status": "ok", "diff": []}
    diff = _entry_diff(client, st, entry, name)
    if client.name == "claude-code" and scope == "user":
        argv = command_line(client, entry, "user", name)
        assert argv is not None
        if st.status == "differs":
            run_client_cli(["claude", "mcp", "remove", name, "--scope", "user"])
        code, out = run_client_cli(argv)
        after = inspect(client, path, entry, name)
        if code != 0 or after.status != "ok":
            raise SatkError("EXTERNAL_TOOL", f"`claude mcp add` failed (exit {code}): {out[-400:]}",
                            hint="run the command yourself: " + " ".join(_q(a) for a in argv))
        return {"written": True, "status": "ok", "diff": diff, "via": "claude mcp add"}
    assert path is not None
    new = _new_text(client, st, entry, name)
    ensure_writable(path)
    backup = None
    if st.exists:
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        atomic_write(backup, path.read_bytes())
    atomic_write(path, new)  # UTF-8 without BOM
    out = {"written": True, "status": "ok", "diff": diff}
    if backup is not None:
        out["backup"] = jpath(backup)
    return out


def check_name(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise SatkError("BAD_PARAMS", f"bad server name {name!r} (letters, digits, '_' and '-', at most 64)")
    return name


def get_client(name: str) -> Client:
    try:
        return CLIENTS[name]
    except KeyError:
        import difflib as _d

        raise SatkError("BAD_PARAMS", f"unknown client {name!r}", did_you_mean=_d.get_close_matches(name, CLIENT_NAMES),
                        data={"clients": list(CLIENT_NAMES)}) from None


def quoted(argv: list[str]) -> str:
    """argv as one line for the user to paste."""
    return " ".join(_q(a) for a in argv)
