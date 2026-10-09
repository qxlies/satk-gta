"""HTTP client for the image endpoint (chat-completions style, images in ``message.images``).

The API key is read from the environment variable :data:`KEY_ENV` and nowhere else. It is never logged,
echoed, shown in a ``repr``, written to a file or put into an error: every message that leaves this module
passes through :func:`scrub`. Transport is stdlib ``urllib`` (no new dependency); redirects are not followed
(the ``Authorization`` header must never travel to another host).

Failure model (internal codes; :data:`ENVELOPE_CODE` gives the code of the frozen envelope list each one rides on,
the internal code is also in ``error.data.imagegen_code``):

* ``NO_API_KEY``      the variable is absent or empty (raised before any request);
* ``AUTH_FAILED``     HTTP 401/403 (the run must stop);
* ``ENDPOINT_FAILED`` HTTP 429/5xx/network error even after one retry 10 s later, or another HTTP error;
* ``NO_IMAGE``        HTTP 200 without a usable ``images[]`` entry (text-only answer, empty list, non-``data:image/`` URL).
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

from ..core.errors import SatkError

__all__ = ["KEY_ENV", "ENDPOINT_ENV", "DEFAULT_ENDPOINT", "DEFAULT_MODEL", "TIMEOUT_S", "RETRY_DELAY_S",
           "ENVELOPE_CODE", "ASPECTS", "SIZES", "Attempt", "ImageClient", "TransportError", "imagegen_error",
           "key_present", "scrub", "endpoint_url", "endpoint_host", "build_body", "parse_response", "http_post"]

KEY_ENV = "SATK_IMAGEGEN_API_KEY"
ENDPOINT_ENV = "SATK_IMAGEGEN_ENDPOINT"
DEFAULT_ENDPOINT = "https://api.rout.my/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.1-flash-image-preview"
TIMEOUT_S = 120.0
RETRY_DELAY_S = 10.0
MAX_BODY = 64 * 1024 * 1024
ASPECTS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
SIZES = ("0.5K", "1K", "2K", "4K")
SNIPPET = 300
#: Internal imagegen code -> code of the frozen envelope list (core.errors); the internal code is the first word
#: of the message and ``error.data.imagegen_code``.
ENVELOPE_CODE = {"NO_API_KEY": "NOT_READY", "AUTH_FAILED": "AUTH", "ENDPOINT_FAILED": "EXTERNAL_TOOL",
                 "NO_IMAGE": "EXTERNAL_TOOL"}

#: Replaced by tests (``monkeypatch.setattr``): ``(url, headers, body, timeout) -> (status, bytes)``.
TRANSPORT: Callable[..., tuple[int, bytes]] | None = None
#: Replaced by tests: the pause before the second try.
SLEEP: Callable[[float], None] = time.sleep


def key_present() -> bool:
    """True when the key variable exists and is not empty (the value is never returned)."""
    return bool(os.environ.get(KEY_ENV, "").strip())


def scrub(text: Any, *extra_secrets: str) -> Any:
    """Remove the API key (and any ``extra_secrets``) from a string or, recursively, from lists and dicts.

    Also masks ``Bearer <token>`` patterns. Other scalars pass through unchanged.
    """
    if isinstance(text, str):
        out = text
        for s in (os.environ.get(KEY_ENV, "").strip(), *extra_secrets):
            if s and len(s) >= 4:
                out = out.replace(s, "[REDACTED]")
        return re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}", "Bearer [REDACTED]", out)
    if isinstance(text, dict):
        return {(scrub(k, *extra_secrets) if isinstance(k, str) else k): scrub(v, *extra_secrets)
                for k, v in text.items()}
    if isinstance(text, (list, tuple)):
        return [scrub(v, *extra_secrets) for v in text]
    return text


def imagegen_error(code: str, msg: str, *, hint: str | None = None, data: dict | None = None) -> SatkError:
    """A :class:`SatkError` for an imagegen failure; the envelope code comes from :data:`ENVELOPE_CODE`."""
    d: dict[str, Any] = {"imagegen_code": code}
    if data:
        d.update(data)
    return SatkError(ENVELOPE_CODE.get(code, "EXTERNAL_TOOL"), f"{code}: {scrub(msg)}",
                     hint=scrub(hint) if hint else None, data=scrub(d))


def endpoint_url() -> str:
    """The endpoint: ``SATK_IMAGEGEN_ENDPOINT`` or the default; https only (http for loopback hosts)."""
    url = os.environ.get(ENDPOINT_ENV, "").strip() or DEFAULT_ENDPOINT
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    loop = host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")
    if parts.scheme != "https" and not (parts.scheme == "http" and loop):
        raise SatkError("BAD_PARAMS", f"{ENDPOINT_ENV} must be an https URL (http only for localhost), got scheme "
                        f"{parts.scheme!r}", hint=f"unset {ENDPOINT_ENV} to use the default endpoint")
    if not host:
        raise SatkError("BAD_PARAMS", f"{ENDPOINT_ENV} has no host")
    if parts.username or parts.password:
        raise SatkError("BAD_PARAMS", f"{ENDPOINT_ENV} must not carry credentials")
    return url


def endpoint_host(url: str | None = None) -> str:
    """Host (and port) of the endpoint, for provenance."""
    return urlsplit(url or endpoint_url()).netloc


def build_body(prompt: str, model: str, aspect: str, size: str) -> dict:
    """The request body of the design (chat completions with image output)."""
    return {"model": model, "messages": [{"role": "user", "content": prompt}], "modalities": ["image", "text"],
            "image_config": {"aspect_ratio": aspect, "image_size": size}}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


class TransportError(Exception):
    """Network failure (DNS, TLS, timeout, reset); the message carries no request data."""


def http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    """POST ``body``; returns ``(status, response bytes)``. HTTP error statuses are returned, not raised."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            return int(r.status), r.read(MAX_BODY)
    except urllib.error.HTTPError as e:
        try:
            return int(e.code), e.read(65536)
        finally:
            e.close()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise TransportError(f"{type(e).__name__}: {reason}") from None


