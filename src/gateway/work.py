from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TokenCounter(Protocol):
    def count(self, prompt: str) -> int: ...


class WorkEstimator(Protocol):
    def estimate(self, input_tokens: int, max_tokens: int) -> tuple[float, float]: ...


@dataclass(frozen=True)
class LinearWorkEstimator:
    """Nonbinding Stage A development model; E4 must calibrate its coefficients."""

    alpha: float = 1.0
    beta: float = 1.0

    def estimate(self, input_tokens: int, max_tokens: int) -> tuple[float, float]:
        prefill = self.alpha * input_tokens
        return prefill + self.beta * max_tokens, prefill


class WhitespaceTokenCounter:
    """Lightweight development fallback; inject the exact model tokenizer in RunPod."""

    def count(self, prompt: str) -> int:
        return len(prompt.split())
