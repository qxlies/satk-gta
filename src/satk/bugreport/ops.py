"""Operation of satk.bugreport: ``satk bug-report`` (M3 A2)."""

from __future__ import annotations

from satk.core.registry import op


@op("bug_report",
    summary="Prepare a bug report for satk: versions, OS, doctor results, configuration, game edition, import "
            "errors and recent errors.log entries, with paths, user/host names and tokens redacted; no game "
            "files. Shows it first; --yes writes it to work/out/bugreport.",
    summary_ru="Отчёт об ошибке satk: версии, ОС, doctor, настройки, редакция игры, последние ошибки; пути, имя "
               "пользователя и токены скрыты, файлов игры нет. Сначала показывает, --yes записывает.",
    mcp=False, group="core",
    examples=("satk bug-report", "satk bug-report --what \"index build stops at 40%\" --yes",
              "satk bug-report --logs 0 --out report.md --yes"))
def bug_report(what: str | None = None, logs: int = 3, out: str | None = None, yes: bool = False) -> dict:
    """Collect, redact and (after confirmation) write a Markdown bug report; nothing is sent anywhere.

    Without --yes the report is shown first: in a terminal satk asks before writing, elsewhere the
    result holds the full text and nothing is written.

    Args:
        what: what happened, in your words (goes to the top of the report).
        logs: how many recent errors.log entries to include (0 = none).
        out: output file (default: work/out/bugreport/satk-bug-report-<time>.md).
        yes: write without asking.
    """
    from .report import bug_report as _run

    return _run(what=what, logs=logs, out=out, yes=yes)
