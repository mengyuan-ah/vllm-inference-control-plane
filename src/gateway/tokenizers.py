from __future__ import annotations

from typing import Any


class HuggingFaceTokenCounter:
    """Exact tokenizer adapter using files already present in the shared HF cache."""

    def __init__(self, model_id: str):
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("transformers is required for exact RunPod tokenization") from exc
        self._tokenizer: Any = AutoTokenizer.from_pretrained(
            model_id,
            local_files_only=True,
        )

    def count(self, prompt: str) -> int:
        return len(self._tokenizer.encode(prompt, add_special_tokens=False))
