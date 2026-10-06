"""satk.game.clone: new directory only, read-only source, hash-checked copy (SPEC §4.1, card WP-01 p.8).

Destinations live under ``paths.work`` of an isolated workspace (fixture ``work``): game.* write
targets outside ``work/`` are refused (``satk.game.guard``, tests in ``test_guard.py``).
"""

from __future__ import annotations

import builtins
import hashlib
import os
import shutil
import threading
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.game import clone as C
from satk.game import exe as X
from satk.game import manifests as M
from satk.game.clone import clone, plan


def test_dry_run_writes_nothing(fake_game, work, tmp_path, tree_snapshot):
    dst = work / "out" / "clone"
    before = tree_snapshot(tmp_path)
    r = clone(fake_game.root, dst, fake_game.stock, dry_run=True)
    assert r["dry_run"] is True
    assert r["files"] == fake_game.stock_count and r["bytes"] == fake_game.stock.total_bytes
    assert r["skip_nonstock"] == fake_game.nonstock_count
    assert r["restore"] == ["gta_sa.exe", "vorbisFile.dll"]
    assert not (work / "out").exists()
    assert tree_snapshot(tmp_path) == before


def test_clone_produces_verified_copy(fake_game, work, tree_snapshot):
    src_before = tree_snapshot(fake_game.root)
    dst = work / "clean"
    r = clone(fake_game.root, dst, fake_game.stock, jobs=3)
    assert r["dst"].endswith("/clean") and r["files"] == fake_game.stock_count
    assert tree_snapshot(fake_game.root) == src_before  # the source is untouched
    assert not dst.with_name("clean.partial").exists()
    got = tree_snapshot(dst)
    assert set(got) == {e.path for e in fake_game.stock} | {M.CLEAN_MANIFEST_NAME}
    for e in fake_game.stock:
        size, mtime_ns, sha = got[e.path]
        assert (size, sha) == (e.size, e.sha256)
        if e.mtime is not None:
            assert mtime_ns // 1_000_000_000 == e.mtime  # preserved -> the fast verify works
    assert (dst / "gta_sa.exe").read_bytes() == fake_game.stock_exe
    assert (dst / "vorbisFile.dll").read_bytes() == (fake_game.root / "vorbisHooked.dll").read_bytes()
    listed = M.read_sha256_file(dst / M.CLEAN_MANIFEST_NAME)
    assert {k: sha for k, (_, sha) in listed.items()} == {k: e.sha256 for k, e in fake_game.stock.entries.items()}


def test_clone_mta_exe(fake_game, work):
    dst = work / "clean-mta"
    r = clone(fake_game.root, dst, fake_game.stock, exe_variant="mta")
    assert r["exe"] == "mta-canonical"
    assert (dst / "gta_sa.exe").read_bytes() == fake_game.mta_exe
    from satk.game.verify import check_clean_manifest, check_tree

    assert check_tree(dst, fake_game.stock).problems == []  # mta exe is an accepted alternative
    assert check_clean_manifest(dst, fake_game.stock)[0] == "ok"


@pytest.mark.parametrize("first_exe", ["stock", "mta"])
def test_clone_from_a_clean_copy(fake_game, work, first_exe, tree_snapshot, monkeypatch):
    """A verified copy (no vorbisHooked.dll, stock or MTA exe) reproduces an identical copy."""
    from satk.game.verify import check_tree

    # exe.to_stock recognizes the MTA image by its (real) hash; teach it the synthetic one
    monkeypatch.setattr(X, "MTA_SHA256", X.sha256(fake_game.mta_exe))
    c1 = work / "c1"
    clone(fake_game.root, c1, fake_game.stock, exe_variant=first_exe)
    assert not (c1 / "vorbisHooked.dll").exists()
    before = tree_snapshot(c1)
    p = plan(c1, work / "c2", fake_game.stock)
    assert p["files"] == fake_game.stock_count and p["skip_nonstock"] == 1  # MANIFEST.sha256
    r = clone(c1, work / "c2", fake_game.stock)
    assert r["exe"] == "hoodlum-stock"
    assert tree_snapshot(c1) == before
    c2 = work / "c2"
    assert check_tree(c2, fake_game.stock).problems == []
    assert (c2 / "gta_sa.exe").read_bytes() == fake_game.stock_exe
    assert (c2 / "vorbisFile.dll").read_bytes() == (fake_game.root / "vorbisHooked.dll").read_bytes()
    if first_exe == "stock":
        assert (c2 / M.CLEAN_MANIFEST_NAME).read_bytes() == (c1 / M.CLEAN_MANIFEST_NAME).read_bytes()


