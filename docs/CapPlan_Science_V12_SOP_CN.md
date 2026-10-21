# CapPlan Science V12：CCF-A 研究定位、相关论文复核、H1–H5 证伪及执行 SOP

**研究状态：2026-10-21；资料：本轮 `related_papers.zip` 中 12 篇完整 PDF、上传的 V11 optimized source、`handoff_science_v11.zip` 原始 JSON(L)/CARLA trace；对照用户此前的 TeX、ideas 和数据构建记录。**

## A. 结论与主张（论文只能写已验证的部分）

**继续，但收窄。** 不以“泛化 POMDP/shield 无法表达三因素耦合”为 research gap。通用 belief-support reach-avoid shield 在充分编码人车、时间、证据和资源后原则上可以处理该问题。更可能具有 CCF-A 水平的目标是：建立可信的 **Evidence-Timed, Progress-Conditioned Executable Handoff** 任务与独立证据链，研究观测因果一致、能力条件化、车辆停姿态约束的接驳可行前沿，并证明**与完整语义 generic exact shield 等价的安全承诺集合**，同时在实际大候选和高成本车辆检查中减少计算、保持或提升服务覆盖。后一部分目前还是研究目标，非既成结果。

推荐英文研究问题：

> How can an autonomous mobility system compute evidence-valid, capability-feasible commitments to a physically executable human–vehicle handoff, while exploiting the coupled interval/resource/observation structure to match generic safety shielding decisions with lower planning and motion-verification cost?

中文：在真实执行历史和能力余量、现场证据的有效期、人车共同时窗与车辆停车姿态共同约束下，如何计算何时可以向乘客发出仍保留可执行服务或经验证安全接续的承诺，并利用接驳特殊结构降低通用规划的计算与运动验证成本？

*定义层*是合理应用研究问题；*相同语义下的高效可证计算*是待证实算法贡献。一个完整 POMDP 状态可以表示这些条件，不能拿三个输入字段构成排他性算法 gap。

## B. 12 篇 PDF 的审查地图

**直接方法竞争，必须正面比较：**

1. `02816-AAAI23.AjdarowM-SR.pdf` — Ajdarów、Brlej、Novotný，**AAAI 2023**，*Shielding in Resource-Constrained Goal POMDPs*。p.1–3 处理 POMDP 下动作资源消耗、可能的补充状态、目标 almost-sure reachability 和 shield；p.2 明确不是只检验当前电量，还避免未来不能达到 goal。不能声称资源非恢复/未来资源安全的新颖性。论文侧重有限状态 consumption POMDP，而不是实际乘客—车辆几何接口，但扩充状态、观测和动作后可以迁移。
2. `Safe_POMDP_Online_Planning_via_Shielding.pdf` — Sheng、Parker、Feng，**ICRA 2024**。p.1、p.3–5 已有 almost-sure reach-avoid 的 centralized 及 factored winning-region shield、online POMCP prior pruning 和 on-the-fly backtracking。**“结构化/分解 belief state”本身已被占位**，必须说明你投影的保持条件、解决的接口/时窗依赖和方法量级差异。与该论文要公平使用同一输入语义。
3. `431.pdf` — Bit-Monnot 等，**IJCAI 2016**，*Which Contingent Events to Observe for the Dynamic Controllability of a Plan*。p.1–2 已讨论不可观察 contingent events 和选择观察实现动态可控。证据时效/观察时机不自动构成全新贡献。
4. `applsci-10-01665-v3.pdf` — Akbari 等，**Applied Sciences 2020**，*Contingent Task and Motion Planning under Uncertainty for Human–Robot Interactions*。将条件策略、感知、人类协作和几何验证联合。CapPlan 不能称“首次 joint TAMP/感知”。
5. `2404.15557v2.pdf` — Sheng 等，*Safe POMDP Online Planning among Dynamic Agents via Adaptive Conformal Prediction*。动态 agent 的预测不确定性和在线安全屏蔽也已有研究；“动态环境和不确定性”不是方法空白。
6. `12074-AhmadiS.pdf` — Ahmadi 等，**AAAI 2025**，*Resource Constrained Pathfinding with Enhanced Bidirectional A* Search*。类型化/多资源路径搜索已有成熟强基线；静态 backwards kernels/dominance 需要比较，不能以普通 Dijkstra 为主对照。
7. `00627-JiangH.pdf` — Jiang 等，**AAAI 2026**，*Optimizing Ride-Pooling Operations with Extended Pickup and Drop-Off Flexibility*。已联合 ride-pooling、flexible PUDO 和车辆运营、约束；“乘客可步行到灵活上车点”不新。可区别于已执行指引与具体物理接口，但仍需实证。

