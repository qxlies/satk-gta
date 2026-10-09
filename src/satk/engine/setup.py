"""``satk engine setup``: offline fork (SPEC §4.12.1), build shims (§4.12.2), dependencies (D3).

* :func:`ensure_fork` — blobless clone of ``src\\mtasa-neon`` with ``upload-pack`` filters,
  remote ``neon`` (file://, push DISABLED, no tags), branch ``main`` from the donor's
  ``refs/remotes/upstream/master``, remote ``upstream`` = GitHub (push DISABLED, never fetched
  here, ``skipFetchAll``), rerere/zdiff3. Idempotent; an existing checkout is only re-configured.
* :func:`write_templates` — ``Directory.Build.targets``, ``shims\\afxres.h``,
  ``satk-premake.lua``, ``bootstrap.ps1`` from ``satk/engine/templates`` (single source).
* :func:`fetch_deps` — every downloaded build/runtime dependency is stored once under
  ``engine\\deps`` and pinned by sha256 in ``engine\\deps-lock.json``: upstream pins (CEF,
  discord-rpc, rapidjson, Unifont from the fork's ``utils/buildactions``), our pin for
  ``DXFiles.zip``, trust-on-first-use pins for the unhashed ``net*.dll``/``netc.dll`` (R22).
  Sources in order: the store, reusable copies (build spike, the fork itself), download.
* :func:`install_deps` — extracts DXFiles and runs the fork's premake actions through the
  wrapper with ``SATK_OFFLINE=1``: premake never downloads; it gets the pinned files.
"""

from __future__ import annotations

import os
import re
import shutil
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath
from ..core.registry import report_progress
from .common import (
    DONOR_UPSTREAM_REF,
    LOCAL_UPSTREAM_REF,
    UPLOADPACK,
    UPSTREAM_URL,
    Layout,
    build_env,
    donor_git,
    file_url,
    find_premake,
    fork_profile,
    git,
    layout,
    read_json,
    run,
    sha256_file,
    stamp,
    write_json,
)

__all__ = [
    "TEMPLATES",
    "DXFILES_URL",
    "DXFILES_SHA256",
    "Dep",
    "ensure_fork",
    "fork_info",
    "write_templates",
    "template_status",
    "manifest",
    "load_lock",
    "fetch_deps",
    "install_deps",
    "verify_installed",
    "setup",
]

TEMPLATES = Path(__file__).resolve().parent / "templates"

#: DXFiles.zip as used by upstream CI (``.github/workflows/build.yaml``); no upstream hash,
#: pinned by satk from the build spike (report 28 §1).
DXFILES_URL = "https://mirror-cdn.multitheftauto.com/bdata/DXFiles.zip"
DXFILES_SHA256 = "8e48162702d3af0d6da39663f4ec7fc793498135226d465907752d46781e9d7d"

_UA = "satk-engine-setup/1 (+https://github.com/multitheftauto/mtasa-blue build deps)"


# --------------------------------------------------------------------------- fork


def _cfg_get(key: str, cwd: Path) -> str | None:
    v = git("config", "--get", key, cwd=cwd, check=False)
    return v or None


def _cfg_set(key: str, value: str, cwd: Path, changes: list[str]) -> None:
    if _cfg_get(key, cwd) != value:
        git("config", key, value, cwd=cwd)
        changes.append(f"{key}={value}")


def _remotes(cwd: Path) -> list[str]:
    out = git("remote", cwd=cwd, check=False)
    return [r.strip() for r in out.splitlines() if r.strip()]


def _ref_exists(ref: str, cwd: Path) -> bool:
    return bool(git("rev-parse", "--verify", "--quiet", ref, cwd=cwd, check=False))


