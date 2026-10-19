# CapPlan Science V8：论文研究问题、工程执行与证据门槛

**日期：2026-10-19；目标：先证伪，再确定 CCF-A 论文主要贡献。**

## 0. 结论边界

上传的 `CapPlan.zip` 内没有 `.tex`，本轮论文定位来自代码、`idea升级.md`、`数据集构建.md`、`execution.md`、V7 handoff 结果、`reports.zip`。由于服务器 `/data0/...` 未挂载在此次对话的计算环境，本轮未真实运行 nuPlan/CARLA。V7 离线证据已被脚本独立重读、聚合并提供精确 SHA256。**不能把未运行的服务器命令当做已有新结果。**

本论文优先研究：**延迟/过期环境证据下，能力受限乘客行动会消耗不可恢复的资源，车辆与乘客之间的 PUDO 承诺何时安全，以及能否在给定动作空间中保持非预知的可执行恢复方案**。

若实验发现简单的 `verify-then-guide`、robust fixed PUDO 或通用 belief-resource shield 与我们的能力/车辆接口建模达到同样效果，必须承认“新在线决策算法是必要的”尚未成立。可转向专用结构化 kernel 与精确求解效率，但须用强基线比较。

形式化：在观测历史 $h_t$、位置 $x_p,x_v$、已耗资源 $r_t$、观测有效期 $e_t$ 下，一个不可逆 `guide(A)` 应只有在所有与历史兼容的可能世界中，存在**同一个非预知后继策略**能完成验证过的乘客接驳或独立验证的安全接续时才被允许。不能用 `∀w ∃ clairvoyant plan` 替换 `∃ causal policy ∀w`。若目标是概率风险而非 worst-case，须声明 risk budget、概率校准来源及置信度，不得混用。

## 1. 本轮确认的事实（仅限附件）

- V7 `evidence_chain.json`：64 条审查行、55 个不同 `(city,physical_site_key)`；64 条 `legal_stop`、64 条净宽与部署空间均为 simulated；实物证实的 PUDO 与 boarding 均为 **0**。`curb_ramp` observed 14、derived 6、simulated 44。
- 32 组精选 A/B 与 17 个不同 physical site-pair。**32 次比较不是 32 个独立事件。**
- `midroute_continue`：0/15/30/60/90/120 秒通知在图上可改向 B 的数量分别为 32、32、28、8、1、1；暂停确认控制分别为 32、32、32、12、1、1。它们是选择后的 graph-only 格点检查，不能推算事故频率。图只用了静态能力筛选与无向边假设。
- 1,280 次 hybrid profile-pair × 固定候选配对，有 231 次 A/B 结果不同（包含相关的 32 个案例与规范化应力 profile；不代表现实乘客比例）。
- CARLA 5 km/h 4 次、8 km/h 4 次中，Clear-A 均无法满足 stationarity + 2s dwell。**因此 Blocked-A 没有有效因果配对对照；不得报告 CARLA 已验证阻塞失败。**
- `reports.zip` 中 test hybrid graph 的跨坡 cross_slope 四城 100% simulated；Pittsburgh 和 Vegas 的 slope/width 也均为 simulated。不能把 simulated physical properties 写成 GIS measured ground truth。

## 2. 研究假设、必要证据与停止条件

| 假设 | 必要实验 | 失败后应该怎么办 |
|---|---|---|
| H1：真实乘客-路缘-车辆接口存在不同于车辆可达性的 mismatch | 独立现场/正射/街景复审、合法停车依据、lane/stop pose、typed curb connector、设备与车门要求；至少含正/负样例 | 如果仅有 synthetic 字段：论文只能描述 benchmark 机制，不谈已发现真实 failure |
| H2：行动历史与反馈延迟形成非平凡的安全承诺期限 | 途中改向、不同 ack/乘客速度、暂停/继续、时窗、全候选 PUDO 与车辆变更的恢复检查 | 早期总能恢复：改写成 timing-sensitive，而不是 static A->B no recourse；第三候选可恢复时停止宣称 stranded |
| H3：专用规划结构超越聪明简单策略 | 相同能力/车辆/观测/动作/计算预算下，fixed, verify-then-guide, myopic actual-ledger, generic published-strength belief shield, exact small model | 简单策略持平：改主张为效率/正确性，或放弃复杂 kernel 必要性 |
| H4：可独立验证的服务完成 | 控制正常停车、合法车位、动态有效性、board interface 和真实乘客确认；独立方法专属 nuPlan 或明确边界的 CARLA | 只有车辆轨迹：严禁写 verified boarding / passenger-complete success |

**Research-gap 防护：** AAAI 2023 resource-constrained POMDP shielding、ICRA 2024 safe POMDP online shielding 已覆盖一般部分观测屏蔽；AAAI 2025 enhanced bidirectional A* 已覆盖高性能 RCSP；SAE J3261（2026-02-17）已将 complete trip 写入推荐实践。新贡献必须来自乘客 typed 能力/服务生命周期/时效证据/连续车辆接驳接口的**耦合、精确可证明机制或显著可复现效率**，而不是把这几个词放到同一张图。

