from capplan.data.schemas import ViolationRecord
from capplan.planning.certificates import select_certificate


def test_failure_certificate_matches_most_severe_normalized_violation():
    v = [
        ViolationRecord("ride", "t2", "motion_exposure", -0.2, "trajectory", 0.9),
        ViolationRecord("access", "t1", "path_width_m", -0.4, "map", 0.7),
        ViolationRecord("wait", "t3", "map_confidence", -0.4, "map", 0.95),
    ]
    c = select_certificate("e", "p", v)
    assert c.transition_id == "t3"  # tie on margin, higher confidence wins
