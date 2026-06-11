from capplan.evaluation.metrics import compute_all_metrics, diagnostic_fidelity, efficiency_cost_of_accommodation, first_last_meter_feasibility, boarding_alighting_feasibility, traffic_safe_passenger_incomplete_rate


def test_tspir_counts_vehicle_success_passenger_failure():
    rows = [{"traffic_safe": True, "route_completion": 1.0, "passenger_complete": False}]
    assert traffic_safe_passenger_incomplete_rate(rows) == 1.0


def test_df_uses_oracle_not_self_certificate():
    row = {"passenger_complete": False, "certificate": {"phase": "access", "resource_type": "slope", "evidence_source": "map"}, "oracle_certificate": {"phase": "board", "resource_type": "door_side", "evidence_source": "vehicle"}}
    assert diagnostic_fidelity([row]) == 0.0


def test_eca_nonnegative_or_nan_when_cap_plan_faster_due_to_different_baseline():
    assert efficiency_cost_of_accommodation([{"tt_cap_s": 8, "tt_std_s": 10}]) == 0.0


def test_flf_baf_not_gated_by_passenger_complete():
    row = {"passenger_complete": False, "first_last_meter_feasible": True, "boarding_alighting_feasible": True}
    assert first_last_meter_feasibility([row]) == 1.0
    assert boarding_alighting_feasibility([row]) == 1.0


def test_crsp_uses_counterfactual_pairs():
    m = compute_all_metrics([], [{"responsive": True}, {"responsive": False}])
    assert m["CRsp"] == 0.5
