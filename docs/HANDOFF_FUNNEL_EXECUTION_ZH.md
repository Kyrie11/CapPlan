# CapPlan：从静态能力规划到不可撤销接驳承诺的验证 SOP

> 范围：**先验证研究问题，再决定是否开发新学习/规划算法**。
> 该实现是独立的 handoff 诊断子系统；不修改原始 5,996-episode AbilityBench 语义数据、capability compiler、V16 parametric kernel、nuPlan DB/GIS 和 checkpoint。
> 请勿把 controlled counterfactual、独立均匀采样 profile、nuPlan 日志回放、CARLA blocker 事件，称为真人接驳成功或真实事件发生率。

## A. 论文故事和“这次没有验证的事项”

论文题目为 *Plan the Handoff, Not Only the Stop*。核心是：车辆的后续运动可重新规划，但乘客已执行的指引、实际位置和不可恢复的资源消耗不可以回滚。上车接驳是主任务，冻结后迁移到下车接续；车辆行驶属于系统验证而非接驳核心创新。

原始 TeX 仍携带旧 CapPlan 的搜索理论、旧静态主表和先前 V16 算法附录；研究故事已经转向在线 handoff，不能把历史静态实验当作新在线任务的效果。

本次 **没有** 运行真实 nuPlan DB 或 CARLA closed-loop；用户上传代码不包括 5,996-episode 实体图/数据库/可执行驾驶轨迹，所以交付的唯一数值效果来自可重现 toy fixture，不是四城 benchmark 的新实证结论。V16 结果 ZIP 证实：256 episodes、2048 requests 的有限图语义匹配与性能收益；`publication_integrated_vehicle_closed_loop_ready=false`。

## B. 工程文件 / 迁移边界

| 入口 | 功能 | 输出 | 真值限制 |
| --- | --- | --- | --- |
| `scripts/run_handoff_funnel.sh` | 稳定无版本号总入口 | `outputs/handoff_funnel/` | 默认生成 geometry 级诊断 |
| `scripts/mine_handoff_recourse_pairs.py` | O→A、O→B 均可，O→A→B 超预算；检查接口元数据 | `recourse_pairs.jsonl`, `mining_audit.json` | 必要局部反例，不能断言全局不可恢复 |
| `scripts/audit_handoff_funnel.py` | 逐层分母和人工审查队列 | `funnel_report.json`, `human_review_queue.csv` | 人工 CSV 默认全待审 |
| `scripts/export_handoff_motion_requests.py` | 导出带原始 stop_pose/token 的运动请求 | `motion_requests.jsonl` | 未经实际驾驶控制器执行，不是运动验证 |
| `scripts/build_handoff_temporal_overlays.py` | 两世界 controlled intervention，时间戳、指引确认、累计消耗 | `temporal_overlays.jsonl` | 事件为**人为假设**，概率是实验参数 |
| `capplan/handoff/finite_oracle.py` | 单观测/双 anchor 有限 belief-support AND–OR DP，守恒累计距离与时序 | 参见策略评测 | 精确仅相对于所声明的动作集合和完整的有限世界支持 |
| `scripts/evaluate_handoff_policies.py` | 决策、候选服务成功、距离、条件 FCR/PCRL | `policy_summary.json`,`policy_rollouts.jsonl` | 仅模拟 candidate handoff；不等于独立 VHCR/真实 boarding |
| `scripts/bootstrap_handoff_site_ci.py` | 重叠物理站点连通分量 clustered CI | `site_cluster_ci.json` | 少于 5 独立簇输出 `null`，不虚构置信区间 |
| `scripts/build_handoff_profile_suite.py` | canonical/empirical 强分离 | `*.jsonl`, `*.manifest.json` | empirical 必须外部真实来源，拒绝无来源构造 |
| `scripts/screen_handoff_profile_sweep.py` | 8 archetype 的区间均匀采样，对同一真实图交叉筛查 | `profile_sweep.json`,`profile_witnesses.jsonl` | 检查合同的**6 个子约束**，绝非全合同真实可执行证明 |
| `scripts/run_carla_handoff_intervention.py` | 有标定场景中 CARLA 受控 blocker 插入、生命周期记录 | 单次 event log | actor 执行不代表车辆成功绕行或 PUDO 完成 |
| `scripts/run_planner_regression.sh` | V16 实现兼容入口（从用户角度不绑定版本） | V16 原始可比评测 | 保留冻结 checkpoint 与实验签名 |
| `scripts/build_dataset_pipeline.sh` | nuPlan/GIS 数据构建稳定入口 | 原 hybrid datasets | 旧内部带日期脚本保留，以免改变数据指纹 |
| `scripts/clean_repository.py` | 归档 **73 个** V1–V15 旧入口脚本 | `legacy/historical_entrypoints.zip` | 不移除核心算法模块、数据文件、配置、测试与文档 |

