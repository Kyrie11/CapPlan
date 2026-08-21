# CapPlan / AbilityBench-AV 数据集构建复核、性能诊断与 2026-08-21 优化方案

## 0. 结论先行

本次复核同时对齐了论文 `main.tex`、`docs/` 下五份递进构建文档、最新《大模型建议》、当前 `CapPlan.zip` 代码与 `reports.zip` 运行报告。

核心结论有四点：

1. **论文需要的不是“nuPlan 场景附加几个无障碍字段”，而是 passenger-complete episode。** 一个可用于论文主实验的 episode 必须能联合承载交通 scene、origin/destination entrance、pedestrian/curb accessibility graph、PUDO、vehicle interface、capability contract，以及 transition / typed-resource / skeleton / failure-certificate labels。缺失或低置信度证据必须 fail-closed / inconclusive，不能默认可行。
2. **当前 pipeline 的 paper-level 证据链总体方向是正确的**：官方 nuPlan split 保持不变；bootstrap candidate 与 paper truth 分开；PUDO/entrance/legality/interface 有独立来源与 provenance；最终通过 reviewed evidence + paper allowlist 再生成论文数据集。真正有问题的是当前执行方式把 `max_scenarios_per_city=0` 当成“论文数据集越全越好”，从而对大量高度相关的 nuPlan 时间快照全部做重型 history + graph + PUDO 物化。
3. 你看到的 `510498scene [35:57:46, 5.93scene/s]` **不是扫描 51 万但只产出很少**。在当前 extractor 中，tqdm 每增加 1 已经完成一个 SceneRecord / EpisodeMetadata 并写入 `.part`；之所以 snapshot 看不到，是因为旧实现只有完整结束后才把 `scenes.jsonl.part` / `episodes.jsonl.part` 原子替换成正式文件。上传报告最后已经到 510,741 个 train/Boston scene；全程均速约 3.94 scene/s。
4. **当前真正最大的性能问题是 work amplification（样本基数），而不是再找一个 2× 的局部 kernel。** 100-scene Boston calibration 平均约 23,919 graph nodes + 26,559 graph edges / episode。若仅按 510,741 个已抽取 scene 外推，就约 122.2 亿 nodes + 135.6 亿 edges；继续给所有 snapshot 建 graph/PUDO 会把数据量与计算量放大到完全不必要的规模。论文主数据集更合理的结构是：“完整 lightweight scenario identity index” + “有确定性选择规则的 paper-scale heavy candidate corpus” + “site-level audit / paper allowlist”。

因此，本次代码优化不降低 GIS 精度、不删历史帧、不放宽 paper gate，而是在不改变证据语义的前提下减少重复工作，并补齐真正安全的断点续建与人工审计 triage。

---

## 1. 论文要求的数据集性质

论文把成功定义为入口到入口的 passenger-complete service，而不是 vehicle route completion。数据集必须支撑 access → wait → board → ride → alight → egress 六个关键阶段及其失败诊断。

一个 paper episode 至少需要：

- traffic scene / ego + agents + road semantics + route corridor；
- origin / destination entrances；
- pedestrian / sidewalk / crossing / curb accessibility graph；
- candidate PUDO anchors 与 curb / stopping / blockage / confidence evidence；
- vehicle-interface specification（door side、ramp/lift、door width、deployment clearance、notification、dwell 等）；
- one or more capability contracts；
- transition validity labels；
- typed resource values（distance、slope、width、clearance、wait、motion、confidence、availability 等）；
- feasible passenger-complete skeleton 或 infeasible failure certificate；
- evidence source / confidence / provenance，且 missing field 不能默认 feasible。

数据集还必须支撑论文 T1–T5，尤其是 same-scene capability counterfactuals 和 failure certificate；因此 capability variants 应与同一个 scene / service anchor 绑定，而不是把变体当成独立交通样本。

**这意味着“scene 数”不是论文数据集唯一规模指标。** 更重要的是独立 logs / routes / physical PUDO sites / entrances 的覆盖、capability-contract diversity、paper-eligible evidence coverage、正负 feasibility frontier、以及 train/val/test 的 site leakage 控制。

---

## 2. 五份递进构建文档与当前实现的总体判断

五份文档的方向是递进且大体正确的：

