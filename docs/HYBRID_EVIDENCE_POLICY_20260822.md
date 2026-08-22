# CapPlan / AbilityBench-AV：四城 Hybrid Evidence Policy（2026-08-22）

## 1. 目标与边界

本策略解决的不是 nuPlan traffic scene 构建失败，而是 passenger-complete 服务层中真实公开数据对物理/法规/入口字段覆盖不足的问题。

保留三种 source policy：

- `bootstrap`：用于几何/拓扑 bring-up，缺失字段 fail-closed。
- `paper`：只接受可审计的真实/权威 evidence，绝不使用 simulated 字段支撑“真实城市 ground truth”结论。
- `hybrid`：真实 nuPlan traffic scene + 真实 GIS/OSM/DEM/georeference/topology + 真实 evidence 优先 + 对仍缺失的 typed resource / dynamic service context 做可复现、物理合理、逐字段带 provenance 的 simulated overlay。

`hybrid` 的设计依据是论文附录：typed-resource labels 可由 measured **or simulated** values 构成；同时论文又明确要求 accessibility missing fields 不能默认判 feasible。因此 hybrid 不是“把空值当 PASS”，而是建立独立的 benchmark scenario truth，并明确禁止把 simulated 值写成 paper city ground truth。

## 2. 当前 reports 的真实诊断

上传的 reports 显示：

| city | unique PUDO sites | NEW_EVIDENCE_REQUIRED | 当前主要缺口 |
|---|---:|---:|---|
| Boston | 272 | 272 | curb height / sidewalk width / env clearance / location-specific legality / intended entrance provenance；curb ramp 已覆盖 266/272 |
| Pittsburgh | 127 | 127 | 全部核心 PUDO audit physical/legal/entrance 字段 |
| Las Vegas | 137 | 137 | 全部核心 PUDO audit physical/legal/entrance 字段 |
| Singapore | 582 | 582 | LTA topology 很多，但核心 physical/legal/entrance audit 字段仍未形成完整 provenance |
| total | 1118 | 1118 | `PUDO_AUDIT_MACHINE_TRIAGE=PASS` 只表示分类程序 PASS，不表示 evidence complete |

因此 `render-audit-packets` 没有 visual rows、`review-source-complete-audits` 没有 source-complete rows 都是正确的 fail-closed 行为。

另外，`paper_site_catalog.*.json` 暴露了一个独立问题：5 m physical-site clustering 下 cross-split site rate 很高：Boston 61.40%、Pittsburgh 67.72%、Vegas 37.23%、Singapore 63.23%。旧的 `test > val > train` whole-episode exclusion manifest 会分别排掉 Boston 1000/1000 train、Pittsburgh 1000/1000 train、Vegas 997/1000 train、Singapore 989/1000 train。因此不能把这个 manifest 直接作为主训练集过滤器；本版新增 `site-disjoint-eval`，只生成严格的 secondary evaluation allowlist，不清空主 train split。

## 3. 官方/权威数据到底能提供什么

### Boston

官方 City of Boston Infrastructure OpenData：

- Sidewalk Inventory：官方 polygon inventory，字段明确存在 `SWK_WIDTH`、`SWK_SLOPE`、`MATERIAL`。
- Ramp Inventory：字段存在 `APRON_SL`、`LANDING_SL`、`REVEAL`、`SWK_WIDTH`、`SWK_MATL`。
- Sidewalk Centerline / Curbs：可作为 pedestrian topology / curb geometry。
- PWD Cartegraph（若 endpoint 可下载成功）：代码优先使用带 `<field>_unit` 的 Width/Slope/ClearWidth/Rise 等记录，只有 source 自带单位时才换算。

注意：OpenData MapServer 的空间参考单位为 feet，并不自动证明每个业务属性字段的单位；因此本版仍不把 `REVEAL` 无条件当 `curb_height_m`，也不把 sidewalk width 偷换成 ramp/lift deployment clear area。

### Pittsburgh

WPRDC 可稳定获得：

- `Sidewalks and Steps SHP`：真实 pedestrian geometry/topology，但 WPRDC 页面本身不是逐点 measured width/curb-height inventory。
- City Steps：楼梯/阶梯 truth，可用于不可达/step-free negative evidence。
- PPA Parking Meters and Payment Points：meter/payment location、zone、rates/limits；不能等价成“一般 AV passenger pickup/dropoff 合法”。
- DOMI Street Closures：适合 dynamic blockage / temporary availability overlay。
- PASDA Allegheny Address Points：只能作为 entrance proxy，不能自动成为 intended entrance。

