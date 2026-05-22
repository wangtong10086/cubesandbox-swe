"""Local lifecycle helpers for upstream CubeSandbox SDKs."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import httpx


@dataclass(frozen=True)
class SandboxState:
    """A paused sandbox handle that can be resumed through upstream connect."""

    sandbox_id: str
    template_id: str | None = None


def save_sandbox_state(sandbox: Any, *, timeout: float = 180, interval: float = 1.0) -> SandboxState:
    """Pause a sandbox and return a lightweight state handle."""
    session = getattr(sandbox, "_session", None)
    if session is not None:
        session.timeout = httpx.Timeout(timeout=max(timeout, 30), connect=30, write=30, pool=30)
    sandbox.pause(timeout=timeout, interval=interval)
    return SandboxState(
        sandbox_id=str(sandbox.sandbox_id),
        template_id=getattr(sandbox, "template_id", None),
    )


def restore_sandbox_state(state: SandboxState, *, sandbox_cls: Any | None = None) -> Any:
    """Restore a paused sandbox using the upstream SDK connect API."""
    if sandbox_cls is None:
        from cubesandbox import Sandbox as sandbox_cls

    try:
        sandbox = sandbox_cls.connect(state.sandbox_id)
    except httpx.TimeoutException:
        sandbox = _connect_with_extended_timeout(state, sandbox_cls=sandbox_cls)
    return _wait_until_running(sandbox)


def _connect_with_extended_timeout(state: SandboxState, *, sandbox_cls: Any) -> Any:
    """Fallback for SDK connect calls that time out while resuming a sandbox."""
    from cubesandbox._config import Config
    from cubesandbox.sandbox import _check_response

    cfg = Config()
    with httpx.Client(
        headers={"Content-Type": "application/json"},
        timeout=httpx.Timeout(timeout=max(cfg.timeout, 30), connect=30, write=30, pool=30),
    ) as client:
        response = client.post(
            f"{cfg.api_url}/sandboxes/{state.sandbox_id}/connect",
            json={"timeout": cfg.timeout},
        )
    _check_response(response)
    return sandbox_cls(response.json(), config=cfg)


def _wait_until_running(sandbox: Any, *, timeout: float = 30, interval: float = 1.0) -> Any:
    get_info = getattr(sandbox, "get_info", None)
    if not callable(get_info):
        return sandbox

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_info().get("state") == "running":
            break
        time.sleep(interval)
    return sandbox
