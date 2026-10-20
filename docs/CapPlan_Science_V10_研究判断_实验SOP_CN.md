# CapPlan Science V10：研究空白、联合可执行性、强通用参考与 H1–H4 研究门槛

分析时间：2026-10-20。目标：CCF-A 方法/场景论文的可证伪研究问题。研究材料：本轮上传的 `CapPlan_Science_V9_full_source (1)(1).zip`、`handoff_science_v9(1).zip`、`Safe_POMDP_Online_Planning_via_Shielding(1).pdf`；结合此前上传的 TeX、`idea升级.md`、`数据集构建.md`，并核对相关论文官网/机构公开页面。本报告不声称访问了服务器远端 `/data0/...` 下完整 nuPlan/GIS/CARLA 数据。

## 0. 一页结论

1. **场景创新**：真实接驳要求步行路径—路缘—停车姿态—部署空间—时间窗共同成立；现实支持该服务需求，但目前独立四城 PUDO 物理真值链尚未闭合。
2. **问题创新**：应当研究 *evidence-valid, progress-conditioned, asynchronous handoff commitment*（证据有效、进度条件化、非同步行动的接驳承诺）。H2 使用**状态依赖的可恢复集合**，不预设全局唯一、单调的最后时间。
3. **算法创新**：不能宣称通用 POMDP、shielding、TAMP 在原则上无法编码；应当在相同输入/动作/物理 checker 下证明专用结构实现安全动作集等价（或显式保守近似），且减少高成本 motion calls、状态展开、P95 在线时延。现在先做 generic exact reference，不提前设计新 kernel。
4. **V9 的决定性事实**：四对/两个 episode 的 16 条时间行中，严格 typed-curb 认证均为 0；放宽接口条件后的 16/16 条时间行都存在第三 PUDO 乐观图级恢复候选。**不是 16 个独立现场恢复事件**。
5. **V9 CARLA**：在 5km/h、6m 目标半径、2s 静止驻留的校准中，12 次 clear 有 3 次成功，全部来自 spawn0；另外 9 次失败。12 次的路线列表前一航点完全相同、而实际 spawn 有四个，路线起点与实际 spawn 均相距超过 20m。建议先检查路由与坐标语义，不能把失败归因于真实路缘。
6. **V10 离线受控新事实**：24 个完全声明的有限模型场景，通用 exact AND/OR 得到 20 WIN/4 LOSE；所有 WIN 策略均在对应有限世界中回放通过。六个反事实组有 5 组安全动作集合改变、3 组 WIN/LOSE 改变；8 格 H4 因子实验 7 WIN/1 LOSE，三因子离散对比为 -1。以上**均为 CONTROLLED_SYNTHETIC_NOT_REAL_CITY**，没有获得真实场景自然发生率或超越 ICRA 2024 算法的证据。
7. **通用搜索趋势**：18 个合成规模组合中 5 个在 5000 展开上限触顶，但 18 个均找到至少一种获证安全策略（因为包含事先可靠的锚点）。触顶后动作集完整性和成本最优性**未证明**；更不能宣称论文中的 factored shield 会触顶。

## 1. 对 ICRA 2024 PDF 的方法对照

上传的 Sheng, Parker, Feng (ICRA 2024) 原文：

- 第 III 节给出有限 `S,A,O,T,R,Z`，当前信念的支持集 `Supp(b)`；策略从动作—观测历史映射到动作。
- 目标为 almost-sure reach-avoid：访问 AVOID 的概率 0、访问 REACH 的概率 1。
- V 节 centralized shielding 计算 productive winning region，在 POMCP 的 root 先剪枝或在模拟树内 on-the-fly backtracking。
- VI 节 factored shielding **已经**利用问题特定分解、入口/出口吸收状态、局部 winning region 迭代传播；Lemma 1 证明计算的区域是 productive winning region，但 Remark 明确 factored union 可以严格小于 centralized winning region，所以可能因保守而损失可服务覆盖。
- V 节的 prior-pruning correctness 讨论还承认使用近似 particle belief 时存在漏掉 unsafe state 的风险。论文声明的安全保证必须连同完整状态空间/足够支持估计的假设来阅读。
- VII 节原实验为 Obstacle、Refuel、Rocksample 等离散模型（主要扩展 PRISM），不是 nuPlan/CARLA 真实 curb/door/ramp/motion checker 接口评测。

