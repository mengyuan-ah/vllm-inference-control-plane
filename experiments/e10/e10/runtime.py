from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

MODEL = "Qwen/Qwen3-4B-Instruct-2507"
MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
INPUT_TOKENS = 1024
OUTPUT_TOKENS = 128
FROZEN_BETA = 107.2820167
REQUEST_WORK = INPUT_TOKENS + FROZEN_BETA * OUTPUT_TOKENS
RECOVERY_MAX_WORK = 20_000.0
SLO_TTFT_S = 0.250
SLO_TPOT_S = 0.015
SLO_E2E_S = 2.0


def build_exact_prompt(tokenizer: Any, seed: int, index: int) -> tuple[str, list[int]]:
    prefix = f"E10 deterministic seed {seed} request {index}. "
    source = (prefix + "The quick brown fox crosses the quiet field. ") * 400
    token_ids = tokenizer.encode(source, add_special_tokens=False)[:INPUT_TOKENS]
    if len(token_ids) != INPUT_TOKENS:
        raise ValueError("tokenizer did not produce enough tokens")
    for _ in range(20):
        prompt = tokenizer.decode(token_ids, skip_special_tokens=False)
        encoded = tokenizer.encode(prompt, add_special_tokens=False)
        if len(encoded) == INPUT_TOKENS:
            return prompt, encoded
        if len(encoded) > INPUT_TOKENS:
            token_ids = encoded[:INPUT_TOKENS]
        else:
            token_ids = encoded + token_ids[: INPUT_TOKENS - len(encoded)]
    raise RuntimeError("unable to construct an exactly 1024-token prompt")


def prompt_record(tokenizer: Any, seed: int, index: int) -> dict[str, Any]:
    prompt, token_ids = build_exact_prompt(tokenizer, seed, index)
    return {
        "prompt": prompt,
        "input_token_ids": token_ids,
        "input_tokens": len(token_ids),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "token_ids_sha256": hashlib.sha256(json.dumps(token_ids).encode()).hexdigest(),
    }


@dataclass
class StreamObservation:
    first_content_monotonic: float | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    saw_done: bool = False
    stream_error: bool = False
    chunk_count: int = 0
    transport_error: bool = False

    def feed(self, data: str, now: float) -> None:
        if data == "[DONE]":
            self.saw_done = True
            return
        event = json.loads(data)
        if event.get("error") is not None:
            self.stream_error = True
        self.usage = event.get("usage") or self.usage
        choices = event.get("choices") or []
        if choices:
            self.chunk_count += 1
            if choices[0].get("text") and self.first_content_monotonic is None:
                self.first_content_monotonic = now
            if choices[0].get("finish_reason") is not None:
                self.finish_reason = choices[0]["finish_reason"]

    def metrics(self, sent: float, completed: float) -> dict[str, Any]:
        completion_tokens = (
            self.usage.get("completion_tokens") if isinstance(self.usage, dict) else None
        )
        success = bool(
            self.saw_done
            and not self.stream_error
            and not self.transport_error
            and self.first_content_monotonic is not None
            and self.finish_reason == "length"
            and type(completion_tokens) is int
            and completion_tokens == OUTPUT_TOKENS
        )
        ttft = (
            self.first_content_monotonic - sent
            if self.first_content_monotonic is not None
            else None
        )
        e2e = completed - sent if success else None
        tpot = (
            (completed - self.first_content_monotonic) / (completion_tokens - 1)
            if success and completion_tokens > 1 and self.first_content_monotonic is not None
            else None
        )
        slo_success = bool(
            success
            and ttft is not None
            and tpot is not None
            and e2e is not None
            and ttft <= SLO_TTFT_S
            and tpot <= SLO_TPOT_S
            and e2e <= SLO_E2E_S
        )
        return {
            "success": success,
            "content_received": self.first_content_monotonic is not None,
            "transport_error": self.transport_error,
            "ttft_s": ttft,
            "tpot_s": tpot,
            "e2e_s": e2e,
            "slo_success": slo_success,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "saw_stream_done": self.saw_done,
            "stream_error": self.stream_error,
            "stream_chunk_count": self.chunk_count,
        }


def derived_offer_count(rate: float, window: float, explicit: int | None) -> int:
    expected = round(rate * window)
    if rate <= 0 or window <= 0 or not math.isclose(expected, rate * window, abs_tol=1e-9):
        raise ValueError("rate times offer window must be a positive integer")
    if explicit is not None and explicit != expected:
        raise ValueError(f"offers={explicit} conflicts with rate*window={expected}")
    return expected


def classify_outcome(row: dict[str, Any]) -> str:
    """Classify client receipt, never infer success/content from gateway completion.

    Missing/contradictory evidence is deliberately not an ordinary failure label.
    Callers retain this invalid label and the errors for audit/analyzer rejection.
    """
    errors = []
    content = row.get("content_received")
    if not isinstance(content, bool):
        errors.append("missing client content observation")
    elif content != (row.get("ttft_s") is not None):
        errors.append("content observation contradicts TTFT")
    if not isinstance(row.get("success"), bool):
        errors.append("missing client completion observation")
    rejected = {
        "Rejected503": "rejected_no_serving_replica",
        "Rejected429": "rejected_safety_bound",
    }.get(row.get("gateway_state"))
    if rejected and (content or row.get("success")):
        errors.append("rejection contradicts client content/success")
    if row.get("success") and not (
        content
        and row.get("saw_stream_done")
        and row.get("finish_reason") == "length"
        and isinstance(row.get("usage"), dict)
        and type(row["usage"].get("completion_tokens")) is int
        and row["usage"]["completion_tokens"] == OUTPUT_TOKENS
        and not any(
            row.get(key) for key in ("stream_error", "transport_error", "timed_out", "error")
        )
    ):
        errors.append("success contradicts client completion evidence")
    if row.get("slo_success") and not row.get("success"):
        errors.append("SLO success without complete client success")
    row["outcome_evidence_errors"] = errors
    if errors:
        return "invalid_evidence"
    if rejected:
        return rejected
    if row.get("success"):
        return "success_after_precontent_failover" if row.get("retry_count") else "success_no_retry"
    return "failed_or_truncated_after_content" if content else "failed_before_content"
