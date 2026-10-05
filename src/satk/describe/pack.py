r"""Read a gta-scout description pack (``gta-scout-annotations-v1``). Stdlib only.

A pack is one JSON file (``<workspace>/src/gta-scout/data/annotations/sa-YYYY-MM-DD.json``)::

    {"format": "gta-scout-annotations-v1", "version": "sa-2026-10-03", "license": "MIT",
     "scope": "...", "sha256": "<digest of the pack without this key>",
     "entries": [{"id", "game", "kind": "model"|"texture", "selector": {"name", "dff", "txd"},
                  "description", "tags", "confidence", "review_method", "limitations",
                  "image_sha256", "sources": [{"archive", "entry", "bytes", "sha256"}]}]}

``sources[].sha256`` is the SHA-256 of the whole IMG directory entry (``bytes`` = sectors * 2048)
or of the loose file. Only ``kind == "model"`` entries with a ``.dff`` source can be bound by
content (:mod:`satk.describe.bind`); texture entries carry only TXD hashes, which differ from the
stock 1.0 TXDs (report 09, section 3.6), so they are counted and skipped.

The digest is ``sha256(json.dumps(pack_without_sha256, ensure_ascii=False, sort_keys=True,
separators=(",", ":")))`` — the same canonical form gta-scout uses (MIT, Dryxio).

Example::

    from satk.describe.pack import find_pack, load_pack, english
    pack = load_pack(find_pack(None))          # newest sa-*.json of <paths.src>/gta-scout
    pack.version, len(pack.models)             # ('sa-2026-10-03', 2291)
    english(pack.models[0].description)        # ('Large rectangular blue-grey billboard ...', 'en')
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from satk.core.errors import SatkError
from satk.core.paths import cfg, jpath

__all__ = [
    "FORMAT",
    "PACK_GLOB",
    "AUTHOR",
    "MAX_TEXT",
    "ModelEntry",
    "Pack",
    "default_pack_dir",
    "find_pack",
    "load_pack",
    "canonical",
    "digest",
    "english",
    "note_text",
    "created_at",
]

FORMAT = "gta-scout-annotations-v1"
PACK_GLOB = "sa-*.json"
#: ``note.author`` of every imported description (the notes schema names this value).
AUTHOR = "import:gta-scout"
#: Same limit as ``satk.notes.db.MAX_TEXT`` (kept here so this module needs no notes import).
MAX_TEXT = 4000
_SHA = re.compile(r"^[0-9a-f]{64}$")
_EN = re.compile(r"(?:^|(?<=[\s.;:!?]))EN:\s*")
_FR_MARK = re.compile(r"(?:^|(?<=[\s.;:!?]))FR:\s*")
#: French function words; two or more distinct ones mean the text still contains French.
_FR_WORDS = re.compile(r"\b(le|la|les|des|du|une|avec|sur|dans|pour|et)\b", re.I)
_VERSION_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_SOURCE_URL = "https://github.com/Dryxio/gta-scout"


@dataclass(frozen=True)
class ModelEntry:
    """One model entry of a pack with one ``.dff`` source binding."""

    id: str  #: pack entry id (64 hex)
    name: str  #: selector name (IDE model name at the publisher)
    dff: str  #: selector DFF stem
    txd: str | None
    description: str  #: original text (FR/EN at the publisher)
    tags: tuple[str, ...]
    confidence: float | None
    method: str  #: review_method, e.g. ``model-visual``
    dff_archive: str | None  #: ``gta3.img`` / ``gta_int.img`` / ``SAMP.img``; ``None`` = loose file
    dff_entry: str  #: ``8screen01.dff``
    dff_bytes: int  #: IMG directory size (sectors * 2048) or file size
    dff_sha256: str

    @property
    def dff_ref(self) -> str:
        """``gta3.img/8screen01.dff`` (as published; for evidence and reports)."""
        return f"{self.dff_archive}/{self.dff_entry}" if self.dff_archive else self.dff_entry


@dataclass
class Pack:
    """A loaded pack: header and the model entries that have a DFF binding."""

    path: Path
    version: str
    license: str
    sha256: str | None
    digest_ok: bool | None  #: ``None`` = not checked (``verify=False`` or no digest in the file)
    entries: int  #: all entries in the file
    models: list[ModelEntry]
    skipped: collections.Counter = field(default_factory=collections.Counter)  #: reason -> entries
    warn: list[str] = field(default_factory=list)

    @property
    def source(self) -> str:
        """Short provenance string for evidence: ``gta-scout:sa-2026-10-03``."""
        return f"gta-scout:{self.version}"

    def summary(self) -> dict:
        """Header for envelopes (no entries)."""
        d = {"file": jpath(self.path), "version": self.version, "license": self.license,
             "entries": self.entries, "models_with_dff": len({m.id for m in self.models}),
             "digest": None if self.digest_ok is None else ("ok" if self.digest_ok else "mismatch"),
             "source": _SOURCE_URL}
        if self.skipped:
            d["skipped"] = dict(sorted(self.skipped.items()))
        return {k: v for k, v in d.items() if v is not None}


# --------------------------------------------------------------------------- locating


def default_pack_dir() -> Path:
    """``<paths.src>/gta-scout/data/annotations`` (not checked)."""
    return Path(cfg().paths.src) / "gta-scout" / "data" / "annotations"


def _newest(d: Path) -> Path | None:
    cands = sorted(p for p in d.glob(PACK_GLOB) if p.is_file())
    return cands[-1] if cands else None


def find_pack(pack: str | Path | None) -> Path:
    """The pack file to read: ``pack`` itself, the newest ``sa-*.json`` in directory ``pack``, or in
    :func:`default_pack_dir` when ``pack`` is ``None``. ``NOT_FOUND`` with a hint otherwise."""
    hint = (f"clone {_SOURCE_URL} into <workspace>/src/gta-scout (read-only reference) "
            "or pass --pack <file.json>")
    p = default_pack_dir() if pack in (None, "") else Path(str(pack))
    if p.is_dir():
        f = _newest(p)
        if f is None:
            raise SatkError("NOT_FOUND", f"no {PACK_GLOB} pack in {jpath(p)}", hint=hint)
        return f
    if p.is_file():
        return p
    raise SatkError("NOT_FOUND", f"gta-scout pack not found: {jpath(p)}", hint=hint)


# --------------------------------------------------------------------------- reading


def canonical(value) -> str:
    """Canonical JSON used by gta-scout digests."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    """SHA-256 hex of :func:`canonical`."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _str(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _confidence(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
        return None
    return float(v)