**关键推论**：如果 CapPlan 的乘客真实位置、累计消耗、消息发送/ACK、车辆状态、停车姿态、证据时间戳、未来物理检查结果都被有限离散化并加入 POMDP，ICRA 2024 的赢域定义原则上能排除三时间窗没有交集的承诺动作。文献没有现成高保真接驳接口，不等于其理论不能发现这类失败。后续 Sheng et al. RA-L 2024《Safe POMDP Online Planning among Dynamic Agents via Adaptive Conformal Prediction》已研究动态多主体预测和在线安全 shielding，也不能声称 shielding 都只适用于静态道路。

**更强的算法研究目标**：在与 centralized/factored generic shield 同一离散模型、同一运动验证 oracle、同一观测历史、同一安全/服务终点下，是否存在特别适合人车 handoff 的**观察分支一致、资源敏感、执行接口保真**的前置条件表示，使得安全动作集不丢失或保守损失可量化，同时明显降低在线时间、离线预计算、内存和昂贵运动查询？现阶段这只是候选算法问题，不是已完成的创新。

另外 Ajdarów, Brlej & Novotný, AAAI 2023《Shielding in Resource-Constrained Goal POMDPs》已覆盖动作消耗资源、部分可观测和保护目标可达性的 shielding。不可以把“历史资源不可回滚”当独创核心。

## 2. 联合可执行性：三个时间集合非空交集仅是必要条件

定义候选 handoff option `o=(p-approach, curb-interface region, vehicle terminal pose, deployment geometry, service occupancy interval, required evidence)`。完整物理交接至少需以下联合见证：

- **行人可达**：存在按当前能力合同、方向性道路、无障碍连接、坡度/净宽/横坡限制可执行的 `Γ_p(t)`；初始已消耗距离、等待、过街成本、通知期间进度均不重置。
- **车辆可达/可停**：存在从车辆真实动态状态出发的 `τ_v(t)`，满足道路方向、碰撞安全、减速到静止、法定停靠、停靠朝向/车门侧、必要驶离；到达 lane center 不等于停止姿态合法。
- **共同物理接口**：乘客可安全进入车门或 lift/ramp 扫掠区，和车辆停止位姿满足几何闭合关系；设备部署完成、存在净宽、合规路侧及安全等待位置。
- **服务时间窗**：必须存在**一个完整时间区间** `[t_board_start,t_board_end]`，乘客已就位、车辆停稳并允许停留、设备可用、占用/阻塞状态符合约束，并且 boarding 持续时间和通知 ACK 均计入。
- **时效证据**：来源、时间戳、置信度和证据覆盖区间应能够支撑相应时间内的动作；对“t0 空闲”的观察不能自动推出“t3 仍可登乘”。如果需要 guarantee，需要预测/不确定性集合或持续检查，以及独立物理检查。
- **确认与终端**：乘客是否确认/拒绝行动指引、能否执行、是否实际识别车辆、入车后是否完成固定/车门关闭；不能让服务终点退化成接近 6 米停车圈。safe-continuation 必须有可核验外部条件，不能作为无限免费的退出按钮。

数学上对已知物理世界 `w`：

`∃ Γ_p, τ_v, t, pose, deployment: pedestrian OK ∧ vehicle safety/stop OK ∧ interface OK ∧ full-dwell window OK ∧ verified terminal OK`。

部分可观测情况更强：

`∃ π_causal  ∀ w ∈ W(b_t): Reach_[Board ∪ VerifiedAssistance](h_t,π_causal,w)`。

论文当前 TeX 第 101 行是 `∀w ∃π^{causal}`，文字上要求共享的非预知策略，却**没有在量词顺序上阻止不同世界选择不同的 π**，建议在正式稿件修正为上面顺序。实验有限世界/离散模型中的 almost-sure（每个正概率支持世界获证）并不提供对模型之外未知未来事件的实地绝对保证。

