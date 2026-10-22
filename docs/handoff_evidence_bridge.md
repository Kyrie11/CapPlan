# Handoff Evidence Bridge (stable experimental protocol)

This code is deliberately **not** a physical accessibility or service-success oracle.

1. `scripts/handoff_study.py review-gates --review-dir REVIEW.zip --output REPORT.json` verifies the *uploaded* review manifest and recomputes conservative H1-H5 status.
2. `dataset-evidence-audit --dataset-root .../outputs/datasets --output REPORT.json` streams only the existing `pudo_anchors.jsonl` files; absent/derived/simulated attributes are not real measurements.
3. `progress-packets --remine directed_remine.jsonl --pairs recourse_pairs.jsonl --dataset-dir DATASET --output geometry_progress.jsonl` attaches the original screened contract and (when available) PUDO field provenance to AS_ENCODED directional path witnesses. New `directed-remine` outputs each O→A edge source and a partial-edge + directed B suffix; old screen files without these are flagged as incomplete. Unmeasured vehicle/evidence/boarding fields remain null.
4. `controlled-factorial --packets geometry_progress.jsonl --assumptions configs/handoff_timed_interventions.example.json --output controlled_factorial.jsonl` adds a distinct *synthetic* clock/vehicle-window layer and checks 2x2x2 factors against an independent integer-grid reference. It cannot estimate real-world risk or demonstrate novelty over a published shield.

Complete Chinese research reasoning and concrete server commands are delivered separately in `CapPlan_接驳承诺_下一阶段研究SOP.md`.

**No command above rewrites `data/outputs/datasets` or `data/outputs/prepared`.** Preserve a physical-site-held-out and a natural, unscreened evaluation split separately from controlled challenge overlays. A field marked missing is UNKNOWN, not FALSE. Do not describe a CARLA lane blocker as an audited legal real-city PUDO.
