# CapPlan Handoff Research Gates V3 — 可证伪研究流水线

本文件针对 **当前上传的 CapPlan 源码**。目标是识别真实、不可忽略、现有强方法难以经济地解决的 handoff 问题，而**不是**人为制造更高失败率。脚本输出中的 PASS 只能表明指定的技术检查完成，绝不等价于论文科学创新成立。

## 0. 实际交付范围与严格边界

本次一次性实现：

1. **G0** 分母、时间、世界支持、能力绑定、证据和样例截断检查：`capplan/handoff/gate_evidence.py`。
2. **G1** 物理站点级 + 城市分层的人工核验任务生成：`scripts/select_gate1_site_review.py`，保留 reviewer 原填写文件，默认不覆盖。
3. **G2** 车辆运动验证请求及外部 checker 结果的日志哈希/场景/时间窗核查：`scripts/audit_gate2_motion.py`。没有外部结果时输出 **0 份证明**。
4. **G3（部分）** 已发出指引但乘客**还在 O→A 途中**时，在同一行人图、同一硬约束下是否可以转往 B 的几何检验：`scripts/audit_handoff_midroute.py`。
5. **G4（部分）** 将八种 canonical 契约的最大 access 距离绑定到有限 oracle；等待预算另作控制变量。忽略的坡度、设备、接口字段会明确标出：`scripts/build_gate4_profile_time_grid.py`。
6. **G5/G6** 明确设置自动 **BLOCKED**，直到外部强基线复现和冻结 drop-off transfer 真正完成；不给一个 proxy 起已发表 shield 的名字。
7. 整体编排：`scripts/run_handoff_research_gates.py`，可检查结果分支和打包最小回传文件。

**没有实现、也没有声称实现：** 独立的车辆轨迹求解器；真实 nuPlan method-specific closed loop；经过独立现场审查的 curb/PUDO 真值；CARLA 坐标映射；三候选/乘客援助/车辆挪位的完备 oracle；完整 belief-space published-strength shield；drop-off transfer 的冻结模型确认。这些均需后续实际完成，不能从几何脚本和有限 oracle 的结果中推断。

## 1. 设置与所需数据

```bash
conda activate capplan
cd /home/senzeyu2/code/CapPlan
python -m pytest -q tests/test_handoff_research_gates.py \
  tests/test_handoff_evidence_upgrade.py tests/test_handoff_funnel_oracle.py

export CAP_HOME="$PWD"
export TEST_DATASET=/data0/senzeyu2/dataset/CapPlan/data/outputs/datasets/abilitybench_av_hybrid_test
export STUDY="$CAP_HOME/outputs/handoff_research_gates_v3"
```

直接使用已有 `handoff_funnel_test_all` 的真实挖掘输出，**不需要重新构建 5,996 个 heavy scenes**。请注意：上传的 `handoff_funnel_test_all_results.zip` 声称全量筛出 65,813 条，但 `mining/recourse_pairs.jsonl` 仅含 **300 条打包样例**。这两种数据量绝不可混用。使用本地原始完整 JSONL 可扩大样本；若仅用上传 ZIP 提取的文件，选择结果只对**300 条样例**有效。

```bash
export PAIRS="$CAP_HOME/outputs/handoff_funnel_test_all/mining/recourse_pairs.jsonl"
export OVERLAYS="$CAP_HOME/outputs/handoff_funnel_v2/falsification/overlays.jsonl"
export PROFILES="$CAP_HOME/outputs/handoff_funnel_v2/canonical_coupled.jsonl"
# 根据你机器上的真实路径修改以上路径
ls -lh "$PAIRS" "$OVERLAYS" "$PROFILES"
```

推荐将 `mining_audit.json` 保留在 `PAIRS` 同目录；G0 会比对声明的候选总数和 JSONL 行数。若未提供 audit，自动输出 `UNVERIFIED_COMPLETENESS`，不允许声称整套数据已审查。

## 2. 推荐第一阶段（先只需一次 prepare）

