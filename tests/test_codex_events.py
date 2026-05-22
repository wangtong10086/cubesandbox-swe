from __future__ import annotations

from cubesandbox_swe.codex_events import parse_jsonl_events


def test_parse_jsonl_events_counts_usage_and_items() -> None:
    stdout = "\n".join(
        [
            '{"type":"item.completed","item":{"role":"assistant","content":"done"}}',
            '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}',
            "not json",
            '{"type":"turn.failed","error":{"message":"rate limited"}}',
        ]
    )

    summary = parse_jsonl_events(stdout)

    assert summary.total_tokens == 15
    assert summary.model_calls == 1
    assert summary.conversation == [{"role": "assistant", "content": "done"}]
    assert summary.last_error == "rate limited"


def test_parse_jsonl_events_handles_error_event() -> None:
    summary = parse_jsonl_events('{"type":"error","message":"bad request"}')
    assert summary.last_error == "bad request"
