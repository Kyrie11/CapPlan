# CapPlan 研究问题与 7 步 Motivation Funnel：代码审计与下一轮执行方案

日期：2026-10-13；材料：用户 TeX、`论文idea升级.md`（重点第四次回答及主故事）、`数据集构建.md`、`CapPlan.zip`、strict 与 relaxed 两个结果包。**本报告把数据集事实、受控反事实、程序语义、外部物理验证明确区分。**

## 0. 学术判断与结论边界

最值得检验的问题不是“能否重新选一个 PUDO”，而是：**指导乘客行动是一种消耗不可恢复功能能力、产生时效承诺的双主体物理动作；在信息不全与车辆/行人不同步时，系统何时可安全发出该动作？** 研究场景合理，但“资源历史不能 reset”“belief 下要 shield 不可恢复动作”并非现有工作完全未考虑。AAAI 2023 的 Resource-Constrained Goal POMDP shielding、ICRA 2024 的 Safe POMDP Shielding 已解决重要一般情形。因此，必须检验 PUDO 物理交接、观测失效、指引确认、多个 typed 能力约束和连续车辆验证的**非平凡耦合**，以及一个通用 shield 在相同输入、相同动作与计算预算下为何不足。若通用 shield 直接复用且无劣势，应调整论文算法主张，而不是创造一个弱对手。

论文建议保留：pickup 是主任务；drop-off 是方法冻结之后的迁移任务；ride 只作为独立车辆安全闭环接口；完整行程三阶段组合作系统结果。**当前不能宣称已实现端到端实测 boarding/nuPlan/CARLA 安全证明，也不能用生成事件频率推断现实阻塞概率。**

## 1. 对既有上传结果的严格复核

| 数据 | 原报告中完整运行的统计 | 正确解释 |
|---|---:|---|
| strict，`episode_limit=100` | 100 episodes，5717 anchors examined，4114 missing full curb connector，1329 missing explicit curb connector，0 qualified pairs | 严格 *100 场景样本*没有得到可用的完整 A/B 证据，不能声称全测试集没有真实反例；需诊断 representation 与物理缺口 |
| relaxed，完整 test | 997 episodes，275 episodes with geometry candidates，65,813 ordered asymmetry pairs | 条件化屏选值，不是事故数/独立物理站点数 |
| relaxed 动作接口 | 65,813 对均涉及模拟/未独立审计接口属性 | 不能自动升格为真实 curb/deployment ground truth |
| motion | 0 verified，0 time-window verified | 尚未建立“车可到 B，但乘客无法恢复”的完整证据链 |
| exact finite oracle | 65,813 个合成 two-anchor overlays 中 condition support = 1 | 两候选、固定支持、单次观测的 oracle；B 恒可用是构造保证 |
| robust fixed B、generic shield proxy | 同为 1.0 | 强烈的**方法必要性反证**，不能据此宣传 kernel 优势 |
| 资源历史 myopic | 约 0.5 | reset-like 行为失败可以成立，但不足以构成独家 research gap |

**非常重要：** 上传的 relaxed ZIP 中 `recourse_pairs.jsonl` 被打包器截断，仅包含前 **300 行示例**（bundle manifest 明示 `truncated: true`）；`mining_audit.json`、`policy_summary.json` 等保留原完整运行分母。现有诊断包里“284 个站点对 / 16 个多样化审查条目”只针对这 **300 条打包样例**，绝不能推断 65,813 条的站点对总量、空间代表性或频率。服务器全量原始 `mining/recourse_pairs.jsonl` 才能进行正式抽样和 cluster bootstrap。

## 2. 逐步 Motivation Funnel：需要证明什么、如何做、何时停止

### Step 1. 首先定位几何上的方向不对称（只证明必要条件）

在真实 nuPlan episode 绑定的 GIS 行人图上定义功能合同 $p$、起点 $O$、两候选 $A,B$ 与实际已消耗量 $c$。首先测试：

- `$c+d_p(O,A) <= B_p$` 且 `$c+d_p(O,B) <= B_p$`；两者在同一合同下均有确切可执行路径，而非直线距离。
- `$c+d_p(O,A)+d_p(A,B)>B_p$`，表示到达 A 之后恢复到 B 超出硬距离预算。
- 可施加已行走前历史和已等待消耗；需要保留 width/slope/cross-slope/step-free hard filters，未知值为 UNKNOWN，不可用拟合均值伪装 observed。
- 候选需不同 physical site，报告去重 episode/physical sites、宽度/坡度来源及 margin；不能把每个候选组合当独立观测。

