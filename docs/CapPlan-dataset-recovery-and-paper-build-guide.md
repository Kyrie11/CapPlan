# CapPlan / AbilityBench-AV：从当前状态恢复到论文可用 benchmark 的完整构建指南

生成日期：2026-08-12  
适用代码：本次修复后的 `CapPlan_repaired_2026-08-12.zip`

## 1. 先明确“构建成功”与“论文可用”的区别

本文的核心并不是把 nuPlan scene 转成普通规划样本，而是验证 **passenger-complete planning**：

`access -> wait -> board -> ride -> alight -> egress`

因此最终每个 benchmark episode 必须至少有：

1. nuPlan 真实 traffic scene、ego/agent history、route/road context；
2. 真实或可审计的 origin/destination entrance；
3. 可路由 pedestrian/curb accessibility graph；
4. PUDO candidates，且主结果必须有 evidence-complete / paper-eligible PUDO；
5. vehicle interface specification；
6. capability contracts；
7. 同 scene、同 O/D、同 request time 的 capability counterfactual group；
8. transition validity、typed-resource、skeleton、failure-certificate labels；
9. provenance/license/hash；
10. train/val/test 保留 nuPlan DB set 的原始 split，不二次随机切分。

本流程严格区分两种 policy：

- `bootstrap`：用于验证真实 nuPlan + 外部 GIS 主链路是否可运行。允许 unknown，但不能把 unknown 当 feasible。**不能用于论文主结果**。
- `paper`：要求足以支撑论文 T1–T5 的可审计证据、vehicle interface、PUDO eligibility、T4 counterfactual coverage 和 provenance。

---

## 2. 本次代码修复的主要问题

本次共修改 24 个源码/配置/文档/测试文件，并新增针对你当前日志的回归测试。最终：

```text
104 passed
```

关键修复：

### 2.1 georeference alignment 假 FAIL

旧版 `validate_georeference_alignment.py` 从 GeoPackage `gpkg_contents` 读取 layer bounds 后，直接把经纬度 bounds 当成 nuPlan local projected coordinates，因此四城都会出现 map/AOI overlap = 0。

修复后：读取 GeoPackage layer SRS，将 extent 正确变换到 configured local CRS，再做 overlap。

### 2.2 nuPlan map CRS 的假 PASS

旧版 `inspect_nuplan_map_crs.py` 递归搜到任意 `map.gpkg` 就 PASS，但 nuPlan devkit 真正加载地图时需要：

```text
data/nuplan/maps/
  nuplan-maps-v1.0.json
  us-ma-boston/9.12.1817/map.gpkg
  us-pa-pittsburgh-hazelwood/9.17.1937/map.gpkg
  us-nv-las-vegas-strip/9.15.1915/map.gpkg
  sg-one-north/9.17.1964/map.gpkg
```

不能多一层：

```text
maps/nuplan-maps-v1.0/us-ma-boston/...
```

修复后 CRS inspector 会检查 manifest 和 exact devkit layout，避免“检查 PASS、实际 extract 崩溃”。

### 2.3 nuPlan adapter property 访问异常

旧 `safe_call()` 通过 `hasattr(scenario, "map_api")` 探测属性；但 `map_api` 是 property，`hasattr` 本身就会触发地图加载并抛 `BlobStoreKeyNotFound`。已改为安全 `getattr`/exception handling，并在真实 nuPlan adapter 入口做 map package readiness 检查。

### 2.4 OSM pedestrian topology 假阴性

旧 `external_validation.py` 只看 GeoJSON 前 500 个 feature。若前 500 条主要是 crossing/kerb/entrance 点，而 footway/sidewalk line 出现在后面，就把实际有 pedestrian topology 的城市误判为 FAIL。

修复后对 OSM 进行完整、按 geometry/tag 语义的扫描，不再依赖“前 500 条”。

### 2.5 Singapore LTA `.geojson` 实际写成 JSONL

`lta_footpath` / `lta_kerbline` 之前没有进入 GeoJSON output profile 分支，结果是逐行 JSON 写入 `.geojson`，后续 parser 报 `Extra data`。已修复为真正的 GeoJSON FeatureCollection。

### 2.6 Singapore DEM 采样找不到候选点

旧 `sample_raster_dem.py` 只找 `{city}_sidewalks.json`，而 OSM 正常产物是 `{city}_sidewalks.geojson`；同时 malformed LTA layer 也不能读取。已同时修复 `.geojson` discovery 和 LTA 输出。

### 2.7 DEM point sampling 的假阳性 PASS

你当前 Vegas 的 AOI tile coverage 只有约 90.84%，但已有 24 个候选点恰好都落在有 DEM 的区域，因此 point sampler 给出 100% PASS。这个 PASS 不能证明整个 configured AOI 已准备好。

修复后 `sample_raster_dem.py` 支持并推荐强制：

```text
--tile_validation_report <a PASS validate_dem_tiles report>
```

如果 tile report FAIL，采样不再掩盖缺口。

### 2.8 Pittsburgh payment points 空经纬度

你的文件共有 2217 行，2021 行有效 WGS84，valid fraction = 0.911592。空坐标包括 virtual terminal / off-street 等记录。

