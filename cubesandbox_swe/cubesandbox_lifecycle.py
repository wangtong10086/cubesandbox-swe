"""Local lifecycle helpers for upstream CubeSandbox SDKs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SandboxState:
    """A paused sandbox handle that can be resumed through upstream connect."""

    sandbox_id: str
    template_id: str | None = None


def save_sandbox_state(sandbox: Any, *, timeout: float = 180, interval: float = 1.0) -> SandboxState:
    """Pause a sandbox and return a lightweight state handle."""
    sandbox.pause(timeout=timeout, interval=interval)
    return SandboxState(
        sandbox_id=str(sandbox.sandbox_id),
        template_id=getattr(sandbox, "template_id", None),
    )


def restore_sandbox_state(state: SandboxState, *, sandbox_cls: Any | None = None) -> Any:
    """Restore a paused sandbox using the upstream SDK connect API."""
    if sandbox_cls is None:
        from cubesandbox import Sandbox as sandbox_cls

    return sandbox_cls.connect(state.sandbox_id)