**相邻动机 / 非直接竞争：**

8. `Who gets the curb  A systematic review of responsive curb management research.pdf`，**Transport Reviews 2026**。已广泛讨论实时路缘状态、服务时长不确定性、动态分配、网络影响、公平性；很好地证明问题具有现实地位，却不能据此声称 CapPlan 的耦合必然频繁发生。
9. `Transactions in GIS - 2026 - Obaid - Smart Routing Framework for Urban Curbside Scanning  A Framework for Time‐Optimized.pdf`，**Transactions in GIS 2026**。路缘数据扫描的路线优化与空间覆盖，适合启发主动证据采集成本评估，**不等同**乘客接驳规划。
10. `3589334.3645595.pdf`，**WWW 2024**，*Robust Route Planning under Uncertain Pickup Requests for Last-mile Delivery*。不确定 pickup logistics 可借鉴分布鲁棒任务建模，但与行动能力受限乘客物理交接不同。
11. `Knowing what matters to customers in restaurant curbside pickup through their service-encounter experiences.pdf`，餐饮取餐体验，**语义上不是自动驾驶 PUDO**，不能拿来佐证物理接驳安全。
12. `ssrn-6187898.pdf`，门店 vs 食品杂货取餐竞争的因果分析工作论文，**与当前规划创新无直接竞争**，不能因 curbside pickup 字符串类似而增加文献缺口权重。

**强制公平性修正：** 用户的 `main_handoff_recourse_quantifier_fixed.tex` 在 Compared Methods 中把 generic resource-POMDP shield 写为“matched state/action ... but without typed handoff/interface semantics”。这是方法差距的混杂变量。V12 TeX 改成共享完整接口、观测、物理 checker、类型化资源、世界支持和动作权限，只允许改变求解计算方式。现有 `GenericSupportSolver` 只是自建有限 AND–OR 参考，不能称为 ICRA 2024/AAAI 2023 的忠实复现。

## C. V11 原始结果（重新读取本轮上传 ZIP）

| 项目 | 结果 | 仅能主张 |
|---|---:|---|
| H4 原始串行、wait=0 | 7 WIN / 8，三阶差=-1 | 特定受控动作模型存在交互 |
| H4 同时发人车命令 | 8 WIN / 8，三阶差=0 | 原 H4 反例被自然的并行 dispatch 推翻 |
| H4 串行、允许 wait=1 tick | 8 WIN / 8，三阶差=0 | 原 H4 极度敏感于人为零等待 |
| 方向图乐观允许图连接 | 32 pair ×5 通知，共 160 行，所有行有第三候选 | A→B 不可行≠全局恢复失败；候选不代表现实有效 |
| strict typed-curb 图判定 | 同 160 行，0 确认 | **UNKNOWN 真实 connector**，不是证明无真实 PUDO |
| 物理审查模板 | 17 对、85 引用，但仅 2 episode，12 episode-anchor，10 unique physical-site key，独立验证 0 | 极度重复且仅 Vegas/Pittsburgh；不能算四城样本量 |
| 局部时窗包 | 12 个，全 UNKNOWN | 没有足够 passenger/vehicle/curb 实测输入 |
| CARLA Clear-A | 12 trial，其中 3 车辆 goal-zone + scripted brake 2s 停稳、9 不成功；0 collision sensor callbacks | 车辆代理控制部分校准；未验证任何真实 nuPlan curb/boarding |
| CARLA Blocked-A / Redirect-B | 本次 V11 ZIP **没有**匹配可比干预轨迹 | 不能推断 blockage 因果效应 |

