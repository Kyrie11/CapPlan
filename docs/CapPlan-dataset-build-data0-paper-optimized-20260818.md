# CapPlan / AbilityBench-AV 四城 full 数据集：2026-08-18 审计后优化流程

本文档基于 `main.tex`、当前 `CapPlan.zip`、四版递进构建指南以及 2026-08-18 代码审计结果整理。目标不是“让 pipeline 跑完”，而是让最终数据能够对论文的 **passenger-complete / capability-aware / failure-certificate** 主张提供可追溯、fail-closed、可复现的证据。

> 当前会话没有实际挂载 `report.zip`，因此本文对你“已经执行的那一次”不能逐日志定责。下面对代码/指令本身的定责是确定的；对历史运行中某个具体 FAIL 是否正是该原因，需要用最后一节列出的 compact reports 复核。

---

## 1. 论文真正需要的数据集

每个 paper episode 至少应同时包含以下七层：

1. **真实交通 scene substrate**：nuPlan DB scene、route corridor、ego/agent history、traffic light、真实 scene timestamp。
2. **真实 passenger interface graph**：building/station entrance、pedestrian graph、crossing、curb connector、PUDO，且 entrance 与 PUDO 必须是两个独立实体。
3. **PUDO 静态接口证据**：curb height、usable sidewalk width、deployment clearance、curb ramp、running slope、cross slope、surface，以及字段级 provenance。
4. **独立 stopping legality**：`legal_stop` 不可从 parking meter/taxi zone/PUDO candidate 语义推导，必须有独立 `legal_basis` 与来源/时间语义。
5. **reference service vehicle interface**：door side、ramp/lift、low-floor/kneeling、door width、deployment clearance、notification modes、dwell policy，不能把 nuPlan capture car 当成服务车辆。
6. **同 scene capability counterfactual**：同一 scene、同一 O/D、同一 request time、同一 vehicle，只改变 capability contract；默认 8 profiles 覆盖 7 个 T4 axis。
7. **独立标签与闭环结果**：transition/resource/passenger feasibility、passenger-complete skeleton 或 failure certificate；车辆安全/进度结果必须来自真实 nuPlan closed-loop rollout，而不是 log ego trajectory。

因此建议保留两层数据：

- `all_bootstrap_candidates`：所有真实 scene + uncertain candidates，用于覆盖率/缺失性分析；
- `paper_eligible_subset`：只保留满足证据门槛的 episode，用于主结果。

绝不能用默认值把第一层“补成”第二层。

---

## 2. 本次确认并修复的代码问题

### 2.1 curb connector 的 `curb_ramp` 曾 fail-open

旧代码在 access/egress path 没经过任何 curb-context edge 时把 `curb_ramp=True`。这会把“没有证据”解释成“有 ramp”。已改为 unknown/fail-closed，并把 PUDO 建成显式 curb-interface graph node，access/egress 必须经过 curb connector。

### 2.2 `paper_eligible` 曾过宽

旧判定只依赖少数连续量和粗粒度 source 字符串，可能把候选 PUDO 错标 paper-ready。现在 paper PUDO 要求：

- `curb_height_m`
- `sidewalk_width_m`
- `deployment_clearance_m`
- `curb_ramp`
- `running_slope`
- `cross_slope`
- `surface`
- pedestrian binding
- independent Tier-A legality + `legal_basis`
- 字段级 provenance

候选层本身永远不能提升 legality/physical truth；只有后来绑定到该物理位置的独立 Tier-A 字段证据可以提升相应字段。

### 2.3 manual audit “merge” 曾可能覆盖官方属性

旧 merge 对同 ID 是整行 replacement，可能丢掉官方 normalized record 中 CSV 没有重填的属性。现在改为字段级 merge，审计字段覆盖对应字段，其他官方字段保留；`field_provenance` 同样字段级合并。entrance 也采用相同规则。

### 2.4 service O/D 曾与 paper PUDO 覆盖无关

旧 service layer 可随机选一个真实 entrance，但该 endpoint 未必能通过 pedestrian graph 到达任何 paper-eligible PUDO。现在 paper mode 只从“在 `paper_endpoint_max_path_m` 内至少连到一个 paper PUDO”的 entrance 中选择 O/D，并将 endpoint coverage 写入 graph metadata。

### 2.5 单个 evidence-poor episode 曾终止整城 full build

