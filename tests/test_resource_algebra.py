from capplan.data.schemas import CapabilityClause, ResourceEvidence
from capplan.semantics.resource_registry import DEFAULT_REGISTRY
from capplan.semantics.typed_resource_algebra import conservative_value, satisfy, signed_margin, update


def test_cumulative_resources_sum_correctly():
    rt = DEFAULT_REGISTRY.get("access_distance_m")
    s = {"access_distance_m": 0.0}
    s = update(s, ResourceEvidence("access_distance_m", "cumulative", 10.0), rt, beta=0.0)
    s = update(s, ResourceEvidence("access_distance_m", "cumulative", 5.0), rt, beta=0.0)
    assert s["access_distance_m"] == 15.0


def test_upper_bottleneck_resources_use_max():
    rt = DEFAULT_REGISTRY.get("slope")
    s = {"slope": 0.0}
    s = update(s, ResourceEvidence("slope", "upper", 0.04), rt, beta=0.0)
    s = update(s, ResourceEvidence("slope", "upper", 0.02), rt, beta=0.0)
    assert s["slope"] == 0.04


def test_lower_bottleneck_affordances_use_min():
    rt = DEFAULT_REGISTRY.get("path_width_m")
    s = {"path_width_m": float("inf")}
    s = update(s, ResourceEvidence("path_width_m", "lower", 1.4), rt, beta=0.0)
    s = update(s, ResourceEvidence("path_width_m", "lower", 1.1), rt, beta=0.0)
    assert s["path_width_m"] == 1.1


def test_categorical_resources_use_predicate_conjunction():
    rt = DEFAULT_REGISTRY.get("ramp")
    s = {"ramp": True}
    s = update(s, ResourceEvidence("ramp", "categorical", True), rt)
    s = update(s, ResourceEvidence("ramp", "categorical", False), rt)
    assert bool(s["ramp"]) is False
    assert s["ramp"].observed is False
    assert s["ramp"].required is True


def test_conservative_evidence_direction_is_correct():
    upper = DEFAULT_REGISTRY.get("slope")
    lower = DEFAULT_REGISTRY.get("path_width_m")
    assert conservative_value(0.05, 0.01, upper, beta=2.0) == 0.07
    assert conservative_value(1.2, 0.1, lower, beta=2.0) == 1.0


def test_signed_margin_smaller_and_larger():
    c1 = CapabilityClause("slope", ["access"], "<=", 0.1, "upper")
    c2 = CapabilityClause("path_width_m", ["access"], ">=", 1.0, "lower")
    assert signed_margin({"slope": 0.05}, c1) > 0
    assert signed_margin({"path_width_m": 0.8}, c2) < 0
