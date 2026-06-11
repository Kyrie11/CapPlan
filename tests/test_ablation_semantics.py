from dataclasses import asdict
from capplan.evaluation.ablations import ablation_config
from capplan.planning.planner import PlannerConfig


def test_ablation_only_changes_intended_config_fields():
    base = PlannerConfig()
    no_casa = ablation_config("no_casa_net_transitions")
    changed = {k for k, v in asdict(no_casa).items() if v != asdict(base)[k]}
    assert changed == {"no_casa_net_transitions"}


def test_no_casa_net_keeps_symbolic_constraints():
    no_casa = ablation_config("no_casa_net_transitions")
    assert no_casa.no_casa_net_transitions
    assert not no_casa.no_typed_resource_ledger
    assert not no_casa.soft_only_capability