## 3. 四个命题的证据等级和下一关口

| 命题 | 已成立的内容 | 尚未证明 | 下一步关口 |
|---|---|---|---|
| H1 物理联合接口 | 空间/时间条件联合是必要的数学性质；现实 PUDO 存在与距离不同的安全、装载需求 | 四城独立真实 PUDO + 已验证车辆停车姿态/部署区导致的具体失配 | 审核物理站点（严格+乐观+随机负对照），完成 CARLA/nuPlan motion 绑定 |
| H2 状态依赖恢复区域 | V10 受控中延迟 ACK 改变可恢复首动作；有早期途中改向图级趋势；同一候选的未来停靠窗口可重开 | 真实途中进度/真实路径方向、第三 PUDO、车辆改点、辅助接续全部进入联合策略后的稳定窗口 | 多动作的同地点配对重放，签收时间/进度/车辆状态严格绑定 |
| H3 通用解法受限且需新算法 | 精确 generic 可解有限小模型；简单固定点可解决某些现实合理的负对照；通用显式枚举会出现状态增长 | 公开最强 factored shielding 在相同信息/动作/运动 checker 下是否存在可量化延迟或 coverage 缺口；专用算法尚未实现 | 先看简单基线真实成本；后复现 PRISM/shield/TAMP；不能依据弱代理对比宣称胜出 |
| H4 三因素交互 | 受控 2×2×2 有 7/8 WIN 及一个三因子差分非零；能力改变静态路由、时效改变检查策略 | 独立物理 site 及真实参数支持这种三因素交互，不是只通过人为短时窗/距离诱发 | 固定物理事件，仅对能力、证据有效期、车辆姿态做有来源的前注册参数扫描 |

建议论文的完整逻辑顺序：**H1 提供新场景必要性 → H2 定义随执行历史变化的对象 → H4 说明联合决策的非可分解结构 → H3 证明领域求解结构的效率必要性**。H3 是未来算法贡献的实验条件，不是“所有现有方法失败”的先验假设。

## 4. V10 受控有限研究设计和真实运行结果

### 独立 generic oracle 的正确边界

`capplan/handoff/commitment_model_v10.py` 只在完整显式世界支持、有限 ticks、确定的车辆离散图和乘客**按已确认指引执行**的假设下，计算精确的统一因果策略。输入包含：有方向和宽度的行人图、独立车辆图、两个或以上候选位置、实际步行/等待、active+pending instruction、ACK 时钟、vehicle target、观测历史及过期时间、世界随时间变化的占用、部署/停车姿态的**模拟已知布尔接口**。两主体在每个 tick 并发推进；观察完成后才按结果划分支持集；不会提前以 hidden world ID 分支。

**不包含**连续 CARLA/nuPlan motion checker、观测噪声校准、人类拒绝/非依从性、真实候选合法性证明、复杂支援人员响应。该模块是独立诊断 **generic finite AND/OR oracle**，不是对 ICRA 2024 公开实现的复制；也不是最终 CapPlan 方法。

16 个指定反事实/负对照 + 8 个 H4 三因素格，共 24 例：

- `observe_mutually_exclusive`：只要现场观测及时，统一条件策略能服务两个隐藏世界；不能先偷看世界再分支。
- `width_0p7` 与 `width_1p2`：同一环境中，首选从 `guide:A` 转为 `guide:B`。
- `ack_zero` 与 `ack_two`：共同环境下迟到 ACK 使 `guide:B` 从安全首动作集合中消失，但仍有 `vehicle:A` 可行（不能把局部指引错误当成整项服务必败）。
- `evidence_long` 与 `evidence_short`：两者都可成功，但后者的后续最优策略会新增途中 `verify:B`，期望成本增加；**证据短效不能自动被说成不可恢复**。
- `vehicle_ready` 与 `vehicle_far`：在紧张的等待约束和限定动作下 WIN/LOSE 改变。
- `transient_snapshot_t0/t1/t3`：对同一物理状态的三个**独立冻结反事实状态**分别求解得到 WIN/LOSE/WIN，说明不能预设单调的全局最后时刻；**不是**一个人在原地无限等待到 t3。
- `correlated_all_blocked`：一部分支持世界里所有声明候选都不可用，应输出 LOSE，不靠未验证援助凭空保证。
- `factor_w*.{e1/e9}.{vA/vB}`：改变行人最低宽度、B 证据有效期与车辆初始停止位置，受控 8 格中 7 WIN/1 LOSE；三阶二元离散对比为 -1。只有三条件共同改变才会出现某个组合的无解，是**构造性存在证明**，不是统计显著性或现实发生率。

