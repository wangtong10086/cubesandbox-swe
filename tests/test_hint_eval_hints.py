from __future__ import annotations

from cubesandbox_swe.hint_eval.hints import generate_hints, leakage_flags
from cubesandbox_swe.hint_eval.schemas import CandidateAction, HINT_CONDITIONS


def test_generate_hints_has_all_conditions() -> None:
    candidates = [
        CandidateAction(
            id="A",
            kind="file",
            label="Inspect source",
            text="inspect src/example.py",
            file_path="src/example.py",
            is_positive=True,
            weight=1.0,
        ),
        CandidateAction(id="B", kind="file", label="Inspect README", text="inspect README.md"),
    ]

    hints, flags = generate_hints(
        candidates,
        cutpoint_type="file_localization",
        hint_strength="l2",
        final_patch="diff --git a/src/example.py b/src/example.py\n",
    )

    assert set(hints) == set(HINT_CONDITIONS)
    assert "src/example.py" in hints["causal"]
    assert flags == []


def test_leakage_guard_catches_patch_text() -> None:
    patch = "diff --git a/src/example.py b/src/example.py\n+    return 'very specific patched greeting value'\n"
    hints = {
        "neutral": "Additional diagnostic note: No extra information is available.",
        "causal": "Use very specific patched greeting value in the answer.",
        "irrelevant": "Read docs.",
        "misleading": "Inspect config.",
    }

    assert "causal:patch_substring" in leakage_flags(hints, patch)