## 3. 本轮新增工程

1. `scripts/audit_science_v8.py`：只读 V7 原始结果，给出六个 fail-closed gate、站点去重与 simulated 字段统计、CARLA 正常对照诊断、图上最后安全改向的**离散时间区间**。按原始文件 SHA256 记录实验版本。
2. `scripts/audit_handoff_all_alternatives_v8.py`：使用**已有** `pudo_anchors.jsonl` 与行人图，从通知变成可执行那一刻的**途中位置**，保留真实累计距离，在同一个资源约束下检查 B 和第三 PUDO。只接受已有 legal+unblocked PUDO；默认要求 typed curb edge；可用 `--allow_missing_typed_curb` 作**乐观敏感性对照**而非现场物理证明。按 episode 分组，每张图只加载一次；相同 edge endpoint 的 shortest path tree 复用。
3. `scripts/calibrate_carla_control_v6.py` 增加 `--require_stationary_stop`、速度、停稳时长与信号相位条件。先要求 Clear-A 停稳，不是旧版只到达 goal zone 就将候选称为可以停靠。
4. `scripts/run_carla_paired_v7.py` 重新从每一帧原始 trace 计算静止驻留时间，既检查 JSON summary、轨迹 SHA256，又检查时间递增、目标点空间距离和速度门槛；不能仅修改 summary JSON 就让停稳 gate 变 PASS。
5. `tests/test_science_v8.py` 和更新后的 V7 测试，覆盖图上第三 PUDO 反证、累计已耗能力、不可将离散 grid 解释为确切 deadline、伪造 CARLA dwell 摘要应被拒绝。

## 4. 最应该先运行的指令

在科研服务器使用独立工作目录，不覆盖生产环境：

```bash
conda activate capplan
cd /home/senzeyu2/code/CapPlan
export CAP_HOME=$PWD
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export DATASET_TEST="$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test"
export PAIRS="$CAP_HOME/outputs/handoff_funnel_test_all/mining/recourse_pairs.jsonl"
export V7="$CAP_HOME/outputs/handoff_science_v7"
export V8="$CAP_HOME/outputs/handoff_science_v8"
mkdir -p "$V8"
python -m pytest -q tests/test_science_v8.py tests/test_handoff_science_v7.py
```

**A. 证据审计（最先做；不依赖 CARLA/nuPlan DB）**

```bash
python scripts/audit_science_v8.py \
  --v7 "$V7" \
  --reports "$DATA_ROOT/external/reports" \
  --output "$V8/offline_audit"
cat "$V8/offline_audit/audit_summary.json"
```

**B. 反证：“A->B 不可恢复”是否误杀第三 PUDO？** 默认只查经过 typed curb connector 的候选，其他物理字段依然来源于 hybrid，输出 `graph_eligible` 不能宣称 verified。先 `--limit 4`，然后扩展到 32：

```bash
python scripts/audit_handoff_all_alternatives_v8.py \
  --dataset_dir "$DATASET_TEST" \
  --pairs "$PAIRS" \
  --output "$V8/all_alternatives_4.jsonl" \
  --limit 4 --notice_times_s 0,30,60,90 \
  --speed_mps 0.8 --ack_s 10
cat "$V8/all_alternatives_4.summary.json"

# 只有小样本运行成功后，才扩大：
python scripts/audit_handoff_all_alternatives_v8.py \
  --dataset_dir "$DATASET_TEST" \
  --pairs "$PAIRS" \
  --output "$V8/all_alternatives_32.jsonl" \
  --limit 32 --notice_times_s 0,30,60,90 \
  --speed_mps 0.8 --ack_s 10

# 乐观几何对照，绝不能解释成物理上真的可走：
python scripts/audit_handoff_all_alternatives_v8.py \
  --dataset_dir "$DATASET_TEST" \
  --pairs "$PAIRS" \
  --output "$V8/all_alternatives_32_optimistic.jsonl" \
  --limit 32 --notice_times_s 0,30,60,90 \
  --speed_mps 0.8 --ack_s 10 --allow_missing_typed_curb
```

解释规则：存在 `non_B_alternate_graph_feasible = true` 表示 B-only 的失败不是图上所有候选均失败；它**不是**证明 C 物理可达。不存在任何候选则只意味着在当前筛选、当前图、当前动作集、该时间点没有找到候选，不是全局 `stranding` 证明。`A->B` 已经超出预算却 `B_graph_feasible=true` 也不矛盾：系统可让乘客在 A 途中转向 B。

**C. CARLA 正常停稳对照**。不要在 V7 已失败的相同 8 次里事后扩大阈值声称因果成功。将 radius / speed 实验条件全部保留。先启动 CARLA 独占实例：

```bash
export CARLA_ROOT=/data0/senzeyu2/dataset/CRADrive/CARLA_0.9.15
export PYTHONPATH="$CARLA_ROOT/PythonAPI/carla:$CAP_HOME:${PYTHONPATH:-}"
export CARLA_LOG_DIR="$V8/carla_server"
bash scripts/carla_headless_v5.sh
# 确保 world 无其他客户端 tick，无其他车辆，以及 PythonAPI 版本为 0.9.15
```

