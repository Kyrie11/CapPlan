# CapPlan / AbilityBench-AV 四城完整数据集构建与论文验证指南（data0 版，2026-08-17）

> 适用代码根目录：`CAP_HOME=/home/senzeyu2/code/CapPlan`  
> 统一数据根目录：`DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data`  
> 本指南是在 `CapPlan-dataset-build.md`、`CapPlan-dataset-build-optimized.md`、`CapPlan-dataset-recovery-and-paper-build-guide.md` 之后的合并/审计版。

## 0. 结论先行：论文真正需要什么数据集

论文不是在证明一个“更舒适的 nuPlan planner”，而是在证明：**traffic-safe / route-complete 不等于 passenger-complete**。因此 AbilityBench-AV 的最小可信 episode 不是一条 nuPlan trajectory，而必须同时包含：

1. **真实交通场景**：nuPlan ego/agent history、route corridor、road semantics、traffic context、时间戳；
2. **真实 origin/destination entrance**：必须是可行人到达的实际入口点，而不是 mission goal、地址质心或建筑中心的自动代理；
3. **pedestrian / curb accessibility graph**：拓扑 + width、slope、cross-slope、surface、curb ramp、crossing、obstacle、lighting/shelter、confidence、timestamp；未知字段保持 unknown；
4. **PUDO candidate + paper evidence**：curb geometry、独立 stopping legality、adjacent pedestrian width、ramp/lift deployment clearance、dynamic availability、confidence；
5. **vehicle-interface specification**：door side、ramp/lift、low-floor/kneeling、door width、deployment clearance、notification modalities、dwell policy；
6. **capability contracts**：同一 scene / OD / request time / vehicle 下的多组能力约束，构成 T4 same-scene counterfactual；
7. **oracle labels**：transition validity、typed resource demand、passenger-specific edge feasibility、passenger-complete skeleton、failure certificate；
8. **closed-loop vehicle result**：用于 CR/RC/TRV/TT 等标准 AV 指标；
9. **provenance/license**：每个用于 paper truth 的源必须能追溯 URL、许可、版本/时间、SHA256、人工审计人和观察时间。

因此必须把数据分为两层：

- **`bootstrap/log-play layer`**：四城大量 nuPlan 场景 + OSM/官方 GIS/DEM 等候选证据，用于流水线、覆盖、预训练、难例发现；允许 evidence unknown，但必须 fail-closed，不能伪造缺失物理量。
- **`paper-eligible audited layer`**：只保留 entrance、PUDO physical、legality、vehicle interface、provenance 都可独立复核的 episode/PUDO。论文主表、T1/T3/T4/T5 和 paper-mode evaluation 只允许使用这一层。

这两个层次不要混成一个“全部 PASS”的概念。`bootstrap PASS` 只说明数据可用于构建；`paper PASS` 才说明证据链够强。

---

## 1. 本次审计发现的关键问题与修复

### 1.1 `val` 不是“城市识别错了”

nuPlan `val` / `test` 可以是混合城市 DB 目录。正确做法是让 `NuPlanScenarioBuilder/ScenarioFilter(map_names=...)` 在同一目录中按 `us-ma-boston`、`us-pa-pittsburgh-hazelwood`、`us-nv-las-vegas-strip`、`sg-one-north` 过滤，而不是复制/移动 DB。

你遇到的：

`contract ...:basic_service_complete references unknown episode ...:basic_service_complete`

真正原因是 T4 新合同 ID 已变成：

`<episode_id>:<profile_id>`

但 validator / closed-loop / CASA loader / planner 仍按旧 `:p0` 后缀解析 episode ID。已统一改为优先读 `contract.metadata.episode_id`，再兼容旧格式。

### 1.2 旧 georeference alignment 的四城 FAIL 是覆盖方向错误

旧脚本计算的是：

`intersection(map, AOI) / area(AOI)`

因为 configured AOI 本来就故意比 nuPlan map 大，这个比例天然很小。paper gate 真正需要的是：

- `area(map ∩ AOI) / area(map)`：nuPlan map 是否被 AOI 覆盖；
- `area(AOI ∩ OSM) / area(AOI)`：OSM 是否覆盖 AOI；
- `area(map ∩ OSM) / area(map)`：OSM 是否覆盖 nuPlan map。

已修复脚本并写入 `spatial_alignment_validated=true`。旧报告中 Boston/Pittsburgh/Singapore 的 map 实际都被 AOI 完整覆盖；Vegas 原 AOI 南界 `36.07` 会裁掉 nuPlan map 南部约 5%，所以 Vegas 改为 `south=36.055`。

**重要**：Vegas AOI 扩大后，OSM 和 1 m DEM 都要按新 AOI 重做/补齐，不可继续拿旧 AOI 的 DEM PASS 报告当 paper 证据。

### 1.3 Pittsburgh payment points 的 errors 是真实空坐标，不应插值

`payment_points_current.csv` 有一部分行 `latitude=''`、`longitude=''`。这不是 CRS 问题。对停车表这种候选源，正确行为是：

- `inspect_tabular_coordinates.py` 检查总体有效率；
- normalize 时 `--skip_invalid`；
- 把 invalid count 留在报告/命令日志；
- 不从地址或相邻 meter 猜坐标。

Payment Points 是 PUDO **candidate source**，不是 physical accessibility 或 legality ground truth。

### 1.4 Boston GIS 的“normalize PASS”不等于物理量可用于 paper

旧 `boston_sidewalk` / `boston_ramp` profile 如果不知道字段单位，必须保持 unknown。不能因为字段叫 width/slope/reveal 就默认 feet/percent/inch。

本次增加：

- `boston_pwd_sidewalk`：读取 Boston PWD/Cartegraph Sidewalks 的显式 `Width_unit` / `Slope_unit`；
- `boston_pwd_ada_ramp`：读取显式单位字段；
- 不再把语义不同的 `Rise` 之类字段当作 curb height/deployment clearance。

