# CapPlan handoff motivation funnel：可证伪研究流程（稳定入口）

> **定位：** 这是用于发现和排除研究问题的实验设施，不是已经证明 CCF-A 原创性的系统。`nuPlan+GIS` 承担真实空间/交通上下文； temporal overlays 属于带来源标识的受控干预。没有真实 method-specific 车辆轨迹、现场观察和 boarding 确认，不得把输出写成真实世界事件发生率、VHCR 或 SOTA。

## 0. 交付保护和环境

本包为从原始 CapPlan 复制出的已清理工程；`capplan/planning/`、`capplan/data/`、dataset pipeline、能力 profile 配置和 regression wrapper 均保留。过时的 73 个左右实验**入口**在 `legacy/historical_entrypoints.zip`（保留字节和 SHA256，随全代码包交付），不删除核心实现。`scripts/clean_repository.py` 默认只审计，可以 `--restore` 复原历史入口。不要在原项目上直接覆盖数据或训练权重。

```bash
conda activate capplan
cd /home/senzeyu2/code
mkdir -p CapPlan_handoff_release
unzip -q /path/to/CapPlan_handoff_research_ready.zip -d CapPlan_handoff_release
cd CapPlan_handoff_release/CapPlan
pip install -e .
python -m pytest -q \
  tests/test_handoff_funnel_oracle.py \
  tests/test_handoff_funnel_integration.py \
  tests/test_handoff_story_upgrade.py \
  tests/test_manual_audit_semantics.py
python -m compileall -q capplan scripts
bash -n scripts/run_handoff_funnel.sh scripts/run_handoff_sensitivity.sh
```

**无真实数据快速回归（不用于论文）：**

```bash
python scripts/build_handoff_smoke_fixture.py --output_dir /tmp/capplan_synthetic_fixture
DATASET_TEST=/tmp/capplan_synthetic_fixture \
 OUTPUT_ROOT="$PWD/outputs/smoke_funnel" EPISODE_LIMIT=6 \
 REQUIRE_CURB_CONNECTOR=1 REQUIRE_INTERFACE=1 \
 bash scripts/run_handoff_funnel.sh
OUTPUT_ROOT="$PWD/outputs/smoke_funnel" bash scripts/run_handoff_sensitivity.sh
```

## 1. 第一次真实图抽检：50–100 个 episode

使用现有 hybrid dataset，**不要重新生成整个 nuPlan/GIS corpus**。确保目录内存在 `episodes.jsonl`、`pudo_anchors.jsonl`、`accessibility_graphs/<episode_id>.jsonl` 或 per-episode nodes/edges JSONL。脚本会复用源图和 provenance，不会调用 nuPlan DB 构建阶段。

```bash
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export DATASET_TEST="$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test"
export OUTPUT_ROOT="$PWD/outputs/handoff_funnel_pilot"
export EPISODE_LIMIT=100 SEED=13
export BUDGET_M=200 PASSENGER_SPEED_MPS=1.0
export EVENT_TIMING=before_a_arrival EVENT_LEAD_S=15
export OBS_SCHEDULE=immediate OBS_DELAY_S=5 ACK_DELAY_S=3
export PROFILE_SWEEP=1 PROFILE_SWEEP_SAMPLES=3
export REQUIRE_INTERFACE=0 REQUIRE_CURB_CONNECTOR=0
bash scripts/run_handoff_funnel.sh
OUTPUT_ROOT="$OUTPUT_ROOT" bash scripts/run_handoff_sensitivity.sh
```

若 `mining_audit.json.errors` 非零，先解决字段兼容，不应把错误计作阴性样本。如果 `distance_recourse_candidates=0`，确认 origin/PUDO 是真实 graph node，检查 `pedestrian_graph_nodes`、字段 `adjacent_ped_node_id`、边坡度/宽度/step-free 过滤条件；**不要伪造正例**。

## 2. 大样本筛选、强证据子集、两套 profile

第一轮通过后建议重新在独立目录做 test 完整标注 episode（有多少按机器实际 `episodes.jsonl` 为准），固定 `SEED=13`：

