"""Windows aliases and atomic-write regressions; all protected files are synthetic."""

from __future__ import annotations

import ctypes
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from satk.core import config
from satk.core import paths as P
from satk.core.errors import SatkError


@pytest.mark.skipif(os.name != "nt", reason="Windows path spellings")
@pytest.mark.parametrize("spelling", ["extended", "extended-forward", "case", "trailing-dot",
                                      "trailing-space", "traversal"])
def test_windows_alias_cannot_write_protected_file(satk_home, spelling):
    root = satk_home / "src"
    root.mkdir()
    victim = root / "untouched.txt"
    victim.write_bytes(b"protected original")
    aliases = {
        "extended": "\\\\?\\" + str(victim),
        "extended-forward": "//?/" + victim.as_posix(),
        "case": str(victim).swapcase(),
        "trailing-dot": str(root) + ".\\untouched.txt",
        "trailing-space": str(root) + " \\untouched.txt",
        "traversal": str(root.parent / "unused" / ".." / "src" / "untouched.txt"),
    }
    with pytest.raises(SatkError) as exc:
        P.atomic_write(aliases[spelling], b"must not be written")
    expected = "BAD_PARAMS" if spelling in ("trailing-dot", "trailing-space") else "PROTECTED_PATH"
    assert exc.value.code == expected
    assert victim.read_bytes() == b"protected original"
    assert list(root.iterdir()) == [victim]


@pytest.mark.skipif(os.name != "nt", reason="Windows path spellings")
def test_extended_protected_root_blocks_ordinary_spelling(satk_home, tmp_path, monkeypatch):
    root = tmp_path / "precious"
    monkeypatch.setenv("SATK_SAFETY_PROTECTED_ROOTS", json.dumps(["\\\\?\\" + str(root)]))
    config.reset()
    with pytest.raises(SatkError) as exc:
        P.ensure_writable(root / "new.txt")
    assert exc.value.code == "PROTECTED_PATH"
    assert not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC paths")
@pytest.mark.parametrize("alias", [r"\\?\UNC\satk-test\share\precious\new.txt",
                                  "//?/unc/SATK-TEST/SHARE/precious/new.txt"])
def test_extended_unc_matches_protected_root(satk_home, monkeypatch, alias):
    # Lexical namespace equivalence needs no network share or network access.
    monkeypatch.setattr(os.path, "realpath", lambda p, **kw: os.fspath(p))
    monkeypatch.setenv("SATK_SAFETY_PROTECTED_ROOTS", json.dumps([r"\\satk-test\share\precious"]))
    config.reset()
    with pytest.raises(SatkError) as exc:
        P.ensure_writable(alias)
    assert exc.value.code == "PROTECTED_PATH"


@pytest.mark.skipif(os.name != "nt", reason="Windows device namespaces")
@pytest.mark.parametrize("path", [r"\\?\GLOBALROOT\Device\HarddiskVolume1\anything",
                                 r"\\?\Volume{85e6b49e-00b3-4541-bc5c-899d21c4a6cf}\anything",
                                 r"\\.\GLOBALROOT\Device\HarddiskVolume1\anything",
                                 r"\??\C:\anything"])
def test_unsupported_namespaces_are_rejected(satk_home, path):
    with pytest.raises(SatkError) as exc:
        P.ensure_writable(path)
    assert exc.value.code == "BAD_PARAMS"


def test_junction_to_protected_root_blocks_missing_descendants(satk_home, tmp_path, make_junction):
    root = satk_home / "src"
    root.mkdir()
    alias = make_junction(tmp_path / "alias", root)
    with pytest.raises(SatkError) as exc:
        P.atomic_write(alias / "not-created" / "out.txt", b"no")
    assert exc.value.code == "PROTECTED_PATH"
    assert list(root.iterdir()) == []


