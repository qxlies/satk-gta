"""GitHub files of the public repository: the CI workflow, issue forms, PR template and community files.

The CI workflow must run what ``satk dev gate --quick`` runs that needs no game data or local tools: the same
``satk`` commands and the test suite with every "needs a tool" marker deselected, on Python 3.12 with exactly
``requirements.lock``. The community files must be English and free of machine paths, internal plan references
and e-mail addresses. PyYAML is not a dependency: a small strict parser below reads the YAML subset these files
use (block mappings and sequences, block scalars, quoted and plain scalars, flow sequences).
"""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from pathlib import Path

import pytest

from satk.core.config import REPO_ROOT
from satk.docs import gate as G

GITHUB = REPO_ROOT / ".github"
WORKFLOW = GITHUB / "workflows" / "ci.yml"
FORMS = GITHUB / "ISSUE_TEMPLATE"
COMMUNITY = ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md")
#: Gate steps that CI does not run, with the reason.
NOT_IN_CI = {"agent-docs-sync": "compares docs/agent with the copies published into a local workspace"}


# --------------------------------------------------------------------------- a strict YAML subset parser

_KEY = re.compile(r"""^(?P<key>"[^"]*"|'[^']*'|[^\s"'#\-\[\]{}][^:#]*?|-[^\s:#][^:#]*?)\s*:(?:\s+|$)""")
_BLOCK = ("|", "|-", "|+", ">", ">-", ">+")


def _is_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def _strip_comment(s: str) -> str:
    if s[:1] in ('"', "'"):
        q, i = s[0], 1
        while i < len(s):
            if q == '"' and s[i] == "\\":
                i += 2
                continue
            if s[i] == q:
                if q == "'" and s[i + 1:i + 2] == "'":
                    i += 2
                    continue
                break
            i += 1
        else:
            raise ValueError(f"unterminated quote: {s}")
        rest = s[i + 1:].strip()
        if rest and not rest.startswith("#"):
            raise ValueError(f"text after a quoted scalar: {s}")
        return s[:i + 1]
    m = re.search(r"\s#", s)
    return s[:m.start()].rstrip() if m else s


