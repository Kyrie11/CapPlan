# CapPlan dataset pipeline patch manifest — 2026-08-18

Validation: `pytest -q` -> **119 passed**; `python -m compileall -q capplan scripts` -> PASS; `bash -n scripts/build_abilitybench_data0_20260817.sh` -> PASS.

## Modified files

- `capplan/data/external_validation.py`
- `configs/abilitybench_nuplan_real_data0.yaml`
- `scripts/build_abilitybench_data0_20260817.sh`
- `scripts/build_dataset.py`
- `scripts/build_manual_audit_layers.py`
- `scripts/build_pudo_evidence.py`
- `scripts/build_service_layer.py`
- `scripts/download_arcgis_layer.py`
- `scripts/prepare_abilitybench_external.py`
- `tests/test_dataset_pipeline_user_report_fixes.py`

## Added files

- `docs/CapPlan-dataset-build-full-paper-optimized-20260818.md`
- `scripts/build_paper_anchor_leakage.py`
- `scripts/build_pudo_site_catalog.py`
- `scripts/check_four_city_paper_readiness.py`
- `scripts/package_capplan_qa_bundle.py`
- `scripts/prepare_pudo_audit_worklist.py`
- `scripts/review_pudo_audit_worklist.py`
- `scripts/select_paper_episodes.py`

## Deleted files

- none

## SHA-256 of delivered changed files

- `fad5d2cf12c67fcb271f2d697c567cb31811dba81cb8461d9896bbdcd07158bc`  `capplan/data/external_validation.py`
- `2ab07f6c6424ae1c19c7f0810dcce3b716b499a7b8374e01ae4d7e88ddf0cc8b`  `configs/abilitybench_nuplan_real_data0.yaml`
- `a5f5e03527e9d3bef6dabef485b8e53a552ee9b950f2976d6cc851cdcc5feba0`  `scripts/build_abilitybench_data0_20260817.sh`
- `3482de77e8d9d273bbe3581d8dd9f86b5391afdfeec006d5e2f06174ad936d01`  `scripts/build_dataset.py`
- `c9cfe688998ca9898ca1091f44425736abc2f6070468da79f8c7ae5c60761397`  `scripts/build_manual_audit_layers.py`
- `1cf5967a324cddb81a12e2510aee859cea07a74d44124ae2cddcf2ff5073a3b0`  `scripts/build_pudo_evidence.py`
- `42b886f2de53aea1759cfcd2f6238b885b86eb552e46b08b3b9c201135b5c304`  `scripts/build_service_layer.py`
- `d104729428e6699e7e930025c7b651561cc68c8fb4fd8e9c4bd7eae045da78dc`  `scripts/download_arcgis_layer.py`
- `6e327ab6d6b7c84c4aa1f06c81b23b5c78c48676bae775e9fc0070df90bd057f`  `scripts/prepare_abilitybench_external.py`
- `ffe46ba484d611cceb6962630161ad8f951a3a7e6cd6a1af7e8b7b63eb53c5e6`  `tests/test_dataset_pipeline_user_report_fixes.py`
- `9dd3af94eac25a16eb5787dd987f2f1927eaaf02198a274f2224609be454af0a`  `docs/CapPlan-dataset-build-full-paper-optimized-20260818.md`
- `051b3339fe7b62034cae2d86930f1012bfb3b5954b49e8e1a4bce09e97c83f3a`  `scripts/build_paper_anchor_leakage.py`
- `df0d9d823318b535f25dbded58944c34f3a09f3811b8302b9fc6d510cd5d02b4`  `scripts/build_pudo_site_catalog.py`
- `9a7fba4637a2d3a11ba95e703194aa6518f68b841f9f64891ea8003c9043bfc6`  `scripts/check_four_city_paper_readiness.py`
- `f6c07465ffbc6b963759c5d695162aa6389c0c66b7a8e4ce9f6637fd3aa63614`  `scripts/package_capplan_qa_bundle.py`
- `93ca48025888a80590c0d3729413ba7f2db30e6a3f51ef791ceb0e6dc02342ea`  `scripts/prepare_pudo_audit_worklist.py`
- `ac3453277a726c7753ee8fa39b4876525e7ba7be671a7131a8611d8c2cce3b85`  `scripts/review_pudo_audit_worklist.py`
- `7e8bf9cf5dec2a1649e85b03010b6b381c964c566136142215cc8aeeb05a95a6`  `scripts/select_paper_episodes.py`
