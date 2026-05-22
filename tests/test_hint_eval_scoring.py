from cubesandbox_swe.hint_eval.scoring import (
    extract_choice_completion,
    extract_choice_logprobs,
    point_distribution,
    uniform_distribution,
)


def test_extract_choice_logprobs_accepts_punctuated_label_tokens() -> None:
    data = {
        "choices": [
            {
                "logprobs": {
                    "content": [
                        {
                            "top_logprobs": [
                                {"token": " A.", "logprob": -0.2},
                                {"token": "B", "logprob": -1.2},
                            ]
                        }
                    ]
                }
            }
        ]
    }

    assert extract_choice_logprobs(data, ["A", "B"]) == {"A": -0.2, "B": -1.2}


def test_extract_choice_completion_parses_final_label() -> None:
    data = {"choices": [{"message": {"content": "\n\nA"}}]}

    assert extract_choice_completion(data, ["A", "B", "C", "D"]) == "A"


def test_extract_choice_completion_parses_reasoning_output_label() -> None:
    data = {"choices": [{"message": {"content": None, "reasoning": "After checking, output: C"}}]}

    assert extract_choice_completion(data, ["A", "B", "C", "D"]) == "C"


def test_point_distribution_is_nearly_one_hot() -> None:
    distribution = point_distribution("B", ["A", "B", "C"])

    assert distribution["B"] > 0.999
    assert sum(distribution.values()) == 1.0


def test_uniform_distribution_sums_to_one() -> None:
    assert uniform_distribution(["A", "B"]) == {"A": 0.5, "B": 0.5}
