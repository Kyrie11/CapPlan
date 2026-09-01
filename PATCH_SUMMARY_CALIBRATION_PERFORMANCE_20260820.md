# CapPlan calibration-driven bootstrap performance patch — 2026-08-20

This patch is based on the uploaded 100-scene Boston + Singapore calibration and its runtime profile.

## Measured baseline

Known stage time: 2741.716 s (45m 41.7s): extraction 16.95%, graph 41.84%, PUDO 41.22%.
The profiler ran for 812 samples, but only 90 samples contained a direct CapPlan extract/graph/PUDO process. The direct-stage active window ended at 01:11:39; the remaining 23,030 s (6h23m50s) is an idle/unrelated tail and must not be attributed to CapPlan.

Graph and PUDO showed ~100% process CPU and near-zero iowait, identifying serial CPU/code paths. Extraction showed much higher sdb utilization and nonzero iowait, so extraction parallelism remains conservative.

## Main changes

- exact route-corridor GIS spatial indexing and conservative chunked route envelopes;
- exact graph/PUDO spatial grids and vectorized route/blockage kernels;
- graph episode multiprocessing with fork/COW sharing of the city GIS index;
- compact deterministic JSONL plus shallow graph-node/edge serialization;
- nuPlan route geometry cache;
- durable input/version fingerprints for extraction, graphs, and PUDO shards;
- extraction resume before devkit initialization when fingerprints match;
- fresh nuPlan DB inspection inventory fingerprint and per-city DB manifests for mixed val/test directories;
- staged scheduler: conservative extraction, bounded graph/PUDO city parallelism;
- scheduler uses `wait -n`, so a slow city cannot leave a parallel slot idle;
- profiler auto-stop and an idle-tail-aware summarizer;
- richer per-stage/per-episode timing reports.

## Quality semantics

The performance indexes preserve exact nearest-distance/tolerance semantics. Explicit curb/PUDO candidates are never capped. The chunked route crop is conservative with respect to the configured route radius: it removes only global-AABB corners that are outside all expanded route chunks. DB manifests do not change the official train/val/test split; they only avoid feeding irrelevant-city DB files to each city builder after a fresh PASS inspection proves the mapping.

Old graph/PUDO resume markers without the new build versions/fingerprints are deliberately invalidated once to prevent stale mixed-version datasets.