这会把“真实覆盖不足”误当成 pipeline crash。现在只对**预期的 episode-level evidence/endpoint coverage 不足**写入 `excluded_episodes.jsonl` 并 drop；georeference、schema、source provenance、fleet provenance 等全局错误仍 hard fail。

### 2.6 nuPlan timestamp 读取错误

旧 adapter 读取非标准 `ego.time_seconds`，读不到时退化为 iteration index，导致动态 evidence 时间对齐失真。现在优先读取官方 `ego_state.time_point.time_us`，并在 scene metadata 写：

- `time_source`
- `absolute_timestamp_available`
- `scene_start_time_us`

`prepare_abilitybench_external.py` 在 paper extract 后自动执行 `audit_scene_time_alignment.py --require_absolute`。

### 2.7 PUDO dynamic input 曾泄漏未来 agent history

旧 `blockage_risk` 聚合了整个 future log-play agent history，然后作为 planner/CASA input，属于未来信息泄漏。现在：

- planner-facing `blockage_risk` 只使用最早可用的当前 agent observation；
- 后续帧只存 `future_blockage_rate_label`，不得进入 raw planner input；
- 写 `dynamic_input_causal=true` 和 `dynamic_evidence_source`；
- 无可用 dynamic observation 时 fail-closed：risk=1、confidence=0；
- paper quality audit 会拒绝 non-causal/unprovenanced eligible PUDO dynamic input。

注意：当前 label 仍是粗粒度 curb-neighborhood occupancy，而不是精确“车辆预计到达 PUDO 时刻”的未来 availability truth；若论文要强调 future availability prediction，需要进一步建立 arrival-time-aligned dynamic labels。

### 2.8 shortlist 只取 val 不够支撑 full dataset

现在 `export_pudo_audit_shortlist.py --pudo_evidence_jsonl` 可重复传入 train/val/test 三份文件，再按物理坐标跨 split 去重。这样 audit 对象是**唯一 physical curb site**，而不是重复 episode candidates。

### 2.9 fleet 现在有单独 paper preflight

新增 `scripts/validate_fleet_interface.py`。它在昂贵 full build 前检查 source、explicit interface fields、door width、clearance、dwell、notification modes 等，避免直到 dataset 阶段才发现 example/default fleet。

### 2.10 新增 cross-split bundle audit

新增 `scripts/audit_dataset_bundle.py`：

- episode/scenario token/log train-val-test overlap：hard fail；
- 8 profile same-scene/same-O/D/time：hard gate；
- 7 个 T4 axis：hard gate；
- eligible PUDO provenance：hard gate；
- endpoint PUDO coverage metadata：hard gate；
- physical `site_id` 跨 split reuse：显式报告；
- 生成 `site_disjoint_test_episodes.txt` 作为 secondary generalization subset。

---

## 3. 对你前面已遇到/文档记录问题的定责

在没有 `report.zip` 的前提下，以下是根据代码和四版文档能确定的类别：

| 现象 | 定责 | 处理 |
|---|---|---|
| 旧 georeference 四城错误 FAIL | 旧代码/coverage 语义错误 | 用修复后的 `validate_georeference_alignment.py` 重跑 |
| Vegas AOI south=36.07 导致地图/DEM 覆盖不足 | 配置/指令错误 | south 必须用 `36.055`，OSM 与 DEM 都按新 AOI 重做 |
| Pittsburgh payment points 有空坐标 | 上游源内存在无效 rows，不是“整个下载文件坏了” | inspection 后 normalization 使用 `--skip_invalid` |
| Boston normalize PASS 但缺 curb geometry | “PASS”语义误读，不是下载损坏 | normalization PASS 只说明解析成功；缺失字段仍需 Tier-A evidence/manual audit |
| `auditor_id is required` | 正确 fail-closed gate | 不应绕过；只有字段全部来自独立 Tier-A official evidence 时才允许 `--allow_automatic_tier_a` |
| provenance 中 TODO/REVIEW/VERIFY 失败 | provenance 输入未完成 | 填真实 source/license/retrieval/version；不要改 gate |
| paper fleet 用 example/default | 输入文件错误 | 换成 manufacturer/operator/measurement verified reference vehicle |
| future agent history 进入 PUDO blockage input | 代码错误 | 本次已修复为 causal current observation |
| nuPlan scene time 退化为 iteration | 代码错误 | 本次已修复为 `time_point.time_us` |

如果 `report.zip` 补传后，我可以把每条 FAIL 精确映射到“源文件/代码/命令/正常 gate”四类。

---

## 4. 10.1 可以自动化到什么程度

### 可以自动化且现在已实现

