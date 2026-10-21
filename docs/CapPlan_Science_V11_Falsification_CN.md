# CapPlan Science V11：CCF-A 研究判断、H1–H4 反证、改进代码及服务器执行 SOP

日期：2026-10-21。**论证规则：先证伪、保留 UNKNOWN、不可将合成/市政代理证据写成真实接驳真值。**

## 0. 材料范围与独立结论

已读取上传的 `main_handoff_recourse.tex`（正文 + 附录）、`idea升级.md`、`数据集构建.md`、`CapPlan.zip` 代码、`handoff_science_v10.zip` 原始 JSON(L) 以及 `reports.zip`。`execution.md` **没有作为独立附件上传**；源码中有 `docs/CapPlan_Science_V10_研究判断_实验SOP_CN.md` 和 `docs/SCIENCE_V8_CCF_A_FALSIFICATION_CN.md`，这里按照现有项目文档与实际脚本构造可执行命令。`handoff_science_v8.zip` 也未上传，本轮实际使用的是 v10；不能假设 v8 的结果已经按当前代码复现。

本环境**不包含**服务器 `/data0/senzeyu2/dataset/CapPlan/data`，没有运行 nuPlan DB 或 CARLA 0.9.15 实验。本轮新增结果只在随包提供的有限合成案例上运行；详见结果文件与新增单测。

**最高优先级新发现：V10 H4 的唯一 LOSE 格子依赖人工串行控制且等待预算为零。**原始 8 格中 7 WIN / 1 LOSE，三阶离散 contrast=-1；新增加同 tick `guide+B / vehicle+B` 并发下发后 8 WIN、contrast=0；或保持串行但等待预算从 0 tick 改成 1 tick 后也 8 WIN、contrast=0。这**直接削弱**了“已有数据已经证明三因素不可分解强耦合”的主张。没有理由通过禁止现实可用的并行动作来保留该反例。

**H1 审核样本独立性新发现：**`physical_audit_queue_32.jsonl` 中 17 个站点对只对应 **2 个 episode**；其中一个 episode 贡献 16/17 个站点对；85 个候选记录是重复引用，去重后仅 12 个 episode-anchor、10 个不同 physical site keys，真实验证数仍为 0。32 对/128 时间行不能当作 32 个独立现场接驳事件。

## 1. 论文 Story：保留、收缩和升级

建议工作题目：**Evidence-Timed, Progress-Conditioned Handoff Commitments for Autonomous Mobility**（可保留 *Plan the Handoff, Not Only the Stop* 作为富有辨识度的标题）。

已有工作和标准已经覆盖 complete trip、灵活 PUDO、能力约束、资源 shielding、belief-space reach-avoid、安全观察/可恢复策略。所以不能宣称“首次完整服务、首次资源保护、首次 POMDP shielding、通用方法原则上无法解决”。已核验文献：

- Ajdarów et al., AAAI 2023, *Shielding in Resource-Constrained Goal POMDPs*: https://ojs.aaai.org/index.php/AAAI/article/view/26715
- Sheng et al., ICRA 2024, *Safe POMDP Online Planning via Shielding*: https://ora.ox.ac.uk/objects/uuid%3A18710166-3151-487e-9dea-9a4a7b1b597e
- SAE J3261_202602: https://saemobilus.sae.org/standards/j3261_202602-recommendations-ads-dvs-accommodate-needs-people-disabilities
- Weidig et al., 2025 PUDO 真实街景用户研究: https://www.maxapress.com/article/doi/10.48130/dts-0025-0012
- Waymo 官方 PUDO 工作机制: https://support.google.com/waymo/answer/9696059?hl=en

推荐的研究问题：**在行动指引让乘客改变真实位置和剩余任务能力、环境证据只对特定时段可靠、车辆停靠/姿态/可用时窗独立演化的情况下，何时能够发出一条在每个被建模可能世界中都有非预知完成或经过验证的安全接续策略的承诺？能否利用人车交接的结构在同等决策质量下减少通用求解的状态/物理检查成本？**

给定执行历史 `h` 和可能世界集合 `W(b)`，正确量词是：

`a admissible <=> exists one causal observation-contingent policy pi, for all w in W(b), Reach_G_or_verified_S(h,a,pi,w).`

