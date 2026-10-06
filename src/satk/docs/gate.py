"""``satk dev gate``: the single acceptance check before merging into ``main``.

Every step runs in its own subprocess from the checkout the gate itself runs from
(``REPO_ROOT``), so a worktree gates its own code. Steps that build shared state (the
vanilla index) use a private ``SATK_PATHS_WORK`` under ``work/tmp/gate`` and never touch
the shared index or caches.

``satk dev gate --changed`` runs the cheap steps plus only the tests a change can affect (:mod:`satk.docs.changed`); a
full gate takes one of at most two machine-wide slots (:mod:`satk.docs.gateslots`); every step runs with ``TEMP`` and
``TMP`` on the work drive and reports progress to stderr while it runs.

Inside a restricted environment (the Codex Windows sandbox) the gate still runs: :func:`detect_restrictions`
probes what is denied there and the steps work around it (in-process index build, a pytest plugin for owner-only
temp directories, tests that need Git Bash or asyncio subprocess pipes skip with a reason). On a normal machine
nothing differs.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.config import REPO_ROOT

if TYPE_CHECKING:
    from .changed import Selection

#: pytest output tail like "1932 passed, 1 skipped, 90 deselected in 90.98s".
_PYTEST_TAIL = re.compile(r"^=*\s*(\d+ (?:passed|failed|error).*?) in [\d.]+s", re.M)
#: A skip line of ``-ra`` whose reason starts with "sandbox:" (tests/sandbox_compat.py): "SKIPPED [3] file:12: sandbox: ...".
_SANDBOX_SKIP = re.compile(r"^SKIPPED \[(\d+)\] [^\n]*?: sandbox: ", re.M)
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
    #: Exit codes that count as a pass (pytest exits 5 when a selection collects no test).
    ok_codes: tuple[int, ...] = (0,)

    def describe(self) -> str:
        """The command without the interpreter and the fixed options (``dry-run`` and messages)."""
        a = list(self.argv)
        if a[1:3] == ["-m", "pytest"]:
            rest, skip = [], False
            for x in a[3:]:
                if skip:
                    skip = False
                elif x == "-p":
                    skip = True
                elif x != "-q" and not x.startswith("--deselect"):
                    rest.append(f'"{x}"' if " " in x else x)
            return "pytest " + " ".join(rest)
        return "satk " + " ".join(a[5:]) if a[1:5] == ["-X", "utf8", "-m", "satk"] else " ".join(a[1:])


@dataclass
class Result:
    name: str
    ok: bool
    seconds: float
    summary: str
    #: The step's complete output (``run(log_dir=...)``): what to read when it failed, instead of rerunning it.
    log: str = ""


def _pytest_summary(code: int, out: str) -> str:
    m = _PYTEST_TAIL.findall(out)
    tail = m[-1] if m else (out.strip().splitlines() or ["no output"])[-1][:160]
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M)
    sandboxed = sum(int(n) for n in _SANDBOX_SKIP.findall(out))
    return (tail + (f"; {sandboxed} skipped for the sandbox" if sandboxed else "")
            + (f"; first failures: {', '.join(failed[:5])}" if failed else ""))


def _last_json_object(out: str) -> dict | None:
    """The last output line that is a JSON object (stderr lines, such as a logged warning, may follow it)."""
    for line in reversed(out.strip().splitlines()):
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            return d if isinstance(d, dict) else None
    return None


def _json_summary(*keys: str) -> Callable[[int, str], str]:
    def summarize(code: int, out: str) -> str:
        d = _last_json_object(out)
        if d is None:
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


@dataclass
class Restrictions:
    """What this environment denies and how the gate works around it (empty on a normal machine)."""

    #: Human-readable findings, reported with the gate result.
    notes: list[str] = field(default_factory=list)
    #: Extra pytest arguments and environment of the two test steps.
    pytest_args: list[str] = field(default_factory=list)
    pytest_env: dict[str, str] = field(default_factory=dict)


def _load_sandbox_helper():
    """``tests/sandbox_compat.py`` of this checkout (``None`` without the tests folder or without pytest)."""
    if "sandbox_compat" in sys.modules:
        return sys.modules["sandbox_compat"]
    path = REPO_ROOT / "tests" / "sandbox_compat.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("sandbox_compat", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["sandbox_compat"] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # noqa: BLE001 - pytest missing: the gate cannot run its tests anyway
        sys.modules.pop("sandbox_compat", None)
        return None


def detect_restrictions() -> Restrictions:
    """Probe the restrictions of a sandboxed run (Codex ``unelevated`` Windows sandbox) and plan around them.

    * worker processes denied (named pipes): nothing to configure, satk runs its pools in-process;
    * owner-only temp directories unusable: the test steps load ``tests/sandbox_compat.py`` as a pytest plugin;
    * Git Bash / MSYS cannot start: the tests that need it skip with a reason (they probe it themselves).
    """
    from ..core.procpool import unavailable_reason

    r = Restrictions()
    why = unavailable_reason()
    if why:
        r.notes.append(f"worker processes unavailable ({why}): index build and other pools run in-process")
    helper = _load_sandbox_helper()
    if helper is not None:
        if helper.owner_only_dirs_unusable():
            r.notes.append("owner-only temp directories unusable: tests run with the tests/sandbox_compat.py plugin")
            tests = str(REPO_ROOT / "tests")
            r.pytest_args += ["-p", "sandbox_compat"]
            r.pytest_env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT / "src"), tests])
        sh = helper.find_sh()
        sh_why = helper.shell_failure(sh) if sh else None
        if sh_why:
            r.notes.append(f"POSIX shell cannot start ({sh_why}): tests that need it skip")
    return r


def plan(*, quick: bool, private_work: Path, restrictions: Restrictions | None = None,
         selection: "Selection | None" = None, with_game: bool = False) -> list[Step]:
    """The gate steps in order. ``quick`` skips the game-data steps (about 3 minutes).

    ``restrictions`` (from :func:`detect_restrictions`) adds the workarounds of a sandboxed run to the test steps.
    ``selection`` (``--changed``, from :func:`satk.docs.changed.select_from_git`) narrows the test steps to the tests a
    change can affect and keeps the game steps only when it asks for them (or ``with_game``); without it the plan is
    the full gate.
    """
    py = sys.executable
    satk = [py, "-X", "utf8", "-m", "satk"]
    pytest = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", *[f"--deselect={d}" for d in DESELECT]]
    pyenv: dict[str, str] = {}
    if restrictions is not None:
        pytest += restrictions.pytest_args
        pyenv = dict(restrictions.pytest_env)
    priv = {"SATK_PATHS_WORK": str(private_work)}
    narrow = selection is not None and not selection.all_tests
    paths = list(selection.paths) if narrow else []
    ok = (0, 5) if selection is not None else (0,)  # a narrowed run may collect nothing in one of its two passes
    steps = [
        Step("assetguard", [*satk, "dev", "assetguard", "--all", "--json"], summarize=_json_summary("checked")),
        Step("docs-generated", [*satk, "dev", "gen-docs", "--check", "--json"], summarize=_json_summary()),
        Step("agent-docs-sync", [*satk, "dev", "sync-agent-docs", "--check", "--json"], summarize=_json_summary(),
             skip=_no_published_agent_docs),
        Step("tests", [*pytest, "-m", "not game", *paths], env=pyenv, summarize=_pytest_summary, ok_codes=ok),
        Step("mcp-selftest", [*satk, "mcp", "selftest", "--json"], summarize=_json_summary("tools", "list_bytes")),
    ]
    game = not quick if selection is None else (not quick and (selection.game or with_game))
    if game:
        # Build the private index first: game-marked tests that need an index use it.
        game_paths = list(selection.game_paths) if narrow and not with_game else []
        steps += [
            Step("index-golden", [*satk, "index", "build", "--profile", "vanilla", "--json"], env=priv,
                 summarize=_json_summary("build_seconds")),
            Step("index-verify", [*satk, "index", "verify", "--profile", "vanilla", "--json"], env=priv,
                 summarize=_json_summary("checked", "failed")),
            Step("tests-game", [*pytest, "-m", "game", *game_paths], env={**priv, **pyenv}, summarize=_pytest_summary,
                 ok_codes=ok),
        ]
    return steps


# --------------------------------------------------------------------------- running

#: pytest ``-q`` progress line: dots and letters, then the percentage.
_PROGRESS = re.compile(r"^[.sxXFE]+\s+\[\s*(\d+)%\]\s*$")


def _fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def _kill_tree(proc: subprocess.Popen) -> None:
    """Stop the step and everything it started (a timed-out pytest has worker children)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_step(st: Step, env: dict[str, str], tag: str, say: Callable[[str], None] | None,
              heartbeat: float) -> tuple[int, str]:
    """Run one step; stdout and stderr are collected, and ``say`` gets a heartbeat while it runs (pytest percent)."""
    proc = subprocess.Popen(st.argv, cwd=REPO_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    lines: list[str] = []
    state = {"pct": None, "bad": 0}

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            m = _PROGRESS.match(line.rstrip())
            if m:
                state["pct"] = int(m.group(1))
                state["bad"] += sum(line.count(c) for c in "FE")

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    t0 = time.perf_counter()
    code: int | None = None
    while code is None:
        left = st.timeout - (time.perf_counter() - t0)
        if left <= 0:
            _kill_tree(proc)
            reader.join(10)
            return -1, "".join(lines) + f"\ntimeout after {st.timeout} s"
        try:
            code = proc.wait(timeout=min(heartbeat, left))
        except subprocess.TimeoutExpired:
            if say:
                pct = f", {state['pct']}%" if state["pct"] is not None else ""
                bad = f", {state['bad']} failing so far" if state["bad"] else ""
                say(f"gate: {tag} still running ({_fmt(time.perf_counter() - t0)}{pct}{bad})")
    reader.join(30)
    return code, "".join(lines)


def run(steps: list[Step], *, keep_going: bool = True, log: Callable[[str], None] | None = None,
        temp_dir: Path | None = None, heartbeat: float = 30.0, log_dir: Path | None = None) -> list[Result]:
    """Run ``steps`` in order; stop at the first failure unless ``keep_going``.

    ``log`` receives progress lines (``[n/total] step ...``, a heartbeat of long steps, ``step ok in 12.3s``).
    ``temp_dir``: ``TEMP``/``TMP``/``TMPDIR`` of every step, so tests never depend on the space left on the system
    drive. ``heartbeat`` is the interval (s) of the "still running" lines. ``log_dir``: every step's complete output
    is written to ``<n>-<step>.log`` there, and a failed step names its file.
    """
    out: list[Result] = []
    base_env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(REPO_ROOT / "src"))
    if temp_dir is not None:
        temp_dir.mkdir(parents=True, exist_ok=True)
        base_env.update(TEMP=str(temp_dir), TMP=str(temp_dir), TMPDIR=str(temp_dir))
    for n, st in enumerate(steps, 1):
        tag = f"[{n}/{len(steps)}] {st.name}"
        reason = st.skip() if st.skip else None
        if reason:
            out.append(Result(st.name, True, 0.0, reason))
            if log:
                log(f"gate: {tag} {reason}")
            continue
        if log:
            log(f"gate: {tag} ...")
        t0 = time.perf_counter()
        try:
            code, text = _run_step(st, {**base_env, **st.env}, tag, log, heartbeat)
        except OSError as e:
            code, text = -1, f"cannot start: {e}"
        summary = st.summarize(code, text) if st.summarize else (text.strip().splitlines() or [""])[-1][:160]
        ok = code in st.ok_codes
        seconds = round(time.perf_counter() - t0, 1)
        path = _write_log(log_dir, n, st.name, code, text)
        out.append(Result(st.name, ok, seconds, summary, path))
        if log:
            log(f"gate: {tag} {'ok' if ok else 'FAILED'} in {_fmt(seconds)}: {summary}"
                + (f"; output: {path}" if path and not ok else ""))
        if not ok and not keep_going:
            break
    return out