正确处理是：

- 坐标质量检查阈值设成 `0.90`，同时要求至少 1000 个有效点；
- normalize 时 `--skip_invalid`；
- 不允许为缺坐标记录编造坐标。

自动 fetch pipeline 也已改为这样处理。

### 2.9 ArcGIS/PASDA 超长 GET

某些 ArcGIS layer 的 objectIds 过多，GET query URL 会过长导致 404/网关失败。修复后 downloader 对长 query 自动改 POST。

### 2.10 T4 counterfactual 原结构不成立

旧 `build_service_layer.py` 为同一 episode 的不同 profile 随机生成了不同 O/D；但论文 T4 要求 **same traffic scene + same O/D + capability changed**。同时外部 profile 情况下没有可靠输出 pair labels。

修复后每个 episode 固定一个 base O/D/time，生成 1 个 base + 7 个 strict variants，覆盖：

```text
access_distance
step_free
min_width
ramp_lift
door_side_clearance
ride_motion
confidence
```

并生成 `counterfactual_pairs.jsonl`，记录：

```text
counterfactual_axis
counterfactual_group_id
weak_profile_id
strict_profile_id
```

schema validator 会检查 pair 的 O/D/time 一致；paper quality gate 会 **逐 episode** 检查七轴覆盖。

### 2.11 T2 ride motion label 来源过弱

旧实现的 ride motion label 含 index-based heuristic，不能作为论文 T2 的可信 trajectory evidence。

修复后从 nuPlan ego history 计算：

- peak absolute acceleration；
- jerk；
- lateral acceleration（由 speed × heading rate）；
- benchmark motion exposure score。

source 标成 `nuplan_ego_history`。

注意：这个 score 是本 benchmark 的可复现 motion-exposure surrogate，**不是 ISO-2631 frequency-weighted metric**。若论文正文要声称 ISO-2631，请另实现标准频率加权；否则修改 claim，避免过度宣称。

### 2.12 Paper vehicle interface 不能靠 dataclass 默认值

`configs/fleet.abilitybench.example.jsonl` 只允许 bootstrap。paper-mode 现在会拒绝 `example/synthetic/proxy/default/unknown` source，并要求这些核心字段在原始 fleet row 中显式出现：

```text
door_side
ramp
lift
low_floor
door_width_m
deployment_clearance_m
notification_modes
dwell_time_s
kneeling
```

不能因为 dataclass 有默认值就当成“已核验车辆规格”。

### 2.13 PUDO paper eligibility 假阳性

旧 dataset materialization 会丢失 `build_pudo_evidence.py` 已经计算的 `paper_evidence_complete/paper_eligible`，后面又可能仅凭几个非空数值重新推断 eligibility。

修复后：

- 明确保留 `paper_evidence_complete` / `paper_eligible` / `evidence_status` / notes；
- paper-mode 只接受 `build_pudo_evidence.py` 的显式 evidence audit；
- 需要 physical/interface evidence + pedestrian binding + independent legal evidence + trustworthy source；
- candidate layer 本身不能推出 `legal_stop=true`。

### 2.14 train/val/test split 语义错误

旧 `build_dataset.py` 即使输入是 nuPlan `val`，仍会在内部重新随机分 70/15/15；`merge_datasets.py` 还会 fallback 复制 split membership。这会破坏官方 DB-set split，产生潜在 leakage。

修复后：

- real nuPlan `--split train` 的 episode 只写 `train_episodes.txt`；
- `val` 只写 val；
- `test` 只写 test；
- merge 只合并上游 split；
- 明确检查 split overlap；
- 不再 fabricating missing split membership。

---

## 3. 你当前所有 PASS/FAIL 应如何解释

### 3.1 nuPlan DB city inspection：PASS，是真 PASS，但范围有限

`train/val/test` 的 DB-city 检查只能证明 DB files 可解析、四城映射符合配置。它不能证明 maps package 可被 devkit 加载，也不能证明 GIS alignment。

建议换修复代码后再跑一次并冻结 report。

### 3.2 旧 `inspect_nuplan_map_crs.py`：PASS 属于假阳性

它找到 nested `map.gpkg` 即 PASS，但实际 `extract_nuplan_scenes.py` 报：

```text
.../data/nuplan/maps/nuplan-maps-v1.0.json not found
```

必须先修 maps package hierarchy，再用新脚本重跑。

### 3.3 四个 OSM PBF prepare：基本是真 PASS

四个 PBF 能被正常裁剪并生成有 pedestrian features 的 GeoJSON，这一步可复用；重新跑主要是为了让新报告/新 provenance 与当前代码版本一致。

### 3.4 `georeference_spatial_alignment.json`：旧 FAIL 主要是代码 bug

四城都显示：

```text
AOI: projected UTM meters
OSM: projected UTM meters
map_local_bounds: raw longitude/latitude degrees
```

这是明显的 CRS unit mismatch。修复后应重新生成，不能继续使用旧 FAIL report。

### 3.5 Pittsburgh Current Payment Points inspection：FAIL 是合理阈值失败，不是整份源不可用

实际：

```text
rows              = 2217
valid_wgs84_rows  = 2021
valid_fraction    = 0.911592...
old threshold     = 0.95
```

