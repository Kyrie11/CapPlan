# CapPlan 下一阶段可直接执行的 SOP（V2 研究工具，2026-10-23）

> 重要：原始 nuPlan DB/GIS、`data/outputs/datasets` 和 `data/outputs/prepared` **全部只读，不删除不覆盖**。请在现有源码 ZIP 的对应版本上部署本轮增量。研究输出另存 `outputs/handoff_study`。旧源码上执行 `patch` 前必须 `--dry-run`，失败不可 `--force`。

## A. 在服务器部署并验证

```bash
conda activate capplan
export CAP_HOME=/home/senzeyu2/code/CapPlan
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export STUDY="$CAP_HOME/outputs/handoff_study"
export HYBRID_TEST="$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test"
export PAIRS="$CAP_HOME/outputs/handoff_funnel_test_all/mining/recourse_pairs.jsonl"
mkdir -p "$STUDY"
cd "$CAP_HOME"

# 先检查将要修改的服务器 checkout，不盲目安装。
sha256sum scripts/handoff_study.py capplan/handoff/evidence_bridge.py
cp -a "$CAP_HOME" "${CAP_HOME}.before_handoff_science_v2.$(date +%Y%m%d_%H%M%S)"

# 将下载的 CapPlan_handoff_science_v2.patch 放到服务器后：
patch -p1 --dry-run < /path/to/CapPlan_handoff_science_v2.patch
# 只有上一步 100% 通过，且代码基线吻合，才执行：
patch -p1 < /path/to/CapPlan_handoff_science_v2.patch

python -m pytest -q tests/test_handoff_science_v2.py \
  tests/test_handoff_evidence_bridge.py tests/test_handoff_research.py \
  tests/test_handoff_commitment_baselines.py
python -m compileall -q capplan/handoff scripts/handoff_study.py scripts/benchmark_causal_v2.py
```

若服务器 checkout 与上传 ZIP 不一致，**不要 force patch**。比较版本后先在旁路解压提供的 `CapPlan_handoff_science_v2_full.zip`，再决定如何将新增文件回迁；不要直接覆盖服务器上未追踪的改动。

## B. 直接分析新上传报告（本地可运行）

```bash
python scripts/handoff_study.py review-gates \
  --review-dir /path/to/handoff_review_bridge.zip \
  --output "$STUDY/review_verified_current.json"
```

## C. 获取缺失的逐例输入并实际检验 H4′

若之前服务器保留了 `geometry_progress.jsonl` 和 `controlled_factorial.jsonl`，不要重算大图，直接运行：

```bash
test -s "$STUDY/geometry_progress.jsonl"
test -s "$STUDY/controlled_factorial.jsonl"

python scripts/handoff_study.py factorial-science \
  --rows "$STUDY/controlled_factorial.jsonl" \
  --output "$STUDY/factorial_science_v2.json"

python scripts/handoff_study.py physical-review-queue \
  --packets "$STUDY/geometry_progress.jsonl" \
  --max-sites 40 \
  --output "$STUDY/physical_review_queue_v2.jsonl"
```

如果 `test -s` 失败，先按上一份 SOP 生成真实图 sidecar，再重新运行：

```bash
test -s "$PAIRS"
python scripts/handoff_study.py directed-remine \
  --dataset-dir "$HYBRID_TEST" --pairs "$PAIRS" \
  --output "$STUDY/directed_remine.jsonl" \
  --per-city 8 --times 0,15,30,60,90 \
  --speed-mps 0.8 --ack-s 3 --third-options

python scripts/handoff_study.py progress-packets \
  --remine "$STUDY/directed_remine.jsonl" --pairs "$PAIRS" \
  --dataset-dir "$HYBRID_TEST" --max-packets 300 \
  --output "$STUDY/geometry_progress.jsonl"

python scripts/handoff_study.py controlled-factorial \
  --packets "$STUDY/geometry_progress.jsonl" \
  --assumptions configs/handoff_timed_interventions.example.json \
  --max-packets 200 --output "$STUDY/controlled_factorial.jsonl"

python scripts/handoff_study.py factorial-science \
  --rows "$STUDY/controlled_factorial.jsonl" \
  --output "$STUDY/factorial_science_v2.json"

python scripts/handoff_study.py physical-review-queue \
  --packets "$STUDY/geometry_progress.jsonl" \
  --max-sites 40 --output "$STUDY/physical_review_queue_v2.jsonl"
```