def _write_log(log_dir: Path | None, n: int, name: str, code: int, text: str) -> str:
    """Save a step's output (``""`` without ``log_dir`` or when it cannot be written)."""
    if log_dir is None:
        return ""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        p = log_dir / f"{n:02d}-{name}.log"
        p.write_text(f"exit code {code}\n{text}", encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return p.as_posix()


# --------------------------------------------------------------------------- private folders


@dataclass
class Workdirs:
    """The private folders of one gate run: ``work/tmp/gate-<checkout hash>/{work,tmp}``."""

    root: Path
    work: Path
    temp: Path
    owner: Path | None = None
    notes: list[str] = field(default_factory=list)
    #: ``gate-<hash>-<pid>``: taken because another gate of the checkout was running; removed whole after a green run.
    sibling: bool = False

    @property
    def logs(self) -> Path:
        """The output of every step of this run: kept after a green run too (small), replaced by the next run."""
        return self.root / "logs"

    def release(self, *, clean: bool) -> None:
        """Drop the owner mark. With ``clean`` (a green run) also the temp folder and the private work (a vanilla index
        and caches, gigabytes: every run starts with fresh ones anyway), so the work drive keeps its space; a failed
        run keeps both for a look."""
        if self.owner is not None:
            try:
                self.owner.unlink()
            except OSError:
                pass
            self.owner = None
        if not clean:
            return
        from .cleanup import remove_tree

        for d in ((self.root,) if self.sibling else (self.temp, self.work)):
            left = remove_tree(d, 5.0)
            if left:  # harmless: the next run wipes it
                self.notes.append(f"{d.name} folder not removed ({len(left)} file(s) left): {d.as_posix()}")


def _sweep_dead_siblings(base: Path, key: str) -> None:
    """Remove ``gate-<key>-<pid>`` folders whose gate is gone (best effort): they would pile up on the work drive."""
    from ..saap.client import pid_alive
    from .cleanup import remove_tree

    for d in base.glob(f"gate-{key}-*"):
        tail = d.name.rsplit("-", 1)[-1]
        if tail.isdigit() and not pid_alive(int(tail)):
            remove_tree(d, 1.0)  # a leftover just stays for the next sweep


def claim_workdirs(key: str) -> Workdirs:
    """Fresh private ``work`` and ``temp`` folders for the checkout ``key``.

    One folder pair per checkout: the old pair is wiped. When another live gate of the same checkout owns that
    folder (``owner.json`` names a live pid), this run takes a sibling ``gate-<key>-<pid>`` instead and wipes nothing,
    so a quick check next to a full gate never deletes the files the full gate is using.
    """
    from ..core.paths import tmp
    from ..saap.client import pid_alive
    from .cleanup import remove_tree

    me = os.getpid()
    root = tmp(f"gate-{key}")
    _sweep_dead_siblings(root.parent, key)
    owner = root / "owner.json"
    notes: list[str] = []
    other = None
    try:
        other = json.loads(owner.read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError, AttributeError):
        pass
    sibling = False
    if isinstance(other, int) and other != me and pid_alive(other):
        root, sibling = tmp(f"gate-{key}-{me}"), True
        owner = root / "owner.json"
        notes.append(f"another gate of this checkout runs (pid {other}): private folders in {root.name}")
    work, temp = root / "work", root / "tmp"
    for d in (work, temp, root / "logs"):
        if d.exists() and remove_tree(d, 5.0):  # a fresh folder: no stale index, caches or temp files between runs
            # a restricted token may not delete what an earlier unrestricted run created: use a sibling instead
            root, sibling = tmp(f"gate-{key}-{me}"), True
            owner = root / "owner.json"
            work, temp = root / "work", root / "tmp"
            notes.append(f"the old private folder {d.name} cannot be removed; using {root.name}")
            break
    temp.mkdir(parents=True, exist_ok=True)
    try:
        owner.write_text(json.dumps({"pid": me}), encoding="utf-8")
    except OSError:
        owner = None  # type: ignore[assignment]
    return Workdirs(root, work, temp, owner, notes, sibling)
