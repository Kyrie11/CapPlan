# Reviewfix10 CUDA training / evaluation optimization

This revision does **not** change the frozen AbilityBench/CapPlan dataset labels or the mathematical CASA objectives introduced in reviewfix9. It optimizes preprocessing, CUDA synchronization, validation inference, and learned-CASA test inference.

## Why one A30 per seed instead of DDP

The current `relation_mlp` is a small transition-level surrogate. Its parameter count and per-batch compute are too small for two-GPU DDP to reliably overcome gradient all-reduce/process overhead. The recommended use of two A30s is therefore **parallel independent seeds / ablation subsets**, which preserves each run's batch size and optimizer trajectory and almost doubles experiment throughput.

When a true heterogeneous HGT/R-GCN service-graph encoder is implemented, DDP should be reconsidered.

## Numerically conservative mode

For the closest continuation of the reviewfix9 FP32 baseline:

- `--device cuda:0`
- `--amp off`
- `--no-tf32`
- `--matmul_precision highest`
- `--fused_adamw off`
- do not enable `--torch_compile`

The caching, TQDM, batched test inference, reduced CUDA logging synchronization, and two-GPU seed parallelism still accelerate the workflow without changing the training objective.

## Optional faster A30 mode

After validating parity on the validation set, A30 supports:

- `--amp bf16`
- `--tf32 --matmul_precision high`
- `--fused_adamw auto`
- optionally `--torch_compile --compile_mode reduce-overhead`

These can change floating-point rounding slightly, so they are not the default recommendation for the first publication-facing run.