def test_install_vorbisfile_is_never_taken_from_the_asi_loader(fake_game, work):
    """In the install vorbisFile.dll is the ASI loader: without vorbisHooked.dll it is missing."""
    (fake_game.root / "vorbisHooked.dll").unlink()
    with pytest.raises(SatkError) as ei:
        plan(fake_game.root, work / "c", fake_game.stock)
    assert ei.value.code == "NOT_FOUND" and ei.value.data["rows"] == [["vorbisHooked.dll", "missing", 655, None]]


def test_source_opened_read_binary_only(fake_game, work, monkeypatch):
    """Card WP-01 p.8: the source is opened only with "rb" (builtins.open is patched)."""
    real_open = builtins.open
    src_root = os.path.normcase(os.path.abspath(fake_game.root))
    seen: list[tuple[str, str]] = []

    def spy(file, mode="r", *a, **kw):
        if isinstance(file, (str, os.PathLike)):
            p = os.path.normcase(os.path.abspath(os.fspath(file)))
            if p.startswith(src_root + os.sep):
                seen.append((p, mode))
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    clone(fake_game.root, work / "c", fake_game.stock, jobs=2)
    assert len(seen) >= fake_game.stock_count
    assert {m for _, m in seen} == {"rb"}


def test_exists_for_non_empty_destination(fake_game, work):
    dst = work / "busy"
    dst.mkdir()
    (dst / "x.txt").write_text("x", encoding="utf-8")
    for dry in (True, False):
        with pytest.raises(SatkError) as ei:
            clone(fake_game.root, dst, fake_game.stock, dry_run=dry)
        assert ei.value.code == "EXISTS"
    f = work / "file"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(SatkError) as ei:
        plan(fake_game.root, f, fake_game.stock)
    assert ei.value.code == "EXISTS"


def test_empty_destination_is_fine(fake_game, work):
    dst = work / "empty"
    dst.mkdir()
    clone(fake_game.root, dst, fake_game.stock)
    assert (dst / M.CLEAN_MANIFEST_NAME).is_file()


def test_stale_partial_is_exists(fake_game, work):
    (work / "c.partial").mkdir()
    with pytest.raises(SatkError) as ei:
        plan(fake_game.root, work / "c", fake_game.stock)
    assert ei.value.code == "EXISTS" and "partial" in ei.value.msg


def test_destination_inside_source_is_protected(fake_game, work):
    src = work / "install"  # a source under work/: only the overlap check can refuse
    shutil.copytree(fake_game.root, src)
    for dst in (src / "clone", src / "models" / "x"):
        with pytest.raises(SatkError) as ei:
            plan(src, dst, fake_game.stock)
        assert ei.value.code == "PROTECTED_PATH" and "overlaps the source" in ei.value.msg
    with pytest.raises(SatkError) as ei:  # the source inside the destination
        plan(src, work, fake_game.stock)
    assert ei.value.code in ("PROTECTED_PATH", "EXISTS")
    with pytest.raises(SatkError) as ei:  # outside work/ the location rule refuses first
        plan(fake_game.root, fake_game.root / "clone", fake_game.stock)


@pytest.mark.parametrize("overlap", ["destination", "source", "staging"])
def test_junction_overlap_is_rejected_before_creating_files(fake_game, tmp_path, tree_snapshot,
                                                          make_junction, overlap):
    src = fake_game.root
    if overlap == "destination":
        alias = make_junction(tmp_path / "alias", src)
        dst = alias / "copy"
    elif overlap == "source":
        src = make_junction(tmp_path / "alias", src)
        dst = fake_game.root / "copy"
    else:
        dst = tmp_path / "copy"
        make_junction(dst.with_name("copy.partial"), src)
    before = tree_snapshot(fake_game.root)
    with pytest.raises(SatkError) as exc:
        clone(src, dst, fake_game.stock, jobs=1)
    assert exc.value.code == "PROTECTED_PATH"
    assert tree_snapshot(fake_game.root) == before
    assert not dst.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows namespaces")