`auto_prefill_pudo_entrance_audit.py` 会从 normalized evidence 中：

- 空间匹配 curb inventory / regulation / entrance；
- 只复制 Tier-A、非 proxy、非 candidate 的字段；
- legality 必须通过 independent legality policy；
- 每个 physical field 单独记录 source/tier；
- 检查邻近多个对象相互冲突时标记 `AMBIGUOUS:*`，不自动选一个；
- 自动填 `auto_residual_fields`；
- entrance 必须是独立 point，不能拿 PUDO 坐标复制成 entrance；
- 所有字段都有 Tier-A provenance 且无 residual 时，可以不做人手转录，后续用 `--allow_automatic_tier_a` materialize。

### 不能“正确自动化”的部分

如果外部源本来不存在下列事实，代码不能制造它们：

- curb/ramp 精确实测尺寸；
- exact doorway/entrance；
- deployment landing clearance；
- 本地当前或历史 service-class stopping legality；
- sign/time restriction 的适用对象；
- verified service-vehicle interface spec；
- provenance license/terms 中未公开的事实。

因此正确策略是：**自动化 evidence fusion + residual detection，而不是自动猜值**。

---

## 5. 安装本次优化代码

将交付 zip 覆盖到代码目录前建议先备份：

```bash
export CAP_HOME=/home/senzeyu2/code/CapPlan
cd /home/senzeyu2/code
cp -a CapPlan "CapPlan.backup.$(date +%Y%m%d_%H%M%S)"
```

然后将新的代码包内容同步到 `$CAP_HOME`。完成后：

```bash
cd "$CAP_HOME"
python -m py_compile \
  capplan/data/nuplan_adapter.py \
  capplan/data/accessibility_layer.py \
  capplan/data/gis_fusion.py \
  scripts/build_pudo_evidence.py \
  scripts/build_dataset.py \
  scripts/build_service_layer.py \
  scripts/prepare_abilitybench_external.py

bash scripts/build_abilitybench_data0_paper_20260818.sh test-code
```

本次交付在沙箱中按 18/17 个测试文件分两组运行：**70 + 49 = 119 tests passed**。

---

## 6. 统一环境

```bash
export CAP_HOME=/home/senzeyu2/code/CapPlan
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export CONFIG=$CAP_HOME/configs/abilitybench_nuplan_real_data0.yaml
export EXT=$DATA_ROOT/external
export REPORTS=$EXT/reports

mkdir -p "$REPORTS/commands" "$REPORTS/build" "$REPORTS/model" "$REPORTS/eval"
cd "$CAP_HOME"
```

配置中 train DB dirs 应保持：

```text
train_boston
train_pittsburgh
train_vegas
train_singapore
```

val/test 保持原官方目录 `val`、`test`，**不要复制 DB 来人为按城市分目录**；代码通过 `map_names` filter 分城。

---

## 7. 从你当前状态继续的完整构建流程

### 7.1 DB/map/OSM/external bootstrap preflight

```bash
bash scripts/build_abilitybench_data0_paper_20260818.sh preflight
```

必须得到：

```text
$REPORTS/nuplan_db_cities.train.json
$REPORTS/nuplan_db_cities.val.json
$REPORTS/nuplan_db_cities.test.json
$REPORTS/georeference_spatial_alignment.json
$REPORTS/external.bootstrap.json
```

都 PASS 后再继续。

### 7.2 若 OSM 仍是旧 Vegas AOI，必须重做

```bash
python scripts/prepare_osm_from_pbf.py \
  --input_pbf "$EXT/raw/osm_pbf/nevada-latest.osm.pbf" \
  --bbox 36.055,-115.23,36.20,-115.10 \
  --output "$EXT/normalized/osm/vegas_sidewalks.geojson" --overwrite

python scripts/validate_georeference_alignment.py \
  --config "$CONFIG" \
  --cities boston,pittsburgh,vegas,singapore \
  --min_map_covered_by_aoi 0.95 \
  --min_aoi_covered_by_osm 0.95 \
  --min_map_covered_by_osm 0.95 \
  --write_georeference \
  --report_json "$REPORTS/georeference_spatial_alignment.json"
```

### 7.3 public sources

```bash
python scripts/fetch_recommended_public_sources.py \
  --config "$CONFIG" \
  --cities boston,pittsburgh,vegas,singapore \
  --strict \
  2>&1 | tee "$REPORTS/commands/fetch_recommended_public_sources.log"
```

Pittsburgh payment points：

