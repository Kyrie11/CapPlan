# CapPlan hybrid dataset reviewfix5 — 2026-08-28

## Scope

This continuation is for the state in which hybrid accessibility graph v3 is already complete for all 12 city/split combinations, PUDO v5/site-consistency v2 were previously valid, and `hybrid-build` failed after the Boston train city dataset was materialized.

It intentionally does **not** redownload external data, re-index nuPlan, re-extract scenes, or rebuild the expensive hybrid accessibility graph v3.

## Root cause of the uploaded reviewfix4 FAIL

The current `prepare_abilitybench_external.py` invokes:

```text
scripts/diagnose_capplan_outputs.py ... --fast_graph_scan
```

but the uploaded `diagnose_capplan_outputs.py` used `args.fast_graph_scan` without registering the argparse option. The Boston train dataset and its fast quality audit completed; the pipeline then exited immediately with `unrecognized arguments: --fast_graph_scan`. Pittsburgh/Vegas/Singapore train, val/test and merges therefore never ran.

The old review bundle was anchored to `hybrid_run_context.reviewfix3.json`, so historical `hybrid_dataset_audit.*` files could be treated as current even though the reviewfix4 dataset-only rerun started later. reviewfix5 creates a dedicated dataset-run context and only permits graph v3 to be intentionally reused from the older lineage.

## Data-quality fixes

1. **Oracle origin fix retained.** The new Boston quality report has 41 skeletons and no `origin` certificate phase, confirming that the hard-coded origin-anchor problem is fixed.
2. **PUDO selection is now route-aware.** Hybrid PUDO JSONL is emitted in deterministic anchor-id order. Taking `rows[:4]` was therefore arbitrary. Candidate transitions now choose the compact PUDO set using exact pedestrian shortest-path distance, prioritizing graph-connected legal/unblocked anchors while retaining a challenge candidate when appropriate. The rule is passenger-independent and preserves same-scene counterfactuals.
3. **Dynamic blockage provenance is explicit.** PUDO v6 records existing nuPlan-agent-history blockage risk as derived evidence; source-uncertain existing scores are conservatively declared simulated. This closes the previous one-missing-core-provenance-per-PUDO audit failure without pretending dynamic scores are municipal measurements.
4. **No threshold relaxation.** Access distance, width, slope, confidence, interface and capability limits are not loosened to manufacture positives. The final quality gate still rejects a benchmark whose passenger-complete skeleton or passenger-edge positives remain below the existing 5% sparsity guard.

## Performance fixes without semantic relaxation

- Exact access/egress routing uses two single-source Dijkstra trees per episode rather than up to eight separate shortest-path searches.
- PUDO candidate ranking reuses those exact shortest-path trees.
- Identical alight/egress/destination suffix transitions are emitted once per drop-off instead of once per pickup/drop-off pair. With the default four pickup/four drop-off candidates, this reduces the canonical transition set from roughly 72 to 48 transitions per episode while preserving all distinct ride alternatives and typed ledgers.
- If PUDO attachment does not modify prepared graph topology, the dataset reuses byte-identical prepared node/edge JSONLs with hardlinks (copy fallback) instead of serializing the entire graph again.
- Merged four-city datasets also hardlink immutable graph files when possible.
- QA/diagnostics continue using exact `.audit.json` sidecars instead of reparsing all graph edges.
- The independent oracle now applies the same typed-resource/cost dominance relation used by TSBS to prune dominated replan-cycle labels.

## Required server sequence

```bash
conda activate capplan

export CAP_HOME=/home/senzeyu2/code/CapPlan
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export CAP_DATA="$DATA_ROOT"
export CONFIG="$CAP_HOME/configs/abilitybench_nuplan_real_data0.yaml"
export EXT="$DATA_ROOT/external"
export REPORTS="$EXT/reports"
export NUPLAN_DATA_ROOT="$DATA_ROOT/nuplan"
export NUPLAN_MAPS_ROOT="$DATA_ROOT/nuplan/maps"
export NUPLAN_MAP_VERSION=nuplan-maps-v1.0
export CAP_HYBRID_SEED=20260822
export CAP_HYBRID_MIN_PUDOS=2
export CAP_SITE_DISJOINT_MIN_SITES=2
export CAP_NUM_WORKERS=0
export CAP_GRAPH_CITY_JOBS=2
export CAP_PUDO_CITY_JOBS=4

mkdir -p "$REPORTS/commands"
cd "$CAP_HOME"

set -o pipefail
bash scripts/build_abilitybench_data0_20260817.sh reviewfix5-preflight \
  2>&1 | tee "$REPORTS/commands/manual_reviewfix5_preflight.txt"
```

Do not continue unless the preflight prints `CAPPLAN_REVIEWFIX5_RUNTIME_GUARD=PASS`, pipeline version `abilitybench_data0_realism_v4_reviewfix5_20260828`, and `CAPPLAN_REVIEWFIX5_DIAGNOSE_FAST_GRAPH_SCAN=present`.

Then:

```bash
ps -ef | grep -E \
'prepare_abilitybench_external.py|build_dataset.py|build_hybrid_|build_abilitybench_data0_20260817.sh' \
| grep -v grep
```

After confirming no old writer is active:

```bash
set -o pipefail
bash scripts/build_abilitybench_data0_20260817.sh \
  hybrid-dataset-resume-reviewfix5 \
  2>&1 | tee \
  "$REPORTS/commands/hybrid_dataset_resume.reviewfix5.master.log"
```

This refreshes cheap PUDO v6 + site consistency + hybrid-ready allowlists, then rebuilds service/dataset/oracle/quality/semantic-audit/merge for train, val and test. It reuses hybrid graph v3.

Finally:

```bash
set -o pipefail
bash scripts/build_abilitybench_data0_20260817.sh \
  hybrid-review-bundle \
  2>&1 | tee \
  "$REPORTS/commands/hybrid_review_bundle.reviewfix5.log"
```

## Freeze criteria

`HYBRID_REVIEW_BUNDLE=PASS` is necessary but inspect the distributions before freezing. In particular:

- no `origin` failure concentration;
- passenger-complete skeleton rate and passenger-edge positive rate above the existing 5% extreme-sparsity guard, without relaxing capability thresholds;
- phase/resource diversity in failure certificates;
- T4 monotonic violation count = 0;
- exactly 8 same-scene/same-OD/same-time/same-vehicle capability requests per retained episode;
- PUDO core provenance missing = 0 and route-relative side semantics valid;
- graph v3 slope ranges remain plausible and graph reports remain PASS;
- merged train/val/test validation is valid.

## Upload after the rerun

Upload these files from the **same run**:

```text
$REPORTS/capplan_hybrid_review_bundle.zip
$REPORTS/commands/hybrid_dataset_resume.reviewfix5.master.log
$REPORTS/commands/hybrid_review_bundle.reviewfix5.log
$REPORTS/commands/hybrid_run_context.reviewfix5_dataset.json
$REPORTS/commands/reviewfix5_dataset_fix.sha256
```

If the main run stops early, also upload any newly generated:

```text
$REPORTS/build/<split>/dataset_quality.*.json
$REPORTS/build/<split>/dataset_diagnostics.*.json
$REPORTS/build/<split>/hybrid_dataset_audit.*.json
```