因此 `0.95` FAIL 正确；但这不表示应该弃用整个 source。它只作为 PUDO **candidate cue**，应 skip invalid 后保留 2021 个有效点。

### 3.6 Pittsburgh normalize parking meter：旧 FAIL 是脚本 fail-fast 行为

第一条 blank coordinate 直接终止。修复后 `--skip_invalid`。

### 3.7 Pittsburgh street closure：normalize PASS，但不要误用作 2021 scene truth

它只说明当前 CSV 被成功解析。除非 closure 有与 nuPlan scene timestamp 对齐的历史记录，否则不要把“当前下载的 closure”应用到 2021/历史 scene 当 dynamic ground truth。

### 3.8 Pittsburgh address points：PASS 只是 proxy layer

address point 可用于 bootstrap entrance candidate，不应冒充 physical entrance ground truth。paper-mode 的 entrance 应是独立观测/权威入口几何或 audit。

### 3.9 Generic GPKG 指令：不是强制步骤

只有当你额外取得**对论文资源字段真正有用的权威 GIS GPKG** 时才运行：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input xxx.gpkg \
  --layer layer_name \
  --output out.geojson \
  --profile generic_city_gis \
  --source "SOURCE NAME"
```

`SOURCE NAME` 写可审计的人类可读来源，例如：

```text
City of Pittsburgh Department of Mobility and Infrastructure - <official layer name>
```

不要为了“补一步”随便找 GPKG。`generic_city_gis` 也不会自动使数据变成 paper ground truth。

### 3.10 Boston GIS normalization：解析 PASS，不能等价为 paper truth PASS

Boston sidewalk/ramp/centerline/curb layers可以作为高质量 authoritative/candidate evidence，但还需：

- 检查字段单位和含义；
- 做 spatial alignment；
- 对用于 boarding/alighting 的 curb/interface 字段建立独立 legality 与 physical provenance；
- main-result PUDO 仍需通过 `paper_eligible` gate。

### 3.11 Boston DEM：真 PASS

你的 report：

```text
coverage = 1.0000
tiles    = 67
status   = PASS
```

可复用。

### 3.12 Pittsburgh DEM：真 PASS

你的 report：

```text
coverage = 0.993456276...
tiles    = 34
status   = PASS
```

达到 0.99，可以复用。

### 3.13 Vegas DEM：真 FAIL

你的 report：

```text
coverage = 0.908387864...
tiles    = 107
status   = FAIL
```

不是脚本 bug。缺口约 9.16%。

而 `sample_raster_dem.py` 只在 24 个已有 candidate points 上得到 coverage=1.0，因此旧 sampling PASS 是 **“点采样成功”的真 PASS，但作为“整个 configured AOI DEM 已就绪”的假阳性**。

### 3.14 Singapore DEM：当前文件覆盖不足

当前 report：

```text
coverage = 0.690065437...
status   = FAIL
```

你手工裁剪的是：

```text
103.7600 .. 103.8100
1.2700   .. 1.3300
```

配置 AOI 是：

```text
103.75 .. 103.82
1.27   .. 1.33
```

应改为下载覆盖完整 One-North AOI 的 GLO-30 grid cell `N01_E103`。

### 3.15 Singapore `no WGS84 points`：代码 bug + malformed LTA 的链式后果

修复后重新生成 LTA Footpath/Kerbline 和 OSM discovery，再采样；同时必须先让 `dem_tiles_singapore.json` PASS。

### 3.16 `external.bootstrap.json`：Vegas/Singapore 的 pedestrian topology FAIL 大概率是假阴性

旧 validator 前 500 feature 抽样 + Singapore malformed GeoJSON 会造成：

```text
vegas: pedestrian_topology blocker
singapore: pedestrian_topology blocker
```

新代码重跑后再判断。paper warnings（缺 audited curb/legality/entrance/provenance 等）则是**真实且应该保留的警告**。

### 3.17 `prepare_abilitybench_external`：是真环境/文件布局错误

直接原因：

```text
/data/nuplan/maps/nuplan-maps-v1.0.json missing
```

这是 maps package hierarchy/manifest 问题，不是 nuPlan DB scene 本身坏掉。

---

## 4. 第一步：安装本次修复代码

建议先备份当前 repo：

```bash
cd /home/senzeyu2/code
cp -a CapPlan CapPlan.before_2026_08_12_fix
```

最简单做法是解压完整修复包覆盖代码目录，**不要覆盖你已有的 `data/` 外部大文件**。也可以仅应用 changed-files zip 或 patch。

然后：

```bash
cd /home/senzeyu2/code/CapPlan
pytest -q
```

预期：

```text
104 passed
```

---

## 5. 第二步：修复 nuPlan map package

官方 devkit 的 `NUPLAN_MAPS_ROOT` 应指向 map root；devkit 用 `map_version=nuplan-maps-v1.0` 查找 `${NUPLAN_MAPS_ROOT}/nuplan-maps-v1.0.json`。

先检查：

```bash
cd /home/senzeyu2/code/CapPlan
find data/nuplan/maps -maxdepth 4 \
  \( -name 'nuplan-maps-v1.0.json' -o -name 'map.gpkg' \) -print | sort