```bash
python scripts/run_handoff_research_gates.py prepare \
  --pairs "$PAIRS" \
  --overlays "$OVERLAYS" \
  --profiles "$PROFILES" \
  --dataset_dir "$TEST_DATASET" \
  --output_dir "$STUDY" \
  --per_city 8 \
  --midroute_limit 40 \
  --notice_times_s 0,30,60,90,120,180 \
  --max_grid_sites 8 --max_grid_profiles 8 \
  --wait_budgets 30,120,none \
  --evaluate_grid
```

如果暂时只有打包 JSONL，没有本机 heavy GIS 图文件，**先去掉 `--dataset_dir`**。其余步骤仍可执行；G3 会显示 `PENDING_GRAPH_FALSIFICATION`，不能伪造通过。

此步骤生成：

| 文件 | 用途 |
|---|---|
| `gate0_preflight.json` | 各城市实际包内候选、物理站点去重、是否截断、事件时间/等待/能力未绑定计数 |
| `gate1/site_review_queue.csv` | 人工审查模板；`PENDING/UNKNOWN` 不是负例 |
| `gate1/selected_review_pairs.jsonl` | 从候选中选择审查样本；不会当作随机总体样本 |
| `gate2/motion_requests.jsonl` | **任务单**，不是 motion 已验证结果 |
| `gate2/gate2_motion_audit.json` | 现有输入的验证及不匹配原因，默认 0 个合格 external attestation |
| `gate3/midroute.jsonl` | 每个通知时刻中途位置→B 的最短图距离（需要本机图） |
| `gate3/midroute.jsonl.summary.json` | 早/晚通知是否改变图上可恢复性，不能代表车辆完成 |
| `gate4/profile_time_grid.jsonl` | 相同事件世界支持下跨 profile 和 wait 的受控评测 |
| `gate4/policies/policy_summary.json` | 有限 O/A/B 规划器的对照，**不是外部强 shield** |
| `research_gate_decision.json` | 七道 Gate 目前的分支状态（只能阻断/指导，不能认证新颖性） |

**优先读三个字段：** `gate0_preflight.json -> pairs.pair_file_coverage`、`research_gate_decision.json -> decision`、`gate3/midroute.jsonl.summary.json -> counts`。 先判断实验是否在正确的样本和真实物理条件下运行，再研究策略差异。

## 3. Gate 0：先做可证伪技术检查

- JSONL 行数与 `mining_audit` 对齐；若只有截断样例，不给城市分布/独立站点频率的总体估计。
- observation request 与事件时刻独立；如果 schedule 已经含有未来事件的真实时刻，须标记 `PRIVILEGED`，不得与可部署策略公平比较。
- `world_support` 必须有规范概率、覆盖起始时刻的时间轨迹；motion packet 如声称验证具体 event time，须经过同一时刻覆盖复核。
- A/B 两端点均在距离预算内只能说明**必要几何条件**。必须额外纳入途中位置、可选第三点、可休息/可援助和车辆移动，否则无从声称 global no-recourse。
- 当前 120 条 falsification sidecars 的 24 个站点对中 21 个来自 Vegas，24×5 不能当成 120 个独立现实站点。
- 单独生成 `PROFILES` 并不代表能力合同影响了 oracle；G4 部分绑定只测试距离和人为等待时窗，**不代表真实乘客能力**。

## 4. Gate 1：需要真实审查什么

如果提供真实 heavy dataset，还会生成 `gate1/anchor_control_queue.csv`：同 episode、非 A/B 的 dataset-legal 和 dataset-illegal/unknown 对照锚点。其标签可能是构造或推导，不是独立真实标签；人工审核必须覆盖这些对照。

人工审查站点 A、B 的有效 pedestrian route、路缘连接和 ramp、停车合法性及时段、车辆和设备装载姿态、正反例；记录 `external_source_uri` 与 reviewer。完成模板后另存 CSV（建议 `site_review_completed.csv`），**不要修改原始生成的候选 JSONL**。建议每城先尝试至少 5 个不同 physical site pairs；如果城市候选不足，应记录真实不足原因，不能补造。

注意 `site_review_queue.csv` 的 `review_decision=PASS` 是审查者声明，程序只检查必填字段一致性，不负责验证外部来源是否真的有效；真正的现场核验需研究人员复查。`curb_connector_checked` 等多个字段应明确使用 `PASS/FAIL/UNKNOWN`；不能把 unknown 变成 fail 或 pass。必须同时保留负对照。

