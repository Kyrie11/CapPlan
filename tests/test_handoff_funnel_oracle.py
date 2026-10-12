"""Reference-model tests: causal observation and irreversible budget accounting."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from capplan.data.handoff_schema import validate_temporal_overlay
from capplan.handoff.finite_oracle import FiniteHandoffOracle, evaluate_baselines


def fixture(budget=200, observation_delay=5, event_time=90, clear_prob=.5):
    return {
      'overlay_id':'toy', 'episode_id':'toy','phase':'pickup',
      'initial_execution_state':{'timestamp_s':0,'passenger_anchor':'O','vehicle_anchor':'V',
                                 'walked_or_rolled_m':0},
      'capability_budget':{'distance_budget_m':budget,'wait_budget_s':600},
      'candidate_handoffs':[{'anchor_id':'A','direct_passenger_distance_m':130,
                              'from_a_to_b_distance_m':120},
                             {'anchor_id':'B','direct_passenger_distance_m':170}],
      'world_support':[{'world_id':'clear','probability':clear_prob,
                         'availability_trace':{'A':[[0,True]],'B':[[0,True]]},
                         'evidence_class':'controlled'},
                        {'world_id':'blocked','probability':1-clear_prob,
                         'availability_trace':{'A':[[0,True],[event_time,False]],'B':[[0,True]]},
                         'evidence_class':'controlled'}],
      'observation_model':{'availability_observation_delay_s':observation_delay,
                            'instruction_acknowledgement_delay_s':3,
                            'scheduled_observation_request_s':90},
      'handoff_confirmation':{'requires_explicit_confirmation':True},
      'provenance':{'dynamic_event':'controlled_not_observed'},
    }


class HandoffOracleTests(unittest.TestCase):
    def test_causal_exact_and_baselines(self):
        overlay=fixture()
        validate_temporal_overlay(overlay)
        oracle=FiniteHandoffOracle(overlay)
        results=evaluate_baselines(oracle)
        self.assertTrue(results['exact_finite_support']['world_support_guaranteed'])
        self.assertEqual(results['exact_finite_support']['weighted_candidate_success_rate'],1)
        self.assertEqual(results['verify_then_guide']['weighted_candidate_success_rate'],1)
        self.assertEqual(results['robust_fixed_anchor']['weighted_candidate_success_rate'],1)
        self.assertAlmostEqual(results['actual_ledger_myopic']['weighted_candidate_success_rate'],0.5)
        self.assertEqual(results['reset_and_replan']['budget_breach_worlds'],1)
        self.assertEqual(results['actual_ledger_myopic']['budget_breach_worlds'],0)
        self.assertEqual(results['exact_finite_support']['policy']['action'],'verify_before_guidance')
        self.assertAlmostEqual(results['exact_finite_support']['weighted_burden_m'],150)

    def test_verify_does_not_predict_future(self):
        oracle=FiniteHandoffOracle(fixture(event_time=150))
        # Scheduled verification at t=90 reveals A at 95, not a future event at 150.
        self.assertEqual(oracle.status('blocked','A',95), True)
        self.assertFalse(oracle.status('blocked','A',151))
        self.assertEqual(oracle.verify_time(oracle.initial),95)

    def test_no_hidden_world_event_time_leakage(self):
        a=fixture(event_time=100)
        b=fixture(event_time=800)
        for overlay in (a,b):
            overlay['observation_model'].pop('scheduled_observation_request_s')
        self.assertEqual(FiniteHandoffOracle(a).verify_time(FiniteHandoffOracle(a).initial),5)
        self.assertEqual(FiniteHandoffOracle(b).verify_time(FiniteHandoffOracle(b).initial),5)

    def test_exogenous_late_schedule_is_declared(self):
        overlay=fixture(event_time=150)
        overlay['observation_model']['scheduled_observation_request_s']=152
        self.assertEqual(FiniteHandoffOracle(overlay).verify_time(FiniteHandoffOracle(overlay).initial),157)

    def test_actual_ledger_and_budget_monotonicity(self):
        for budget in (150,180,200,250,300):
            res=evaluate_baselines(FiniteHandoffOracle(fixture(budget=budget)))
            # Frozen model's sound conservative fallback is allowed only if B
            # is reachable from the actual origin under that particular budget.
            if budget < 170:
                self.assertFalse(res['exact_finite_support']['world_support_guaranteed'])
            else:
                self.assertTrue(res['exact_finite_support']['world_support_guaranteed'])

    def test_future_event_occupies_boarding_window(self):
        oracle=FiniteHandoffOracle(fixture(event_time=140))
        self.assertTrue(oracle.status('blocked','A',133))
        self.assertFalse(oracle.available_for_boarding('blocked','A',133))

    def test_waiting_budget_blocks_long_verification(self):
        o=fixture()
        o['capability_budget']['wait_budget_s']=40
        r=evaluate_baselines(FiniteHandoffOracle(o))
        self.assertTrue(r['exact_finite_support']['world_support_guaranteed'])  # direct B
        self.assertEqual(r['exact_finite_support']['policy']['action'],'guide:B')
        self.assertFalse(r['verify_then_guide']['world_support_guaranteed'])
        self.assertEqual(r['verify_then_guide']['weighted_false_commit_rate'],None)

    def test_equal_support_information_is_not_clairvoyance(self):
        overlay=fixture()
        overlay['world_support'][1]['availability_trace']['A']=[[0,True]]
        oracle=FiniteHandoffOracle(overlay)
        policy,_=oracle.solve()
        self.assertEqual(policy.action,'guide:A')
        self.assertTrue(all(oracle.simulate(policy,w)['candidate_service_success'] for w in oracle.worlds))

if __name__=='__main__':unittest.main()