- 第一阶段先把真实 nuPlan、pedestrian topology、PUDO 与 capability schema 接起来，同时明确 bootstrap 不能冒充 paper truth；
- 后续逐步修正 OSM road/sidewalk 混淆、proxy legality/interface、CRS、真实 entrance、physical dimensions、provenance；
- recovery 版本强调官方 nuPlan DB split immutable，不能二次随机重划；
- 20260817 版本强化 data0 的实际目录、paper fail-closed、manual audit；
- 20260818 版本进一步引入 physical PUDO site dedup、audit、paper evidence recompute、allowlist、physical anchor leakage 与最终 QA。

因此，**我不建议推倒现有数据逻辑。** 应保留其 paper evidence / audit / allowlist 结构，只调整 heavy scene materialization 的规模策略与恢复机制。

当前 `CapPlan.zip` 里已经包含上一轮建议的大部分性能修复，包括：

- PUDO vectorized / exact metric-grid spatial query；
- graph episode-level multiprocessing；
- graph compact serialization；
- route-corridor chunked GIS crop；
- val/test DB→city inventory manifest；
- route geometry exact cache；
- build fingerprint；
- `wait -n` city scheduler。

这些优化应该保留。本轮是在此基础上继续修复“规模、断点、审计”三个问题。

---

## 3. reports.zip：目前 510k 到底发生了什么

### 3.1 DB inventory 正常

上传报告中的 inspection 全部 PASS：

| split | DB 数 | Boston | Pittsburgh | Singapore | Vegas | issues |
|---|---:|---:|---:|---:|---:|---|
| train | 7351 | 1647 | 1560 | 2394 | 1750 | `[]` |
| val | 1381 | 192 | 174 | 255 | 760 | `[]` |
| test | 1310 | 152 | 160 | 258 | 740 | `[]` |

并且 train/val/test 都已有 `db_inventory_fingerprint`。因此 DB split/city routing 不是当前异常来源。

### 3.2 当前命令确实在做“所有 Boston train scenario”的重型抽取

报告中的真实命令是：

```bash
python scripts/prepare_abilitybench_external.py \
  --config .../abilitybench_nuplan_real_data0.yaml \
  --split train --source_policy bootstrap --cities boston \
  --max_scenarios_per_city 0 --stages extract ...
```

进而调用：

```bash
python scripts/extract_nuplan_scenes.py \
  ... --split train --max_scenarios 0 --num_workers 4 \
  --nuplan_db_dirs train_boston \
  --nuplan_map_names us-ma-boston
```

`max_scenarios=0` 在 CapPlan 里就是“保留所有匹配的 ScenarioFilter 结果”。上传日志最后为：

```text
510741scene [35:58:53, 5.01scene/s]
```

这 510,741 不是只做了 metadata scan；每个计数已经经过 `NuPlanAdapter._extract_real_scenario()` 的 scene hydration，并写入 `.part`。

### 3.3 为什么“好像没有构建多少结果”

旧 extractor 使用：

```text
scenes.jsonl.part
episodes.jsonl.part
```

只有整个 selection 完成后才 replace 到：

```text
scenes.jsonl
episodes.jsonl
```

所以现有 QA/performance snapshot 仍然只能看到之前完成的 100-scene calibration 正式文件；当前 510k 在 `.part` 中。

旧代码还有一个严重恢复问题：重新运行时会删除未知 `.part`，因此**不要在停止当前进程后直接用旧代码重启**。

---

## 4. 性能瓶颈：局部热点与结构性热点

### 4.1 calibration 的局部热点

上传的 100-scene calibration：

| city | extract | graph | PUDO |
|---|---:|---:|---:|
| Boston | 135.19 s | 863.14 s | 546.41 s |
| Singapore | 329.42 s | 283.88 s | 583.68 s |

旧 PUDO 的 `graph_candidate_s` 占 Boston 约 77.5%、Singapore 约 82.3%；上一轮代码已对此做 exact vectorization / grid query。

Graph/PUDO calibration 时 CPU 利用明显不足，而 extraction 会明显压数据盘，因此上一轮并发参数仍然合理：

```bash
CAP_NUM_WORKERS=4
CAP_GRAPH_NUM_WORKERS=4
CAP_EXTRACT_CITY_JOBS=1
CAP_GRAPH_CITY_JOBS=2
CAP_PUDO_CITY_JOBS=4
```

### 4.2 当前更大的瓶颈是 cardinality

Boston calibration graph：