```

目标结构：

```text
data/nuplan/maps/nuplan-maps-v1.0.json
data/nuplan/maps/us-ma-boston/9.12.1817/map.gpkg
data/nuplan/maps/us-pa-pittsburgh-hazelwood/9.17.1937/map.gpkg
data/nuplan/maps/us-nv-las-vegas-strip/9.15.1915/map.gpkg
data/nuplan/maps/sg-one-north/9.17.1964/map.gpkg
```

如果你当前是：

```text
data/nuplan/maps/nuplan-maps-v1.0/<map-name>/<version>/map.gpkg
```

并且 nested 目录内确实还有官方 manifest，可按下面移动；**不要手写假的 manifest**：

```bash
MAPS=data/nuplan/maps
NESTED="$MAPS/nuplan-maps-v1.0"

test -f "$NESTED/nuplan-maps-v1.0.json" && \
  cp -a "$NESTED/nuplan-maps-v1.0.json" "$MAPS/"

for loc in \
  us-ma-boston \
  us-pa-pittsburgh-hazelwood \
  us-nv-las-vegas-strip \
  sg-one-north; do
  if [ -d "$NESTED/$loc" ]; then
    mkdir -p "$MAPS/$loc"
    rsync -a "$NESTED/$loc/" "$MAPS/$loc/"
  fi
done
```

如果 nested 目录里根本没有 `nuplan-maps-v1.0.json`，应重新下载/解压官方 nuPlan maps package；不要从网上随便找一个 JSON，也不要自己伪造。

然后执行：

```bash
python scripts/inspect_nuplan_map_crs.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston+pittsburgh+vegas+singapore \
  --output_dir data/external/georeference
```

必须先 PASS，再做 scene extract。

---

## 6. 第三步：重跑 DB city + georeference + OSM alignment

```bash
for split in train val test; do
  python scripts/inspect_nuplan_db_cities.py \
    --config configs/abilitybench_nuplan_real.yaml \
    --split "$split" \
    --fail_on_unknown \
    --report_json "data/external/reports/nuplan_db_cities.${split}.json"
done
```

OSM PBF 可以复用；若要冻结与修复版本一致的新产物，按正确 bbox 重跑：

```bash
export BOS_PBF=data/external/raw/osm_pbf/massachusetts-latest.osm.pbf
export PIT_PBF=data/external/raw/osm_pbf/pennsylvania-latest.osm.pbf
export VEG_PBF=data/external/raw/osm_pbf/nevada-latest.osm.pbf
export SG_PBF=data/external/raw/osm_pbf/malaysia-singapore-brunei-latest.osm.pbf

python scripts/prepare_osm_from_pbf.py --input_pbf "$BOS_PBF" --bbox 42.30,-71.15,42.42,-70.98 --output data/external/normalized/osm/boston_sidewalks.geojson --overwrite
python scripts/prepare_osm_from_pbf.py --input_pbf "$PIT_PBF" --bbox 40.38,-80.04,40.48,-79.88 --output data/external/normalized/osm/pittsburgh_sidewalks.geojson --overwrite
python scripts/prepare_osm_from_pbf.py --input_pbf "$VEG_PBF" --bbox 36.07,-115.23,36.20,-115.10 --output data/external/normalized/osm/vegas_sidewalks.geojson --overwrite
python scripts/prepare_osm_from_pbf.py --input_pbf "$SG_PBF"  --bbox 1.27,103.75,1.33,103.82     --output data/external/normalized/osm/singapore_sidewalks.geojson --overwrite
```

然后：

```bash
python scripts/validate_georeference_alignment.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston,pittsburgh,vegas,singapore \
  --write_georeference \
  --report_json data/external/reports/georeference_spatial_alignment.json
```

旧 report 必须丢弃，因为是旧 CRS bug 下生成的。

---

## 7. 第四步：重新归一化公共 GIS

建议让修复后的 fetcher 重新跑四城，至少 Pittsburgh + Singapore 必须重跑：

```bash
python scripts/fetch_recommended_public_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston,pittsburgh,vegas,singapore \
  --force \
  --strict
```

如果某源因 portal/API 限制不能自动下载，按输出的 `manual_fallback` 手工下载，仍然放到 YAML/文档约定路径。

### 7.1 Pittsburgh Payment Points

手工文件：

```text
data/external/raw/wprdc/pittsburgh/payment_points_current.csv
```

先检查：

```bash
python scripts/inspect_tabular_coordinates.py \
  --input data/external/raw/wprdc/pittsburgh/payment_points_current.csv \
  --min_valid_fraction 0.90 \
  --min_valid_rows 1000
```

再：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/wprdc/pittsburgh/payment_points_current.csv \
  --output data/external/normalized/candidates/pittsburgh/payment_points_current.jsonl \
  --profile pittsburgh_parking_meter \
  --source "Pittsburgh Parking Authority via WPRDC" \
  --skip_invalid
```

这个 layer 只作为 candidate cue，不是独立 legal-stop evidence。

### 7.2 Singapore LTA

必须重新生成以下修复后的正常文件：

