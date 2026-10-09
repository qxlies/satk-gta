"""The HTTP client with a mocked transport: success, NO_IMAGE, 401, 5xx + retry, redaction."""

from __future__ import annotations

import json
import pickle

import pytest

from satk.core.errors import SatkError
from satk.imagegen import client as C

from .conftest import FAKE_KEY, FakeEndpoint, badge_image, ok_body, png_of, text_only_body


@pytest.fixture
def png():
    return png_of(badge_image(64))


def make(script, **kw):
    ep = FakeEndpoint(script)
    sleeps: list[float] = []
    cl = C.ImageClient(FAKE_KEY, endpoint=C.DEFAULT_ENDPOINT, transport=ep, sleep=sleeps.append, **kw)
    return cl, ep, sleeps


def test_success_request_shape_and_image(png):
    cl, ep, sleeps = make([(200, ok_body(png))])
    a = cl.generate("a prompt", aspect="1:1", size="1K")
    assert a.ok and a.image == png and a.code is None and a.http_requests == 1 and sleeps == []
    req = ep.requests[0]
    assert req["url"] == "https://api.rout.my/v1/chat/completions"
    assert req["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["body"] == {"model": C.DEFAULT_MODEL, "messages": [{"role": "user", "content": "a prompt"}],
                           "modalities": ["image", "text"], "image_config": {"aspect_ratio": "1:1", "image_size": "1K"}}
    assert req["timeout"] == 120.0
    assert a.response_text == "Here is your icon."


def test_no_image_text_only_records_text_and_finish_reason():
    text = "Sorry. " * 80
    cl, ep, sleeps = make([(200, text_only_body(text, finish="content_filter"))])
    a = cl.generate("p")
    assert not a.ok and a.code == "NO_IMAGE" and a.http_requests == 1 and not a.fatal
    assert a.response_text == text[:300] and len(a.response_text) == 300
    assert a.finish_reason == "content_filter"
    rec = a.record()
    assert rec["code"] == "NO_IMAGE" and rec["finish_reason"] == "content_filter" and sleeps == []


@pytest.mark.parametrize("images", [[], [{"image_url": {"url": "https://example.com/x.png"}}],
                                    [{"image_url": {"url": "data:text/plain;base64,aGk="}}], [{"type": "image_url"}],
                                    "oops"])
def test_no_image_unusable_images_array(images):
    body = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "hm", "images": images}}]}).encode()
    cl, *_ = make([(200, body)])
    assert cl.generate("p").code == "NO_IMAGE"


def test_no_image_garbage_body():
    cl, *_ = make([(200, b"<html>not json</html>")])
    a = cl.generate("p")
    assert a.code == "NO_IMAGE" and a.response_text == ""


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failed_is_fatal_and_not_retried(status):
    cl, ep, sleeps = make([(status, b'{"error":"bad key"}')])
    a = cl.generate("p")
    assert a.code == "AUTH_FAILED" and a.fatal and a.http_requests == 1 and sleeps == []


def test_500_then_success_retries_once_after_10s(png):
    cl, ep, sleeps = make([(500, b"oops"), (200, ok_body(png))])
    a = cl.generate("p")
    assert a.ok and a.http_requests == 2 and sleeps == [10.0] and len(ep.requests) == 2


@pytest.mark.parametrize("status", [429, 500, 503])
def test_endpoint_failed_after_one_retry(status):
    cl, ep, sleeps = make([(status, b"busy")])
    a = cl.generate("p")
    assert a.code == "ENDPOINT_FAILED" and a.http_requests == 2 and sleeps == [10.0] and not a.fatal


def test_network_error_retries_then_endpoint_failed():
    cl, ep, sleeps = make([C.TransportError("TimeoutError: timed out")])
    a = cl.generate("p")
    assert a.code == "ENDPOINT_FAILED" and a.http_requests == 2 and "network error" in a.message