```bash
export OUTPUT_ROOT="$PWD/outputs/handoff_funnel_test_all"
export EPISODE_LIMIT=1000000
export PROFILE_SWEEP=1 PROFILE_SWEEP_SAMPLES=5
bash scripts/run_handoff_funnel.sh
OUTPUT_ROOT="$OUTPUT_ROOT" bash scripts/run_handoff_sensitivity.sh
```

这只是几何 + 受控事件模型筛选。加强路缘限制时另开一个输出目录重新运行：

```bash
export OUTPUT_ROOT="$PWD/outputs/handoff_funnel_strict_curb"
export REQUIRE_INTERFACE=1 REQUIRE_CURB_CONNECTOR=1
export PROFILE_SWEEP=0
bash scripts/run_handoff_funnel.sh
```

在 `canonical/counterfactual` 套件中，原来的八 archetype 只用于逐轴及联合阈值压力测试，没有人口权重；区间均匀样本不是对现实乘客能力的统计估计。`profile_sweep.json` 的 6 个过滤维度仍是必要条件，而非完整的人-车接驳执行证明。`empirical` 套件必须外部提供经过同意、有抽样框、联合分布、测量不确定性和研究来源的记录，代码拒绝伪造：

```bash
python scripts/build_handoff_profile_suite.py --suite empirical \
  --empirical_input /path/to/consented_task_profiles.jsonl \
  --output "$PWD/outputs/empirical_profiles.jsonl"
```

如果没有真实受试者数据，就在论文中明示 `empirical suite NOT AVAILABLE`，不得从 8 archetype 推算真实人口百分比。

## 3. 七步 funnel 的报告与准入边界

| Step | 输出 | 支持的结论 | 尚不支持 |
|---|---|---|---|
| 1 真实图几何 | `mining/mining_audit.json`, `recourse_pairs.jsonl` | 双直接路均预算内、A→B 加已消耗超预算的图上必要见证 | 全局无恢复、现实故障发生率 |
| 2 curb/接口 | `audit/funnel_report.json` + 人工队列 | GIS 字段存在与显式 curb connector 过滤后的条件数量 | 未经现场核验的坡道、停车合法性 |
| 3 车辆动作 | `motion_requests.jsonl`，外部 motion packets | 以正确 A/B stop_pose 和事件时间窗提交实际验证任务；匹配的 motion packet 可做门槛筛选 | 导出任务不等于车辆执行成功 |
| 4 人工核验 | `audit/human_review_queue.csv` | 高差异案例的源证据、独立物理站点人工结论 | 无审查/重复站点不等于独立真实正例 |
| 5 因果有限世界 | `temporal_overlays.jsonl`、`policy_rollouts.jsonl` | 已执行路径、资源与观察时序的有限支持策略反例 | 完整城市 POMDP 全局最优 |
| 6 强基线检验 | `policies/policy_summary.json`, `sensitivity/sensitivity_report.json` | paired controlled-event 负担/接受质量/承诺后恢复差距 | published strength shielding/SOTA 复现 |
| 7 研究决策 | `research_gate.json` | 哪些证据仍缺失、是否值得继续推进 | 自动授予 CCF-A 创新性 |

**关键修复：** 本次观测调度不再从隐藏世界轨迹读取最晚的未来事件时间。`OBS_SCHEDULE=immediate` 表示立即请求、延迟后得到当前观测；`OBS_SCHEDULE=fixed` 需要外部已知的绝对请求时刻（`OBS_REQUEST_TIME_S=...`）；`event_aligned_upper_bound` 只是**特权知道实验事件时刻的上界**，代码标有 `PRIVILEGED_`，不能用作现实可部署策略。这一点能直接揭示 stale verification 的失败。

`EVENT_TIMING=before_a_arrival` 通过路长和声明的速度，把受控事件放在 A 到达前，避免固定 t=90 恰好晚于所有 handoff 造成无效反例；这并**不是**从真实日志里识别事件。在 `sensitivity` 中 `late_event_negative_control` 检验无效对照。任何受控概率 (`CLEAR_PROB`) 都不是现实频率。

## 4. 车辆 motion 结果的独立输入（真实第三步）

