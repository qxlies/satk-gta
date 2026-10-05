"""Subprocess runner for long-running operations of the MCP server (``python -m satk.mcp.worker``).

Protocol (one request per process, UTF-8 JSON lines):

* stdout first: ``{"hello": {"pid": N}}`` -- this process's pid, so the server can put it into
  the call's process tree (``satk.mcp.proctree``) *before* sending the request (the venv
  ``python.exe`` is a launcher; this process is its child);
* stdin: ``{"op": "<dotted name>", "args": {...}, "module": "<module>", "file": "<path>"}``
  (``module``/``file`` let the worker load an operation that lives outside ``satk.<pkg>.ops``);
* stdout: any number of ``{"progress": [done, total, msg]}`` then exactly one ``{"envelope": {...}}``.

The real fd 1 is kept privately for the protocol; fd 1 and ``sys.stdout`` are pointed at stderr,
so prints of the operation (or of a C library) can never corrupt the stream.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from typing import TextIO

__all__ = ["main"]


def _claim_stdout() -> TextIO:
    sys.stdout.flush()
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(proto_fd, "w", encoding="utf-8", newline="\n")


def _load(module: str | None, file: str | None) -> None:
    if module:
        try:
            importlib.import_module(module)
            return
        except Exception:  # noqa: BLE001 - fall back to the file path
            pass
    if file and os.path.isfile(file):
        name = f"_satk_worker_{abs(hash(file))}"
        spec = importlib.util.spec_from_file_location(name, file)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)


def main() -> int:
    proto = _claim_stdout()

    def send(obj_text: str) -> None:
        proto.write(obj_text + "\n")
        proto.flush()

    send(json.dumps({"hello": {"pid": os.getpid()}}))  # nothing runs before the server has contained us

    from satk.core import envelope
    from satk.core.errors import SatkError
    from satk.core.log import setup
    from satk.core.registry import discover, get_op, invoke, progress_handler

    setup()
    try:
        req = json.loads(sys.stdin.readline() or "{}")
        name = req["op"]
        args = req.get("args") or {}
    except (ValueError, KeyError, TypeError) as e:
        send('{"envelope":' + envelope.dumps(SatkError("PROTOCOL", f"bad worker request: {e}").to_dict()) + "}")
        return 2
    discover()
    try:
        get_op(name)
    except SatkError:
        _load(req.get("module"), req.get("file"))

    def progress(done: float, total: float | None, msg: str | None) -> None:
        send(json.dumps({"progress": [done, total, msg]}, ensure_ascii=False))

    with progress_handler(progress):
        env = invoke(name, args)
    send('{"envelope":' + envelope.dumps(env) + "}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
