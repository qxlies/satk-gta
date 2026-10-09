"""The real stdlib transport against a loopback server (no external network)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from satk.imagegen import client as C

from .conftest import FAKE_KEY, badge_image, ok_body, png_of

ORIG_POST = C.http_post  # the autouse fixture replaces the module attribute; tests pass this one explicitly


class Server:
    def __init__(self, handler):
        self.seen: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", "0"))
                outer.seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                   "ctype": self.headers.get("Content-Type"), "body": json.loads(self.rfile.read(n))})
                handler(self, outer)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1/chat/completions"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def reply(h, status, body=b"", headers=None):
    h.send_response(status)
    h.send_header("Content-Length", str(len(body)))
    for k, v in (headers or {}).items():
        h.send_header(k, v)
    h.end_headers()
    h.wfile.write(body)


def test_real_transport_success_and_headers(monkeypatch):
    png = png_of(badge_image(48))
    with Server(lambda h, s: reply(h, 200, ok_body(png))) as srv:
        monkeypatch.setenv("SATK_IMAGEGEN_ENDPOINT", srv.url)
        cl = C.ImageClient(FAKE_KEY, transport=ORIG_POST, timeout=5)
        a = cl.generate("p")
    assert a.ok and a.image == png
    seen = srv.seen[0]
    assert seen["auth"] == f"Bearer {FAKE_KEY}" and seen["ctype"] == "application/json"
    assert seen["body"]["modalities"] == ["image", "text"] and seen["body"]["image_config"]["image_size"] == "1K"


def test_real_transport_401_and_500_statuses(monkeypatch):
    codes = iter([401])
    with Server(lambda h, s: reply(h, next(codes), b"nope")) as srv:
        monkeypatch.setenv("SATK_IMAGEGEN_ENDPOINT", srv.url)
        a = C.ImageClient(FAKE_KEY, transport=ORIG_POST, timeout=5, sleep=lambda s: None).generate("p")
    assert a.code == "AUTH_FAILED" and a.fatal
    with Server(lambda h, s: reply(h, 500, b"err")) as srv:
        monkeypatch.setenv("SATK_IMAGEGEN_ENDPOINT", srv.url)
        a = C.ImageClient(FAKE_KEY, transport=ORIG_POST, timeout=5, sleep=lambda s: None).generate("p")
    assert a.code == "ENDPOINT_FAILED" and a.http_requests == 2 and len(srv.seen) == 2


def test_redirects_are_not_followed_so_the_key_stays_on_the_host(monkeypatch):
    with Server(lambda h, s: reply(h, 302, b"", {"Location": "http://127.0.0.1:9/elsewhere"})) as srv:
        monkeypatch.setenv("SATK_IMAGEGEN_ENDPOINT", srv.url)
        a = C.ImageClient(FAKE_KEY, transport=ORIG_POST, timeout=5, sleep=lambda s: None).generate("p")
    assert a.code == "ENDPOINT_FAILED" and a.http_status == 302 and len(srv.seen) == 1


def test_connection_refused_is_a_transport_error_without_secrets():
    cl = C.ImageClient(FAKE_KEY, endpoint="http://127.0.0.1:9/x", transport=ORIG_POST, timeout=2,
                       sleep=lambda s: None)
    a = cl.generate("p")
    assert a.code == "ENDPOINT_FAILED" and a.http_requests == 2 and FAKE_KEY not in json.dumps(a.record())
