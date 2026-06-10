from capplan.data.capability_contracts import default_contract
from capplan.data.schemas import CapabilityClause, CapabilityContract
from capplan.semantics.capability_compiler import CapabilityCompiler, stricter_or_equal


def test_compiler_groups_interfaces_and_budgets():
    compiled = CapabilityCompiler().compile(default_contract("p"))
    assert "ramp" in compiled.interfaces
    assert "access_distance_m" in compiled.budgets
    assert compiled.tokens


def test_stricter_contract_order():
    weak = CapabilityContract("w", [CapabilityClause("access_distance_m", ["access"], "<=", 200.0, "cumulative")])
    strict = CapabilityContract("s", [CapabilityClause("access_distance_m", ["access"], "<=", 100.0, "cumulative")])
    assert stricter_or_equal(weak, strict)
    assert not stricter_or_equal(strict, weak)
