"""Redaction for ``satk bug-report``: no user, host, token or machine path leaves the computer.

:class:`Redactor` replaces, in this order:

1. values of secret environment variables (names with TOKEN, SECRET, PASSWORD, API_KEY, ...) and
   ``key=value`` / ``"key": "value"`` pairs whose key names a secret -> ``<redacted>``;
   well-known token shapes (``sk-...``, ``ghp_...``, ``github_pat_...``, ``xox?-...``, ``AKIA...``,
   JWTs, ``Bearer ...``) -> ``<redacted>``;
2. e-mail addresses -> ``<email>``;
3. the game folders -> ``<game>``, the workspace -> ``<workspace>``, the satk checkout -> ``<satk>``
   (in any spelling: ``\\``, ``/``, doubled ``\\\\`` as in JSON, any letter case);
4. the user profile and every ``X:\\Users\\<name>`` -> ``%USERPROFILE%``;
5. the user name and the computer name as whole words -> ``<user>`` / ``<host>`` (skipped for
   names that are ordinary words such as ``User`` or ``Admin``: those only go away inside paths).

``counts`` tells how many replacements of each kind were made. stdlib only.
"""

from __future__ import annotations

import os
import re
from typing import Iterable

__all__ = ["Redactor", "SECRET_ENV", "COMMON_NAMES"]

#: Environment variable names whose values are secrets.
SECRET_ENV = re.compile(r"TOKEN|SECRET|PASSW(?:OR)?D|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL|AUTH|COOKIE|SESSION",
                        re.I)
#: User/host names that are ordinary words: not replaced in free text (paths are still redacted).
COMMON_NAMES = frozenset({"user", "users", "admin", "administrator", "owner", "guest", "default", "public", "home",
                          "pc", "desktop", "laptop", "windows", "satk", "gta", "game", "root", "test"})
_SKIP_PROFILES = frozenset({"public", "default", "default user", "all users"})

_SEP = r"[\\/]+"
_END = r"(?=[\\/\"'`\s<>|:;,)\]}]|$)"
_SECRET_KEY = r"[\w.-]*(?:token|secret|passw(?:or)?d|api[_-]?key|access[_-]?key|private[_-]?key|credential|cookie)[\w.-]*"
_TOKEN_SHAPES = [
    r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}",
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bAIza[0-9A-Za-z_-]{30,}",
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
]


def _path_regex(p: str) -> re.Pattern[str] | None:
    """Regex for one folder in any separator spelling and letter case, not matching longer names."""
    p = p.strip().rstrip("\\/")
    if len(p) < 3:
        return None
    parts = [re.escape(x) for x in re.split(r"[\\/]+", p) if x]
    if not parts:
        return None
    lead = _SEP if p[:1] in "\\/" else ""
    return re.compile(lead + _SEP.join(parts) + _END, re.I)


class Redactor:
    """Callable text redactor; see the module docstring."""

    def __init__(self, env: dict[str, str] | None = None, *, games: Iterable[str | os.PathLike | None] = (),
                 workspace: str | os.PathLike | None = None, satk: str | os.PathLike | None = None,
                 user: str | None = None, host: str | None = None) -> None:
        env = dict(os.environ if env is None else env)
        self.counts: dict[str, int] = {}
        self._rules: list[tuple[str, re.Pattern[str], str]] = []
        # 1. secrets
        values = sorted({v for k, v in env.items() if SECRET_ENV.search(k) and v and len(v) >= 8}, key=len,
                        reverse=True)
        for v in values:
            self._rules.append(("token", re.compile(re.escape(v)), "<redacted>"))
        self._rules.append(("token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer <redacted>"))
        self._rules.append(("token", re.compile(rf"(?i)\b({_SECRET_KEY})([\"']?\s*[:=]\s*[\"']?)([^\s\"',;{{}}]{{8,}})"),
                            r"\1\2<redacted>"))
        for shape in _TOKEN_SHAPES:
            self._rules.append(("token", re.compile(shape), "<redacted>"))
        # 2. e-mail addresses
        self._rules.append(("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "<email>"))
        # 3. named folders, longest first (a clean copy inside the workspace stays <game>)
        named: list[tuple[str, str, str]] = []
        for g in games:
            if g:
                named.append(("game", os.path.abspath(os.fspath(g)), "<game>"))
        if workspace:
            named.append(("workspace", os.path.abspath(os.fspath(workspace)), "<workspace>"))
        if satk:
            named.append(("satk", os.path.abspath(os.fspath(satk)), "<satk>"))
        # 4. the user profile
        profile = env.get("USERPROFILE") or env.get("HOME")
        if profile:
            named.append(("user", os.path.abspath(profile), "%USERPROFILE%"))
        seen: set[str] = set()
        for kind, path, repl in sorted(named, key=lambda t: len(t[1]), reverse=True):
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            rx = _path_regex(path)
            if rx is not None:
                self._rules.append((kind, rx, repl))
        skip = "|".join(re.escape(x) for x in sorted(_SKIP_PROFILES))
        self._rules.append(("user", re.compile(rf"(?i)\b[A-Z]:{_SEP}Users{_SEP}(?!(?:{skip}){_END})[^\\/:*?\"<>|\r\n,;)\]}}]+"),
                            "%USERPROFILE%"))
        # 5. user and computer names as words
        user = user if user is not None else (env.get("USERNAME") or env.get("USER"))
        host = host if host is not None else env.get("COMPUTERNAME")
        for kind, name, repl in (("user", user, "<user>"), ("host", host, "<host>")):
            if name and len(name) >= 3 and name.lower() not in COMMON_NAMES:
                self._rules.append((kind, re.compile(rf"(?i)(?<![\w-]){re.escape(name)}(?![\w-])"), repl))

    def __call__(self, text: str) -> str:
        for kind, rx, repl in self._rules:
            text, n = rx.subn(repl, text)
            if n:
                self.counts[kind] = self.counts.get(kind, 0) + n
        return text