结果：20 WIN、4 LOSE，6 个分组中的 5 组出现 root safe action 集变化，3 组出现胜负变化，4 组最优首动作变化，5 组最优动作前缀变化，所有 WIN 策略经过对每个支持世界的因果回放。严禁把其中任何一条描述成真实路缘实验。

**启发式反证的诚实口径**：在 20 个 generic exact 判为 WIN 的合成模型中，最近 PUDO 规则给出获证安全首动作 12 次/不安全 8 次；固定已有证据锚点规则为 12/8；单步 vehicle-first 规则为 8/12。三者使用与 generic oracle 相同的动作集合，但这里只比较**第一步动作是否属于 exact 安全动作集**，并未实现三条完整反馈策略，更不等于 POMDP shield 的实际失败率。该结果仅说明局部业务规则在某些构造任务上不足以保证接驳，不能推导成熟通用方法需要新算法。最近点规则已修正为按满足通行宽度的**完整多跳最短路**计距离，而非只比较首段长度。

### 受控复杂度

`bench_generic_commitment_v10.py` 改变候选站点 2/3/4、世界数量 2/4、时域 4/6/8，共 18 种组合；模型中有一个预先验证的可靠选项，因而 **18 组全部至少能找到一个 winning 策略**。状态数从 125 到资源上限 5000；5 组达到 5000 上限，安全动作集的完整性和策略成本最优性未确认。强 baseline 如 factored shielding、POMCP、SMT、TAMP 尚未复现，不能把此结果当作其失败证据。

今后比较必须要求：`SafeActions_method = SafeActions_exact`（或注明保守遗漏），同时报告状态扩展、预计算、内存、查询次数、P50/P95/P99 延迟、验车的实际计算次数与时长、服务覆盖/false promise、未知/超时。没有真实运动求解成本就不能声称显著节约 motion checker 时间。

## 5. V9 独立 CARLA 路线异常

新的只读脚本 `audit_v9_evidence_and_carla_v10.py` 发现 12 条 Clear-A 的 `planned_first_5_xy[0]` 完全相同，为约 `(12.77,-14.24)`，而声明的四个 vehicle spawn 坐标不同；全部路线首航点距离实际 spawn 超过 20m。可能是控制器拿到了与当前车辆起点不相容的路线，也可能是 `get_plan()` 暴露的航点序列并不代表你以为的规划起点。**需要在真实 CARLA 0.9.15 客户端逐步检查，不能仅凭离线日志断定 BasicAgent 的实现 bug。**

新增真实服务器 preflight 的建议：实际 ego 起点与首规划航点计算距离，同时记录 `agent.set_destination()` 前/后的位置与 route trace；匹配几何的 clear 控制必须先通过，才能进行 blocked-vs-clear / redirect 对照。若目标 A 离车辆仅十几米却需要沿 lane topology 绕行上百米，应检查道路方向、是否 A 在对向车道以及目标点是否真正是 stop zone。6m 半径只是受控测试，不能作为现实上下客距离精度。

## 6. 真实数据：先补物理证据，还是重做四城大数据？

当前答案：不重做 5,996-heavy scene 的 GIS/DEM 等大工程。优先利用 `export_handoff_audit_packets_v10.py` 将 V9 乐观图级 PUDO 生成**现场审核队列**，所有 `curb_connector,vehicle_stop_pose,legal_stop,platform_clearance,observation_timestamp,ack_state` 字段保持 `null/UNKNOWN`，绝不自动构造正例。至少同时采：明显可能成功、接口缺失、单体可达但联合待证、未按失败筛选的普通地点。按四城、物理站点而不是时间行分层。现场访问困难时使用能标明拍摄日期、方向、空间误差的官方 GIS + 街景人工审核与明确的 unknown；市政法规/停车限制必须与时间窗匹配。

