#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.request


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080/_stage_a/accounting"
    with urllib.request.urlopen(url, timeout=5) as response:
        data = json.load(response)
    result = {
        "queued_requests": data["queue"]["queued_requests"],
        "queued_work": data["queue"]["queued_work"],
        "active_reservations": sum(item["inflight"] for item in data["replicas"].values()),
        "outstanding_work": sum(item["outstanding_work"] for item in data["replicas"].values()),
    }
    print(json.dumps(result, sort_keys=True))
    if any(result.values()):
        raise SystemExit("gateway accounting did not reconcile to zero")


if __name__ == "__main__":
    main()