```text
data/external/normalized/city_gis/singapore/footpath.geojson
data/external/normalized/city_gis/singapore/kerbline.geojson
```

以及候选：

```text
data/external/normalized/candidates/singapore/passenger_pickup_bay.jsonl
data/external/normalized/candidates/singapore/taxi_stand.jsonl
```

如果旧 malformed `.geojson` 仍在，`--force` 会覆盖；也可先移走做备份。

---

## 8. 第五步：DEM 必须按 configured AOI 重做 readiness gate

### 8.1 正确 AOI

```text
Boston:     west=-71.15  south=42.30  east=-70.98  north=42.42
Pittsburgh: west=-80.04  south=40.38  east=-79.88  north=40.48
Las Vegas:  west=-115.23 south=36.07  east=-115.10 north=36.20
Singapore:  west=103.75  south=1.27   east=103.82  north=1.33
```

你“指令执行结果”中记录的 Pittsburgh/Las Vegas AOI 有明显复制/标签错位。实际现有 Boston/Pittsburgh tile reports 已覆盖正确 YAML AOI，因此更像日志记录错误或下载过程混用了多次 AOI；最终以 `validate_dem_tiles.py` report 为准。

### 8.2 Boston

现有 `coverage=1.0`，可复用，但建议修复代码下重跑 report：

```bash
python scripts/validate_dem_tiles.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --city boston \
  --rasters data/external/raw/dem/boston/*.tif \
  --expected_resolution_m 1 \
  --min_coverage 0.99 \
  --report_json data/external/reports/dem_tiles_boston.json

python scripts/sample_raster_dem.py \
  --external_root data/external \
  --city boston \
  --rasters data/external/raw/dem/boston/*.tif \
  --vertical_datum NAVD88 \
  --source_name USGS_3DEP_1m \
  --nominal_resolution_m 1 \
  --tile_validation_report data/external/reports/dem_tiles_boston.json \
  --include_city_gis
```

### 8.3 Pittsburgh

现有 `coverage≈0.993456`，可复用：

```bash
python scripts/validate_dem_tiles.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --city pittsburgh \
  --rasters data/external/raw/dem/pittsburgh/*.tif \
  --expected_resolution_m 1 \
  --min_coverage 0.99 \
  --report_json data/external/reports/dem_tiles_pittsburgh.json

python scripts/sample_raster_dem.py \
  --external_root data/external \
  --city pittsburgh \
  --rasters data/external/raw/dem/pittsburgh/*.tif \
  --vertical_datum NAVD88 \
  --source_name USGS_3DEP_1m \
  --nominal_resolution_m 1 \
  --tile_validation_report data/external/reports/dem_tiles_pittsburgh.json \
  --include_city_gis
```

### 8.4 Vegas

先在 USGS The National Map/3DEP 的 1 m availability 上用 **正确 Vegas AOI** 检查并补所有实际有覆盖的 1 m tiles。

如果补齐后仍无法达到 0.99，说明该 AOI 的 1 m source 本身不完整。不要：

- 降低 0.99 然后继续声称“Vegas 1 m DEM complete”；
- 混 1 m + 10 m 后仍标 `nominal_resolution_m=1`。

此时使用完整覆盖的 seamless 1/3 arc-second（约 10 m）作为 **terrain prior**，统一标：

```text
source_name = USGS_3DEP_1_3_arcsec
nominal_resolution_m = 10
```

示例：

```bash
python scripts/validate_dem_tiles.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --city vegas \
  --rasters data/external/raw/dem/vegas_10m/*.tif \
  --expected_resolution_m 10 \
  --min_coverage 0.99 \
  --report_json data/external/reports/dem_tiles_vegas.json

python scripts/sample_raster_dem.py \
  --external_root data/external \
  --city vegas \
  --rasters data/external/raw/dem/vegas_10m/*.tif \
  --vertical_datum NAVD88 \
  --source_name USGS_3DEP_1_3_arcsec \
  --nominal_resolution_m 10 \
  --tile_validation_report data/external/reports/dem_tiles_vegas.json \
  --include_city_gis
```

如果你最终补齐了真正的 1 m coverage，则仍使用 1 m 命令和 source name。

### 8.5 Singapore

推荐下载 Copernicus DEM：

```text
COP-DEM_GLO-30-DGED
latest/reference delivery used by the project docs: 2024_1
GRID ID: N01_E103
```

不要再只裁 `103.7600..103.8100`；下载完整 grid/product，保证覆盖 YAML AOI。

```bash
python scripts/validate_dem_tiles.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --city singapore \
  --rasters data/external/raw/dem/singapore/*.tif \
  --expected_resolution_m 30 \
  --min_coverage 0.99 \
  --report_json data/external/reports/dem_tiles_singapore.json

python scripts/sample_raster_dem.py \
  --external_root data/external \
  --city singapore \
  --rasters data/external/raw/dem/singapore/*.tif \
  --vertical_datum EGM2008 \
  --source_name COPERNICUS_GLO30_DSM \
  --nominal_resolution_m 30 \
  --tile_validation_report data/external/reports/dem_tiles_singapore.json \
  --include_city_gis
```