CARLA 3 个成功集中在 `spawn_index=0`、`a_steps=2/3/4`；其余 spawn=1/2/3 的 9 个失败。控制轨迹 `planned_route_length_m` 与 Euclidean 差距、驶向方向及场景的 BasicAgent 路线问题需要审查。不能仅把 `collisions=0` 说成真正零碰撞保证。CARLA Town03 是虚拟小城，尚不能替代四城真实 legal curb 与乘客接口。

## D. 五个可证伪的科学命题（按 gate 顺序）

### H1：物理空间—姿态—登乘持续时间的不可分解性

不能只比较 `PedestrianReachable` 与 `VehicleReachable` 两个布尔量：必须验证行人可通路、typed curb connector、真实停车位姿、合法驻留、设备扫掠区、乘客到达与整段登乘时间。某一位置存在两条分别可行的路径不保证存在同一真实时空交接见证。**逻辑上的交集必要性已经成立，频率/真实样例仍未知**。成功标准：至少每城若干互不重叠的物理站点具备独立来源和明确时间戳的物理记录，以及有 matched clear-controls 的车辆执行结果。失败转向：独立证据不足若成为瓶颈，研究什么时候应该主动核验接驳条件以及如何输出 UNKNOWN。

### H2：连续进度与通知/确认导致恢复余量变化

应使用 passenger 实际路径位置、已消耗 ledger，和 `t_effective = t_notice + delivery + passenger_ack + possible_actuation_delay`；在 ACK 之前执行旧指令产生的移动必须入账。人行道反向可通行必须独立编码，不可把有向图默认当无向；站点可用不仅有 B，还应有第三 PUDO、车辆移动或安全援助。图级变化初步成立；`A→B` 超预算与单时刻阈值不能证明全局受困。失败转向：从“乘客普遍不可恢复”转向“非同步人车协调的额外能力/等待/交通成本”。

### H3：联合决策的真实收益及与 generic shield 的差别

**目前尚未证明。** 对业务规则：比较实际 ledger myopic、固定可靠点、最优观测时机、vehicle-first、三 PUDO。对通用方法：全状态/全动作/全观测/POMDP shield 具有与 CapPlan 一样的物理语义，以安全承诺集合等价 + 拒绝率 + service coverage + motion check 次数 + p95/p99 时延为指标。先在小样本完整数值 oracle 上比决策质量，再谈方法性能。不能把原 `GenericSupportSolver` 说成 published POMDP shielding 的复现。如果固定安全点覆盖和负担都无明显损失，及时承认复杂在线恢复机制不必要。

### H4：能力—证据—车辆的真正交互

V11 已推翻“现有 2×2×2 H4 具有对合理动作空间稳健的三阶交互”。正确的科学假设应该重写为：在**实测或独立验证**的乘客速度/ACK、合法驻留/登乘时间、证据有效期和车辆到达时窗下，联合可执行性的安全承诺边界会随上述三类变量系统性变化，可能呈现多段时窗和 profile-dependent action reversal。新实验须允许 passenger+vehicle 并行行动、等待和第三 PUDO；同时覆盖零/低/高等待成本正负对照。若交互消失，应将 H4 降为敏感性分析，而非用强行串行行动挽救命题。

### H5（拟新增主要算法命题）：精确结构化观测一致可行前沿

给定选项 i、boarding duration δ、乘客到达区间 [a_i,b_i]、剩余等待 w_i、车辆驻留窗 V_i、接口窗 I_i、证据有效窗 E_i、世界 w 的实际开放窗 O_i^w。对**不确定到达且服务时刻必须相同**的模型，固定服务 start 必须满足：

```
T_i^w = [b_i, a_i + w_i] ∩ (V_i ⊖ δ) ∩ (I_i ⊖ δ) ∩ (E_i ⊖ δ) ∩ (O_i^w ⊖ δ)
```

其中 ∩ 为区间交集，⊖ δ 表示扣去完整驻留时长（区间 [l,r] 变成可开始窗口 [l,r-δ]，若长度小于 δ 则为空）。若到达是乘客可自主选择的时间，不确定量词应另写，**不能将存在性与鲁棒性混用**。

