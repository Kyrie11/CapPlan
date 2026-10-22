# CapPlan 接驳承诺：研究必要性、强基线和可证伪实验 SOP

> 本文针对本次上传的 `CapPlan_handoff_stable_optimized(1).zip` 和 `handoff_research_review.zip`。未能访问服务器的 `/data0/.../outputs` 实体文件；所有服务器现场检查均由下列只读命令完成。**受控 finite-model、CARLA Town03、nuPlan+GIS 与已独立核验的真实 PUDO 必须分别标注。**

## 1. 当前证据等级

| 命题 | 上传材料能说明 | 不能说明 | 决策 |
|---|---|---|---|
| H1 物理联合接驳 | 六个 CARLA triplets：Clear-A 6/6、Blocked-A 0/6、真实执行 B 指令 6/6；其中五组配置匹配 | **Redirect-B 0/6** 未成功停稳；12 条 PUDO 实物核验完整率 0/12；无真实合法停车、轮椅部署/登乘真值 | 保留，先修 B 直接到达与无阻塞改向控制 |
| H2 进度相关恢复窗口 | 32 对旧距离条件筛选的站点，已输出 25 条 `AS_ENCODED::DIRECTED_TERMINAL_AB_ASYMMETRY_EXISTS`；3 条初始双点条件不满足 | 原始四个 episode 触发 `KeyError('direction_mode')`，旧成功口径有缺失；图边方向也未验证为真实交通规则；不能计算自然失败率 | 保留，修正异常分母，并做真实图路径/第三 PUDO/执行时钟 |
| H3 专用求解必要性 | 分离式 endpoint 规则在受控时窗例子中存在可构造的误接受 | **无 AAAI'23、ICRA'24 忠实复现**，无证据说明完善的通用 shielding 决策不正确 | 保持未证实状态；严格公平比较 |
| H4 三因素耦合 | 一旦允许并行命令或等待，旧唯一 H4 LOSE 消失，说明模型动作协议十分关键 | 不能依赖受限串行动作声称物理三阶耦合 | 转为 H4′：安全动作/承诺集合随关键因素变化产生稳定策略翻转 |
| H5 结构化计算效率 | 上传中 500 个一次观测受限问题，区间求解与独立网格判定 0 次不一致；记录约 1.222 倍局部微基准加速 | 不是 full belief-POMDP、没有真实运动验证成本、不是发表算法基线 | 继续做安全动作集合正确性、预处理成本与 motion-call 指标，不应立即开发大网络 |

本轮独立审计还指出：`directed_remine` 期望 64 个方向检查记录（32 对 × 2），历史报告仅聚合 56 条成功统计，并列出四个 episode 的异常。因此 **25/32 不是经过完整无偏分母验证的成功率**。新补丁修复无路径提前返回字段，同时每对分别记录失败或 UNKNOWN。

## 2. 论文主张及必要的研究门槛

**建议场景：** 乘客已按旧指引移动；车辆接近后发现无法完成原位置合法、安全的上下车，系统尝试改点。新点虽在乘客路径和车辆道路网络上分别可达，但消息确认延迟、剩余能力、证据到期、接口部署时间和车辆合法驻留时间不能产生同一个可执行开始时刻。

**问题必要性：** 必须提供正对照和负对照，并在**相同的现实或明示模拟约束**下证明：端点级独立可达的某方案缺少联合执行见证，而存在另一个可靠接续策略或者应合理拒绝承诺的情况。不能把全部拒绝当成功；不能把缺少 GIS 记录当物理失败。

**方法必要性：** 一个完整通用 POMDP 在理论上可以编码以上变量。目标应是相同安全动作集合（或证明安全的保守子集），同时显著降低搜索、前处理、车辆 motion-check 调用、内存及 p95/p99 时延。**若强通用方法同等好且足够快，则不能继续宣称 H5 新算法必要。**

形式化可用：

\[ K(h)=\{a:\exists\pi^{\rm causal}\ \forall w\in\mathcal W(h),\ \mathrm{Reach}_{G\cup S}(h,a,\pi,w)\}. \]

“所有世界各自存在计划”并不意味着“同一观测历史下存在非预知可执行策略”。这也是受控有限模型现有的有效检验，但属于已知条件规划范畴，并非独有定理。

## 3. 外部已发表算法：复现顺序与实际可用代码

