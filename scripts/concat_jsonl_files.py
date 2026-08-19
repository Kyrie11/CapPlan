#!/usr/bin/env python
from __future__ import annotations
import argparse
import json
from pathlib import Path
from capplan.utils.serialization import iter_jsonl


def main() -> None:
    p = argparse.ArgumentParser(description="Atomically stream-concatenate JSONL inputs.")
    p.add_argument("--inputs", nargs="+", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(out.suffix + ".part"); part.unlink(missing_ok=True)
    count = 0
    with part.open("w", encoding="utf-8") as dst:
        for value in args.inputs:
            src = Path(value)
            if not src.exists(): raise FileNotFoundError(src)
            for row in iter_jsonl(src):
                dst.write(json.dumps(row, sort_keys=True) + "\n"); count += 1
    part.replace(out)
    print(json.dumps({"status":"PASS","rows":count,"output":str(out),"inputs":args.inputs}, indent=2))
    print("JSONL_CONCAT_CHECK=PASS")
if __name__ == "__main__": main()
