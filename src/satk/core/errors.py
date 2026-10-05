"""Error type, error codes and CLI exit codes (SPEC §3.3). FROZEN contract.

Every failure that reaches a user or an agent is a :class:`SatkError` with one of the
codes in :data:`ERROR_CODES`. The same codes are used by the CLI, the MCP server and
SAAP/1. Do not invent new codes in other packages: ask the lead (WP-00 owns this list).

Example::

    from satk.core.errors import SatkError
    raise SatkError("NOT_FOUND", "no model 'infernos'",
                    hint="satk asset find infernos --kind model",
                    did_you_mean=["model:411"])
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "ERROR_CODES",
    "NOT_READY_CODES",
    "EXIT_OK",
    "EXIT_ERROR",
    "EXIT_USAGE",
    "EXIT_NOT_READY",
    "SatkError",
    "exit_code_for",
    "require_module",
    "bootstrap_hint",
]

#: All error codes (SPEC §3.3). ``ASSET_GUARD`` is an additive WP-00 code used only by
#: ``satk dev assetguard`` (a file looks like a game asset); see report/contract notes.
ERROR_CODES: frozenset[str] = frozenset(
    {
        "BAD_ID",
        "BAD_PARAMS",
        "NOT_FOUND",
        "AMBIGUOUS",
        "NOT_READY",
        "INDEX_MISSING",
        "READ_ONLY",
        "PROTECTED_PATH",
        "EXISTS",
        "TIMEOUT",
        "UNSUPPORTED",
        "DEPENDENCY",
        "EXTERNAL_TOOL",
        "CONSENT_REQUIRED",
        "AUTH",
        "PROTOCOL",
        "UNKNOWN_METHOD",
        "BUSY",
        "REVISION",
        "INTERNAL",
        "ASSET_GUARD",
        "CHECK_FAILED",  # additive: `satk dev gate` found a failing step
    }
)

#: Codes that mean "not ready yet" (CLI exit code 3).
NOT_READY_CODES: frozenset[str] = frozenset({"NOT_READY", "INDEX_MISSING", "DEPENDENCY"})

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_READY = 3


def exit_code_for(code: str | None) -> int:
    """CLI exit code for an error code: 0 ok, 2 bad arguments, 3 not ready, 1 otherwise."""
    if code is None:
        return EXIT_OK
    if code == "BAD_PARAMS":
        return EXIT_USAGE
    if code in NOT_READY_CODES:
        return EXIT_NOT_READY
    return EXIT_ERROR


class SatkError(Exception):
    """The one exception type that crosses package boundaries.

    Args:
        code: one of :data:`ERROR_CODES` (unknown codes are kept, but tests flag them).
        msg: short English message, e.g. ``"no model 'infernos'"``.
        hint: a command or action that likely fixes the problem.
        did_you_mean: SIDs or names close to what was asked.
        data: extra machine-readable details (kept small).
    """

    def __init__(
        self,
        code: str,
        msg: str,
        *,
        hint: str | None = None,
        did_you_mean: Iterable[str] = (),
        data: dict | None = None,
    ):
        super().__init__(f"{code}: {msg}")
        self.code = str(code)
        self.msg = str(msg)
        self.hint = hint
        self.did_you_mean = [str(x) for x in did_you_mean]
        self.data = dict(data) if data else None

    @property
    def exit_code(self) -> int:
        return exit_code_for(self.code)

    def error_dict(self) -> dict[str, Any]:
        """The inner ``error`` object; empty fields are omitted."""
        err: dict[str, Any] = {"code": self.code, "msg": self.msg}
        if self.hint:
            err["hint"] = self.hint
        if self.did_you_mean:
            err["did_you_mean"] = list(self.did_you_mean)
        if self.data:
            err["data"] = self.data
        return err

    def to_dict(self) -> dict[str, Any]:
        """The full error envelope: ``{"ok": false, "error": {...}}``."""
        return {"ok": False, "error": self.error_dict()}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SatkError({self.code!r}, {self.msg!r})"


def bootstrap_hint() -> str:
    """The command that installs the optional dependencies of this checkout (``bootstrap.ps1 -Deps``)."""
    from .config import REPO_ROOT  # lazy: config imports this module

    return f"powershell -ExecutionPolicy Bypass -File {REPO_ROOT / 'scripts' / 'bootstrap.ps1'} -Deps"


def require_module(module: str, *, pip: str | None = None, purpose: str | None = None):
    """Import an optional dependency lazily or raise ``DEPENDENCY``.

    Use inside functions only (stdlib-only rule, SPEC §2.3)::

        np = require_module("numpy", purpose="softrender")
    """
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as e:
        name = pip or module.split(".")[0]
        what = f" (needed for {purpose})" if purpose else ""
        raise SatkError(
            "DEPENDENCY",
            f"python package '{name}' is not installed{what}",
            hint=bootstrap_hint(),
            data={"module": module, "import_error": str(e)},
        ) from e
