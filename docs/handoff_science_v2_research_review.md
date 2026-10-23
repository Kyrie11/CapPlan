# CapPlan：最终研究定位、20 轮思想演进审查、H1–H5 证据审计与可证伪实验路线

日期：2026-10-23  |  目标：CCF-A 水平的方法论文  |  本报告不声称已完成现场接驳验证或发表算法复现

## 0. 证据与环境范围

实际读取：独立 TeX、`idea升级.md` 20 轮、`数据集构建.md`、下一阶段研究 SOP、源码 ZIP（390 个初始条目）、`reports.zip`（337 个文件，含目录 348 条目）、`handoff_review_bridge.zip`、`review_bridge_verified.json`。拆包核对 `bundle_manifest.json` 的文件清单、报告字段及同轮结果。**本环境没有**服务器 `/data0/senzeyu2/dataset/CapPlan/data` 和 `/home/senzeyu2/code/CapPlan` 挂载；最新 ZIP 仅有 `geometry_progress.summary.json`、`controlled_factorial.summary.json`，没有二者的逐例 JSONL。因此本文无法对实际 125 个 packet 的每一条路线进行独立重算，也不能执行 CARLA/nuPlan 服务器闭环。这一限制必须写进结果解释。

当前 TeX 的摘要明确聚焦 `evidence-timed, progress-conditioned handoff commitments`，在线状态为 `(x_p,x_v,q,R,H,b,t)`，接纳条件为 observation-causal 策略在所有声明可能世界中到达完成 `G` 或外部确认的安全接续 `S`。TeX 主结果表仍为 `--` 占位；其“方法贡献”属于目标与候选构造，不能按已完成的真实评测书写。

## 1. 逐轮思想演进：20 轮应该保留和放弃的内容

| 轮次 | 该轮核心进展 | 本次最终取舍 |
|---:|---|---|
| 01 | 由 Passenger-Complete、V16 精确复用转向乘客–路缘–车辆联合可行性与不完整证据 | 保留系统范围；V16 作为旧基线而非主贡献 |
| 02 | 从静态完整行程进一步聚焦承诺后退路、不可恢复执行消耗和信息因果 | 确立新主线 |
| 03 | 真实服务场景、非重置资源、提前/延迟观测与稳健 B 的强反例 | 保留稳健 B 作为不可删的强基线 |
| 04 | 将 pickup 设为主任务、drop-off 为迁移验证、ride 为外部交通安全约束 | 写入论文边界 |
| 05 | 构造 motivation funnel，从故事假设出发追查真实候选而非依靠标签准确率 | 保留筛查逻辑，不用筛选池估计发生率 |
| 06 | 发现现实 curb connector、PUDO 语义和 A→B 人行方向可能不完整；允许中途改向 | 否定仅靠 A→B 直达超预算的真实失败主张 |
| 07 | 七阶段 Gate，把车辆接口审计、恢复路径、时窗和强基线分别当做可否证门槛 | 保留审计门槛 |
| 08 | G1/G2 尚无独立真值，G3 为时间敏感图级机制，G4 合成干预 | 保留 UNKNOWN 与否定控制 |
| 09 | 允许第三 PUDO、等待和车辆适应，固定点可能消除之前大部分所谓失败 | 不允许删掉基线动作以制造复杂性 |
| 10 | H1–H3 首次集中审查：车辆历史邻近 ≠ 可合法停靠 | 用停靠姿态、许可、时序与完整登乘作为联合见证 |
| 11 | Science V7：现实 PUDO 属性严重依赖 simulated，失败案例缺 lane/stop 物理绑定 | 不把编码图站点直接称真实合法 PUDO |
| 12 | 收敛到 delayed/incomplete evidence 下的不可撤销 passenger guidance | 保留核心科学问题 |
| 13 | H1/H2/H3/H4 明确区分问题成立证据与算法优势；不能宣称所有 POMDP 失败 | H3 改为公平验证门 |
| 14 | CARLA 清空目标 A 逐渐成功，但不同起点和配置受控不足 | 五组匹配+跨 seed 重复必须成为后续实验 |
| 15 | 旧三因素反例被合理并行行动/等待削弱 | 放弃旧 H4 强命题 |
| 16 | 设立 H5：观测一致的时间窗–资源恢复前沿，目标全安全动作集合等价 | 保留为尚待实现算法命题 |
| 17 | 受限参考 500 例一致，速度比 1.221×；多项 ZIP 审计修正 | 只能称一次观测有限模型实验 |
| 18 | 明确 H4′：真实控制权限下安全动作集合可解释变化 | 以状态依赖替换必须三阶相互作用 |
| 19 | 强调旧数据来源与物理证据缺口，提议几何进度/时钟桥接 | 轻量 sidecar，不重建大图 |
| 20 | CARLA 五组 B 直达和无阻塞改向正对照通过；新增进度包、因素实验和数据审计 | 本轮新结果带来 H4′ 正面机制信号，同时发现严重 provenance 风险 |