**不改**：`capplan/planning/parametric_capability_kernel.py` 及其它所有被最新算法引用的模块、数据构建代码、source manifests、场景图、配置与测试。避免因仓促重命名导致历史 checkpoint、测试或数据指纹失效。若需要恢复旧脚本，运行 `python scripts/clean_repository.py --restore`。

## C. 服务器：最短可执行流程

服务器示例路径沿用旧数据构建记录，实际若不同，修改对应环境变量。

```bash
conda activate capplan
cd /home/senzeyu2/code
# 先把原有工程和结果留存！不要先删除原来的数据目录
cp -a CapPlan "CapPlan.backup.$(date +%Y%m%d-%H%M%S)"
# 推荐把交付 ZIP 解压到一个全新目录，再用该目录运行
mkdir -p /home/senzeyu2/code/capplan_handoff_release
unzip -q /path/to/CapPlan_handoff_clean.zip -d /home/senzeyu2/code/capplan_handoff_release
cd /home/senzeyu2/code/capplan_handoff_release/CapPlan
pip install -e .
python -m pytest -q tests/test_handoff_funnel_oracle.py tests/test_handoff_story_upgrade.py

export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export DATASET_TEST="$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test"
export OUTPUT_ROOT="$PWD/outputs/handoff_funnel"
export EPISODE_LIMIT=100
export SEED=13
export BUDGET_M=200
export EVENT_TIME_S=90
export PASSENGER_SPEED_MPS=1.0
export PROFILE_SWEEP=1
export PROFILE_SWEEP_SAMPLES=3
bash scripts/run_handoff_funnel.sh
```

第一次仅做 50–100 episode 的接口/数据检查，完成后再把 `EPISODE_LIMIT` 提高到 997（当前 test 的已有标注场景约数；以本机磁盘实际文件为准）。不要对全体 2000 多万 nuPlan scenario 重建 heavy graph。若已有数据实在不存在，先用稳定入口 `bash scripts/build_dataset_pipeline.sh hybrid-build` 续构，而不是从零重复昂贵的 GIS/DEM/PUDO 阶段。

**其他配置：** `REQUIRE_INTERFACE=1` 将无明确路缘间隙、宽度、门侧的 anchor 剔除；默认保留为仅待核查候选，不伪装为实测。`MOTION_PACKETS=/absolute/path/motion_packets.jsonl` 可读取第三步的外部独立运动验证文件；必须按完整有效时窗和 `motion_duration_s` 验证，不能填入后验推测的 `reachable=true`。

如想保留原有服务器工程并逐文件升级，使用随交付提供的**仅新增/修改文件 patch**，然后先 `python scripts/clean_repository.py` 做 dry-run 审核，再 `python scripts/clean_repository.py --apply` 归档旧实验入口。

## D. 七步 motivation funnel：每一步具体做什么

### Step 1：几何必要条件，保留所有分母

输入：`episodes.jsonl`、`pudo_anchors.jsonl`、逐 episode 的 `accessibility_graphs/`，以及当前 contract 的距离、坡度、宽度、step-free 和最低可信度字段。

依次统计：候选 anchor 数 → 合法 anchor → 图上存在完整 curb connector → 两个直接路线均符合预算 → 有向 A→B 可达 → 累计 O→A→B 超预算。记录每个 edge 原始 `source` 和 `metadata`；两个 PUDO 如果有相同 physical-site key，则拒绝作为不同站点。显式保存路径 edge list、签名/来源和超限量。

**注意：** 数据侧行人图实现沿用源图的双向步行拓扑假设；独立审查时必须排除现实单向/不可跨越边、错误 curb connector、不同现场海拔和坡度方向。即使找到上千例也不能宣称“有上千例真实接驳失败”。

### Step 2：接口完整性 + evidence boundary

人工或现有静态证据核查 curb legality、stop_pose、door side、deploy clearance、sidewalk width、boarding interval，以及 nuPlan/GIS/derived/simulated 各字段 provenance。`hybrid_evidence_complete=true` ≠ 市政现场测量。

`REQUIRE_INTERFACE=1` 是强过滤审查，不会重写底层 hybrid 数据。接口未知时状态为 **unknown**；不要使用合理补值把 unknown 填成 real。人工审查表必须保留 `PENDING_REVIEW`，并在专家查验后填现场可信度和原因。

### Step 3：方法专属车辆 motion check

实际导出：

