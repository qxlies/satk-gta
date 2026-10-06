"""Offline SAAP/1 proof: build the host-side C++ server (no game) and run conformance over TCP.

Marked ``engine`` because it needs the VS C++ build tools. It compiles ``test_server.exe`` from
the native project and runs the real SAAP conformance cases against it through a TCP socket, so
the ASI's transport, framing, auth, envelope and JSON are exercised without gta_sa.exe.
"""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.spbridge import ops as O


def _have_tools() -> bool:
    try:
        O._vcvars()
        return True
    except SatkError:
        return False


@pytest.mark.engine
def test_cpp_server_passes_conformance(tmp_path, monkeypatch):
    if not _have_tools():
        pytest.skip("VS C++ build tools not configured ([paths].vcvars)")
    # isolate the work dir so conformance capture files land under tmp, not the shared work
    monkeypatch.setenv("SATK_PATHS_WORK", str(tmp_path / "work"))
    from satk.core import config as _config

    _config.reset()
    try:
        r = O.sp_selftest()
    except SatkError as e:
        if e.code == "CHECK_FAILED":
            pytest.fail(f"conformance failures: {e.data}")
        raise
    finally:
        _config.reset()
    assert r["ok"] and r["fail"] == 0
    assert r["pass"] >= 20, f"too few conformance cases ran ({r['pass']})"
    # the mock server advertises these; they must have been exercised, not all skipped
    assert r["percent"] == 100.0


@pytest.mark.engine
def test_asi_builds_x86(tmp_path):
    if not _have_tools():
        pytest.skip("VS C++ build tools not configured ([paths].vcvars)")
    r = O._compile("asi", tmp_path / "asi")
    assert r["status"] == "ok", f"ASI build failed: {r['detail']}"
    out = tmp_path / "asi" / "satk_sp.asi"
    assert out.is_file() and out.stat().st_size > 0
    # a 32-bit PE: DOS header 'MZ', PE machine = 0x14c (I386)
    data = out.read_bytes()
    assert data[:2] == b"MZ"
    import struct

    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe_off:pe_off + 4] == b"PE\x00\x00"
    machine = struct.unpack_from("<H", data, pe_off + 4)[0]
    assert machine == 0x14C, f"ASI must be x86 (machine 0x{machine:X})"