```text
2,391,919 nodes / 100 = 23,919.19 nodes/episode
2,655,887 edges / 100 = 26,558.87 edges/episode
```

如果对当前 510,741 scene 全部按同一量级建 graph：

```text
~12.22 billion nodes
~13.56 billion edges
```

这还只是 train/Boston 的当前前缀。即使 graph/PUDO 单 episode 再快几倍，最终磁盘、JSONL、后续 training I/O 和 QA 都会被海量相关快照拖垮。

因此最有效且不损害论文性质的优化不是降低 graph 精度，而是**把“完整官方 substrate 的可追溯性”和“昂贵 passenger-complete evidence 的物化范围”分开**。

### 4.3 extraction 本身还能否加速

当前 heavy extractor 每 scene 需要历史 ego/agent/traffic-light、mission/route、roadblock/lane geometry 等。`CAP_NUM_WORKERS` 主要帮助 nuPlan scenario discovery/filter；昂贵的 per-scenario hydration 仍按稳定顺序执行。报告也显示 extraction 对盘 I/O 敏感。

理论上可以进一步多进程化 hydration，但在同一大批 SQLite/GPKG 上会增加随机 I/O、map DB 竞争和内存，并使稳定 resume/order 更复杂。对 paper-scale 1000/250/250 每城而言，收益远小于风险，因此本轮没有通过并行化 history hydration 改变运行语义。

---

## 5. 推荐的数据集物化架构

### Layer 0：完整 immutable nuPlan identity inventory

新增：

```bash
bash scripts/build_abilitybench_data0_20260817.sh index-nuplan-full
```

它保存完整官方 split 中匹配 scene 的稳定 identity：episode ID、scenario token、log、scenario type、map、timestamp 等，不加载 20-step history、不建 accessibility graph、不做 PUDO。

它的作用是：保留“我究竟从哪个完整 nuPlan population 选择了 paper candidate”的可追溯性，而不是把每个 snapshot 都变成昂贵训练样本。

### Layer 1：paper-scale heavy candidate corpus

推荐新 stage：

```bash
bash scripts/build_abilitybench_data0_20260817.sh \
  bootstrap-candidates-paper-scale-staged
```

默认使用 config 中已经存在的：

```yaml
train: 1000 / city
val:    250 / city
test:   250 / city
```

即最多 6000 个 heavy traffic scenes，再为其构建 history / graph / PUDO。它是**候选语料**，不是最终 paper allowlist。

这与论文早期文档中“主论文 2k–4k 独立 scenes 总体 + 多 contract variants”的量级是一致方向的；当前 6000 候选多留了一层 evidence/audit/allowlist 淘汰余量。

若某 city/split 在 paper audit 后可用 episode 太少，应增加对应 cap，而不是放宽证据质量门槛。

### Layer 2：physical-site catalog + evidence audit

继续：

```bash
site-catalogs
prefill-audits
classify-audits
triage-audits
render-audit-packets
```

先对 PUDO 做 physical-site dedup，再审核，而不是对 capability variant 重复审计。

### Layer 3：paper evidence recompute + allowlist

完成真实审核后：

```bash
build-provenance
paper-preflight
rebuild-paper-evidence-full-staged
select-paper-allowlists
paper-build-allowlisted
merge-all
qa-strict
qa-bundle
```

最终 paper dataset 只由 paper evidence gate 和 allowlist 决定。

### Layer 4：T2/T3 闭环车辆规划

数据集构建完成不等于论文 T2/T3 已验证。必须继续运行已有的 nuPlan closed-loop export/eval pipeline，并把 collision、route completion、rule violations、ride motion metrics 等回灌到论文结果。

---

## 6. 本轮代码修改

### 6.1 crash-safe extraction checkpoint / exact resume

`extract_nuplan_scenes.py` 新增：

```text
scene_context_partial_state.json
```

checkpoint 记录：

- selection fingerprint；
- 已 fsync 的 `scenes.jsonl.part` / `episodes.jsonl.part` byte offsets；
- row count；
- last episode id；
- map/type counts。

如果 crash 后文件尾部有未提交内容，下次会先 truncate 到最后 committed byte offset，再继续。

新增：

```bash
--checkpoint_interval 1000
```

可用环境变量：

```bash
CAP_EXTRACT_CHECKPOINT_INTERVAL=1000
```

### 6.2 一次性收养你现在的 legacy `.part`