| 阶段 | 方法/成果 | 官方/可确认项目 | 本研究中的角色 | 注意 |
|---|---|---|---|---|
| P0 | 本项目独立整数时间网格 AND-OR + 区间求解 | 已有 `semantic_oracle_v13.py`、`temporal_recourse_v12.py` | 有限语义精确对照 | **不是已发表 POMDP shielding 复现** |
| P1 | CAV 2021 *Enforcing Almost-Sure Reachability in POMDPs* | https://github.com/sjunges/shielding-POMDPs | 开源 winning region / belief-support shielding 参考 | CAV 2021，不是 ICRA 2024 的代码 |
| P1 | PRISM、Storm/stormpy | https://github.com/prismmodelchecker/prism ; https://github.com/stormchecker/stormpy | 统一有限 POMDP/MDP 和 reach-avoid 模型导出、独立验证 | PRISM 项目存在 ≠ ICRA 特定扩展已公开 |
| P2 | ICRA 2024 Sheng et al., *Safe POMDP Online Planning via Shielding* | 论文称 PRISM extension；目前未确认可直接下载的专用复现包 | 4 种 centralized/factored + prior/on-the-fly shielding 强基线 | 可联系作者询问代码、模型与运行配置；不能把通用 PRISM 当论文复现 |
| P2 | AAAI 2023 Ajdarów et al., *Shielding in Resource-Constrained Goal POMDPs* | 论文有算法与实验，但未确认对应完整公开代码 | resource-aware POMCP shield，重要强基线 | 消耗/补充资源语义需按原论文建模，不能只加普通阈值 |
| P3 | BasicPOMCP、DESPOT | https://github.com/JuliaPOMDP/BasicPOMCP.jl ; https://github.com/AdaCompNUS/despot | 在线 POMDP 价值优化与无保护对照 | **不自带资源-目标 shield 的原论文保证** |

相关链接：AAAI 2023 https://ojs.aaai.org/index.php/AAAI/article/view/26715 ；ICRA 2024 https://arxiv.org/abs/2309.10216 ；CAV 2021 代码仓库含 Docker 与旧工具依赖。复现请固定 Git commit、环境、随机种子、超时、预处理时间，以及给每种求解器完全相同的状态、观测、动作、候选 PUDO、援助选择、车辆检查接口。不能向通用 shield 隐藏 curb/interface 属性然后宣称专用方法必要。

## 4. 代码改动

1. 修复 `directed-remine` 无法找到 O→A/O→B 路径时提前返回缺少 `direction_mode` 的 bug；异常记录不得丢失在外层 episode try 里，输出完整分母与明确 UNKNOWN。
2. 增加 `capplan/handoff/baselines.py`：分离式端点规则、共享完整登乘开始时刻的稳健固定动作、一次观察策略、独立整数网格安全动作集合核对；明确没有复现发表算法。
3. 增加 `capplan/handoff/review_gates.py`：审核 ZIP manifest/哈希、区分缺失证据、低可信模拟、真实失败与计算对照。
4. CARLA 增加 `clear_B` 与 `clear_A_redirect_B` 两个对照（默认旧三组模式保留兼容）；支持生成五组离线执行计划，并单独审核五种模式。以此把 B 本身不可达、无阻塞改向失败和阻塞引起的变化分开。
5. 所有新增功能使用 `scripts/handoff_study.py` 稳定入口，不再新建 `_vNN` 研究 CLI。历史脚本暂不删除，以保留过去的运行结果可复现性。

## 5. 服务器执行（最小无损路径）

### 环境和部署

```bash
conda activate capplan
export CAP_HOME=/home/senzeyu2/code/CapPlan
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export HYBRID_TEST="$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test"
export STUDY="$CAP_HOME/outputs/handoff_study"
mkdir -p "$STUDY"
cd "$CAP_HOME"

# 先备份代码；不动 data/outputs
cp -a "$CAP_HOME" "${CAP_HOME}.before_handoff_baselines.$(date +%Y%m%d-%H%M%S)"
patch -p1 --dry-run < /path/to/CapPlan_handoff_research_patch.patch
# 上一行成功后才执行
patch -p1 < /path/to/CapPlan_handoff_research_patch.patch
python -m compileall -q capplan/handoff scripts/handoff_study.py scripts/run_carla_controlled_handoff_v5.py
python -m pytest -q tests/test_handoff_commitment_baselines.py tests/test_handoff_research.py tests/test_science_v13.py tests/test_science_v12.py tests/test_science_v11.py tests/test_science_v10.py tests/test_handoff_funnel_oracle.py tests/test_handoff_research_gates.py
```

### 先复核此次实验 ZIP

```bash
mkdir -p "$STUDY/review_input"
unzip -qo /path/to/handoff_research_review.zip -d "$STUDY/review_input"
python scripts/handoff_study.py review-gates \
  --review-dir "$STUDY/review_input" \
  --output "$STUDY/research_gates.json"
```

### 两种可证伪的模型对照

```bash
python scripts/handoff_study.py baseline-audit \
  --cases examples/handoff_protocol_controls.jsonl \
  --output "$STUDY/baseline_comparison.jsonl"
python scripts/handoff_study.py baseline-audit \
  --cases examples/handoff_temporal_v12.jsonl \
  --output "$STUDY/original_finite_baselines.jsonl"
python scripts/handoff_study.py observation-benchmark \
  --output "$STUDY/observation_benchmark.json" \
  --replicates 500 --horizon 100 --options 5 --worlds 4 --seed 13
```

