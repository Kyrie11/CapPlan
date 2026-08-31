# CapPlan passenger-complete reviewfix7 — 2026-08-30

## Purpose

This patch is the final lightweight dataset-freeze correction after the reviewfix5/hotfix2 build. It does **not** rebuild nuPlan extraction, DEM/OSM/GIS, base accessibility graphs, or hybrid accessibility graph v3.

The uploaded 2026-08-30 reports show that train/val/test semantic construction completed, but the actual runtime checkout still used the reviewfix5 service-layer builder. As a result, the old short-OD fallback could move the passenger destination to a globally distant frontage node, producing destination-to-nuPlan-route distances above 1 km while the old semantic audit still returned PASS.

## Reviewfix7 semantics

1. `build_service_layer.py` uses `capplan_route_local_od_v2_20260830`.
2. Both origin and destination service anchors must remain within `service.max_entrance_route_distance_m` of the corresponding nuPlan route endpoint (default 250 m).
3. When the nominal 80 m passenger OD-separation target cannot be met by a route-local entrance/frontage alternative, the original route-anchored short OD is retained and explicitly marked `od_separation_target_met=false`; the code never uses a global farthest-node fallback.
4. Hybrid semantic audit v5 treats missing/excess route-anchor distance evidence as a hard error.
5. Passenger-complete distribution audit v2 is run automatically for train/val/test and reports base/strict-profile binding, failure diversity, OD tails, and monotonicity.
6. Review bundle v6 requires the new semantic and distribution reports and fixes the run-context self-stale false positive.
7. A reviewfix7 runtime guard hashes/validates the OD builder and all freeze-critical scripts before any dataset write.

## Reused upstream artifacts

The following are intentionally reused and should not be deleted or rebuilt:

- nuPlan DB inspection/index and scene extraction;
- downloaded/normalized OSM, GIS, DEM and georeference evidence;
- base accessibility graphs;
- hybrid accessibility graph v3 (`abilitybench_hybrid_accessibility_v3_20260825`), after reused-lineage preflight.

PUDO v7 and ready allowlists are cheap and are refreshed before rebuilding service requests, transition/oracle labels, city datasets and merged split datasets.

## Recommended server path

After deploying the full reviewfix7 source tree, run:

```bash
bash scripts/build_abilitybench_data0_20260817.sh reviewfix7-preflight
bash scripts/build_abilitybench_data0_20260817.sh hybrid-dataset-resume-reviewfix7
bash scripts/build_abilitybench_data0_20260817.sh hybrid-review-bundle
```

`HYBRID_REVIEW_BUNDLE=PASS` is a structural freeze requirement. A `PASSENGER_COMPLETE_DISTRIBUTION_AUDIT=WARN` is not automatically a build failure; inspect base-profile success and each strict capability axis before publication freeze. `FAIL` is a hard blocker.

## Current neural-model boundary

The dependency-light CASA backend is explicitly named `relation_mlp`. Historical `hgt`/`rgcn` names are compatibility aliases for the same relation-aware transition MLP family; they do not perform the heterogeneous graph message passing described by the paper. Use `--feature_policy paper_safe` for the current learned baseline. A publication claim of a true HGT/R-GCN CASA-Net requires a separate graph-sample/export and message-passing implementation.

## Closed-loop evaluation claim guard

`run_closed_loop_eval.py` and `run_ablations.py` do not execute nuPlan Hydra internally. They can consume externally generated `nuplan_vehicle_metrics.jsonl`; `--nuplan_sim_config` is recorded only as provenance. Publication-facing `--paper_mode` now fails closed unless real metrics are imported and the user explicitly acknowledges that episode-level imported metrics are post-hoc evidence rather than method-specific integrated closed-loop simulation. Final paper vehicle results should come from a real nuPlan simulation integration for the selected planner/PUDO decisions.