旧 `.part` 没有 fingerprint，不能仅凭“最后 episode ID 一样”就贸然续写。

新代码在：

```bash
CAP_ADOPT_EXTRACT_PARTIAL=1
```

时会：

1. scenes/episodes 两个 `.part` lockstep 扫描并校验；
2. 计算全部 prefix episode IDs 的有序 SHA-256；
3. 重新运行当前 ScenarioFilter identity sequence；
4. 证明 `.part` 的**每一个 episode ID 都是当前 selection 的严格前缀**；
5. 只有证明成功才建立新 checkpoint 并跳过前缀的 expensive hydration。

因此“全量 all-scenario”路径可以安全续建。但这只解决恢复，不代表全量 heavy build 是推荐论文策略。

### 6.3 graph resume 改成 per-episode fingerprint

旧 graph fingerprint 把整个 `scenes.jsonl` SHA 放入 build identity：只要 scene 集合新增 1 个 episode，所有 graph shard 都失效。

新版本将：

```text
static evidence/config fingerprint
+
per-episode route-context fingerprint
```

组合成 episode graph identity。这样增加无关 episode 不会让已完成 graph 全部重建；但真正改变某个 episode route/georef/GIS/config 时仍会安全失效。

### 6.4 full lightweight scenario index

新增 `scripts/index_nuplan_scenarios.py`，用于 Layer 0。其输出明确标记为 identity inventory，不是 passenger-complete sample。

### 6.5 可选 nuPlan 官方过滤器，但默认不启用

代码支持：

```yaml
timestamp_threshold_s: null
ego_displacement_minimum_m: null
```

nuPlan 官方 ScenarioFilter 本身支持 total limit、timestamp de-clustering、ego displacement filter。

为了不悄悄改变你当前 sample semantics，本轮 config 仍保持 `null`。后续可以基于完整 identity index 做 temporal-correlation 分析，再把 de-clustered set 作为一个明确报告的 secondary experiment，而不是现在随手设一个“魔法 5 秒”。

### 6.6 audit machine triage

新增 `scripts/triage_pudo_audits.py`，输出四个桶：

```text
MACHINE_REJECT_INVALID_OR_AMBIGUOUS
NEW_EVIDENCE_REQUIRED
VISUAL_REVIEW_REQUIRED
MACHINE_PASS_EXPLICIT_AUTHORITATIVE_SOURCE
```

默认 sanity / association routing gate：

```text
curb_height_m              0.00–0.50 m
sidewalk_width_m           0.30–20.0 m
deployment_clearance_m     0.30–20.0 m
running/cross slope        -1.0–1.0
physical match             <=15 m
regulation match           <=12 m
entrance candidate         <=80 m
```

**这些距离只是异常/复核分桶门槛，不是 accessibility feasibility truth。**

自动 machine-pass 只有一种情况：publication-critical evidence 全部是 Tier-A，且 source 自身已有明确 entrance semantic relation 和 stopping-legality segment relation。最近入口、最近 regulation、距离足够近都不会自动变成 truth。

### 6.7 visual audit packet

新增 `scripts/render_pudo_audit_packets.py`，生成：

```text
external/audits/<city>/visual_packets/
  index.html
  manifest.json
  *.png
```

每张图使用已经构建的本地 accessibility graph，显示：

- nearby pedestrian topology；
- PUDO；
- candidate / truth entrance；
- PUDO↔entrance spatial relationship。

不依赖在线 web tile，保证可复现、避免隐藏网络/许可依赖。

它能帮助确认“候选入口是否语义上合理”，但如果 source 根本不包含 curb height / deploy clearance 等真实物理事实，仍需要官方数据、照片或实测，不能用图形邻近关系替代。

---

## 7. 当前已有结果应该复用还是覆盖

### 推荐 paper-scale 路径：不要直接复用 510k 作为 candidate corpus

原因：

1. 它是 `max=0` selection 的**未完成前缀**；
2. 推荐 `max=1000` 的 nuPlan official `limit_total_scenarios` 不是简单“取 all selection 前 1000”；官方逻辑会优先保留 labelled scenario types 并对需要削减的类型做 deterministic equisampling；
3. 直接拿 510k 前缀截成 1000 会产生不必要的时间/DB 顺序偏差；
4. 重新抽取 1000 个 heavy scene 只需远小于你已经花掉的 36 小时，没必要为了复用少量 history 引入选择偏差。