保留两种不同验证任务：**存在性**只需少量完整审查、有独立物理条件和真实车辆运动的案例；**自然频率**必须来自没有按失效条件筛选的场景及事件基率，不能从 16/16 图级时间行或 24 合成机制得出。

能力 Profile 保留 canonical counterfactual 无人口权重。NHANES/NHATS 等公开功能调查可辅助估计年龄/移动功能等联合分布，不得把“有困难”的问卷选项映射为固定医学 200m；用户提供的任务级行走、等待、设备几何、安全余量才是具体合同。等核心问题稳定后再构建 empirical 评测。

## 7. 建议你搜索上传的论文（优先按原题）

以下标题均有公开论文或正式条目，可用 DOI、会议名定位原文。

| 优先 | 论文 title | 主要威胁或对应 gap |
|---|---|---|
| P0 | **Shielding in Resource-Constrained Goal POMDPs**, AAAI 2023 (Ajdarów et al.) | 资源受限+部分可观测+提前 shielding；不能重复发明资源保护 |
| P0 | **Safe POMDP Online Planning via Shielding**, ICRA 2024 (Sheng et al.) | centralized/factored winning region；分解不可简单称为独创 |
| P0 | **Safe POMDP Online Planning among Dynamic Agents via Adaptive Conformal Prediction**, RA-L 2024 (Sheng et al.) | 时变多主体预测下的 POMDP 安全性，限制“现有 shield 静态”主张 |
| P0 | **Online Partial Conditional Plan Synthesis for POMDPs With Safe-Reachability Objectives: Methods and Experiments**, T-ASE 2021 (Wang et al.) | 条件计划、部分分支、重新规划概率与缓存 |
| P0 | **Which Contingent Events to Observe for the Dynamic Controllability of a Plan**, IJCAI 2016 (Bit-Monnot et al.) | 观测选择/时序可控性不新颖 |
| P0 | **Contingent Task and Motion Planning under Uncertainty for Human–Robot Interactions**, Applied Sciences 2020 (Akbari et al.) | 感知—动作—几何—条件分支共同规划 |
| P0 | **Optimizing Ride-Pooling Operations with Extended Pickup and Drop-Off Flexibility**, AAAI 2026 (Jiang et al.) | 灵活 PUDO、乘客步行和车辆路径优化已覆盖 |
| P1 | **Resource Constrained Pathfinding with Enhanced Bidirectional A* Search**, AAAI 2025 (Ahmadi et al.) | 强资源预算搜索与投影剪枝，需要防止低配对照 |
| P1 | **Understanding Needs and Challenges with Regard to Pick-Up and Drop-Off Zones for Persons with Mobility Disabilities: Assessments via Real-Life Street Scenarios**, Digital Transportation and Safety 2025 (Weidig et al.) | 真实用户 PUDO 风险、设备和距离选择依据 |
| P1 | **Congestion-aware pick-up and drop-off network design problem**, Transportation Research Part C 2026 | 法规/时空停靠空间/真实部署约束 |
| P1 | **Occupancy-aware Trajectory Planning for Autonomous Valet Parking in Uncertain Dynamic Environments**, arXiv 2025 | 时变车位占用+主动感知/轨迹可能已经覆盖相邻场景 |

建议交叉检索词（Google Scholar / Semantic Scholar / IEEE / AAAI / ICAPS）：

- `resource constrained POMDP shielding belief support safe reach avoid`；
- `factored winning region POMDP task motion temporal constraints`；
- `contingent task and motion planning delayed observation dynamic controllability`；
- `belief space motion planning lazy geometric checks incremental replanning`；
- `autonomous mobility pickup curb availability time windows uncertainty`；
- `passenger pedestrian vehicle rendezvous asynchronous guidance acknowledgement`；
- `accessible pickup dropoff wheelchair deployment pose legal dwell`；
- `pickup relocation real-time passenger walking burden mobility constraints`；
- `receding horizon rendezvous temporal uncertainty resource consumption`；
- `physical-interface-aware autonomous vehicle handoff verified service`。