def ensure_fork(L: Layout | None = None) -> dict:
    """Create or re-configure ``engine\\mtasa`` (SPEC §4.12.1). Never touches ``src``."""
    L = L or layout()
    fork, donor = L.fork, L.donor
    ensure_writable(fork)
    git_dir = ensure_writable(fork / ".git")
    if git_dir.is_file():  # linked worktree: Git writes outside the checkout itself
        text = git_dir.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            git_dir = ensure_writable(fork / text[len("gitdir:"):].strip())
    common_dir = git_dir / "commondir"
    if common_dir.is_file():
        common_dir = ensure_writable(git_dir / common_dir.read_text(encoding="utf-8").strip())
        ensure_writable(common_dir / "config")
    ensure_writable(git_dir / "config")
    steps: list[str] = []
    created = False
    if not (fork / ".git").exists():
        if fork.exists() and any(fork.iterdir()):
            raise SatkError("EXISTS", f"{jpath(fork)} exists, is not empty and is not a git checkout",
                            hint="move it away; satk engine setup clones into an empty directory")
        if not (donor / ".git").exists():
            raise SatkError("NOT_READY", f"donor clone not found: {jpath(donor)}",
                            hint="the fork is created offline from <workspace>\\src\\mtasa-neon")
        if not donor_git("rev-parse", "--verify", "--quiet", DONOR_UPSTREAM_REF, check=False):
            raise SatkError("NOT_READY", f"donor has no {DONOR_UPSTREAM_REF}",
                            hint="the donor must track upstream mtasa-blue (remote 'upstream')")
        ensure_writable(L.root)
        L.root.mkdir(parents=True, exist_ok=True)
        git("clone", "--no-checkout", "--filter=blob:none", "-u", UPLOADPACK, file_url(donor), str(fork),
            cwd=L.root, timeout=1800)
        steps.append("clone --no-checkout --filter=blob:none (offline, file://)")
        created = True
    remotes = _remotes(fork)
    if "neon" not in remotes and "origin" in remotes:
        url = _cfg_get("remote.origin.url", fork) or ""
        if url.rstrip("/").lower() == file_url(donor).lower():
            git("remote", "rename", "origin", "neon", cwd=fork)
            steps.append("remote rename origin -> neon")
            remotes = _remotes(fork)
    if "neon" not in remotes:
        git("remote", "add", "neon", file_url(donor), cwd=fork)
        git("config", "remote.neon.promisor", "true", cwd=fork)
        git("config", "remote.neon.partialclonefilter", "blob:none", cwd=fork)
        steps.append("remote add neon")
    changes: list[str] = []
    _cfg_set("remote.neon.uploadpack", UPLOADPACK, fork, changes)
    _cfg_set("remote.neon.pushurl", "DISABLED", fork, changes)
    _cfg_set("remote.neon.tagOpt", "--no-tags", fork, changes)
    if not _ref_exists(LOCAL_UPSTREAM_REF, fork):
        git("fetch", "neon", f"{DONOR_UPSTREAM_REF}:{LOCAL_UPSTREAM_REF}", cwd=fork, timeout=1800)
        steps.append(f"fetch neon {DONOR_UPSTREAM_REF}:{LOCAL_UPSTREAM_REF}")
    if not _ref_exists("refs/heads/main", fork):
        git("switch", "-c", "main", LOCAL_UPSTREAM_REF, cwd=fork, timeout=1800)
        steps.append("switch -c main upstream-local/master")
    if _ref_exists("refs/heads/master", fork) and not _ref_exists("refs/heads/lab/neon", fork):
        git("branch", "-m", "master", "lab/neon", cwd=fork)
        steps.append("branch -m master lab/neon (read-only Neon reference)")
    if "upstream" not in _remotes(fork):
        git("remote", "add", "upstream", UPSTREAM_URL, cwd=fork)
        steps.append("remote add upstream (GitHub, not fetched: consent D4)")
    _cfg_set("remote.upstream.pushurl", "DISABLED", fork, changes)
    _cfg_set("remote.upstream.tagOpt", "--no-tags", fork, changes)
    _cfg_set("remote.upstream.skipFetchAll", "true", fork, changes)
    _cfg_set("rerere.enabled", "true", fork, changes)
    _cfg_set("rerere.autoUpdate", "true", fork, changes)
    _cfg_set("merge.conflictStyle", "zdiff3", fork, changes)
    if changes:
        steps.append("config: " + ", ".join(changes))
    info = fork_info(L)
    info["created"] = created
    info["steps"] = steps
    return info