不是 `for all w exists different clairvoyant pi_w`。主 TeX `eq:recourse_preserving_action` 仍误写成后者；本轮提供仅修该问题的 TeX 版本。Guarantee 仅对于声明的有限时域、可能世界、行为和已验证 lower-level motion checker 而言；若未穷尽/超过计算上限应输出 UNKNOWN。

**建议三项而非四项核心贡献：**

1. *Executable, evidence-timed handoff options*: 单个选项是行人可达区域、车辆停止姿态、设备部署区域、证据与登乘持续时间的交集，而非静态 PUDO 点。
2. *Progress-dependent robust commitment policy*: 对消息 ACK 前后实际乘客进度、累计消耗、车辆状态及多时间窗采取统一非预知策略；不能仅靠在 A 失败后从零规划 A->B。
3. *Sound, structured computation*: 这仍是**研究目标**。在与 exact/centralized/factored shield 相同的物理能力和观察权限下，保全 safety/coverage，同时降低离线/在线状态展开、实际 motion checker calls、p95/p99 延迟。未实现不能在论文写成已验证贡献。

## 2. 三种真实交叉耦合、及其与通用 shielding 的边界

*空间—姿态耦合*：乘客路径终点、路缘交互区、车辆停止位姿是不同几何对象。各自可达不蕴含存在满足车门侧、设备扫掠与人行连续性的同一接驳见证。单点 `ped_reachable=true && car_reachable=true` 是不充分的；充分表达的 generic POMDP/TAMP 仍可以发现这种失配。

*能力—消息时序耦合*：`t_ack=t_notice+network+human_delay`；ACK 生效前乘客可能继续沿旧路线运动，改变已消耗距离和方向性可达状态。随后可能出现 `d_p(x_p(t_ack), B) + distance_consumed > budget`。当完整车停窗口、三点替代、车辆改变姿态等动作被加入后，简单 A->B 不可行不意味着全局失败。

*车辆—证据—登乘时窗耦合*：需要一个完成整段登乘的交集时间 `I_p_remaining_wait ∩ I_vehicle_stopped ∩ I_deployment ∩ I_observation_valid`，每个集合非空并不代表交集非空；观测在 t0 看到空位不自动保证 t2 仍空。这个论断是必要的建模事实，不是数学上普适的算法不可能性定理。多阶段开放窗可能意味着安全时间区间不连通，不能只报告单一最后时间。

关键区别：**独立的子系统 shield 可能因交换不足的条件而错判；在联合状态中编码全部变量的通用 POMDP shield 原则上可以正确求解。** 下一阶段必须比的是后者的成本，不是人为剥夺它对物理姿态/证据/车辆动作的访问权。ICRA 2024 已经有 factored shielding，未来对比须报告保守丢失的覆盖及中央 shield 成本，不能泛称“现有 shielding 都没有结构分解”。

## 3. H1–H4 证据等级、Gate 与失败转向

| 假设 | V10 证据 | 本轮 V11 发现 | 门槛与可能转向 |
|---|---|---|---|
| H1 物理联合接驳失配 | 物理上必要；strict curb 0；review 0 | 17 对候选仅 2 episode、10 个 physical sites | 4 城独立 PUDO 审核 + 车辆 stop pose/dwell 证据。若实物失配稀少，转服务接口不完整证据获取。 |
| H2 通知/实际进度的恢复窗口 | 图级有变化（V8/V9 文档）；v10 ACK 可改变安全首动作 | 原 mining 将行人边双向化。本轮新增显式反向边敏感性 | 在真实可通行有向图、速度/ACK、第三 PUDO、停车/协助选项下重复；若早改向完全解决，转向响应时间/协同责任，不宣称普遍受困。 |
| H3 专用新算法必要性 | 通用 Exact 合成案例 20WIN/4LOSE；18 个缩放中 5 个 cap hit | 不是已发表 shield 复现，也没开发等效高效 solver | 比 strong fixed/verify-first/actual-ledger + published shield；若相同策略质量，测结构求解效率/证据成本；无收益则放弃新 kernel。 |
| H4 三因子交互 | 8 格 7 WIN / 1 LOSE，三阶 contrast=-1 | **并发控制或 1 tick 可等待均使 8/8 WIN、contrast=0**；H4 对协议假设不稳健 | 需加入有来源的验证延迟/车辆停靠时窗/物理接口与联合控制，前注册扫描；若消失，将 H4 降为时序敏感性分析而非三因子新颖性。 |