一个不观察的固定接驳选项在声明的世界支持下可用 iff `∃ i, intersection_w T_i^w != empty`。若存在一个事前明示、及时完成的观测，其可能结果将 worlds 划分成已知观测类，那么受限“一次观察→指引”策略 iff `∀ observed branch y, ∃ i_y,t_y ∈ intersection_{w in W_y} T_i^w`，且这些 t_y 的到达区间必须是**该观察完成之后**还能实际执行的。这是有限模型的基本区间理论，不宣称前述事实本身的原创性；潜在算法创新在将它扩展到多次观测、异构资源变换、运动姿态检查且精确压缩状态。

**V12 新代码**只实现上述受限问题的独立可证伪计算工具，保证 UNKNOWN 不被写成物理失败：
- `temporal_recourse_v12.py`：合并/相交分段时间窗，按 boarding δ 缩窗；严格处理不确定到达 `all` 与可选择到达 `exists`；固定联合世界安全、一次观察的观测因果约束、缺信息 UNKNOWN；支持多个 PUDO。
- `audit_temporal_recourse_v12.py`：评估及 JSONL 输出。`NO_POLICY_IN_RESTRICTED_CLASS` 不代表一般 POMDP 无解。
- `benchmark_temporal_join_v12.py`：随机整数时窗，独立逐秒求解比对区间正确性与计算开销；不对标发表方法。
- `carla_calibration_v12.py` + `audit_carla_controls_v12.py`：从 trajectory 逐 tick 重算 stationary dwell，验证 SHA，检查匹配 control 才可解释 paired experiment。之前的 V11 无完整 config attestation，不能自动声明 matched。
- `run_carla_controlled_handoff_v5.py`：只增加 `experiment_config` 元数据，不修改 BasicAgent、世界或停车控制行为。
- `triage_handoff_science_v12.py`：把 H1–H4 证据门禁写成保守可复现 JSON。

### V12 本地定量（受控模型，不是论文主结果）

5 个有限时间窗案例：1 个需要观察后按分支发指引，1 个固定可靠第三点，2 个在指定受限策略类中没有方案，1 个 missing interface -> UNKNOWN。独立整数-grid 枚举在所有 320 个随机案例中与区间候选结果一致。80 个随机实例/时窗长度的 micro-benchmark，`100s` 逐秒枚举反而更快，`5000s` 区间约 4.16×；此结果只能支持「在较长时域里窗口合并有潜在结构性效率」，不支持「已超越 ICRA 2024 shielding」。

## E. 数据建设：先补独立真实证据，不推倒已有四城 dataset

保留 nuPlan+GIS+simulated fields 作为 **hybrid algorithm-development**。对于主 H1 不能从同一 simulated geometry 的标签倒推真实可执行。建议从未筛选 natural episodes 抽样 + 几何 candidate 队列并行，分别估计失败条件和覆盖，不按失败标签重采样估计发生率。V11 物理 pending 仅 Vegas 7 个、Pittsburgh 5 个，没有 Boston/Singapore，因此必须扩充物理区域覆盖，优先真实 curb connector/合法停车与 stop pose，而非加新模型。

公共源建议：市政 sidewalk/curb ramp 记录，Project Sidewalk（若对应区域有覆盖）和新加坡 OneMap BFA（需许可、核对范围）；所有 observation 带时间、空间分辨率和来源。CARLA Town03 干预是受控仿真，不证明 Boston/Pittsburgh 等真实 curb 事实。Profile 保留 8 个无人口权重的 canonical counterfactual；可以用 NHANES 步行困难分组作外部敏感性参照，但不能从问卷自动推导个体安全移动距离、等待极限，亦不能把未授权受限调查资料交给在线大模型。

## F. 服务器执行 SOP（无需重建 GIS/nuPlan heavy graph）

### F1. 按实际服务器版本备份并 patch

**Patch 是以本次上传的 `CapPlan_science_v11_optimized(1).zip` 为基线，不是原始 CapPlan 或其他 reviewfix。**

