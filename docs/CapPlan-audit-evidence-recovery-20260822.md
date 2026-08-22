# CapPlan PUDO audit evidence recovery and review workflow — 2026-08-22

## 1. What the current reports mean

`PUDO_AUDIT_MACHINE_TRIAGE=PASS` means only that the triage program completed successfully. It does **not** mean that the PUDO evidence is publication-ready.

The uploaded report snapshot contains 1118 unique PUDO audit sites:

| City | Sites | Source-prefilled | Source-complete | Visual-review | NEW_EVIDENCE_REQUIRED |
|---|---:|---:|---:|---:|---:|
| Boston | 272 | 268 | 0 | 0 | 272 |
| Pittsburgh | 127 | 0 | 0 | 0 | 127 |
| Vegas | 137 | 0 | 0 | 0 | 137 |
| Singapore | 582 | 0 | 0 | 0 | 582 |

Boston has curb-ramp evidence for 266/272 sites, but still has no populated paper-grade curb height, sidewalk width, deployment clearance, location-specific stopping legality, or intended entrance. Pittsburgh/Vegas/Singapore currently have no source-complete paper row at all.

Therefore both observed messages are expected:

- `render-audit-packets` used to render only `VISUAL_REVIEW_REQUIRED`; there were zero such rows.
- `review-source-complete-audits` can only review source-complete rows; there were zero such rows.

Setting `REVIEWER_ID` and `CONFIRM_SOURCE_REVIEW=YES` is deliberately insufficient to create missing evidence. Even after source-complete rows exist, each reviewed row must have an explicit `review_accept=true`, and entrance-candidate rows need `entrance_linkage_approved=true` before they can be stamped as human-reviewed.

## 2. What is safe to automate

Safe automation:

- schema/range/provenance validation;
- exact source-feature relationship recovery;
- deterministic source matching and rejection gates;
- preservation of authoritative source semantics;
- construction of candidate entrance links for review;
- evidence-gap reports and offline topology visualizations;
- compact upload bundles for independent review.

Unsafe automation that remains disabled:

- converting a nearby parking meter/taxi zone into general AV stopping legality;
- treating the nearest entrance/address as the intended service entrance;
- using sidewalk width as ramp/lift deployment clearance;
- inventing curb height from ramp rise or nominal geometry;
- converting missing real-world facts into positive labels solely to make the paper gate pass.

This boundary preserves the passenger-complete dataset semantics: missing evidence remains fail-closed/inconclusive rather than feasible by default.

## 3. Changes in this patch

### 3.1 Singapore Passenger Pickup Bay semantics

The LTA Passenger Pickup Bay normalizer now preserves the explicit source semantics as an authoritative static `general_passenger_loading` stopping relation. Physical interface fields remain unknown.

A recovery script converts already-downloaded legacy Passenger Pickup Bay candidate rows into `normalized/curb_regulations/singapore.jsonl`, so the original ZIP does not need to be downloaded again merely to recover legality semantics.

### 3.2 Direct source relation before nearest spatial matching

`prepare_pudo_audit_worklist.py` now parses the original `candidate_anchor_ids_<split>` values. If an anchor is `episode:candidate:<regulation_id>` and `<regulation_id>` exists in the authoritative regulation source, that direct relation is used first and annotated as:

- `legal_linkage_method=direct_feature_relation`
- `legal_relation_id=<regulation_id>`

Only when there is no direct relationship does the code fall back to a nearest authoritative regulation candidate. A nearest match is still reviewable evidence, not an explicit semantic relation.

### 3.3 Official-source refresh

`refresh-audit-public-sources` retries Boston PWD Cartegraph ADA-ramp/sidewalk layers using the current null-ID/pagination-safe ArcGIS downloader and fetches the Singapore LTA Train Station Exit layer as an authoritative entrance-candidate source.

Failures are non-fatal and are recorded with traceback in `$REPORTS/recommended_public_sources.json`.

### 3.4 Audit status is explicit

`audit-status` writes:

- `$REPORTS/pudo_audit_status.json`
- `$REPORTS/pudo_evidence_gap_manifest.csv`
- `$REPORTS/pudo_evidence_gap_manifest.json`

The status report explicitly distinguishes program `PASS` from evidence readiness.

### 3.5 Rendering no longer silently produces nothing useful

`CAP_AUDIT_RENDER_SCOPE` supports:

- `auto` (default): render visual-review rows; when there are none, render `NEW_EVIDENCE_REQUIRED` diagnostic rows instead;
- `visual`: visual-review rows only;
- `new_evidence`: evidence-gap rows only.

Outputs are under:

`$REPORTS/audit_packets/<city>/visual/`

