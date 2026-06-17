# ChatGPT review patch summary

Applied small, safe code-level fixes during the current review:

1. `scripts/build_dataset.py`
   - `--pudo_source evidence_jsonl` now materializes audited PUDO candidates directly from the evidence JSONL instead of generating nuPlan-route PUDOs and only applying ID-matched overrides.
   - Added robust parsing for `curb_pose`, `stop_pose`, `curb_x/curb_y`, `x/y`, and point/polyline `geometry` in PUDO evidence rows.
   - Keeps `nuplan_route` as the explicit route-derived candidate path.

2. `capplan/planning/transition_generator.py`
   - Removed a duplicated `kneeling` ResourceEvidence row in board/alight interface evidence.

3. `scripts/train_casa.py`
   - Validation metrics can now consume predicted uncertainty instead of always using a fixed `0.1` calibration interval.
   - Torch CASA training uses the uncertainty head in the calibration loss.
   - Paper-mode training now requires the explicit phase, typed-demand, uncertainty, and availability heads plus `--value_target offline_tsbs`.

4. `README.md`
   - Clarified that evidence-jsonl PUDO mode consumes audited candidate rows directly and requires pose/geometry fields.

Verification:

```bash
python -m pytest -q
# 69 passed
```