**可运行代码**：`scripts/mine_handoff_recourse_pairs.py`，在 `--require_interface_complete --require_explicit_curb_connector` 下完成严格 screening。输出 `mining_audit.json` 的逐级分母。开放式 relaxed 仅用于寻找检查对象，不用于主结果。

### Step 2. 全部 curb connector + vehicle interface 的审核（目前最高优先级）

这一步不是把 existing ped node 最近邻吸附到 curb 就自动通过。需要沿实际 passenger path 检查 pedestrian node 与 curb/contact 位姿之间的**可 traversable、方向及坡度正确、净宽明确且支持接口部署**的连接，同时核对停车合法性和车门/坡道/升降平台可用侧。应区分：

1. 缺失 typed curb edge **可能是图表达缺口**；
2. typed curb edge 存在且属性 PASS，只证明 graph-level predicate，不等同实物测量；
3. 外部独立审核通过、时间有效且几何/接口/来源可复核，才可能支持强物理 claim。

`diagnose_handoff_curb_connectors.py` 按城市统计“ped node 缺失”“sidewalk snapped but no typed curb edge”“有 typed edge 但资源不足/未知”“graph-level PASS”，提供原始 node/source 和例子。不要通过随机构造过街边、修改真实坡道字段或一律把 `curb` 设为 True 来挽救实验。

如果具备独立 GIS/人工审查的 curb connector，才可以使用 `--reviewed_curb_connectors_jsonl` 加入**内存中的增量审查 overlay**；其记录必须包含 episode/anchor/site、两个物理端点、length/width/slope/cross-slope、step-free/ramp、独立来源和审查人。程序 fail-closed，**不修改原 AbilityBench、也不自动确认此记录是现场真值**。

### Step 3. 车辆运动、时窗和 interface 的 method-specific 验证

对入选、跨城市且不同 physical sites 的 candidate 生成车辆 motion requests；输入包含 nuPlan 场景 token/map、PUDO stop/curb pose、事件时刻和 audit references。必须由车辆规划器/仿真器实际执行轨迹并回填 collision-free、合法停车、stop-pose/interface、动态交通约束、车辆 dwell/boarding time window；不能用 graph reachability 或常速估计代替。

**已修复关键时钟 bug：** 原挖掘时通常检查第 90 秒的 motion packet，但 sidecar 可能根据乘客速度把实际干预时刻改为其 A 到达前 15 秒。现在 `packet_covers_event` 会对**sidecar 的真实 event_s + motion_duration_s** 重新验证 `[valid_start_s,valid_end_s]`，不匹配就标记 false。外部 JSON 的布尔字段仍是**checker 的声明**；原始轨迹/日志需独立核查，不能只改一个 JSON 就当物理证据。

### Step 4. 小规模独立 PUDO 审核，构造论文 motivation 图

按 city × physical site × source × mobility contract × event-type 层化挑选 20–40 个候选进行第一轮 review；保证正/负/UNKNOWN 三类、同时覆盖破坏假设的边缘案例。每个高质量例子至少有：地图/路缘图片或实测记录与来源、A/B 真实位置、审核人及可复核证据、vehicle executed trace、乘客两条路径的 typed 资源分解、世界切换与时间轴。至少 5 个**不同 physical sites**完成独立审查后，再考虑论文主 Motivation 图。这个门槛只是建议的最小可审计探索集合，不能当统计显著性的替代。

若 step 2/3 无法产生可信例子，应停止“已证明现实强 failure”的论述；分析到底是 GIS 证据不足、候选不足，还是问题在现实中实际被很好的保守固定 PUDO 策略规避。

### Step 5. 小型可核验非预知 AND–OR/DP oracle

正确的量词是存在一个**单一的非预知策略**在所有兼容世界上完成 `verified boarded` 或 `independently verified safe continuation`，而不是 `∀w ∃ separate clairvoyant plan`：

`∃ π causal : ∀w ∈ support(E) , TerminalComplete(π,w) ∨ TerminalSafeContinue(π,w)`。

状态应包含 `(passenger actual location, vehicle actual state, typed consumed ledger, t, observation validity, acknowledgment/commitment history, belief support)`。动作包含观察、等待、乘客指引、车辆调度/重定位、人工援助或声明 safe continuation（如果场景确有并可核验）；每条干预有真实或控制时钟、因果信息与来源。不能让策略通过测试世界的未来事件时刻来挑选检查时间。

