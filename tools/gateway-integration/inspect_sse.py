#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--input-tokens", type=int, required=True)
    parser.add_argument("--output-tokens", type=int, required=True)
    args = parser.parse_args()
    text = args.capture.read_text()
    events = []
    clean_done = False
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            clean_done = True
        elif data:
            events.append(json.loads(data))
    content = [
        choice.get("text", "")
        for event in events
        for choice in (event.get("choices") or [])
        if choice.get("text")
    ]
    usages = [event["usage"] for event in events if event.get("usage") is not None]
    assert content, "no non-empty streamed content"
    assert clean_done, "missing [DONE] stream terminator"
    assert usages, "missing final usage event"
    usage = usages[-1]
    assert usage["prompt_tokens"] == args.input_tokens
    assert usage["completion_tokens"] == args.output_tokens
    print(
        json.dumps(
            {
                "non_empty_content_events": len(content),
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "clean_done": clean_done,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