```bash
python scripts/inspect_tabular_coordinates.py \
  --input "$EXT/raw/wprdc/pittsburgh/payment_points_current.csv" \
  --min_valid_fraction 0.90 --min_valid_rows 1000

python scripts/normalize_accessibility_evidence.py \
  --input "$EXT/raw/wprdc/pittsburgh/payment_points_current.csv" \
  --output "$EXT/normalized/candidates/pittsburgh/payment_points_current.jsonl" \
  --profile pittsburgh_parking_meter \
  --source "Pittsburgh Parking Authority via WPRDC" \
  --skip_invalid
```

### 7.4 DEM gate

U.S. 三城建议统一：

```bash
for city in boston pittsburgh vegas; do
  python scripts/validate_dem_tiles.py \
    --config "$CONFIG" --city "$city" \
    --rasters "$EXT"/raw/dem/$city/*.tif \
    --expected_resolution_m 1 --min_coverage 0.99 \
    --report_json "$REPORTS/dem_tiles_${city}.json"

  python scripts/sample_raster_dem.py \
    --external_root "$EXT" --city "$city" \
    --rasters "$EXT"/raw/dem/$city/*.tif \
    --vertical_datum NAVD88 \
    --source_name USGS_3DEP_1m --nominal_resolution_m 1 \
    --tile_validation_report "$REPORTS/dem_tiles_${city}.json" \
    --include_city_gis
done
```

Singapore：

```bash
python scripts/validate_dem_tiles.py \
  --config "$CONFIG" --city singapore \
  --rasters "$EXT"/raw/dem/singapore/*.tif \
  --expected_resolution_m 30 --min_coverage 0.99 \
  --report_json "$REPORTS/dem_tiles_singapore.json"

python scripts/sample_raster_dem.py \
  --external_root "$EXT" --city singapore \
  --rasters "$EXT"/raw/dem/singapore/*.tif \
  --vertical_datum EGM2008 \
  --source_name COPERNICUS_GLO30_DSM --nominal_resolution_m 30 \
  --tile_validation_report "$REPORTS/dem_tiles_singapore.json" \
  --include_city_gis
```

DEM slope 只能作 terrain prior，不应被当作 curb/ramp 精确 slope truth。

---

## 8. 先构建 full bootstrap candidates，再做一次跨 split site audit

不要只从 val×10 生成最终 shortlist。先对 train/val/test 全量 matching scenes 生成 scene/graph/PUDO candidates：

```bash
bash scripts/build_abilitybench_data0_paper_20260818.sh bootstrap-candidates
```

这一步会同时生成新的：

```text
$REPORTS/build/<split>/...
$REPORTS/scene_time.<city>.json
$DATA_ROOT/outputs/prepared/<split>/scene_contexts/<city>/...
$DATA_ROOT/outputs/prepared/<split>/pudo/<city>.jsonl
```

bootstrap 不要求 paper fields 完整；它的作用是确定 full benchmark 实际覆盖的候选 physical sites。

---

## 9. 自动化 10.1：跨 train/val/test shortlist + Tier-A prefill

```bash
bash scripts/build_abilitybench_data0_paper_20260818.sh prefill-audit
```

每城得到：

```text
$EXT/audits/<city>/pudo_audit_shortlist.csv
$EXT/audits/<city>/pudo_audit_prefilled.csv
$REPORTS/manual_audit_shortlist.all_splits.<city>.json
$REPORTS/manual_audit_autoprefill.<city>.json
$REPORTS/manual_audit_residual_summary.json
```

先看：

```bash
cat "$REPORTS/manual_audit_residual_summary.json"
```

判定：

- `residual_rows == 0`：该城 shortlist 可以完全由独立 Tier-A normalized evidence materialize；
- `residual_rows > 0`：只对 `auto_residual_fields` 中列出的字段补人工/独立证据；不要重填已经有字段级 Tier-A provenance 的字段。

为每城准备最终文件：

```bash
for city in boston pittsburgh vegas singapore; do
  cp "$EXT/audits/$city/pudo_audit_prefilled.csv" \
     "$EXT/audits/$city/pudo_audit_final.csv"
done
```

对 `pudo_audit_final.csv` 中 residual 字段补齐。只要某行包含人工测量/判断，就必须补：

- `auditor_id`
- `observed_at`（含 timezone）
- 必要时 `photo_ref`
- `legal_basis`
- 字段对应 source/tier