**当前有限 oracle 仅覆盖** O/A/B、一次 delayed observation、两次候选尝试，没有车辆轨迹、真正 safe-continuation terminal 或任意行人恢复，因此只能用作 micro-model correctness/falsification control。审稿时必须单独标注它的 action-set completeness 边界。

### Step 6. 与六类 baseline **同信息**比较，主动寻找否定论文的证据

至少比较：reset-and-replan（作为失效示例）；actual-ledger myopic（区分历史必要性）；verify-then-guide（检验单次及时检查能否足够）；robust fixed anchor（检验保守策略是否完全规避问题）；精确 finite support oracle（上界）；**真实复现的 generic resource-constrained belief-space shield**（不是当前固定 B 代理）。对比的观测、动作、时间预算、车辆 interface checker 必须统一。

已经增加 5 组反证型控制：

- `one_sided`：B 永久可达。**固定 B 100% 本应成立**；如果论文凭此宣称方法必须有复杂 kernel，就失败。
- `two_sided_observable`：两种世界分别只剩 A 或 B，固定 B 不能保证；观察 A 可以区分，`verify-then-guide` 常与 exact 同样成功。证明**信息有价值**，未必证明新 kernel 必要。
- `two_sided_unobservable`：观测结束后才出现两侧不同故障，若不能再次验证或安全接续，应拒绝不可保证的承诺；不能把拒绝统计为成功 boarding。
- `both_fail_negative_control`：部分世界两侧皆不可用；保证失败是环境不可恢复，而不是规划器差。
- `late_event_negative_control`：handoff 之后才发生故障；不得产生虚假的提前不可行。

主指标联合报告：FS-Coverage **条件于 initial oracle recoverable**；FCR/PCRL；最终 physical boarding verified VHCR；passenger distance/wait P50/P95；safe continuation independently confirmed SCR；false refusal；observations/latency/motion calls。不要只用纯“避险率”，否则从不服务的策略会获胜。按 physical site **connected components** 聚类并报告样本数；不能把已构造的 0.5 / 1.0 控制概率当现实发生率。

**当前新增受控对照的 12 个 bundled site-pair examples × 5 families 结果：** one-sided exact=100%、fixed B=100%；two-sided observable exact=100%、verify-then-guide=100%、fixed B=50%；two-sided unobservable exact robust success=0%；both-fail exact robust success=0%；late-event 所有方法=100%。这是 deliberate construction/smoke check，不是现实测量。此时新 kernel 相比 verify-then-guide 的独立贡献仍为**未证实**。

### Step 7. 只有在真实 failure + 强 baseline gap 成立之后研究方法

如前 6 步确证缺口，再专门研究 capability-conditioned belief recourse kernel，其科学问题应限于：给定乘客 typed hard resources、有限带有效期观测、车辆停靠/接驳可执行约束，怎样只在具有共同非预知安全后继时许可 irreversible guide；在不完整 evidence 及有限 motion-check 成本下求条件精确与快速边界？保正确性与减开销要分别验证。针对成熟 POMDP shielding/RCSP 的比较尤其关键。

论文 drop-off 迁移需**冻结模型与阈值**后评价；CARLA 要人工标定 nuPlan→CARLA 场景映射，其 blocker spawn 不代表车辆 motion 或 boarding verification；完整服务要报告 handoff—ride—handoff 的条件化 success conjunction，不允许仅靠 vehicle route progress 替代用户可执行 handoff。

## 3. 数据集与 Profile 的下一步：原库先冻结，增量 evidence 并行

**不建议立刻推倒重新生成 5996 nuPlan/GIS scenes。** 保留原始 split、static site facts、nuPlan traffic timestamp/geometry、field-level observed/derived/simulated provenance；先对已有测试 split 做严格 connector gap audit 和 physical-site-stratified shortlist。可审查 connector 数据作为**新的 sidecar**加入，不需要重做全部原始 pedestrian GIS 图。待审核证明缺失是系统性的且具有足够独立证据后，再决定是否重新生成派生图；重建必须保存版本号与底层事实不可变的 hash，并排除同物理站点重复对验证置信度的污染。