```bash
python scripts/export_handoff_motion_requests.py \
  --pairs "$OUTPUT_ROOT/mining/recourse_pairs.jsonl" \
  --output "$OUTPUT_ROOT/motion_requests.jsonl"
```

在有 nuPlan 数据+SDK 的服务器上，用**实际选中的 A、B stop_pose**（不是原日志车辆目标）检查相关交通窗内的 A→B 可达、无碰撞、交通规则、停靠合法性、接口、到达/驻留时窗。

合法结果逐行写：

```json
{"episode_id":"...","from_anchor":"A","to_anchor":"B","reachable":true,"collision_free":true,"legal_stop":true,"interface_verified":true,"method_specific_motion_checked":true,"motion_duration_s":20.0,"valid_start_s":90.0,"valid_end_s":130.0,"source":"actual_nuplan_custom_planner_execution","planner_hash":"...","trajectory_id":"...","simulator_log":"..."}
```

必须由外部控制器和日志生成，**不可由本包的距离、车辆平均速度或原 ego 轨迹冒充**。当前 `capplan/planning/trajectory_refinement.py` 中 nuPlan 方法接口尚有未完成部分，所以本交付没有宣称可运行 method-specific vehicle closed-loop。`scripts/export_nuplan_closed_loop_jobs.py` 是场景选择器，不是 PUDO motion proof。

### Step 4：独立人工审查

先核查 20–30 个有实际 motion packet、不同城市和物理站点的案例，补充若干无超预算差异的负例及接近阈值的边界例。至少审查公开地图/街景、curb side、合法性、上车空间、出入口连接、可用性是否可能随时间变化。人工审查 `human_review_queue.csv` 的正例不是现场已经发生故障的事故记录。报告通过比例和专家不确定项，不要删掉失败样本。

### Step 5：精确有限世界因果 oracle

执行 `scripts/build_handoff_temporal_overlays.py` 和 `scripts/evaluate_handoff_policies.py`。当前动作集合：从 O 指引到 A/B、从 A 失败后指引到 B、在 O 决定观测 A 并等到观测时刻。状态包含真实 passenger anchor、已执行移动量、时钟、世界 belief support、是否已观测；观测只按实际时刻的值分支，不能从未来 truth 提前泄漏。登乘必须在整个所声明时间窗保持候选点可用。算法是有限支持 AND–OR 动态规划，不是无穷动作空间 POMDP oracle，也不含实体车辆/救援动作。

可重复进行 `CLEAR_PROB=0.1,0.5,0.9` 与 `OBS_DELAY_S=0,5,20`、`BOARDING_S=10,30` 的敏感性实验，显示控制事件权重对策略/结果的影响。这些权重全是**人为干预概率**。

### Step 6：五类基线，定义不能偷换

- `reset_and_replan`：失败后只用“当前新增腿长”检查原总预算，诊断历史消耗被错误丢失。
- `actual_ledger_myopic`：同样最近 anchor 选择，但从实际位置严格累积历史行走；可能安全拒绝但失去服务。
- `verify_then_guide`：在事件后观测 A，基于所获得的信息直接选择 A/B。使用与 exact 相同的事件时钟知识，避免不公平的 clairvoyance。
- `robust_fixed_anchor`：不获取信息，始终选已知在两个世界都可用的 B。
- `generic_resource_shield_proxy`：目前**只是**保守 B 策略的校验 proxy；不是 Ajdarów 等 AAAI 2023 shield 原论文的完整实现，不得在正文表格冒称该论文 baseline。真实强基线仍需单独复现/适配。

外加 `exact_finite_support` 作为当前动作集上的 reference。报告 candidate success、距离、false-commit 和 post-commitment recourse loss；由于没有物理 co-location 和身份/boarding 完整确认，这些数值 **不是 VHCR、SCR 或系统级 FS-Coverage**。`SEC` 需要有真实观测有效区间及相应执行日志；当前不能计算的字段必须填空。

### Step 7：基于否定结果的研究决策门槛

仅在上述证据足够后再实现下一层复杂算法：

1. 如果在严格路缘拓扑和车辆运动过滤后，几何双 anchor witness 接近零，则主 motivation 要调整，而不是继续创造 synthetic positive。
2. 如果 `actual_ledger_myopic` 与 reset 差异几乎没有，历史不可恢复能力可能不是主要机制。
3. 如果 `verify_then_guide` 与 exact 在所有关键场景中的保证和负担都相同，新算法需要以强基线的实际计算效率/时效/信息结构为题，而不是再堆新的 belief kernel。
4. 如果 `robust_fixed_anchor` 既不增加负担也不降低覆盖率，则“何时观测/何时承诺”的收益不足。
5. 如需要扩大算法贡献，优先纳入时间窗、双方异步、多个互斥可用的 anchor、部分可靠的观测、观察后失效与可恢复确认，但每个新事件都要明确来源、可观测条件、物理意义与真实外推限制。
6. 冻结模型后，再处理 drop-off (`alight` 后才发现 egress 不可达)；不同任务共用算法语义而不重新调参，最后才组成完整行程。