```bash
conda activate capplan
export CAP_HOME=/home/senzeyu2/code/CapPlan
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export TEST_DATASET="$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test"
export STUDY="$CAP_HOME/outputs/handoff_science_v12"
mkdir -p "$STUDY"
cd "$CAP_HOME"

cp -a "$CAP_HOME" "${CAP_HOME}.backup_pre_v12.$(date +%Y%m%d_%H%M%S)"
patch -p1 --dry-run < /path/to/CapPlan_science_v12.patch
# 只有没有 failed hunks 才应用：
patch -p1 < /path/to/CapPlan_science_v12.patch
python -m compileall -q capplan scripts
python -m pytest -q tests/test_science_v12.py tests/test_science_v11.py \
    tests/test_science_v10.py tests/test_science_v8.py \
    tests/test_handoff_research_gates.py tests/test_handoff_funnel_oracle.py
sha256sum capplan/handoff/temporal_recourse_v12.py capplan/handoff/carla_calibration_v12.py \
    scripts/triage_handoff_science_v12.py scripts/run_carla_controlled_handoff_v5.py \
    > "$STUDY/implementation_hashes.sha256"
```

如果 patch dry-run 失败，**不要 `--force`**；先把完整 V12 ZIP 部署到独立实验目录（例如 `/home/senzeyu2/code/CapPlan_science_v12`）执行只读脚本，不动正在训练的代码树。

### F2. 导入 V11 结果并复核 H1–H4

上传的 `handoff_science_v11.zip` 若原本尚不在服务器，可解压到独立 evidence 目录：

```bash
mkdir -p "$STUDY/input"
unzip -q /path/to/handoff_science_v11.zip -d "$STUDY/input"
export V11="$STUDY/input/handoff_science_v11"

test -s "$V11/h4_protocol_robustness.summary.json"
python scripts/triage_handoff_science_v12.py \
    --v11_dir "$V11" --output "$STUDY/triage_v12.json"
python scripts/audit_carla_controls_v12.py \
    --clear_dir "$V11/carla_clear_A" \
    --output "$STUDY/carla_calibration_v12.json"
```

**预期基于本轮上传的原始 ZIP：** `H4=MODEL_ASSUMPTION_SENSITIVE`；`H1=NOT_PHYSICALLY_ESTABLISHED`；CARLA `3 / 12` 车辆停稳代理成功、`PARTIALLY_CALIBRATED`；0 real handoff。若服务器另有新数据，结果可能变化，应上传新的原始同轮报告。

### F3. 验证 H5 受限时窗工具，而不是先训练网络

```bash
python scripts/audit_temporal_recourse_v12.py \
    --cases examples/handoff_temporal_v12.jsonl \
    --output "$STUDY/temporal_cases_v12.jsonl"
python scripts/benchmark_temporal_join_v12.py \
    --output "$STUDY/temporal_benchmark_v12.json" \
    --replicates 200 --horizons 100,500,2000,5000 --options 8 --worlds 4 --seed 13
cat "$STUDY/temporal_cases_v12.summary.json"
cat "$STUDY/temporal_benchmark_v12.json"
```

参数化基准预期所有 mismatch=0。**不建议直接把上述抽象案例结果写进论文 main result**；它验证了用于下一阶段扩展的局部语义和代码正确性。

### F4. 真正提升 H1：独立场景采样 + PUDO 审核

```bash
python scripts/sample_unscreened_handoff_controls_v4.py \
    --dataset_dir "$TEST_DATASET" \
    --output "$STUDY/unscreened_controls.jsonl" \
    --per_city 10 --seed 13
```

注意：这一步仅生成盲抽对照，不等于独立 PUDO 审核。使用 `$V11/physical_pending.jsonl` 作为起始审核任务，但它仅含 Pittsburgh 和 Vegas；再从盲抽及其他四城站点挑选候选，保留 `site_key, data_source, publication/collection_time, license, side/curb/ramp, legal_stopping, stop_pose, interface_deployment, observation_validity`，缺字段保持 UNKNOWN。审核前不要将其导入自动物理成功标签。

### F5. CARLA：先完成匹配控制的合法实验门槛

先启动你已有的 CARLA 0.9.15 headless 服务器（检查其实际位置和端口），确保不会与其他实验同时控制 world。首先只验证 Clear-A，不必立刻导入 32 对真实 PUDO：

