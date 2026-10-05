"""Lint rules as data: ``data/lint_rules.json`` (+ a preset, + an optional user override file).

A rule = id, severity, message template, parameters (thresholds), a one-line description in English and
Russian and the source it is based on. The checks (other modules of :mod:`satk.lint`) only say *which*
rule fired and with which fields; :class:`Collector` turns that into a :class:`Finding` using the rule's
severity and template, or drops it when the rule is disabled or filtered out.

Example::

    rules = Rules.load(preset="strict")
    col = Collector(rules)
    col.add("txd.pow2", "models/x.txd", tex="wall", w=100, h=64)
    print(col.findings[0].row())   # ['txd.pow2', 'error', 'models/x.txd', "texture 'wall': 100x64 is not ..."]
"""

from __future__ import annotations

import copy
import difflib
import fnmatch
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import SatkError

__all__ = ["SEVERITIES", "SEV_RANK", "PRESETS", "Rule", "Rules", "Finding", "Collector", "rules_path"]

SEVERITIES: tuple[str, ...] = ("info", "warn", "error", "fatal")
SEV_RANK: dict[str, int] = {s: i for i, s in enumerate(SEVERITIES)}
PRESETS: tuple[str, ...] = ("game", "strict")
_RULE_KEYS = frozenset({"sev", "msg", "what", "what_ru", "ref", "params", "enabled"})
_OVERRIDE_KEYS = frozenset({"sev", "params", "enabled", "msg"})

_cache: dict[str, dict] = {}


def rules_path() -> Path:
    """``data/lint_rules.json`` of this checkout (read like ``data/exe_versions.json``, ``core.detect``)."""
    from ..core.config import DATA_ROOT

    return DATA_ROOT / "lint_rules.json"


def _read_json(path: Path, what: str) -> dict:
    """Parsed JSON file (cached per path, size and mtime; a fresh copy each call)."""
    try:
        st = path.stat()
        key = f"{path}|{st.st_size}|{st.st_mtime_ns}"
        if key not in _cache:
            _cache[key] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"{what} not found: {path.as_posix()}") from None
    except (OSError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"cannot read {what} {path.as_posix()}: {e}") from None
    if not isinstance(_cache[key], dict):
        raise SatkError("BAD_PARAMS", f"{what} {path.as_posix()}: expected a JSON object")
    return copy.deepcopy(_cache[key])


@dataclass(frozen=True)
class Rule:
    """One rule after preset/override merging."""

    id: str
    sev: str
    msg: str
    what: str
    what_ru: str
    ref: str
    params: dict = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class Finding:
    """One lint result. ``file`` is the item label (path relative to the lint root, ``<img>/<entry>``)."""

    rule: str
    sev: str
    file: str
    msg: str

    def row(self) -> list:
        return [self.rule, self.sev, self.file, self.msg]

    def sort_key(self) -> tuple:
        return (-SEV_RANK[self.sev], self.file.lower(), self.rule, self.msg)