or

`$REPORTS/audit_packets/<city>/evidence_gap/`

These are review aids, not measurements of missing numeric fields.

### 3.6 Upload-friendly review bundle

`audit-review-bundle` creates:

`$REPORTS/capplan_audit_review_bundle.zip`

It contains only small report JSON files, capped CSV samples, and capped PNG context packets. It does not include NPZ files or the full dataset.

## 4. Recommended continuation from the user's current state

No heavy rebuild is required. Reuse the current site catalogs and candidates.

```bash
cd /home/senzeyu2/code/CapPlan

export CAP_HOME=/home/senzeyu2/code/CapPlan
export CAP_DATA=/data0/senzeyu2/dataset/CapPlan/data
export DATA_ROOT="$CAP_DATA"
export CONFIG="$CAP_HOME/configs/abilitybench_nuplan_real_data0.yaml"
export EXT="$CAP_DATA/external"
export REPORTS="$EXT/reports"
```

First record the current state:

```bash
bash scripts/build_abilitybench_data0_20260817.sh audit-status
```

Refresh only the official sources relevant to this audit recovery:

```bash
bash scripts/build_abilitybench_data0_20260817.sh refresh-audit-public-sources
```

Then recover source semantics and rerun only the cheap audit stages:

```bash
bash scripts/build_abilitybench_data0_20260817.sh recover-audit-evidence
```

Inspect:

```bash
jq '{
  totals,
  ready_for_human_source_review,
  paper_evidence_ready,
  cities
}' "$REPORTS/pudo_audit_status.json"
```

Render 100 diagnostic/review rows per city:

```bash
export CAP_AUDIT_RENDER_SCOPE=auto
export CAP_AUDIT_RENDER_MAX_ROWS=100
export CAP_AUDIT_RENDER_RADIUS_M=120

bash scripts/build_abilitybench_data0_20260817.sh render-audit-packets
```

Build a compact upload bundle:

```bash
export CAP_AUDIT_BUNDLE_MAX_ROWS=100
export CAP_AUDIT_BUNDLE_MAX_IMAGES=24
bash scripts/build_abilitybench_data0_20260817.sh audit-review-bundle

ls -lh "$REPORTS/capplan_audit_review_bundle.zip"
```

Upload `capplan_audit_review_bundle.zip` for an independent review before stamping any row as human-reviewed.

## 5. When to run `review-source-complete-audits`

Do **not** run it merely because `REVIEWER_ID` is set. Run it only after:

1. `pudo_audit_status.json` shows source-complete/visual-review candidates;
2. the relevant rows have actually been inspected;
3. each approved row has `review_accept=true`;
4. any nearest/candidate entrance that is being promoted has `entrance_linkage_approved=true`.

Then:

```bash
export REVIEWER_ID="yusenze"
export CONFIRM_SOURCE_REVIEW=YES
bash scripts/build_abilitybench_data0_20260817.sh review-source-complete-audits
bash scripts/build_abilitybench_data0_20260817.sh import-source-complete-audits
```

Rows that remain `NEW_EVIDENCE_REQUIRED` must **not** be routed through this command. They need a better source, photograph/field observation, or an explicitly reported synthetic/simulated evidence protocol. If the paper intends real-world audited labels, fabricating these values is not acceptable.

## 6. What to upload for subsequent review

Preferred first upload:

```text
$REPORTS/capplan_audit_review_bundle.zip
```

If a targeted audit requires more coverage, increase the caps or remove them:

```bash
export CAP_AUDIT_BUNDLE_MAX_ROWS=0
export CAP_AUDIT_BUNDLE_MAX_IMAGES=0
bash scripts/build_abilitybench_data0_20260817.sh audit-review-bundle
```

Use unlimited images only if the resulting ZIP is practically uploadable. A better workflow is normally 100–300 rows/images per review batch.

For a data-level sanity check after evidence is imported and paper PUDO evidence is rebuilt, additionally package only reports, small JSONL samples and statistics rather than all NPZ/graphs.

## 7. Expected outcome of this patch

The patch is expected to reduce **recoverable** missingness, especially:

- Singapore stopping-legality evidence for PUDO anchors that originated from the LTA Passenger Pickup Bay source;
- Singapore entrance candidates around LTA Train Station Exit features;
- Boston width/ramp evidence if the PWD Cartegraph retry succeeds.

It is **not** expected to make all 1118 sites source-complete. The current source inventory does not prove all publication-critical curb height, deployment clearance, stopping legality, and intended-entrance facts in Pittsburgh/Vegas and many Boston/Singapore sites. Remaining gaps are real evidence acquisition work, not a software bug.
