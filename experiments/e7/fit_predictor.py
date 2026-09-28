#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from e7.calibrate import fit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output is immutable and already exists")
    payload = fit(args.arm)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
