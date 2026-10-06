"""Text encodings of 8-bit GXT/FXT strings. Stdlib only.

``gta`` (default): the code page of the SA fonts. ASCII is ASCII; bytes ``0x80..0xB0`` are the accented
letters the European versions use (``0x96`` = ``ß``, ``0x9E`` = ``é``, ``0xAD`` = ``Ñ``, ...): checked
against the vanilla German, Spanish, French and Italian ``.gxt`` files, whose accented words decode to
correct spelling with this table. Bytes ``0xB1..0xFF`` have no letter in the stock fonts; they decode to
private-use characters ``U+E0B1..U+E0FF`` so any file still round-trips byte for byte.

``latin1``: byte = code point (raw bytes, what ``satk.formats.gxt`` returns). ``cp1251``: Windows Cyrillic,
for fan translations that redraw the font in that layout (the game itself has no Cyrillic glyphs).
"""

from __future__ import annotations

__all__ = ["CHARSETS", "decode", "encode", "GTA_TABLE"]

CHARSETS = ("gta", "latin1", "cp1251")
#: ``0x80 + i`` -> letter (SA font layout of the European versions).
GTA_TABLE = "ÀÁÂÄÆÇÈÉÊËÌÍÎÏÒÓÔÖÙÚÛÜßàáâäæçèéêëìíîïòóôöùúûüÑñ¿¡"
_DEC = {0x80 + i: ch for i, ch in enumerate(GTA_TABLE)}
_ENC = {ch: b for b, ch in _DEC.items()}
_PUA = 0xE000
#: Typographic characters the fonts lack -> ASCII stand-ins.
_FOLD = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
         "…": "...", " ": " "}


def decode(raw: bytes, charset: str = "gta") -> str:
    """Bytes of one string (without the terminator) -> text."""
    if charset == "latin1":
        return raw.decode("latin-1")
    if charset == "cp1251":
        return raw.decode("cp1251", errors="replace")
    if charset != "gta":
        raise ValueError(f"unknown charset {charset!r}; expected one of {CHARSETS}")
    if raw.isascii():
        return raw.decode("ascii")
    return "".join(chr(b) if b < 0x80 else (_DEC.get(b) or chr(_PUA + b)) for b in raw)


def encode(text: str, charset: str = "gta", folded: list[str] | None = None) -> bytes:
    """Text -> bytes. ``ValueError`` names the first character the charset cannot store.

    Args:
        folded: if given, typographic characters (curly quotes, dashes, ellipsis) are replaced by ASCII
            and the replaced characters are appended here; otherwise they are errors too.
    """
    if "\0" in text:
        raise ValueError("text contains a NUL character")
    if charset in ("latin1", "cp1251"):
        codec = "latin-1" if charset == "latin1" else "cp1251"
        try:
            return text.encode(codec)
        except UnicodeEncodeError as e:
            if folded is not None:
                t2 = "".join(_FOLD.get(c, c) for c in text)
                if t2 != text:
                    folded += sorted({c for c in text if c in _FOLD})
                    return encode(t2, charset)
            raise ValueError(f"character {text[e.start]!r} (U+{ord(text[e.start]):04X}) is not in {charset}") from None
    if charset != "gta":
        raise ValueError(f"unknown charset {charset!r}; expected one of {CHARSETS}")
    if text.isascii():
        return text.encode("ascii")
    out = bytearray()
    for ch in text:
        o = ord(ch)
        if o < 0x80:
            out.append(o)
        elif ch in _ENC:
            out.append(_ENC[ch])
        elif _PUA + 0xB1 <= o <= _PUA + 0xFF:
            out.append(o - _PUA)
        elif folded is not None and ch in _FOLD:
            if ch not in folded:
                folded.append(ch)
            out += _FOLD[ch].encode("ascii")
        else:
            raise ValueError(f"character {ch!r} (U+{o:04X}) has no glyph in the SA fonts")
    return bytes(out)