def _dff_sources(sources) -> list[tuple[str | None, str, int, str]]:
    out = []
    for s in sources if isinstance(sources, list) else ():
        if not isinstance(s, dict):
            continue
        entry = _str(s.get("entry"))
        sha = s.get("sha256")
        size = s.get("bytes")
        arch = s.get("archive")
        if not entry or not entry.lower().endswith(".dff") or "/" in entry or "\\" in entry:
            continue
        if not isinstance(sha, str) or not _SHA.match(sha):
            continue
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            continue
        if arch is not None and (not isinstance(arch, str) or not arch.strip() or "/" in arch or "\\" in arch):
            continue
        out.append((arch.strip() if isinstance(arch, str) else None, entry, size, sha))
    return out


def _model_entries(e: dict, skipped: collections.Counter) -> list[ModelEntry]:
    if e.get("game") != "sa":
        skipped["other_game"] += 1
        return []
    kind = e.get("kind")
    if kind != "model":
        skipped["texture" if kind == "texture" else "other_kind"] += 1
        return []
    sel = e.get("selector") if isinstance(e.get("selector"), dict) else {}
    name = _str(sel.get("name"))
    desc = _str(e.get("description"))
    eid = e.get("id")
    if not name or not desc or len(desc) > MAX_TEXT or not isinstance(eid, str) or not _SHA.match(eid):
        skipped["invalid"] += 1
        return []
    dffs = _dff_sources(e.get("sources"))
    if not dffs:
        skipped["no_dff_source"] += 1
        return []
    tags = tuple(t.strip() for t in e.get("tags") or () if isinstance(t, str) and t.strip()) \
        if isinstance(e.get("tags"), list) else ()
    return [ModelEntry(id=eid, name=name, dff=_str(sel.get("dff")) or name, txd=_str(sel.get("txd")),
                       description=desc, tags=tags, confidence=_confidence(e.get("confidence")),
                       method=_str(e.get("review_method")) or "?", dff_archive=arch, dff_entry=entry,
                       dff_bytes=size, dff_sha256=sha)
            for arch, entry, size, sha in dffs]


