"""Assemble the portable zip, the wheel and the sdist; write ``SHA256SUMS.txt``; smoke-test the zip.

Zip layout (``satk-<ver>-win64/`` at the top; it mirrors a checkout, so code that finds its files
through ``REPO_ROOT`` works unchanged):

=================================  ===================================================================
``satk.cmd``, ``satk-mcp.cmd``     launchers: ``python\\python.exe -s -X utf8 -m satk`` / ``-m satk.mcp``
``Start satk.cmd`` (+ Russian      double-click: a console where ``satk`` works; runs ``satk init`` the
name)                              first time
``README-FIRST.txt`` (+ ``.ru``)   five steps and where to read more
``portable.txt``                   portable mode: the folder is the workspace (``work/``, ``satk.toml``)
``LICENSE``, ``NOTICE.md``,        licenses and history; ``RELEASE.json`` = what was built (commit,
``CHANGELOG.md``, ``RELEASE.json`` Python, wheels)
``python/``                        embeddable CPython; ``python312._pth`` adds ``..\\src`` and ``import site``
``python/Lib/site-packages/``      runtime wheels of the lock (no pip, no pytest)
``src/satk/``, ``data/``,          the runtime part of the repository at the ref
``vendor/``, ``proto/``,
``mta-resources/``, ``blender/``,
``docs/``
=================================  ===================================================================

Every entry carries the commit time, entries of each part are sorted: one ref and one lock give
the same zip bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..core.errors import SatkError
from ..core.paths import atomic_write, ensure_removable, ensure_writable, jpath
from .dist import build_sdist, build_wheel, shipped
from .fetch import fetch
from .tree import Tree
from .wheels import wheel_entries

__all__ = ["ZIP_TREES", "ZIP_FILES", "ZIP_EXCLUDE", "PORTABLE_DIR", "CHANGELOG", "START_RU", "SITE_PACKAGES",
           "DOCTOR_REQUIRED", "Step", "zip_name", "pth_text", "build_zip", "sha256sums", "smoke", "clean_env", "unpack"]

#: Repository trees copied into the zip.
ZIP_TREES = ("blender/", "data/", "docs/", "mta-resources/", "proto/", "src/", "vendor/")
#: Repository root files copied into the zip.
ZIP_FILES = ("LICENSE", "NOTICE.md", "README.md", "satk.toml.example")
#: Not shipped (about this development workspace, not about satk).
ZIP_EXCLUDE = frozenset({"docs/agent/workspace-CLAUDE.md"})
#: Files copied to the root of the zip as they are.
PORTABLE_DIR = "packaging/portable/"
CHANGELOG = "packaging/CHANGELOG.md"
#: The Russian name of the double-click launcher ("Zapustit satk.cmd"), a copy of ``Start satk.cmd``.
#: Spelled with escapes so this source stays ASCII.
START_RU = "\u0417\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c satk.cmd"
START_EN = "Start satk.cmd"
SITE_PACKAGES = "python/Lib/site-packages/"
#: ``satk doctor`` checks that must not fail in a fresh unpacked zip (the runtime itself). Others may:
#: before ``satk init`` there is no game, and the development components (MTA fork, viewer) are absent.
DOCTOR_REQUIRED = ("python", "utf8", "deps", "ops_import", "package_data", "portable", "media_pillow", "model3d")


def zip_name(version: str) -> str:
    """``satk-<ver>-win64`` (folder inside the zip and the zip's base name)."""
    return f"satk-{version}-win64"


def pth_text(original: str) -> str:
    """The embeddable ``._pth``: its own entries + ``..\\src`` + ``import site`` (site-packages, ``.pth`` files)."""
    keep = [ln.strip() for ln in original.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#") and ln.strip() != "import site"]
    return "\r\n".join([*keep, "..\\src", "import site"]) + "\r\n"


class _ZipOut:
    """Deterministic zip writer (fixed time, no attributes, duplicates refused)."""

    def __init__(self, path: Path, top: str, timestamp: int):
        self.top = top
        self.dt = max(time.gmtime(timestamp)[:6], (1980, 1, 1, 0, 0, 0))
        self.names: set[str] = set()
        self.bytes = 0
        self.zf = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6)

    def add(self, rel: str, data: bytes) -> None:
        key = rel.lower()
        if key in self.names:
            raise SatkError("CHECK_FAILED", f"two sources write {rel} into the zip")
        self.names.add(key)
        zi = zipfile.ZipInfo(f"{self.top}/{rel}", self.dt)
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.create_system = 0
        zi.external_attr = 0
        self.zf.writestr(zi, data, compresslevel=6)
        self.bytes += len(data)

    def close(self) -> None:
        self.zf.close()


def build_zip(tree: Tree, lock: dict, cache: Path, out_dir: Path, *, offline: bool, ref: str,
              log: Callable[[str], None] = lambda s: None) -> tuple[Path, dict]:
    """Write ``<out_dir>/satk-<ver>-win64.zip``; returns it and the ``RELEASE.json`` data."""
    from .tree import read_version

    version = read_version(tree)
    top = zip_name(version)
    py = lock["python"]
    embed = fetch(py["url"], cache / py["file"], sha256=py["sha256"], size=py.get("size"), offline=offline)
    wheel_paths = []
    for w in lock["wheels"]:
        log(f"wheel {w['file']}")
        wheel_paths.append(fetch(w["url"], cache / w["file"], sha256=w["sha256"], size=w.get("size"),
                                 offline=offline))
    target = ensure_writable(out_dir / f"{top}.zip")
    part = target.with_name(target.name + ".part")
    ensure_writable(part)
    z = _ZipOut(part, top, tree.timestamp)
    done = False
    try:
        # 1. root: launchers, readmes, licenses, changelog
        portable = tree.under(PORTABLE_DIR)
        if not any(p.endswith("/" + START_EN) for p in portable):
            raise SatkError("CHECK_FAILED", f"{PORTABLE_DIR}{START_EN} is missing")
        for p in portable:
            name = p[len(PORTABLE_DIR):]
            if "/" in name or not shipped(name):
                continue
            data = tree.files[p]
            if name.endswith(".txt"):  # Notepad of older Windows needs CRLF
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            z.add(name, data)
            if name == START_EN:
                z.add(START_RU, tree.files[p])
        z.add("CHANGELOG.md", tree.files[CHANGELOG] if CHANGELOG in tree.files else b"")
        for f in ZIP_FILES:
            if f in tree.files:
                z.add(f, tree.files[f])
        # 2. repository trees
        for t in ZIP_TREES:
            for p in tree.under(t):
                if p not in ZIP_EXCLUDE and shipped(p):
                    z.add(p, tree.files[p])
        # 3. embeddable CPython with our ._pth
        with zipfile.ZipFile(embed) as ez:
            names = sorted(i.filename for i in ez.infolist() if not i.is_dir())
            pths = [n for n in names if n.endswith("._pth")]
            if len(pths) != 1:
                raise SatkError("CHECK_FAILED", f"{embed.name}: expected one ._pth file, found {pths}")
            for n in names:
                data = ez.read(n)
                if n == pths[0]:
                    data = pth_text(data.decode("utf-8")).encode("ascii")
                z.add(f"python/{n}", data)
        # 4. wheels
        for wp in wheel_paths:
            for rel, data in wheel_entries(wp):
                z.add(SITE_PACKAGES + rel, data)
        manifest = {
            "name": "satk", "version": version, "commit": tree.commit, "ref": ref,
            "python": py["version"], "platform": lock.get("target", {}).get("platform"),
            "wheels": {w["name"]: w["version"] for w in lock["wheels"]},
            "files": len(z.names) + 1,
        }
        z.add("RELEASE.json", (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        manifest["unpacked_mb"] = round(z.bytes / 1e6, 1)
        done = True
    finally:
        z.close()
        if not done:
            part.unlink(missing_ok=True)
    os.replace(part, target)
    return target, manifest


def sha256sums(files: Iterable[Path], out: Path) -> dict[str, str]:
    """Write ``SHA256SUMS.txt`` (``<hex>  <name>``, sha256sum format) next to the files."""
    from .fetch import sha256_file

    sums = {f.name: sha256_file(f) for f in sorted(files, key=lambda p: p.name)}
    atomic_write(out, "".join(f"{h}  {n}\n" for n, h in sums.items()))
    return sums


def build_dists(tree: Tree, out_dir: Path) -> list[Path]:
    return [build_wheel(tree, out_dir), build_sdist(tree, out_dir)]


# --------------------------------------------------------------------------- smoke run


@dataclass
class Step:
    name: str
    ok: bool
    seconds: float
    summary: str

    def row(self) -> list:
        return [self.name, self.ok, round(self.seconds, 1), self.summary]


def clean_env(temp: Path) -> dict[str, str]:
    """The environment of a fresh user: no ``SATK_*``/``PYTHON*``/venv variables; TEMP under ``temp``."""
    drop = ("SATK_", "PYTHON", "VIRTUAL_ENV", "CONDA_", "PIP_")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(drop)}
    temp.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = env["TMP"] = str(temp)
    return env


def unpack(zip_path: Path, dest: Path) -> Path:
    """Fresh ``dest`` with the zip unpacked; returns the top folder inside it."""
    if dest.exists():
        ensure_removable(dest)
        shutil.rmtree(dest)
    ensure_writable(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        tops = {n.split("/", 1)[0] for n in zf.namelist()}
        if len(tops) != 1:
            raise SatkError("CHECK_FAILED", f"{zip_path.name}: expected one top folder, found {sorted(tops)}")
        zf.extractall(dest)
    return dest / tops.pop()


def _run(root: Path, args: list[str], env: dict[str, str], timeout: int) -> tuple[int, dict | None, str]:
    launcher = root / "satk.cmd"
    try:
        p = subprocess.run([str(launcher), *args, "--json"], cwd=root, env=env, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, None, f"timeout after {timeout} s"
    out = p.stdout.decode("utf-8", "replace").strip()
    try:
        return p.returncode, json.loads(out.splitlines()[-1]) if out else None, p.stderr.decode("utf-8", "replace")
    except json.JSONDecodeError:
        return p.returncode, None, (out + "\n" + p.stderr.decode("utf-8", "replace"))[-600:]


def smoke(zip_path: Path, dest: Path, *, version: str, game: Path | None,
          log: Callable[[str], None] = lambda s: None) -> list[Step]:
    """Unpack ``zip_path`` into ``dest`` and run the launchers like a new user would."""
    steps: list[Step] = []
    t0 = time.perf_counter()
    root = unpack(zip_path, dest)
    steps.append(Step("unpack", True, time.perf_counter() - t0, jpath(root)))
    env = clean_env(dest.parent / (dest.name + "-temp"))

    def step(name: str, args: list[str], check: Callable[[dict], str], timeout: int = 300) -> dict | None:
        log(f"smoke: satk {' '.join(args)}")
        t = time.perf_counter()
        code, env_out, err = _run(root, args, env, timeout)
        dt = time.perf_counter() - t
        if code != 0 or not env_out or not env_out.get("ok"):
            msg = (env_out or {}).get("error", {}).get("message") if env_out else None
            steps.append(Step(name, False, dt, f"exit {code}: {msg or err.strip()[-300:]}"))
            return None
        try:
            steps.append(Step(name, True, dt, check(env_out)))
        except (AssertionError, KeyError, TypeError) as e:
            steps.append(Step(name, False, dt, f"unexpected answer: {e or type(e).__name__}"))
            return None
        return env_out

    def v_check(d: dict) -> str:
        assert d["satk"] == version, f"version {d.get('satk')} != {version}"
        exe = Path(d["exe"])
        assert exe.parent == root / "python", f"python is {d['exe']}, not the bundled one"
        return f"satk {d['satk']}, Python {d['python']} (bundled)"

    def c_check(d: dict) -> str:
        ws = Path(d["paths"]["workspace"])
        assert os.path.normcase(str(ws)) == os.path.normcase(str(root)), f"workspace {jpath(ws)} != {jpath(root)}"
        return f"workspace = the unpacked folder; config: {', '.join(d.get('sources', []))}"

    def d_check(d: dict) -> str:
        status = {c["name"]: c["status"] for c in d["checks"]}
        missing = [n for n in DOCTOR_REQUIRED if n not in status]
        assert not missing, f"checks missing: {', '.join(missing)}"
        bad = [n for n in DOCTOR_REQUIRED if status[n] == "fail"]
        assert not bad, f"failed checks: {', '.join(bad)}"
        other = sorted(n for n, s in status.items() if s == "fail")
        return (f"{', '.join(DOCTOR_REQUIRED)} not failing; {d['counts'].get('ok', 0)} ok, "
                f"{d['counts'].get('warn', 0)} warn, {len(other)} optional fail{(' (' + ', '.join(other) + ')') if other else ''}")

    def m_check(d: dict) -> str:
        return f"tools={d.get('tools')}, list_bytes={d.get('list_bytes')}"

    step("version", ["version"], v_check, 120)
    step("config", ["config", "show"], c_check, 120)
    step("doctor", ["doctor"], d_check, 300)
    step("mcp-selftest", ["mcp", "selftest"], m_check, 300)
    if game is not None:
        first = time.perf_counter()
        init = step("init", ["init", "--game", str(game), "--yes"],
                    lambda d: f"profile {d.get('profile')}, config {d.get('config')}", 300)
        prof = (init or {}).get("profile") or "game"
        built = step("index-build", ["index", "build", "--profile", prof],
                     lambda d: ", ".join(f"{k}={v}" for k, v in sorted((d.get("counts") or {}).items())[:6])
                     or "built", 1800)
        if built is not None:
            def f_check(d: dict) -> str:
                rows = d.get("rows") or []
                assert rows, "no rows"
                return f"{len(rows)} rows, first {rows[0][0]}; first result {time.perf_counter() - first:.0f} s " \
                       "after init"
            step("first-result", ["asset", "find", "grove", "--limit", "3", "--profile", prof], f_check, 120)
    return steps