## E. Profile 与数据是否需要重建？

**不应重建现有 5,996 场景 heavy corpus。** 目前主要缺的是事件层、物理接口与独立闭环、人工现场核查，而不是多生成几十万几乎相同的静态场景。保留原 official train/val/test，另外基于 `hybrid_physical_site_key` 做 site-disjoint 或 connected-component 分组；不要因为同站点泄漏而把所有官方场景清空。

当前 `configs/capability_profiles.yaml` 八 archetype + 区间独立均匀采样，只可作为 canonical 能力语义压力测试，不能作为人群分布。要避免把 `manual_wheelchair` 标签理解为每个真实使用者的固有能力，强制使用具体 trip-level capability contract。`scripts/screen_handoff_profile_sweep.py` 只检查 distance/width/slope/cross-slope/step-free/confidence 六个维度，因此输出属于 necessary witness。接口、剩余距离、等待耐受、环境依赖等未检查约束要明确写在 JSON 内。

若要生成真实 empirical-evaluation suite：

```bash
python scripts/build_handoff_profile_suite.py \
  --suite empirical --empirical_input /path/to/consented_task_profiles.jsonl \
  --output outputs/handoff_funnel/empirical_profiles.jsonl
```

必填字段 `profile_id`, `consent_scope`, `sampling_frame`, `collection_protocol`, `source_reference`, `measurement_uncertainty`, `sampling_weight`, `mobility`, `wait`, `interface`。缺少来源直接拒绝，不允许 uniform 伪装成实证。用户权重来源、样本设计、联合相关性还必须人工审计。

## F. CARLA：只用作下一阶段受控干预

CARLA 世界和 nuPlan GIS 坐标不共用；必须先人工标定某个真实/等价 curb site，然后复制 `configs/handoff_carla_intervention.example.json` 改为准确的 `carla_map`、`calibration_id`、`blocker_transform`。

```bash
python scripts/run_carla_handoff_intervention.py \
  --config /path/to/calibrated_intervention.json \
  --output outputs/handoff_funnel/carla_intervention.dry_run.json

# 仅在 CARLA 服务可用、场景已标定、外部车辆闭环控制器启动后执行：
python scripts/run_carla_handoff_intervention.py \
  --config /path/to/calibrated_intervention.json \
  --output outputs/handoff_funnel/carla_intervention.executed.json \
  --execute --host 127.0.0.1 --port 2000
```

该代码在指定 simulation timestamp 放置 blocker、等待指定 duration、最后清理 actor，记录真正执行时刻。失败则报告 spawn error。它**不会**自动解决 nuPlan→CARLA 地图/坐标配准或验证行驶轨迹，不能把成功放置 barrier 当作车辆证明。

## G. 如何判断本轮输出是否值得继续投入 CCF-A

逐个查看：

```text
outputs/handoff_funnel/mining/mining_audit.json
outputs/handoff_funnel/audit/funnel_report.json
outputs/handoff_funnel/audit/human_review_queue.csv
outputs/handoff_funnel/motion_requests.jsonl
outputs/handoff_funnel/temporal_overlays.jsonl
outputs/handoff_funnel/policies/policy_summary.json
outputs/handoff_funnel/policies/site_cluster_ci.json
outputs/handoff_funnel/profile_sweep/profile_sweep.json
outputs/handoff_funnel/research_gate.json
```

建议做一张“筛选分母表”+ 3–5 个分别对应不同真实物理站点的高质量图 + 一张策略覆盖/负担/PCRL/FCR 表。没有独立 motion 证明前“成功的车辆可恢复”栏必须是 `unverified`。没有现场事件统计前不要给“现实 PUDO 改点故障率”的数字。若 cluster 不足 CI 输出 `null`，后续以更多地点增加独立性，而不是假装大量重复 episode 是独立观测。

研究主张也要收窄：SAE J3261 (2026) 已认可 complete trip；FlexiPool (AAAI 2026) 已联合步行 PUDO/车辆路线；Shielding in Resource-Constrained Goal POMDPs (AAAI 2023) 已处理一般资源安全问题。仍可争取的**候选空白**是带车辆/乘客异步、信息有效时窗、不可回滚的人类指导和物理确认的耦合策略，但必须与这些相邻工作实证和理论区分，不能宣称全新 POMDP 或首次完整无障碍行程。
