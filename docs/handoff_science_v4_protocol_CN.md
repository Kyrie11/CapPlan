# CapPlan Research-Gate V4：证据闭环、失败主张证伪与分支实验 SOP

**审计对象：**本轮 `main.tex`、`CapPlan(3).zip`、`handoff_gates_review_bundle.zip`，以及《数据集构建(1).md》第七次回答。代码增量基于当前上传 ZIP，不应覆盖不同 checkout。

## 0. 科学结论及证据层级

1. A/B 几何屏选命中 ≠ 现实失效。全量筛选共 65,813 行候选，来自 997 个 test scenes，图结构来源含真实 GIS，但所有行缺少完整双端 typed curb + 独立运动验证，不是发生率。
2. Gate1 选了 32 对物理站点，四城各 8 对：审计的是 convenience sample，尚无独立审核成功案例；Gate2 32 个请求的 method-specific motion 返回 0。
3. Gate3 原始 32 对，通知发行 0/30/60/90/120/180 秒，恢复可行数 32/4/3/0/0/0；但旧代码用 `max(0,notice-ack)` 算进度，会高估收到指令后实际可恢复性，故 V4 修为通知发行 + ACK 延时，并可比较 ACK 期间停止/继续两种动作模型。此实验是图级必要条件，不是系统级保证。
4. Gate4 192 次 finite-support 测试 exact 与固定 B 均 96.875% support guarantee；当前 profile-time grid 只绑定距离与人为 wait budget，不验证其余身体/接口合同。新的 full-profile-path audit 可以审计部分资源，但同样不是现场真值。
5. 《数据集构建》第七次回答给出的 `HYBRID_BENCHMARK_AUDIT_CHECK=PASS` 指向结构完整性与语义一致性，**不能解释为四城 curb/装载接口真实测量通过**。保持原 5,996 heavy scenes 数据，增加独立的证据层，不重建所有图。

## 1. 三层数据，拒绝交叉冒充

- H: 原始 hybrid（nuPlan/GIS + observed/derived/simulated, static site correlated & dynamic episode variation），可训练搜索及压力实验。
- A: audited physical site（道路边、人行接点、实际 curb ramp、过街连接、净宽/横坡、停靠权限、车辆设备部署占用、照片/记录、时间、来源、相同站点负对照）。仅经核验的字段可提升证据等级；所有未经核验字段保持 UNKNOWN。
- M: 审核过的 motion/interaction（车辆真实坐标系和姿态、轨迹、碰撞、路口/交通合规、到达/等待时窗、乘客接续执行），nuPlan closed loop/CARLA controlled intervention 不等于实地乘客 boarding confirmed。

**关键物理实体必须拆开**：pedestrian graph waypoint / curb interaction polygon / vehicle stop pose / ramp or door swept region。绝不把 `curb_pose = vehicle_stop_pose` 当作实测部署可用。

## 2. 部署与校验

```bash
conda activate capplan
cd /home/senzeyu2/code/CapPlan
# 先备份：cp -a ...
patch -p1 --dry-run < /path/to/CapPlan_handoff_science_v4.patch
patch -p1 < /path/to/CapPlan_handoff_science_v4.patch
python -m pytest -q tests/test_handoff_science_v4.py tests/test_handoff_research_gates.py tests/test_handoff_funnel_oracle.py
```

初始化路径（请核对本机实际文件）：

```bash
export CAP_HOME=/home/senzeyu2/code/CapPlan
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export TEST_DATASET="$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test"
export PAIRS="$CAP_HOME/outputs/handoff_funnel_test_all/mining/recourse_pairs.jsonl"
export OVERLAYS="$CAP_HOME/outputs/handoff_funnel_v2/falsification/overlays.jsonl"
export PROFILES="$CAP_HOME/outputs/handoff_funnel_v2/canonical_coupled.jsonl"
export STUDY="$CAP_HOME/outputs/handoff_science_v4"
mkdir -p "$STUDY"
for f in "$PAIRS" "$OVERLAYS" "$PROFILES" "$TEST_DATASET/episodes.jsonl"; do test -e "$f" || echo "MISSING: $f"; done
```