现有 8 archetype × 区间均匀是**non-population semantic stress contracts**；保留 `canonical` 不改成真实人口分布。新增 `--design coupled_quantile` 为同一 archetype 生成 q 从严到松、涉及 walk/wait/slope/width/clearance 等明确定义偏序的 paired stress contracts；虽然阈值人为耦合，但**仍不是实测相关分布或真实乘客抽样**。两种设计配套可做 severity monotonicity/counterfactual flips，不能拿来报告人口公平性。

`empirical` 测试必须另行同意采集 task-specific functional constraints、联合测量误差、抽样框/权重，独立城市/站点划分评估。在当前缺少真实记录时输出 `empirical n=0 / not estimated`，**不要根据 PROWAG/ADA 标准反推乘客身体预算**。

## 4. 工程交付清单（相对于上传的原始 CapPlan.zip）

| 文件/修改 | 作用 |
|---|---|
| `capplan/handoff/evidence.py` | 保守 motion packet 校验与时窗覆盖，拒绝 NaN/缺字段/自相矛盾间隔 |
| `scripts/mine_handoff_recourse_pairs.py` | 引入公共 evidence 检查、标记 mining clock、可选独立审查 curb overlay |
| `scripts/build_handoff_temporal_overlays.py` | 使用真实 sidecar 事件时刻重新判定 motion coverage，修复时钟漂移假阳性 |
| `capplan/handoff/audited_curb_overlay.py` | 只接收具名外部审查数据，以内存方式生成审查 curb edge，不改原数据 |
| `scripts/diagnose_handoff_curb_connectors.py` | 真正区分图表达缺口、参数不合格和 typed edge pass |
| `scripts/review_handoff_bundles.py` | 对 strict/relaxed 原始结果 ZIP/目录做 7 步 gate、明确截断边界、生成 site-diverse review queue |
| `scripts/export_handoff_motion_requests.py` | `--diverse` 按 physical site 去重/按城市限额发放小规模车辆验证任务 |
| `scripts/build_handoff_falsification_suite.py` | 5 类强基线反证式受控动态场景 |
| `scripts/summarize_handoff_falsification.py` | 分 event family 输出模型结果；禁止混合叙事 |
| `scripts/build_handoff_profile_suite.py` | 新增 nested/coupled quantile stress sampling，不产生虚假 empirical weights |
| `scripts/screen_handoff_profile_sweep.py` | 支持直接读取固定 suite；支持 strict curb screening |
| `tests/test_handoff_evidence_upgrade.py` | 回归测试时间泄漏、声明来源、baseline 差异、profile monotonicity |

## 5. 执行 SOP（在含 nuPlan/GIS 全量数据的服务器运行）

先将代码包解压覆盖**备份的新目录**，或在与上传 ZIP 完全一致的原版项目中使用统一补丁：

```bash
cd /home/senzeyu2/code/CapPlan
# 先备份原项目。对上传源代码对应的版本：
patch --dry-run -p1 < /path/to/CapPlan_handoff_funnel_upgrade.patch
patch -p1 < /path/to/CapPlan_handoff_funnel_upgrade.patch
export DATASET_TEST=/data0/senzeyu2/dataset/CapPlan/data/outputs/datasets/abilitybench_av_hybrid_test
export OUTPUT_ROOT=$PWD/outputs/handoff_funnel_v2
mkdir -p "$OUTPUT_ROOT"
```

**I. 先跑最紧要的 curb/数据表达诊断；无需重建数据。**

```bash
python scripts/diagnose_handoff_curb_connectors.py \
 --dataset_dir "$DATASET_TEST" --output_dir "$OUTPUT_ROOT/curb_diagnosis" \
 --episode_limit 100 --seed 13
```

**II. 针对已有全量 funnel 结果做证据审计，切记选择服务器中的未截断目录。**

```bash
python scripts/review_handoff_bundles.py \
 --strict /home/senzeyu2/code/CapPlan/outputs/handoff_funnel_strict_curb \
 --relaxed /home/senzeyu2/code/CapPlan/outputs/handoff_funnel_test_all \
 --output_dir "$OUTPUT_ROOT/gate" --max_per_city 12 --max_per_site 1
```

**III. 在现有 full pairs 上先构造具有站点多样性的外部车辆核验请求。**

```bash
python scripts/export_handoff_motion_requests.py \
 --pairs /home/senzeyu2/code/CapPlan/outputs/handoff_funnel_test_all/mining/recourse_pairs.jsonl \
 --output "$OUTPUT_ROOT/motion_requests_diverse.jsonl" \
 --diverse --max_per_city 12 --max_per_site 1
```