检查 `paired groups=125` 是否完整、三因素 Boolean 交互数量是否为零、是否有“不由任何两因素替代的真三因素必要”模式。**非零三阶差分也只能说明合成响应交互**；不要据此宣称真实路缘物理因果。

## D. 立即运行多观察 exact 基线，但不要误称已发表 shield

```bash
python scripts/handoff_study.py causal-exact \
  --cases examples/handoff_science_v2/causal_cases.jsonl \
  --max-states 20000 \
  --output "$STUDY/causal_exact_smoke.jsonl"
cat "$STUDY/causal_exact_smoke.summary.json"

# 只在新声明的合成模型上扩大可复现正确性检查
python scripts/benchmark_causal_v2.py \
  --seed 13 --n 120 \
  --output-prefix "$STUDY/causal_benchmark_seed13"
cat "$STUDY/causal_benchmark_seed13.results.summary.json"
```

安全动作支持 `guide:A/B`（乘客运动同时派车）、`dispatch:A/B`（仅车辆）、`wait`、`verify:A/B`（耗时后依据结果拆 belief）、`board`（完整登乘持续）等。模型支持有限世界和整数秒时窗，完全不知道的真实物理时窗返回 `UNKNOWN_INPUT`。该测试的解集一致性在有限状态上有效，不是 CAV21/AAAI23/ICRA24 的发表方法复现，且新 interval primitive 尚无速度优势。

## E. CARLA 0.9.15：控制实验下一轮

此处使用现有可执行器（未改变 CARLA 物理模型），目标是生成独立试验而不是自动构造真实路缘验证。先确认 CARLA server 正在运行、客户端 API 版本适配及可独占 Town 地图。**不要把示例文件当作已审核 PUDO。**

```bash
export CLEAR_DIR=/path/to/existing/successful/clear_A
python scripts/handoff_study.py carla-plan \
  --clear-dir "$CLEAR_DIR" \
  --output-dir "$STUDY/carla_v2" \
  --max-controls 6 --redirect-at-s 8 --extended-controls

# 人工检查 $STUDY/carla_v2/run_matched_carla_v13.sh
# 确保 CARLA server、map、spawn、seed、blocker 尺度/位置均受控后：
# bash "$STUDY/carla_v2/run_matched_carla_v13.sh"

python scripts/handoff_study.py carla-fiveway \
  --study "$STUDY/carla_v2" \
  --output "$STUDY/carla_fiveway_v2.json"
```

每次保存 A/B 的坐标系、stop pose、车辆速度低于阈值的连续时间、真实 blocker 尺度/位置/影响路线、碰撞回调、重定向生效时钟、完整五组对照；新 seed 和 spawn 不能仅复用同一站点的不同时间片充当独立样本。需要独立合法路缘与登乘接口审核才能升级 H1。

## F. 新增结果与回传

`review-pack` 旧接口只打包其固定清单，不会自动包含新 `factorial_science_v2.json` 和 `physical_review_queue_v2.jsonl`，因此本轮建议使用下面独立小 ZIP，避免误以为新结果已经被封装：

```bash
cd "$STUDY"
zip -j handoff_science_v2_review.zip \
  factorial_science_v2.json \
  physical_review_queue_v2.jsonl \
  causal_exact_smoke.summary.json \
  causal_benchmark_seed13.results.summary.json \
  geometry_progress.summary.json \
  controlled_factorial.summary.json

# 如果需要我们按实际路径和物理审核逐例复核，再单独上传下列文件；
# 不必上传大型图或 nuPlan DB：
#   geometry_progress.jsonl
#   controlled_factorial.jsonl
#   handoff_science_v2_review.zip
```

## G. 后续主实验的 GO/NO-GO

- 先证伪独立接口：若多数候选不是合法、可停、可部署 PUDO，不是“真实恢复失败”，先改善数据与采集。
- 控制动作空间：保留可靠固定 B、Verify-then-Guide、所有中途改向与第三点、车辆等待/变更、已验证援助；不许删掉合理救济选项造难题。
- H4′：三阶交互为 0 并不否定状态依赖安全动作；报告变量单因素与二阶/三阶效应并给负对照。
- H5：要求独立通用 exact 的**完整动作集合** 0 不一致和明确效率瓶颈，再去实现领域前沿；如与强 shield 相同安全却既不更快也不省 motion calls，不宣称新方法优势。
- 所有超时输出 UNKNOWN；真实物理字段为空绝不记成 infeasible。证据校准及事件实际发生率需要独立采样。
