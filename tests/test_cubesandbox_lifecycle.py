from __future__ import annotations

from cubesandbox_swe.cubesandbox_lifecycle import restore_sandbox_state, save_sandbox_state


def test_save_sandbox_state_uses_upstream_pause() -> None:
    calls = []

    class Sandbox:
        sandbox_id = "sandbox-id"
        template_id = "template-id"

        def pause(self, **kwargs):
            calls.append(kwargs)

    state = save_sandbox_state(Sandbox(), timeout=12, interval=2)

    assert state.sandbox_id == "sandbox-id"
    assert state.template_id == "template-id"
    assert calls == [{"timeout": 12, "interval": 2}]


def test_restore_sandbox_state_uses_upstream_connect() -> None:
    calls = []

    class Sandbox:
        @classmethod
        def connect(cls, sandbox_id):
            calls.append(sandbox_id)
            return {"sandbox_id": sandbox_id}

    state = save_sandbox_state(type("Paused", (), {"sandbox_id": "sb", "template_id": "tpl", "pause": lambda self, **_: None})())

    assert restore_sandbox_state(state, sandbox_cls=Sandbox) == {"sandbox_id": "sb"}
    assert calls == ["sb"]
