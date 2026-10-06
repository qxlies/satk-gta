"""Operations of satk.docs (owner: docs, A3): ``dev linkcheck``, ``dev docs-smoke``, ``dev docs-parity``,
``dev workflow-cost``, ``dev sync-agent-docs``, ``dev gate``.

CLI only (``mcp=False``): these are developer/acceptance tools, not agent tools.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from ..core.config import REPO_ROOT, SRC_ROOT
from ..core.envelope import table
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, jpath, work
from ..core.registry import op


@op("dev.linkcheck",
    summary="Check that paths and links mentioned in Markdown files exist (links, absolute paths, relative "
            "paths in inline code). Missing paths under work/ are warnings.",
    summary_ru="Проверить, что пути и ссылки в Markdown существуют (ссылки, абсолютные пути, относительные пути "
               "в inline-коде). Отсутствующее под work/ — предупреждение.",
    mcp=False, group="dev",
    examples=("satk dev linkcheck <workspace>/CLAUDE.md", "satk dev linkcheck docs --show-ok"))
def linkcheck(files: list[str], base: str | None = None, show_ok: bool = False) -> dict:
    """Check Markdown files (directories: all ``*.md`` inside); exit 1 if a path is missing.

    Args:
        files: Markdown files or directories.
        base: extra base directory for relative paths in inline code (tried first).
        show_ok: list every checked reference, not only problems.
    """
    from .linkcheck import check_paths, config_roots

    missing_inputs = [f for f in files if not Path(f).exists()]
    if missing_inputs:
        raise SatkError("NOT_FOUND", f"no such file or directory: {missing_inputs[0]}")
    t0 = time.perf_counter()
    checked, findings = check_paths(files, bases=[Path(base)] if base else (), **config_roots(cfg()))
    bad = [f for f in findings if f.status in ("missing", "anchor")]
    warn = [f for f in findings if f.status == "generated"]
    rows = [f.row() for f in findings if show_ok or f.status != "ok"]
    counts = {"files": len(checked), "refs": len(findings), "passed": sum(f.status == "ok" for f in findings),
              "missing": len(bad), "generated": len(warn), "ms": round((time.perf_counter() - t0) * 1000, 1)}
    cols = ["file", "line", "ref", "status", "detail"]
    if bad:
        raise SatkError("NOT_FOUND", f"{len(bad)} missing path(s)/anchor(s) in {len({f.file for f in bad})} file(s)",
                        hint="fix the path, or mark an intentional example with <!-- linkcheck: ignore -->",
                        data={**counts, "cols": cols, "rows": rows})
    env = table(cols, rows, warn=[f"{len(warn)} generated path(s) under work/ do not exist yet"] if warn else ())
    env.update(counts)
    return env


@op("dev.docs_smoke",
    summary="Run the 'Quick example' shell blocks of the user docs (docs/en and docs/ru); fail on any error or "
            "unknown command. Pages that rebuild shared state (index/re/kb build, notes) run in a private work dir.",
    summary_ru="Выполнить блоки «Быстрый пример» руководства (docs/en и docs/ru); ошибка, если хоть одна команда "
               "упала или не существует. Страницы, которые пересобирают общее (index/re/kb build, заметки), — в своём work.",
    mcp=False, group="dev", long_running=True,
    examples=("satk dev docs-smoke", "satk dev docs-smoke docs/en/quickstart.md --dry-run", "satk dev docs-smoke --strict"))
def docs_smoke(pages: list[str] | None, strict: bool = False, dry_run: bool = False,
               timeout: float = 600.0, isolate: Literal["auto", "all", "none"] = "auto") -> dict:
    """Execute the quick examples; the log with full outputs goes to ``work/logs/docs-smoke-*.jsonl``.

    Args:
        pages: Markdown pages (default: docs/en/*.md and docs/ru/*.md of this checkout, without _*.md).
        strict: non-satk command lines in a quick example fail instead of being skipped.
        dry_run: only parse and classify, run nothing.
        timeout: seconds per command.
        isolate: private work dir (SATK_PATHS_WORK under work/tmp/docs-smoke, removed afterwards) for: auto = pages
            that rebuild shared state (index/re/kb build, paths import, note/bookmark writes); all = every page;
            none = no page.
    """
    import tempfile

    from ..core.cli import CommandTree
    from ..core.paths import ensure_writable, tmp
    from ..core.registry import all_ops, report_progress
    from .cleanup import remove_tree
    from .smoke import classify, default_pages, extract_commands, run_command, shared_state_writer

    paths = [Path(p) for p in pages] if pages else default_pages(REPO_ROOT)
    if not paths:
        raise SatkError("NOT_FOUND", "no pages to check", hint="satk dev docs-smoke docs/en/quickstart.md")
    for p in paths:
        if not p.is_file():
            raise SatkError("NOT_FOUND", f"no such page: {p}")
    tree = CommandTree.build(all_ops())
    cmds = []
    for p in paths:
        try:
            rel = p.resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = jpath(p)
        cmds.extend(extract_commands(p.read_text(encoding="utf-8"), rel))
    for c in cmds:
        c.status, c.detail = classify(c, tree, strict=strict)
    # pages that get their own work directory (the whole page: its later commands read what it built)
    writers: dict[str, str] = {}
    for c in cmds:
        w = shared_state_writer(c, tree) if c.status == "run" else None
        if w and c.page not in writers:
            writers[c.page] = w
    private = {c.page for c in cmds} if isolate == "all" else set(writers) if isolate == "auto" else set()
    work_dirs: dict[str, Path] = {}
    log_lines: list[str] = []
    for k, c in enumerate(cmds):
        if c.status != "run":
            continue
        if dry_run:
            c.status = "would-run"
            if c.page in private:
                c.detail = "private work dir" + (f" ({writers[c.page]})" if c.page in writers else "")
            continue
        env_extra = None
        if c.page in private:
            if c.page not in work_dirs:
                work_dirs[c.page] = Path(tempfile.mkdtemp(prefix=Path(c.page).stem + "-", dir=tmp("docs-smoke")))
            env_extra = {"SATK_PATHS_WORK": str(work_dirs[c.page])}
        report_progress(k, len(cmds), c.text)
        code, envelope, out, err, ms = run_command(c.argv or [], src=SRC_ROOT, cwd=REPO_ROOT, timeout=timeout,
                                                   env_extra=env_extra)
        c.code, c.ms = code, round(ms, 1)
        if code == 0:
            c.status, c.detail = "ok", ""
        else:
            c.status = "fail"
            if envelope and isinstance(envelope.get("error"), dict):
                e = envelope["error"]
                c.detail = f"{e.get('code')}: {e.get('msg')}"[:200]
            else:
                tail = (err.strip().splitlines() or out.strip().splitlines() or [""])[-1]
                c.detail = (f"exit {code}: " + tail)[:200]
        log_lines.append(json.dumps({"page": c.page, "line": c.line, "cmd": c.text, "argv": c.argv, "exit": code,
                                     "ms": c.ms, "work": jpath(work_dirs[c.page]) if c.page in work_dirs else None,
                                     "stdout": out[-20000:], "stderr": err[-20000:]}, ensure_ascii=False))
    # private work dirs: removed when the page passed, kept (and reported) for a failing page
    private_work: dict[str, str] = {}
    for page, d in work_dirs.items():
        if any(c.page == page and c.status == "fail" for c in cmds):
            private_work[page] = jpath(d)
        else:
            ensure_writable(d)
            left = remove_tree(d)
            private_work[page] = "removed" if not left else f"NOT removed: {jpath(d)} ({', '.join(left[:3])})"
    counts = {key: sum(c.status == st for c in cmds)
              for key, st in (("passed", "ok"), ("failed", "fail"), ("skipped", "skip"), ("would_run", "would-run"))}
    log = None
    if log_lines:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        log = atomic_write(work("logs", f"docs-smoke-{stamp}.jsonl"), "\n".join(log_lines) + "\n")
    cols = ["page", "line", "command", "status", "exit", "ms", "detail"]
    rows = [c.row() for c in cmds]
    summary = {"pages": len(paths), "commands": len(cmds), **{k: v for k, v in counts.items() if v}}
    if private:
        summary["private_work"] = private_work or {p: f"would use one ({writers.get(p, 'isolate=all')})"
                                                   for p in sorted(private)}
    if log:
        summary["log"] = jpath(log)
    if counts["failed"]:
        raise SatkError("EXTERNAL_TOOL", f"{counts['failed']} quick-example command(s) failed",
                        hint=f"see {jpath(log)}" if log else "fix the commands marked 'fail'",
                        data={**summary, "cols": cols, "rows": rows})
    stuck = [v for v in private_work.values() if v.startswith("NOT removed")]
    env = table(cols, rows, warn=[f"private work dir {v}" for v in stuck])
    env.update(summary)
    return env


@op("dev.docs_parity",
    summary="Check that the English user docs and their Russian mirror match: README.md/README.ru.md and "
            "docs/en/<page>.md/docs/ru/<page>.md both exist, start with the language switch, and have the same "
            "sections, code blocks and quick-example commands.",
    summary_ru="Проверить, что английское руководство и русское зеркало совпадают: README.md/README.ru.md и "
               "docs/en/<стр>.md/docs/ru/<стр>.md есть оба, начинаются с переключателя языка, разделы, блоки кода "
               "и команды «Быстрого примера» те же.",
    mcp=False, group="dev",
    examples=("satk dev docs-parity",))
def docs_parity(root: str | None = None) -> dict:
    """List every parity problem; exit 1 (CHECK_FAILED) if there is one.

    Args:
        root: repository checkout to check (default: the checkout satk runs from).
    """
    from .parity import check

    repo = Path(root) if root else REPO_ROOT
    if not (repo / "docs").is_dir():
        raise SatkError("NOT_FOUND", f"no docs/ directory in {jpath(repo)}", hint="satk dev docs-parity --root <checkout>")
    n, issues = check(repo)
    cols = ["page", "check", "detail"]
    rows = [i.row() for i in issues]
    if issues:
        raise SatkError("CHECK_FAILED", f"{len(issues)} parity problem(s) in {len({i.page for i in issues})} page(s)",
                        hint="add the missing mirror or fix the page; the checks are listed in satk.docs.parity",
                        data={"pairs": n, "cols": cols, "rows": rows})
    env = table(cols, rows)
    env["pairs"] = n
    return env


@op("dev.workflow_cost",
    summary="Run the agent workflows S1-S8 of docs/agent/workflows.md through the MCP dispatch path and report "
            "calls and estimated tokens per workflow (text chars/3.5, images w*h/750).",
    summary_ru="Прогнать сценарии агента S1–S8 (docs/agent/workflows.md) так, как их вызывает MCP, и показать "
               "число вызовов и оценку токенов (текст: символы/3,5; картинки: w*h/750).",
    mcp=False, group="dev", long_running=True,
    examples=("satk dev workflow-cost", "satk dev workflow-cost S1 S3 S7 --steps",
              "satk dev workflow-cost S2 --target ariane"))
def workflow_cost(workflows: list[str] | None, target: Literal["mock", "ariane"] = "mock",
                  steps: bool = False) -> dict:
    """Execute the documented call chains and measure them (they need the built indexes/symdb/Blender).

    Args:
        workflows: ids S1..S8 (default: all; S5 runs Blender for ~15 s, S6 needs target=game and skips).
        target: viewer target of S2 (ariane starts the viewer window and stops it afterwards).
        steps: also return every call of every workflow (tool, args, chars, tokens, image size, ms).
    """
    from ..core.registry import report_progress
    from .workflows import CHARS_PER_TOKEN, WORKFLOWS, run

    ids = [w.upper() for w in (workflows or list(WORKFLOWS))]
    bad = [w for w in ids if w not in WORKFLOWS]
    if bad:
        raise SatkError("BAD_PARAMS", f"unknown workflow {bad[0]!r}", did_you_mean=list(WORKFLOWS))
    results = []
    for k, w in enumerate(ids):
        report_progress(k, len(ids), w)
        results.append(run(w, target=target) if w == "S2" else run(w))
    cols = ["wf", "status", "calls", "budget", "tokens", "images", "seconds", "result"]
    rows = [[r["wf"], r["status"], r["calls"], r["budget"], r["tokens"], r["images"], r["seconds"],
             json.dumps(r.get("answer", r.get("reason")), ensure_ascii=False, separators=(",", ":"), default=str)[:300]]
            for r in results]
    env = table(cols, rows)
    env["method"] = f"tokens = (args + result chars) / {CHARS_PER_TOKEN} + image w*h/750; calls include image Reads"
    if steps:
        env["steps"] = {r["wf"]: {"cols": ["tool", "args", "chars", "tokens", "ok", "image", "ms"], "rows": r["steps"]}
                        for r in results}
    over = [r["wf"] for r in results if r["status"] == "ok" and r["calls"] > r["budget"]]
    failed = [r["wf"] for r in results if r["status"] == "fail"]
    if over:
        env.setdefault("warn", []).append(f"OVER_BUDGET: {', '.join(over)} need more calls than documented")
    if failed:
        raise SatkError("EXTERNAL_TOOL", f"workflow(s) failed: {', '.join(failed)}", hint="satk dev workflow-cost "
                        + " ".join(failed) + " --steps", data=env)
    return env


@op("dev.sync_agent_docs",
    summary="Copy agent docs from the repo to the workspace: docs/agent/SKILL.md -> .claude/skills/satk/SKILL.md "
            "and, in a development checkout, the workspace CLAUDE.md source -> CLAUDE.md. --check only reports drift.",
    summary_ru="Скопировать агентные документы из репозитория в рабочее пространство (SKILL.md и CLAUDE.md); "
               "--check только сообщает о расхождениях.",
    mcp=False, group="dev",
    examples=("satk dev sync-agent-docs --check", "satk dev sync-agent-docs"))
def sync_agent_docs(check: bool = False, force: bool = False) -> dict:
    """Publish (or with ``check`` verify) the workspace copies of the agent docs.

    Args:
        check: do not write; exit 1 (REVISION) if a copy is missing or differs.
        force: overwrite a workspace copy that was edited after its repo source.
    """
    from .sync import plan

    ws = Path(cfg().paths.workspace)
    pairs = plan(REPO_ROOT, ws)
    cols = ["src", "dst", "state", "dst_newer"]
    no_src = [p for p in pairs if p.state == "no-source"]
    if no_src:
        raise SatkError("NOT_FOUND", f"source missing: {no_src[0].src.as_posix()}")
    stale = [p for p in pairs if p.state != "same"]
    if check:
        if stale:
            raise SatkError("REVISION", f"{len(stale)} workspace copy(ies) out of date",
                            hint="satk dev sync-agent-docs", data={"cols": cols, "rows": [p.row() for p in pairs]})
        return table(cols, [p.row() for p in pairs])
    edited = [p for p in stale if p.dst_newer and not force]
    if edited:
        raise SatkError("EXISTS", f"workspace copy edited after its source: {edited[0].dst.as_posix()}",
                        hint="move the edit into the repo source, then re-run (or --force to overwrite)",
                        data={"cols": cols, "rows": [p.row() for p in pairs]})
    written = []
    for p in stale:
        atomic_write(p.dst, p.src.read_bytes())
        written.append(jpath(p.dst))
    env = table(cols, [p.row() for p in plan(REPO_ROOT, ws)])
    env["written"] = written
    return env


@op("dev.gate",
    summary="Acceptance gate before merging into main: assetguard, generated/agent docs, the non-game test suite, "
            "MCP selftest, and (unless --quick) game-marked tests plus a private vanilla index build and golden verify.",
    summary_ru="Проверка перед слиянием в main: assetguard, сгенерированные и агентные документы, тесты, MCP selftest; "
               "без --quick ещё тесты на данных игры и сборка индекса vanilla с эталонными числами (в своём work).",
    mcp=False, group="dev", long_running=True,
    examples=("satk dev gate", "satk dev gate --quick", "satk dev gate --changed", "satk dev gate --stop-on-fail"))
def gate(quick: bool = False, stop_on_fail: bool = False, changed: bool = False, base: str = "main",
         with_game: bool = False, dry_run: bool = False) -> dict:
    """Run every acceptance step in a subprocess; exit 1 (CHECK_FAILED) if any step fails.

    Progress goes to stderr (a line per step, a heartbeat with the pytest percentage while a long step runs); the
    complete output of every step is kept in ``work/tmp/gate-<checkout hash>/logs`` and a failed step names its file.
    A full gate takes one of at most two machine-wide gate slots (``SATK_GATE_SLOTS`` overrides, 0 = unlimited) and
    waits with a message while both are busy; ``--quick`` and ``--changed`` never wait (except a ``--changed`` whose
    selection degenerates to everything, game steps included: that is a full gate). Every step runs with TEMP/TMP in
    ``work/tmp/gate-<checkout hash>/tmp``, not on the system drive.

    Args:
        quick: skip the steps that read game data (about 3 minutes instead of 6).
        stop_on_fail: stop at the first failing step instead of running all of them.
        changed: intermediate check: the cheap steps (assetguard, docs, MCP selftest) plus only the tests that the
            files changed since the merge-base with ``--base`` can affect (printed with the reason for each); the game
            steps only when index, formats or game code changed. The merge acceptance stays the full gate.
        base: branch the merge-base is taken against for ``--changed``.
        with_game: with ``--changed``, run the game steps (private index build, golden verify, game tests) anyway.
        dry_run: print the planned steps and the test selection without running anything.
    """
    import sys

    from .changed import select_from_git
    from .gate import claim_workdirs, detect_restrictions, plan, run
    from .gateslots import hold, slot_limit

    def say(msg: str) -> None:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()

    selection = select_from_git(REPO_ROOT, base) if changed else None
    if selection is not None:
        for line in selection.lines():
            say(f"gate: {line}")
    restrictions = detect_restrictions()
    cols = ["step", "ok", "seconds", "summary"]
    if dry_run:
        steps = plan(quick=quick, private_work=Path("<private work>"), restrictions=restrictions, selection=selection,
                     with_game=with_game)
        env = table(cols, [[st.name, True, 0.0, "planned: " + st.describe()] for st in steps])
        env["dry_run"] = True
        if selection is not None:
            env["selection"] = selection.summary()
        return env

    key = _checkout_key(REPO_ROOT)
    # A full gate (everything, game steps included) is the heavy kind the slots limit; --quick and --changed are light.
    heavy = not quick and (selection is None or (selection.all_tests and (selection.game or with_game)))
    label = f"gate-{key}"
    slot_note = []
    with hold(label, root=str(REPO_ROOT), say=say) if heavy else _no_slot() as slot:
        if heavy and slot.waited >= 1.0:
            slot_note.append(f"waited {slot.waited:g} s for a gate slot ({slot_limit()} at most at once)")
        dirs = claim_workdirs(key)
        notes: list[str] = list(dirs.notes) + restrictions.notes
        results: list = []
        try:
            steps = plan(quick=quick, private_work=dirs.work, restrictions=restrictions, selection=selection,
                         with_game=with_game)
            results = run(steps, keep_going=not stop_on_fail, log=say, temp_dir=dirs.temp, log_dir=dirs.logs)
        finally:
            dirs.release(clean=bool(results) and all(r.ok for r in results))
        notes += [n for n in dirs.notes if n not in notes]
    rows = [[r.name, r.ok, r.seconds, r.summary + (f"; output: {r.log}" if r.log and not r.ok else "")] for r in results]
    failed = [r.name for r in results if not r.ok]
    total = round(sum(r.seconds for r in results), 1)
    say(f"gate: {'FAILED: ' + ', '.join(failed) if failed else 'ok'} ({len(results)} steps, {total:g} s)")
    if failed:
        data: dict = {"cols": cols, "rows": rows}
        if notes:
            data["restricted_environment"] = notes
        if selection is not None:
            data["selection"] = selection.summary()
        raise SatkError("CHECK_FAILED", f"gate failed: {', '.join(failed)}",
                        hint="fix the failing steps; details: rerun the step's command shown in docs/ru/workflows.md",
                        data=data)
    env = table(cols, rows)
    env["seconds"] = round(sum(r.seconds for r in results), 1)
    if selection is not None:
        env["selection"] = selection.summary()
    if slot_note:
        env["slot"] = slot_note[0]
    if notes:
        env.setdefault("warn", []).extend(f"UNSUPPORTED: restricted environment: {n}" for n in notes)
    return env


def _checkout_key(root: Path) -> str:
    """Short stable id of a checkout: its private gate folders are named after it."""
    import hashlib

    return hashlib.blake2b(str(root).lower().encode("utf-8"), digest_size=4).hexdigest()


class _no_slot:
    """The slot of a run that does not take one (``--quick``, ``--changed``)."""

    def __enter__(self):
        from .gateslots import Slot

        return Slot(None)

    def __exit__(self, *exc) -> None:
        return None
