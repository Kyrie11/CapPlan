"""Candidate PUDO anchors and vehicle-interface metadata."""
from __future__ import annotations

import random
from typing import List

from capplan.data.schemas import PUDOAnchor, VehicleInterface


def synthetic_pudo_anchors(episode_id: str, seed: int = 0, n: int = 4) -> List[PUDOAnchor]:
    rng = random.Random(seed + 11)
    anchors: List[PUDOAnchor] = []
    for i in range(n):
        anchors.append(PUDOAnchor(
            anchor_id=f"pudo_{i}",
            episode_id=episode_id,
            x=20.0 + 20.0 * i,
            y=rng.uniform(-2, 2),
            side="right" if i % 2 == 0 else "left",
            legal_stop=True,
            curb_height_m=0.04 + 0.03 * i,
            sidewalk_width_m=1.6 - 0.16 * i,
            deployment_clearance_m=1.8 - 0.22 * i,
            blockage_risk=0.03 + 0.05 * i,
            map_confidence=0.95 - 0.04 * i,
            lighting="day",
            shelter=i % 2 == 0,
        ))
    return anchors


def synthetic_vehicle_interface(episode_id: str, vehicle_id: str = "veh_0", accessible: bool = True) -> VehicleInterface:
    return VehicleInterface(
        vehicle_id=vehicle_id,
        episode_id=episode_id,
        door_side="right" if accessible else "left",
        ramp=accessible,
        lift=False,
        low_floor=accessible,
        door_width_m=1.05 if accessible else 0.75,
        deployment_clearance_m=1.6 if accessible else 0.8,
        notification_modes=["visual", "audio", "app"] if accessible else ["visual"],
        dwell_time_s=60.0 if accessible else 30.0,
        kneeling=accessible,
    )