对于完全来自 official Tier-A、没有 residual 的行，不强制人工“重新抄写”，`--allow_automatic_tier_a` 会按字段 provenance 验证。

---

## 10. 10.1 以后原来需要人工审核的内容如何处理

### 10.1 materialize PUDO + entrance layers

```bash
bash scripts/build_abilitybench_data0_paper_20260818.sh paper-layers
```

它会默认**合并**现有 official normalized layers，不覆盖未被审计字段。不要加 `--replace_normalized_layers`。

### 10.2 fleet interface

将真实 reference vehicle 写入：

```text
$EXT/normalized/fleet/vehicle_interfaces.jsonl
```

然后：

```bash
python scripts/validate_fleet_interface.py \
  --fleet_jsonl "$EXT/normalized/fleet/vehicle_interfaces.jsonl" \
  --output "$REPORTS/fleet_interface.paper.json"
```

`CAPPLAN_FLEET_CHECK PASS` 才继续。

### 10.3 provenance/license

完成：

```text
$EXT/provenance_registry.yaml
```

所有 `TODO/REVIEW/VERIFY` 必须消失。然后运行：

```bash
bash scripts/build_abilitybench_data0_paper_20260818.sh paper-preflight
```

这一步同时跑 verified fleet、四城 provenance manifest、`external.paper.json`。

### 10.4 time semantics

paper extract 自动检查 absolute nuPlan time。若想单独复核：

```bash
for split in train val test; do
  for city in boston pittsburgh vegas singapore; do
    python scripts/audit_scene_time_alignment.py \
      --scene_dir "$DATA_ROOT/outputs/prepared/$split/scene_contexts/$city" \
      --require_absolute \
      --output "$REPORTS/scene_time.${split}.${city}.json"
  done
done
```

对于 2026 manual audit/current regulation，建议论文明确语义为“static/service-rule reference overlay on historical traffic replay”，除非你能提供 valid interval 覆盖 nuPlan scene timestamp 的历史法规/设施记录。不要把 2026 temporary blockage 写成 2021 scene truth。

---

## 11. paper pilot

即使 preflight PASS，也先跑 20 scenes/city val：

```bash
python scripts/prepare_abilitybench_external.py \
  --config "$CONFIG" --split val --source_policy paper \
  --cities boston+pittsburgh+vegas+singapore \
  --max_scenarios_per_city 20 \
  --stages preflight,extract,graphs,pudo,service,dataset,merge \
  2>&1 | tee "$REPORTS/commands/paper.val.4city20.log"

python scripts/validate_dataset.py \
  --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_val" --strict

python scripts/audit_dataset_quality.py \
  --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_val" \
  --paper_mode --fail_if_not_publication_ready \
  --output "$REPORTS/dataset_quality.paper.val20.json"
```

pilot 不通过不要直接 full build。

---

## 12. 完整四城 full train / val / test

```bash
bash scripts/build_abilitybench_data0_paper_20260818.sh paper-full
```

等价核心命令是：

```bash
for split in train val test; do
  python scripts/prepare_abilitybench_external.py \
    --config "$CONFIG" --split "$split" --source_policy paper \
    --cities boston+pittsburgh+vegas+singapore \
    --max_scenarios_per_city 0 \
    --stages preflight,extract,graphs,pudo,service,dataset,merge

done
```

这里 `0` 表示所有符合 split + city map filter 的 scenarios。

关键语义：

- 证据不足 episode 进入 `excluded_episodes.jsonl`；
- retained episode 不允许默认补 physical/legal/interface truth；
- global provenance/georef/schema/fleet 错误仍会终止构建。

---

## 13. 跨 split audit 与 canonical dataset

```bash
bash scripts/build_abilitybench_data0_paper_20260818.sh bundle-audit
```

重点输出：

```text
$REPORTS/dataset_bundle.paper.json
$REPORTS/site_disjoint_test_episodes.txt
$REPORTS/dataset_quality.paper.train.json
$REPORTS/dataset_quality.paper.val.json
$REPORTS/dataset_quality.paper.test.json
$REPORTS/dataset_quality.paper.all.json
$DATA_ROOT/outputs/datasets/abilitybench_av_all/
```

`site_disjoint_test_episodes.txt` 建议作为额外泛化测试；主 benchmark 仍保留 official nuPlan traffic split，并显式报告 site overlap。

---

## 14. 我建议采用的 publication hard gates

### scene / split

- train/val/test DB city inspection PASS；
- episode ID / scenario token / log 三者无跨 split overlap；
- retained episodes 使用真实 nuPlan source；
- absolute scene timestamp rate = 1.0；
- four-city coverage 与你声明的 benchmark 范围一致。