重要：USGS/Copernicus DEM 只能作为大尺度 terrain/elevation prior，不能当 curb reveal、curb ramp running slope、cross-slope 或 deployment clearance 的 ground truth。

---

## 9. 第六步：重新做 bootstrap preflight

先准备 bootstrap fleet（仅用于 sanity check）：

```bash
mkdir -p data/external/normalized/fleet
cp configs/fleet.abilitybench.example.jsonl \
   data/external/normalized/fleet/vehicle_interfaces.jsonl
```

然后：

```bash
python scripts/validate_external_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston,pittsburgh,vegas,singapore \
  --source_policy bootstrap \
  --output data/external/reports/external.bootstrap.json
```

bootstrap PASS 的含义仅是：

```text
nuPlan / map / georef / pedestrian topology / minimum required external chain is usable
```

不是 publication-ready。

---

## 10. 第七步：先跑小规模 sanity dataset

先 Boston val 10：

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split val \
  --source_policy bootstrap \
  --cities boston \
  --max_scenarios_per_city 10 \
  --stages map_crs,preflight,extract,graphs,pudo,service,dataset,merge
```

再验证：

```bash
python scripts/validate_dataset.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_val_boston \
  --strict

python scripts/audit_dataset_quality.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_val_boston
```

bootstrap audit 不应再仅因为 `source_policy != paper` 假 FAIL。

Boston 10 通过后再：

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split val \
  --source_policy bootstrap \
  --cities boston+pittsburgh+vegas+singapore \
  --max_scenarios_per_city 10 \
  --stages map_crs,preflight,extract,graphs,pudo,service,dataset,merge
```

重点检查：

```text
graph nodes/edges
PUDO candidate count
service requests count = 8 per episode
counterfactual_pairs = 7 per episode
same O/D/time for each counterfactual group
no train/val/test overlap
```

---

## 11. 第八步：从 bootstrap PUDO 导出真正值得人工核验的点

对每城从 bootstrap PUDO evidence 导出 shortlist：

```bash
python scripts/export_pudo_audit_shortlist.py \
  --pudo_evidence_jsonl data/outputs/prepared/val/pudo/boston.jsonl \
  --georeference_json data/external/georeference/boston.json \
  --city boston \
  --max_candidates_per_episode 4 \
  --dedup_radius_m 5 \
  --output_csv data/external/audits/boston/pudo_audit_shortlist.csv
```

其它三城替换 city/path。

shortlist 的作用是减少人工工作量。它不会自动把 candidate 变成 truth。

---

## 12. Paper PUDO/entrance audit 最低要求

对选中的 PUDO site 至少核验：

```text
curb_height_m
sidewalk_width_m
deployment_clearance_m
curb_ramp
legal_stop
legal_basis
observed_at
auditor_id
```

建议额外：

```text
running_slope
cross_slope
surface
photo_ref / source_record_id
```

### Entrance 必须独立

若审计真实 building/service entrance：

```text
entrance_id
entrance_lon
entrance_lat
```

不能把 curb coordinate 复制成 entrance coordinate，也不能把普通 parcel/address point 无条件当 physical entrance ground truth。

生成正式 evidence：

```bash
python scripts/build_manual_audit_layers.py \
  --input_csv data/external/audits/boston/pudo_audit_shortlist.csv \
  --city boston \
  --external_root data/external
```

产物：

```text
normalized/curb_inventory/<city>.jsonl
normalized/curb_regulations/<city>.jsonl
normalized/entrances/<city>.geojson
audits/<city>/manual_audit_manifest.jsonl
```

---

## 13. Paper vehicle interface 文件

必须用实际实验/仿真所代表的 vehicle platform 的 manufacturer/operator/measurement evidence 替换 example fleet。

最终路径：

```text
data/external/normalized/fleet/vehicle_interfaces.jsonl
```

每行必须显式包含至少：

```json
{
  "episode_id": "*",
  "vehicle_id": "YOUR_VERIFIED_PLATFORM",
  "door_side": "...",
  "ramp": true,
  "lift": false,
  "low_floor": true,
  "door_width_m": 0.0,
  "deployment_clearance_m": 0.0,
  "notification_modes": ["..."],
  "dwell_time_s": 0.0,
  "kneeling": false,
  "source": "manufacturer_or_operator_verified_source",
  "metadata": {
    "source_document": "...",
    "version_or_date": "..."
  }
}
```

上面的 `0.0/...` 仅是 schema illustration，不是建议数值；必须替换为真实核验值。

---

## 14. Provenance：paper 前必须冻结

```bash
cp data/external/schemas/provenance_registry.example.yaml \
   data/external/provenance_registry.yaml
```

为 paper 会用到的每个 source 填：

```text
role
path
source_url
license
retrieved_at
evidence_tier
authoritative
```

然后：

```bash
for city in boston pittsburgh vegas singapore; do
  python scripts/build_provenance_manifest.py \
    --registry data/external/provenance_registry.yaml \
    --city "$city" \
    --output "data/external/manifests/${city}.json"
done
```

脚本会对实际文件做 SHA256。

---

## 15. Paper external preflight

```bash
python scripts/validate_external_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston,pittsburgh,vegas,singapore \
  --source_policy paper \
  --output data/external/reports/external.paper.json
```