def fork_info(L: Layout | None = None) -> dict:
    """Branch, revisions and remotes of the fork (read-only)."""
    L = L or layout()
    fork = L.fork
    if not (fork / ".git").exists():
        return {"exists": False, "path": jpath(fork)}
    head = git("rev-parse", "HEAD", cwd=fork, check=False)
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=fork, check=False)
    base = git("rev-parse", LOCAL_UPSTREAM_REF, cwd=fork, check=False)
    main = git("rev-parse", "--short=9", "refs/heads/main", cwd=fork, check=False)
    ahead = git("rev-list", "--count", f"{LOCAL_UPSTREAM_REF}..refs/heads/main", cwd=fork, check=False) if base else ""
    remotes: dict[str, dict] = {}
    for line in git("remote", "-v", cwd=fork, check=False).splitlines():
        parts = line.split()
        if len(parts) >= 3:
            remotes.setdefault(parts[0], {})[parts[2].strip("()")] = parts[1]
    upstream_fetched = bool(git("for-each-ref", "--count=1", "refs/remotes/upstream/", cwd=fork, check=False))
    dirty = git("--no-optional-locks", "status", "--porcelain", cwd=fork, check=False)
    return {
        "exists": True,
        "path": jpath(fork),
        "branch": branch,
        "head": head[:9] if head else None,
        "main": main or None,
        "base": base[:9] if base else None,
        "ahead_of_base": int(ahead) if ahead.isdigit() else None,
        "remotes": remotes,
        "upstream_fetched": upstream_fetched,
        "rerere": _cfg_get("rerere.enabled", fork) == "true",
        "dirty": len([x for x in dirty.splitlines() if x.strip()]),
    }


# --------------------------------------------------------------------------- templates


def _template_map(L: Layout) -> dict[str, Path]:
    return {
        "afxres.h": L.afxres,
        "Directory.Build.targets": L.targets,
        "satk-premake.lua": L.wrapper,
        "bootstrap.ps1": L.bootstrap,
    }


def template_bytes(name: str) -> bytes:
    """Template content with deterministic line endings (independent of the git checkout's
    ``core.autocrlf``): CRLF for ``.ps1`` (Windows PowerShell), LF for everything else.
    ``{{WORKSPACE}}`` becomes ``paths.workspace`` (Windows spelling) of the current config."""
    data = (TEMPLATES / name).read_bytes().replace(b"\r\n", b"\n")
    data = data.replace(b"{{WORKSPACE}}", str(cfg().paths.workspace).encode("utf-8"))
    if name.lower().endswith(".ps1"):
        data = data.replace(b"\n", b"\r\n")
    return data


def template_status(L: Layout | None = None) -> list[dict]:
    """Per template: ``{"name", "path", "status": ok|missing|differs}``."""
    L = L or layout()
    out = []
    for name, dest in _template_map(L).items():
        want = template_bytes(name)
        if not dest.is_file():
            st = "missing"
        else:
            st = "ok" if dest.read_bytes() == want else "differs"
        out.append({"name": name, "path": jpath(dest), "status": st})
    return out


def write_templates(L: Layout | None = None) -> list[dict]:
    """Write/refresh the engine-level files from ``templates`` (atomic, idempotent)."""
    L = L or layout()
    out = []
    for name, dest in _template_map(L).items():
        want = template_bytes(name)
        if dest.is_file() and dest.read_bytes() == want:
            out.append({"name": name, "path": jpath(dest), "status": "ok"})
            continue
        st = "updated" if dest.exists() else "written"
        atomic_write(dest, want)
        out.append({"name": name, "path": jpath(dest), "status": st})
    return out


# --------------------------------------------------------------------------- dependency manifest


@dataclass
class Dep:
    """One downloaded dependency (build or runtime)."""

    id: str
    name: str
    url: str
    sha256: str | None  # expected hash; None = trust on first use (TOFU), then pinned in the lock
    pin: str  # "upstream" | "satk" | "tofu"
    store: str  # "cache" | "net"
    purpose: str  # "build" | "runtime"
    reuse: tuple[str, ...] = ()  # candidate paths relative to a reuse root
    installs_to: str = ""
    extra: dict = field(default_factory=dict)


def _lua_str(text: str, var: str, *, local: bool = True) -> str:
    """Value of ``local VAR = "..."`` (first top-level assignment)."""
    pat = rf'^\s*{"local " if local else ""}{re.escape(var)}\s*=\s*"([^"]*)"'
    m = re.search(pat, text, re.M)
    if not m:
        raise SatkError("NOT_READY", f"cannot find {var} in the fork's build actions",
                        hint="upstream changed utils/buildactions; update satk.engine.setup.manifest")
    return m.group(1)