Boston paper build 优先使用 PWD/Cartegraph 现行资产层；旧 Infrastructure/OpenData 可以作为补充、交叉验证或候选层。

### 1.5 manual audit `auditor_id is required` 是正确 fail-closed

`export_pudo_audit_shortlist.py` 生成的是“待审计候选列表”，不是审核完成文件。`build_manual_audit_layers.py` 要求实际填写 auditor ID 是刻意设计的 paper gate。

本次 shortlist 还增加 `entrance_lon/entrance_lat`，使入口位置也可独立核验；并支持把 shortlist/build 统计写进 `external/reports`。

### 1.6 provenance example 只有 Boston + TODO 占位符不能用于 paper

已提供四城 data0 模板：

`data/external/schemas/provenance_registry.data0.paper.example.yaml`

`build_provenance_manifest.py` 现在会拒绝 `TODO/TBD/REVIEW/REPLACE/VERIFY/...` 这类占位符，要求：

- source URL 非占位；
- license/terms 非占位；
- `retrieved_at` 是带时区 ISO timestamp；
- 本地证据文件存在且可 hash；
- manual audit manifest 已生成。

### 1.7 CASA value target / feature leakage / sampler

已修复三类问题：

1. `--value_target offline_tsbs` 原先只做 CLI 门禁，实际仍使用 skeleton/heuristic prior。现在：
   - `skeleton` = 纯 0/1 skeleton membership；
   - `offline_tsbs` 必须存在 `completion_value_labels.offline_tsbs.jsonl`；
   - `rollout` 必须存在 `completion_value_labels.rollout.jsonl`；
   - 缺文件直接 fail。
2. paper mode 新 `paper_safe` feature policy 会屏蔽 `z_e`、availability、completion_value、to_phase、直接 resource aggregate 等 target-derived slots，防止把答案塞进输入。
3. `--profile_balanced_sampler` / `--action_balanced_sampler` 原先是 no-op；现在真正做 inverse-frequency weighted resampling。

### 1.8 当前 learned CASA-Net 仍有论文级实现缺口

当前 `hgt/rgcn` 名称对应的实现仍是 **relation-aware MLP surrogate**：把 action/source phase/target phase 做 embedding 后拼到单 transition feature 上；没有真正对 heterogeneous service graph 做 message passing，也没有把 nuPlan agent/map/service observation tensor 编码成图节点/边上下文。

因此：

- 当前代码可严谨论证：capability semantic compilation、service automaton、typed resource algebra、conservative margins、TSBS、counterfactual semantics、failure certificates，以及基于 audited evidence 的 deterministic/oracle labels；
- 当前 lightweight learned heads 可做工程实验，但**不能把它写成真正 HGT/R-GCN service-graph network 的充分实现证据**；
- 若论文保留“CASA-Net 从 heterogeneous service graph + dynamic agents 学 learned transition/demand/availability”的强表述，还必须在数据集构建后新增真正 graph samples + graph message-passing model。见第 15 节。

### 1.9 `--max_scenarios_per_city 0` 现在表示“所有匹配场景”

原代码 `[:max_scenarios]` 使 0 变成空数据；prepare override 还用了 `override or default`，所以不能用 0。

已修复：真实 nuPlan 数据下 `0` = all matching scenarios；synthetic 数据仍要求 >0。建议先小规模 pilot，再用 0 跑完整候选集。

---

## 2. 官方/公开外部源分级

### 2.1 Evidence tier 规则

**Tier A — authoritative / audited paper evidence**

- City/transport agency 官方资产 GIS，且字段语义/单位明确；
- 官方 curb/loading regulation；
- 官方 vehicle manufacturer/fleet interface specification；
- 现场/影像/人工测量 audit，带 auditor/timestamp/source；
- 与 scene timestamp 匹配的动态事实；
- 可复现 DEM 产品及 metadata。

**Tier B — community / proxy candidate evidence**

- OpenStreetMap；
- address point / building centroid；
- parking meter/taxi-zone geometries；
- 当前公开 closure 数据但与历史 scene 不匹配；
- DEM 对局部 sidewalk slope 的近似（DEM 是 terrain prior，不是 curb-ramp running/cross slope ground truth）。

Tier B 可以产生候选和 uncertainty，不能自动升级为 Tier A。

### 2.2 四城共同源

1. **nuPlan v1.1 + maps v1.0**：road/traffic scene truth；
2. **OpenStreetMap PBF**（Geofabrik 分发）：pedestrian topology/crossing/curb/ramp/community tags；
3. **DEM**：
   - U.S. cities: USGS 3DEP 1 m；
   - Singapore: Copernicus DEM GLO-30；
4. **manual audit**：入口、curb/loading physical、legality、deployment clearance；
5. **fleet interface spec**；
6. **provenance registry/manifest**。

### 2.3 Boston

优先：

- Boston PWD `Cartegraph_PWD_readonly/MapServer/0` ADARamps；
- Boston PWD `Cartegraph_PWD_readonly/MapServer/7` Sidewalks；
- Infrastructure/OpenData: sidewalk inventory, ramp inventory, sidewalk centerline, curbs 作为补充；
- USGS 3DEP 1 m；
- manual PUDO/entrance audit + official stopping/curb rule basis。

### 2.4 Pittsburgh

- WPRDC Sidewalks/Steps：真实 pedestrian topology；
- Pittsburgh Parking Authority Current Payment Points：候选 curb/PUDO proximity；空坐标行丢弃；
- DOMI Street Closures：可作为带有效时间区间的 dynamic source；**只有 closure validity 与 nuPlan scene timestamp 重叠时才能作为该 scene 的真实 dynamic label**；否则只是 current/context layer；
- PASDA/Allegheny address points：entrance candidate，不是入口真值；
- USGS 3DEP 1 m；
- manual physical/legality/entrance audit。

### 2.5 Las Vegas

