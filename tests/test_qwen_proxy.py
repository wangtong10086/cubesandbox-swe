from __future__ import annotations

import io
import json
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


def test_responses_proxy_can_disable_required_tool_choice() -> None:
    required_body = _capture_upstream_responses_body(require_initial_tool_choice=True)
    auto_body = _capture_upstream_responses_body(require_initial_tool_choice=False)

    assert required_body["tool_choice"] == "required"
    assert auto_body["tool_choice"] == "auto"


def _capture_upstream_responses_body(*, require_initial_tool_choice: bool) -> dict[str, Any]:
    payload = {
        "model": "test-model",
        "input": [{"type": "message", "role": "user", "content": "inspect the repo"}],
        "tools": [
            {
                "type": "namespace",
                "name": "mcp__cubesandbox__",
                "tools": [
                    {
                        "type": "function",
                        "name": "cube_read_file",
                        "description": "Read a file",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ],
            }
        ],
        "tool_choice": "auto",
    }
    raw = json.dumps(payload).encode("utf-8")
    captured: dict[str, Any] = {}
    handler = object.__new__(ProxyHandler)
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.headers = {"Content-Length": str(len(raw))}
    handler.require_initial_tool_choice = require_initial_tool_choice

    def fake_request_upstream(method: str, path: str, body: bytes | None, content_type: str | None):  # noqa: ANN202
        captured["method"] = method
        captured["path"] = path
        captured["content_type"] = content_type
        captured["body"] = json.loads((body or b"{}").decode("utf-8"))
        return 200, {"Content-Type": "application/json"}, b'{"output":[]}'

    def fake_send_json(status: int, value: Any) -> None:
        captured["status"] = status
        captured["response"] = value

    handler.request_upstream = fake_request_upstream
    handler.send_json = fake_send_json

    handler.proxy_responses()

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/responses"
    assert captured["content_type"] == "application/json"
    assert captured["status"] == 200
    return captured["body"]
