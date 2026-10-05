"""``packaging/release-lock.json``: the pinned inputs of the zip besides the git tree.

* ``python`` - the official embeddable CPython (``python-<ver>-embed-amd64.zip`` from python.org):
  size and SHA-256, plus how it was verified (python.org's published MD5 and size, Authenticode of
  every PE file, the OpenPGP signature of the Windows release key - see ``packaging/README.md``).
  3.12 is in security-fix mode: 3.12.10 is its last release with Windows binaries.
* ``wheels`` - the runtime dependency closure of the ``pyproject.toml`` extras except ``dev``
  (Pillow, numpy, mcp and what they need on Windows), one ``cp312``/``win_amd64``-compatible wheel
  each, with the SHA-256 that PyPI publishes. Versions are those of ``requirements.lock``.

:func:`relock` rebuilds the file (network: python.org and PyPI). It runs in the development venv:
the closure comes from the installed distributions' metadata, and :mod:`packaging` (a pytest
dependency there) evaluates markers and wheel tags. Builds only read the file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

from ..core.errors import SatkError
from .fetch import fetch, get_json, sha256_file

__all__ = ["LOCK_PATH", "LOCK_VERSION", "PYTHON_VERSION", "PLATFORM", "PYTHON_PGP", "load", "parse",
           "runtime_roots", "closure", "pick_wheel", "relock", "authenticode", "render"]

#: Lock file, relative to the repository root.
LOCK_PATH = "packaging/release-lock.json"
LOCK_VERSION = 1
#: Embeddable CPython of the release (the last 3.12 with Windows binaries).
PYTHON_VERSION = "3.12.10"
PLATFORM = "win_amd64"
#: OpenPGP key that signs python.org's Windows binaries (Steve Dower), as listed on
#: https://www.python.org/downloads/metadata/pgp/ .
PYTHON_PGP = "7ED10B6531D7C8E1BC296021FC624643487034E5"
PYTHON_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"
PYTHON_API = "https://www.python.org/api/v2/downloads/release/?name=Python%20{v}"
PYTHON_FILES = "https://www.python.org/api/v2/downloads/release_file/?release={id}"
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"
#: Signers accepted for PE files of the embeddable zip.
SIGNERS = ("CN=Python Software Foundation", "CN=Microsoft Windows Software Compatibility Publisher")


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(text: str) -> dict[str, Any]:
    """Validated lock data (``CHECK_FAILED`` on a malformed file)."""
    try:
        d = json.loads(text)
        ok = (d.get("lock_version") == LOCK_VERSION and isinstance(d.get("python"), dict)
              and isinstance(d.get("wheels"), list)
              and all(isinstance(w.get(k), str) for w in d["wheels"] for k in ("name", "version", "file", "url",
                                                                              "sha256"))
              and all(isinstance(d["python"].get(k), str) for k in ("version", "file", "url", "sha256")))
    except (json.JSONDecodeError, AttributeError) as e:
        raise SatkError("CHECK_FAILED", f"{LOCK_PATH}: not valid JSON: {e}", hint="satk dev release --relock") from None
    if not ok:
        raise SatkError("CHECK_FAILED", f"{LOCK_PATH}: unexpected structure (lock_version {LOCK_VERSION})",
                        hint="satk dev release --relock")
    return d


def load(path: str | os.PathLike) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"no release lock: {p}", hint="satk dev release --relock (needs the network)")
    return parse(p.read_text(encoding="utf-8"))


def render(lock: dict[str, Any]) -> str:
    """Stable text of the lock (sorted keys, one wheel per line block, trailing newline)."""
    return json.dumps(lock, indent=1, sort_keys=True, ensure_ascii=True) + "\n"


# --------------------------------------------------------------------------- dependency closure


def runtime_roots(pyproject_text: str) -> list[str]:
    """Distribution names of the runtime extras (all ``[project.optional-dependencies]`` except ``dev``)."""
    import tomllib

    extras = tomllib.loads(pyproject_text).get("project", {}).get("optional-dependencies", {})
    names: set[str] = set()
    for extra, reqs in extras.items():
        if extra == "dev":
            continue
        for r in reqs:
            m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", r)
            if m:
                names.add(_canon(m.group(1)))
    return sorted(names)


def _packaging():
    """:mod:`packaging` from the venv (a pytest dependency) or pip's vendored copy."""
    try:
        from packaging import markers, requirements, tags, utils  # type: ignore[import-not-found]
    except ImportError:
        try:
            from pip._vendor.packaging import markers, requirements, tags, utils  # type: ignore[import-not-found]
        except ImportError:
            raise SatkError("DEPENDENCY", "--relock needs the 'packaging' module (installed with pytest in the "
                                          "development venv)", hint="run it with the satk.cmd of a checkout") from None
    return markers, requirements, tags, utils