- City of Las Vegas `CLV_ParkingServices_ParkingZones`：Taxi/No Parking/Handicap/parking zones 可作为官方 curbside semantic candidates；
- Taxi Zones 官方说明明确说多数位置由 digitizing/aerial 近似，因此**不要**把 polyline 几何直接当 physical curb measurement；
- OSM 负责 pedestrian topology 候选；
- USGS 3DEP 1 m，按新 AOI `36.055..36.20` 补齐；
- paper PUDO/entrance 必须人工/官方独立核验。

### 2.6 Singapore

- LTA DataMall static geospatial：
  - Passenger Pickup Bay（首选 PUDO candidate）；
  - Taxi Stand（辅助）；
  - Footpath（pedestrian topology）；
  - Kerbline（curb geometry candidate）；
- OneMap：authoritative national map，可用于 building/address/road/POI anchoring，但 Search API 返回的是建筑/地址坐标，**不是 doorway entrance ground truth**；
- Copernicus DEM GLO-30，WGS84 horizontal / EGM2008 vertical；
- paper entrance/PUDO physical/legality仍需独立 audit 或精确官方设施记录。

### 2.7 官方下载/服务链接（建议写入 provenance registry）

- nuPlan devkit / v1.1 release: `https://github.com/motional/nuplan-devkit`
- nuPlan dataset hierarchy: `https://nuplan-devkit.readthedocs.io/en/latest/dataset_setup.html`
- Geofabrik North America: `https://download.geofabrik.de/north-america.html`
- Geofabrik Malaysia/Singapore/Brunei: `https://download.geofabrik.de/asia/malaysia-singapore-brunei.html`
- OpenStreetMap license/attribution: `https://www.openstreetmap.org/copyright`
- Boston Infrastructure/OpenData: `https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer`
- Boston PWD Cartegraph: `https://gisportal.boston.gov/ArcGIS/rest/services/PWD/Cartegraph_PWD_readonly/MapServer`
- Pittsburgh Parking Meters/Payment Points: `https://data.wprdc.org/dataset/pittsburgh-parking-meters-and-payment-points`
- Pittsburgh DOMI Street Closures: `https://data.wprdc.org/dataset/street-closures`
- Las Vegas Parking Services zones: `https://mapdata.lasvegasnevada.gov/clvgis/rest/services/Transportation/CLV_ParkingServices_ParkingZones/MapServer`
- LTA DataMall: `https://datamall.lta.gov.sg/`
- Singapore OneMap API: `https://www.onemap.gov.sg/apidocs/`
- USGS National Map Downloader: `https://apps.nationalmap.gov/downloader/`
- USGS 3DEP 1 m product metadata: `https://data.usgs.gov/datacatalog/data/USGS%3A77ae0551-c61e-4979-aedd-d797abdcde0e`
- USGS Seamless 1 m metadata: `https://data.usgs.gov/datacatalog/data/USGS%3A4f34caac-f28f-4ea0-8d82-eafb2b8f9a5d`
- Copernicus DEM collection: `https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM`
- U.S. Access Board PROWAG R311: `https://www.access-board.gov/prowag/complete.html`

网页本身不是 provenance 的全部。registry 还应记录实际下载文件/REST query、retrieval time、产品/图层版本、license/terms、SHA256。

---

## 3. data0 目录迁移

```bash
export CAP_HOME=/home/senzeyu2/code/CapPlan
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export CONFIG=$CAP_HOME/configs/abilitybench_nuplan_real_data0.yaml
export EXT=$DATA_ROOT/external
export REPORTS=$EXT/reports

mkdir -p /data0/senzeyu2/dataset/CapPlan
mkdir -p "$DATA_ROOT"
rsync -aH --info=progress2 "$CAP_HOME/data/" "$DATA_ROOT/"
mkdir -p "$REPORTS/commands" "$REPORTS/build" "$REPORTS/model" "$REPORTS/eval"
```

迁移后不要再让 paper 命令混用 `$CAP_HOME/data`。代码/配置仍留在 `$CAP_HOME`，大文件和生成结果全部进入 data0。

建议第一次迁移完成后：

```bash
cd "$CAP_HOME"
python - <<'PY'
import yaml
p='configs/abilitybench_nuplan_real_data0.yaml'
cfg=yaml.safe_load(open(p))
for k in ['data_root','db_root','map_root']:
    assert str(cfg['nuplan'][k]).startswith('/data0/senzeyu2/dataset/CapPlan/data/')
assert cfg['external_root'].startswith('/data0/')
assert cfg['outputs_root'].startswith('/data0/')
print('DATA0_CONFIG=PASS')
PY
```

---

## 4. 清理旧报告语义

你当前 `reports` 中同时有：

- `dem_tiles_boston.json`（当前 canonical）；
- `dem_tiles.boston.json`（旧版本/旧 gate）。

不要让论文脚本自动 glob 到旧 dotted reports。建议：

```bash
mkdir -p "$REPORTS/archive/pre_20260817"
for f in "$REPORTS"/dem_tiles.boston.json "$REPORTS"/dem_tiles.pittsburgh.json "$REPORTS"/dem_tiles.vegas.json; do
  [ -e "$f" ] && mv "$f" "$REPORTS/archive/pre_20260817/"
done
```

旧 `georeference_spatial_alignment.json` 也保留归档，再由新脚本覆盖 canonical 文件。

---

## 5. nuPlan DB / map readiness

```bash
cd "$CAP_HOME"
for split in train val test; do
  python scripts/inspect_nuplan_db_cities.py \
    --config "$CONFIG" \
    --split "$split" \
    --fail_on_unknown \
    --report_json "$REPORTS/nuplan_db_cities.${split}.json" \
    2>&1 | tee "$REPORTS/commands/nuplan_db_cities.${split}.log"
done

python scripts/inspect_nuplan_map_crs.py \
  --config "$CONFIG" \
  --cities boston+pittsburgh+vegas+singapore \
  --output_dir "$EXT/georeference" \
  2>&1 | tee "$REPORTS/commands/nuplan_map_crs.log"
```