总体演进逻辑是：**完整行程理念 → 人车联合交接 → 执行后恢复问题 → 信息时效和观测因果 → 对旧反例的主动否证 → 可承诺动作集合 → 结构化精确计算**。历史各轮中出现的旧路径与 ZIP 交付链接不是本次上传产物，不作为当前部署附件引用。

## 2. 最终故事与研究任务

推荐英文标题（投稿前根据实际结果调整）：**When Is a Pickup Promise Safe? Evidence-Timed and Capability-Conditioned Handoff Planning for Autonomous Mobility**。

现实依据：2026 年 SAE J3261 已将确定 PUDO、寻找车辆、进入车辆、离车等纳入完整可达行程；2025 年 UK DfT/CCAV 自动化客运研究识别非驾驶驾驶员职责与登乘协助；Weidig 等 2025 年真实街道研究显示 PUDO 环境、车身识别和装载方式影响辅助设备乘客选择；2026 年响应式路缘管理综述强调路缘资源的随机到达、时变占用与服务驻留。**这些文献支持问题现实性，不证明 CapPlan 所假定的“承诺后被困”事件已在某个 nuPlan anchor 发生，也不提供自然发生率。**

故事只选一个主要触发事件：乘客已确认前往 A，A 的路缘/接口变化；车辆可以尝试改向 B，但时窗与乘客先前支出不能重置。允许 B 一开始就是可靠固定点；允许车辆改变停车姿态、A 等候再开放、中途乘客转向、第三点 C 和经外部核实的安全援助。这些动作在新算法与基线之间必须等权。

单个交接的联合见证为：有向且符合能力的 passenger path、合法 curb、可执行 stop pose、设备展开/识别区、车辆完整 dwell window、boarding duration、证据在**整个登乘**期间有效、通信确认后的共同起始时刻，以及后续闭环交通安全。乘客到达 B 与车辆分别“有一个可用时段”并不保证它们存在**同一个完整登乘时段**。

形式定义（有限世界、严格保证）：

`A_RP(h) = { a | exists observation-causal policy pi, forall w in Supp(b_h), Reach(G or independently verified S | h,a,pi,w) }`

与错误的 `forall w exists policy pi_w` 区别是未来世界不能在当前被偷看。观察的生成时间、请求/到达时间、可见字段、延迟、确认历史与证据到期都必须进入模型。UNKNOWN（缺证据/超时）不能被当成已证明物理不可行；不可靠替代路径不能被当成已证实安全接续。

外层完整 trip 仍是 `pickup -> ride -> drop-off`，本文首篇只对 pickup 精确求解，drop-off 留冻结机制的迁移任务；不宣称同时解决 fleet assignment、车队优化与所有障碍类别。

## 3. H1–H5 的阶段证据、决定和直接下一实验