def target_env(python_version: str = PYTHON_VERSION) -> dict[str, str]:
    """PEP 508 marker environment of the embeddable CPython on 64-bit Windows."""
    short = ".".join(python_version.split(".")[:2])
    return {"implementation_name": "cpython", "implementation_version": python_version, "os_name": "nt",
            "platform_machine": "AMD64", "platform_python_implementation": "CPython", "platform_release": "10",
            "platform_system": "Windows", "platform_version": "10.0", "python_full_version": python_version,
            "python_version": short, "sys_platform": "win32"}


def closure(roots: list[str], *, python_version: str = PYTHON_VERSION,
            distribution: Callable[[str], Any] | None = None) -> dict[str, tuple[str, str]]:
    """``{canonical name: (name, version)}``: ``roots`` and their requirements for the target, with extras.

    ``distribution`` (default :func:`importlib.metadata.distribution`) returns an object with
    ``metadata["Name"]``, ``version`` and ``requires``; a missing one is ``DEPENDENCY``.
    """
    from importlib import metadata as md

    markers, requirements, _, _ = _packaging()
    dist_of = distribution or md.distribution
    env = target_env(python_version)
    seen: dict[str, frozenset[str]] = {}
    found: dict[str, tuple[str, str]] = {}
    todo: list[tuple[str, frozenset[str]]] = [(r, frozenset()) for r in roots]
    while todo:
        name, extras = todo.pop()
        key = _canon(name)
        have = seen.get(key)
        if have is not None and extras <= have:
            continue
        seen[key] = (have or frozenset()) | extras
        try:
            d = dist_of(name)
        except Exception:  # noqa: BLE001 - PackageNotFoundError or a test double
            raise SatkError("DEPENDENCY", f"{name} is not installed in this Python; --relock reads the installed "
                                          "versions", hint="bootstrap the venv from requirements.lock first") from None
        found[key] = (d.metadata["Name"], d.version)
        for spec in d.requires or []:
            req = requirements.Requirement(spec)
            wanted = [""] + sorted(seen[key])
            if req.marker is None or any(req.marker.evaluate({**env, "extra": e}) for e in wanted):
                todo.append((req.name, frozenset(_canon(x) for x in req.extras)))
    return dict(sorted(found.items()))


def _lock_pins(text: str) -> dict[str, str]:
    pins = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            k, v = line.split("==", 1)
            pins[_canon(k)] = v.strip()
    return pins


def pick_wheel(files: list[dict], *, python_version: str = PYTHON_VERSION, platform: str = PLATFORM) -> dict:
    """The best wheel of a PyPI release (``urls`` of its JSON) for CPython ``python_version`` on ``platform``."""
    _, _, tags, utils = _packaging()
    major, minor = (int(x) for x in python_version.split(".")[:2])
    supported = [*tags.cpython_tags((major, minor), abis=[f"cp{major}{minor}"], platforms=[platform]),
                 *tags.compatible_tags((major, minor), f"cp{major}{minor}", [platform])]
    rank = {t: i for i, t in enumerate(supported)}
    best: tuple[int, dict] | None = None
    for f in files:
        if f.get("packagetype") != "bdist_wheel" or f.get("yanked"):
            continue
        try:
            _, _, _, wtags = utils.parse_wheel_filename(f["filename"])
        except Exception:  # noqa: BLE001 - InvalidWheelFilename
            continue
        r = min((rank[t] for t in wtags if t in rank), default=None)
        if r is not None and (best is None or r < best[0]):
            best = (r, f)
    if best is None:
        raise SatkError("NOT_FOUND", "no compatible wheel", data={"files": [f.get("filename") for f in files]})
    return best[1]


# --------------------------------------------------------------------------- python.org