当前公开目录搜索未找到可直接覆盖全城的 measured curb-height / deployment-clearance / sidewalk-width ground-truth layer；这不证明不存在，只表示当前公开、可机器消费的数据不足以作为这些字段的 paper truth。

### Las Vegas / nuPlan Strip

本版把 Vegas 基础 GIS 优先锚定到 Clark County public ArcGIS：

- `PW/pwEditLayers/FeatureServer/1`：`pwRamps`。
- `PW/pwEditLayers/FeatureServer/2`：`pwConcrete`。
- `AdminServ/Strip_Sidewalk_LinearFeet_Line/FeatureServer/0`：Strip sidewalk linear geometry。

这些源可增强 curb/pedestrian geometry/topology，但未发现公开 schema 能直接给出本论文所需的 curb height、deployment clear area、AV passenger-loading legality 或 intended trip entrance。

原 City of Las Vegas Taxi Zones 不再作为 Vegas/Strip 的默认 legality truth，只保留为 jurisdiction-limited auxiliary candidate。

### Singapore

LTA DataMall Whole Island 公开：

- Footpath：designated pedestrian path line。
- Kerbline：road edge line。
- Passenger Pickup Bay：官方定义为道路侧“designated for vehicles to pick up or drop off passengers”的区域；因此它可以恢复 **该 feature 的 passenger loading semantics**。
- Train Station Exit Point：真实 station exit point，可作为高质量 entrance candidate。
- Taxi Stand 等：只保留对应 service-class semantics，不泛化成 general passenger loading。

LTA public catalogue 的这些层没有声明 site-level curb height、sidewalk width、ramp deployment clear area，因此不能从“有几十万条 GIS”推出这些物理量已经测量。

### DEM

- 美国三城：USGS 3DEP 1 m product 是 **bare-earth DEM**，适合 terrain/slope prior 和 georeference sanity check；如果以后要做更真实的 curb/sidewalk local reconstruction，应优先下载 USGS source LiDAR point clouds，而不是继续换另一个 1 m raster DEM。
- Singapore：Copernicus GLO-30 是 30 m **DSM**，包含 buildings / infrastructure / vegetation。它适合宏观 elevation context，不适合直接恢复 sidewalk width、curb height、deployment clearance。

结论：DEM 文件本身不是本轮缺口的主要原因；即便完全正确下载，也不会给 `legal_basis`、`intended_entrance`、sidewalk width 或 passenger-loading clear area。

## 4. Hybrid evidence 的字段语义

### 4.1 observed / derived / simulated 三层 provenance

每个 hybrid 字段写入 `field_provenance[field]`：

- `kind=observed`：权威 source / 已审核 source relation / 真实人工 observation。
- `kind=derived`：从真实 geometry/topology/DEM/OSM 的可复现几何计算得到。
- `kind=simulated`：仅在真实 evidence 缺失时，用 deterministic physically-plausible scenario prior 生成。

优先级永远是 `observed > derived > simulated`；hybrid overlay 不覆盖已有 observed/audited value。

只要某条 anchor/edge 有 simulated publication-critical field：

- `paper_claim_allowed=false`
- `paper_evidence_complete=false`
- `paper_eligible=false`

因此 `hybrid` benchmark 不会污染 `paper` branch。

### 4.2 `legal_stop` 的修正

旧 bootstrap 里的 `legal_stop=False` 很多是“未知时 fail-closed”，不是 source 证明“此处禁止”。本版只有存在明确 legality provenance 的 False 才作为 observed prohibition；无 provenance 的 False 在进入 hybrid overlay 时恢复为 UNKNOWN，再由 scenario truth 赋值：

- `SIMULATED_BENCHMARK_LOADING_PERMISSION`
- `SIMULATED_BENCHMARK_LOADING_PROHIBITION`

这两个值只表示 benchmark scenario service permission，不表示 Boston/Pittsburgh/Las Vegas/Singapore 的真实市政法规。

### 4.3 `deployment_clearance_m` 的修正

环境侧与车辆侧必须分开：

- PUDO：`deployment_clearance_m` = **available environment clear space**。
- Vehicle interface：`c_deploy` = **required deployment envelope**。
- Interface feasibility：`available >= required`。

旧逻辑把两者做 `min()` 会破坏 violation margin；本版 transition generator 已修复，同时保留 legacy schema backward compatibility。

美国 hybrid accessible-loading 的 clear-space lower bound 以 ADA/PROWAG passenger-loading access aisle 60 in = 1.525 m 为设计参考；实际 simulated positive range 默认 1.55–2.25 m。它是 plausibility prior，不是对具体 mapped curb 的测量。

### 4.4 intended entrance

