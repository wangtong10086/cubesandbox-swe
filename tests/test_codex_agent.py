from __future__ import annotations

from cubesandbox_swe.codex_agent import build_swe_prompt, parse_codex_json_output


def test_build_swe_prompt_points_agent_at_app() -> None:
    prompt = build_swe_prompt("Fix the bug", repo="owner/repo", language="python")

    assert "Repository: owner/repo" in prompt
    assert "Language: python" in prompt
    assert "Fix the bug" in prompt
    assert "Modify ONLY source code files under /app" in prompt


def test_parse_codex_json_output_extracts_usage_items_and_errors() -> None:
    stdout = "\n".join(
        [
            '{"type":"item.completed","item":{"role":"assistant","content":"done"}}',
            '{"type":"turn.completed","usage":{"input_tokens":3,"output_tokens":5}}',
            '{"type":"turn.failed","error":{"message":"network failed"}}',
        ]
    )

    total_tokens, model_calls, conversation, last_error = parse_codex_json_output(stdout)

    assert total_tokens == 8
    assert model_calls == 1
    assert conversation == [{"role": "assistant", "content": "done"}]
    assert last_error == "network failed"
