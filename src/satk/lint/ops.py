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
from .rules import SEV_RANK, SEVERITIES, Rules
from .runner import lint

Sev = Literal["info", "warn", "error", "fatal"]
Preset = Literal["game", "strict", "vanilla", "sa_plus"]


def _cursor(cursor: str | None) -> int:
    try:
        n = int(cursor) if cursor else 0
    except ValueError:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}") from None
    if n < 0:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}")
    return n


def _save(rep, findings, summary: dict, target: str, preset: str, only: list[str] | None, ref: str = "") -> str:
    """Write every finding to ``work/out/lint/<slug>-<hash>.json`` (same input -> same file)."""
    key = json.dumps([target, preset, sorted(only or []), rep.rules_source, ref], ensure_ascii=False)
    h = hashlib.blake2b(key.encode("utf-8"), digest_size=4).hexdigest()
    slug = "".join(ch if ch.isalnum() else "_" for ch in target.strip().replace("\\", "/").rsplit("/", 1)[-1])
    path = work("out", "lint", f"{(slug or 'target')[:40]}-{h}.json")
    doc = {"target": target, "preset": preset, "root": rep.root, "files": rep.files, "summary": summary,
           "by_rule": rep.by_rule, "cols": ["rule", "sev", "file", "msg"],
           "rows": [f.row() for f in findings]}
    atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return jpath(path)


def _hints(rules: set[str], preset: str, config: str | None) -> dict[str, str]:
    """``{rule: how to fix}`` for the rules of a page that carry a hint."""
    if not rules:
        return {}
    rs = Rules.load(preset=preset, config=config)
    return {r: rs[r].hint for r in sorted(rules) if r in rs.rules and rs[r].hint}


@op("asset.lint", summary="Lint mod/game assets: DFF, TXD, COL, IDE and their links, plus vehicle/ped/weapon "
    "semantics (lamps, paint, frames, shading); presets game|strict|vanilla|sa_plus; --baseline vanilla or --like "
    "SID drops what vanilla does too. Target: file, folder, IMG, SID.",
    summary_ru="Линтер ассетов: DFF, TXD, COL, IDE, связи и семантика машин/педов/оружия; пресеты vanilla/sa_plus; "
               "--baseline vanilla или --like SID убирают то, что делает и ваниль.",
    mcp=False, long_running=True,
    examples=("satk asset lint models/gta3.img/infernus.dff", "satk asset lint model:17613",
              "satk asset lint mymod --preset sa_plus --baseline vanilla",
              "satk asset lint mymod/premier.dff --like model:426",
              "satk asset lint . --preset strict --sev info", "satk asset lint models/gta3.img --rule col"))