V10 原先的 24 个 scenario 中 H4 世界是完全显式、主要确定性模拟，其中 `motion_ok`、`interface_ok` 是模拟布尔字段，8 格 H4 无独立车辆轨迹和真实路缘标定；此外边被按“一跳一 tick”推进，而不是按照米数/真实速度连续推进。它可以验证离散策略语义，不能给出真实通知秒数的临界值。

## 4. 现实性：现在不应全面重建数据

`reports.zip` 并非当前 Handoff V10 的现场证据。其 `hybrid_graph.test.*.json` 仍标记 `abilitybench_hybrid_accessibility_v1_20260822`；部分 `build/val/dataset_quality.*` 是仅 10 episodes 的历史/试点版本（0 skeleton），不能代替后续文档宣称的 5996 retained / reviewfix 状态。`reports.zip` 不能证明当前四城大型 dataset 已全部通过最新 Handoff 物理审计。报告来源本身必须分版本与完整运行 ID 审查。

在旧 test graph v1 report 中，四城 cross-slope 都是 simulated；Pittsburgh/Vegas `slope` 和 `width_m` 在对应 v1 测试记录中也均为 simulated。它们对于 mechanism stress testing 可以保留，但不应声称为已实测坡度和净宽。旧 paper physical audit `pudo_audit_status.json` 也显示大量核心字段缺少独立现场/市政证据。

最划算的下一步不是买/爬更多城市数据，而是构建**小型按城市、站点分层且含未按失败条件筛选对照的独立审核切片**：每城先审 5–10 个不同物理 PUDO（资源允许再扩展），至少保证每个站点独立的可用步行/curb 连通、合法性来源和时间、车辆 stop pose、接口部署区域及必要的时序证据；结果可为 VERIFIED/REJECTED/UNKNOWN，不能把缺失连接器自动当成非法。与现有 5996 heavy benchmark 平行管理，不回写模拟真值。

可用公共资源与边界：

- Boston 官方市政 ArcGIS Ramp Inventory、Sidewalk Inventory、Curbs 等有公开接口和部分 ramp inspection/width 字段：https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/3 。需检查数据拍摄/维护时间、空间覆盖和字段单位（地图采用投影英尺）。
- Project Sidewalk 开放 curb-ramp、障碍、道路缺损标注及 API：https://projectsidewalk.org/api ，需要核实与你的 nuPlan 范围重合；众包标签不是厘米级 ground truth。
- Singapore OneMap BFA 已有轮椅使用者验证的无障碍路线，但 API 需要经批准、覆盖范围有限，不保证 nuPlan sg-one-north 重合：https://www.onemap.gov.sg/apidocs/bfa 。**没有访问许可时不能当作已取得数据**。
- 其余城市优先查既有 municipal curb/sidewalk/ADA 设施清单与带时间的街景，而非盲目合成新 connector。
- CARLA Town03 仅作 *controlled vehicle-only intervention*，与 Boston/Pittsburgh/Las Vegas/Singapore 的真实停靠区不是同一地点；必须先有严格 `Clear-A` 停稳+无碰撞对照，然后比较阻塞/改点。V9 已观察到不同 spawn 使用相同首个规划 waypoint 的离线异常，需先检查 BasicAgent 的路线语义，不能据此归因真实 curb 失败。

## 5. Profile 数据：可引入真实统计，但不应直接生成个体上限

