# CapPlan hybrid realism-v4 reviewfix1 (2026-08-24)

This note records the post-build audit performed against the uploaded `capplan_hybrid_review_bundle.zip` and the changes required before freezing AbilityBench-AV hybrid train/val/test.

## Current uploaded run state

The uploaded review bundle is an **intermediate realism-v4 run**, not a finished hybrid dataset. The pipeline identity is the expected realism-v4 checkout and the corrected base accessibility graph/PUDO materialization completed, but the bundled hybrid graph/PUDO/ready artifacts are older than the realism-v4 identity timestamp and the final per-city/merged hybrid dataset audits are absent.

The corrected base graph logs use `20260823_dem_evidence_v5` and report `dem_point_nodes_inserted_total=0`: high-resolution DEM is elevation evidence for semantic pedestrian nodes, never a graph vertex. Singapore coarse elevation remains excluded from sidewalk-scale grade.

## Reviewfix1 changes

1. `paper_safe` CASA loading is fail-closed for passenger-conditioned edge labels. Every retained `(transition_id, passenger_id)` pair must have exactly one `y_e,p`; the loader no longer falls back to passenger-independent `z_e`.
2. CASA uncertainty calibration now follows the paper loss: each typed-demand residual is covered by `beta_tau * sigma_tau` under the demand mask. It no longer calibrates resource uncertainty against the binary edge-label residual.
3. Hybrid PUDO static physical evidence is canonicalized across train/val/test peers for the same `hybrid_physical_site_key`. Dynamic blockage/availability remains episode/time dependent.
4. A new `audit_hybrid_site_consistency.py` hard-checks cross-split static-site consistency while excluding dynamic fields.
5. `audit_hybrid_benchmark.py` v3 hard-checks passenger-edge-label completeness/uniqueness, complete accepted lifecycle skeletons, certificate sign/confidence/diagnostic fields, T4 monotonicity, PUDO eligibility, OD/time/vehicle consistency, and outcome coverage.
6. Hybrid graph reports now expose per-field provenance-source counts and explicit high-resolution-DEM-derived slope counts/rates.
7. `build_hybrid_review_bundle.py` v2 distinguishes `PASS`, `INCOMPLETE`, and `FAIL`, anchors freshness to the latest pipeline identity, checks expected artifact versions, and requires all per-city/merged audits and validation manifests. The shell `hybrid-review-bundle` stage now uses `--require_complete`; it still writes a diagnostic ZIP before returning non-zero on an incomplete run.
8. A `hybrid-realism-resume-post-base` stage reuses the already-expensive corrected v5 base graph/PUDO outputs and rebuilds site/audit/hybrid/service/dataset/semantic-audit stages.

## Recommended continuation on the server

After deploying reviewfix1 and verifying `version`, use:

```bash
bash scripts/build_abilitybench_data0_20260817.sh hybrid-realism-resume-post-base \
  2>&1 | tee "$REPORTS/commands/hybrid_realism_reviewfix1_resume.master.log"

bash scripts/build_abilitybench_data0_20260817.sh hybrid-review-bundle
```

Do not rerun scene extraction, downloads, or corrected realism-v4 base graph/PUDO unless their files have been deleted/corrupted. The review bundle must report `HYBRID_REVIEW_BUNDLE=PASS` before the hybrid dataset is frozen for training.

## Paper-claim boundary

The resulting dataset is suitable for the symbolic capability compiler/service automaton/typed resource oracle/TSBS stack and the current relation-aware CASA transition surrogate when all semantic audits pass. The current `hgt`/`rgcn` implementation is not a true heterogeneous service-graph message-passing network. A literal implementation of the paper's CASA-Net claim still requires per-episode heterogeneous graph tensors, raw dynamic-agent/scene context, capability-conditioned typed relations, and real HGT/R-GCN/GNN message passing. Likewise the current ride-motion field is an ISO-inspired benchmark surrogate, not a complete ISO-2631 trajectory model.