@pytest.mark.parametrize("extended_source", [True, False])
def test_extended_namespace_cannot_hide_source_overlap(fake_game, tree_snapshot, extended_source):
    src = str(fake_game.root)
    dst = str(fake_game.root / "copy")
    if extended_source:
        src = "\\\\?\\" + src
    else:
        dst = "\\\\?\\" + dst
    before = tree_snapshot(fake_game.root)
    with pytest.raises(SatkError) as exc:
        clone(src, dst, fake_game.stock, jobs=1)
    assert exc.value.code == "PROTECTED_PATH"
    assert tree_snapshot(fake_game.root) == before


@pytest.mark.parametrize("dst", [r"D:\Mods\GTA\src\clone", r"D:\Mods\GTA\src", r"D:\Mods\GTA\GTA San Andreas\clone"])
def test_destination_inside_protected_roots(fake_game, dst):
    with pytest.raises(SatkError) as ei:
        plan(fake_game.root, Path(dst), fake_game.stock)
    assert ei.value.code == "PROTECTED_PATH"


@pytest.mark.parametrize("overlap", ["destination", "source", "staging"])
def test_junction_overlap_is_rejected_before_creating_files(fake_game, work, junction, tree_snapshot, overlap):
    """Overlaps hidden behind junctions (source under work/, so the overlap check itself must refuse)."""
    src = work / "install"
    shutil.copytree(fake_game.root, src)
    before = tree_snapshot(src)
    if overlap == "destination":
        dst = junction(work / "alias", src) / "copy"
    elif overlap == "source":
        dst = src / "copy"
        src = junction(work / "alias", src)
    else:
        dst = work / "copy"
        junction(work / "copy.partial", src)
    with pytest.raises(SatkError) as ei:
        clone(src, dst, fake_game.stock, jobs=1)
    assert ei.value.code == "PROTECTED_PATH" and "overlaps the source" in ei.value.msg
    assert tree_snapshot(work / "install") == before
    assert not (work / "copy").exists() or overlap == "staging"


@pytest.mark.parametrize("extended", ["source", "destination"])
def test_extended_namespace_cannot_hide_source_overlap(fake_game, work, tree_snapshot, extended):
    src = work / "install"
    shutil.copytree(fake_game.root, src)
    s, d = str(src), str(src / "copy")
    if extended == "source":
        s = "\\\\?\\" + s
    else:
        d = "\\\\?\\" + d
    before = tree_snapshot(src)
    with pytest.raises(SatkError) as ei:
        clone(s, d, fake_game.stock, jobs=1)
    assert ei.value.code == "PROTECTED_PATH" and "overlaps the source" in ei.value.msg
    assert tree_snapshot(src) == before


@pytest.mark.parametrize("dst", [r"D:\Mods\GTA\src\clone", r"D:\Mods\GTA\src", r"D:\Mods\GTA\GTA San Andreas\clone",
                                 r"\\?\D:\Mods\GTA\GTA San Andreas\nested", r"\\?\D:\Mods\GTA\src\nested",
                                 r"\\localhost\D$\Mods\GTA\GTA San Andreas\nested", r"//?/D:/Mods/GTA/GTA San Andreas/x",
                                 r"\\.\D:\Mods\GTA\GTA San Andreas\nested", r"D:\Mods\GTA\gta-sa-clean\nested",
                                 r"\\?\D:\Mods\GTA\gta-sa-clean\nested"])
def test_destination_inside_protected_roots(fake_game, dst):
    """Real roots, also behind extended-length/device/UNC spellings (verifier repro); writes nothing."""
    with pytest.raises(SatkError) as ei:
        plan(fake_game.root, Path(dst) if not dst.startswith(("\\\\", "//")) else dst, fake_game.stock)
    assert ei.value.code == "PROTECTED_PATH", ei.value.msg