- NHANES PFQ 2017–18 包含“步行四分之一英里有多困难”条目（PFQ061B），可以形成按人口/困难程度分组的 **条件能力敏感性层**；“some difficulty”不能对应硬的 200m 极限。https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/PFQ_J.htm
- NHATS 是美国 65 岁以上 Medicare 人群全国性调查，包含助行设备、行动功能、自评及客观能力信息，公开文件**需账户注册**，不代表全龄或四国城市乘客。**特别注意 2026 年新版使用条件禁止将 NHATS/NSOC 原始或衍生数据上传至 ChatGPT 等公共 LLM 服务，并限制研究者间的数据转交；只能在符合条款的本地环境使用、报告允许的汇总统计。** https://www.nhats.org/data-access / https://www.nhats.org/conditions-of-use
- FHWA 2022 NHTS 有公开出行行为数据，可用于任务/出行频率及某些移动困难属性分析，但不是 curb 部署和行动能力的实测合同：https://nhts.ornl.gov/downloads
- Weidig et al. 2025 有 25 位辅助设备使用者的真实街道 PUDO 体验（14 手动轮椅、5 动力轮椅/踏板车、6 助行器具），可启发设备/任务接口变量；样本数和取样方法不支持当作人群概率权重：https://www.maxapress.com/article/doi/10.48130/dts-0025-0012
- WHO WHODAS 2.0/Model Disability Survey 提供标准化功能与环境障碍评估框架；不能从问卷域分数推出单次安全等待秒数：https://www.who.int/classifications/international-classification-of-functioning-disability-and-health/who-disability-assessment-schedule

保持两种 suite：`canonical/counterfactual`（当前 8 组、无人口权重）及 `empirical/transfer`（可公开获得的问卷约束类别 + 真实设备/任务参数，明确不确定性、抽样框和不可推导的字段）；如无任务级真实数据，报告宽/窄/速度/等待和 ACK 的区间敏感性，不虚构联合分布。

## 6. 本轮代码优化与本地验证

本轮在本次上传的 `CapPlan.zip` 上增加**非破坏式 v11 science 验证**：

- `capplan/handoff/commitment_model_v10.py`：加 `allow_parallel_control_actions`（缺省 false，完全保留 v10 历史结果）；允许一个时钟的乘客 guidance + 车辆 target/本地 verify 并行，隐藏世界只能在 verify 真正结束后分支；未添加免费观察或未来真值。
- `scripts/run_handoff_h4_robustness_v11.py`：同一 8 格 H4 在串行/并行、0/1 tick 等待的 4 种协议下运行 exact 求解、输出交互 contrast 及同 world 回放检查。
- `scripts/audit_handoff_all_alternatives_v8.py`：新增 **opt-in** `--respect_directed_pedestrian_edges`，未找到明确反向边时不得凭空让途中乘客沿有向边掉头；缺省保持原乐观无向口径。由于 GIS 文件可能仅用一条 edge 表达天然双向通行，该模式也是**编码敏感性**，非法律/现实方向真值。
- `capplan/handoff/window_intersection_v11.py` + CLI：按到达范围、已剩余等待时间、车辆合法驻留、证据有效期、整段登乘持续时间寻找多个不连续交集；unknown 不能解释成失败，交集也不证明真实停靠/完成。
- `scripts/prepare_handoff_time_packets_v11.py`：将 v10 站点对按 episode-anchor 和 physical site 去重，输出全部未知的审查模板，不填造市政/车辆/接口/时间正例。
- `tests/test_science_v11.py`：反例、方向性中途掉头、联合命令不偷看世界、多窗和未知证据回归。
- `main_handoff_recourse_quantifier_fixed.tex`：修正共享因果策略的量词顺序。

本地：新增和相关 targeted suite **25/25 PASS**；Python `compileall` PASS。尝试了全仓库 `pytest -q`，在约 37% 处达到 180 秒超时，**不能声称全仓库回归通过**。没有运行真实 DB/CARLA。

## 7. 服务器执行 SOP（不删除/不重建旧 GIS，先运行便宜的证伪实验）

### 7.1 版本备份与补丁（务必 dry-run）

```bash
conda activate capplan
export CAP_HOME=/home/senzeyu2/code/CapPlan
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export TEST_DATASET="$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test"
export STUDY="$CAP_HOME/outputs/handoff_science_v11"
mkdir -p "$STUDY"
cd "$CAP_HOME"

# 不删除原工程，不 touch 已准备的大型图
cp -a "$CAP_HOME" "${CAP_HOME}.backup_before_science_v11.$(date +%Y%m%d_%H%M%S)"
patch -p1 --dry-run < /path/to/CapPlan_science_v11.patch
# 上行无 failed hunk 时才执行；失败不要 --force
patch -p1 < /path/to/CapPlan_science_v11.patch
python -m compileall -q capplan scripts
python -m pytest -q tests/test_science_v11.py tests/test_science_v10.py tests/test_science_v8.py
```

