"""Dependency helper for ``scripts/bootstrap.ps1`` (stdlib only, run with the venv python).

    python scripts/deps.py pins          # print the pinned optional deps from pyproject.toml
    python scripts/deps.py lock [FILE]   # write requirements.lock from ``pip freeze``
    python scripts/deps.py check [FILE]  # exit 1 if installed versions differ from the lock

The lock has no timestamp, so re-running it without changes gives an identical file.
"""

from __future__ import annotations

import platform
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXTRAS = ("img", "num", "mcp", "dev")


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(".", "-")


def pins() -> list[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    opt = data["project"].get("optional-dependencies", {})
    return [p for e in EXTRAS for p in opt.get(e, [])]


def freeze() -> dict[str, str]:
    r = subprocess.run([sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, ver = line.split("==", 1)
        out[name.strip()] = ver.strip()
    return out


def pip_version() -> str:
    try:
        import pip  # noqa: PLC0415

        return pip.__version__
    except ImportError:  # pragma: no cover
        return "?"


def render_lock(installed: dict[str, str]) -> str:
    want = pins()
    have = {_norm(k): v for k, v in installed.items()}
    notes = []
    for p in want:
        name, _, ver = p.partition("==")
        got = have.get(_norm(name))
        if got is None:
            notes.append(f"# MISSING  {p} (not installed)")
        elif ver and got != ver:
            notes.append(f"# CHANGED  {p} -> {name}=={got} (pinned version unavailable; closest installed)")
    lines = [
        "# satk requirements.lock - exact versions in <checkout>\\.venv. ASCII only.",
        "# Written by scripts/bootstrap.ps1 -Deps (scripts/deps.py lock). Reinstall exactly:",
        "#   bootstrap.ps1 -Deps -FromLock   (or: .venv\\Scripts\\python -m pip install -r requirements.lock)",
        f"# Python {platform.python_version()} ({platform.python_implementation()}, {sys.platform}), pip {pip_version()}",
        "# Top-level pins (pyproject.toml extras img,num,mcp,dev): " + " ".join(want),
    ]
    lines += notes or ["# All pinned versions were available on PyPI and are installed as pinned."]
    lines.append("")
    lines += [f"{k}=={v}" for k, v in sorted(installed.items(), key=lambda kv: _norm(kv[0]))]
    return "\n".join(lines) + "\n"


def parse_lock(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            n, v = line.split("==", 1)
            out[_norm(n)] = v.strip()
    return out


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "pins"
    target = Path(argv[1]) if len(argv) > 1 else REPO / "requirements.lock"
    if cmd == "pins":
        print(" ".join(pins()))
        return 0
    if cmd == "lock":
        text = render_lock(freeze())
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        tmp.replace(target)
        print(f"wrote {target}")
        return 0
    if cmd == "check":
        lock = parse_lock(target)
        have = {_norm(k): v for k, v in freeze().items()}
        diff = sorted(k for k in set(lock) | set(have) if lock.get(k) != have.get(k))
        for k in diff:
            print(f"{k}: lock={lock.get(k)} installed={have.get(k)}")
        return 1 if diff else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