def test_destination_inside_configured_src(fake_game, satk_home):
    with pytest.raises(SatkError) as ei:
        plan(fake_game.root, satk_home / "src" / "gta", fake_game.stock)
    assert ei.value.code == "PROTECTED_PATH"


def test_clean_copy_path_may_be_recreated_when_absent(fake_game, satk_home):
    """The configured clean-copy root itself may be (re)created by clone (it is absent here)."""
    target = satk_home / "gta-sa-clean"
    r = clone(fake_game.root, target, fake_game.stock)
    assert Path(r["dst"]) == target and (target / M.CLEAN_MANIFEST_NAME).is_file()


def test_destination_nested_in_clean_copy_is_protected(fake_game, satk_home, tree_snapshot):
    """Only the clean copy root itself, never a directory inside it (verifier repro gta-sa-clean/nested)."""
    game = satk_home / "gta-sa-clean"
    game.mkdir()
    for dst in (game / "nested", game / "models" / "deep", "\\\\?\\" + str(game / "nested")):
        with pytest.raises(SatkError) as ei:
            plan(fake_game.root, dst, fake_game.stock)
        assert ei.value.code == "PROTECTED_PATH", dst
    assert list(game.iterdir()) == []
    assert plan(fake_game.root, game, fake_game.stock)["dst"] == str(game).replace("\\", "/")  # empty root: ok


def test_corrupt_source_aborts_and_cleans_up(fake_game, work):
    p = M.to_path(fake_game.root, "data/maps/la/lae2.ide")
    p.write_bytes(b"Y" * p.stat().st_size)  # same size, other content
    dst = work / "c"
    with pytest.raises(SatkError) as ei:
        clone(fake_game.root, dst, fake_game.stock)
    assert ei.value.code == "REVISION"
    assert not dst.exists() and not dst.with_name("c.partial").exists()