`intended_entrance` 本质上是 request/trip semantic，不是一个 municipal curb layer 应该提供的静态属性。真实入口/站口/地址点可以提供 candidate anchor；最终 intended origin/destination 应来自 request/app/geocoder 或 benchmark request generation。

`paper` branch 仍要求 trusted entrance + audited linkage；`hybrid` branch 允许基于真实 graph/location 生成 request-specific OD truth，并明确记录 source 为 hybrid request generation，而不是把 nearest entrance 冒充真实旅客意图。

### 4.5 accessibility edge overlay

只补 PUDO 不够。论文 verifier 对 access/egress 还需要 path edge 的 width / slope / cross-slope / surface / curb-ramp / step-free / availability 等字段；缺失仍会 fail-closed。

`build_hybrid_accessibility_overlay.py` 保留真实 nodes/topology，只对 missing edge attributes 补 deterministic scenarios，并逐字段 provenance。它同时生成 narrow/steep/no-ramp 等 physically plausible negatives，以支撑 T4 monotonic counterfactual 与 T5 failure certificate。

## 5. 新增/修改代码

主要修改：

- `capplan/data/schemas.py`：edge metadata + PUDO field provenance / truth mode / hybrid eligibility。
- `capplan/planning/transition_generator.py`：environment available clearance 与 vehicle required deployment envelope 分离。
- `scripts/build_hybrid_pudo_evidence.py`：PUDO observed/derived/simulated overlay。
- `scripts/build_hybrid_accessibility_overlay.py`：accessibility-edge missing-field overlay。
- `scripts/build_site_disjoint_eval_allowlists.py`：严格 secondary site-disjoint evaluation allowlists；不 purge train。
- `scripts/build_dataset.py`：新增 `source_policy=hybrid`、hybrid quality gate、publication/paper guard。
- `scripts/prepare_abilitybench_external.py`：hybrid graph/PUDO/service/dataset 独立目录，避免覆盖 paper/bootstrap 输出。
- `capplan/data/external_validation.py` / `validate_external_sources.py` / `check_abilitybench_pipeline.py`：识别 hybrid policy，并明确 `benchmark_ready != publication_ready`。
- `scripts/fetch_recommended_public_sources.py`：四城 refresh，加入 Clark County sources，LTA/TrainStationExit source recovery，保留 City-of-Las-Vegas taxi layer 为 auxiliary。
- `scripts/normalize_accessibility_evidence.py`：新增 Clark County normalizers；继续禁止把 topology/geometry 强行解释成 measured physical quantity。
- `scripts/build_abilitybench_data0_20260817.sh`：新增 `site-disjoint-eval`、`hybrid-evidence`、`hybrid-build`、`hybrid-from-existing`。

## 6. 从你当前状态继续：推荐命令

你已经有 6000-scene paper-scale heavy candidate corpus、graph/PUDO/site catalog，不要重做重型阶段。

```bash
cd /home/senzeyu2/code/CapPlan

export CAP_HOME=/home/senzeyu2/code/CapPlan
export CAP_DATA=/data0/senzeyu2/dataset/CapPlan/data
export DATA_ROOT="$CAP_DATA"
export CONFIG="$CAP_HOME/configs/abilitybench_nuplan_real_data0.yaml"
export EXT="$CAP_DATA/external"
export REPORTS="$EXT/reports"

export NUPLAN_DATA_ROOT="$CAP_DATA/nuplan"
export NUPLAN_MAPS_ROOT="$CAP_DATA/nuplan/maps"
export NUPLAN_MAP_VERSION=nuplan-maps-v1.0

export CAP_HYBRID_SEED=20260822
export CAP_HYBRID_MIN_PUDOS=2
export CAP_SITE_DISJOINT_MIN_SITES=2

# 0) 记录当前真实 audit baseline
bash scripts/build_abilitybench_data0_20260817.sh audit-status

# 1) 自动刷新四城能恢复的真实 public source
bash scripts/build_abilitybench_data0_20260817.sh refresh-audit-public-sources

# 2) 重新做 source semantic recovery + audit prefill/classify/triage/status
bash scripts/build_abilitybench_data0_20260817.sh recover-audit-evidence

# 3) 生成 secondary strict site-disjoint eval allowlists，不删除主 train
bash scripts/build_abilitybench_data0_20260817.sh site-disjoint-eval

# 4) 在真实 graph/PUDO 上构建逐字段 provenance 的 hybrid overlay
bash scripts/build_abilitybench_data0_20260817.sh hybrid-evidence

# 5) 构建独立 hybrid train/val/test，不覆盖现有 paper/bootstrap dataset
bash scripts/build_abilitybench_data0_20260817.sh hybrid-build
```

也可以一条命令：

```bash
bash scripts/build_abilitybench_data0_20260817.sh hybrid-from-existing
```