**绝不使用上传的精简 300-row 例子代替服务器全量的 65,813-row 配对文件。** 若项目实际路径不同，更改变量而不是猜测已存在。

## 3. 阶段 A：无须 CARLA，先复核真实来源与候选筛选（立即运行）

```bash
cd "$CAP_HOME"
python scripts/run_handoff_research_gates.py prepare \
  --pairs "$PAIRS" --overlays "$OVERLAYS" --profiles "$PROFILES" \
  --dataset_dir "$TEST_DATASET" --output_dir "$STUDY" \
  --per_city 8 --midroute_limit 32 \
  --notice_times_s 0,30,60,90,120,180 \
  --speed_mps 1.0 --ack_s 3.0 \
  --max_grid_sites 8 --max_grid_profiles 8 \
  --wait_budgets 30,120,none --evaluate_grid
```

本轮 `prepare` 新增产物：
- `gate1/physical_evidence_v4.json`：字段级 simulated/observed/derived；stop/curb pose alias。
- `gate0_unscreened_episode_controls.{jsonl,manifest.json}`：只按城市和稳定 hash 选择未先看失败标签的 benchmark episode；用于将来的几何对照，不是自然发生率。
- `gate3/midroute.jsonl`、`gate3/midroute.summary.json`：修正可行动作时钟的几何恢复。
- `gate3/alternative_pudos.{jsonl,summary.json}`：检查乘客到达 A 后是否有其他距离可行的第三 PUDO（不代表实际合法停靠或车辆可达）。
- `gate4/profile_paths_v4.{jsonl,summary.json}`：用原始 graph + canonical 全维参数的一部分检查 O→A/O→B 与路缘接口。结果只能被称为 hybrid subset screening。
- `research_triage_v4.json`：分支建议；任何 physical / novelty PASS 均不能由这段脚本自动赋予。

先看：

```bash
cat "$STUDY/gate1/physical_evidence_v4.json"
cat "$STUDY/gate3/midroute.summary.json"
cat "$STUDY/gate3/alternative_pudos.summary.json"
cat "$STUDY/gate4/profile_paths_v4.summary.json"
cat "$STUDY/research_triage_v4.json"
```

**对旧时钟的 sensitivity**（通知期间停/走、不同速度/确认延时）：

```bash
SELECTED="$STUDY/gate1/selected_review_pairs.jsonl"
for speed in 0.6 1.0 1.4; do
  python scripts/audit_handoff_midroute.py \
    --dataset_dir "$TEST_DATASET" --pairs "$SELECTED" \
    --output "$STUDY/gate3/midroute_speed_${speed}.jsonl" \
    --notice_times_s 0,15,30,45,60,90,120 --speed_mps "$speed" --ack_s 5 --limit 32
 done
python scripts/audit_handoff_midroute.py \
  --dataset_dir "$TEST_DATASET" --pairs "$SELECTED" \
  --output "$STUDY/gate3/midroute_pause_during_ack.jsonl" \
  --notice_times_s 0,15,30,45,60,90,120 --speed_mps 1 --ack_s 5 \
  --pause_during_ack --limit 32
```

注意修正后 0 秒通知也必须等待 ACK，不能用旧版本的 32/32 数字替换新结果。更现实的测试还需要确认通信延迟、消息是否确认、真实路径进度、临时停止及沿途行动；现阶段仅是时钟和移动上界压力测试。

## 4. 阶段 B：独立 PUDO 核验（人工优先）

先查看 `$STUDY/gate1/site_review_queue.csv` 和 `anchor_control_queue.csv`，选 8–12 对做独立审核 pilot，然后扩至四城各 8 对。至少需：原始 street/municipal GIS 或可合法使用的现场影像链接、时间、观察者、地图版本、行人到路缘连接、实际 curb/坡道几何、合法停车时窗、可停姿态、轮椅车辆设备部署边界、遮挡/施工状态、审查不确定性。保留无法辨认的 UNKNOWN，严格禁止图上吸附生成实地坡道。