特别请优先上传 **AAAI 2023 全文、RA-L 2024 后续 shielding、T-ASE 2021 条件规划、IJCAI 2016 观测时机、2020 contingent TAMP、AAAI 2026 FlexiPool**。下一轮需要读清这些研究的时间/物理接口假设、复杂度和代码可用性，再判断三层 gap 应如何收缩。

官方/公开出处（实际检索核对）：

- https://ojs.aaai.org/index.php/AAAI/article/view/26715
- https://arxiv.org/abs/2309.10216
- https://arxiv.org/abs/2404.15557
- https://www.kavrakilab.org/publications/wang2021-online-partial-conditional-plan-synthesis.html
- https://www.ijcai.org/Abstract/16/431
- https://www.mdpi.com/2076-3417/10/5/1665
- https://ojs.aaai.org/index.php/AAAI/article/view/41208
- https://ojs.aaai.org/index.php/AAAI/article/view/34892
- https://www.maxapress.com/article/doi/10.48130/dts-0025-0012
- https://www.sciencedirect.com/science/article/pii/S0968090X26002032
- https://arxiv.org/abs/2509.09206

## 8. 下一步服务器命令：从 V9 到 V10（非破坏性增量）

> 先检查服务器是否为这次上传的 V9 source；`patch --dry-run` 失败不得强制修改。以下所有输出都放到 `$CAP_HOME/outputs/handoff_science_v10`，不修改 nuPlan DB/GIS/原先的数据集。

```bash
conda activate capplan
export CAP_HOME=/home/senzeyu2/code/CapPlan
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export TEST_DATASET="$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test"
export PAIRS="$CAP_HOME/outputs/handoff_funnel_test_all/mining/recourse_pairs.jsonl"
export V9="$CAP_HOME/outputs/handoff_science_v9"
export V10="$CAP_HOME/outputs/handoff_science_v10"
export CARLA_ROOT=/data0/senzeyu2/dataset/CRADrive/CARLA_0.9.15
mkdir -p "$V10"
cd "$CAP_HOME"

# 保留 checkout 身份和哈希，避免旧版文件混合
sha256sum capplan/handoff/finite_oracle.py scripts/audit_handoff_coupled_windows_v9.py \
    scripts/run_carla_controlled_handoff_v5.py | tee "$V10/before_patch_sha256.txt"
patch -p1 --dry-run < /path/to/CapPlan_Science_V10.patch
# 仅 dry-run PASS 时正式应用
patch -p1 < /path/to/CapPlan_Science_V10.patch
python -m compileall -q capplan scripts
python -m pytest -q tests/test_science_v10.py tests/test_science_v9.py \
   tests/test_science_v8.py tests/test_handoff_science_v7.py \
   tests/test_handoff_funnel_oracle.py tests/test_handoff_research_gates.py
```

**先运行 A：不需要 CARLA/nuPlan DB 的 V10 机制反证与通用 exact 对照**：

```bash
python scripts/run_handoff_falsification_v10.py \
  --cases examples/handoff_finite_v10.jsonl \
  --output "$V10/generic_exact_controlled.jsonl" \
  --max_states 20000

python scripts/bench_generic_commitment_v10.py \
  --output "$V10/generic_scaling.jsonl" \
  --sites 2,3,4 --worlds 2,4 --horizons 4,6,8 --max_states 5000
```

**再运行 B：不需要启动 CARLA 的历史记录诊断**：

```bash
python scripts/audit_v9_evidence_and_carla_v10.py \
  --v9_dir "$V9" \
  --output "$V10/v9_cross_layer_diagnosis.json"

python scripts/export_handoff_audit_packets_v10.py \
  --v9_rows "$V9/alternatives_dual_4.jsonl" \
  --output "$V10/physical_audit_queue_4.jsonl" \
  --max_pairs 32 --max_candidates 5
```

**如果服务器有完整 `$PAIRS` 和 heavy test data，先扩大旧 V9 的非破坏式统计，再导出审核队列**：