`motion_requests.jsonl` 里已经包含真实 stop_pose、原始 scenario token、事件时刻和需要验证的条件。用可调用的**方法专属 nuPlan planner/simulator**执行 A→B，并由独立日志核查碰撞、停靠合法性、接口和完整动作时间窗。现有原始 `trajectory_refinement.py` 的闭环接入未完成，本包不能自动替你产出一个真实 `reachable=true`。

真实运动验证完成后按 `configs/handoff_motion_evidence.example.jsonl` 写 JSONL；至少包含 episode_id/from_anchor/to_anchor、`reachable`, `collision_free`, `legal_stop`, `interface_verified`, `method_specific_motion_checked` 全 true、`motion_duration_s`, `valid_start_s`,`valid_end_s`, `source`, `planner_hash`, `trajectory_id`, `simulator_log`，并保证证据覆盖 `[event_time,event_time+motion_duration]`。不要把平均车速推测、静态车道可达或 CARLA 放置障碍物记录替换成方法专属车辆证明。

```bash
export MOTION_PACKETS=/absolute/path/to/independent_nuplan_motion_packets.jsonl
export OUTPUT_ROOT="$PWD/outputs/handoff_funnel_motion_checked"
export REQUIRE_INTERFACE=1 REQUIRE_CURB_CONNECTOR=1
bash scripts/run_handoff_funnel.sh
```

`audit/human_review_queue.csv` 初始均为 `PENDING_REVIEW`；审查完填写 `PASS`/`FAIL` 以及静态几何、接口、source 和 reviewer 字段。相同工作目录重跑会按 `episode_id/A/B` 保留已有的人工字段，但**建议先备份经审查的 CSV**。CARLA 受控障碍是独立下一层证据，须由地图配准与闭环执行日志支撑。

## 5. 统计与三个可证伪问题

**Q1：真实物理结构是否存在？** 报告：随机抽检的 episodes、可用 anchors、已验证 curb connector 数、可达 pair、满足预算不对称的 pair、去重 site pairs、严格接口后通过率、独立 reviewer pass/fail。要求**至少五个独立物理站点**的跨城样本，任何源自模拟数据字段都应单独统计，不当作市政现场测量。

**Q2：传统规划是否系统性失败？** 报告 reset-and-replan 的实际预算违反次数与 candidate success、actual-ledger myopic 的正确拒绝及服务损失；同时要比较 verified method-specific 车辆动作、精确图剩余替代点以及固定 robust B。当前两锚策略 proxy **不是** AAAI 2023 resource-constrained belief shield 的正式实现；在没有真实强基线复现前，只可说这些本地策略类别在受控模型中的行为，不能说整个领域已有方法均失败。

**Q3：选择何时发出指引是否真的改善？** 必须看 `sensitivity` 中：立即/陈旧观察 vs 实验知道事件时刻的特权上界，观察延迟、登乘持续时间、事件低/高概率、迟发事件负对照。只有 exact 相对 `verify_then_guide` 与 `robust_fixed_anchor` 都有有意义的覆盖—负担—时效 tradeoff，才值得探索新的能力投影 belief/recourse kernel。若固定 B 依然 100% 保障、且几乎不增加负担，简单规则基线足够；应收窄或调整创新诉求。

统计报告只允许在相同被筛选样本上解释**条件差异**，不能将 challenge 样本正例率混成随机全城概率。将 sampled cohort 和 challenge slice 分开保存；site-cluster CI 不足五个独立簇时返回 `null`。对 provenance 为 `synthetic` 的字段及合同不符合真实人群的情形，保留对外效度的警告。

## 6. 结果归档、上传和后续迭代

```bash
python scripts/pack_handoff_results.py \
 --results_dir "$PWD/outputs/handoff_funnel_test_all" \
 --output "$PWD/outputs/handoff_funnel_test_all_results.zip" \
 --examples 300
# 若一定要传所有 JSONL，则增加 --full_jsonl（体积可能较大）
```

上传：`handoff_funnel_test_all_results.zip`、`handoff_funnel_strict_curb` 的独立结果包、真实 motion packets 的来源说明和 20–30 个人工审查的 **PASS/FAIL/UNKNOWN** 记录。随后再决定是否扩展三/多 PUDO、可失效观测、人工确认援助、双方不同步、drop-off 迁移。**不能在这之前声称已经证明新算法 CCF-A 潜力。**
