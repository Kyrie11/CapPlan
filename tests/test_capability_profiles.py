from capplan.data.capability_contracts import default_contract, make_profile, profile_to_contract, sample_contracts_with_pairs
from capplan.semantics.capability_compiler import CapabilityCompiler, stricter_or_equal


def test_profile_to_contract_compilation_has_required_phase_scopes():
    c = profile_to_contract(make_profile("e:p0", "manual_wheelchair", seed=1))
    by_res = {x.resource_name: x for x in c.clauses}
    assert "access" in by_res["access_distance_m"].phase_scope
    assert "egress" in by_res["egress_distance_m"].phase_scope
    assert any(g.logic == "any_of" for g in c.groups)


def test_counterfactual_pair_is_monotonic_for_ordered_resources():
    contracts, pairs = sample_contracts_with_pairs("e", 2, seed=1)
    assert pairs and pairs[0].relation == "stricter_or_equal"
    assert stricter_or_equal(contracts[0], contracts[1])


def test_door_side_clause_uses_required_side_not_true_boolean():
    c = default_contract("p")
    door = next(x for x in c.clauses if x.resource_name == "door_side")
    assert door.threshold in {"left", "right", "either", "both"}
    assert door.threshold is not True


def test_compile_returns_G_B_I_U_Z():
    compiled = CapabilityCompiler().compile(default_contract("p"))
    assert compiled.G_p and compiled.B_p and compiled.I_p and compiled.U_p and compiled.Z_p
