"""Synthetic images and a fake endpoint for the imagegen tests: no network, no real key."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest

FAKE_KEY = "test-only-imagegen-token-9f3a7c1d5b2e8046"


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setenv("SATK_IMAGEGEN_API_KEY", FAKE_KEY)
    monkeypatch.delenv("SATK_IMAGEGEN_ENDPOINT", raising=False)
    return FAKE_KEY


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Any test that forgets to install a transport fails instead of reaching a real endpoint."""
    from satk.imagegen import client

    def boom(*a, **k):
        raise AssertionError("a test tried to use the real network")

    monkeypatch.setattr(client, "http_post", boom)
    monkeypatch.setattr(client, "SLEEP", lambda s: None)


def badge_image(side: int = 256, *, bg=(255, 0, 255), badge=(31, 36, 40), ring=(58, 65, 72), fill: float = 0.8,
                emblem=(232, 234, 237), accent=(52, 168, 83), leak: bool = False, ss: int = 4):
    """A synthetic generated-looking icon: flat key-colour background, rounded badge (anti-aliased), a ring and a
    simple emblem. ``leak`` paints a magenta blob inside the badge (an AI artefact the key cannot remove)."""
    from PIL import Image, ImageDraw

    C = side * ss
    im = Image.new("RGB", (C, C), bg)
    d = ImageDraw.Draw(im)
    b = C * fill
    o = (C - b) / 2
    d.rounded_rectangle([o, o, o + b, o + b], radius=0.12 * b, fill=badge)
    d.rounded_rectangle([o + 0.05 * b, o + 0.05 * b, o + 0.95 * b, o + 0.95 * b], radius=0.08 * b, outline=ring,
                        width=max(1, int(0.02 * b)))
    d.polygon([(o + 0.2 * b, o + 0.75 * b), (o + 0.5 * b, o + 0.35 * b), (o + 0.8 * b, o + 0.75 * b)], fill=accent)
    d.rectangle([o + 0.2 * b, o + 0.75 * b, o + 0.8 * b, o + 0.8 * b], fill=emblem)
    if leak:
        d.ellipse([o + 0.4 * b, o + 0.1 * b, o + 0.6 * b, o + 0.3 * b], fill=(240, 20, 230))
    return im.resize((side, side), Image.LANCZOS)


def png_of(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def ok_body(png: bytes, text: str = "Here is your icon.") -> bytes:
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    return json.dumps({"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": text, "images": [{"type": "image_url", "image_url": {"url": url}}]}}]}).encode()


def text_only_body(text: str = "I cannot draw that.", finish: str = "stop") -> bytes:
    return json.dumps({"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": text}}]}).encode()


class FakeEndpoint:
    """A transport that answers from a script (a list of ``(status, body)`` or callables); records requests."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.requests.append({"url": url, "headers": dict(headers), "body": json.loads(body.decode("utf-8")),
                              "timeout": timeout})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if callable(item):
            item = item(self.requests[-1])
        if isinstance(item, Exception):
            raise item
        return item


def all_text(root: Path) -> list[tuple[Path, bytes]]:
    return [(p, p.read_bytes()) for p in sorted(Path(root).rglob("*")) if p.is_file()]