因此推荐：**保留/归档当前 `.part`，然后按 config cap 重新选择 heavy candidate。**

可以继续复用：

- nuPlan DB / maps；
- external 下载文件；
- normalized OSM/city GIS/DEM/entrance/regulation/inventory；
- georeference；
- DB inspection；
- provenance source inputs；
- 已经独立完成、且 fingerprint 未改变的 static evidence。

100-scene calibration graph/PUDO 因本轮 graph version/fingerprint 收紧会有一次预期重建，避免新旧算法产物混合。之后新的 episode shard 可以安全 resume。

### 如果你坚持全量所有 scenario

可以安全续你现在的 510k 前缀，见第 10 节。但**不建议随后对所有 snapshot 全部 graph/PUDO**。

---

## 8. 推荐的完整构建指令

### 8.1 先停止当前旧 extractor，并保留 `.part`

使用 **Ctrl-C 正常停止**，不要 `kill -9`。停止后不要再启动旧代码。

```bash
export CAP_DATA=/data0/senzeyu2/dataset/CapPlan/data
export REPORTS="$CAP_DATA/external/reports"

SCENE_DIR="$CAP_DATA/outputs/prepared/train/scene_contexts/boston"
LEGACY="$CAP_DATA/checkpoints/legacy_all_train_boston_$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LEGACY"

mv "$SCENE_DIR/scenes.jsonl.part" "$LEGACY/" 2>/dev/null || true
mv "$SCENE_DIR/episodes.jsonl.part" "$LEGACY/" 2>/dev/null || true
cp "$REPORTS/commands/bootstrap_staged.train.boston.extract.log" "$LEGACY/" 2>/dev/null || true

echo "legacy partial saved to: $LEGACY"
```

### 8.2 覆盖安装优化代码

最稳妥的方法是先备份旧 repo，再解压完整优化版：

```bash
cd /home/senzeyu2/code
mv CapPlan "CapPlan.backup.$(date +%Y%m%d-%H%M%S)"
unzip /path/to/CapPlan_optimized_20260821.zip -d /home/senzeyu2/code
mv /home/senzeyu2/code/CapPlan_optimized_20260821 /home/senzeyu2/code/CapPlan
cd /home/senzeyu2/code/CapPlan
```

或者把 patch zip 按相对路径覆盖到现有 repo。

### 8.3 环境和依赖

```bash
cd /home/senzeyu2/code/CapPlan
pip install -r requirements.txt

export CAP_HOME=/home/senzeyu2/code/CapPlan
export CAP_DATA=/data0/senzeyu2/dataset/CapPlan/data
export DATA_ROOT="$CAP_DATA"
export CONFIG="$CAP_HOME/configs/abilitybench_nuplan_real_data0.yaml"
export EXT="$CAP_DATA/external"
export REPORTS="$EXT/reports"

export NUPLAN_DATA_ROOT="$CAP_DATA/nuplan"
export NUPLAN_MAPS_ROOT="$CAP_DATA/nuplan/maps"
export NUPLAN_MAP_VERSION=nuplan-maps-v1.0

export CAP_NUM_WORKERS=4
export CAP_GRAPH_NUM_WORKERS=4
export CAP_EXTRACT_CITY_JOBS=1
export CAP_GRAPH_CITY_JOBS=2
export CAP_PUDO_CITY_JOBS=4
export CAP_EXTRACT_CHECKPOINT_INTERVAL=1000
```

检查：

```bash
python -m compileall -q capplan scripts tests
bash -n scripts/build_abilitybench_data0_20260817.sh
pytest -q \
  tests/test_20260821_scale_resume_audit.py \
  tests/test_bootstrap_performance_optimizations.py \
  tests/test_gis_fusion_builders.py \
  tests/test_nuplan_adapter_modes.py \
  tests/test_nuplan_pudo_evidence.py \
  tests/test_dataset_pipeline_20260817_fixes.py \
  tests/test_dataset_pipeline_current_run_fixes.py \
  tests/test_dataset_pipeline_user_report_fixes.py
```

### 8.4 重新确认 DB inventory

```bash
bash scripts/build_abilitybench_data0_20260817.sh inspect-nuplan
```

检查 val/test：

```bash
jq '{status,db_count,city_db_counts,db_inventory_fingerprint,issues}' \
  "$REPORTS/nuplan_db_cities.val.json"
jq '{status,db_count,city_db_counts,db_inventory_fingerprint,issues}' \
  "$REPORTS/nuplan_db_cities.test.json"
```

