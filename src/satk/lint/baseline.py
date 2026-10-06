"""Reference-aware linting (``satk.lint.baseline``): ``asset lint --baseline vanilla`` and ``--like <SID>``.

* :func:`rule_rates` lints a profile's game root the way the game loads it (with the same preset and
  config) and records, per rule, how many subjects it examined and how many files it fired on. The answer
  is cached in ``<work>/cache/lint/rates-<key>.json``; the key covers the rules file, the config file, the
  preset, the profile's index content hash and :data:`RATES_VERSION`, so a repeat costs nothing (the first
  run on the vanilla game takes about 15-20 s).
* :func:`apply_baseline` drops the findings of rules that fire on at least ``max_rate`` of the reference
  subjects (what vanilla itself does is no defect) and returns the rate of every rule.
* :func:`like_rules` lints a reference model (``model:426``: its DFF, TXD chain and collision) and returns
  the rules it triggers; ``asset lint --like`` drops those from the answer.

The rate of a rule is ``files it fired on / subjects it examined`` (``Collector.seen``: DFFs of the class,
vehicles, textures, collision models ...); rules that do not count subjects use the number of linted files
of their kind.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, jpath, profile_root, work
from .rules import Finding, rules_path

__all__ = ["RATES_VERSION", "rule_rates", "rate_of", "apply_baseline", "like_rules"]

#: Bump when a check changes what it counts (invalidates the cached rates).
RATES_VERSION = 2
_KIND = {"dff": "dff", "veh": "dff", "ped": "dff", "weap": "dff", "mat": "dff", "link": "dff", "txd": "txd",
         "col": "col", "ide": "ide", "img": "dff", "file": "dff"}


def _sha(path: str | Path | None) -> str:
    if not path:
        return "-"
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def _index_hash(profile: str) -> str:
    try:
        from ..index.api import open_index

        rows = open_index(profile).query("SELECT value FROM meta WHERE key = 'content_hash'", limit=1).get("rows")
        return str(rows[0][0])[:16] if rows else "-"
    except SatkError:
        return "-"


def rule_rates(profile: str = "vanilla", preset: str = "game", config: str | None = None) -> dict:
    """``{"rules": {rule: {"fired": files, "checked": subjects, "rate": r}}, "files": n, ...}`` for the
    game root of ``profile`` (cached, see the module docstring)."""
    root = profile_root(profile)
    key_src = json.dumps([RATES_VERSION, profile, preset, _sha(rules_path()), _sha(config), _index_hash(profile),
                          jpath(root)])
    key = hashlib.blake2b(key_src.encode("utf-8"), digest_size=6).hexdigest()
    path = work("cache", "lint", f"rates-{profile}-{preset}-{key}.json")
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    from .runner import lint

    rep = lint(str(root), profile=profile, preset=preset, config=config, use_index=False)
    rules: dict[str, dict] = {}
    for rid in sorted(set(rep.fired_files) | set(rep.checked)):
        fired = rep.fired_files.get(rid, 0)
        checked = rep.checked.get(rid) or rep.kinds.get(_KIND.get(rid.split(".", 1)[0], "dff"), 0)
        rules[rid] = {"fired": fired, "checked": checked, "rate": round(fired / checked, 4) if checked else 0.0}
    doc = {"profile": profile, "preset": preset, "root": jpath(root), "files": rep.files, "kinds": rep.kinds,
           "rules": rules}
    atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return doc


def rate_of(rates: dict, rule: str) -> float:
    """Rate of ``rule`` in a :func:`rule_rates` answer (0 when the reference never fired it)."""
    r = (rates.get("rules") or {}).get(rule)
    return float(r["rate"]) if r else 0.0


def apply_baseline(findings: list[Finding], rates: dict, max_rate: float) -> tuple[list[Finding], dict[str, int]]:
    """Drop findings of rules whose reference rate is at least ``max_rate``; ``(kept, {rule: dropped})``."""
    kept: list[Finding] = []
    dropped: dict[str, int] = {}
    for f in findings:
        if rate_of(rates, f.rule) >= max_rate:
            dropped[f.rule] = dropped.get(f.rule, 0) + 1
        else:
            kept.append(f)
    return kept, dropped


def like_rules(sid: str, *, profile: str, preset: str, config: str | None) -> set[str]:
    """Rules the reference model ``sid`` (``model:426``, ``dff:premier`` ...) triggers itself."""
    from .runner import lint

    rep = lint(sid, profile=profile, preset=preset, config=config)
    return {f.rule for f in rep.findings}