```bash
python scripts/run_handoff_research_gates.py report \
  --output_dir "$STUDY" \
  --review_csv "$STUDY/gate1/site_review_completed.csv"
```

若发现大量缺 typed curb connector，先判定是否是图表示缺失：缺边≠现场无路缘。用已有 `diagnose_handoff_curb_connectors.py`，配合人工审查的观测材料；只有真正有证据时才使用已有 `audited_curb_overlay.py` 的接口，不改变底层 geometry。

## 5. Gate 2：如何接入真实车辆验证

`gate2/motion_requests.jsonl` 是需求清单。每条 request 应执行与你论文计划比较的方法对应的真实轨迹计算/车辆运动复现。要保留原始场景 `scenario_token`、A/B 停靠 pose、时间参考 `scene_relative_seconds`、轨迹标识、校验器版本、原始执行日志路径与 SHA-256。

外部结果每行最少包含：

```json
{
  "episode_id":"REAL_ID", "from_anchor":"A", "to_anchor":"B",
  "scenario_token":"REAL_TOKEN", "map_name":"us-ma-boston",
  "reachable":true, "collision_free":true, "legal_stop":true,
  "interface_verified":true, "method_specific_motion_checked":true,
  "source":"INDEPENDENT_EXECUTED_MOTION_CHECKER",
  "motion_duration_s":10, "valid_start_s":80, "valid_end_s":120,
  "time_reference":"scene_relative_seconds",
  "planner_hash":"REPRODUCIBLE_HASH", "trajectory_id":"REAL_TRAJECTORY_ID",
  "checker_version":"VERSION", "simulator_log":"/absolute/path/to/real.log",
  "simulator_log_sha256":"SHA256_OF_ACTUAL_LOG"
}
```

以上是字段示例，**不是已有事实，不可直接提交这个假结果**。对于失败 case 必须把 `reachable` 等写真实值。校验器拒绝不在原始 request 集合内、重复场景、缺日志、日志哈希错、场景 token/城市不匹配、事件时刻不在覆盖窗口内的结果；通过也仅标为 `LOG_BOUND_CHECKER_ATTESTATION`，而非已独立证明 safe handoff。还需要真实的轨迹安全、道路停车合法性和乘客接口审查。

```bash
python scripts/audit_gate2_motion.py \
  --requests "$STUDY/gate2/motion_requests.jsonl" \
  --claims /path/to/actual_motion_checker_results.jsonl \
  --log_root /path/to/checker_logs \
  --output_dir "$STUDY/gate2"

python scripts/run_handoff_research_gates.py report \
  --output_dir "$STUDY" --review_csv "$STUDY/gate1/site_review_completed.csv"
```

motion checked at **mining event 90s** 不表示覆盖了 temporal sidecar 的另一个事件时间。重新生成 sidecar 时，原 `build_handoff_temporal_overlays.py` 已进行该时窗复核。后续应使用实际 sidecar 的 time-indexed motion evidence，并进行持续安全检查，不能自动升级标注。

## 6. Gate 3：主动推翻“乘客必须先到 A，再从 A 转 B”

图检验以 `notice_time` 与乘客在 O→A 路径上的走过距离为输入，精确保留已走距离。若位于某条 pedestrian edge 中间，允许沿该边到两端点，再从端点在同一筛选硬约束下到 B；不传送到 A/B，也不重置距离预算。

```bash
python scripts/audit_handoff_midroute.py \
  --dataset_dir "$TEST_DATASET" \
  --pairs "$STUDY/gate1/selected_review_pairs.jsonl" \
  --output "$STUDY/gate3/midroute.jsonl" \
  --notice_times_s 0,30,60,90,120,180 --speed_mps 1.0 --ack_s 3.0
```

如果中途向 B 改向在相当一部分时刻可行，必须**收缩原始“不可恢复”的主张**，把问题改为通知延迟与状态依赖的恢复窗口。若始终不可行，也只能断言“在给定 O→A 路径、当前 B 和限制的图动作中不可行”；第三 PUDO、车辆主动挪位、可休息/辅助、路边接续仍未排除。G3 不会报告“全局 stranded”。