要求：

```text
status = PASS
issues = []
db_inventory_fingerprint != null / empty
```

### 8.5 建完整 lightweight identity index

```bash
bash scripts/build_abilitybench_data0_20260817.sh index-nuplan-full
```

这是 full substrate inventory，不是 heavy paper sample。

### 8.6 paper-scale heavy candidate build（推荐）

```bash
bash scripts/build_abilitybench_data0_20260817.sh bootstrap-preflight
bash scripts/build_abilitybench_data0_20260817.sh qa-snapshot

bash scripts/build_abilitybench_data0_20260817.sh \
  bootstrap-candidates-paper-scale-staged
```

过程中可看：

```bash
bash scripts/build_abilitybench_data0_20260817.sh \
  bootstrap-performance-snapshot
```

### 8.7 site catalog / audit / machine triage / visualization

```bash
bash scripts/build_abilitybench_data0_20260817.sh site-catalogs
bash scripts/build_abilitybench_data0_20260817.sh prefill-audits
bash scripts/build_abilitybench_data0_20260817.sh classify-audits
bash scripts/build_abilitybench_data0_20260817.sh triage-audits
```

先抽样可视化，例如每城 100 条：

```bash
export CAP_AUDIT_RENDER_MAX_ROWS=100
export CAP_AUDIT_RENDER_RADIUS_M=120
bash scripts/build_abilitybench_data0_20260817.sh render-audit-packets
```

如果抽样确认绘图逻辑无误，需要全部画：

```bash
export CAP_AUDIT_RENDER_MAX_ROWS=0
bash scripts/build_abilitybench_data0_20260817.sh render-audit-packets
```

重点看：

```text
$EXT/audits/<city>/machine_reject_invalid_or_ambiguous.csv
$EXT/audits/<city>/new_evidence_required.csv
$EXT/audits/<city>/visual_review_required.csv
$EXT/audits/<city>/machine_pass_explicit_authoritative.csv
$EXT/audits/<city>/visual_packets/index.html
```

### 8.8 真实 reviewer 只处理需要人看的行

对于 `visual_review_required.csv`，人工逐行确认后设置 `review_accept=true`；若入口 candidate 确认是实际 service entrance，再设置 `entrance_linkage_approved=true`。

然后：

```bash
export REVIEWER_ID="<真实审核人员ID>"
export CONFIRM_SOURCE_REVIEW=YES

bash scripts/build_abilitybench_data0_20260817.sh review-source-complete-audits
bash scripts/build_abilitybench_data0_20260817.sh import-source-complete-audits
```

对于 `new_evidence_required.csv` 中 source 根本没有的物理事实，实际补充官方源/照片/人工测量，并填写：

```text
$EXT/audits/<city>/pudo_audit_manual_completed.csv
```

再运行：

```bash
bash scripts/build_abilitybench_data0_20260817.sh import-completed-manual-audits
```

不要伪造 `auditor_id`、`observed_at` 或通过距离阈值自动写入人工 truth。

### 8.9 冻结 provenance，重算 paper evidence，生成最终 dataset

```bash
bash scripts/build_abilitybench_data0_20260817.sh build-provenance
bash scripts/build_abilitybench_data0_20260817.sh paper-preflight

bash scripts/build_abilitybench_data0_20260817.sh \
  rebuild-paper-evidence-full-staged

bash scripts/build_abilitybench_data0_20260817.sh select-paper-allowlists
bash scripts/build_abilitybench_data0_20260817.sh qa-snapshot

bash scripts/build_abilitybench_data0_20260817.sh paper-build-allowlisted
bash scripts/build_abilitybench_data0_20260817.sh merge-all
bash scripts/build_abilitybench_data0_20260817.sh qa-strict
bash scripts/build_abilitybench_data0_20260817.sh qa-bundle
```

最终：

```text
$REPORTS/capplan_paper_qa_bundle.zip
```

---

## 9. 什么时候需要增加 1000/250/250 cap

不要先凭感觉把 heavy corpus 改成 all。先完成 site/audit/paper selection，检查每 city/split：

- paper allowlist episode 数；
- distinct PUDO site 数；
- distinct entrance 数；
- distinct logs 数；
- positive / infeasible certificate 分布；
- scenario type 分布；
- capability contract counterfactual coverage；
- train/val/test physical anchor disjointness；
- `paper_eligible_pudos_per_episode`；
- graph quality / missing evidence / inconclusive rate。