def _merge_params(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def _bad_rule(rid: str, known: Iterable[str], where: str) -> SatkError:
    return SatkError("BAD_PARAMS", f"{where}: unknown lint rule {rid!r}",
                     did_you_mean=difflib.get_close_matches(rid, list(known), n=3, cutoff=0.6),
                     hint="satk asset lint-rules")


def _apply(rules: dict[str, dict], overrides: dict, where: str) -> None:
    if not isinstance(overrides, dict):
        raise SatkError("BAD_PARAMS", f"{where}: expected an object of rule overrides")
    for rid, ov in overrides.items():
        if rid.startswith("_"):
            continue
        if rid not in rules:
            raise _bad_rule(rid, rules, where)
        if not isinstance(ov, dict) or set(ov) - _OVERRIDE_KEYS:
            raise SatkError("BAD_PARAMS", f"{where}: rule {rid!r}: allowed keys are {sorted(_OVERRIDE_KEYS)}")
        r = rules[rid]
        if "sev" in ov:
            if ov["sev"] not in SEV_RANK:
                raise SatkError("BAD_PARAMS", f"{where}: rule {rid!r}: sev must be one of {list(SEVERITIES)}")
            r["sev"] = ov["sev"]
        if "enabled" in ov:
            r["enabled"] = bool(ov["enabled"])
        if "msg" in ov:
            r["msg"] = str(ov["msg"])
        if "params" in ov:
            if not isinstance(ov["params"], dict):
                raise SatkError("BAD_PARAMS", f"{where}: rule {rid!r}: params must be an object")
            r["params"] = _merge_params(r.get("params") or {}, ov["params"])


def _match(rid: str, pats: list[str]) -> bool:
    for p in pats:
        p = p.strip().lower()
        if not p:
            continue
        if any(ch in p for ch in "*?["):
            if fnmatch.fnmatchcase(rid, p):
                return True
        elif rid.startswith(p):                       # "col", "col.box", "link.texture_missing"
            return True
    return False


class Rules:
    """The effective rule set (rules file + preset + optional override file + rule filter)."""

    def __init__(self, rules: dict[str, Rule], *, preset: str, classes: dict, source: str):
        self.rules = rules
        self.preset = preset
        self.classes = classes
        self.source = source

    @classmethod
    def load(cls, preset: str = "game", config: str | Path | None = None, only: list[str] | None = None,
             path: Path | None = None) -> "Rules":
        """Build the effective rules.

        Args:
            preset: a key of ``presets`` in the rules file (``game`` = calibrated on vanilla, ``strict``).
            config: JSON file ``{"rules": {id: {"sev"|"enabled"|"params"|"msg": ...}}}`` applied last.
            only: run only these rules: id prefixes (``col``, ``dff.uv``) or globs (``*.parse``).
            path: rules file (default :func:`rules_path`).
        """
        p = path or rules_path()
        data = _read_json(p, "lint rules")
        raw = data.get("rules")
        if not isinstance(raw, dict) or not raw:
            raise SatkError("INTERNAL", f"{p.as_posix()}: no 'rules' object")
        rules: dict[str, dict] = {}
        for rid, r in raw.items():
            if set(r) - _RULE_KEYS or r.get("sev") not in SEV_RANK or not r.get("msg"):
                raise SatkError("INTERNAL", f"{p.as_posix()}: rule {rid!r} is malformed")
            rules[rid] = {"params": {}, "enabled": True, "what": "", "what_ru": "", "ref": "", **r}
        presets = data.get("presets") or {}
        if preset not in presets:
            raise SatkError("BAD_PARAMS", f"unknown lint preset {preset!r}",
                            did_you_mean=difflib.get_close_matches(preset, list(presets), n=3, cutoff=0.5))
        _apply(rules, presets[preset], f"preset {preset}")
        source = p.as_posix()
        if config:
            cp = Path(config)
            extra = _read_json(cp, "lint config")
            if set(extra) - {"rules", "_comment", "format"}:
                raise SatkError("BAD_PARAMS", f"{cp.as_posix()}: expected {{\"rules\": {{...}}}}")
            _apply(rules, extra.get("rules") or {}, cp.as_posix())
            source += f" + {cp.as_posix()}"
        if only:
            pats = [x for s in only for x in str(s).split(",")]
            hit = [rid for rid in rules if _match(rid, pats)]
            if not hit:
                raise SatkError("BAD_PARAMS", f"no lint rule matches {only!r}", hint="satk asset lint-rules")
            for rid in rules:
                if rid not in hit:
                    rules[rid]["enabled"] = False
        out = {rid: Rule(rid, r["sev"], r["msg"], r["what"], r["what_ru"], r["ref"], r["params"], r["enabled"])
               for rid, r in rules.items()}
        classes = {k: v for k, v in (data.get("classes") or {}).items() if not k.startswith("_")}
        return cls(out, preset=preset, classes=classes, source=source)

    def __getitem__(self, rid: str) -> Rule:
        return self.rules[rid]

    def on(self, rid: str) -> bool:
        """True if the rule exists and runs (checks may skip expensive work for disabled rules)."""
        r = self.rules.get(rid)
        return r is not None and r.enabled

    def param(self, rid: str, key: str, default: Any = None) -> Any:
        r = self.rules.get(rid)
        return default if r is None else r.params.get(key, default)


class _Fields(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fmt_value(v: Any) -> Any:
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 1e9 else f"{v:.3g}"
    if isinstance(v, (list, tuple, set, frozenset)):
        return ", ".join(str(_fmt_value(x)) for x in v)
    return v


class Collector:
    """Collects findings for one lint run."""

    def __init__(self, rules: Rules):
        self.rules = rules
        self.findings: list[Finding] = []
        self.fired: Counter = Counter()

    def on(self, rid: str) -> bool:
        return self.rules.on(rid)

    def add(self, rid: str, file: str, **fields: Any) -> None:
        """Record rule ``rid`` for ``file``; ``fields`` fill the message template (with the rule params)."""
        r = self.rules.rules.get(rid)
        if r is None:
            raise KeyError(f"lint rule {rid!r} is not in the rules file")
        if not r.enabled:
            return
        vals = _Fields({k: _fmt_value(v) for k, v in r.params.items() if not isinstance(v, dict)})
        vals.update({k: _fmt_value(v) for k, v in fields.items()})
        try:
            msg = r.msg.format_map(vals)
        except (ValueError, IndexError, AttributeError, KeyError):  # a broken template in a user override
            msg = r.msg + " " + ", ".join(f"{k}={v}" for k, v in sorted(fields.items()))
        self.findings.append(Finding(rid, r.sev, file, msg))
        self.fired[rid] += 1