def authenticode(files: list[Path]) -> dict[str, tuple[str, str]]:
    """``{file name: (status, signer subject)}`` from PowerShell ``Get-AuthenticodeSignature``."""
    if os.name != "nt":
        raise SatkError("UNSUPPORTED", "Authenticode checks need Windows")
    with tempfile.TemporaryDirectory(prefix="satk-sig-", dir=os.path.dirname(files[0])) as td:
        lst = Path(td) / "files.txt"
        lst.write_text("\n".join(str(f) for f in files), encoding="utf-8")
        script = ("$OutputEncoding=[Text.Encoding]::UTF8;[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                  f"Get-Content -LiteralPath '{lst}' -Encoding UTF8 | ForEach-Object {{"
                  "$s=Get-AuthenticodeSignature -LiteralPath $_;"
                  "\"$($s.Status)`t$($s.SignerCertificate.Subject)`t$(Split-Path $_ -Leaf)\" }")
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                           timeout=300)
    if p.returncode != 0:
        raise SatkError("EXTERNAL_TOOL", "Get-AuthenticodeSignature failed: "
                        + p.stderr.decode("utf-8", "replace").strip()[:300])
    out: dict[str, tuple[str, str]] = {}
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 3:
            out[parts[2]] = (parts[0], parts[1])
    return out


def _verify_python(zip_path: Path, tmp: Path) -> list[str]:
    """Authenticode of every PE file of the embeddable zip; returns the distinct signers."""
    d = tmp / "embed"
    with zipfile.ZipFile(zip_path) as zf:
        pe = [n for n in zf.namelist() if n.lower().endswith((".exe", ".dll", ".pyd"))]
        zf.extractall(d, members=pe)
    sig = authenticode(sorted(d / n for n in pe))
    bad = {n: s for n, s in sig.items() if s[0] != "Valid" or not s[1].startswith(SIGNERS)}
    if bad or len(sig) != len(pe):
        raise SatkError("CHECK_FAILED", f"{zip_path.name}: Authenticode check failed", data={"bad": bad,
                                                                                         "checked": len(sig)})
    return sorted({s[1].split(",")[0] for s in sig.values()})


def _python_entry(version: str, cache: Path, tmp: Path, offline: bool) -> dict:
    rel = get_json(PYTHON_API.format(v=version))
    if not rel:
        raise SatkError("NOT_FOUND", f"python.org has no release {version}")
    rid = rel[0]["resource_uri"].rstrip("/").rsplit("/", 1)[-1]
    url = PYTHON_URL.format(v=version)
    info = next((f for f in get_json(PYTHON_FILES.format(id=rid)) if f.get("url") == url), None)
    if info is None:
        raise SatkError("NOT_FOUND", f"python.org lists no embeddable amd64 zip for {version}",
                        hint="3.12 security releases after 3.12.10 ship no Windows binaries")
    path = fetch(url, cache / Path(url).name, sha256=None, size=int(info["filesize"]), offline=offline)
    md5 = sha256_file(path, algo="md5")
    if md5 != info["md5_sum"]:
        raise SatkError("CHECK_FAILED", f"{path.name}: md5 {md5} differs from python.org ({info['md5_sum']})")
    signers = _verify_python(path, tmp)
    return {"version": version, "file": path.name, "url": url, "size": path.stat().st_size,
            "sha256": sha256_file(path), "md5": md5, "openpgp": PYTHON_PGP,
            "verified": ["size and md5 = python.org release API", "Authenticode: " + "; ".join(signers),
                         f"OpenPGP signature ({Path(url).name}.asc) by {PYTHON_PGP}: see packaging/README.md"]}


def relock(repo: Path, cache: Path, tmp: Path, *, python_version: str = PYTHON_VERSION,
           offline: bool = False) -> dict[str, Any]:
    """New lock data for ``repo`` (its ``pyproject.toml`` and ``requirements.lock``); network unless cached."""
    roots = runtime_roots((repo / "pyproject.toml").read_text(encoding="utf-8"))
    deps = closure(roots, python_version=python_version)
    pins = _lock_pins((repo / "requirements.lock").read_text(encoding="ascii", errors="replace"))
    off = {k: (v, pins.get(k)) for k, (_, v) in deps.items() if pins.get(k) != v}
    if off:
        raise SatkError("CHECK_FAILED", "installed versions differ from requirements.lock",
                        hint="reinstall the venv from requirements.lock (bootstrap.ps1 -Deps -FromLock)",
                        data={"installed_vs_lock": off})
    wheels = []
    for key, (name, version) in deps.items():
        meta = get_json(PYPI_JSON.format(name=key, version=version))
        f = pick_wheel(meta.get("urls", []), python_version=python_version)
        wheels.append({"name": key, "version": version, "file": f["filename"], "url": f["url"],
                       "size": int(f["size"]), "sha256": f["digests"]["sha256"]})
    return {"lock_version": LOCK_VERSION,
            "target": {"python": python_version, "platform": PLATFORM},
            "python": _python_entry(python_version, cache, tmp, offline),
            "roots": roots,
            "wheels": wheels,
            "excluded": sorted(k for k in pins if k not in deps)}
