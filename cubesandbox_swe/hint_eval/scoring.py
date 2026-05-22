"""Candidate scorers for hint-eval."""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import math
import os
import re
from typing import Any

import httpx

from .schemas import CandidateAction, HINT_CONDITIONS, ScoreRecord


class ScoreClient(ABC):
    @abstractmethod
    def score_candidates(
        self,
        prompt: str,
        candidates: list[CandidateAction],
        *,
        condition: str = "neutral",
        target_distribution: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Return normalized candidate probabilities."""


class FakeScoreClient(ScoreClient):
    """Deterministic no-network scorer for tests and smoke flows."""

    def score_candidates(
        self,
        prompt: str,
        candidates: list[CandidateAction],
        *,
        condition: str = "neutral",
        target_distribution: dict[str, float] | None = None,
    ) -> dict[str, float]:
        del prompt
        logits: dict[str, float] = {}
        positive_ids = set((target_distribution or {}).keys())
        first_negative = next((candidate.id for candidate in candidates if candidate.id not in positive_ids), None)
        for index, candidate in enumerate(candidates):
            logit = 1.0 / (index + 2)
            if candidate.id in positive_ids:
                logit += 1.0
            if condition == "causal" and candidate.id in positive_ids:
                logit += 1.0
            elif condition == "misleading" and candidate.id == first_negative:
                logit += 1.5
            elif condition == "irrelevant":
                logit += 0.05
            logits[candidate.id] = logit
        return softmax(logits)


class ChoiceLogprobsClient(ScoreClient):
    """OpenAI-compatible chat-completions scorer using top logprobs for choice labels."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        trust_env: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.trust_env = trust_env

    def score_candidates(
        self,
        prompt: str,
        candidates: list[CandidateAction],
        *,
        condition: str = "neutral",
        target_distribution: dict[str, float] | None = None,
    ) -> dict[str, float]:
        del condition, target_distribution
        labels = [candidate.id for candidate in candidates]
        choice_prompt = format_choice_prompt(prompt, candidates)
        system_prompt = (
            "Choose the best candidate id. "
            f"Reply with exactly one of: {', '.join(labels)}. Do not explain."
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": choice_prompt},
            ],
            "temperature": 0,
            "max_tokens": 1,
            "logprobs": True,
            "top_logprobs": max(20, len(labels)),
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=self.timeout, trust_env=self.trust_env) as client:
            response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        logprobs = extract_choice_logprobs(data, labels)
        if not logprobs:
            return self.score_candidates_by_completion(system_prompt, choice_prompt, labels)
        if set(labels) - set(logprobs):
            floor = min(logprobs.values()) - 20.0
            for missing in set(labels) - set(logprobs):
                logprobs[missing] = floor
        return softmax(logprobs)

    def score_candidates_by_completion(self, system_prompt: str, choice_prompt: str, labels: list[str]) -> dict[str, float]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": choice_prompt},
            ],
            "temperature": 0,
            "max_tokens": 512,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=self.timeout, trust_env=self.trust_env) as client:
            response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        label = extract_choice_completion(response.json(), labels)
        if not label:
            return uniform_distribution(labels)
        return point_distribution(label, labels)


def make_score_client(
    scorer: str,
    *,
    model: str,
    base_url: str | None = None,
    api_key_env: str | None = None,
    timeout: float = 60.0,
) -> ScoreClient:
    if scorer == "fake":
        return FakeScoreClient()
    if scorer == "choice-logprobs":
        if not base_url:
            raise ValueError("--base-url is required for choice-logprobs")
        env_name = api_key_env or "OPENAI_API_KEY"
        api_key = os.environ.get(env_name)
        if not api_key:
            raise ValueError(f"{env_name} must be set for choice-logprobs")
        return ChoiceLogprobsClient(base_url=base_url, api_key=api_key, model=model, timeout=timeout)
    raise ValueError(f"unsupported scorer: {scorer}")


def score_probe(probe: dict[str, Any], *, client: ScoreClient, scorer: str, model: str) -> ScoreRecord:
    candidates = [CandidateAction(**candidate) for candidate in probe["candidate_actions"]]
    target = {str(key): float(value) for key, value in probe["target_distribution"].items()}
    condition_scores: dict[str, dict[str, float]] = {}
    for condition in HINT_CONDITIONS:
        prompt = render_scoring_prompt(probe, condition)
        condition_scores[condition] = client.score_candidates(
            prompt,
            candidates,
            condition=condition,
            target_distribution=target,
        )
    return ScoreRecord(
        schema_version="hint_eval_score_v1",
        probe_id=str(probe["probe_id"]),
        task_id=probe.get("task_id"),
        instance_id=probe.get("instance_id"),
        model=model,
        scorer=scorer,
        cutpoint_type=str(probe["cutpoint_type"]),
        target_distribution=target,
        candidate_actions=probe["candidate_actions"],
        hints=probe["hints"],
        condition_scores=condition_scores,
        trajectory_file=probe.get("trajectory_file"),
        prefix_id=probe.get("prefix_id"),
        prefix_source=probe.get("prefix_source"),
        support_bucket=probe.get("support_bucket"),
        prefix_group=probe.get("prefix_group"),
        oracle_source=probe.get("oracle_source"),
        trajectory_resolved=probe.get("trajectory_resolved"),
        candidate_diagnostics=probe.get("candidate_diagnostics") or {},
    )