| 命题 | 上传 ZIP/JSON 的确切证据 | 不能声称 | 决策与下一个判断门槛 |
|---|---|---|---|
| H1：联合物理接驳不同于车辆抵达 | CARLA 五组完整 6、配置匹配 4；Clear-A/B/无阻塞改向均 6/6，Blocked-A/阻塞改向均 0/6；真实接口审核 0/12 | “已证实真实城市无障碍登乘失败” | **继续。** 多地点多 seed、stop pose、堵塞物真实几何、完整 hold、独立 curb/合法性/部署空间审核；至少取得跨地点正负匹配见证 |
| H2：进度/方向/通知改变恢复 | 预筛选 32 对，存储有向编码满足条件 25，对初始双选不满足 3，缺路径 4；125 packet 来自 25 对；81 packet 满足旧筛查预算 | “25/32 是自然失败频率”；“编码弧即现实单行通道” | **继续且收窄。** 逐边方向证据、弧双向物理性、中途改向 B、第三点 C、ACK 区间和实时余量；同地不同通知时刻成对比较 |
| H3：联合规划方法价值 | 4 个受控比较案例有 2 个弱 endpoint 模型误接受；尚未复现任何已发表 POMDP shield | “成熟 POMDP/RCSP 无法表达此类问题” | **验证门。** 固定安全 B、Verify-then-Guide、实际 ledger 的 react/replan、generic exact belief AND/OR、已发表 factored shielding，必须相同信息、动作和超时 |
| H4′：安全承诺集合随状态变化 | 1000 受控组合/125 几何 packet；安全 271、不安全 729；81/125 至少一次安全性变化；能力敏感 11、证据 21、车辆 81；参考分歧 0，单调违例 0 | “真实三阶因果交互已成立”；“81/125 是独立物理站点或自然事件频率” | **升级后保留。** 先算 2×2×2 三阶有限差分，再给每例是否存在真实联合窗口、反向对照及安全动作集合变化；零三阶是允许的否定结果 |
| H5：结构化求解的严格价值 | 500 例一次观察有限模型 0 分歧，1.221×；本次新增多观察模型 120 个受控案例安全初始动作集合 120/120 一致，但简单 interval boarding 优化 mean 19.97ms **慢于** grid 19.48ms | “已发表 shield 性能领先”；“H5 算法创新已成立” | **作为后续算法方向。** 需要真正观测一致的多分支前沿、时间/资源联合因果摘要、motion oracle 调用复用；在等价安全动作集合下显著减少调用、内存与 p95/p99 |

**重要更新：** `paired_flip_counts` 里的车辆敏感 81、证据 21、能力 11，量度是“分别改变该因素后存在某种结果差异”，不能自动推断二阶或三阶交互；该差异还不能外推到真实位置。新 `factorial-science` 工具在服务器使用逐例文件计算完整八角点三阶 Boolean 差分、需要三个放宽的严格模式与单调性反例。

## 4. 数据信息透明度审计

本轮 `dataset_evidence_audit.json` 对 train/val/test 的 PUDO 条目完成扫描：370,057 / 99,526 / 61,155，共 530,738。核心 curb_height、sidewalk_width、deployment_clearance、blockage_risk 在所有行均为 simulated；legal_stop 只有 34 条 observed；curb_ramp observed 11,382、derived 84,177、simulated 435,179。位于 train/val/test 多 split 的 physical_site_key 4,105 / 4,608（注意统计单位为 key，须核实空间去重定义）。

此外，`reports.zip` 中的 `hybrid_graph.test.pittsburgh.json`、`hybrid_graph.test.vegas.json` 仍报告 `abilitybench_hybrid_accessibility_v1_20260822`、slope 和 width 全量模拟。这些报告的生成世代和当前源码未必完全一致，禁止将它们当作独立当前 GIS 真值；在服务器对现用 graph 重新做版本与 SHA 盘点，再决定是否需要局部数据修复。

**推荐三层冻结：**

- *Natural scene level*：保留官方 nuPlan split、原地理、原 provenance 和真实负对照；不用预筛选率推发生率。
- *Controlled challenge level*：固定场地，系统改变能力、证据、车辆窗、ACK、第三 PUDO、等待、并行，且所有 synthetic hidden truth 只给 evaluator。
- *Independent physical audit level*：首批跨城挑 24–40 个**不同物理位置**的候选正负对照以闭合 H1，之后扩大到更稳定的 100+ 审核点。数量是研究建议，不是现有观察数。

新增观测侧 `y_t`、私有物理 truth `x_t` 和历史 `h_t` 三份数据，**训练输入只含行动当时可见的 y 与来源/可信度**，隐藏世界仅供 evaluator；没有外部真实值的维度保留 UNKNOWN 或明确 controlled simulated。physical-site held-out 必须优先消除 key 重叠，再报告 site-clustered CI；可增加 leave-city-out。现有 8 组 canonical contract 用于等条件对照，不赋予人群 prevalence 权重。