不要把 request 当作 motion packet！请从 nuPlan 特定规划器执行日志/安全检查器返回真实的 `configs/handoff_motion_evidence.example.jsonl` 结构（附轨迹文件、checker 版本与日志 hash）。CARLA 干预必须先完成城市地图变换标定，运行 `scripts/run_carla_handoff_intervention.py` 的 `--execute`，同时另行运行实际车辆 controller 和接口 checker。

**IV. 在已冻结 pairs 上运行负控制/故事必要性检查。**

```bash
python scripts/build_handoff_falsification_suite.py \
 --pairs /home/senzeyu2/code/CapPlan/outputs/handoff_funnel_test_all/mining/recourse_pairs.jsonl \
 --output "$OUTPUT_ROOT/falsification/overlays.jsonl" --max_site_pairs 24
python scripts/evaluate_handoff_policies.py \
 --overlays "$OUTPUT_ROOT/falsification/overlays.jsonl" \
 --output_dir "$OUTPUT_ROOT/falsification/policies"
python scripts/summarize_handoff_falsification.py \
 --rollouts "$OUTPUT_ROOT/falsification/policies/policy_rollouts.jsonl" \
 --output "$OUTPUT_ROOT/falsification/report.json"
```

**V. 先固定能力合同测试矩阵，而非假装已有经验人群分布。**

```bash
python scripts/build_handoff_profile_suite.py \
 --suite canonical --seed 13 --per_archetype 16 \
 --design independent_uniform --output "$OUTPUT_ROOT/profiles/canonical_uniform.jsonl"
python scripts/build_handoff_profile_suite.py \
 --suite canonical --seed 13 --per_archetype 5 \
 --design coupled_quantile --output "$OUTPUT_ROOT/profiles/canonical_coupled.jsonl"
python scripts/screen_handoff_profile_sweep.py \
 --dataset_dir "$DATASET_TEST" --output_dir "$OUTPUT_ROOT/profiles/strict_sweep" \
 --profiles_jsonl "$OUTPUT_ROOT/profiles/canonical_coupled.jsonl" \
 --episode_limit 20 --seed 13 --require_explicit_curb_connector
```

`empirical` 路线必须等独立受同意记录，才执行 `--suite empirical --empirical_input /real/consented/profiles.jsonl`；切勿用合成 canonical 填充。

**VI. 若有独立 review PASS 且可审计的 curb 记录，再作单独 strict 实验**（不自动替换主 benchmark）：

```bash
python scripts/mine_handoff_recourse_pairs.py \
 --dataset_dir "$DATASET_TEST" --output_dir "$OUTPUT_ROOT/mining_reviewed" \
 --episode_limit 997 --seed 13 --distance_budget_m 200 \
 --require_step_free --require_interface_complete \
 --require_explicit_curb_connector \
 --reviewed_curb_connectors_jsonl /real/independent_reviewed_curb_connectors.jsonl
```

如果返回 0 pairs，应正式报告和追踪原因，而不是强制抽样“成功故事”。若时间预算与硬件不足，先筛 100 episodes 做数据诊断，再扩到完整 997。

## 6. 数据回传与 CCF-A 决策门槛

请上传以下轻量文件，不需要重传原 nuPlan DB：`curb_diagnosis/connector_gap.json`；`gate/evidence_funnel.json` 和 diverse review queue；外部车辆 motion checker 结果 + 对应执行日志/轨迹索引；`falsification/report.json`；人工 reviewer 的 PASS/FAIL/UNKNOWN 和 evidence references；profile_sweep summary。

研究决策分叉：

- **没有符合物理证据的 A/B**：优先补资料/改 PUDO 表达，不能强行声称现实普遍发生。
- **存在真实 A/B，但 verified/robust fixed + strong shield 仍解决全部**：场景研究仍有价值，但当前新算法动机不足；转向接口协调/现实数据问题或更一般时效观测场景，严格承认上限。
- **真实 A/B + 共享资源历史仍不充分 + 非预知策略显著改善 coverage/commitment 错误 + strong shield 出现可量化服务/计算权衡**：才值得设计 capability-projected belief/recourse kernel 和可证明语义；再补 nuPlan 方法级闭环、CARLA 干预与冻结 drop-off 迁移。

短期可证实的是**精确控制模型可用于挑战当前研究叙事**；短期不可证实的是现实异常事件自然频率、人口能力分布或 CCF-A 新颖性已经成立。