PASS 只代表 DB city mapping 与 map CRS metadata 可解析；还需要下一节 spatial alignment。

---

## 6. OSM PBF 与正确 AOI

PBF 文件仍可放：

```text
$EXT/raw/osm_pbf/massachusetts-latest.osm.pbf
$EXT/raw/osm_pbf/pennsylvania-latest.osm.pbf
$EXT/raw/osm_pbf/nevada-latest.osm.pbf
$EXT/raw/osm_pbf/malaysia-singapore-brunei-latest.osm.pbf
```

重新生成：

```bash
export BOS_PBF="$EXT/raw/osm_pbf/massachusetts-latest.osm.pbf"
export PIT_PBF="$EXT/raw/osm_pbf/pennsylvania-latest.osm.pbf"
export VEG_PBF="$EXT/raw/osm_pbf/nevada-latest.osm.pbf"
export SG_PBF="$EXT/raw/osm_pbf/malaysia-singapore-brunei-latest.osm.pbf"

python scripts/prepare_osm_from_pbf.py --input_pbf "$BOS_PBF" \
  --bbox 42.30,-71.15,42.42,-70.98 \
  --output "$EXT/normalized/osm/boston_sidewalks.geojson" --overwrite \
  2>&1 | tee "$REPORTS/commands/osm_boston.log"

python scripts/prepare_osm_from_pbf.py --input_pbf "$PIT_PBF" \
  --bbox 40.38,-80.04,40.48,-79.88 \
  --output "$EXT/normalized/osm/pittsburgh_sidewalks.geojson" --overwrite \
  2>&1 | tee "$REPORTS/commands/osm_pittsburgh.log"

# 注意 Vegas 南界已经改成 36.055
python scripts/prepare_osm_from_pbf.py --input_pbf "$VEG_PBF" \
  --bbox 36.055,-115.23,36.20,-115.10 \
  --output "$EXT/normalized/osm/vegas_sidewalks.geojson" --overwrite \
  2>&1 | tee "$REPORTS/commands/osm_vegas.log"

python scripts/prepare_osm_from_pbf.py --input_pbf "$SG_PBF" \
  --bbox 1.27,103.75,1.33,103.82 \
  --output "$EXT/normalized/osm/singapore_sidewalks.geojson" --overwrite \
  2>&1 | tee "$REPORTS/commands/osm_singapore.log"
```

再做修复后的 alignment：

```bash
python scripts/validate_georeference_alignment.py \
  --config "$CONFIG" \
  --cities boston,pittsburgh,vegas,singapore \
  --min_map_covered_by_aoi 0.95 \
  --min_aoi_covered_by_osm 0.95 \
  --min_map_covered_by_osm 0.95 \
  --write_georeference \
  --report_json "$REPORTS/georeference_spatial_alignment.json" \
  2>&1 | tee "$REPORTS/commands/georeference_spatial_alignment.log"
```

**不要为了 PASS 降低阈值。** 若 Vegas 仍失败，优先核对 OSM PBF clip、新 config 和 map GPKG 是否一致。

---

## 7. 自动/半自动公共 GIS 准备

```bash
python scripts/fetch_recommended_public_sources.py \
  --config "$CONFIG" \
  --cities boston,pittsburgh,vegas,singapore \
  --strict \
  2>&1 | tee "$REPORTS/commands/fetch_recommended_public_sources.log"
```

该脚本会把 report 写到：

`$REPORTS/recommended_public_sources.json`

它的 PASS 表示“可自动获取的 topology/candidate/dynamic source 已成功准备”，**不是** paper source PASS。

### Pittsburgh payment points 单独复核

```bash
python scripts/inspect_tabular_coordinates.py \
  --input "$EXT/raw/wprdc/pittsburgh/payment_points_current.csv" \
  --min_valid_fraction 0.90 \
  --min_valid_rows 1000 \
  2>&1 | tee "$REPORTS/commands/pittsburgh_payment_points.inspect.log"

python scripts/normalize_accessibility_evidence.py \
  --input "$EXT/raw/wprdc/pittsburgh/payment_points_current.csv" \
  --output "$EXT/normalized/candidates/pittsburgh/payment_points_current.jsonl" \
  --profile pittsburgh_parking_meter \
  --source "Pittsburgh Parking Authority via WPRDC" \
  --skip_invalid \
  2>&1 | tee "$REPORTS/commands/pittsburgh_payment_points.normalize.log"
```

`--skip_invalid` 是这里的正确策略。

---

## 8. DEM：使用产品 metadata，而不是只看命令参数

### 8.1 U.S. 三城

USGS 3DEP 1 m standard tile 通常是 UTM/NAD83 + NAVD88；Seamless 1 m 是 NAD83(2011) / Conus Albers EPSG:6350 + NAVD88/GEOID18。**最终以每个 GeoTIFF header/metadata 为准**，不要硬写一个 CRS 覆盖 raster 自身 metadata。

Boston：

```bash
python scripts/validate_dem_tiles.py --config "$CONFIG" --city boston \
  --rasters "$EXT"/raw/dem/boston/*.tif --expected_resolution_m 1 --min_coverage 0.99 \
  --report_json "$REPORTS/dem_tiles_boston.json"

python scripts/sample_raster_dem.py --external_root "$EXT" --city boston \
  --rasters "$EXT"/raw/dem/boston/*.tif \
  --vertical_datum NAVD88 --source_name USGS_3DEP_1m --nominal_resolution_m 1 \
  --tile_validation_report "$REPORTS/dem_tiles_boston.json" --include_city_gis
```

Pittsburgh 同理。

Vegas 必须按新 AOI 在 National Map Downloader 重新搜/补 tile：

```text
west=-115.23
south=36.055
east=-115.10
north=36.20
```

然后：