必须核对 `mismatch_count=0` 及 `safe_fixed_action_sets_agree`。前两个受控负对照理应呈现“端点规则接受，但完整时间/登乘窗口无可行开始时刻”；两个正对照必须仍然可行。**这不是现实事故发生率，也不是新方法已经战胜通用 shield。**

### 重新跑图级 H2，确保完整分母

```bash
export PAIRS="$CAP_HOME/outputs/handoff_funnel_test_all/mining/recourse_pairs.jsonl"
test -s "$PAIRS" || { echo "找不到旧挖掘原始候选文件"; exit 1; }
python scripts/handoff_study.py directed-remine \
  --dataset-dir "$HYBRID_TEST" \
  --pairs "$PAIRS" \
  --output "$STUDY/directed_remine.jsonl" \
  --per-city 8 --times 0,15,30,60,90 --speed-mps 0.8 --ack-s 3 \
  --third-options
cat "$STUDY/directed_remine.summary.json"
```

预期这次不再出现 `KeyError('direction_mode')`。报告含 `original_selected_pairs`、`evaluated_pair_count`、`unknown_pair_count` 与各方向分类；对真实街道单向性仍须独立审核。每次图级检测均保留真实时间、已走距离及第三候选，但**合法停车和证据有效期尚未在这一阶段被认证**。

### CARLA 五组控制实验：不能省略两个新增正对照

历史成功的 Clear-A 轨迹必须在服务器上已有实际路径。请用以下只读命令寻找：

```bash
find "$CAP_HOME/outputs" -path '*/clear_A/*/execution_report.json' | head -20
```

确认某个真实的 `clear_A` 目录后：

```bash
export CLEAR_DIR=/path/to/existing/carla/clear_A
python scripts/handoff_study.py carla-plan \
  --clear-dir "$CLEAR_DIR" \
  --output-dir "$STUDY/carla5" \
  --max-controls 6 --redirect-at-s 8 --extended-controls
# 审阅脚本/确认唯一 CARLA 仿真实例及Python客户端版本
cat "$STUDY/carla5/run_matched_carla_v13.sh"
# 只有环境及仿真配置正确后人工执行：
# bash "$STUDY/carla5/run_matched_carla_v13.sh"

# 完成后只读审核：
python scripts/handoff_study.py carla-fiveway \
  --study "$STUDY/carla5" --output "$STUDY/carla_fiveway.json"
python scripts/handoff_study.py carla-triplets \
  --study "$STUDY/carla5" --output "$STUDY/carla_triplets.json"
```

**解释优先级：** 如果 `clear_B` 失败，优先调查 B 坐标/规划器/停车目标；如果 `clear_B` 成功但 `clear_A_redirect_B` 失败，先调查改向状态与时钟；如果两者成功而 `blocked_A_redirect_B` 失败，才有更强的受控阻塞交互证据。即便全部通过，也不是现实 curb 合法性或轮椅登乘成功。

### 打包回传（不包含大型 DB/图/轨迹）

```bash
python scripts/handoff_study.py review-pack \
  --study "$STUDY" --output "$STUDY/capplan_handoff_review_small.zip"
```

若 CARLA 新的五组失败，请另外上传**对应少量** `execution_report.json`、相关 `trajectory.jsonl` 和实验配置，不能只看总体 success 标志。

## 6. GO / NO-GO 与数据升级

**问题 GO：** 至少找到若干不同物理站点、不同 episode 的具体机制样例，每个样例有 A/B/第三候选动作空间、经过验证的路径/姿态/时间证据和必要的安全接续对照；在相同世界、相同成本约束下，分离式规划确实产生没有共同执行见证的承诺，或可靠固定点造成可复核覆盖/负担损失。样例发生频率不能由预筛选候选推断。

**算法 GO：** 先将相同案例导出为完整的有限 belief-support 模型，使用 generic exact 和发表算法复现比较。证明 safe-action 集合一致或可量化保守性，再比较含预计算的总耗时、p95/p99、状态扩展、RAM、motion-check 调用；只看当前 1.22× 的微基准差距不足以支持 H5 的核心贡献。

**目前不建议重新构建四城 graph。** 现有 inventory 显示 train/val/test/all 对应 3999/1000/997/5996 个 episode（这些是服务器 metadata 统计，而非本环境独立解析原始 DB 的结果）。增强建议只添加 `outputs/handoff_study` 旁路 temporal event/observation/pedestrian progress/vehicle pose sidecar 和独立证据记录，保留每字段 provenance 和物理站点ID。开发/挑战集可以使用受控事件，但正式测试要包含未按失败条件筛选的自然地理场景、site-heldout 和严格的物理证据 UNKNOWN。

**若 CARLA 控制无法获得合理的 B 目标停稳，请先解决 motion 控制问题，不要设计声称可解决它的新算法。** 若真实条件下“更早确认/固定可靠 PUDO”与精确联合作用无差距，应收缩复杂在线恢复主张，转向证据足够性及结构化规划的可证正确效率问题；若同等动作权限下通用 shield 也达到同等安全、覆盖和成本，则 H5 计算必要性应被否定或重新定义。
