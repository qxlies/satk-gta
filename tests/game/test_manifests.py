"""Manifests and selection rules of the clean copy (SPEC §4.1, §0.2 V1/V2)."""

from __future__ import annotations

import json
import re
import tokenize
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.game import exe as X
from satk.game import manifests as M

GAME_PKG = Path(M.__file__).resolve().parent


# --------------------------------------------------------------------------- V2 regression


def test_x360btns_is_excluded():
    assert "models/x360btns.txd" in M.EXCLUDE_PATHS
    stock = json.loads((M.DATA_DIR / M.STOCK_MANIFEST).read_text(encoding="utf-8"))
    assert "models/x360btns.txd" in stock["rules"]["exclude"]
    for spelling in ("models/x360btns.txd", "MODELS/X360BTNS.TXD", r"models\x360btns.txd", "models\\x360btns.txd"):
        assert M.classify(spelling) == "exclude", spelling
    assert "models/x360btns.txd" not in M.load_stock()
    # the prototype bug turned the literal into "models60btns.txd" (a "\x36" escape)
    assert all("models60btns" not in p and "\\" not in p for p in M.EXCLUDE_PATHS)


_BAD_ESCAPE = re.compile(r"(?<!\\)(?:\\\\)*\\x", re.IGNORECASE)


def _string_tokens(path: Path):
    """(line, prefix, text) of every string literal piece, f-strings included (3.12 tokens)."""
    fprefix: list[str] = []
    with open(path, "rb") as f:
        for tok in tokenize.tokenize(f.readline):
            name = tokenize.tok_name[tok.type]
            if name == "STRING":
                m = re.match(r"^([A-Za-z]*)", tok.string)
                yield tok.start[0], m.group(1).lower(), tok.string
            elif name == "FSTRING_START":
                fprefix.append(re.match(r"^([A-Za-z]*)", tok.string).group(1).lower())
            elif name == "FSTRING_MIDDLE":
                yield tok.start[0], fprefix[-1] if fprefix else "f", tok.string
            elif name == "FSTRING_END" and fprefix:
                fprefix.pop()


def test_no_backslash_x_escapes_in_game_package():
    """SPEC §4.1: string literals with a \\x escape are forbidden in satk.game (bug V2)."""
    bad = []
    for py in sorted(GAME_PKG.glob("*.py")):
        for line, prefix, text in _string_tokens(py):
            if "r" in prefix:
                continue
            if _BAD_ESCAPE.search(text):
                bad.append(f"{py.name}:{line}: {text[:60]}")
    assert bad == []


def test_escape_detector_itself():
    assert _BAD_ESCAPE.search('"models\\x360btns.txd"'.replace("\\\\", "\\"))  # the buggy literal
    assert not _BAD_ESCAPE.search('"models\\\\x360btns.txd"')  # an escaped backslash is fine


# --------------------------------------------------------------------------- paths


@pytest.mark.parametrize("raw,canon", [
    ("models\\gta3.img", "models/gta3.img"),
    ("models/gta3.img", "models/gta3.img"),
    ("./models//gta3.img", "models/gta3.img"),
    ("audio\\CONFIG\\BankLkup.dat", "audio/CONFIG/BankLkup.dat"),
    ("gta_sa.exe", "gta_sa.exe"),
])
def test_norm_rel(raw, canon):
    assert M.norm_rel(raw) == canon
    assert M.key(raw) == canon.casefold()


@pytest.mark.parametrize("bad", ["..\\x.dat", "models/../../x", "C:\\x.dat", "\\x.dat", "/abs/x"])
def test_norm_rel_rejects_escapes(bad):
    with pytest.raises(SatkError) as ei:
        M.norm_rel(bad)
    assert ei.value.code == "BAD_PARAMS"


def test_to_path_uses_components(tmp_path):
    p = M.to_path(tmp_path, "data\\paths\\carrec.img")
    assert p == tmp_path / "data" / "paths" / "carrec.img"


@pytest.mark.parametrize("rel,role", [
    ("models/gta3.img", "stock"),
    ("ReadMe/ReadMe.txt", "stock"),
    ("readme/readme.txt", "stock"),
    ("audio/SFX/GENRL", "stock"),
    ("eax.dll", "stock"),
    ("STREAM.INI", "stock"),
    ("gta_sa.exe", "restore"),
    ("vorbisFile.dll", "restore"),
    ("vorbisHooked.dll", "nonstock"),
    ("data/default.two", "exclude"),
    ("data/maps/x.TWO", "exclude"),
    ("data/colorcycle.dat", "exclude"),
    ("models/ps3btns.txd", "exclude"),
    ("models/sixaxis.txd", "exclude"),
    ("text/languages.ini", "exclude"),
    ("SAMP/SAMP.img", "nonstock"),
    ("cleo.asi", "nonstock"),
    ("modloader/x/y.dff", "nonstock"),
    ("anim", "nonstock"),  # a root file named like a stock directory
])
def test_classify(rel, role):
    assert M.classify(rel) == role


def test_is_fast():
    assert M.is_fast("models/gta3.img") and M.is_fast("data\\paths\\carrec.img") and M.is_fast("audio/SFX/GENRL")
    assert not M.is_fast("data/gta.dat") and not M.is_fast("gta_sa.exe") and not M.is_fast("movies/Logo.mpg")


# --------------------------------------------------------------------------- committed data


