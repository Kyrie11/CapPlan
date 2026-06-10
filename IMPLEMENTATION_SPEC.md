# CapPlan Implementation Specification

This document maps the paper concepts to concrete files.

## Passenger-complete service semantics

* `capplan/semantics/service_automaton.py` defines phases `origin, access, wait, board, ride, alight, egress, destination` and actions `access, wait, board, ride, alight, egress, replan`.
* `CapPlanPlanner.plan` returns `PlannerResult(success=True, skeleton=...)` only when typed safe-budget search reaches `destination` and all hard clauses satisfy the typed algebra.

## Capability semantic type system

* `CapabilityClause` and `CapabilityContract` live in `capplan/data/schemas.py`.
* `CapabilityCompiler` validates structured contracts against `ResourceRegistry`, creates phase-scoped guards, budget tables, interface tables, uncertainty tables, and CASA tokens.

## Resource registry and typed algebra

* `capplan/semantics/resource_registry.py` registers cumulative, upper, lower, categorical, and probabilistic resources.
* `capplan/semantics/typed_resource_algebra.py` implements:
  * `update`
  * `satisfy`
  * `dominates`
  * `signed_margin`
  * conservative evidence conversion.

Update rules:

* cumulative: `R' = R + evidence`
* upper bottleneck: `R' = max(R, evidence)`
* lower bottleneck: `R' = min(R, evidence)`
* categorical: predicate conjunction
* probabilistic: union-bound failure-risk composition

## CASA construction and prediction

* `capplan/planning/transition_generator.py` generates access, wait, board, ride, alight, egress, and replan transitions.
* `capplan/models/casa_net.py` exposes the CASA-Net input/output contract.
* `capplan/models/predictors.py` supplies a deterministic baseline predictor with typed evidence, uncertainty, availability, completion value, and phase belief.

## Typed safe-budget search

* `capplan/planning/typed_safe_budget_search.py` implements labels `(anchor, phase, resource_ledger, cost, history)`.
* Expansion requires lifecycle legality, dynamic availability, interface/resource checks, conservative evidence, and active capability satisfaction.
* Dominance is evaluated only for same anchor and phase.

## Diagnostic certificates

* `capplan/planning/certificates.py` implements reproducible certificate selection.
* Search emits `ViolationRecord` objects for rejected transitions and returns the most severe normalized signed margin with tie-breakers.

## Dataset construction

* `capplan/data/nuplan_adapter.py` exposes a nuPlan-compatible scenario iterator with synthetic fallback.
* `capplan/data/accessibility_layer.py` generates accessibility graphs.
* `capplan/data/pudo_interface_layer.py` generates PUDO anchors and vehicle-interface records.
* `capplan/data/capability_contracts.py` creates realistic contracts and same-scene counterfactuals.
* `capplan/data/label_oracle.py` computes labels using the same typed resource algebra.
* `scripts/build_dataset.py` writes all dataset files.

## Evaluation

* `capplan/evaluation/metrics.py` implements CR, RC, TRV, TT, DR, PCR, TSPIR, PAR, CVR, CSM, FLF, BAF, MER, MVR, SBR, IR, DF, SME, CRsp, and ECA.
* `capplan/evaluation/closed_loop.py` runs mock closed-loop evaluation and can be swapped for nuPlan simulation.
* `capplan/evaluation/ablations.py` defines full and ablated variants.
* `capplan/evaluation/experiment_runner.py` writes metrics tables.

## Known engineering limits

The current CASA-Net class is a modular deterministic baseline rather than a trained heterogeneous graph transformer.  It preserves the required interface so a learned model can be substituted without altering the symbolic semantics or planner.  The default trajectory refinement is a deterministic placeholder when nuPlan closed-loop simulation is unavailable.  The dataset adapter detects nuPlan but does not ship nuPlan-specific private data loaders.