期望关键输出：

```text
$REPORTS/hybrid_pudo.{train,val,test}.{city}.json
$REPORTS/hybrid_graph.{train,val,test}.{city}.json
$REPORTS/site_disjoint/{city}.json
$REPORTS/site_disjoint/all.{train,val,test}.site_disjoint.txt

$CAP_DATA/outputs/prepared/{split}/pudo_hybrid_evidence.jsonl
$CAP_DATA/outputs/prepared/{split}/accessibility_graphs_hybrid/
$CAP_DATA/outputs/datasets/abilitybench_av_hybrid_{train,val,test}/
```

## 7. 从头重建：推荐锚定顺序

```bash
# A. immutable/raw substrate
bash scripts/build_abilitybench_data0_20260817.sh inspect-nuplan
bash scripts/build_abilitybench_data0_20260817.sh index-nuplan-full
bash scripts/build_abilitybench_data0_20260817.sh bootstrap-preflight

# B. paper-scale heavy candidates，仍然是 1000/250/250 per city
bash scripts/build_abilitybench_data0_20260817.sh bootstrap-candidates-paper-scale-staged

# C. physical site and real-source recovery
bash scripts/build_abilitybench_data0_20260817.sh site-catalogs
bash scripts/build_abilitybench_data0_20260817.sh refresh-audit-public-sources
bash scripts/build_abilitybench_data0_20260817.sh recover-audit-evidence
bash scripts/build_abilitybench_data0_20260817.sh site-disjoint-eval

# D. benchmark-ready hybrid truth
bash scripts/build_abilitybench_data0_20260817.sh hybrid-evidence
bash scripts/build_abilitybench_data0_20260817.sh hybrid-build

# E. 保留真实 paper audit branch，不让 hybrid 替代 paper truth
export CAP_AUDIT_RENDER_SCOPE=auto
export CAP_AUDIT_RENDER_MAX_ROWS=100
bash scripts/build_abilitybench_data0_20260817.sh render-audit-packets
bash scripts/build_abilitybench_data0_20260817.sh audit-review-bundle
```

只有真实 paper evidence 足够后，才运行原来的 paper pipeline：

```bash
bash scripts/build_abilitybench_data0_20260817.sh build-provenance
bash scripts/build_abilitybench_data0_20260817.sh paper-preflight
bash scripts/build_abilitybench_data0_20260817.sh rebuild-paper-evidence-full-staged
bash scripts/build_abilitybench_data0_20260817.sh select-paper-allowlists
bash scripts/build_abilitybench_data0_20260817.sh qa-snapshot
bash scripts/build_abilitybench_data0_20260817.sh paper-build-allowlisted
bash scripts/build_abilitybench_data0_20260817.sh merge-all
bash scripts/build_abilitybench_data0_20260817.sh qa-strict
bash scripts/build_abilitybench_data0_20260817.sh qa-bundle
```

## 8. 论文中应该如何表述

Hybrid benchmark 可以支持这样的结论：

> traffic scenes and geometric/topological context are anchored to nuPlan and public geospatial sources; incomplete passenger-service attributes are completed by deterministic, physically constrained simulated counterfactuals with per-field provenance, and are used only as benchmark scenario truth.

不要写：

> all curb heights / clearances / legal stopping labels are measured ground truth from the four cities.

如果最终需要做“真实四城部署可行性”结论，就必须继续 paper/audited branch，补真实照片、LiDAR/perception、现场测量或明确法规 source；hybrid 不能代替这类 claim。

## 9. 外部源

- Boston Infrastructure/OpenData: https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer
- WPRDC Sidewalk-to-Street Walkability / Sidewalks and Steps: https://data.wprdc.org/dataset/sidewalk-to-street-walkability-ratio
- WPRDC Pittsburgh Parking Meters and Payment Points: https://data.wprdc.org/dataset/pittsburgh-parking-meters-and-payment-points
- Clark County PW FeatureServer: https://maps.clarkcountynv.gov/arcgis/rest/services/PW/pwEditLayers/FeatureServer
- Clark County AdminServ: https://maps.clarkcountynv.gov/arcgis/rest/services/AdminServ
- LTA Static Datasets: https://datamall.lta.gov.sg/content/datamall/en/static-data.html
- USGS 3DEP: https://www.usgs.gov/3d-elevation-program
- Copernicus DEM GLO-30: https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM
- U.S. Access Board ADA Passenger Loading Zone §503: https://www.access-board.gov/ada/chapter/ch05/
- Singapore BCA Code on Accessibility 2025: https://www1.bca.gov.sg/safety-and-standards/accessibility/code-on-accessibility-in-the-built-environment/