若某 city/split 因“候选太少”而不是“证据质量差”导致覆盖不足，再提高该 split cap（例如只增加 train），重新运行 heavy build。新的 extraction checkpoint 与 graph per-episode fingerprint 使扩容不会无条件推倒所有已完成 shard。

论文统计建议按 log / physical site 做 clustered reporting / bootstrap CI，避免把相邻 nuPlan 时间快照或同一 site 的 capability variants 当成完全独立的 N。

---

## 10. 如果你坚持继续当前 all-scenario 510k 前缀

这是**恢复路径，不是推荐 paper sampling 路径**。

先按第 8.1 节把旧 `.part` 归档、安装新代码。然后恢复 `.part`：

```bash
SCENE_DIR="$CAP_DATA/outputs/prepared/train/scene_contexts/boston"
mkdir -p "$SCENE_DIR"

cp "$LEGACY/scenes.jsonl.part" "$SCENE_DIR/scenes.jsonl.part"
cp "$LEGACY/episodes.jsonl.part" "$SCENE_DIR/episodes.jsonl.part"
```

启用一次性 legacy adoption：

```bash
export CAP_ADOPT_EXTRACT_PARTIAL=1
export CAP_EXTRACT_CHECKPOINT_INTERVAL=1000

bash scripts/build_abilitybench_data0_20260817.sh \
  bootstrap-candidates-full-staged
```

新代码会先完整验证 legacy prefix；只有 exact proof 通过才继续。如果报：

```text
legacy .part files are not the exact prefix ... refusing adoption
```

不要强行绕过。说明 selection/input/order 不一致，应保留旧 `.part` 作为归档并重新构建该 selection。

一旦成功收养并产生 `scene_context_partial_state.json`，之后可以：

```bash
unset CAP_ADOPT_EXTRACT_PARTIAL
```

后续中断直接重跑同一 selection 即可 checkpoint resume。

**即使 extraction 完成，我仍不建议对所有 snapshot 全量 graph/PUDO。** 更合理的是用 full identity index / scene IDs 选择 paper candidate，再只物化需要的 graph/PUDO。

---

## 11. 仍然必须人工/外部真值解决的部分

机器规则可以可靠处理：

- schema / type / numeric-range 错误；
- CRS / georeference consistency；
- source provenance 缺失；
- physical / legal / entrance spatial association 明显超距；
- duplicated site；
- train/val/test physical site leakage；
- evidence tier / stale fingerprint；
- source 自身已有 explicit semantic relation 的 authoritative linkage。

机器规则**不能仅凭最近邻或阈值**可靠替代：

- “这个 entrance feature 是否就是该 trip/POI 的实际可服务入口”；
- “这个 curb segment 是否在对应 service class / time / direction 下允许本车 PUDO”；
- 图上宽度是否等于真实有效净宽；
- ramp/lift 在真实停车 pose 是否可部署；
- 临时施工/障碍/路缘状态；
- source 未提供的 curb height / deploy clearance 等真实物理量。

这些必须来自权威显式关联、可审核影像/照片、实际观测或人工审核。新增 visual packet 的目标是**把肉眼审查成本降到最低**，而不是伪装成自动真值。

---

## 12. 最终判断

**数据逻辑：** paper-level 架构总体正确，尤其是 fail-closed evidence、site audit、allowlist、provenance、physical-anchor leakage 这些方向应该保留。

**当前执行指令：** `bootstrap-candidates-full-staged` + `max_scenarios=0` 对“完整候选 population 的轻量索引”是合理概念，但对“每个 snapshot 都建 heavy graph/PUDO”不合理，也不是论文所需样本性质的最佳实现。

**推荐最终方案：**

```text
完整 official nuPlan identity population
        ↓
确定性 paper-scale heavy candidate corpus
        ↓
physical site dedup
        ↓
authoritative prefill + machine triage + selective visual/manual review
        ↓
paper evidence recompute
        ↓
site-disjoint paper allowlist
        ↓
capability counterfactual variants + oracle labels
        ↓
closed-loop nuPlan vehicle evaluation
```

这样保留了论文所需的数据性质，同时把绝大部分无信息增益的重复 heavy materialization 去掉，并让中断恢复、后续增量扩容和人工审计都可控。

