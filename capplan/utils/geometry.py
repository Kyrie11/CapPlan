from __future__ import annotations

import math
from typing import Iterable, Sequence, Tuple

Point = Tuple[float, float]


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def polyline_length(points: Sequence[Point]) -> float:
    return sum(distance(points[i], points[i + 1]) for i in range(len(points) - 1))


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def normalized_ratio(numer: float, denom: float, eps: float = 1e-9) -> float:
    return numer / (abs(denom) + eps)