def _scalar(s: str):
    s = _strip_comment(s.strip())
    if s.startswith('"'):
        return json.loads(s)
    if s.startswith("'"):
        return s[1:-1].replace("''", "'")
    if s.startswith("["):
        if not s.endswith("]"):
            raise ValueError(f"unterminated flow sequence: {s}")
        inner = s[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    if s.startswith("{"):
        if s != "{}":
            raise ValueError(f"flow mappings are not supported: {s}")
        return {}
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if s in ("", "~", "null", "Null", "NULL"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if s[:1] in ("&", "*", "!", "%", "@", "`", "|", ">"):
        raise ValueError(f"unsupported YAML syntax: {s}")
    return s


class _Parser:
    def __init__(self, text: str):
        self.lines = text.replace("\r\n", "\n").split("\n")
        self.i = 0

    def peek(self) -> tuple[int, str] | None:
        while self.i < len(self.lines):
            s = self.lines[self.i]
            if s.strip() and not s.strip().startswith("#"):
                lead = s[:len(s) - len(s.lstrip())]
                if "\t" in lead:
                    raise ValueError(f"line {self.i + 1}: tab in indentation")
                return len(lead), s.strip()
            self.i += 1
        return None

    def document(self):
        p = self.peek()
        if p is None:
            return None
        if p[0] != 0:
            raise ValueError("the document must start at column 0")
        value = self.node(0)
        if self.peek() is not None:
            raise ValueError(f"line {self.i + 1}: unexpected content")
        return value

    def node(self, indent: int):
        return self.seq(indent) if _is_item(self.peek()[1]) else self.map(indent)

    def seq(self, indent: int) -> list:
        out = []
        while (p := self.peek()) is not None and p[0] >= indent:
            if p[0] > indent:
                raise ValueError(f"line {self.i + 1}: bad indentation")
            if not _is_item(p[1]):
                break  # a key at the same indent ends a "key:\n- item" sequence
            rest = p[1][1:].lstrip()
            if not rest:
                self.i += 1
                n = self.peek()
                out.append(self.node(n[0]) if n is not None and n[0] > indent else None)
            elif _KEY.match(rest) or _is_item(rest):
                col = indent + len(p[1]) - len(rest)
                self.lines[self.i] = " " * col + rest  # "- key: v" = a mapping that starts at the key's column
                out.append(self.node(col))
            else:
                self.i += 1
                out.append(_scalar(rest))
        return out

    def map(self, indent: int) -> dict:
        out: dict = {}
        while (p := self.peek()) is not None and p[0] >= indent:
            if p[0] > indent:
                raise ValueError(f"line {self.i + 1}: bad indentation")
            if _is_item(p[1]):
                break
            m = _KEY.match(p[1])
            if not m:
                raise ValueError(f"line {self.i + 1}: expected 'key: value': {p[1]}")
            key = _scalar(m.group("key"))
            if key in out:
                raise ValueError(f"line {self.i + 1}: duplicate key {key!r}")
            rest = _strip_comment(p[1][m.end():].strip())
            self.i += 1
            if rest in _BLOCK:
                out[key] = self.block_scalar(indent, rest)
            elif rest:
                out[key] = _scalar(rest)
            else:
                n = self.peek()
                if n is not None and (n[0] > indent or (n[0] == indent and _is_item(n[1]))):
                    out[key] = self.node(n[0])
                else:
                    out[key] = None
        return out

    def block_scalar(self, parent: int, style: str) -> str:
        lines: list[str] = []
        indent = None
        while self.i < len(self.lines):
            s = self.lines[self.i]
            if not s.strip():
                lines.append("")
                self.i += 1
                continue
            ind = len(s) - len(s.lstrip(" "))
            if indent is None:
                if ind <= parent:
                    break
                indent = ind
            if ind < indent:
                break
            lines.append(s[indent:])
            self.i += 1
        while lines and not lines[-1]:
            lines.pop()
        if style.startswith(">"):
            paras, cur = [], []
            for ln in lines:
                if ln:
                    cur.append(ln)
                else:
                    paras.append(" ".join(cur))
                    cur = []
            paras.append(" ".join(cur))
            text = "\n".join(paras)
        else:
            text = "\n".join(lines)
        return text if style.endswith("-") or not text else text + "\n"


def load_yaml(path: Path):
    """Parse ``path`` (the YAML subset above); ``ValueError`` on anything else."""
    return _Parser(path.read_text(encoding="utf-8")).document()


def test_yaml_subset_parser():
    text = (
        "# comment\n"
        "name: CI  # trailing comment\n"
        "on:\n"
        "  push:\n"
        "    branches: [main, 'rel']\n"
        "  pull_request:\n"
        "jobs:\n"
        "  t:\n"
        "    steps:\n"
        "      - uses: a/b@v1\n"
        "        with:\n"
        "          x: \"3.12\"\n"
        "      - run: |\n"
        "          one\n"
        "\n"
        "          # not a comment\n"
        "      - plain item\n"
        "    flag: true\n"
        "    n: 30\n"
        "list:\n"
        "- a\n"
        "- b: c\n"
        "  d: e\n"
        "folded: >-\n"
        "  a\n"
        "  b\n"
    )
    assert _Parser(text).document() == {
        "name": "CI",
        "on": {"push": {"branches": ["main", "rel"]}, "pull_request": None},
        "jobs": {"t": {"steps": [{"uses": "a/b@v1", "with": {"x": "3.12"}}, {"run": "one\n\n# not a comment\n"},
                                 "plain item"], "flag": True, "n": 30}},
        "list": ["a", {"b": "c", "d": "e"}],
        "folded": "a b",
    }
    for bad in ("a: 1\n  b: 2\n", "a: 1\na: 2\n", "a: &x 1\n", "a:\n\t- b\n", "a: 'x\n", "a: {b: 1}\n"):
        with pytest.raises(ValueError):
            _Parser(bad).document()


# --------------------------------------------------------------------------- the CI workflow


def _workflow() -> dict:
    return load_yaml(WORKFLOW)


def _steps(wf: dict) -> list[dict]:
    return [s for job in wf["jobs"].values() for s in job["steps"]]


def _commands(wf: dict) -> list[list[str]]:
    """Every command line of every ``run`` step, split like a shell would."""
    out = []
    for step in _steps(wf):
        for line in (step.get("run") or "").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(shlex.split(line, posix=True))
    return out


def _python_m(wf: dict, module: str) -> list[list[str]]:
    """Arguments after ``python -m <module>`` of every such command."""
    return [c[3:] for c in _commands(wf) if c[:3] == ["python", "-m", module]]


def _job_env(wf: dict) -> dict:
    env = dict(wf.get("env") or {})
    for job in wf["jobs"].values():
        env.update(job.get("env") or {})
    return env


def test_workflow_triggers_and_runner():
    wf = _workflow()
    assert wf["name"] == "CI"
    assert {"push", "pull_request"} <= set(wf["on"])
    assert wf["permissions"] == {"contents": "read"}
    jobs = list(wf["jobs"].values())
    assert jobs and all(j["runs-on"] == "windows-latest" for j in jobs)
    assert all(isinstance(j.get("timeout-minutes"), int) for j in jobs)
    env = _job_env(wf)
    assert env.get("PYTHONPATH") == "src" and str(env.get("PYTHONUTF8")) == "1"


def test_workflow_sets_up_python_312_with_the_lock():
    wf = _workflow()
    uses = {s["uses"].split("@")[0]: s for s in _steps(wf) if "uses" in s}
    assert set(uses) == {"actions/checkout", "actions/setup-python"}
    for s in uses.values():  # pinned to a major version tag or a full commit
        assert re.fullmatch(r"[\w./-]+@(v\d+|[0-9a-f]{40})", s["uses"]), s["uses"]
    sp = uses["actions/setup-python"]["with"]
    assert sp["python-version"] == "3.12" and sp["cache"] == "pip"
    assert sp["cache-dependency-path"] == "requirements.lock"
    pip = _python_m(wf, "pip")
    assert pip == [["install", "-r", "requirements.lock"]], "install exactly the pinned lock, nothing else"
    assert (REPO_ROOT / "requirements.lock").is_file()


def _tool_markers() -> set[str]:
    """Markers of tests that need something outside the repository ("reads the game", "needs Blender", ...)."""
    pp = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out = set()
    for line in pp["tool"]["pytest"]["ini_options"]["markers"]:
        name, _, text = line.partition(":")
        if text.strip().startswith(("needs", "reads")):
            out.add(name.strip())
    return out


def test_workflow_runs_the_gate_checks():
    """Every step of ``satk dev gate --quick`` runs in CI with the same arguments, except ``NOT_IN_CI``."""
    wf = _workflow()
    satk_cmds = _python_m(wf, "satk")
    pytest_cmds = _python_m(wf, "pytest")
    assert len(pytest_cmds) == 1
    args = pytest_cmds[0]
    assert "-q" in args and args[args.index("-p") + 1] == "no:cacheprovider"
    expr = args[args.index("-m") + 1]
    excluded = set()
    for term in expr.split(" and "):
        m = re.fullmatch(r"not (\w+)", term.strip())
        assert m, f"the marker filter is a plain 'not a and not b' list: {expr}"
        excluded.add(m.group(1))
    tools = _tool_markers()
    assert {"game", "viewer", "blender", "engine"} <= tools <= excluded
    assert not [a for a in args if a.startswith("tests") or a.startswith("-k")], "CI runs the whole suite"

    for step in G.plan(quick=True, private_work=Path("unused")):
        if step.name in NOT_IN_CI:
            continue
        argv = step.argv[1:]  # drop the interpreter
        if argv[:2] == ["-m", "pytest"]:
            # the gate deselects files that the CI marker filter excludes as a whole
            for d in G.DESELECT:
                text = (REPO_ROOT / d).read_text(encoding="utf-8")
                assert re.search(r"pytestmark\s*=\s*pytest\.mark\.(\w+)", text).group(1) in excluded, d
            continue
        assert argv[:4] == ["-X", "utf8", "-m", "satk"], step
        want = [a for a in argv[4:] if a != "--json"]
        assert want in satk_cmds, f"gate step {step.name!r} ({' '.join(want)}) is missing from CI"
    assert set(NOT_IN_CI) <= {s.name for s in G.plan(quick=True, private_work=Path("unused"))}


# --------------------------------------------------------------------------- issue forms and PR template

_FORM_TYPES = {"markdown", "textarea", "input", "dropdown", "checkboxes"}


def _forms() -> dict[str, dict]:
    return {p.name: load_yaml(p) for p in sorted(FORMS.glob("*.yml")) if p.name != "config.yml"}


def test_issue_forms_are_valid():
    forms = _forms()
    assert {f["name"] for f in forms.values()} == {"Bug report", "Feature request", "Question"}
    for name, form in forms.items():
        assert form["description"] and isinstance(form["body"], list), name
        ids = [item["id"] for item in form["body"] if item["type"] != "markdown"]
        assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[a-z][a-z0-9-]*", i) for i in ids), name
        labels = [item["attributes"]["label"] for item in form["body"] if item["type"] != "markdown"]
        assert len(labels) == len(set(labels)), name
        for item in form["body"]:
            assert item["type"] in _FORM_TYPES, (name, item)
            attrs = item["attributes"]
            if item["type"] == "markdown":
                assert attrs["value"].strip()
                continue
            assert attrs["label"], (name, item)
            if item["type"] == "dropdown":
                assert attrs["options"] and len(attrs["options"]) == len(set(attrs["options"]))
            if item["type"] == "checkboxes":
                assert all(o["label"] for o in attrs["options"])


def test_bug_form_asks_for_the_report_and_versions():
    form = _forms()["bug_report.yml"]
    text = (FORMS / "bug_report.yml").read_text(encoding="utf-8")
    for cmd in ("satk bug-report", "satk version", "satk game info"):
        assert cmd in text, cmd
    required = {item["id"] for item in form["body"]
                if item["type"] != "markdown" and (item.get("validations") or {}).get("required")}
    assert {"what", "steps", "bug-report", "satk-version", "os", "game-version"} <= required
    checks = [o for item in form["body"] if item["type"] == "checkboxes" for o in item["attributes"]["options"]]
    assert any("game files" in o["label"] and o.get("required") for o in checks)


def test_issue_config_routes_security_reports_privately():
    cfg = load_yaml(FORMS / "config.yml")
    assert cfg["blank_issues_enabled"] is False
    urls = [link["url"] for link in cfg["contact_links"]]
    assert any(u.endswith("/security/advisories/new") for u in urls)
    assert all(u.startswith("https://github.com/") for u in urls)


def test_pull_request_template_has_a_checklist():
    text = (GITHUB / "PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^- \[ \] ", text, re.M)) >= 6
    for word in ("CONTRIBUTING.md", "assetguard", "--no-verify", "gen-docs", "docs/en/"):
        assert word in text, word


def test_contributing_lists_the_ci_commands():
    """The "run what CI runs" block of CONTRIBUTING.md is exactly the checks of the workflow."""
    text = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    block = re.search(r"Run what CI runs.*?```powershell\n(.*?)```", text, re.S)
    assert block, "CONTRIBUTING.md: the block after 'Run what CI runs' is missing"
    documented = []
    for line in block.group(1).splitlines():
        if not line.strip():
            continue
        argv = shlex.split(line.strip().replace("\\", "/"), posix=True)  # Windows paths: no shell escapes
        if argv[:1] == [".venv/Scripts/python"]:
            argv = ["python", *argv[1:]]
        elif argv[:1] == ["./satk.cmd"]:
            argv = ["python", "-m", "satk", *argv[1:]]
        documented.append(argv)
    wf = _workflow()
    checks = [c for c in _commands(wf) if c[:3] in (["python", "-m", "pytest"], ["python", "-m", "satk"])]
    assert documented == checks


# --------------------------------------------------------------------------- community files

_MACHINE = re.compile(r"(?i)\b[a-z]:[\\/]+(?:games[\\/]+gta|files[\\/]|users[\\/]+user\b)")
_INTERNAL = re.compile(r"docs[\\/]design|\bSPEC\b|M2-PLAN|M3-(?:CANDIDATES|EXECUTION)|PAUSE-STATE|ROADMAP|"
                       r"\bWP-\d|\bM[23]-\d|\bM3[- ][A-C]\d|workspace-CLAUDE")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")


def _public_files() -> list[Path]:
    return [REPO_ROOT / n for n in COMMUNITY] + sorted(p for p in GITHUB.rglob("*") if p.is_file())


def test_community_files_exist_and_link_each_other():
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for target in ("CODE_OF_CONDUCT.md", "SECURITY.md", "NOTICE.md", "docs/en/install.md"):
        assert f"]({target})" in contributing and (REPO_ROOT / target).is_file(), target
    for word in ("requirements.lock", "PYTHONPATH", "satk dev gate", "assetguard", "--no-verify",
                 "game", "viewer", "blender", "engine", "README.ru.md", "docs/ru/"):
        assert word in contributing, word
    security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "Report a vulnerability" in security and "security advisory" in security
    coc = (REPO_ROOT / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8")
    assert "Contributor Covenant" in coc and "version 2.1" in coc and "[INSERT" not in coc


@pytest.mark.parametrize("path", _public_files(), ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_public_files_are_clean(path):
    text = path.read_text(encoding="utf-8")
    assert not text.startswith("\ufeff"), "no byte-order mark"
    assert not _CYRILLIC.search(text), "English only (the Russian mirror lives in docs/ru and README.ru.md)"
    assert not _MACHINE.search(text), _MACHINE.search(text).group(0)
    assert not _INTERNAL.search(text), _INTERNAL.search(text).group(0)
    assert not _EMAIL.search(text), _EMAIL.search(text).group(0)
