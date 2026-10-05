"""``satk dev gate``: the single acceptance check before merging into ``main``.

Every step runs in its own subprocess from the checkout the gate itself runs from
(``REPO_ROOT``), so a worktree gates its own code. Steps that build shared state (the
vanilla index) use a private ``SATK_PATHS_WORK`` under ``work/tmp/gate`` and never touch
the shared index or caches.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.config import REPO_ROOT

#: pytest output tail like "1932 passed, 1 skipped, 90 deselected in 90.98s".
_PYTEST_TAIL = re.compile(r"^=*\s*(\d+ (?:passed|failed|error).*?) in [\d.]+s", re.M)
#: The real MTA-fork build tests write outside the workspace and take minutes.
DESELECT = ("tests/engine/test_real_fork.py",)


@dataclass
class Step:
    name: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    summarize: Callable[[int, str], str] | None = None
    timeout: int = 1800
    #: Returns why the step does not apply here (it is then reported as passed and skipped), else ``None``.
    skip: Callable[[], str | None] | None = None


@dataclass
class Result:
    name: str
    ok: bool
    seconds: float
    summary: str


def _pytest_summary(code: int, out: str) -> str:
    m = _PYTEST_TAIL.findall(out)
    tail = m[-1] if m else (out.strip().splitlines() or ["no output"])[-1][:160]
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M)
    return tail + (f"; first failures: {', '.join(failed[:5])}" if failed else "")


def _json_summary(*keys: str) -> Callable[[int, str], str]:
    def summarize(code: int, out: str) -> str:
        try:
            d = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return (out.strip().splitlines() or ["no output"])[-1][:160]
        if not d.get("ok", False):
            err = d.get("error") or {}
            return f"{err.get('code', 'FAILED')}: {err.get('msg', '')}"[:200]
        return ", ".join(f"{k}={d[k]}" for k in keys if k in d) or "ok"

    return summarize


def _no_published_agent_docs() -> str | None:
    """Skip reason of ``agent-docs-sync`` outside a development workspace: no copy was ever published there."""
    from ..core.paths import cfg
    from .sync import plan as sync_plan

    pairs = sync_plan(REPO_ROOT, Path(cfg().paths.workspace))
    if pairs and all(p.state == "missing" for p in pairs):
        return "skipped: no agent docs published into this workspace (satk dev sync-agent-docs)"
    return None


def plan(*, quick: bool, private_work: Path) -> list[Step]:
    """The gate steps in order. ``quick`` skips the game-data steps (about 3 minutes)."""
    py = sys.executable
    satk = [py, "-X", "utf8", "-m", "satk"]
    pytest = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", *[f"--deselect={d}" for d in DESELECT]]
    priv = {"SATK_PATHS_WORK": str(private_work)}
    steps = [
        Step("assetguard", [*satk, "dev", "assetguard", "--all", "--json"], summarize=_json_summary("checked")),
        Step("docs-generated", [*satk, "dev", "gen-docs", "--check", "--json"], summarize=_json_summary()),
        Step("agent-docs-sync", [*satk, "dev", "sync-agent-docs", "--check", "--json"], summarize=_json_summary(),
             skip=_no_published_agent_docs),
        Step("tests", [*pytest, "-m", "not game"], summarize=_pytest_summary),
        Step("mcp-selftest", [*satk, "mcp", "selftest", "--json"], summarize=_json_summary("tools", "list_bytes")),
    ]
    if not quick:
        # Build the private index first: game-marked tests that need an index use it.
        steps += [
            Step("index-golden", [*satk, "index", "build", "--profile", "vanilla", "--json"], env=priv,
                 summarize=_json_summary("build_seconds")),
            Step("index-verify", [*satk, "index", "verify", "--profile", "vanilla", "--json"], env=priv,
                 summarize=_json_summary("checked", "failed")),
            Step("tests-game", [*pytest, "-m", "game"], env=priv, summarize=_pytest_summary),
        ]
    return steps


def run(steps: list[Step], *, keep_going: bool = True, log: Callable[[str], None] | None = None) -> list[Result]:
    """Run ``steps`` in order; stop at the first failure unless ``keep_going``."""
    out: list[Result] = []
    base_env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(REPO_ROOT / "src"))
    for st in steps:
        if log:
            log(f"gate: {st.name} ...")
        reason = st.skip() if st.skip else None
        if reason:
            out.append(Result(st.name, True, 0.0, reason))
            continue
        t0 = time.perf_counter()
        try:
            p = subprocess.run(st.argv, cwd=REPO_ROOT, env={**base_env, **st.env}, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=st.timeout)
            code, text = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            code, text = -1, f"timeout after {st.timeout} s"
        summary = st.summarize(code, text) if st.summarize else (text.strip().splitlines() or [""])[-1][:160]
        out.append(Result(st.name, code == 0, round(time.perf_counter() - t0, 1), summary))
        if code != 0 and not keep_going:
            break
    return out
