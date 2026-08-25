# CapPlan hybrid realism-v4 reviewfix2 (2026-08-25)

## Root cause of the reviewfix1 FAIL

The reviewfix1 resume completed site catalogs, evidence recovery and all 12 hybrid PUDO v4 city/split overlays, then stopped at `hybrid_site_consistency`. The audit saw 530,768 PUDO rows, 4,608 physical-site keys and 3,733 conflicts. Every reported conflict type was `side`; the PUDO builders likewise reported only `conflict:side`. No curb-height, sidewalk-width, deployment-clearance or curb-ramp static conflict was reported.

`side` is produced by `build_pudo_evidence.py::RouteIndex.distance_and_side()`: it is the candidate point's left/right relation to the *directed route segment for the current episode*. It is therefore a service-approach relation, not immutable curb geometry. The same physical curb may legitimately be left for one route direction and right for a reverse approach. Treating this field as physical-site static evidence was the reviewfix1 bug.

## reviewfix2 policy

- Immutable site facts remain cross-split audited: curb height, sidewalk width, deployment clearance, curb-ramp state, lighting and shelter.
- Dynamic blockage/confidence remains episode/time specific.
- `side` remains required for board/alight interface checks, but is explicitly excluded from physical-site static transfer and static consistency failure. Its variation is reported informationally.
- Strict paper-site evidence remains incomplete; this is not a hybrid blocker because simulated typed-resource truth is allowed when provenance is explicit.
- The fresh reviewfix1 hybrid PUDO v4 files are numerically reusable, but the recommended resume refreshes this cheap overlay once so the PUDO reports no longer carry obsolete `conflict:side` diagnostics. Base graph/PUDO, site catalogs and source-prefill outputs are still reused.

## Minimal continuation

```bash
bash scripts/build_abilitybench_data0_20260817.sh version \
  | tee "$REPORTS/commands/manual_pipeline_identity_before_reviewfix2.txt"

bash scripts/build_abilitybench_data0_20260817.sh hybrid-realism-resume-post-pudo \
  2>&1 | tee "$REPORTS/commands/hybrid_realism_reviewfix2_resume.master.log"

bash scripts/build_abilitybench_data0_20260817.sh hybrid-review-bundle \
  2>&1 | tee "$REPORTS/commands/hybrid_review_bundle.reviewfix2.log"
```

The dataset is freeze-ready only if the final line is `HYBRID_REVIEW_BUNDLE=PASS` and the final semantic audits pass.