@dataclass
class Attempt:
    """One candidate: one or two HTTP requests (the second only after 429/5xx/network error)."""

    ok: bool = False
    code: str | None = None            # imagegen code when not ok
    message: str = ""
    http_status: int | None = None
    http_requests: int = 0
    elapsed_s: float = 0.0
    finish_reason: str | None = None
    response_text: str = ""            # first 300 characters of message.content
    image: bytes | None = field(default=None, repr=False)   # decoded image bytes (usually PNG)
    mime: str | None = None
    fatal: bool = False                # AUTH_FAILED: the run must stop
    started_utc: str = ""

    def record(self) -> dict:
        """Provenance fields of this attempt (no image bytes)."""
        d: dict[str, Any] = {"ok": self.ok, "http_requests": self.http_requests,
                             "elapsed_s": round(self.elapsed_s, 2), "started_utc": self.started_utc}
        for k in ("code", "http_status", "finish_reason", "mime"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        if self.message:
            d["message"] = scrub(self.message)
        d["response_text"] = scrub(self.response_text)
        return d


def parse_response(raw: bytes) -> tuple[bytes | None, str | None, str, str]:
    """``(image bytes | None, mime, response text (<= 300 chars), finish_reason)`` of an HTTP 200 body.

    The image is the first ``choices[0].message.images[i].image_url.url`` that is a ``data:image/...;base64,``
    URL with decodable base64.
    """
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, TypeError):
        return None, None, "", ""
    choice = None
    if isinstance(doc, dict) and isinstance(doc.get("choices"), list) and doc["choices"]:
        choice = doc["choices"][0]
    if not isinstance(choice, dict):
        return None, None, "", ""
    finish = choice.get("finish_reason")
    finish_s = str(finish) if finish is not None else ""
    msg = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = msg.get("content")
    if isinstance(content, list):  # content parts: keep the text parts
        content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    text = (content if isinstance(content, str) else "")[:300]
    images = msg.get("images")
    if isinstance(images, list):
        for it in images:
            url = ((it.get("image_url") or {}).get("url")) if isinstance(it, dict) and isinstance(
                it.get("image_url"), dict) else None
            if not isinstance(url, str):
                continue
            m = re.match(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.*)$", url, re.S)
            if not m:
                continue
            try:
                data = base64.b64decode(m.group(2))
            except (binascii.Error, ValueError):
                continue
            if data:
                return data, m.group(1).lower(), text, finish_s
    return None, None, text, finish_s