#: The install audit describes the maintainers' own install: development checkouts only (data/public-exclude.txt).
needs_install_manifest = pytest.mark.skipif(not (M.DATA_DIR / M.INSTALL_MANIFEST).is_file(),
                                            reason="no install audit manifest in this checkout (not published)")


def test_missing_install_manifest_is_not_found(monkeypatch):
    monkeypatch.setattr(M, "INSTALL_MANIFEST", "install-0000-00-00.json")
    with pytest.raises(SatkError) as ei:
        M.load_install()
    assert ei.value.code == "NOT_FOUND" and "--manifest" in ei.value.hint


@needs_install_manifest
def test_install_manifest_counts():
    inst = M.load_install()
    assert len(inst) == 549
    assert inst.total_bytes == 5_095_934_355
    roles: dict[str, int] = {}
    for e in inst:
        r = M.classify(e.path)
        roles[r] = roles.get(r, 0) + 1
    assert roles["stock"] + roles["restore"] == 416
    assert roles["exclude"] + roles["nonstock"] == 133
    assert inst.get("gta_sa.exe").sha256 == X.LOCAL_SHA256
    assert inst.get("vorbisFile.dll").sha256 == X.VORBISFILE_ASI_SHA256
    assert inst.get("vorbisHooked.dll").sha256 == X.VORBISFILE_STOCK_SHA256


def test_stock_manifest_golden():
    st = M.load_stock()
    assert len(st) == 416
    assert st.total_bytes == 5_029_186_364
    assert st.meta["files"] == 416 and st.meta["bytes"] == 5_029_186_364 and st.meta["skip_nonstock"] == 133
    exe = st.get("gta_sa.exe")
    assert exe.sha256 == X.STOCK_SHA256 and exe.alt == (X.MTA_SHA256,) and exe.via == "exe:stock"
    vf = st.get("vorbisFile.dll")
    assert vf.sha256 == X.VORBISFILE_STOCK_SHA256 and vf.src == "vorbisHooked.dll"
    for rel in M.PROTECT_IMGS:
        assert rel in st and M.is_fast(rel)
    assert sum(1 for e in st if M.is_fast(e.path)) == 40  # 8 IMG + 32 audio files


def test_stock_manifest_rules_match_code():
    st = M.load_stock()
    r = st.meta["rules"]
    assert r["dirs"] == list(M.STOCK_DIRS) and r["root_files"] == list(M.STOCK_ROOT_FILES)
    assert r["exclude"] == list(M.EXCLUDE_GLOBS) + list(M.EXCLUDE_PATHS)
    assert r["protect"] == list(M.PROTECT_IMGS) and len(r["protect"]) == 8
    assert r["fast_check"] == list(M.FAST_GLOBS)


@needs_install_manifest
def test_stock_manifest_is_reproducible():
    """The committed file is exactly what ``python -m satk.game.manifests build`` generates."""
    text = M.dump_stock(M.build_stock(M.load_install(), source=M.INSTALL_MANIFEST))
    assert (M.DATA_DIR / M.STOCK_MANIFEST).read_text(encoding="utf-8") == text
    assert M._main(["build", "--check"]) == 0


def test_build_stock_checks_vorbisfile(fake_game):
    with pytest.raises(SatkError) as ei:
        M.build_stock(fake_game.install, vorbisfile_sha256="0" * 64)
    assert ei.value.code == "REVISION"


def test_fake_install_roles(fake_game):
    assert len(fake_game.stock) == fake_game.stock_count
    assert fake_game.stock.meta["skip_nonstock"] == fake_game.nonstock_count
    assert len(fake_game.install) == fake_game.stock_count + fake_game.nonstock_count


# --------------------------------------------------------------------------- MANIFEST.sha256


def test_sha256_file_roundtrip(tmp_path):
    items = [("models/gta3.img", "a" * 64), ("anim/anim.img", "b" * 64), ("audio/CONFIG/x.dat", "c" * 64),
             ("gta_sa.exe", "d" * 64), ("audio/config/a.dat", "e" * 64)]
    text = M.format_sha256_file(items)
    lines = text.splitlines()
    assert lines[0] == "b" * 64 + "  anim\\anim.img"
    assert lines[1].endswith("audio\\config\\a.dat") and lines[2].endswith("audio\\CONFIG\\x.dat")
    assert text.endswith("\n") and "\r" not in text
    p = tmp_path / "MANIFEST.sha256"
    p.write_text(text, encoding="utf-8")
    back = M.read_sha256_file(p)
    assert back[M.key("models/gta3.img")] == ("models/gta3.img", "a" * 64)
    assert len(back) == 5


def test_read_sha256_file_rejects_garbage(tmp_path):
    p = tmp_path / "m.sha256"
    p.write_text("not a hash line\n", encoding="utf-8")
    with pytest.raises(SatkError):
        M.read_sha256_file(p)


@pytest.mark.game
def test_clean_copy_manifest_file_is_reproduced(clean_root):
    """``format_sha256_file`` of the stock manifest == the MANIFEST.sha256 of gta-sa-clean, byte for byte."""
    st = M.load_stock()
    text = M.format_sha256_file((e.path, e.sha256) for e in st)
    assert (clean_root / M.CLEAN_MANIFEST_NAME).read_bytes() == text.encode("utf-8")


@pytest.mark.game
def test_stock_paths_match_clean_copy_files(clean_root):
    from satk.game.verify import walk_files

    files = {M.key(f) for f in walk_files(clean_root)} - {M.key(M.CLEAN_MANIFEST_NAME)}
    assert files == set(M.load_stock().entries)