def _many_files(root: Path, n: int, size, bad: int | None = None) -> M.Manifest:
    entries = []
    for i in range(n):
        rel = f"data/f{i:02d}.dat"
        p = M.to_path(root, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = bytes([i]) * size(i)
        p.write_bytes(data)
        sha = "0" * 64 if i == bad else hashlib.sha256(data).hexdigest()
        entries.append(M.Entry(rel, len(data), sha))
    return M.Manifest.from_entries("t", entries)


def test_first_failure_cancels_queued_copies(work, monkeypatch):
    """Verifier probe_abort: the largest file has a wrong hash -> no other file is copied after it."""
    src = work / "src40"
    stock = _many_files(src, 40, lambda i: 100_000 - i * 1000, bad=0)
    calls: list[str] = []
    real = C._copy_verified

    def spy(s, d, expected, tick):
        calls.append(d.name)
        return real(s, d, expected, tick)

    monkeypatch.setattr(C, "_copy_verified", spy)
    with pytest.raises(SatkError) as ei:
        clone(src, work / "dst", stock, jobs=1)
    assert ei.value.code == "REVISION"
    assert calls == ["f00.dat"], calls  # was 40 of 40
    assert not (work / "dst").exists() and not (work / "dst.partial").exists()


def test_failure_stops_running_copies(work, monkeypatch):
    """A copy in progress ends after its current buffer once another one failed."""
    monkeypatch.setattr(C, "BUFSIZE", 4096)
    src = work / "src2"
    big = 16 << 20
    stock = _many_files(src, 2, lambda i: big if i == 0 else 10_000, bad=1)
    done: list[int] = []

    def progress(n, total, msg):
        done.append(n)
        threading.Event().wait(0.001)  # slow the big copy down: ~4096 buffers

    with pytest.raises(SatkError) as ei:
        clone(src, work / "dst", stock, jobs=2, progress=progress)
    assert ei.value.code == "REVISION"
    assert max(done, default=0) < big // 2, max(done)
    assert not (work / "dst").exists() and not (work / "dst.partial").exists()


def test_interrupt_in_a_copy_stops_and_cleans_up(work):
    """KeyboardInterrupt (as Ctrl+C would raise it) cancels the rest and removes dst.partial."""
    src = work / "src10"
    stock = _many_files(src, 10, lambda i: 50_000)
    calls = []

    def progress(n, total, msg):
        calls.append(n)
        if threading.current_thread().name.startswith("satk-clone") and len(calls) == 3:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        clone(src, work / "dst", stock, jobs=1, progress=progress)
    assert len(calls) <= 4
    assert not (work / "dst").exists() and not (work / "dst.partial").exists()


def test_missing_source_file(fake_game, work):
    M.to_path(fake_game.root, "text/american.gxt").unlink()
    with pytest.raises(SatkError) as ei:
        plan(fake_game.root, work / "c", fake_game.stock)
    assert ei.value.code == "NOT_FOUND" and ei.value.data["rows"][0][:2] == ["text/american.gxt", "missing"]


def test_wrong_exe_aborts(fake_game, work):
    exe = fake_game.root / "gta_sa.exe"
    data = bytearray(exe.read_bytes())
    data[X.PATCH_OFFSET:X.PATCH_OFFSET + 12] = bytes(12)  # neither patched nor original
    exe.write_bytes(bytes(data))
    with pytest.raises(SatkError) as ei:
        clone(fake_game.root, work / "c", fake_game.stock)
    assert ei.value.code == "REVISION" and "gta_sa.exe" in ei.value.msg
    assert not (work / "c").exists()


def test_missing_source_root(fake_game, work):
    with pytest.raises(SatkError) as ei:
        plan(work / "none", work / "c", fake_game.stock)
    assert ei.value.code == "NOT_FOUND"


def test_unknown_exe_variant(fake_game, work):
    with pytest.raises(SatkError) as ei:
        plan(fake_game.root, work / "c", fake_game.stock, exe_variant="compact")
    assert ei.value.code == "BAD_PARAMS"


def _nonstock_count(root: Path, stock) -> int:
    """Files of ``root`` outside the stock manifest, by a walk independent of ``clone`` (see ``_dry_run_nonstock``)."""
    base = os.path.abspath(root)
    return sum(1 for dp, _dns, fns in os.walk(base) for fn in fns
               if M.key(os.path.relpath(os.path.join(dp, fn), base)) not in stock.entries)


def _dry_run_nonstock(root: Path, run, stock) -> dict:
    """``run()``'s report whose ``skip_nonstock`` matches an independent count of the real folder.

    The game folders collect files that other tools write while the suite runs (logs, SA-MP and MTA leftovers, an
    agent's temp file), so no number is hard-coded: the count is taken before and after the dry run, and a changing
    folder gets a few attempts. Stock files and bytes stay exact: the manifest defines them.
    """
    for _ in range(4):
        before = _nonstock_count(root, stock)
        r = run()
        after = _nonstock_count(root, stock)
        if min(before, after) <= r["skip_nonstock"] <= max(before, after):
            return r
    raise AssertionError(f"skip_nonstock {r['skip_nonstock']} never matched a walk of {root} ({before}..{after})")


@pytest.mark.game
def test_real_dry_run(installed_root, real_work, tree_snapshot):
    dst = real_work / "wp-01" / "clone"
    stock = M.load_stock()
    r = _dry_run_nonstock(installed_root, lambda: clone(installed_root, dst, stock, dry_run=True), stock)
    assert (r["files"], r["bytes"]) == (416, 5_029_186_364)
    assert r["skip_nonstock"] > 0  # the install carries SA-MP and mods next to the stock files
    assert r["restore"] == ["gta_sa.exe", "vorbisFile.dll"]
    assert not (real_work / "wp-01").exists()


@pytest.mark.game
def test_real_dry_run_from_clean_copy(clean_root, real_work):
    """Verifier finding: --src gta-sa-clean was NOT_FOUND (vorbisHooked.dll); now a valid source."""
    stock = M.load_stock()
    r = _dry_run_nonstock(clean_root, lambda: plan(clean_root, real_work / "c2", stock), stock)
    assert (r["files"], r["bytes"]) == (416, 5_029_186_364)


@pytest.mark.game
def test_real_clean_copy_is_exists(installed_root, clean_root):
    with pytest.raises(SatkError) as ei:
        plan(installed_root, clean_root, M.load_stock())
    assert ei.value.code == "EXISTS"
