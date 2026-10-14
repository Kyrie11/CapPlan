"""Conservative provenance checks for time-indexed motion-evidence packets.

A JSON packet is a *declaration by an external checker*, not proof that a
closed-loop simulation ran. Keep both that distinction and the checked interval
explicit. No speed estimate or geometrical vehicle reachability is promoted.
"""
from __future__ import annotations
import math

_REQUIRED_TRUE = ("reachable", "collision_free", "legal_stop",
                  "interface_verified", "method_specific_motion_checked")


def packet_valid(packet: dict | None) -> bool:
    if not isinstance(packet, dict):
        return False
    if not all(packet.get(k) is True for k in _REQUIRED_TRUE):
        return False
    if not isinstance(packet.get("source"), str) or not packet["source"].strip():
        return False
    try:
        a, b = float(packet["valid_start_s"]), float(packet["valid_end_s"])
        dur = float(packet["motion_duration_s"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return False
    return all(map(math.isfinite, (a, b, dur))) and 0 <= a < b and 0 <= dur <= b-a


def packet_covers_event(packet: dict | None, event_s: float) -> bool:
    if not packet_valid(packet):
        return False
    if not isinstance(event_s, (int, float)) or not math.isfinite(event_s):
        return False
    return (float(packet["valid_start_s"]) <= event_s and
            event_s + float(packet["motion_duration_s"]) <= float(packet["valid_end_s"]))