```bash
python scripts/audit_handoff_alternative_evidence_v9.py \
  --dataset_dir "$TEST_DATASET" --pairs "$PAIRS" \
  --output "$V10/alternatives_dual_32.jsonl" --limit 32 \
  --notice_times_s 0,30,60,90 --speed_mps 0.8 --ack_s 10

python scripts/export_handoff_audit_packets_v10.py \
  --v9_rows "$V10/alternatives_dual_32.jsonl" \
  --output "$V10/physical_audit_queue_32.jsonl" \
  --max_pairs 32 --max_candidates 5
```

**C：CARLA 优先级**：先核对 `v9_cross_layer_diagnosis.json` 的 `ROUTE_START_TOO_FAR_FROM_DECLARED_VEHICLE_START` 与实际 BasicAgent 初始化行为；不要仅凭 spawn0 已有 3 次 6m 通过就默认所有 clear controls 有效。先修正路线/目标坐标不一致，再用相同速度/半径/hold 的 clear、blocked、redirect 严格配对。若 6m 只证明“进入较宽目标圈后脚本制动”，仍然不能升级成真实 PUDO/合法停靠/无障碍登乘证据。具体已有 CARLA baseline 入口与本轮 V9 的 `calibrate_carla_control_v6.py` 保持不变，V10 没有虚构新的车辆控制性能。

**D：回传小包（不包括大型 DB）**：

```bash
cd "$CAP_HOME"
zip -j "$V10/handoff_science_v10_review.zip" \
  "$V10/generic_exact_controlled.summary.json" \
  "$V10/generic_scaling.summary.json" \
  "$V10/v9_cross_layer_diagnosis.json" \
  "$V10/physical_audit_queue_32.summary.json" \
  "$V10/alternatives_dual_32.summary.json"
```

如果 32 对尚未运行，上述 `zip` 中两份 `*_32.summary.json` 不会存在；请暂时只打包前三份与 4 对审核摘要，**不要用历史陈旧文件代替同一次运行结果**。若希望进一步核查行级行为，请连同 `generic_exact_controlled.jsonl`、`generic_scaling.jsonl`、`physical_audit_queue_32.jsonl` 单独打包；若 CARLA 已调试，请附一个 valid clear 与一个失败 clear 的报告/轨迹。

## 9. 阶段性停止条件与研究转向

- **Gate A：可靠的物理接口案例**。至少在多个独立 physical site 验证行人连接、合法停靠、车门/坡道展开与相同时间窗。没有这层证据，不可将图级现象宣传为真实系统性 stranded failure。
- **Gate B：多恢复动作的 H2/H4**。正确地枚举途中转向、第三 PUDO、车辆挪车/等待、经验证的援助动作；若 H2 随这些动作加入基本消失，则将论文从“必然 stranded”转成“何时重发指引及由车辆承担服务变更成本”。
- **Gate C：简单策略必要性**。可靠固定 B、优先检查、实际-ledger 重规划若在自然和高风险子集上均服务质量相近，且乘客/车辆负担相近，就应该停止试图证明其决策明显失败。
- **Gate D：通用强求解**。完整 PRISM/POMDP/shield 与 TAMP 公平比较，包含同等候选和物理 checker。若决策一致但成本显著更高，可以考虑**服务接口保持正确的 lazy motion proof + observation-aware capability projected viability**；若求解也不慢、覆盖相当，放弃“新 kernel 必要性”，将主要贡献调整到场景基准与可审计可靠服务，并重新考虑适合的会议定位。
- **Gate E：算法定稿前提**。需要拿到相对独立的 case-level evidence、可信场景比例分母、可复现实测车辆查询耗时，以及发现方法效率差距的最小反例。然后才选择是否设计新 kernel。

**重要：人不是可强制控制的代理。** V10 中人按照收到并确认的指引运动，是有限世界的简化条件。若真实乘客拒绝、延迟、多停留、休息后能力改变或辅助服务兑现概率不确定，必须扩充可观测状态及世界集合；未建模的拒绝风险不能转写为真实 world 下 100% success。