def manifest(L: Layout | None = None) -> list[Dep]:
    """Dependencies with URLs and upstream pins read from the fork's ``utils/buildactions``."""
    L = L or layout()
    ba = L.fork / "utils" / "buildactions"
    try:
        cef = (ba / "install_cef.lua").read_text(encoding="utf-8")
        dis = (ba / "install_discord.lua").read_text(encoding="utf-8")
        uni = (ba / "install_unifont.lua").read_text(encoding="utf-8")
        dat = (ba / "install_data.lua").read_text(encoding="utf-8")
    except OSError as e:
        raise SatkError("NOT_READY", f"fork build actions not readable: {e}", hint="satk engine setup") from None

    cef_ver = _lua_str(cef, "CEF_VERSION")
    cef_name = f"cef_binary_{cef_ver}_windows32_minimal.tar.bz2"
    cef_url = _lua_str(cef, "CEF_URL_PREFIX") + cef_ver + _lua_str(cef, "CEF_URL_SUFFIX")
    d_ver, r_ver = _lua_str(dis, "DISCORD_VERSION"), _lua_str(dis, "RAPID_VERSION")
    uni_file = _lua_str(uni, "UNIFONT_DOWNLOAD_FILENAME")
    uni_url = _lua_str(uni, "UNIFONT_BASEURL") + _lua_str(uni, "UNIFONT_TAG") + "/" + uni_file
    base = _lua_str(dat, "BASE_URL")

    def net(var: str) -> str:
        m = re.search(rf'^\s*local\s+{var}\s*=\s*BASE_URL\s*\.\.\s*"([^"]+)"', dat, re.M)
        if not m:
            raise SatkError("NOT_READY", f"cannot find {var} in install_data.lua")
        return m.group(1)

    deps = [
        Dep("dxfiles", "DXFiles.zip", DXFILES_URL, DXFILES_SHA256, "satk", "cache", "build",
            ("utils/DXFiles.zip",), "engine/deps/DXFiles"),
        Dep("cef", cef_name, cef_url, _lua_str(cef, "CEF_HASH").lower(), "upstream", "cache", "build",
            ("vendor/cef3/temp.tar.bz2",), "vendor/cef3/cef", {"version": cef_ver}),
        Dep("discord-rpc", f"discord-rpc-{d_ver}.zip",
            _lua_str(dis, "DISCORD_URL") + d_ver + _lua_str(dis, "DISCORD_EXT"),
            _lua_str(dis, "DISCORD_HASH").lower(), "upstream", "cache", "build",
            ("vendor/discord-rpc/discord-rpc.zip",), "vendor/discord-rpc/discord"),
        Dep("rapidjson", f"rapidjson-{r_ver}.zip",
            _lua_str(dis, "RAPID_URL") + r_ver + _lua_str(dis, "RAPID_EXT"),
            _lua_str(dis, "RAPID_HASH").lower(), "upstream", "cache", "build",
            ("vendor/discord-rpc/rapidjson.zip",), "vendor/discord-rpc/discord/thirdparty/rapidjson"),
        Dep("unifont", uni_file, uni_url, _lua_str(uni, "UNIFONT_HASH").lower(), "upstream", "cache", "runtime",
            ("Shared/data/MTA San Andreas/MTA/cgui/unifont.ttf",), "Shared/data/MTA San Andreas/MTA/cgui/unifont.ttf"),
    ]
    for dep_id, var, reuse, dest in (
        ("net-x86", "NET_PATH_X86_WIN", "Bin/server/net.dll", "Bin/server/net.dll"),
        ("net-x64", "NET_PATH_X64_WIN", "Bin/server/x64/net.dll", "Bin/server/x64/net.dll"),
        ("net-arm64", "NET_PATH_ARM64_WIN", "Bin/server/arm64/net.dll", "Bin/server/arm64/net.dll"),
        ("netc", "NETC_PATH_WIN", "Bin/MTA/netc.dll", "Bin/MTA/netc.dll"),
    ):
        fname = net(var)
        deps.append(Dep(dep_id, fname, base + fname, None, "tofu", "net", "runtime", (reuse,), dest))
    return deps


# --------------------------------------------------------------------------- lock


def load_lock(L: Layout | None = None) -> dict:
    L = L or layout()
    data = read_json(L.lock, None)
    if not isinstance(data, dict):
        return {"version": 1, "items": []}
    data.setdefault("items", [])
    return data


