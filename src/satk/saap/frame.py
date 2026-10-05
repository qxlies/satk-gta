"""SAAP/1 framing: ``u32`` big-endian length + UTF-8 JSON object (SPEC §4.8.1).

The same ``!I`` prefix is used by Ariane's ``ARIANE_IPC/1`` (whose payload is text lines);
:func:`read_frame`/:func:`write_frame` therefore work on bytes, :func:`read_json`/
:func:`write_json` add the JSON layer. Everything here is stdlib only.

Example::

    write_json(sock, {"saap": 1, "id": "r1", "method": "ping"})
    reply = read_json(sock, MAX_RESPONSE)
"""

from __future__ import annotations

import hmac
import json
import math
import socket
import struct
from typing import Any

from ..core.errors import SatkError

__all__ = [
    "MAX_REQUEST",
    "MAX_RESPONSE",
    "HEADER",
    "FrameError",
    "encode",
    "encode_json",
    "decode_json",
    "recv_exact",
    "read_frame",
    "write_frame",
    "read_json",
    "write_json",
    "token_ok",
    "token_equal",
]

#: Request payload limit (1 MiB).
MAX_REQUEST = 1024 * 1024
#: Response payload limit (8 MiB).
MAX_RESPONSE = 8 * 1024 * 1024
# Bound parser work independently of Python's recursion and integer-conversion settings.
MAX_JSON_DEPTH = 64
MAX_INTEGER_DIGITS = 128
HEADER = struct.Struct("!I")


class FrameError(SatkError):
    """A framing/transport failure (code ``PROTOCOL``, or ``TIMEOUT`` for socket timeouts).

    ``closed`` is true when the peer closed the connection cleanly before a frame started.
    """

    def __init__(self, msg: str, *, code: str = "PROTOCOL", closed: bool = False, length: int | None = None):
        super().__init__(code, msg, data={"length": length} if length is not None else None)
        self.closed = closed
        self.length = length


def encode(payload: bytes, limit: int = MAX_RESPONSE) -> bytes:
    """Header + payload; ``PROTOCOL`` when empty or above ``limit``."""
    n = len(payload)
    if n == 0:
        raise FrameError("empty frame", length=0)
    if n > limit:
        raise FrameError(f"frame of {n} bytes exceeds the limit of {limit}", length=n)
    return HEADER.pack(n) + payload


def encode_json(obj: Any, limit: int = MAX_RESPONSE) -> bytes:
    """Frame for a JSON object (compact UTF-8, NaN rejected)."""
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return encode(data, limit)


def _check_depth(text: str) -> None:
    depth = 0
    quoted = escaped = False
    for ch in text:
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError(f"nesting exceeds {MAX_JSON_DEPTH} levels")
        elif ch in "]}":
            depth -= 1


def _parse_int(text: str) -> int:
    if len(text.lstrip("-")) > MAX_INTEGER_DIGITS:
        raise ValueError(f"integer exceeds {MAX_INTEGER_DIGITS} digits")
    return int(text)


def _parse_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite numbers are not supported")
    return value


def _reject_constant(text: str) -> None:
    raise ValueError(f"{text} is not a JSON number")


def decode_json(payload: bytes) -> dict:
    """Parse one UTF-8 JSON object with finite numbers, bounded depth and integer size.

    Conversion failures and lone Unicode surrogates are ``PROTOCOL`` errors, including
    strings that would otherwise fail later when echoed in a response (e.g. request IDs).
    """
    try:
        text = payload.decode("utf-8")
        _check_depth(text)
        obj = json.loads(text, parse_int=_parse_int, parse_float=_parse_float, parse_constant=_reject_constant)
        pending = [obj]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                value.encode("utf-8")
            elif isinstance(value, dict):
                pending.extend(value)
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    except UnicodeError as e:
        raise FrameError(f"payload contains invalid UTF-8 text: {e}") from None
    except json.JSONDecodeError as e:
        raise FrameError(f"payload is not JSON: {e.msg} at {e.pos}") from None
    except (ValueError, RecursionError, OverflowError) as e:
        raise FrameError(f"invalid JSON payload: {e}") from None
    if not isinstance(obj, dict):
        raise FrameError("payload is not a JSON object")
    return obj


def recv_exact(sock: socket.socket, n: int, *, at_start: bool = False) -> bytes:
    """Read exactly ``n`` bytes; ``PROTOCOL`` on EOF (``closed=True`` if nothing was read)."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(min(n - len(buf), 1 << 20))
        except socket.timeout:
            raise FrameError("timed out waiting for data", code="TIMEOUT") from None
        except OSError as e:
            raise FrameError(f"connection error: {e}", closed=at_start and not buf) from None
        if not chunk:
            raise FrameError("connection closed by peer" if buf or not at_start else "connection closed",
                             closed=at_start and not buf)
        buf += chunk
    return bytes(buf)


def read_frame(sock: socket.socket, limit: int) -> bytes:
    """Read one frame payload. Above ``limit`` → ``FrameError`` (payload not read; close the socket)."""
    (n,) = HEADER.unpack(recv_exact(sock, HEADER.size, at_start=True))
    if n == 0:
        raise FrameError("empty frame", length=0)
    if n > limit:
        raise FrameError(f"frame of {n} bytes exceeds the limit of {limit}", length=n)
    return recv_exact(sock, n)


def write_frame(sock: socket.socket, payload: bytes, limit: int = MAX_RESPONSE) -> None:
    sock.sendall(encode(payload, limit))


def read_json(sock: socket.socket, limit: int) -> dict:
    return decode_json(read_frame(sock, limit))


def write_json(sock: socket.socket, obj: Any, limit: int = MAX_RESPONSE) -> None:
    sock.sendall(encode_json(obj, limit))


def token_ok(token: Any) -> bool:
    """Shape check of a SAAP token: str of 32–256 UTF-8 bytes without CR/LF."""
    if not isinstance(token, str):
        return False
    try:
        b = token.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return 32 <= len(b) <= 256 and b"\r" not in b and b"\n" not in b


def token_equal(a: str, b: str) -> bool:
    """Constant-time comparison of two tokens."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