def render_scoring_prompt(probe: dict[str, Any], condition: str) -> str:
    messages = probe.get("prefix_messages") if isinstance(probe.get("prefix_messages"), list) else []
    prefix = "\n".join(
        f"{message.get('role', 'unknown')}: {str(message.get('content', ''))[:1000]}"
        for message in messages[-8:]
        if isinstance(message, dict)
    )
    hint = probe.get("hints", {}).get(condition, "")
    candidates = "\n".join(f"{item['id']}. {item['text']}" for item in probe.get("candidate_actions", []))
    return (
        "Given the SWE task trajectory prefix, choose the next best action.\n\n"
        f"Trajectory prefix:\n{prefix}\n\n"
        f"Hint:\n{hint}\n\n"
        f"Candidates:\n{candidates}\n\n"
        "Return only the candidate id."
    )


def format_choice_prompt(prompt: str, candidates: list[CandidateAction]) -> str:
    choices = "\n".join(f"{candidate.id}. {candidate.text}" for candidate in candidates)
    return f"{prompt}\n\nCandidate ids:\n{choices}"


def extract_choice_logprobs(data: dict[str, Any], labels: list[str]) -> dict[str, float]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("chat completion response has no choices")
    logprobs_payload = choices[0].get("logprobs") if isinstance(choices[0], dict) else None
    content = logprobs_payload.get("content") if isinstance(logprobs_payload, dict) else None
    if not isinstance(content, list) or not content:
        raise RuntimeError("chat completion response has no token logprobs")
    top = content[0].get("top_logprobs") if isinstance(content[0], dict) else None
    if not isinstance(top, list):
        raise RuntimeError("chat completion response has no top_logprobs")
    label_set = set(labels)
    result: dict[str, float] = {}
    for entry in top:
        if not isinstance(entry, dict):
            continue
        token = normalize_choice_label(str(entry.get("token") or ""), label_set)
        if token and isinstance(entry.get("logprob"), (int, float)):
            result[token] = float(entry["logprob"])
    return result


def extract_choice_completion(data: dict[str, Any], labels: list[str]) -> str | None:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return None
    label_set = set(labels)
    content = message.get("content")
    if isinstance(content, str):
        label = normalize_choice_label(content, label_set)
        if label:
            return label
        for match in re.finditer(r"\b[A-Z]\b", content):
            label = normalize_choice_label(match.group(0), label_set)
            if label:
                return label
    for key in ("reasoning", "reasoning_content"):
        reasoning = message.get(key)
        if not isinstance(reasoning, str):
            continue
        matches = list(re.finditer(r"(?:answer|option|candidate|output|reply)\s*(?:is|:|：)?\s*([A-Z])", reasoning, re.I))
        for match in reversed(matches):
            label = normalize_choice_label(match.group(1).upper(), label_set)
            if label:
                return label
    return None


def normalize_choice_label(text: str, labels: set[str]) -> str | None:
    stripped = text.strip().strip("`'\"()[]{}:：,，.。")
    if stripped in labels:
        return stripped
    match = re.search(r"\b([A-Z])\b", stripped)
    if match and match.group(1) in labels:
        return match.group(1)
    return None


def point_distribution(label: str, labels: list[str]) -> dict[str, float]:
    if len(labels) == 1:
        return {label: 1.0}
    epsilon = 1e-6
    off_target = epsilon / (len(labels) - 1)
    return {candidate: (1.0 - epsilon if candidate == label else off_target) for candidate in labels}


def uniform_distribution(labels: list[str]) -> dict[str, float]:
    if not labels:
        return {}
    probability = 1.0 / len(labels)
    return {label: probability for label in labels}


def softmax(logits: dict[str, float]) -> dict[str, float]:
    if not logits:
        return {}
    max_logit = max(logits.values())
    exps = {key: math.exp(value - max_logit) for key, value in logits.items()}
    total = sum(exps.values())
    return {key: value / total for key, value in exps.items()}


def scores_to_json(records: list[ScoreRecord]) -> list[dict[str, Any]]:
    return [record.to_dict() for record in records]


def score_records_as_text(records: list[ScoreRecord]) -> str:
    return "\n".join(json.dumps(record.to_dict(), sort_keys=True) for record in records)