def test_request_budget_one_means_no_retry():
    cl, ep, sleeps = make([(500, b"x")])
    a = cl.generate("p", request_budget=1)
    assert a.code == "ENDPOINT_FAILED" and a.http_requests == 1 and sleeps == []


def test_other_http_error_is_endpoint_failed_without_retry():
    cl, ep, sleeps = make([(404, b"nope")])
    a = cl.generate("p")
    assert a.code == "ENDPOINT_FAILED" and a.http_requests == 1


def test_no_api_key(monkeypatch):
    monkeypatch.delenv("SATK_IMAGEGEN_API_KEY", raising=False)
    assert not C.key_present()
    with pytest.raises(SatkError) as e:
        C.ImageClient.from_env()
    assert e.value.data["imagegen_code"] == "NO_API_KEY" and e.value.msg.startswith("NO_API_KEY")
    assert e.value.code in {"NOT_READY"}


def test_key_present_checks_name_only(fake_key):
    assert C.key_present()
    assert C.ImageClient.from_env().host == "api.rout.my"


def test_endpoint_override_and_scheme_rules(monkeypatch):
    monkeypatch.setenv("SATK_IMAGEGEN_ENDPOINT", "https://example.test/v1/chat/completions")
    assert C.endpoint_url().startswith("https://example.test")
    monkeypatch.setenv("SATK_IMAGEGEN_ENDPOINT", "http://127.0.0.1:8080/v1/x")
    assert C.endpoint_host() == "127.0.0.1:8080"
    for bad in ("http://example.test/x", "ftp://example.test/x", "https://user:pw@example.test/x", "https:///x"):
        monkeypatch.setenv("SATK_IMAGEGEN_ENDPOINT", bad)
        with pytest.raises(SatkError) as e:
            C.endpoint_url()
        assert e.value.code == "BAD_PARAMS"
        assert "pw" not in str(e.value)


def test_repr_and_pickle_never_show_the_key(png):
    cl, *_ = make([(200, ok_body(png))])
    assert FAKE_KEY not in repr(cl) and FAKE_KEY not in str(cl)
    with pytest.raises(TypeError):
        pickle.dumps(cl)
    a = cl.generate("p")
    assert FAKE_KEY not in repr(a) and FAKE_KEY not in json.dumps(a.record())


def test_errors_and_records_scrub_a_key_echoed_by_the_server(fake_key):
    echo = f"invalid key {FAKE_KEY} / Authorization: Bearer {FAKE_KEY}".encode()
    cl, *_ = make([(500, echo)])
    a = cl.generate("p")
    assert FAKE_KEY not in a.message and FAKE_KEY not in json.dumps(a.record())
    body = text_only_body(f"your key {FAKE_KEY} is odd")
    cl, *_ = make([(200, body)])
    a = cl.generate("p")
    assert FAKE_KEY not in a.response_text and FAKE_KEY not in json.dumps(a.record())
    err = C.imagegen_error("AUTH_FAILED", f"rejected {FAKE_KEY}", hint=f"use {FAKE_KEY}", data={"k": FAKE_KEY})
    assert FAKE_KEY not in json.dumps(err.to_dict()) and FAKE_KEY not in str(err)
    assert err.code == "AUTH" and err.data["imagegen_code"] == "AUTH_FAILED"


def test_scrub_masks_bearer_patterns_and_nested_values():
    out = C.scrub({"a": ["Authorization: Bearer abcdef123456"], "b": 3})
    assert "abcdef123456" not in json.dumps(out) and out["b"] == 3


def test_transport_exception_does_not_leak(png):
    def broken(url, headers, body, timeout):
        raise RuntimeError(f"boom {headers['Authorization']}")

    cl = C.ImageClient(FAKE_KEY, endpoint=C.DEFAULT_ENDPOINT, transport=broken, sleep=lambda s: None)
    a = cl.generate("p")
    assert a.code == "ENDPOINT_FAILED" and FAKE_KEY not in json.dumps(a.record()) and FAKE_KEY not in a.message
