from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, List


def _default(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "to_json"):
        return obj.to_json()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def dump_json(path: str | Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, default=_default)


def load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def write_jsonl(path: str | Path, records: Iterable[Any]) -> None:
    """Write deterministic compact JSONL in buffered batches.

    The original implementation called ``json.dumps`` and ``file.write`` once
    per record using the stdlib's default separators.  Accessibility graph
    builds routinely write millions of node/edge rows, so the per-line Python
    call overhead and extra whitespace become a measurable part of bootstrap
    runtime.  Compact separators change only JSON formatting, not values, and
    batching reduces syscall/Python overhead while preserving deterministic key
    ordering.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), default=_default)
    with path.open("w", encoding="utf-8") as f:
        buffer: List[str] = []
        for r in records:
            buffer.append(encoder.encode(r) + "\n")
            if len(buffer) >= 1024:
                f.write("".join(buffer))
                buffer.clear()
        if buffer:
            f.write("".join(buffer))


def iter_jsonl(path: str | Path) -> Iterator[Any]:
    """Stream JSONL records without materializing the whole file in memory.

    Full four-city nuPlan builds can contain many thousands of scene/PUDO rows;
    callers that only need one pass should prefer this iterator over
    :func:`read_jsonl`.
    """
    p = Path(path)
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def read_jsonl(path: str | Path) -> List[Any]:
    return list(iter_jsonl(path))