## 5. 未来算法：Observation-Consistent Handoff Viability Frontier（拟议，不是已证明贡献）

输入：`h=(passenger pose, vehicle pose, service phase, consumed typed ledger, instruction/ACK history, belief support, t)`，候选 Handoff option 是 `(directed walk path, legal curb, stop pose, valid dwell windows, deployment geometry, evidence clocks, board interval)`。

1. 保留 observation-equivalence classes 与完整因果 history：不允许凭 hidden world ID 执行动作；先按观察划分 belief，再对同一观察分支共享动作。
2. 正向保存*联合* Pareto 消耗 `(distance, wait, exposure, path/side/door constraints)`；严禁把两个不同可恢复方案的分量最小值组合成虚构的第三方案。
3. 反向构造 `(option, time interval, evidence lifetime, vehicle pose, resource residual, world-support)` 的 viability 前沿。观测孩子按 AND 求交，可控制动作按 OR 求并；从不删除尚未证明无法完成的候选。
4. 延迟 motion calls：缓存有效于 *车辆初始状态 + 停靠姿态 + 地图/阻塞状态 + time window* 的返回见证和不可能性约束；必须带依赖条件与 expiry，不能把 CARLA BasicAgent 没到 B 当运动学不可达定理。
5. 每个 commit 前用独立完整 reference 确认当前 policy 的全部安全动作集合；资源剪枝需给保安全的偏序证明。超时返回 UNKNOWN、请求额外证据或外部确认 safe continuation。

建议理论路线：针对有限明确动作/离散时间/完备 motion oracle，证明 **soundness**、**conditional completeness**、**observation non-anticipativity**、**typed-ledger monotonicity**、**safe dominance and equivalence of action sets**。泛化到连续时间与真实感知误差需要额外保守包络/可靠校准，不能直接由有限模型定理推出。

当前附带的 `causal_policy_v2.py` 是一个**参考验证器/算法脚手架**，而不是上述新 frontier 已完成实现；它允许 `guide` 同步派车、中途改向、单独派车、等待、带延迟验证和完整 dwell/证据窗口，给出初始全部可承诺动作，两条路径分别使用逐秒完整登乘检查与区间检查，刻意限制 scope 为小型有限模型。其 120 例受控测试证实输出一致，却未显示计算优势；这恰是继续研究 H5 需要诚实保留的否定证据。

## 6. 实验矩阵与报告规范

| 实验 | 对照与控制 | 关键输出 | 不允许的结论 |
|---|---|---|---|
| M1：现实物理联合交接 | 每个站点 PUDO 物理审核 + nuPlan/CARLA 五组动作，保留正对照 | verified stop pose/dwell、接口、完整交接窗口与失败归因 | 停稳代理 = 已成功上下车 |
| M2：执行历史必要性 | actual-ledger vs reset-and-replan，同地/同能力/不同通知与 ACK，第三点和车辆恢复开放 | 切换窗口、额外移动、被屏蔽的真实 commit、恢复动作 | 预筛选图距离 = 自然实际失败 |
| M3：H4′ 交互 | 2×2×2 同地点干预 + 真实时窗配对 + 不可缺失的反例 | safe-action-set Jaccard、敏感性、三阶差分、负对照 | 配对 flip = 三阶因果 |
| M4：决策价值 | Robust fixed, Verify-then-Guide, vehicle-first, actual-ledger adaptive, generic exact | verified completion, coverage, false commitment, burden, unknown | 专用法只比被削动作基线好即宣称必要 |
| M5：算法优势 | 等权的 generic exact/factored shield/无缓存、结构核和 full frontier | 完整动作集等价、time/memory/p95/p99、motion calls、unknown、置信区间 | 1.22× 的受限单观察比值 = CCF-A 新算法优势 |
| M6：外部泛化 | Official nuPlan vs physical-site-heldout vs independent audit + CARLA seeds | 跨 site/city、真实字段敏感性、违约事件来源 | 生成器与求解器 agreement = 真实泛化 |

评估安全与覆盖必须同时报告；一个始终拒绝指导的模型可能有零错误承诺但零服务覆盖。未经过现场核实的 `safe continuation` 不得被用来给服务成功凑数。对“没有策略”应标注有限候选/有限时间/已包含哪些动作和观察，不可全局外推。

