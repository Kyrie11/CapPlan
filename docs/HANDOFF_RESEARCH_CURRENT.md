# Handoff research — stable entry point

Current entry point: `python scripts/handoff_study.py --help`. **Do not create new
`scripts/*_vN.py` experiment commands.** Old `_vN` scripts are frozen for
replication, not active research APIs. Remove or archive them only after checking
all Python imports, historical shell SOPs, hashes and regression tests.

This module implements research **checks**, not a production autonomous driving
planner. Its evidence distinctions are strict:

- `ENCODED_GRAPH_ONLY`: directional as encoded / symmetrized sensitivity only;
  missing reverse arcs might be a serialization convention, not physical one-way.
- `CARLA_TOWN_TRAJECTORY_SURROGATE_ONLY`: native CARLA Town trajectory,
  goal-zone scripted braking, no real curb legality, deployable ramp or boarding.
- `DECLARED_FINITE_MODEL`: one scheduled observation / one fixed or contingent
  site-start decision; not the generic multi-observation POMDP or AAAI/ICRA shield.
- `UNKNOWN_MISSING_EVIDENCE`: never automatically becomes a physically feasible
  service or an infeasibility certificate.

Common workflow:

```bash
python scripts/handoff_study.py legacy-inventory --output "$STUDY/legacy_inventory.json"
python scripts/handoff_study.py dataset-inventory --outputs-root "$DATA_ROOT/outputs" --output "$STUDY/outputs_inventory.json" --count-episodes
python scripts/handoff_study.py carla-triplets --study "$PREVIOUS_CARLA" --output "$STUDY/carla_triplets.json"
python scripts/handoff_study.py site-evidence --packets "$PACKETS" --output "$STUDY/site_evidence.json"
python scripts/handoff_study.py directed-remine --dataset-dir "$TEST_DATASET" --pairs "$PAIRS" --output "$STUDY/directed_remine.jsonl" --per-city 8 --third-options
python scripts/handoff_study.py temporal-check --cases examples/handoff_temporal_v12.jsonl --output "$STUDY/temporal.jsonl"
python scripts/handoff_study.py observation-benchmark --output "$STUDY/observation_benchmark.json" --replicates 500
python scripts/handoff_study.py review-pack --study "$STUDY" --output "$STUDY/handoff_research_review.zip"
```

Directed re-mining is bounded-memory for candidate selection. Checking third PUDOs
requires scanning `pudo_anchors.jsonl` and is somewhat more expensive. No
script edits original files under `datasets/` or `prepared/`.

CARLA client fix: `run_carla_controlled_handoff_v5.py` now records whether a B
redirect actually happened, holds an early A stop until the scheduled redirect,
releases the A braking latch, and resets stop-zone/dwell statistics for B.
This is NOT a certified collision-free stop or a real vehicle-to-person handoff.

For publication, use the official nuPlan splits as the development baseline and
a separate held-out site test. Fully label `observed / derived / simulated` at
the field and time-of-observation levels. Do not give POMDP baselines a smaller
observation/action space or omit human-vehicle interface predicates.
