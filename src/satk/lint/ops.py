"""Operations of ``satk.lint`` (M2-06): ``asset lint`` and ``asset lint-rules`` (CLI only, ``mcp=False``;
MCP reaches them through the generic operation tool).

Module-level imports stay stdlib/satk only (SPEC §2.3).
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from ..core.envelope import clamp_limit, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, jpath, work
from ..core.registry import op
from .rules import SEV_RANK, Rules
from .runner import lint

Sev = Literal["info", "warn", "error", "fatal"]


def _cursor(cursor: str | None) -> int:
    try:
        n = int(cursor) if cursor else 0
    except ValueError:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}") from None
    if n < 0:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}")
    return n


def _save(rep, target: str, preset: str, only: list[str] | None) -> str:
    """Write every finding to ``work/out/lint/<slug>-<hash>.json`` (same input -> same file)."""
    key = json.dumps([target, preset, sorted(only or []), rep.rules_source], ensure_ascii=False)
    h = hashlib.blake2b(key.encode("utf-8"), digest_size=4).hexdigest()
    slug = "".join(ch if ch.isalnum() else "_" for ch in target.strip().replace("\\", "/").rsplit("/", 1)[-1])
    path = work("out", "lint", f"{(slug or 'target')[:40]}-{h}.json")
    doc = {"target": target, "preset": preset, "root": rep.root, "files": rep.files, "summary": rep.summary,
           "by_rule": rep.by_rule, "cols": ["rule", "sev", "file", "msg"],
           "rows": [f.row() for f in rep.findings]}
    atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return jpath(path)


@op("asset.lint", summary="Lint mod/game assets: DFF, TXD, COL, IDE and the IDE<->DFF/TXD/COL links, rules from "
    "data/lint_rules.json. Target: file, directory, IMG, <img>/<entry> or SID. Table rule/sev/file/msg.",
    summary_ru="Линтер ассетов: DFF, TXD, COL, IDE и связка IDE<->DFF/TXD/COL; правила — data/lint_rules.json.",
    mcp=False, long_running=True,
    examples=("satk asset lint models/gta3.img/infernus.dff", "satk asset lint model:17613",
              "satk asset lint . --preset strict --sev info", "satk asset lint models/gta3.img --rule col"))
def asset_lint(target: str, sev: Sev = "warn", rule: list[str] | None = None,
               preset: Literal["game", "strict"] = "game", config: str | None = None, index: bool = True,
               fail_on: Literal["never", "info", "warn", "error", "fatal"] = "never", save: bool = False,
               limit: int = 20, cursor: str | None = None, profile: str = "vanilla") -> dict:
    """Lint assets.

    Args:
        target: file (.dff .txd .col .ide), IMG archive, `<img>/<entry>`, directory (a game root is linted the
            way the game loads it) or SID (`model:`, `dff:`, `txd:`, `col:`, `file:`, `ide:`).
        sev: lowest severity shown in the table (the summary counts all): info|warn|error|fatal.
        rule: run only these rules: id prefixes (`col`, `dff.uv`, `link.texture_missing`) or globs (`*.parse`).
        preset: game = defaults calibrated on vanilla (0 fatal); strict = budgets for new content.
        config: JSON file with rule overrides {"rules": {"<id>": {"sev"|"enabled"|"params"|"msg": ...}}}.
        index: look up names outside the target in the profile index (a mod that reuses vanilla TXDs/COLs).
        fail_on: return CHECK_FAILED when a finding of this severity or higher exists (never = always ok).
        save: also write every finding to work/out/lint/<target>-<hash>.json.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
        profile: profile whose root resolves relative paths and whose index is used.
    """
    rep = lint(target, profile=profile, preset=preset, config=config, only=rule, use_index=index)
    shown = rep.at_least(sev)
    start = _cursor(cursor)
    lim = clamp_limit(limit)
    page = shown[start:start + lim]
    nxt = str(start + lim) if start + lim < len(shown) else None
    env = table(["rule", "sev", "file", "msg"], [f.row() for f in page], total=len(shown), next=nxt, warn=rep.warn)
    env["summary"] = rep.summary
    env["files"] = rep.files
    if rep.by_rule:
        env["by_rule"] = dict(list(rep.by_rule.items())[:20])
    if rep.root:
        env["root"] = rep.root
    if preset != "game":
        env["preset"] = preset
    if save:
        env["saved"] = _save(rep, target, preset, rule)
    if fail_on != "never":
        bad = rep.at_least(fail_on)
        if bad:
            raise SatkError("CHECK_FAILED", f"{len(bad)} lint finding(s) at {fail_on} or above in {target}",
                            hint=f"satk asset lint {target} --sev {fail_on}",
                            data={"summary": rep.summary, "cols": ["rule", "sev", "file", "msg"],
                                  "rows": [f.row() for f in bad[:lim]]})
    return env


@op("asset.lint_rules", summary="List the asset lint rules (id, severity, parameters, what, source) of a preset.",
    summary_ru="Правила линтера ассетов: id, серьёзность, параметры, что проверяет, источник.", mcp=False,
    examples=("satk asset lint-rules", "satk asset lint-rules --rule col --preset strict", "satk asset lint-rules --ru"))
def asset_lint_rules(rule: list[str] | None = None, preset: Literal["game", "strict"] = "game",
                     config: str | None = None, ru: bool = False, ref: bool = False, limit: int = 100,
                     cursor: str | None = None) -> dict:
    """List lint rules.

    Args:
        rule: only these rules: id prefixes (`col`, `dff.uv`) or globs (`*.parse`).
        preset: game|strict (severities and parameters after the preset).
        config: JSON override file, as for `asset lint`.
        ru: describe the rules in Russian.
        ref: add the source column (gta-reversed file:line, plugin-sdk, research report).
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    rules = Rules.load(preset=preset, config=config, only=rule)
    rows = []
    for r in sorted(rules.rules.values(), key=lambda r: r.id):
        if not r.enabled and rule:
            continue
        params = json.dumps(r.params, ensure_ascii=False, separators=(",", ":")) if r.params else ""
        row = [r.id, r.sev if r.enabled else "off", params, r.what_ru if ru else r.what]
        if ref:
            row.append(r.ref)
        rows.append(row)
    rows.sort(key=lambda x: (x[0].split(".")[0], -SEV_RANK.get(x[1], -1), x[0]))
    start = _cursor(cursor)
    lim = clamp_limit(limit)
    page = rows[start:start + lim]
    nxt = str(start + lim) if start + lim < len(rows) else None
    cols = ["rule", "sev", "params", "what"] + (["ref"] if ref else [])
    env = table(cols, page, total=len(rows), next=nxt)
    env["source"] = rules.source
    return env
