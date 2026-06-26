# AbilityBench/CapPlan 修复摘要

本包是在当前论文与代码基础上的工程修复版，重点解决四类会直接影响论文主张验证的问题：外部证据采集不稳定、paper-mode 数据证据不完整但未被拦截、真实 OD anchor 下 oracle 起点错误导致 `skeleton_label_count=0`、以及 CASA 训练在全负标签上静默继续。

## 1. 外部数据获取与 prepare 链路

修改文件：

- `scripts/fetch_abilitybench_external_four_cities.py`
- `scripts/prepare_abilitybench_external.py`
- `configs/abilitybench_nuplan_real.yaml`

主要变化：

- Overpass 下载改为逐 tile 缓存与逐 tile 重试：成功的 tile 写入 `abilitybench_external/osm/tile_cache/<city>_g<grid>_tile_XXX.json`，后续即使传入 `--force` 也复用已成功 tile，避免新一轮失败把前面成功请求全部浪费。
- 支持 endpoint pool，并按 tile 轮换 endpoint：默认配置包含 `overpass-api.de`、`overpass.kumi.systems`、`overpass.openstreetmap.ru`、`overpass.osm.ch`。
- 对 HTTP 429 读取 `Retry-After`，否则使用指数退避 + jitter；对 SSL/TLS endpoint 错误自动换 endpoint。
- `prepare_abilitybench_external.py` 的 download stage 不再单独 curl 原始 query，而是调用 robust fetcher，保证目录规范化输出一致。
- paper source preflight 不再只检查路径是否存在，也检查 OSM/OpenSidewalks/curb inventory/curb regulations/entrances/DEM 是否非空。空 JSONL/空 GeoJSON 会在 paper mode 下直接失败。

## 2. paper-mode georeference 与 graph metadata

修改文件：

- `capplan/data/gis_fusion.py`
- `capplan/data/accessibility_layer.py`
- `scripts/build_dataset.py`

主要变化：

- GIS fusion 输出的 graph metadata 增加 `georeference_validated`、`transform_backend`、`projected_map_frame`、feature 计数等诊断字段。
- `PreparedAccessibilityBuilder` 与 `load_accessibility_graph()` 读取 `.nodes.jsonl/.edges.jsonl` 时，会合并同名 combined graph JSONL 中的 metadata，避免 georeference 诊断在保存/加载后丢失。
- `--require_validated_georeference` 改为要求 metadata 中 `georeference_validated is True`；缺失、`False`、`None` 都不能通过 paper mode。

## 3. offline oracle 真实 anchor 起点修复

修改文件：

- `capplan/data/label_oracle.py`
- `tests/test_independent_oracle.py`

主要变化：

- `IndependentLabelOracle.exhaustive_search()` 不再硬编码从 literal `("origin", "origin")` 起步，而是从 transition set 中推断真实 origin phase 的 `from_anchor`。
- 新增回归测试：当真实入口名为 `entrance_A`、目的地为 `entrance_B`，且 transition 中不存在 literal `origin` anchor 时，oracle 仍能找到 passenger-complete skeleton。

这个修复对真实 service request 非常关键：真实入口通常不是 `origin` 这个字符串，旧实现会让所有真实场景从不存在的 anchor 出发，直接造成 `skeleton_label_count=0` 或全负 passenger labels。

## 4. CASA 训练防退化保护

修改文件：

- `scripts/train_casa.py`

主要变化：

- paper mode 下新增训练/验证标签 sanity check：
  - passenger edge labels 必须同时有正负样本；
  - value/skeleton target 必须有正值；
  - typed demand supervision 必须存在；
  - availability target 必须有限。
- 这样不会在全负标签、无 skeleton、无 demand supervision 的数据上静默训练出“全部不可行”的模型。

## 5. nuPlan planner wrapper 行为

修改文件：

- `capplan/planning/trajectory_refinement.py`

主要变化：

- `CapPlanNuPlanPlanner.compute_planner_trajectory()` 不再无条件 stub raise；如果注入的 planner 实现了 `compute_planner_trajectory()` 或 `plan(...)`，会委托执行并返回轨迹。
- 若未注入真实 planner，则明确报错，避免把 imported vehicle metrics closed-loop 与 direct ego-control planner 混淆。

## 6. 验证

已运行：

```bash
python -m py_compile \
  scripts/fetch_abilitybench_external_four_cities.py \
  scripts/prepare_abilitybench_external.py \
  scripts/build_dataset.py \
  scripts/train_casa.py \
  capplan/data/gis_fusion.py \
  capplan/data/accessibility_layer.py \
  capplan/data/label_oracle.py \
  capplan/planning/trajectory_refinement.py \
  tests/test_independent_oracle.py

pytest -q \
  tests/test_independent_oracle.py \
  tests/test_dataset_labels.py \
  tests/test_gis_fusion_builders.py \
  tests/test_nuplan_pudo_evidence.py \
  tests/test_casa_training_smoke.py \
  tests/test_readme_commands.py
```

结果：关键回归测试 `13 passed`。未在此文件中声明全量 `pytest` 通过。

## 7. 仍需真实数据补齐的部分

本修复没有伪造外部证据。论文级 benchmark 仍需要真实/审计来源的数据补齐：

- `curb_inventory/<city>.jsonl`：`curb_height_m`、`sidewalk_width_m`、`deployment_clearance_m`、side、置信度等；
- `curb_regulations/<city>.jsonl`：legal stop / loading zone / bus lane / no-stopping 等 curb rule；
- `entrances/<city>.geojson`：真实建筑/POI/交通入口；
- `dem/<city>.jsonl`：真实 elevation/slope samples；
- OpenSidewalks 与 city GIS 辅助行人网络；
- 每个城市的 georeference 配置必须标记为 validated，且坐标投影误差经过审计。

仅 OSM/Overpass 可作为 bootstrap 诊断，不足以支撑 paper-mode benchmark。
