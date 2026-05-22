from __future__ import annotations

from typing import Any

from scripts.qwen_responses_namespace_proxy import ProxyHandler


class _DummyResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __enter__(self) -> "_DummyResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return b'{"ok":true}'


class _DummyHandler:
    upstream_base = "https://qwen.example/v1"
    api_key: str | None = None
    timeout = 1


def test_proxy_omits_authorization_for_no_auth_upstream(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):  # noqa: ANN001
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return _DummyResponse()

    monkeypatch.setattr("scripts.qwen_responses_namespace_proxy.urllib.request.urlopen", fake_urlopen)

    status, _headers, data = ProxyHandler.request_upstream(_DummyHandler(), "GET", "/v1/models", None, None)

    assert status == 200
    assert data == b'{"ok":true}'
    assert captured == {"authorization": None, "timeout": 1}


def test_proxy_sends_authorization_when_upstream_key_is_set(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):  # noqa: ANN001
        captured["authorization"] = request.get_header("Authorization")
        return _DummyResponse()

    monkeypatch.setattr("scripts.qwen_responses_namespace_proxy.urllib.request.urlopen", fake_urlopen)
    handler = _DummyHandler()
    handler.api_key = "secret"

    ProxyHandler.request_upstream(handler, "POST", "/v1/chat/completions", b"{}", "application/json")

    assert captured["authorization"] == "Bearer secret"