## 7. 门槛、转向与优先级（建议按顺序执行）

**阶段 A，立即：** 不重建大型数据集。对这轮 125 packet 的原始 JSONL 生成跨城 site-review queue，并单独分析 1000 个 factorial 行的 interaction contrast；抽查与记录 `O->A` path provenance、`midroute->B` 真正方向性、有无替代 B/C。若很多见证缺站点 connector，先修路径与 PUDO 表达，不进入真实事故叙事。

**阶段 B，随后：** 对 CARLA 原 4 组匹配试验补不同 spawn/seed/blocker 尺度和位置、stop pose 与 hold，且明确车辆是否从当前状态可转向 B；物理独立审查出最小跨场地正负对照。这决定 H1 主要是物理冲突还是数据/控制器伪影。

**阶段 C，同时小规模：** 使用新脚手架定义相同世界/动作/观测的 2–4 hidden world + 2–4 PUDO + multi-observation causal exact benchmark。先执行全集一致性和未来信息泄漏、预算不重置、整个 boarding window 等单元测试；再用公开资源 shield 或 PRISM/Storm 等模型验证前沿作强对照。能完全表达但比较慢，不是现有方法的错误。

**阶段 D，H1 和 H2 有足够可复核的正负物理案例之后：** 才做真正的结构化 frontier、增量车辆运动可行性反馈、证明正确性；如果 H5 只在弱基线上加速，保留该模块作为工程优化而不作为主贡献。如果审计发现几乎所有原 A/B 均非现实可用场所，**果断转向 evidence acquisition + verified PUDO/curb-grounded planning**，不要修改数据来保留原故事。如果全面强基线 service-cost 和覆盖已经等价，主问题可转成同等安全下更低的验证调用成本，或者停止专用 kernel 主张。

## 8. 外部文献（本轮核对，不能当作对所有论文的穷尽 novelty 调查）

- SAE J3261_202602, issued Feb 17, 2026: https://saemobilus.sae.org/standards/j3261_202602-recommendations-ads-dvs-accommodate-needs-people-disabilities
- Weidig et al. 2025, real-street PUDO assessment: https://www.maxapress.com/article/doi/10.48130/dts-0025-0012
- DfT / CCAV 2025, driver roles and inclusivity: https://www.gov.uk/government/publications/driver-roles-and-inclusivity-automated-passenger-services
- Heydari-Gharaei & Roorda 2026, responsive curb management survey: https://www.tandfonline.com/doi/full/10.1080/01441647.2026.2723950
- Ajdarów et al., AAAI 2023, resource-constrained goal POMDP shielding: https://ojs.aaai.org/index.php/AAAI/article/view/26715
- Sheng et al., ICRA 2024, centralized and factored safe POMDP shielding: https://ora.ox.ac.uk/objects/uuid%3A18710166-3151-487e-9dea-9a4a7b1b597e
- Junges et al., CAV 2021, almost-sure reachability + public experiments: https://github.com/sjunges/shielding-POMDPs
- nuPlan official closed-loop framework: https://www.nuplan.org/

## 9. 当前交付实现和验证边界

包含：三个新增模块 `causal_policy_v2.py`、`factorial_science_v2.py`、`site_review_queue_v2.py`；更新 `scripts/handoff_study.py` CLI；新增 seeded controlled benchmark 脚本 `scripts/benchmark_causal_v2.py`；例子 `examples/handoff_science_v2/causal_cases.jsonl`；单元测试 `tests/test_handoff_science_v2.py`。

**实际完成**：本环境目标测试 `37 passed, 1 skipped`；新增脚本可编译；种子 13 的 120 个 controlled multi-observation case：120/120 初始动作集合完全一致，0 比较差异；平均 grid 19.478 ms，interval 19.971 ms，p95 grid 67.111 ms、interval 62.911 ms（仅单次本地测量）。性能与正确性是有限合成模型的，不是 nuPlan/CARLA 的真实轨迹结果。全仓 pytest 因环境时间限制没有完成；不能宣称它通过。

**需要服务器补跑**：原始 `geometry_progress.jsonl`、`controlled_factorial.jsonl` 尚未包含于回传包；完成新两项逐例审计和模型输入/独立物理审核之后才能升级 H1/H2/H4′ 和 H5 的证据等级。