```bash
python scripts/validate_dem_tiles.py --config "$CONFIG" --city vegas \
  --rasters "$EXT"/raw/dem/vegas/*.tif --expected_resolution_m 1 --min_coverage 0.99 \
  --report_json "$REPORTS/dem_tiles_vegas.json"

python scripts/sample_raster_dem.py --external_root "$EXT" --city vegas \
  --rasters "$EXT"/raw/dem/vegas/*.tif \
  --vertical_datum NAVD88 --source_name USGS_3DEP_1m --nominal_resolution_m 1 \
  --tile_validation_report "$REPORTS/dem_tiles_vegas.json" --include_city_gis
```

### 8.2 Singapore

现有 `COP-DEM_GLO-30-DGED`, tile `N01_E103` 可以继续用，前提是 validator 对 configured AOI coverage >=0.99。

```bash
python scripts/validate_dem_tiles.py --config "$CONFIG" --city singapore \
  --rasters "$EXT"/raw/dem/singapore/*.tif --expected_resolution_m 30 --min_coverage 0.99 \
  --report_json "$REPORTS/dem_tiles_singapore.json"

python scripts/sample_raster_dem.py --external_root "$EXT" --city singapore \
  --rasters "$EXT"/raw/dem/singapore/*.tif \
  --vertical_datum EGM2008 --source_name COPERNICUS_GLO30_DSM --nominal_resolution_m 30 \
  --tile_validation_report "$REPORTS/dem_tiles_singapore.json" --include_city_gis
```

**解释限制**：DEM-derived slope 是 terrain prior。不能把 30 m Copernicus slope 写成 ramp/curb 的精确 running slope/cross-slope；这些必须来自设施 GIS 或 manual audit。

---

## 9. Bootstrap preflight 与 sanity build

先准备 bootstrap fleet，仅用于 bring-up：

```bash
mkdir -p "$EXT/normalized/fleet"
cp "$CAP_HOME/configs/fleet.abilitybench.example.jsonl" \
   "$EXT/normalized/fleet/vehicle_interfaces.jsonl"
```

然后：

```bash
python scripts/validate_external_sources.py \
  --config "$CONFIG" \
  --cities boston,pittsburgh,vegas,singapore \
  --source_policy bootstrap \
  --output "$REPORTS/external.bootstrap.json" \
  2>&1 | tee "$REPORTS/commands/external.bootstrap.log"
```

Boston 小样：

```bash
python scripts/prepare_abilitybench_external.py \
  --config "$CONFIG" --split val --source_policy bootstrap \
  --cities boston --max_scenarios_per_city 10 \
  --stages map_crs,preflight,extract,graphs,pudo,service,dataset,merge \
  2>&1 | tee "$REPORTS/commands/bootstrap.val.boston10.log"
```

四城小样：

```bash
python scripts/prepare_abilitybench_external.py \
  --config "$CONFIG" --split val --source_policy bootstrap \
  --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 10 \
  --stages map_crs,preflight,extract,graphs,pudo,service,dataset,merge \
  2>&1 | tee "$REPORTS/commands/bootstrap.val.4city10.log"
```

新 pipeline 会同时把 compact diagnostics mirror 到：

`$REPORTS/build/val/`

重点看：

- `graph_source.*.json`
- `graph_quality.*.json`
- `graph_spatial.*.json`
- `pudo.*.json`
- `service.*.json`
- `dataset_quality.*.json`
- `dataset_diagnostics.*.json`

---

## 10. Manual PUDO + entrance audit：paper 最关键的一步

在 bootstrap pipeline 已生成 PUDO evidence 后，四城都导出：

```bash
for city in boston pittsburgh vegas singapore; do
  python scripts/export_pudo_audit_shortlist.py \
    --pudo_evidence_jsonl "$DATA_ROOT/outputs/prepared/val/pudo/${city}.jsonl" \
    --georeference_json "$EXT/georeference/${city}.json" \
    --city "$city" \
    --max_candidates_per_episode 4 \
    --dedup_radius_m 5 \
    --output_csv "$EXT/audits/${city}/pudo_audit_shortlist.csv" \
    --report_json "$REPORTS/manual_audit_shortlist.val.${city}.json"
done
```

### 10.1 每一条 paper PUDO 至少填什么

CSV 中必须人工/独立来源填：

- `auditor_id`
- `observed_at`（带时区 ISO timestamp）
- `curb_height_m`（若适用/可测）
- `sidewalk_width_m`
- `deployment_clearance_m`
- `curb_ramp`
- `legal_stop`
- `legal_basis`
- `entrance_id`
- `entrance_lon`, `entrance_lat`

建议同时记录：

- running slope / cross slope；
- surface；
- permanent obstruction；
- lighting / shelter（如果用于 wait contract）；
- source photo / official GIS object ID；
- measurement method / precision。

### 10.2 legality 必须独立于 candidate 语义

例如：

- parking meter 存在 ≠ ridehail PUDO 当前合法；
- taxi zone ≠ 任意 AV 可合法停靠；
- Passenger Pickup Bay 是很强的 candidate，但仍需要确认适用车辆/时段/限制；
- 仅 OSM `parking=*` / `kerb=*` 不足以作为 paper legality truth。

`legal_basis` 应引用 city/LTA rule、sign/curb regulation、official asset id 或现场 observation。

### 10.3 U.S. 几何检查可参考 PROWAG，但不要把它当本地 ridehail 法规

对“accessible passenger loading zone”的几何 audit，可把 PROWAG R311 作为标准化对照：pull-up space 至少约 2.44 m × 6.1 m，adjacent access aisle 至少约 1.525 m wide 并连接 pedestrian access route。但这是 accessibility design benchmark；本地停车/ridehail legality 仍要单独取证。

### 10.4 生成 paper layers

审核 CSV 填完后：