paper policy 至少检查：

```text
pedestrian_topology
georeference_parseable
georeference_validated
curb_physical_inventory
curb_legality_or_regulation
entrance_layer
elevation_or_measured_slope
authoritative_accessibility_evidence
source_provenance_and_license_manifest
```

如果只有 Boston 准备充分，不要强行让其它三城 PASS。先：

```bash
--cities boston
```

做 paper pilot，然后扩城。

---

## 16. Paper dataset 的正确 train / val / test 构建方式

先 val 小样本：

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split val \
  --source_policy paper \
  --cities boston \
  --max_scenarios_per_city 20 \
  --stages preflight,extract,graphs,pudo,service,dataset,merge
```

通过后分别构建官方 split：

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split train \
  --source_policy paper \
  --stages extract,graphs,pudo,service,dataset,merge

python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split val \
  --source_policy paper \
  --stages extract,graphs,pudo,service,dataset,merge

python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split test \
  --source_policy paper \
  --stages extract,graphs,pudo,service,dataset,merge
```

最终使用：

```text
abilitybench_av_train
abilitybench_av_val
abilitybench_av_test
```

不要在单个 `abilitybench_av_val` 内再随机切出 train/test。

---

## 17. Publication quality gate

城市 dataset：

```bash
python scripts/validate_dataset.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_val_boston \
  --strict
```

再：

```bash
python scripts/audit_dataset_quality.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_val_boston \
  --paper_mode \
  --fail_if_not_publication_ready \
  --min_graph_nodes 100 \
  --min_graph_edges 150 \
  --min_paper_eligible_pudos_per_episode 2 \
  --min_episode_pudo_coverage_rate 0.80 \
  --min_failure_phase_diversity 2 \
  --min_edge_positive_rate 0.10 \
  --min_skeleton_positive_rate 0.10
```

必须：

```text
ABILITYBENCH_DATASET_CHECK=PASS
```

重点看：

```text
paper_eligible_total
paper_eligible_by_episode
episodes_meeting_pudo_gate
episode_pudo_coverage_rate
failure_phase_diversity
graph_min_nodes
graph_min_edges
edge_positive_rate
skeleton_positive_rate
counterfactual per-episode seven-axis coverage
```

---

## 18. 总 gate

Bootstrap：

```bash
python scripts/check_abilitybench_pipeline.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --source_policy bootstrap \
  --splits train,val,test \
  --report_json data/external/reports/pipeline.bootstrap.json
```

Paper：

```bash
python scripts/check_abilitybench_pipeline.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --source_policy paper \
  --splits val,test \
  --dataset_dir data/outputs/datasets/abilitybench_av_val_boston \
  --report_json data/external/reports/pipeline.paper.json
```

最终：

```text
ABILITYBENCH_PIPELINE_CHECK=PASS
```

---

## 19. 四城外部文件建议清单

### 19.1 四城共同必需

1. nuPlan v1.1 DB set：你已有；
2. 官方 nuPlan maps package：必须含 `nuplan-maps-v1.0.json` + 四城 map GPKG；
3. Geofabrik OSM PBF：你已有四个；
4. DEM/DSM；
5. real/audited fleet interface JSONL；
6. paper provenance manifests；
7. targeted PUDO curb physical + legal/regulation + entrance audit evidence。

### 19.2 Boston

建议保留/重建：

- City of Boston sidewalk inventory / sidewalk geometry；
- City of Boston curb-ramp inventory；
- sidewalk centerline；
- curb/sidewalk physical layers；
- USGS 3DEP terrain DEM；
- targeted PUDO legality + curb/interface + entrance audit。

Boston 的 public ramp/sidewalk layers可以增强 authoritative accessibility evidence，但不要把某个 public layer 的 presence 自动等价为 legal stop 或 vehicle ramp deployment feasibility。

### 19.3 Pittsburgh

- WPRDC Sidewalks and Steps；
- Pittsburgh Parking Authority Current Payment Points：仅 candidate cue；
- PASDA/County address points：仅 bootstrap entrance proxy；
- street closures：仅有时间对齐时用于 dynamic evidence；
- USGS 3DEP DEM；
- targeted curb physical + regulation/legality + real entrance audit。

### 19.4 Las Vegas

- OSM pedestrian topology；
- City of Las Vegas Taxi Zones：仅 PUDO candidate cue，且官方 layer 描述本身说明很多位置是 approximate；
- 可选 parking-meter/parking-zone layers作候选或 regulation context；
- USGS DEM：1 m availability 不足时用完整 10 m seamless terrain prior；
- targeted curb/interface/legality/entrance audit。

### 19.5 Singapore

- LTA Footpath；
- LTA Kerbline；
- LTA Passenger Pickup Bay；
- LTA Taxi Stand；
- Copernicus DEM GLO-30 `N01_E103`；
- targeted interface geometry / clearance / legal basis / entrance audit。

LTA Passenger Pickup Bay / Taxi Stand 比普通停车点更适合作为 candidate，但仍不能单独提供全部 boarding/alighting physical feasibility 字段。

---

## 20. 生成文件如何管理

### `data/external/raw/`

视为 immutable source archive：