### graph / PUDO

- 每 retained episode graph nodes >=100、edges >=150；
- 每 retained episode `paper_eligible PUDO >=2`；
- origin 与 destination 各至少 1 个可达 paper PUDO；
- endpoint path <=500 m；
- episode paper-PUDO coverage rate >=0.80；
- eligible PUDO `field_provenance` 完整；
- `dynamic_input_causal=true`；
- no proxy/synthetic entrance/accessibility source in paper main set。

### T4/T5

- 每 episode 8 requests；
- same scene / same O/D / same request time / same vehicle；
- 7 axes 全覆盖；
- failure certificate 至少覆盖 2 个不同 failure phases；
- feasible/infeasible 不应极端退化，quality audit 默认要求 passenger-positive 和 skeleton-positive rate >=0.10。

### provenance/fleet

- provenance 无 placeholder；
- fleet source 不是 example/default/proxy；
- core interface fields 全部显式提供；
- legality 与 candidate source 独立。

---

## 15. full build 之后，只上传这些 compact outputs 即可复核

无需打包 datasets。建议：

```bash
cd "$EXT"
zip -r reports_for_capplan_review_$(date +%Y%m%d).zip \
  reports/nuplan_db_cities.train.json \
  reports/nuplan_db_cities.val.json \
  reports/nuplan_db_cities.test.json \
  reports/georeference_spatial_alignment.json \
  reports/dem_tiles_boston.json \
  reports/dem_tiles_pittsburgh.json \
  reports/dem_tiles_vegas.json \
  reports/dem_tiles_singapore.json \
  reports/recommended_public_sources.json \
  reports/external.bootstrap.json \
  reports/external.paper.json \
  reports/fleet_interface.paper.json \
  reports/manual_audit_residual_summary.json \
  reports/manual_audit_shortlist.all_splits.*.json \
  reports/manual_audit_autoprefill.*.json \
  reports/manual_audit_layers.*.json \
  reports/scene_time*.json \
  reports/dataset_quality.paper.*.json \
  reports/dataset_bundle.paper.json \
  manifests/ \
  audits/*/manual_audit_manifest.jsonl \
  reports/commands/
```

如果 command logs 太大，只保留：

- 每阶段最后一次 PASS log；
- 所有 FAIL log。

---

## 16. 论文结论与当前 learned CASA implementation 的边界

修复后的数据集足够支持当前代码中的：

- capability contract + compiler；
- service automaton；
- PUDO/access-egress/vehicle-interface typed feasibility；
- TSBS pruning/search；
- passenger-complete skeleton；
- failure certificate；
- same-scene capability counterfactual；
- 与真实 nuPlan closed-loop vehicle metrics 联合评估。

但当前 `CASAHetGraphNet(model_type=hgt/rgcn)` 仍是 transition-level relation-aware MLP surrogate，并没有对 entrance/ped/PUDO/vehicle/road/dynamic-agent heterogeneous graph 做真正 message passing；`paper_safe` feature policy 又有意屏蔽 label-derived transition fields。因此它可以作为 learned-guidance baseline，但**不足以证明论文中“真正 HGT/R-GCN CASA-Net heterogeneous graph encoder”这一强主张**。

如果论文保留该主张，下一阶段应增加：

- raw heterogeneous node/edge tensors；
- causal dynamic-agent history window；
- label-independent raw geometry/interface features；
- arrival-time-aligned future availability targets；
- phase observation/state history；
- 真正 HGT/R-GCN message passing model。

在这些尚未实现前，建议论文对 learned component 的表述保持为 relation-aware learned guidance/surrogate；符号算法、TSBS、failure certificate 和 passenger-complete benchmark 部分可以按本流程做严格实验。

---

## 17. 最短可靠执行顺序

```text
1. 安装 20260818 patch，119 tests PASS
2. preflight
3. 修正/验证 OSM + DEM + public sources
4. bootstrap-candidates（train/val/test full）
5. prefill-audit（跨 split physical-site dedup）
6. 只补 residual evidence
7. paper-layers
8. verified fleet
9. provenance freeze
10. paper-preflight
11. val 4city×20 pilot
12. paper-full
13. bundle-audit
14. CASA learned surrogate / 或实现真正 graph model
15. nuPlan true closed-loop
16. full + ablations + T4/T5 + episode-level CI
17. 上传 compact reports 复核
```