```bash
export CARLA_PORT=2000
for spawn in 0 1 2 3; do
  for steps in 2 3 4; do
    python scripts/run_carla_controlled_handoff_v5.py \
      --map Town03 --mode clear_A --spawn_index "$spawn" \
      --a_steps "$steps" --b_steps 8 --seed 13 \
      --host 127.0.0.1 --port "$CARLA_PORT" \
      --target_speed_kmh 20 --arrival_radius_m 6 \
      --service_stop_hold_s 2 --stop_speed_mps 0.5 \
      --freeze_traffic_lights --max_seconds 60 --dt 0.05 \
      --output_dir "$STUDY/carla_v12/clear_A/spawn${spawn}_asteps${steps}"
  done
done
python scripts/audit_carla_controls_v12.py \
  --clear_dir "$STUDY/carla_v12/clear_A" \
  --output "$STUDY/carla_v12/clear_control_gate.json"
```

**只有某个具体 `(spawn,steps)` 的 Clear-A 通过轨迹重新计算，才对该配置执行 Blocked-A 和 Redirect-B；其余配置保持无效控制，不得用于证明 blockage。** V12 在 `execution_report.json` 中补充 `experiment_config`（实验时间、车速、控制模式、地图、车辆蓝图等），并根据这些字段匹配 pair。跑完 blocker/redirect 后（两个额外文件夹用相同 spawn/steps/seed 结构），执行：

```bash
python scripts/audit_carla_controls_v12.py \
  --clear_dir "$STUDY/carla_v12/clear_A" \
  --blocked_dir "$STUDY/carla_v12/blocked_A" \
  --redirect_dir "$STUDY/carla_v12/redirect_B" \
  --output "$STUDY/carla_v12/paired_gate.json"
```

即使 `paired_gate` 判定可比较，它仍只是 CARLA 原生城镇的车辆代理控制结果，**不是独立合法停车/轮椅上车成功**。真实 H1 还要检查 nuPlan 车辆 motion、curb connector、动态 event 与同一真实 PUDO 的 spatial-temporal binding。

### F6. 决策 Gate 与论文转向

- **G1**：四城独立实物界面可核验吗？否 → 先补证据结构和审核，不增大量 simulated positive。
- **G2**：CARLA matched Clear 成功吗？否 → 修 BasicAgent 地图目标和停车控制，不用 Blocked 结果推断 H1。
- **G3**：合理并行调度、ACK、真实有向人行连接、第三 PUDO 后，是否仍存在承诺边界？否 → H2 改成联动服务成本，不写普遍不可恢复。
- **G4**：合同/profile、完整登乘驻留时长、证据有效期、车辆时窗共同导致不同策略？否 → H4 降为敏感性分析。
- **G5**：与相同语义 full-state exact / faithful published shielding 的安全动作集合、时间、内存、motion queries 比较；若只优化时间网格扫描且泛化方法也很快，不把 H5 当新规划算法贡献。

**强基线忠实复现应在 G1–G4 成立且动作/世界/证据参数已经冻结之后投入；不能用伪 shield baseline 先制造差距。**

### F7. 下一次请回传同一实验轮次的最小文件

`$STUDY/triage_v12.json`、`$STUDY/carla_calibration_v12.json`、`$STUDY/temporal_cases_v12.summary.json`、`$STUDY/temporal_benchmark_v12.json`、`$STUDY/implementation_hashes.sha256`、真实物理审核清单及资料来源（若完成）、新的 CARLA `execution_report.json`/轨迹和 matched gate（若完成），不用上传全部 nuPlan DB、图和模型。

## G. 对最终投稿故事的负面结论与转向规则

只证明“多个独立约束交集可能为空”“A→B 超预算”“晚收到信息更糟”都不足以自动达到 CCF-A，且前两项可以被已有 shielding/动态可控规划直接建模。一个诚实的顶会科学故事应包括：完整信息下有意义的物理接口问题 → 针对不可提前决定的事件，计算受观察因果约束的可执行策略 → 利用特殊结构**经过证明**实现更好的规模/成本与覆盖 → 独立现实证据和车辆执行闭环。

如果最后完整资源/时窗/motion 约束下通用 shield 已经足够快，或物理案例始终无法核实，应停止将 H5 写成算法贡献，考虑另立题为 **Evidence-Acquisition for Verifiable Autonomous Handoffs** 的基准/主动验证工作（同样必须与 IJCAI 2016 等观测规划比较）。不要通过任意缩短等待、屏蔽并行动作或删掉真实第三上车点制造“系统性失败”。
