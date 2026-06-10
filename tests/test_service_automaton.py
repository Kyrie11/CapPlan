from capplan.semantics.service_automaton import ServiceAutomaton


def test_service_automaton_requires_lifecycle():
    a = ServiceAutomaton()
    assert a.legal("origin", "access", "access")
    assert not a.legal("origin", "ride", "ride")
    assert a.accept("destination")
