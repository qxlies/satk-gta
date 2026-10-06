"""Limits operations; discovery imports only the standard library and core contracts."""

from __future__ import annotations

from ..core.registry import op


@op("limits.plan", mcp=False, group="asset",
    summary="Plan stock 1.0 US engine capacity for an install and mods. Count effective content, distinguish "
            "runtime pools, link known crashes, and write separate fastman92 and Open Limit Adjuster INI "
            "suggestions under work/out/limits without changing the game.",
    summary_ru="Планирование лимитов движка для установленной игры и модов; отчёт и предложения настроек в work/out/limits.",
    examples=("satk limits plan", "satk limits plan --profile vanilla",
              "satk limits plan --profile installed --mods modloader/mymap"))
def limits_plan(profile: list[str] | None = None, mods: list[str] | None = None, base: str = "installed",
                area: list[float] | None = None, limit: int = 20, cursor: str | None = None) -> dict:
    """Inspect the game and effective Mod Loader content without building an index.

    Args:
        profile: a profile name, mod folders, or a profile followed by mod folders; default installed.
        mods: additional mod folders, ZIPs or IMGs, resolved with the installed Mod Loader priorities.
        base: underlying profile when profile contains only mod paths (default installed).
        area: optional x y radius streaming scenario; otherwise report a minimum, not a whole-map peak.
        limit: table rows per page (max 500); the saved report and INIs always cover every limit.
        cursor: next cursor from an earlier page of the same plan.
    """
    from .planner import plan

    return plan(profile=profile, mods=mods, base=base, area=area, limit=limit, cursor=cursor)