def test_junction_to_ancestor_cannot_be_removed(satk_home, tmp_path, make_junction):
    root = satk_home / "src"
    root.mkdir()
    alias = make_junction(tmp_path / "alias", satk_home)
    with pytest.raises(SatkError) as exc:
        P.ensure_removable(alias)
    assert exc.value.code == "PROTECTED_PATH"
    assert root.is_dir()


@pytest.mark.parametrize("suffix", [" ", "..", ". "])
def test_literal_dot_space_junction_is_not_normalized_away(satk_home, tmp_path, make_junction, suffix):
    root = satk_home / "src"
    root.mkdir()
    victim = root / "original.txt"
    victim.write_text("protected", encoding="utf-8")
    # Such names can be created with the extended API and opened as INTERIOR Win32
    # components. Trimming before resolving hides the junction's actual target.
    alias = make_junction(tmp_path / ("literal" + suffix), root, literal=True)
    assert (alias / victim.name).resolve() == victim
    for leaf in (victim.name, "missing/subdir/output.txt"):
        with pytest.raises(SatkError) as exc:
            P.ensure_writable(alias / leaf)
        assert exc.value.code in ("BAD_PARAMS", "PROTECTED_PATH")
    assert victim.read_text(encoding="utf-8") == "protected"


@pytest.mark.skipif(os.name != "nt", reason="Windows short names")
def test_short_name_of_protected_root(satk_home, tmp_path, monkeypatch):
    root = tmp_path / "protected directory with a long name"
    root.mkdir()
    get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    get_short.restype = ctypes.c_uint32
    buf = ctypes.create_unicode_buffer(32768)
    n = get_short(str(root), buf, len(buf))
    assert 0 < n < len(buf), ctypes.get_last_error()
    if "~" not in buf.value:
        pytest.skip("8.3 names are disabled on this volume")
    monkeypatch.setenv("SATK_PATHS_SRC", str(root))
    config.reset()
    with pytest.raises(SatkError) as exc:
        P.ensure_writable(Path(buf.value) / "missing" / "out.txt")
    assert exc.value.code == "PROTECTED_PATH"


def test_unresolvable_path_is_not_assumed_writable(satk_home, monkeypatch):
    target = satk_home / "work" / "alias"
    realpath = os.path.realpath

    def denied(path, **kwargs):
        if os.path.normcase(os.fspath(path)) == os.path.normcase(str(target)):
            raise PermissionError("cannot resolve alias")
        return realpath(path, **kwargs)

    monkeypatch.setattr(os.path, "realpath", denied)
    with pytest.raises(SatkError) as exc:
        P.ensure_writable(target)
    assert exc.value.code == "BAD_PARAMS"


@pytest.mark.skipif(os.name != "nt", reason="Windows extended paths")
def test_extended_path_outside_protected_roots_remains_usable(satk_home):
    target = satk_home / "work" / "out.txt"
    P.atomic_write("\\\\?\\" + str(target), "allowed")
    assert target.read_text(encoding="utf-8") == "allowed"


def test_atomic_writers_never_share_staging_file(satk_home, monkeypatch):
    target = satk_home / "work" / "shared.bin"
    payloads = [bytes([i]) * (128 * 1024) for i in range(8)]
    ready = threading.Barrier(len(payloads))
    writer = threading.local()
    replace = os.replace

    def together(src, dst):
        if not getattr(writer, "ready", False):
            writer.ready = True
            ready.wait(timeout=10)
        assert Path(src).read_bytes() == writer.payload
        replace(src, dst)

    def write(payload):
        writer.payload = payload
        return P.atomic_write(target, payload)

    monkeypatch.setattr(os, "replace", together)
    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        futures = [pool.submit(write, payload) for payload in payloads]
        errors = [f.exception() for f in futures]
    assert errors == [None] * len(payloads)
    assert target.read_bytes() in payloads
    assert list(target.parent.iterdir()) == [target]


@pytest.fixture
def replace_clock(monkeypatch):
    """Advance retry time without making failure-path tests sleep for two seconds."""
    elapsed = 0.0
    sleeps = []

    def sleep(seconds):
        nonlocal elapsed
        assert seconds > 0
        sleeps.append(seconds)
        elapsed += seconds

    monkeypatch.setattr(P, "time", SimpleNamespace(monotonic=lambda: elapsed, sleep=sleep), raising=False)
    return sleeps