```bash
for city in boston pittsburgh vegas singapore; do
  python scripts/build_manual_audit_layers.py \
    --input_csv "$EXT/audits/${city}/pudo_audit_shortlist.csv" \
    --city "$city" \
    --external_root "$EXT" \
    --report_json "$REPORTS/manual_audit_layers.${city}.json"
done
```

不要使用 `--allow_anonymous_auditor` 做论文结果。

---

## 11. Paper fleet interface

bootstrap example 不能进入 paper main results。你需要用真实/可验证的 reference vehicle spec 替换：

`$EXT/normalized/fleet/vehicle_interfaces.jsonl`

至少要有：

- vehicle/interface ID；
- door side；
- ramp available；
- lift available；
- low floor / kneeling；
- usable door width；
- deployment clearance；
- supported audio/visual/haptic notification；
- dwell policy；
- source / manufacturer/operator document；
- `provided_interface_fields` 或等价完整性记录。

**不要声称 nuPlan 原采集车本身就是无障碍服务车辆。** 正确论文表述是：nuPlan 提供真实 traffic/log-play scene；AbilityBench-AV 为该 scene 绑定一个独立审计的 reference service vehicle interface。

---

## 12. Dynamic availability 与 2021 nuPlan 时间一致性

这是 paper reliability 容易被忽略的一点。

### 12.1 可作为历史 scene truth 的动态证据

- nuPlan 自己 scene timestamp 附近的 agent history / occupancy / traffic signal；
- 有 `valid_from/valid_to` 且包含该 scene timestamp 的 closure / construction record；
- 同期审计/历史影像（若许可和时间明确）。

### 12.2 不能直接当 2021 scene truth 的数据

- 2026 当前 street closure；
- 当前 parking/taxi availability；
- 当前 construction/obstacle；
- 2026 人工审计出来的 temporary blockage。

它们可用于：

1. static infrastructure verification；
2. dynamic counterfactual scenario source；
3. uncertainty robustness；

但不能被标成“该 2021 log 当时就发生了”。

### 12.3 建议动态实验分两种

- **historical log-play truth**：由 nuPlan agents + timestamp-matched records 得到；
- **controlled overlay**：明确标记 `counterfactual_dynamic_overlay=true`，模拟 temporary blockage/weather/lighting/construction，作为 robustness/T4/T5 controlled test。

---

## 13. Provenance / license freeze

```bash
cp "$CAP_HOME/data/external/schemas/provenance_registry.data0.paper.example.yaml" \
   "$EXT/provenance_registry.yaml"
```

然后人工把所有 placeholder 替换成准确值。每城至少登记：

- nuPlan version/terms；
- OSM/ODbL attribution；
- city/LTA GIS source URL；
- DEM product/version/license；
- manual audit manifest；
- reference fleet source；
- normalization method/version；
- retrieval timestamp。

生成：

```bash
mkdir -p "$EXT/manifests"
for city in boston pittsburgh vegas singapore; do
  python scripts/build_provenance_manifest.py \
    --registry "$EXT/provenance_registry.yaml" \
    --city "$city" \
    --output "$EXT/manifests/${city}.json" \
    2>&1 | tee "$REPORTS/commands/provenance.${city}.log"
done
```

如果仍有 `TODO/REVIEW/VERIFY`，现在会 fail；这是预期行为。

---

## 14. Paper external preflight

```bash
python scripts/validate_external_sources.py \
  --config "$CONFIG" \
  --cities boston,pittsburgh,vegas,singapore \
  --source_policy paper \
  --output "$REPORTS/external.paper.json" \
  2>&1 | tee "$REPORTS/commands/external.paper.log"
```

当前你上传的旧 `external.paper.json` 应当 FAIL，因为：

- Boston 缺 curb physical、legality、entrance、provenance；
- Pittsburgh/Vegas/Singapore 还额外缺 authoritative accessibility evidence。

**不要删除 gate 或降低到 bootstrap 来做论文主表。** 应把缺失 evidence 补齐。

---

## 15. 完整四城 train / val / test 构建

### 15.1 先做 paper pilot

Paper preflight PASS 后：

```bash
python scripts/prepare_abilitybench_external.py \
  --config "$CONFIG" --split val --source_policy paper \
  --cities boston+pittsburgh+vegas+singapore \
  --max_scenarios_per_city 20 \
  --stages preflight,extract,graphs,pudo,service,dataset,merge \
  2>&1 | tee "$REPORTS/commands/paper.val.4city20.log"
```

再：

```bash
python scripts/validate_dataset.py \
  --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_val" --strict

python scripts/audit_dataset_quality.py \
  --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_val" \
  --paper_mode --fail_if_not_publication_ready \
  --output "$REPORTS/dataset_quality.paper.val.json"
```

### 15.2 构建完整 matching scenarios

修复后 `--max_scenarios_per_city 0` = 所有符合该 split + map filter 的场景：

```bash
for split in train val test; do
  python scripts/prepare_abilitybench_external.py \
    --config "$CONFIG" --split "$split" --source_policy paper \
    --cities boston+pittsburgh+vegas+singapore \
    --max_scenarios_per_city 0 \
    --stages preflight,extract,graphs,pudo,service,dataset,merge \
    2>&1 | tee "$REPORTS/commands/paper.${split}.all.log"
done
```

但“all matching”不等于每个都能进入 paper main set。若某 episode 没有至少两个 paper-eligible PUDO，严格 paper build 应 fail/drop，而不是补默认属性。建议在完整候选构建后保留：

- `all_bootstrap_candidates`；
- `paper_eligible_subset`；

并在 report 中列出 excluded episode 和理由。

### 15.3 split leakage

必须至少保证：

- official nuPlan train/val/test DB 不交叉；
- 同一 episode 的 8 个 T4 capability profiles 必须都留在同一 split；
- paper manual-audit 站点如果被多个 episode 复用，最好按 physical PUDO/site ID group split，避免同一 curb site 横跨 train/val/test；
- 若 site-level disjoint 会让数据太少，至少报告 overlap 并把跨 split site 作为 secondary analysis，不要隐藏。