- 下载后不要无声覆盖；
- 记录 retrieval date / URL / license / hash；
- source 更新时保存新版本或至少让 provenance manifest 可追踪。

### `data/external/normalized/`

可再生成的 derived layer：

- parser/normalizer 代码修复后应重建；
- 本次必须重建 Singapore LTA 和 Pittsburgh payment candidate；
- 不要把 normalized file 本身当“原始官方证据”。

### `data/external/georeference/`

由 map package + CRS inspection 生成：

- 修 map hierarchy 或 CRS 代码后必须全部重建；
- 旧 georeference alignment report 不应继续使用。

### `data/external/reports/`

每个 frozen dataset version 保留对应 report：

- DB cities；
- map CRS；
- georef alignment；
- DEM tiles；
- DEM sampling；
- external source preflight；
- pipeline gate。

### `data/outputs/prepared/<split>/`

是可重建 intermediate：

- 修改 external evidence、georef、counterfactual config、fleet 或 PUDO audit 后建议删除相应 city/split 后重建；
- 不要把旧 prepared 与新 source 混合。

### `data/outputs/datasets/`

一旦用于论文指标就冻结版本：

```text
abilitybench_av_train_v1
abilitybench_av_val_v1
abilitybench_av_test_v1
```

源或代码改变就生成 v2，不要原地篡改论文已报告数据。

### 动态数据

例如 current street closure / temporary blockage：

- 必须有 timestamp/validity；
- 没有与 nuPlan scene 时间对齐就不要融合成 historical truth；
- 可作为“controlled counterfactual perturbation”，但需明确是模拟/构造层，不是原场景事实。

---

## 21. 这个 benchmark 如何完整支撑论文 T1–T5

### T1 Passenger-complete pickup/drop-off

主结果只统计 paper-eligible PUDO：

- pedestrian access/egress connectivity；
- distance/slope/width/surface/ramp；
- curb height；
- legal stop；
- deployment clearance；
- vehicle interface compatibility；
- confidence。

### T2 Capability-aware ride planning

nuPlan 提供 traffic scene/route/agents；CapPlan service layer提供 passenger motion contract。

论文主实验应在 planner-produced trajectory 上计算：

- collision / drivable / route progress；
- acceleration / jerk / lateral acceleration / braking；
- ride-motion budget residual / violation。

本次代码提供 nuPlan ego-history motion labels用于监督/benchmark reference；若做闭环 planner 比较，最终指标必须从各方法实际 trajectory 重算。

### T3 End-to-end

必须联合验证：

```text
access + wait + board + ride + alight + egress
```

不能把 vehicle route completion 当 PC success。

### T4 Same-scene capability counterfactual

每 episode 固定：

```text
traffic scene
O/D
request time
vehicle platform
```

只改变 capability axis；七轴 pair 已由修复代码显式输出。必须统计：

- irrelevant tightening 时 plan 不应无意义改变；
- relevant tightening 时 path/interface/trajectory/certificate 正确改变；
- impossible contract 返回 certificate。

### T5 Diagnostic failure certificate

label/oracle 与模型输出分离：

- failure phase；
- failed transition；
- resource type；
- signed margin；
- evidence source；
- confidence。

建议对 test 中一部分 infeasible cases 做人工复核，防止 verifier 和 planner 共用同一错误导致“自洽但错误”的 DF/SME。

---

## 22. 建议的论文数据分层

不要把所有样本混成一个“truth level”。建议至少三个标记：

```text
bootstrap_only
paper_eligible
controlled_counterfactual
```

并对每个 edge/PUDO/resource 保留：

```text
evidence_source
evidence_tier
confidence
observed_at / retrieved_at
unknown_reason
```

论文主表：只用 `paper_eligible` main-result subset。  
Robustness/missing-evidence 实验：从冻结的 paper truth layer 生成 deterministic seeded masks/noise views，不能改写 base truth。  
Bootstrap 大规模数据：可用于预训练/工程 sanity/coverage study，但不要和 audited test truth 混为一谈。

---

## 23. 最推荐的实际执行顺序

从你当前状态，按下面顺序最稳妥：

```text
A. 安装修复代码，pytest 104/104
B. 修 nuPlan maps hierarchy + manifest
C. DB city checks 重跑
D. map CRS / georeference alignment 重跑
E. OSM 四城确认
F. Pittsburgh payment + Singapore LTA 用新 normalizer 重做
G. Boston/Pittsburgh DEM report 重做；Vegas补齐/切 10m；Singapore下载完整 N01_E103
H. 四城 bootstrap external preflight
I. Boston val 10 bootstrap sanity
J. 四城 val 10 bootstrap sanity
K. 导出 PUDO audit shortlist
L. targeted curb physical / legality / entrance audit
M. real vehicle interface JSONL
N. provenance manifests
O. paper external preflight（先 Boston pilot）
P. paper val small set + schema/quality gate
Q. 再扩 train/val/test，保留官方 split
R. 冻结 dataset version + reports + manifests + code commit/hash
S. 运行 T1–T5、baseline、ablation、uncertainty experiments
```

只有走到 O–R 并通过 paper-mode quality gate，才应称为“能够完全支撑论文主算法论证的 benchmark”。