`site_review_queue.csv` 的 PASS 不能自动使某个案例变成 'verified real system failure'；`review_handoff_bundles.py`/`inspect_review()` 只能做报告结构校验，还需要逐条确认 primary source 和样本的正、负对照。

若大部分物理接口不匹配：把原 claim 收缩为“hybrid screen 的候选空间需要站点级验证”，转到 data/interface-grounded planning；不要为了找反例做 false-positive connector 填充。

## 5. 阶段 C：车辆运动和事件时间验证

本轮 `prepare` 自动写入 `gate2/motion_requests.jsonl`。实际使用运行环境中的 nuPlan/CARLA/其他物理检查器得到具体车辆运动，返回 `configs/handoff_motion_evidence.example.jsonl` 指定的字段，确保事件时窗、场景 token、地图和日志 hash 一致。

```bash
python scripts/audit_gate2_motion.py \
  --requests "$STUDY/gate2/motion_requests.jsonl" \
  --claims /path/to/method_specific_real_motion_claims.jsonl \
  --log_root /path/to/actual_simulator_logs \
  --output_dir "$STUDY/gate2"
```

**重要：**`log_bound_motion_attestations` 不等于独立验证的真实接驳。CARLA `run_carla_handoff_intervention.py` 当前只负责受控阻塞生成，并不能证明车辆方法或 boarding 通过；nuPlan 地图坐标不能直接当 CARLA 地图坐标。车辆姿态/真实 stop polygon、人车 rendezvous、装载接口另需验证。

## 6. 阶段 D：乘客 functional profile 的实用评估与真实事件回放

**Canonical**：保留 8 archetypes × independent_uniform 和 nested coupled_quantile，两种设计必须分别命名。每个 contract 应有距离、速度/停顿范围、坡度、横坡、宽度、step-free、设备、等待暴露、信息接收、车辆门侧与停靠时窗；当前 `profile_paths_v4` 只计算静态步行/部分路缘资源，不包含实时消息及车辆设备。请勿由 `profile_paths_v4` 的 pass 得出 passenger-complete success。

**Empirical**：不人工把 uniform archetype 重命名为人群样本。使用现有 `build_handoff_profile_suite.py --suite empirical` 输入同意范围内的 task-based 功能量测，保存 `sampling_frame`、`collection_protocol`、独立来源、uncertainty 和可溯源的 sampling weight。只有实际匹配人群抽样框才能估计 population prevalence。建议记录任务中移动速度的区间、休息转移、消息 ACK 延迟及设备部署参数；无需保留真实姓名或敏感医疗诊断。

**动态在线评判接口（只读，不自动发指令）**：

```bash
python scripts/replay_handoff_live_state_v4.py \
  --profile /path/to/one_consented_profile.json \
  --events /path/to/real_or_controlled_timestamped_events.jsonl \
  --output "$STUDY/gate4/live_state_replay.jsonl"
```

事件按 `timestamp_s` 严格排序。支持 `move`（distance_m、source）、`wait`（duration_s）、`observe`（field/value/valid_until_s/evidence_class/source）、`issue`（instruction_id）、`acknowledge`（instruction_id）、`motion_attestation`、`physical_review`、`finish`。这个工具只累计真实输入值，保留 stale/unknown，不会主动下达动作；若没有 GPS/轮速/人工任务量测，不能伪造真实 passenger progress。

## 7. 分支与理论假设调整（每条都可证伪）