严格控制（原 4m、8 km/h，**即使仍失败也需要保留**）：

```bash
python scripts/calibrate_carla_control_v6.py \
  --output_dir "$V8/carla_clear_preflight_r4" \
  --map Town03 --spawn_indices 0,1,2,3 --a_steps_options 4,7,10 \
  --target_speed_kmh 8 --max_seconds 50 \
  --arrival_radius_m 4 --service_stop_hold_s 2 \
  --require_stationary_stop --freeze_traffic_lights
```

独立的、**事先指定**服务半径敏感性条件（6m，只能作为车辆目标区的实验假设，不是法规上的 curb 可达阈值）：

```bash
python scripts/calibrate_carla_control_v6.py \
  --output_dir "$V8/carla_clear_preflight_r6" \
  --map Town03 --spawn_indices 0,1,2,3 --a_steps_options 4,7,10 \
  --target_speed_kmh 8 --max_seconds 50 \
  --arrival_radius_m 6 --service_stop_hold_s 2 \
  --require_stationary_stop --freeze_traffic_lights

# 若、且仅若 preflight_r6/calibration_summary.json
# 有明确 stationary control 候选，才做与该半径完全一致的对照。
python scripts/run_carla_paired_v7.py \
  --calibration "$V8/carla_clear_preflight_r6/calibration_summary.json" \
  --output_dir "$V8/carla_paired_r6_speed8" \
  --map Town03 --max_trials 4 --seed 13 \
  --speed_kmh 8 --radius_m 6 --stop_hold_s 2 \
  --max_seconds 50 --redirect_at_s 5
```

对照问题是**车辆**在脚本障碍前后的可控停靠行为，不是 nuPlan PUDO 可达、不是真实 curb legality、不是人上车。即使对照 PASS，H1/H2/H3 仍待独立确认。

**D. 最小返回文件**

```bash
cd "$CAP_HOME"
# 不要直接上传任何真实定位精度敏感原始 DB 或庞大日志；先审阅结果。
python - <<'PY'
from pathlib import Path
import zipfile
r=Path('outputs/handoff_science_v8')
allow=['offline_audit/audit_summary.json','offline_audit/commitment_window_grid.json',
 'offline_audit/carla_clear_control_diagnosis.csv',
 'all_alternatives_4.summary.json','all_alternatives_32.summary.json',
 'all_alternatives_32_optimistic.summary.json',
 'carla_clear_preflight_r4/calibration_summary.json',
 'carla_clear_preflight_r6/calibration_summary.json',
 'carla_paired_r6_speed8/paired_summary.json']
with zipfile.ZipFile(r/'science_v8_review_bundle.zip','w',zipfile.ZIP_DEFLATED) as z:
    for n in allow:
        p=r/n
        if p.is_file():z.write(p,n)
print('Review bundle:',r/'science_v8_review_bundle.zip')
PY
```

之后提供 `all_alternatives_32.jsonl` 与 independent curb review 的可审阅小样本（脱敏），再决定是否实现较昂贵的资源型 belief shield、motion-kernel 与 frozen drop-off transfer。

## 5. 论文实验矩阵：必须先证伪而不是训练新神经网络

- H1 实景：不能只用训练集分数；按真实 PUDO/site 去重，挑选跨四城的正/负/证据不足样例；所有物理字段保留 source/measurement confidence。随机抽取 **未用是否失败筛选过** 的场景估计覆盖率。
- H2 时间：速度、已行走资源、ack delay、观测 stale window、通知发生时刻、继续/暂停行走、vehicle relocation 与 C 站点；指标是 conditional feasibility, recovered service, false commit, no-commit service loss, time/cost，不只是某个 0/1 failure rate。
- H3 算法：固定 A/B、vehicle-first、verify-then-guide、last-observation heuristic、actual-ledger myopic、通用 resource POMDP shield、能力条件化求解器；相同信息、action-set、车辆核验证时延。通用 shield 成功就不能声称“其他模型系统性失败”。
- H4 数据：原数据不重建；用于训练的 official split 与物理站点无交叉的 secondary evaluation 分开；当前 cross_slope 大比例模拟且坡度覆盖不齐，必须区分 `observed`, `derived`, `simulated`, `missing` 并报告按来源层化差异与缺失消融。

## 6. 可投稿的核心科学切入点与分支

优先候选：**Capability-conditioned, evidence-valid, motion-executable handoff commitment**。如果真实非预知恢复动作集在控制环境中出现不可计算/高代价场景，进一步研究能力契约诱导的安全承诺前置条件、动态观测过期传播、惰性车辆运动核验证；证明 soundness 与 finite-support conditional completeness（只在模型假设内），将 V16 多查询核复用作为效率部分。

如果强 baseline 足够好：保留强算法作 baseline，聚焦 **不完整 GIS 与 PUDO 物理证据的分级、可审计验证和最小信息获取策略**；但是这本身仍需要独立验证样本与显著反事实增益。绝不能将已知一般 shield/RCSP 算法重新命名为论文主创新。
