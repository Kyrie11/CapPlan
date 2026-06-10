from capplan.evaluation.metrics import compute_all_metrics


def test_all_metrics_present_and_basic_values():
    episodes = [
        {"collision": False, "completed_route_m": 100, "planned_route_m": 100, "route_completion": 1.0, "rule_violation": False, "travel_time_s": 10, "vehicle_distance_m": 110, "shortest_route_m": 100, "passenger_complete": True, "phase_accepted": True, "capability_margins": {"a": 0.2}, "first_last_meter_feasible": True, "boarding_alighting_feasible": True, "motion_exposure": 1, "motion_budget": 2, "motion_violation": False, "budget_residuals": {"a": 0.2}, "inconclusive": False, "tt_cap_s": 10, "tt_std_s": 8},
        {"collision": False, "completed_route_m": 100, "planned_route_m": 100, "route_completion": 1.0, "rule_violation": False, "travel_time_s": 12, "vehicle_distance_m": 100, "shortest_route_m": 100, "passenger_complete": False, "phase_accepted": False, "capability_margins": {"a": -0.1}, "first_last_meter_feasible": False, "boarding_alighting_feasible": False, "motion_exposure": 3, "motion_budget": 2, "motion_violation": True, "budget_residuals": {"a": -0.1}, "inconclusive": True, "certificate": {"phase": "access", "resource_type": "a", "evidence_source": "map", "signed_margin": -0.1}, "oracle_certificate": {"phase": "access", "resource_type": "a", "evidence_source": "map", "signed_margin": -0.2}, "tt_cap_s": 12, "tt_std_s": 8},
    ]
    m = compute_all_metrics(episodes, [{"responsive": True}])
    for key in ["CR", "RC", "TRV", "TT", "DR", "PCR", "TSPIR", "PAR", "CVR", "CSM", "FLF", "BAF", "MER", "MVR", "SBR", "IR", "DF", "SME", "CRsp", "ECA"]:
        assert key in m
    assert m["PCR"] == 0.5
    assert m["CRsp"] == 1.0