如果服务器目录与上传的 `CapPlan.zip` 不同，停止 patch，在独立新目录解压完整 `CapPlan_science_v11_optimized.zip`，先用独立工作树运行 research scripts，不要用完整 ZIP 覆盖当前含未提交修改的主工程。

### 7.2 第一关：无需 DB、立即可跑的 H4 反证

```bash
python scripts/run_handoff_h4_robustness_v11.py \
 --cases examples/handoff_finite_v10.jsonl \
 --output "$STUDY/h4_protocol_robustness.jsonl"
cat "$STUDY/h4_protocol_robustness.summary.json"
```

预期 4 个协议中原始 `[wait0,parallel0]` 为 7WIN/contrast=-1，其他三种都为 8WIN/contrast=0；**这不是成功地佐证旧 H4，而是验证旧 H4 不稳健**。如结果不同，先检查实际 checkout/hash，而不是调环境生成更多符合预期的场景。

### 7.3 第二关：现有 V10 物理审核池的真正分母和 UNKNOWN

```bash
export V10="$CAP_HOME/outputs/handoff_science_v10"
# 如果这个目录没有 queue，先将本次上传的 handoff_science_v10.zip 解压到 $CAP_HOME/outputs。
test -s "$V10/physical_audit_queue_32.jsonl" || { echo 'V10_QUEUE_MISSING'; exit 2; }

python scripts/prepare_handoff_time_packets_v11.py \
 --queue "$V10/physical_audit_queue_32.jsonl" \
 --output "$STUDY/physical_pending.jsonl"
python scripts/audit_handoff_time_intersection_v11.py \
 --packets "$STUDY/physical_pending.jsonl" \
 --output "$STUDY/physical_pending_timing.jsonl"
cat "$STUDY/physical_pending.summary.json"
cat "$STUDY/physical_pending_timing.summary.json"
```

预期得到 17 pair、2 episode、12 unique episode-anchor 和 10 physical sites、12/12 UNKNOWN。此阶段不应把所有 unknown 视为物理失败或成功；从此模板选取 4 城独立物理 sites 和随机对照，经人工/公开市政时间化审查填写缺失字段后，再使用同一时间窗检查器。

### 7.4 第三关：中途改向的无向 vs 有向图敏感性

```bash
export PAIRS="$CAP_HOME/outputs/handoff_funnel_test_all/mining/recourse_pairs.jsonl"
# 先确认是未截断、属于本次研究的完整 65,813 candidate 文件，而非历史 300 样例
test -s "$PAIRS" || { echo 'PAIRS_MISSING'; exit 2; }
test -d "$TEST_DATASET" || { echo 'TEST_DATASET_MISSING'; exit 2; }

python scripts/audit_handoff_all_alternatives_v8.py \
 --dataset_dir "$TEST_DATASET" --pairs "$PAIRS" \
 --output "$STUDY/alternatives_directed_strict.jsonl" \
 --limit 32 --notice_times_s 0,15,30,60,90 \
 --speed_mps 0.8 --ack_s 10 \
 --respect_directed_pedestrian_edges

# 仅作上界对照：省略 typed curb 不等于真实存在可通行接口
python scripts/audit_handoff_all_alternatives_v8.py \
 --dataset_dir "$TEST_DATASET" --pairs "$PAIRS" \
 --output "$STUDY/alternatives_directed_optimistic.jsonl" \
 --limit 32 --notice_times_s 0,15,30,60,90 \
 --speed_mps 0.8 --ack_s 10 \
 --respect_directed_pedestrian_edges --allow_missing_typed_curb
```

有向结果减少未必意味着乘客真实不能掉头；先审查原始 graph 是“双向各存一条”还是“一条边表达双向”再解释。还要与无向/暂停 ACK 对照；真实结论需要车辆运动、完整候选与援助动作。