class ImageClient:
    """Image endpoint client. ``repr``/``str`` never show the key; the object cannot be pickled."""

    __slots__ = ("_key", "endpoint", "timeout", "transport", "sleep", "retry_delay")

    def __init__(self, key: str, endpoint: str | None = None, *, timeout: float = TIMEOUT_S,
                 transport: Callable[..., tuple[int, bytes]] | None = None,
                 sleep: Callable[[float], None] | None = None, retry_delay: float | None = None):
        self._key = key
        self.endpoint = endpoint or endpoint_url()
        self.timeout = float(timeout)
        self.transport = transport
        self.sleep = sleep
        self.retry_delay = RETRY_DELAY_S if retry_delay is None else float(retry_delay)

    @classmethod
    def from_env(cls, **kw) -> "ImageClient":
        """Client with the key of ``SATK_IMAGEGEN_API_KEY``; ``NO_API_KEY`` when it is absent."""
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            raise imagegen_error("NO_API_KEY", f"environment variable {KEY_ENV} is not set",
                                 hint=f"set {KEY_ENV} in the user environment (it is never read from a file or a "
                                      "command line); `satk imagegen placeholder` needs no key")
        return cls(key, **kw)

    def __repr__(self) -> str:
        return f"ImageClient(endpoint={self.endpoint!r}, key=<redacted>)"

    __str__ = __repr__

    def __reduce__(self):  # no pickling or copying of the key
        raise TypeError("ImageClient holds a secret and cannot be pickled")

    @property
    def host(self) -> str:
        return endpoint_host(self.endpoint)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    def generate(self, prompt: str, *, model: str = DEFAULT_MODEL, aspect: str = "1:1", size: str = "1K",
                 request_budget: int = 2) -> Attempt:
        """One candidate. Endpoint trouble is returned, not raised: read ``ok``, ``code`` and ``fatal``.

        ``request_budget`` caps the HTTP requests this call may send (1 = no retry).
        """
        post = self.transport or TRANSPORT or http_post
        sleep = self.sleep or SLEEP
        budget = max(1, int(request_budget))
        body = json.dumps(build_body(prompt, model, aspect, size)).encode("utf-8")
        a = Attempt(started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        t0 = time.monotonic()
        problem = ""
        for n in (1, 2):
            a.http_requests += 1
            try:
                status, raw = post(self.endpoint, self._headers(), body, self.timeout)
            except TransportError as e:
                status, raw, problem = None, b"", f"network error: {e}"
            except Exception as e:  # a faulty transport must not leak anything either
                status, raw, problem = None, b"", f"network error: {type(e).__name__}"
            a.http_status = status
            if status in (401, 403):
                a.code, a.fatal = "AUTH_FAILED", True
                a.message = f"HTTP {status}: the endpoint rejected the key"
                break
            if status is None or status == 429 or status >= 500:
                if status is not None:
                    problem = f"HTTP {status}: {raw[:200].decode('utf-8', errors='replace')}"
                if n == 1 and a.http_requests < budget:
                    sleep(self.retry_delay)
                    continue
                a.code, a.message = "ENDPOINT_FAILED", problem
                break
            if status != 200:
                a.code = "ENDPOINT_FAILED"
                a.message = f"HTTP {status}: {raw[:200].decode('utf-8', errors='replace')}"
                break
            img, mime, text, finish = parse_response(raw)
            a.response_text, a.finish_reason = text, (finish or None)
            if img is None:
                a.code = "NO_IMAGE"
                a.message = "HTTP 200 without a usable image (images[] missing, empty or not a data:image URL)"
            else:
                a.ok, a.image, a.mime = True, img, mime
            break
        a.elapsed_s = time.monotonic() - t0
        a.message = scrub(a.message)
        a.response_text = scrub(a.response_text)
        return a