def _lock_by_id(lock: dict) -> dict[str, dict]:
    return {it.get("id"): it for it in lock.get("items", []) if isinstance(it, dict)}


def _store_path(L: Layout, dep: Dep, sha: str) -> tuple[Path, str]:
    """(absolute path, path relative to ``deps`` with forward slashes)."""
    rel = f"cache/{dep.name}" if dep.store == "cache" else f"net/{sha}/{dep.name}"
    return L.deps / rel, rel


def _copy_into(src: Path, dst: Path) -> None:
    ensure_writable(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    shutil.copyfile(src, part)
    os.replace(part, dst)


def _download(url: str, dst: Path, timeout: float = 120) -> int:
    ensure_writable(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    n = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:  # noqa: S310 - fixed https URLs
            total = int(r.headers.get("Content-Length") or 0) or None
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                n += len(b)
                report_progress(n, total, f"download {dst.name}")
    except OSError as e:
        try:
            part.unlink()
        except OSError:
            pass
        raise SatkError("EXTERNAL_TOOL", f"download failed: {url}: {e}") from None
    os.replace(part, dst)
    return n


def default_reuse_roots(L: Layout | None = None) -> list[Path]:
    """Trees that may already hold verified copies: the build spike, then the fork itself."""
    L = L or layout()
    roots = [Path(os.path.abspath(cfg().paths.workspace)) / "spikes" / "mtasa-blue", L.fork]
    return [r for r in roots if r.is_dir()]


def fetch_deps(L: Layout | None = None, *, allow_download: bool = False, reuse_roots: list[Path] | None = None,
               update_pins: bool = False, only: list[str] | None = None) -> list[dict]:
    """Make every dependency available in ``engine\\deps`` and write ``deps-lock.json``.

    Raises ``REVISION`` if a TOFU-pinned file changed upstream and ``update_pins`` is off.
    Returns rows ``{id, name, sha256, size, source, pin, status, file}``.
    """
    L = L or layout()
    deps = manifest(L)
    if only:
        known = {d.id for d in deps}
        bad = [x for x in only if x not in known]
        if bad:
            raise SatkError("BAD_PARAMS", f"unknown dependency id(s): {', '.join(bad)}", data={"ids": sorted(known)})
        deps = [d for d in deps if d.id in only]
    roots = default_reuse_roots(L) if reuse_roots is None else reuse_roots
    lock = load_lock(L)
    by_id = _lock_by_id(lock)
    rows: list[dict] = []
    for i, dep in enumerate(deps):
        report_progress(i, len(deps), f"dependency {dep.id}")
        prev = by_id.get(dep.id)
        expected = dep.sha256
        if expected is None and prev and prev.get("url") == dep.url and not update_pins:
            expected = prev.get("sha256")  # TOFU pin from the lock
        status = None
        source = None
        sha = None
        # --update-pins on a trust-on-first-use file = fetch the CDN's current version again
        refresh = update_pins and dep.pin == "tofu" and allow_download
        # 1. already in the store
        if not refresh and prev and prev.get("file") and (prev.get("sha256") == expected or expected is None):
            p = L.deps / prev["file"]
            if p.is_file():
                h = sha256_file(p)
                if h == prev.get("sha256"):
                    sha, source, status = h, prev.get("source", "store"), "ok"
        # 2. reusable copies (spike tree, fork tree)
        if status is None and not refresh:
            for root in roots:
                for rel in dep.reuse:
                    cand = root / rel
                    if not cand.is_file():
                        continue
                    h = sha256_file(cand)
                    if expected is not None and h != expected:
                        continue
                    dst, _ = _store_path(L, dep, h)
                    if not (dst.is_file() and sha256_file(dst) == h):
                        _copy_into(cand, dst)
                    sha, source, status = h, f"reused:{jpath(cand)}", "reused"
                    break
                if status:
                    break
        # 3. download (consent D3: --deps)
        if status is None and allow_download:
            tmpdst = L.cache / f".{dep.name}.download"
            _download(dep.url, tmpdst)
            h = sha256_file(tmpdst)
            if expected is not None and h != expected:
                tmpdst.unlink(missing_ok=True)
                code = "REVISION" if dep.pin == "tofu" else "EXTERNAL_TOOL"
                raise SatkError(code, f"{dep.name}: sha256 {h} != pinned {expected}",
                                hint=("the CDN file changed; re-run with --update-pins only if you trust it"
                                      if dep.pin == "tofu" else "upstream archive mismatch; do not use it"),
                                data={"url": dep.url})
            dst, _ = _store_path(L, dep, h)
            ensure_writable(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmpdst, dst)
            sha, source, status = h, "download", "downloaded"
        if status is None:
            rows.append({"id": dep.id, "name": dep.name, "status": "missing", "pin": dep.pin,
                         "purpose": dep.purpose, "url": dep.url})
            continue
        dst, rel = _store_path(L, dep, sha)
        entry = {
            "id": dep.id, "name": dep.name, "url": dep.url, "sha256": sha, "size": dst.stat().st_size,
            "file": rel, "pin": dep.pin, "purpose": dep.purpose, "source": source,
            "installs_to": dep.installs_to,
            "pinned_at": (prev or {}).get("pinned_at") if prev and prev.get("sha256") == sha
            else time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        entry.update(dep.extra)
        by_id[dep.id] = entry
        rows.append({"id": dep.id, "name": dep.name, "sha256": sha, "size": entry["size"], "file": rel,
                     "pin": dep.pin, "purpose": dep.purpose, "source": source, "status": status})
    report_progress(len(deps), len(deps), "dependencies")
    order = [d.id for d in manifest(L)]
    items = sorted(by_id.values(), key=lambda it: order.index(it["id"]) if it.get("id") in order else 99)
    write_json(L.lock, {
        "version": 1,
        "comment": "satk: sha256 of every downloaded MTA build/runtime dependency. "
                   "Files live under engine/deps/<file>. New hashes only via satk engine setup --deps --update-pins.",
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "items": items,
    })
    return rows


# --------------------------------------------------------------------------- install


def _extract_dxfiles(L: Layout, zpath: Path, sha: str) -> str:
    marker = L.dxfiles / ".satk-sha256"
    if (L.dxfiles / "Include" / "d3dx9.h").is_file() and marker.is_file() and \
            marker.read_text(encoding="utf-8").strip() == sha:
        return "ok"
    ensure_writable(L.dxfiles)
    staging = L.deps / ".DXFiles.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    root = staging.resolve()
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            target = (staging / info.filename).resolve()
            if root not in target.parents and target != root:
                raise SatkError("BAD_PARAMS", f"unsafe path in {zpath.name}: {info.filename}")
        z.extractall(staging)
    (staging / ".satk-sha256").write_text(sha + "\n", encoding="utf-8")
    if L.dxfiles.exists():
        shutil.rmtree(L.dxfiles)
    os.replace(staging, L.dxfiles)
    return "extracted"


def _premake_action(L: Layout, action: str) -> dict:
    for path in (L.fork, L.fork / "vendor", L.fork / "Shared", L.bin, L.logs):
        ensure_writable(path)
    premake = find_premake(L.fork)
    if premake is None:
        raise SatkError("NOT_READY", "premake5.exe not found", hint="satk engine setup (fork utils/premake5.exe)")
    log = L.logs / f"{stamp()}-premake-{action}.log"
    ensure_writable(log)
    r = run([premake, f"--file={L.wrapper}", action], cwd=L.fork,
            env=build_env({"SATK_OFFLINE": "1", "SATK_FORK": str(L.fork), "SATK_DEPS_LOCK": str(L.lock)}),
            log=log, timeout=1800)
    served = len(re.findall(r"^satk: served ", r.out, re.M))
    row = {"step": f"premake {action}", "exit": r.code, "seconds": r.seconds, "served": served, "log": jpath(log)}
    if r.code != 0:
        raise SatkError("EXTERNAL_TOOL", f"premake {action} failed (exit {r.code})",
                        hint=f"see {jpath(log)}", data={"tail": r.out[-1500:], **row})
    return row


def verify_installed(L: Layout | None = None) -> list[dict]:
    """Installed state per dependency: present, and for files copied verbatim, sha256 vs lock."""
    L = L or layout()
    lock = _lock_by_id(load_lock(L))
    out = []
    checks = {
        "dxfiles": L.dxfiles / "Include" / "d3dx9.h",
        "cef": L.fork / "vendor" / "cef3" / "cef" / "include" / "cef_version.h",
        "discord-rpc": L.fork / "vendor" / "discord-rpc" / "discord" / "src" / "discord_rpc.cpp",
        "rapidjson": L.fork / "vendor" / "discord-rpc" / "discord" / "thirdparty" / "rapidjson" / "include"
        / "rapidjson" / "document.h",
    }
    verbatim = {
        "unifont": L.fork / "Shared" / "data" / "MTA San Andreas" / "MTA" / "cgui" / "unifont.ttf",
        "net-x86": L.bin / "server" / "net.dll",
        "net-x64": L.bin / "server" / "x64" / "net.dll",
        "net-arm64": L.bin / "server" / "arm64" / "net.dll",
        "netc": L.bin / "MTA" / "netc.dll",
    }
    if fork_profile(L) == "server":  # a sparse server checkout: only the x64 server module is expected
        checks = {}
        verbatim = {"net-x64": verbatim["net-x64"]}
    for dep_id, p in checks.items():
        out.append({"id": dep_id, "path": jpath(p), "status": "ok" if p.is_file() else "missing"})
    for dep_id, p in verbatim.items():
        pin = (lock.get(dep_id) or {}).get("sha256")
        if not p.is_file():
            st = "missing"
        elif not pin:
            st = "unpinned"
        else:
            st = "ok" if sha256_file(p) == pin else "mismatch"
        out.append({"id": dep_id, "path": jpath(p), "status": st})
    return out


def install_deps(L: Layout | None = None) -> list[dict]:
    """Extract DXFiles and run install_cef/install_discord/install_unifont/install_data offline."""
    L = L or layout()
    lock = _lock_by_id(load_lock(L))
    missing = [d.id for d in manifest(L) if d.id not in lock]
    if missing:
        raise SatkError("NOT_READY", f"dependencies not fetched: {', '.join(missing)}",
                        hint="satk engine setup --deps")
    steps = []
    dx = lock["dxfiles"]
    t0 = time.perf_counter()
    st = _extract_dxfiles(L, L.deps / dx["file"], dx["sha256"])
    steps.append({"step": "DXFiles", "exit": 0, "seconds": round(time.perf_counter() - t0, 2), "status": st,
                  "path": jpath(L.dxfiles)})
    for action in ("install_cef", "install_discord", "install_unifont", "install_data"):
        report_progress(len(steps), 5, action)
        steps.append(_premake_action(L, action))
    bad = [v for v in verify_installed(L) if v["status"] != "ok"]
    if bad:
        raise SatkError("EXTERNAL_TOOL", f"after install: {', '.join(b['id'] + '=' + b['status'] for b in bad)}",
                        data={"bad": bad, "steps": steps})
    return steps


# --------------------------------------------------------------------------- all together


def setup(*, deps: bool = False, offline: bool = False, update_pins: bool = False,
          reuse_from: list[str] | None = None) -> dict:
    """Fork + templates; with ``deps`` also fetch (reuse first, download unless ``offline``) and install."""
    L = layout()
    donor_before = donor_git("status", "--porcelain", check=False) if (L.donor / ".git").exists() else None
    info = ensure_fork(L)
    tpl = write_templates(L)
    out: dict = {"fork": info, "templates": tpl}
    roots = default_reuse_roots(L) + [Path(p) for p in (reuse_from or [])]
    if deps:
        rows = fetch_deps(L, allow_download=not offline, reuse_roots=roots, update_pins=update_pins)
        out["deps"] = rows
        missing = [r["id"] for r in rows if r["status"] == "missing"]
        if missing:
            raise SatkError("NOT_READY", f"dependencies missing: {', '.join(missing)}",
                            hint="re-run without --offline (downloads need consent D3)", data={"deps": rows})
        out["install"] = install_deps(L)
        out["lock"] = jpath(L.lock)
    else:
        lock = _lock_by_id(load_lock(L))
        out["deps_pinned"] = sorted(lock)
        out["next"] = "satk engine setup --deps  (client build deps, consent D3)" if len(lock) < 9 else "satk engine gen"
    if donor_before is not None:
        donor_after = donor_git("status", "--porcelain", check=False)
        out["donor_clean"] = donor_before == donor_after == ""
        if donor_before != donor_after:
            out.setdefault("warn", []).append("DONOR_CHANGED: git status of src/mtasa-neon changed during setup")
    return out