### 7.5 第四关：CARLA 0.9.15 独立车辆控制，仅在清晰对照通过后继续

```bash
export CARLA_ROOT=/data0/senzeyu2/dataset/CRADrive/CARLA_0.9.15
export PYTHONPATH="$CARLA_ROOT/PythonAPI/carla:$CAP_HOME:${PYTHONPATH:-}"
python -c 'import carla; from agents.navigation.basic_agent import BasicAgent; print("CARLA_CLIENT_OK")'

# 单独终端启动，确认无其他客户端同时 tick
export CARLA_LOG_DIR="$STUDY/carla_server"
bash scripts/carla_headless_v5.sh

# 在另一个终端运行预注册 Clear-A 控制
python scripts/calibrate_carla_control_v6.py \
 --output_dir "$STUDY/carla_clear_A" --map Town03 \
 --spawn_indices 0,1,2,3 --a_steps_options 2,3,4 \
 --target_speed_kmh 5 --max_seconds 50 --arrival_radius_m 6 \
 --service_stop_hold_s 2 --require_stationary_stop --freeze_traffic_lights
```

再审阅每次 `execution_report.json` 的 `control_diagnostics_v9`（起始车辆 pose、`planned_first_5_xy`、航点偏移、route length）和轨迹。一旦路线与实际 spawn 不一致、Clear-A 无严格驻留、或者无法保持同一几何/交通条件，**停止 blocked/redirect 因果实验**；不要扩大半径或放松驻留阈值来“修好”实验。CARLA Town 无法证明真实四城的 curb legality/轮椅登乘；要形成 H1 系统证据，需把选出的真实 physical site 和 nuPlan method-specific stop pose/motion validator 独立绑定。

### 7.6 哪些数据应回传、以及研究停止规则

压缩以下相对较小的报告文件：`h4_protocol_robustness.summary.json`、`physical_pending.summary.json`、`physical_pending_timing.summary.json`、两份 `alternatives_*.summary.json`、CARLA Clear-A `calibration_summary.json`、选中的 `execution_report.json` + `trajectory.jsonl`、所有物理审核记录及来源证明。**无需再传几百 GB 原始 nuPlan/GIS graph**。

停止或转向规则：

- H4 即便加入合理并行控制/非零等待仍无交互：降为有条件时间敏感性；寻找真实车停/证据约束而不是修改动作空间阻止并发。
- 真实 PUDO 接口长期为 UNKNOWN：优先做 evidence acquisition/verification 的研究，不称物理失败已经被证明。
- 途中改向及第三 PUDO 始终可行：不再讲“不可恢复普遍发生”，研究信息更新、额外乘客成本与车辆承担调整的决策。
- Strong fixed/optimal verify/common generic shield 在公平成本下都持平：放弃“别人解决不了”，只可能主张结构化求解效率；若效率同样无收益，应放弃复杂 online kernel。

## 8. CCF-A 可投稿前还缺什么（不可用合成 QA 替代）

1. 至少一小组不同城市/独立站点、具时间/合法性来源和车辆运动证据的**联合可行/不可行**实例，包含未按失败筛选的对照。
2. 正确的实际中途状态、通知 ACK 时钟、等待/资源代价、方向性 pedestrian connectivity、第三 PUDO 和现实安全接续条件；对每个候选至少有完整交接需要的 dwell 检查。
3. 相同观测权限、完整动作集合、相同约束下的真正 published-strength resource shielding / factored POMDP / exact oracle 比较；分别报告可靠性、完成覆盖、服务负担和实际资源/计算成本，而不是 only first action safe。
4. 把 conditional guarantee 与 realized success、UNKNOWN、拒绝和人工接续清楚分开；对真实核验站点聚类做 CI，不把重复场景帧当独立样本。
5. 方法专属的 nuPlan/CARLA 动态车辆控制，不能使用 `mock_strict` 或仅进入 CARLA 6m 区域代替真实停车与 boarding。

**研究优先级：先通过 H4 自证伪补正科学论证 → 真实 H1 审核与 H2 执行时钟 → 更有来源的 H4 因子扫描 → H3 强基线/算法设计。暂不重新生成全量 GIS/DEM，也不开始新的大型神经网络架构。**