def asset_lint(target: str, sev: Sev = "warn", rule: list[str] | None = None, preset: Preset = "game",
               config: str | None = None, index: bool = True,
               fail_on: Literal["never", "info", "warn", "error", "fatal"] = "never", save: bool = False,
               limit: int = 20, cursor: str | None = None, profile: str = "vanilla",
               baseline: Literal["vanilla", "installed", "samp"] | None = None, like: str | None = None,
               baseline_rate: float = 0.05) -> dict:
    """Lint assets.

    Args:
        target: file (.dff .txd .col .ide), IMG archive, `<img>/<entry>`, directory (a game root is linted the
            way the game loads it) or SID (`model:`, `dff:`, `txd:`, `col:`, `file:`, `ide:`).
        sev: lowest severity shown in the table (the summary counts all): info|warn|error|fatal.
        rule: run only these rules: id prefixes (`col`, `dff.uv`, `link.texture_missing`) or globs (`*.parse`).
        preset: game = defaults calibrated on vanilla (0 fatal); strict = budgets for new content; vanilla and
            sa_plus = the detail tiers of satk.style (vanilla budgets and conventions; sa_plus = higher budgets,
            the default tier for new assets).
        config: JSON file with rule overrides {"rules": {"<id>": {"sev"|"enabled"|"params"|"msg"|"class_sev": ...}}}.
        index: look up names outside the target in the profile index (a mod that reuses vanilla TXDs/COLs).
        fail_on: return CHECK_FAILED when a finding of this severity or higher exists (never = always ok).
        save: also write every finding to work/out/lint/<target>-<hash>.json.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
        profile: profile whose root resolves relative paths and whose index is used.
        baseline: profile whose whole game is the reference: findings of rules that fire on at least
            --baseline-rate of its subjects are dropped, and rows get a <profile>_rate column (cached in
            work/cache/lint; the first run takes about 20 s).
        like: reference model SID (model:426): rules it triggers itself are dropped from the answer.
        baseline_rate: reference rate (0..1) from which a rule counts as normal for the game (default 0.05).
    """
    if not 0 <= baseline_rate <= 1:
        raise SatkError("BAD_PARAMS", f"--baseline-rate must be 0..1, got {baseline_rate}")
    rep = lint(target, profile=profile, preset=preset, config=config, only=rule, use_index=index)
    findings = rep.findings
    suppressed: dict[str, int] = {}
    if like:
        from .baseline import like_rules

        ref = like_rules(like, profile=profile, preset=preset, config=config)
        kept = []
        for f in findings:
            if f.rule in ref:
                suppressed[f.rule] = suppressed.get(f.rule, 0) + 1
            else:
                kept.append(f)
        findings = kept
    rates = None
    if baseline:
        from .baseline import apply_baseline, rule_rates

        rates = rule_rates(baseline, preset, config)
        findings, dropped = apply_baseline(findings, rates, baseline_rate)
        for k, v in dropped.items():
            suppressed[k] = suppressed.get(k, 0) + v
    summary = {s: 0 for s in reversed(SEVERITIES)}
    for f in findings:
        summary[f.sev] += 1
    shown = [f for f in findings if SEV_RANK[f.sev] >= SEV_RANK[sev]]
    start = _cursor(cursor)
    lim = clamp_limit(limit)
    page = shown[start:start + lim]
    nxt = str(start + lim) if start + lim < len(shown) else None
    cols = ["rule", "sev", "file", "msg"]
    rows = [f.row() for f in page]
    if rates is not None:
        from .baseline import rate_of

        cols.append(f"{baseline}_rate")
        rows = [r + [rate_of(rates, r[0])] for r in rows]
    env = table(cols, rows, total=len(shown), next=nxt, warn=rep.warn)
    env["summary"] = summary
    env["files"] = rep.files
    by_rule = {k: v - suppressed.get(k, 0) for k, v in rep.by_rule.items() if v > suppressed.get(k, 0)}
    if by_rule:
        env["by_rule"] = dict(list(by_rule.items())[:20])
    if suppressed:
        env["suppressed"] = dict(sorted(suppressed.items(), key=lambda kv: (-kv[1], kv[0]))[:20])
    hints = _hints({f.rule for f in page}, preset, config)
    if hints:
        env["hints"] = hints
    if rep.root:
        env["root"] = rep.root
    if preset != "game":
        env["preset"] = preset
    if like:
        env["like"] = like
    if save:
        env["saved"] = _save(rep, findings, summary, target, preset, rule,
                             f"{baseline}|{like}|{baseline_rate}" if (baseline or like) else "")
    if fail_on != "never":
        bad = [f for f in findings if SEV_RANK[f.sev] >= SEV_RANK[fail_on]]
        if bad:
            raise SatkError("CHECK_FAILED", f"{len(bad)} lint finding(s) at {fail_on} or above in {target}",
                            hint=f"satk asset lint {target} --sev {fail_on}",
                            data={"summary": summary, "cols": ["rule", "sev", "file", "msg"],
                                  "rows": [f.row() for f in bad[:lim]]})
    return env


@op("asset.lint_rules", summary="List the asset lint rules (id, severity, parameters, what, source, fix, the crash "
    "or defect it prevents) of a preset.",
    summary_ru="Правила линтера ассетов: id, серьёзность, параметры, что проверяет, источник, исправление.", mcp=False,
    examples=("satk asset lint-rules", "satk asset lint-rules --rule veh --preset sa_plus --ref",
              "satk asset lint-rules --ru"))
def asset_lint_rules(rule: list[str] | None = None, preset: Preset = "game",
                     config: str | None = None, ru: bool = False, ref: bool = False, limit: int = 100,
                     cursor: str | None = None) -> dict:
    """List lint rules.

    Args:
        rule: only these rules: id prefixes (`col`, `dff.uv`) or globs (`*.parse`).
        preset: game|strict|vanilla|sa_plus (severities and parameters after the preset).
        config: JSON override file, as for `asset lint`.
        ru: describe the rules in Russian.
        ref: add the source, hint (how to fix) and prevents (crash or defect) columns.
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
            row += [r.ref, r.hint, r.prevents]
        rows.append(row)
    rows.sort(key=lambda x: (x[0].split(".")[0], -SEV_RANK.get(x[1], -1), x[0]))
    start = _cursor(cursor)
    lim = clamp_limit(limit)
    page = rows[start:start + lim]
    nxt = str(start + lim) if start + lim < len(rows) else None
    cols = ["rule", "sev", "params", "what"] + (["ref", "hint", "prevents"] if ref else [])
    env = table(cols, page, total=len(rows), next=nxt)
    env["source"] = rules.source
    return env