@pytest.mark.parametrize("winerror", [None, 5, 32, 33])
def test_atomic_write_retries_transient_replace_errors(satk_home, monkeypatch, replace_clock, winerror):
    target = satk_home / "work" / "out.txt"
    target.write_bytes(b"previous value")
    error = PermissionError(13, "Access is denied") if winerror is None else OSError("destination busy")
    if winerror is not None:
        error.winerror = winerror
    replace = os.replace
    attempts = []

    def transient(src, dst):
        attempts.append(Path(src))
        assert Path(src).read_bytes() == b"new value"
        assert target.read_bytes() == b"previous value"
        if len(attempts) <= 3:
            raise error
        return replace(src, dst)

    monkeypatch.setattr(os, "replace", transient)
    assert P.atomic_write(target, b"new value") == target
    assert target.read_bytes() == b"new value"
    assert len(attempts) == 4 and len(set(attempts)) == 1
    assert len(replace_clock) == 3 and 0 < sum(replace_clock) < 2
    assert list(target.parent.iterdir()) == [target]


def test_atomic_write_retry_budget_preserves_target_and_cleans_staging(satk_home, monkeypatch, replace_clock):
    target = satk_home / "work" / "out.txt"
    target.write_bytes(b"previous value")
    error = PermissionError(13, "still locked")
    attempts = 0

    def locked(*args):
        nonlocal attempts
        attempts += 1
        raise error

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(PermissionError) as exc:
        P.atomic_write(target, b"new value")
    assert exc.value is error
    assert attempts > 1 and 1.9 <= sum(replace_clock) <= 2.1
    assert target.read_bytes() == b"previous value"
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.skipif(os.name != "nt", reason="Windows destination sharing rules")
def test_atomic_write_retries_while_destination_is_open(satk_home, monkeypatch):
    target = satk_home / "work" / "out.txt"
    target.write_bytes(b"previous value")
    contended = threading.Event()
    replace = os.replace

    def observed_replace(src, dst):
        try:
            return replace(src, dst)
        except PermissionError:
            contended.set()
            raise

    monkeypatch.setattr(os, "replace", observed_replace)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with target.open("rb") as reader:
            future = pool.submit(P.atomic_write, target, b"new value")
            assert contended.wait(5), "writer did not reach the locked destination"
            assert reader.read() == b"previous value"
        assert future.result(timeout=5) == target
    assert target.read_bytes() == b"new value"
    assert list(target.parent.iterdir()) == [target]


def test_atomic_write_ignores_preplanted_staging_hardlink(satk_home):
    root = satk_home / "src"
    root.mkdir()
    victim = root / "original.txt"
    victim.write_bytes(b"protected original")
    target = satk_home / "work" / "result.txt"
    planted = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    os.link(victim, planted)
    P.atomic_write(target, b"new output")
    assert target.read_bytes() == b"new output"
    assert victim.read_bytes() == b"protected original"
    assert planted.read_bytes() == b"protected original"
    assert not os.path.samefile(target, victim)


@pytest.mark.parametrize("failure,winerror", [("replace", None), ("replace", 112), ("fsync", None)])
def test_failed_atomic_write_preserves_target_and_removes_staging(satk_home, monkeypatch, replace_clock,
                                                                failure, winerror):
    target = satk_home / "work" / "out.txt"
    target.write_bytes(b"previous value")

    def fail(*args):
        error = OSError("simulated write failure")
        if winerror is not None:
            error.winerror = winerror
        raise error

    monkeypatch.setattr(os, failure, fail)
    with pytest.raises(OSError, match="simulated write failure"):
        P.atomic_write(target, b"new value")
    assert replace_clock == []
    assert target.read_bytes() == b"previous value"
    assert list(target.parent.iterdir()) == [target]