| 观察 | 允许说的结论 | 不得说的结论 | 转向 |
|---|---|---|---|
| G1 大量 UNKNOWN 但未审核 | 数据证据缺失 | 现实中路缘不可用 | 物理接口审核、evidence acquisition |
| G1 真实多站点接口不可行 | PUDO 的 passenger/vehicle 联合可行性是实际难题 | A→B 失效事件发生率很高 | joint service-to-motion viability |
| G3 途中改向或第三 PUDO 可行 | 单纯 A→B 反例夸大 strandedness | 所有改点都可恢复 | progress-conditioned commitment timing |
| G3 多动作均失效且真实证实 | 特定场景存在服务恢复失效 | 不能由任何现有方法系统解决 | safe continuation / wait/aid contracts |
| G4 不同真实合同最优策略翻转 | passenger heterogeneity 对接驳决策有实质影响 | 所有 archetype 代表真实人群比例 | capability-conditioned coordination |
| 固定 PUDO 解决所有可核验案例 | 原动态 recourse 方法必要性不成立 | 必须设计复杂在线规划器 | data/evidence viability or efficiency |
| strong POMDP shield 决策同优 | 一般算法已经能表达本问题 | general planning 不能解决 | exactness-equivalent faster structure, not unique solvability |

## 8. 下一阶段研究缺口与对照方案

*问题级 gap*：完整服务的承诺动作对乘客实际进度/功能资源造成影响，人车与路缘行动的观测时间不同。已有 vehicle navigation、PUDO optimization、无障碍路由各自涉及子问题，但需要端到端明确合同及实际接驳。这不等于相关工作原则上不能解决。必须与真实的已发表 resource-constrained POMDP shielding 比较，而不是当前 generic_resource_shield_proxy。

*强 baseline 同输入/动作*：固定 PUDO，wait-until-verified（允许选择最优观测时间），actual-ledger replanning，中途 redirect+第三 PUDO，vehicle-first relocate，general belief-space resource shield / exact finite oracle。所有策略共享 observed event, motion checker, actionable message latency, stop/deployment cost、等待预算与援助动作。若 fixed 已达 oracle，优化 passenger burden/vehicle detour/ coverage 的实际 Pareto，而非仅 report synthetic failure rate。

*必要性*：先报告支持集服务覆盖与误承诺率、实际行走和等待分布、车辆占用/绕行及 live site confirmation，再谈 CapPlan 内核计算效率。多站点审核只支持存在性，发生率必须靠先验不筛选的服务总体及动态真实事件源；所上传 997 test scenarios 并非自然叫车 sample。实体 site 重复时 bootstrap/CI 应按 site 与 scenario 层级聚类，不按 65,813 pair 行独立估计。

*迁移*：等问题和方法冻结后做 drop-off 和 pickup→ride→drop-off，仍保留真实车辆运动及下车后入口接续。

## 9. 给下一轮研究分析回传最少文件

```bash
python scripts/run_handoff_research_gates.py pack \
  --output_dir "$STUDY" \
  --bundle_output "$STUDY/handoff_science_v4_review.zip"
```

上传此 ZIP + `$STUDY/gate3/midroute_speed_*.summary.json` + `$STUDY/gate3/midroute_pause_during_ack.summary.json`。外部真实审核完成后再上传经隐私处理的 `site_review_queue.csv`、照片/街景可复核来源摘要和 G2 motion attestation 审核结果；无需上传 nuPlan DB 或个人健康信息。若 `prepare` 阶段失败，上传错误日志与 `research_gate_decision.json`，不要假定 Gate 已通过。

## 10. 同类工作的边界和论文故事

SAE J3261(2026) 明确已覆盖 complete trip，这个概念不是首次；Waymo 官方 PUDO 帮助页明确 road closures/temporary blockage 以及步行到接驳点的现实行为，但没有公开系统性 stranded passenger 比例。其 WAV 目前有人驾驶，不能用其服务说明作为 fully autonomous wheelchair boarding 的现成实证。Weidig 等 2025 的真实 PUDO 研究支持物理接口差异。AAAI 2023 的 resource-constrained POMDP shielding、ICRA 2024 的 safe POMDP shielding 已涵盖状态约束和在线 action filtering：不能声称这些数学方法无法表达 handoff。你可争取的缺口是 **time-valid physical evidence + progress-dependent functional recourse + vehicle/curb motion coupling** 在一个可执行 service decision 中的统一、可靠、高效实现。

候选标题（暂不改稿）：`Evidence-Timed, Capability-Conditioned Handoff Commitments for Autonomous Mobility`。只有 G1–G5 证据与强基线支持才将其升级为正式 novelty claim。