def load_pack(path: str | Path, *, verify: bool = True) -> Pack:
    """Read and check a pack.

    ``UNSUPPORTED`` for another format or licence, or (with ``verify``) a digest that does not
    match the content (a locally edited pack: pass ``verify=False``). Malformed entries are
    skipped and counted in :attr:`Pack.skipped` (``invalid``), never fatal.
    """
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"no such pack: {jpath(p)}") from None
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise SatkError("UNSUPPORTED", f"{jpath(p)} is not a JSON pack: {e}") from None
    if not isinstance(raw, dict) or raw.get("format") != FORMAT or not isinstance(raw.get("entries"), list):
        got = raw.get("format") if isinstance(raw, dict) else type(raw).__name__
        raise SatkError("UNSUPPORTED", f"{jpath(p)}: format {got!r}, expected {FORMAT!r}")
    lic = raw.get("license")
    if lic != "MIT":
        raise SatkError("UNSUPPORTED", f"{jpath(p)}: licence {lic!r}; only MIT packs are imported")
    version = _str(raw.get("version")) or p.stem
    sha = raw.get("sha256") if isinstance(raw.get("sha256"), str) else None
    warn: list[str] = []
    digest_ok: bool | None = None
    if verify and sha:
        digest_ok = digest({k: v for k, v in raw.items() if k != "sha256"}) == sha
        if not digest_ok:
            raise SatkError("UNSUPPORTED", f"{jpath(p)}: pack digest does not match its content",
                            hint="the pack was edited locally; pass --no-verify to import it anyway")
    elif verify:
        warn.append("PACK_DIGEST: the pack has no sha256; content not verified")
    skipped: collections.Counter = collections.Counter()
    models: list[ModelEntry] = []
    for e in raw["entries"]:
        if not isinstance(e, dict):
            skipped["invalid"] += 1
            continue
        models += _model_entries(e, skipped)
    return Pack(path=p, version=version, license=lic, sha256=sha, digest_ok=digest_ok,
                entries=len(raw["entries"]), models=models, skipped=skipped, warn=warn)


# --------------------------------------------------------------------------- text


def english(description: str) -> tuple[str, str]:
    """English part of a publisher description and its language code.

    ``"FR: ... EN: ..."`` gives the text after the last ``EN:`` (``"en"``); other texts are kept
    whole: ``"en"`` when no French is detected, ``"mul"`` (several languages) otherwise.
    """
    d = " ".join(str(description).split())
    m = None
    for m in _EN.finditer(d):
        pass
    if m is not None and d[m.end():].strip():
        return d[m.end():].strip(), "en"
    return d, _lang_of(d)


def _lang_of(text: str) -> str:
    if _FR_MARK.search(text) or len({w.lower() for w in _FR_WORDS.findall(text)}) >= 2:
        return "mul"
    return "en"


def note_text(description: str, mode: str = "en") -> tuple[str, str]:
    """``(text, lang)`` stored in the note: ``mode="en"`` -> :func:`english`; ``"full"`` -> the
    original text (whitespace collapsed)."""
    if mode == "full":
        d = " ".join(str(description).split())
        return d, _lang_of(d)
    if mode != "en":
        raise SatkError("BAD_PARAMS", f"text mode must be 'en' or 'full', got {mode!r}")
    return english(description)


def created_at(version: str) -> str | None:
    """Deterministic note timestamp from a pack version (``sa-2026-10-03`` -> ``2026-10-03T00:00:00Z``)."""
    m = _VERSION_DATE.search(version or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00:00Z" if m else None