---

## 16. 合并为 CASA 训练用 canonical dataset

每个 split 单独 build 后，CASA 不能只拿 `abilitybench_av_train` 然后静默用 train 充当 val。现在 paper_mode 已经 fail-closed。

合并：

```bash
python scripts/merge_datasets.py \
  --input_dirs \
    "$DATA_ROOT/outputs/datasets/abilitybench_av_train" \
    "$DATA_ROOT/outputs/datasets/abilitybench_av_val" \
    "$DATA_ROOT/outputs/datasets/abilitybench_av_test" \
  --output_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" \
  --strict \
  2>&1 | tee "$REPORTS/commands/merge.abilitybench_av_all.log"

python scripts/validate_dataset.py \
  --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" --strict \
  2>&1 | tee "$REPORTS/commands/validate.abilitybench_av_all.log"

python scripts/audit_dataset_quality.py \
  --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" \
  --paper_mode --fail_if_not_publication_ready \
  --output "$REPORTS/dataset_quality.paper.all.json" \
  2>&1 | tee "$REPORTS/commands/audit.abilitybench_av_all.log"
```

---

## 17. CASA 训练：现在能做什么，不能把什么写进 paper

### 17.1 当前安全的 lightweight learned run

如果只把当前 relation-aware surrogate 作为“learned guidance engineering baseline”，可运行：

```bash
python scripts/train_casa.py \
  --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" \
  --output_dir "$DATA_ROOT/outputs/models/casa_relation_surrogate" \
  --epochs 50 --batch_size 256 --lr 1e-3 \
  --model_type hgt \
  --paper_mode \
  --phase_supervision \
  --predict_typed_demand \
  --predict_uncertainty \
  --predict_availability \
  --value_target skeleton \
  --profile_balanced_sampler \
  --action_balanced_sampler \
  --save_calibration_report \
  2>&1 | tee "$REPORTS/commands/train_casa_relation_surrogate.log"

mkdir -p "$REPORTS/model/casa_relation_surrogate"
cp "$DATA_ROOT/outputs/models/casa_relation_surrogate/"{config.json,val_metrics.json,calibration_report.json,train_metrics.jsonl} \
   "$REPORTS/model/casa_relation_surrogate/"
```

但 `model_type=hgt` 在当前代码中仍是 surrogate 名称；**论文不要写“我们实现了真正 HGT graph encoder”**。

### 17.2 若使用 offline-TSBS completion value

必须先离线生成：

`$DATASET/completion_value_labels.offline_tsbs.jsonl`

格式：

```json
{"transition_id":"...","passenger_id":"...","target":1.0}
```

每个训练 sample 都必须有 target。然后改：

```bash
--value_target offline_tsbs
```

否则 loader 现在会 fail，而不是偷偷退回 heuristic。

### 17.3 要完整支撑论文 CASA-Net，需要新增的 dataset objects

真正 heterogeneous graph training sample 至少要保存：

- node types: entrance / ped / curb / PUDO / vehicle / road / dynamic-agent / wait-state；
- edge types: ped-connect / curb-adjacent / candidate-service-transition / road-connect / agent-interaction / interface-connect；
- per-node raw features：geometry-relative pose、surface/width/slope/confidence、vehicle interface；
- per-edge raw features：distance、relative geometry、route relation、visibility；
- dynamic agents：history window + interaction features；
- phase observation/state history；
- capability tokens；
- labels **单独存储**，不进入 raw input slots。

然后使用真正 HGT/R-GCN/GNN message passing。当前 `candidate_transitions.jsonl` 已有 label/oracle 目标，但缺少足够的 raw graph observation tensor，不能靠把 oracle evidence 当 input 来替代。

### 17.4 phase head 的论文表述也要修正或补数据

当前 `y_phase = transition.to_phase`，而 transition schema 自己已包含 action/from/to semantic。因此它更像 transition phase semantic classification，不是从 passenger/service observations 识别隐式 service state。

要保留论文 `L_phase supervises passenger-service state recognition`，应新增时间序列 observation labels；否则建议把 paper 改成“symbolic automaton phase is known/maintained, phase auxiliary head predicts transition target phase semantics”。

---

## 18. T1–T5 如何由数据集逐一论证

### T1 Passenger-complete pickup/drop-off

需要：

- audited entrance；
- pedestrian path topology；
- width/slope/surface/curb ramp；
- PUDO legality + curb geometry + deployment clearance；
- vehicle interface。

指标：FLF、BAF、CVR、CSM、TT/detour。

### T2 Capability-aware ride planning

nuPlan ego/agent history提供真实 log-play dynamic context；你当前 code 已从 ego history 计算 peak acceleration / jerk / lateral acceleration 和 composite motion exposure。

**不要称为严格 ISO 2631 exposure**，除非后续实现频率加权、轴向权重、暴露时长等完整定义。当前更准确叫 `ISO-inspired / benchmark motion surrogate`。

若主张 trajectory optimization 改善 passenger motion，还必须做真正 nuPlan closed-loop planner rollout，不能只评分原 log ego trajectory。

### T3 End-to-end passenger complete

必须整条 skeleton：

`entrance -> access -> PUDO -> wait -> board -> ride -> alight -> PUDO -> egress -> destination entrance`

并同时满足 nuPlan vehicle safety metrics + typed contract。

### T4 Same-scene capability counterfactual

同一个：

- episode/traffic scene；
- O/D entrance；
- request time；
- vehicle interface；

只改变 capability contract。推荐至少覆盖：

- access distance；
- max slope；
- min width；
- step-free；
- ramp/lift；
- door/deploy clearance；
- ride motion；
- confidence threshold。

必须同时有 irrelevant change / reroute / re-interface / infeasible certificate 四类结果，否则 CRsp 很容易被单一方向样本夸大。

### T5 Failure certificate