## 7. Gate 4：profile 和验证时窗实验

```bash
python scripts/build_gate4_profile_time_grid.py \
  --overlays "$OVERLAYS" --profiles "$PROFILES" \
  --output "$STUDY/gate4/profile_time_grid.jsonl" \
  --max_sites 8 --max_profiles 8 --wait_budgets 30,120,none

python scripts/evaluate_handoff_policies.py \
  --overlays "$STUDY/gate4/profile_time_grid.jsonl" \
  --output_dir "$STUDY/gate4/policies"
```

对同一站点/同一事件分组比较 `exact_finite_support`、`verify_then_guide`、`robust_fixed_anchor`、`actual_ledger_myopic`，并联合输出 unsafe commitment 和乘客实际负担。注意 `generic_resource_shield_proxy` 实际只是固定 B：**绝不可称为复现现有文献算法**。

`max_access_distance_m` 可作为本次有限 oracle 的累计 access 预算；`max_wait_exposure_s` 与 oracle 的累计总等待不一定是同一种测量，脚本**不会静默映射**。等待上限采用独立受控值，结果属于机制敏感性，非人群概率。

## 8. G5/G6 的分支及科研决策标准

- 若 G1 无独立接口证据：**先纠正场景表示**，暂不对策略做真实服务结论。
- 若 G1 通过但 G2 无轨迹：**做真实运动与停车姿态检查**，否则不能声称车辆会执行 A→B。
- 若 G3 的途中改向经常恢复：调整科学问题为 **Evidence-timed and progress-conditioned handoff**，不能再只用 A→B 超预算当系统性失败。
- 若真实接口和运动均支持，但 `Verify-then-Guide` 与 `Fixed/Vehicle-first` 在公平成本下匹配 oracle：优先研究 observation cost、时效、车辆移动、信息价值。若仍无可见差距，就要放弃“新在线策略必要”主张，转向具有正确性证明的高效领域专用 kernel。
- **G5** 必须实现真实对等的 resource-constrained belief shield（相同信息、时间、动作、服务/安全定义），然后比较安全覆盖率、服务负担、motion checker 次数与计算时延。当前只提供阻断状态，不给不实 baseline 代码。
- **G6** 必须冻结 pickup 方法，统一评测脚本、条件和真实 motion 检验，才允许 drop-off 与车内系统级迁移结论。

主要研究性假设建议预注册：

- `H1`: 真实 PUDO、实际通行与车辆停靠约束中存在可独立验证的 handoff mismatch。
- `H2`: 在真实事件时序/消息延迟与真实乘客行动下，某些非预知策略必须进行代价显著的等待、重定向或车辆挪位；仅检查当前位置/重置剩余距离不足。
- `H3`: 与强 `Verify-then-Guide`/fixed/vehicle-first/resource-belief shielding 在**同等信息与动作权限**下相比，领域结构化决策具有可复核的服务收益或相同安全性下明显的计算优势。
- `H4`: 物理站点与乘客能力的反事实变化具有可解释、可预测的策略翻转，而不是人为独立 uniform threshold 产生的统计假象。

H1–H4 任何一个不成立都应诚实记录，而非改动规则去让它成立。现实自然失效率须额外以未按结果筛选的运行总体与观察事件频率估算；不能从 controlled intervention 或候选挖掘频率推导。

## 9. 每个阶段完成后上传最少哪些文件

```bash
python scripts/run_handoff_research_gates.py pack \
  --output_dir "$STUDY" \
  --bundle_output "$STUDY/handoff_gates_review_bundle.zip"
```

**第一轮只需上传** `handoff_gates_review_bundle.zip` 与 `gate3/midroute.jsonl`（如果已执行，文件不大）及真实 G1/G2 证据的摘要或链接。压缩包包括哈希清单、审计摘要、少量入选案例和小规模策略汇总，不包含巨大 nuPlan 数据或模拟器原始资产。

研究者第二轮审阅后再决定是否进入更加昂贵的 CARLA、强 shield 和冻结 drop-off 阶段。任何一个 Gate 缺少事实证据就应保持 HOLD，**不是工程失败，而是在保护论文的科学有效性**。