infeasible episode 需要离线 independent verifier 给出：

- failed phase；
- transition；
- resource type；
- signed normalized margin；
- evidence source；
- confidence。

并单独报告 phase accuracy、resource/source macro-F1、signed margin MAE；不能只验证模型自己是否重复自己的 failure reason。

---

## 19. Evaluation：必须是真 nuPlan closed-loop 才能填 paper vehicle metrics

先导出/运行/导入 nuPlan closed-loop：

```bash
python scripts/run_nuplan_closed_loop_pipeline.py \
  --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_test" \
  --output_dir "$DATA_ROOT/outputs/eval/nuplan_test" \
  --stages export,run,import,eval \
  --nuplan_run_command '<YOUR_NUPLAN_SIM_COMMAND using {job_dir} {dataset_dir} {output_dir}>' \
  --casa_mode learned \
  --casa_checkpoint "$DATA_ROOT/outputs/models/casa_relation_surrogate/checkpoint.pt"
```

如果你先单独跑 nuPlan，再导入：

```bash
python scripts/run_closed_loop_eval.py \
  --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_test" \
  --output_dir "$DATA_ROOT/outputs/eval/capplan_full" \
  --trajectory_mode nuplan_closed_loop \
  --casa_mode learned \
  --casa_checkpoint "$DATA_ROOT/outputs/models/casa_relation_surrogate/checkpoint.pt" \
  --paper_mode \
  --import_nuplan_metrics_from '<NUPLAN_METRICS_FILE_OR_DIR>' \
  --vehicle_metrics "$REPORTS/eval/vehicle_metrics.full.json" \
  --passenger_metrics "$REPORTS/eval/passenger_metrics.full.json"
```

所有 ablation 都必须在同一 test episodes / vehicle simulation setup 上跑：

- full
- no_capability_compiler
- no_service_automaton
- no_casa_net_transitions
- no_typed_resource_ledger
- no_conservative_margins
- no_completion_value_guidance
- soft_only_capability

不要让不同方法使用不同 subset。

---

## 20. 论文结果最低统计要求

主表至少同时报告：

- CR / RC / TRV；
- PCR；
- TSPIR；
- CVR / CSM；
- FLF / BAF；
- MER/MVR（明确 surrogate 定义）；
- ECA；
- 95% bootstrap confidence interval（按 episode resample；T4 pair 应按 scene-group resample）。

额外强烈建议：

- 按 city 分层结果；
- 按 capability profile 分层；
- feasible/infeasible balance；
- failure phase diversity；
- evidence missingness；
- paper-eligible episode rate；
- PUDO site reuse / split leakage rate；
- T4 monotonicity violation rate；
- calibration coverage vs nominal beta/confidence。

---

## 21. 最终 publication gates

只有以下全部满足，才建议开始填论文主表：

1. `nuplan_db_cities.train/val/test.json`: PASS；
2. `georeference_spatial_alignment.json`: PASS，新 coverage semantics；
3. 4 个 `dem_tiles_*.json`: PASS，新 Vegas AOI；
4. `recommended_public_sources.json`: PASS/PARTIAL 已人工补全 required sources；
5. `external.bootstrap.json`: PASS；
6. 四城 manual audit manifests 存在且 non-empty；
7. 四城 provenance manifests 完成，禁止 placeholder；
8. reference fleet 是 verified，不是 example；
9. `external.paper.json`: PASS；
10. train/val/test dataset strict validation PASS；
11. `audit_dataset_quality --paper_mode --fail_if_not_publication_ready`: PASS；
12. T4 8-profile episode binding/counterfactual audit PASS；
13. CASA train/val 使用 canonical merged dataset，不允许 train-as-val；
14. paper feature policy 无 oracle leakage；
15. completion-value target 与命令声明一致；
16. nuPlan closed-loop metrics 已真实生成/导入；
17. paper main claims 不超过当前 learned CASA implementation 能力。

---

## 22. 每次运行后请打包给我复核的 reports

你不需要再打包整个 data。优先打包：

```text
$EXT/reports/
  nuplan_db_cities.train.json
  nuplan_db_cities.val.json
  nuplan_db_cities.test.json
  georeference_spatial_alignment.json
  dem_tiles_boston.json
  dem_tiles_pittsburgh.json
  dem_tiles_vegas.json
  dem_tiles_singapore.json
  recommended_public_sources.json
  external.bootstrap.json
  external.paper.json
  manual_audit_shortlist.*.json
  manual_audit_layers.*.json
  dataset_quality.*.json
  build/train/*
  build/val/*
  build/test/*
  model/*
  eval/*
  commands/*.log
```

另外请包含：

```text
$EXT/manifests/*.json
$EXT/audits/*/manual_audit_manifest.jsonl
```

如果 archive 太大，`commands/` 可以只保留 FAIL 命令和最后一次 PASS 命令。

---

## 23. 推荐执行顺序（最短可靠路径）

1. 安装本次 patch；
2. rsync 到 data0；
3. DB city + map CRS；
4. Vegas AOI 改为 36.055，重做 OSM；
5. 新 georeference alignment PASS；
6. `fetch_recommended_public_sources --strict`；
7. 重新补 Vegas DEM，四城 DEM validate + sample；
8. bootstrap preflight；
9. 4-city val × 10 sanity build；
10. export manual audit shortlist；
11. 做四城 PUDO + entrance physical/legality audit；
12. 换掉 example fleet；
13. 填 provenance registry 并生成四城 manifests；
14. `external.paper.json` 必须 PASS；
15. paper val × 20 pilot；
16. full train/val/test (`max_scenarios_per_city=0`)；
17. merge canonical dataset + strict audit；
18. learned model训练（先当 surrogate；若论文保留 graph claim，则实现真正 GNN）；
19. nuPlan true closed-loop test；
20. full/ablation/T4/T5 统计 + CI；
21. 打包 `$EXT/reports` 给我复核。

