# CapPlan / AbilityBench-AV 数据集构建（优化版，paper-oriented）

> 适用代码：本代码包当前版本。本文档替代旧 `docs/CapPlan-dataset-build.md` 中存在语义风险的步骤。
>
> 目标不是把所有缺失 GIS 字段“补齐”，而是：**真实证据有多少就使用多少，未知保持 unknown；只有具有独立 stopping-legality + pedestrian binding + board/alight interface 物理证据的 PUDO 才能进入 paper 主结果。**

---

## 0. 论文数据语义与本流程的判定标准

论文中的 passenger-complete episode 至少包含：nuPlan traffic scene、origin/destination entrance、pedestrian accessibility graph、PUDO、vehicle interface、capability contract，以及 transition/resource/skeleton/certificate labels。

本实现将外部数据分成三层：

1. **candidate/topology**：可以产生候选或拓扑，但不能自动证明可行性。例如 OSM footway、Pittsburgh payment point、Las Vegas Taxi Zone、Singapore Passenger Pickup Bay。
2. **auditable physical/regulatory evidence**：可以支持某个具体物理量或 stopping legality。例如 Boston official sidewalk/ramp GIS 的真实字段、人工量测、带证据的停车规则。
3. **paper_eligible PUDO**：必须同时满足：
   - `curb_height_m != None`
   - `sidewalk_width_m != None`
   - `deployment_clearance_m != None`
   - `adjacent_ped_node_id != None`
   - `legal_stop == True`
   - `legal_stop_source` 是独立证据，不能是 route/lane heuristic
   - 存在可审计 `curb_inventory_source` / `interface_evidence_source`
   - 不能来自 synthetic/mock/proxy 冒充 ground truth

**重要：全城候选层允许大量 unknown。unknown 不是错误；把 unknown 填成假的可行值才是错误。**

最终 paper quality gate 默认要求：

- 每个被计入 paper 主结果的 episode 至少 `2` 个 `paper_eligible` PUDO（使 pickup/drop-off 选择不退化为单点）；
- 至少 `80%` 的被审计数据集 episode 达到上述 PUDO gate；
- accessibility graph 每 episode 至少 `100 nodes / 150 edges`；
- failure certificates 至少覆盖 `2` 个不同 passenger-service failure phase；
- edge/skeleton 标签不能全部为正或全部为负，默认要求正样本率至少 `0.10`；
- schema、provenance、external-source preflight 全部通过。

这些阈值是“默认论文 gate”，不是自然法则；如果论文实验设计改为一个明确的 audited challenge subset，应在论文中声明 subset 生成规则，并相应修改 YAML 阈值，不能为了 PASS 任意放宽。

---

## 1. 一次性环境准备

假设：

```bash
export CAP_ROOT=/home/senzeyu2/code/CapPlan
cd "$CAP_ROOT"
```

系统工具：

```bash
sudo apt-get update
sudo apt-get install -y \
  osmium-tool \
  gdal-bin \
  sqlite3 \
  jq \
  curl
```

Python：

```bash
pip install -e .
pip install -r requirements.txt
pip install -r requirements-data.txt
```

确认关键工具：

```bash
osmium --version
ogr2ogr --version
python - <<'PY'
import pyproj, rasterio, yaml
print("pyproj", pyproj.__version__)
print("rasterio", rasterio.__version__)
print("env PASS")
PY
```

代码自身测试：

```bash
pytest -q
```

本修改版应看到全部测试通过。

---

## 2. 统一目录

现有目录可继续使用：

```text
$CAP_ROOT/data/
├── nuplan/
│   ├── nuplan-v1.1/splits/
│   │   ├── train_boston/*.db
│   │   ├── train_pittsburgh/*.db
│   │   ├── train_vegas/*.db
│   │   ├── train_singapore/*.db
│   │   ├── val/*.db
│   │   └── test/*.db
│   └── maps/
│       ├── nuplan-maps-v1.0.json
│       ├── us-ma-boston/9.12.1817/map.gpkg
│       ├── us-pa-pittsburgh-hazelwood/9.17.1937/map.gpkg
│       ├── us-nv-las-vegas-strip/9.15.1915/map.gpkg
│       └── sg-one-north/9.17.1964/map.gpkg
├── external/
│   ├── raw/
│   ├── normalized/
│   ├── georeference/
│   ├── audits/
│   ├── manifests/
│   └── reports/
└── outputs/
```

配置已经按该 split 布局修改为：

```yaml
train: [train_boston, train_pittsburgh, train_vegas, train_singapore]
val:   [val]
test:  [test]
```

**不要物理拆分 val/test DB。** 代码用 nuPlan `map_names`/数据库 location 检查城市，再由 scenario filter 按 map 筛选。

---

## 3. nuPlan DB 城市组成检查（train / val / test 都运行）

```bash
python scripts/inspect_nuplan_db_cities.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split train \
  --fail_on_unknown \
  --report_json data/external/reports/nuplan_db_cities.train.json

python scripts/inspect_nuplan_db_cities.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split val \
  --fail_on_unknown \
  --report_json data/external/reports/nuplan_db_cities.val.json

python scripts/inspect_nuplan_db_cities.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split test \
  --fail_on_unknown \
  --report_json data/external/reports/nuplan_db_cities.test.json
```

### PASS 标准

stdout 最后出现：

```text
NUPLAN_DB_CITY_CHECK=PASS
```

含义：每个 DB 中的 location/map metadata 都能够通过 YAML 的 `map_names + location_aliases` 映射到 Boston/Pittsburgh/Vegas/Singapore；同时每个配置的 `db_dir` 都确实贡献了 DB。脚本默认递归扫描，因此嵌套目录也支持。

若 FAIL：同时检查 JSON 的 `unknown_locations`、`db_dir_reports`、`empty_db_dirs` 与 `configured_cities_not_observed`；**不要猜城市，也不要复制 DB 到另一个城市目录。** nuPlan DB 常见 location 字符串与 map name 不完全相同，例如 `las_vegas` 对应配置城市键 `vegas`，应通过 `location_aliases` 显式映射。

---

## 4. nuPlan 四城 CRS：重新生成并保留 split-independent 设计

```bash
python scripts/inspect_nuplan_map_crs.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston+pittsburgh+vegas+singapore \
  --output_dir data/external/georeference
```

输出：

```text
data/external/georeference/boston.json
data/external/georeference/pittsburgh.json
data/external/georeference/vegas.json
data/external/georeference/singapore.json
```

脚本现在：

- 优先读取 nuPlan GPKG `meta.projectedCoordSystem`；
- 只在必要时 fallback 到 GeoPackage SRS registry；
- 用 `pyproj.CRS` 检查必须为 projected CRS；
- 明确区分：
  - `crs_metadata_validated=true`
  - `spatial_alignment_validated=false`

后者只有在真实 scene + external GIS 建图并验证 overlap 后才可认为通过。

检查：

```bash
for f in data/external/georeference/*.json; do
  echo "===== $f ====="
  jq '{map_name,local_crs,projected_map_frame,crs_metadata_validated,spatial_alignment_validated,map_gpkg}' "$f"
done
```

### PASS 标准

- 脚本无异常并输出 `status=PASS`；
- `local_crs` 可由 pyproj 解析且是 projected；
- `map_gpkg` 指向当前 `$CAP_ROOT/data/nuplan/maps/...`；
- 此时 `spatial_alignment_validated=false` **是正常状态**，不要手工改 true。

在第 5 节重新生成 OSM 后，执行第二阶段 gross spatial alignment：

```bash
python scripts/validate_georeference_alignment.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston,pittsburgh,vegas,singapore \
  --write_georeference \
  --report_json data/external/reports/georeference_spatial_alignment.json
```

要求 stdout 最后出现：

```text
GEOREFERENCE_SPATIAL_ALIGNMENT_CHECK=PASS
```

此检查会验证 configured AOI、nuPlan GPKG feature extent 和 prepared OSM extent 在同一 projected frame 下有实质重叠。PASS 后才会把 `spatial_alignment_validated` 写为 true。后续 per-episode accessibility graph 构建仍是更强的 scene-level alignment check。

---

## 5. OSM：必须用修订后的离线 PBF 流程重新生成

旧输出建议全部重建，因为旧代码会把 `sidewalk=*` 的 road centerline 误当成 pedestrian geometry。

修订后只保留真正的 pedestrian geometry/tag，例如：

```text
highway=footway/path/pedestrian/steps/crossing
footway=sidewalk/crossing/access_aisle
```

而 `sidewalk=yes/both/left/right/separate` 仅被认为是“道路旁存在 sidewalk 的属性”，不会把机动车道路中心线转成 pedestrian edge。

### 5.1 使用你现有的四个 PBF

把下面四个变量改成你实际文件名：

```bash
export BOS_PBF=data/external/raw/osm_pbf/massachusetts-latest.osm.pbf
export PIT_PBF=data/external/raw/osm_pbf/pennsylvania-latest.osm.pbf
export VEG_PBF=data/external/raw/osm_pbf/nevada-latest.osm.pbf
export SG_PBF=data/external/raw/osm_pbf/malaysia-singapore-brunei-latest.osm.pbf
```

重新生成：

```bash
python scripts/prepare_osm_from_pbf.py \
  --input_pbf "$BOS_PBF" \
  --bbox 42.30,-71.15,42.42,-70.98 \
  --output data/external/normalized/osm/boston_sidewalks.geojson \
  --overwrite

python scripts/prepare_osm_from_pbf.py \
  --input_pbf "$PIT_PBF" \
  --bbox 40.38,-80.04,40.48,-79.88 \
  --output data/external/normalized/osm/pittsburgh_sidewalks.geojson \
  --overwrite

python scripts/prepare_osm_from_pbf.py \
  --input_pbf "$VEG_PBF" \
  --bbox 36.07,-115.23,36.20,-115.10 \
  --output data/external/normalized/osm/vegas_sidewalks.geojson \
  --overwrite

python scripts/prepare_osm_from_pbf.py \
  --input_pbf "$SG_PBF" \
  --bbox 1.27,103.75,1.33,103.82 \
  --output data/external/normalized/osm/singapore_sidewalks.geojson \
  --overwrite
```

### PASS 标准

每次 stdout 的 JSON：

```text
status = PASS
pedestrian_line_features > 0
suspicious_carriageway_sidewalk_features = 0
```

只要最后一项非 0，脚本会直接失败，不允许该文件进入 paper pipeline。

### 5.2 如果重新下载

美国州级 PBF 可以使用 Geofabrik 对应州；Singapore 当前在 Geofabrik `Malaysia, Singapore, and Brunei` extract 中。

官方页面：

```text
https://download.geofabrik.de/
https://download.geofabrik.de/asia/malaysia-singapore-brunei.html
```

下载后仍然必须用上面的 bbox 做本地裁剪。

### 5.3 Overpass

**主流程跳过。**

Overpass 只保留为小 bbox sanity test；不应成为四城 paper-scale OSM 下载依赖。

---

## 6. 一键抓取/归一化目前可稳定自动化的官方公共层

```bash
python scripts/fetch_recommended_public_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston,pittsburgh,vegas,singapore
```

严格模式（任意可自动化源失败就退出非 0）：

```bash
python scripts/fetch_recommended_public_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston,pittsburgh,vegas,singapore \
  --strict
```

重新下载覆盖已有 raw：

```bash
python scripts/fetch_recommended_public_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --force
```

输出报告：

```text
data/external/reports/recommended_public_sources.json
```

### 脚本现在会抓什么

#### Boston

官方 ArcGIS Infrastructure/OpenData：

```text
Layer 0  Sidewalk Inventory
Layer 3  Ramp Inventory
Layer 5  Sidewalk Centerline
Layer 6  Curbs
```

保存到：

```text
data/external/raw/arcgis/boston/
data/external/normalized/city_gis/boston/
```

**修订规则：**

- `SWK_WIDTH` 不再同时写入 `deployment_clearance_m`；
- ramp/sidewalk 单位默认 unknown，除非显式提供已验证 unit；
- sidewalk polygon 是 physical inventory area，不当作 route centerline；
- Sidewalk Centerline 的 `SWALK-CL/CWALK-CL/PWALK-CL` 分别映射为 sidewalk/crossing/private_walk；
- curb line 不直接变 pedestrian edge。

#### Pittsburgh

自动抓：

```text
Sidewalks and Steps SHP
Current Payment Points
Payment Points (Archives)
Payments Points + Rates
DOMI Street Closures -> Street Closures
Allegheny County Addressing Address Points -> GeoJSON
```

其中：

- `Sidewalks and Steps SHP` 才进入 pedestrian topology；**Blockgroup sidewalk/street ratio 不再使用为 graph geometry**；
- Current Payment Point 只进入 PUDO candidate；
- Archives 保留 raw，供按历史 nuPlan 时间匹配；
- Rates 保留 raw，作为时间/停车上下文；
- Street Closures 是 dynamic overlay；
- Address Point 明确生成 `entrance_proxy`，不能作为 paper entrance ground truth。

**已经明确排除：**

```text
High Injury Network -> curb ramp        禁止
Zoning -> loading/loading zone          禁止
```

#### Las Vegas

抓官方 Taxi Zones，进入：

```text
data/external/normalized/candidates/vegas/taxi_zones.jsonl
```

它只提出 curb/PUDO candidate；不能自动赋 `legal_stop=true`。

#### Singapore

从 LTA DataMall Static Datasets 页面自动发现当前季度 ZIP 文件，不硬编码 `Mar2026` 文件名：

```text
Passenger Pickup Bay -> PUDO candidate
Taxi Stand           -> secondary PUDO candidate
Footpath             -> pedestrian topology
Kerbline             -> curb geometry
```

下载器单独使用：

```bash
python scripts/download_lta_static_geospatial.py \
  --dataset passenger_pickup_bay \
  --output_dir data/external/raw/lta/singapore

python scripts/download_lta_static_geospatial.py \
  --dataset footpath \
  --output_dir data/external/raw/lta/singapore

python scripts/download_lta_static_geospatial.py \
  --dataset kerbline \
  --output_dir data/external/raw/lta/singapore

python scripts/download_lta_static_geospatial.py \
  --dataset taxi_stand \
  --output_dir data/external/raw/lta/singapore
```

成功时：

```text
LTA_STATIC_DOWNLOAD=PASS
```

---

## 7. CKAN/WPRDC 下载失败时怎么处理

`download_ckan_resource.py` 已加入：

- `RemoteDisconnected`
- socket timeout
- connection reset
- HTTP/URL errors
- exponential backoff + jitter
- 精确 `--resource_name`
- 可指定 `--resource_id`
- ZIP/SHP 支持

查看一个 package 中真实资源名：

```bash
python scripts/download_ckan_resource.py \
  --portal https://data.wprdc.org \
  --package_id pittsburgh-parking-meters-and-payment-points \
  --list_resources
```

若 API 仍失败，手工下载并放到以下目录即可，之后 normalization 不要求文件由脚本下载。

### Pittsburgh 手工 fallback

#### A. Sidewalks and Steps

网站：

```text
https://data.wprdc.org/dataset/sidewalk-to-street-walkability-ratio
```

Data and Resources → **Sidewalks and Steps SHP**。

保存 ZIP：

```text
data/external/raw/wprdc/pittsburgh/sidewalks_and_steps.zip
```

归一化：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/wprdc/pittsburgh/sidewalks_and_steps.zip \
  --output data/external/normalized/city_gis/pittsburgh/sidewalks_steps.geojson \
  --profile pittsburgh_sidewalks_steps \
  --source "WPRDC Sidewalks and Steps"
```

#### B. Parking Payment Points

网站：

```text
https://data.wprdc.org/dataset/pittsburgh-parking-meters-and-payment-points
```

三个 CSV 都建议保留：

```text
Current Payment Points
Payment Points (Archives)
Payments Points + Rates
```

三个资源必须分别保存，不要互相覆盖：

```text
data/external/raw/wprdc/pittsburgh/payment_points_current.csv
data/external/raw/wprdc/pittsburgh/payment_points_archive.csv
data/external/raw/wprdc/pittsburgh/payment_points_rates.csv
```

**PUDO candidate 主输入固定使用 Current Payment Points**。Archive 仅用于历史/时间匹配，Rates 仅用于停车费率与时段上下文。

先验证 Current CSV 确实含可用 WGS84 坐标：

```bash
python scripts/inspect_tabular_coordinates.py \
  --input data/external/raw/wprdc/pittsburgh/payment_points_current.csv \
  --min_valid_fraction 0.90 \
  --min_valid_rows 1000
```

要求：

```text
TABULAR_COORDINATE_CHECK=PASS
```

再归一化：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/wprdc/pittsburgh/payment_points_current.csv \
  --output data/external/normalized/candidates/pittsburgh/payment_points_current.jsonl \
  --profile pittsburgh_parking_meter \
  --source "Pittsburgh Parking Authority via WPRDC" \
  --skip_invalid
```

自动下载优先使用官方 CKAN resource ID，并在 direct dump 被 WPRDC 关闭时尝试 `datastore_search` API fallback。Current resource ID 为 `9ed126cc-3c06-496e-bd08-b7b6b14b4109`。

`Current Payment Points` 中存在合法的非空间/虚拟 payment point 行，其 `latitude/longitude` 为空。它们不能被伪造坐标，也不应让整个 candidate layer 失败。`--min_valid_fraction 0.90 --min_valid_rows 1000` 用来确认“绝大多数且绝对数量足够”的空间点；normalizer 用 `--skip_invalid` 明确丢弃无坐标行并保留其统计。该图层仍只是 PUDO candidate cue，不是 `legal_stop=true` 的独立法规证据。

#### C. Street Closures

网站：

```text
https://data.wprdc.org/dataset/street-closures
```

资源：**Street Closures**（CSV）。

保存：

```text
data/external/raw/wprdc/pittsburgh/street_closures.csv
```

归一化：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/wprdc/pittsburgh/street_closures.csv \
  --output data/external/normalized/dynamic/pittsburgh/street_closures.jsonl \
  --profile pittsburgh_street_closure \
  --source "City of Pittsburgh DOMI Street Closures via WPRDC" \
  --skip_invalid
```

旧 `Right-of-Way Permits` 仍可作为 2002-07-01~2021-06-10 左右的 historical source，但不要替代现行 Street Closures；使用时必须按 episode 时间匹配。

#### D. Address Points

网站：

```text
https://data.wprdc.org/dataset/allegheny-county-addressing-address-points2
```

优先资源：**GeoJSON**。

保存：

```text
data/external/raw/pasda/pittsburgh/address_points.geojson
```

归一化：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/pasda/pittsburgh/address_points.geojson \
  --output data/external/normalized/candidates/pittsburgh/address_points.geojson \
  --profile pittsburgh_address_point \
  --source "Allegheny County Address Points via WPRDC"
```

注意：输出仍是 `entrance_proxy`；只能帮助 shortlist/搜索，不能通过 paper entrance gate。

---

## 8. SHP / ZIP / GPKG 输入支持

`normalize_accessibility_evidence.py` 现在原生支持：

```text
.geojson
.json
.jsonl
.csv
.shp
.gpkg
.zip (shapefile archive)
```

SHP/GPKG/ZIP 需要系统 `ogr2ogr`，脚本会转换到 WGS84 GeoJSON 再进行语义归一化。

多 layer GeoPackage 可指定：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input xxx.gpkg \
  --layer layer_name \
  --output out.geojson \
  --profile generic_city_gis \
  --source "SOURCE NAME"
```

---

## 9. Boston 原有数据如何重新处理

如果已经下载 raw Boston GeoJSON，**不用重新下载也可以**，但必须用新 normalization 重跑。

例如：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/arcgis/boston/sidewalk_inventory.geojson \
  --output data/external/normalized/city_gis/boston/sidewalk_inventory.geojson \
  --profile boston_sidewalk \
  --source "City of Boston Sidewalk Inventory"

python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/arcgis/boston/ramp_inventory.geojson \
  --output data/external/normalized/city_gis/boston/ramp_inventory.jsonl \
  --profile boston_ramp \
  --source "City of Boston Ramp Inventory"

python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/arcgis/boston/sidewalk_centerline.geojson \
  --output data/external/normalized/city_gis/boston/sidewalk_centerline.geojson \
  --profile boston_sidewalk_centerline \
  --source "City of Boston Sidewalk Centerline"

python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/arcgis/boston/curbs.geojson \
  --output data/external/normalized/city_gis/boston/curbs.geojson \
  --profile boston_curb \
  --source "City of Boston Curbs"
```

### 单位规则

默认：

```text
width_unit=unknown
reveal_unit=unknown
slope_unit=unknown
```

这是故意的。

如果你后续通过数据字典/City metadata/人工抽样确认某字段单位，再显式传入，例如：

```bash
--width_unit feet
--reveal_unit inches
--slope_unit percent
```

**不要根据数值大小自动判断 “0.5 是 ratio 还是 percent”。**

### Boston PASS 标准

每个 normalization stdout `status=PASS`；同时：

- ramp 输出 `deployment_clearance_m` 不能由 `SWK_WIDTH` 自动得到；
- 未确认单位的字段可以是 `None`；
- raw value 应被保留供 provenance/audit；
- sidewalk centerline/crosswalk 类型没有混成 curb/road。

---

## 10. DEM：美国三城手工下载 + 自动检测

### 10.1 USGS 下载

打开官方 The National Map Downloader：

```text
https://apps.nationalmap.gov/downloader/
```

分别使用以下 AOI：

```text
Boston:     west=-71.15 south=42.30 east=-70.98 north=42.42
Pittsburgh: west=-80.04 south=40.38 east=-79.88 north=40.48
Las Vegas:  west=-115.23 south=36.07 east=-115.10 north=36.20
```

操作：

1. Search/zoom 到城市；
2. 用 AOI/extent 工具覆盖上述 bbox；
3. 选 **Elevation Products (3DEP)**；
4. 优先看 **1 meter DEM / Seamless 1 Meter DEM (如果该 AOI 有覆盖)**；
5. 点 Show/availability；
6. 下载所有与 bbox 相交的 GeoTIFF tile；
7. 如果该区域无 1 m coverage，再使用 1/3 arc-second（约 10 m）作为 terrain prior；
8. 不要把 10 m fallback 仍标成 `USGS_3DEP_1m`。

落盘：

```text
data/external/raw/dem/boston/*.tif
data/external/raw/dem/pittsburgh/*.tif
data/external/raw/dem/vegas/*.tif
```

先检查 TIFF：

```bash
gdalinfo data/external/raw/dem/boston/xxx.tif | \
  egrep 'Coordinate System|Pixel Size|NoData|UNIT'
```

不需要先物理 mosaic。先验证多 tile 对整个 AOI 的实际 non-NoData coverage、CRS、分辨率与重叠情况：

```bash
python scripts/validate_dem_tiles.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --city boston \
  --rasters "$CAP_ROOT"/data/external/raw/dem/boston/*.tif \
  --expected_resolution_m 1 \
  --min_coverage 0.99 \
  --report_json data/external/reports/dem_tiles_boston.json
```

要求：

```text
DEM_TILE_CHECK=PASS
```

如需要一个统一入口用于 GIS 可视化，可额外传：

```bash
--build_vrt data/external/raw/dem/boston/boston_3dep.vrt
```

VRT 只引用原 TIFF，不复制高分辨率栅格。

采样：

```bash
python scripts/sample_raster_dem.py \
  --external_root "$CAP_ROOT/data/external" \
  --city boston \
  --rasters "$CAP_ROOT"/data/external/raw/dem/boston/*.tif \
  --vertical_datum NAVD88 \
  --source_name USGS_3DEP_1m \
  --nominal_resolution_m 1 \
  --tile_validation_report data/external/reports/dem_tiles_boston.json \
  --include_city_gis
```

Pittsburgh/Vegas 同理。

### DEM PASS 标准

默认要求 source vertices coverage >= 99%；否则脚本失败。

只在你明确接受 partial coverage 时才使用：

```bash
--allow_partial_coverage
```

输出还记录：

```text
source_resolution_x
source_resolution_y
source_resolution_unit
nominal_resolution_m
vertical_datum
```

防止 geographic-degree raster 被错误标成 meter resolution。

**DEM 只能作为 terrain elevation prior，不能作为 curb-ramp running slope / cross slope / curb reveal 的 ground truth。**

---

## 11. Singapore Copernicus DEM：具体选择

选：

```text
COP-DEM_GLO-30-DGED
```

当前官方文档给出的可查询完整 delivery 示例/版本：

```text
COP-DEM_GLO-30-DGED/2024_1
```

One-North bbox 全部位于：

```text
GRID ID = N01_E103
```

因为 bbox 是 `lat 1.27~1.33, lon 103.75~103.82`。

官方入口：

```text
https://browser.dataspace.copernicus.eu/
https://documentation.dataspace.copernicus.eu/Data/Others/CCM.html
```

截至 2026-07-28 起，GLO-30 View Service 要求账户成为 CCM authorised user；在 CDSE profile 中启用：

```text 
I am also interested in accessing Copernicus Contributing Missions data
```

搜索条件：

```text
Dataset: GCOP-DEM_GLO-30-DED / 2024_1
Grid ID: N01_E103
```

下载 DEM GeoTIFF/native DGED product；质量 mask 当前 pipeline 不强制。

落盘：

```text
data/external/raw/dem/singapore/*.tif
```

先验证完整 One-North AOI 覆盖；不要只下载 `103.7600~103.8100` 的手工裁剪，因为配置 AOI 是 `103.75~103.82`：

```bash
python scripts/validate_dem_tiles.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --city singapore \
  --rasters "$CAP_ROOT"/data/external/raw/dem/singapore/*.tif \
  --expected_resolution_m 30 \
  --min_coverage 0.99 \
  --report_json data/external/reports/dem_tiles_singapore.json
```

再采样：

```bash
python scripts/sample_raster_dem.py \
  --external_root "$CAP_ROOT/data/external" \
  --city singapore \
  --rasters "$CAP_ROOT"/data/external/raw/dem/singapore/*.tif \
  --vertical_datum EGM2008 \
  --source_name COPERNICUS_GLO30_DSM \
  --nominal_resolution_m 30 \
  --tile_validation_report data/external/reports/dem_tiles_singapore.json \
  --include_city_gis
```

注意它是 DSM，包含建筑/基础设施/植被表面；只作为大尺度 terrain prior。

---

## 11.5. Vehicle interface：bootstrap 示例与 paper 证据必须分开

YAML 指向：

```text
data/external/normalized/fleet/vehicle_interfaces.jsonl
```

仅为打通 **bootstrap**，可以：

```bash
mkdir -p data/external/normalized/fleet
cp configs/fleet.abilitybench.example.jsonl \
   data/external/normalized/fleet/vehicle_interfaces.jsonl
```

这两条示例车辆只用于工程 sanity check。修订后的 paper-mode 会拒绝 `abilitybench_example_fleet`、synthetic/proxy/unknown vehicle source。主结果必须换成量测或制造商/运营方核验的接口规格，并在每行 `source` / `metadata.source` 中写可审计来源；至少包括 `door_side, ramp, lift, low_floor, door_width_m, deployment_clearance_m, notification_modes, dwell_time_s, kneeling`。

这些字段还必须在 `fleet_jsonl` 中**显式出现**。修订后的 loader 会记录 `metadata.provided_interface_fields`，paper-mode 不再把 dataclass 默认值当作“已量测证据”。

---

## 12. Bootstrap preflight：先确认“真实数据主链路能跑”，不要求 paper 完整

```bash
python scripts/validate_external_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston,pittsburgh,vegas,singapore \
  --source_policy bootstrap \
  --output data/external/reports/external.bootstrap.json
```

### Bootstrap PASS 标准

```text
EXTERNAL_SOURCE_CHECK=PASS
```

每城至少：

```text
pedestrian topology 可用
georeference parseable/projected
```

缺 curb clearance、legal stop、verified entrance 等会产生 warning，而不是伪造值。

---

## 13. 先构建一个很小的 bootstrap sanity set

建议先 Boston 10 scenes：

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split val \
  --source_policy bootstrap \
  --cities boston \
  --max_scenarios_per_city 10 \
  --stages map_crs,preflight,extract,graphs,pudo,service,dataset,merge
```

如果 map CRS 已经生成，可省略 `map_crs`。

检查：

```bash
python scripts/validate_dataset.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_val_boston \
  --strict

python scripts/audit_dataset_quality.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_val_boston
```

### PASS 代表什么

Bootstrap PASS 只表示：

- nuPlan scene 可读取；
- GIS 与 scene 基本空间对齐；
- graph 非空且达到规模 gate；
- PUDO candidates 能生成；
- service/dataset schema 可构建；
- 没有 synthetic fallback 偷偷代替真实数据。

它**不等价于 publication-ready**。

---

## 13.5. T4 same-scene capability counterfactual 必须显式生成

修订后的配置默认使用：

```text
configs/capability_profiles.counterfactual.yaml
configs/demand.counterfactual.yaml
```

每个 episode 生成 1 个 base + 7 个 stricter variants，且强制共享完全相同的 `origin_entrance_id / destination_entrance_id / request_time_s / traffic scene`。七个轴为：

```text
access_distance
step_free
min_width
ramp_lift
door_side_clearance
ride_motion
confidence
```

最终 `counterfactual_pairs.jsonl` 必须每 episode 至少 7 对，并携带 `counterfactual_axis / counterfactual_group_id / weak_profile_id / strict_profile_id`；`validate_dataset.py` 会再次校验 pair 的 service-request O/D/time 一致性，`audit_dataset_quality.py --paper_mode` 会检查七轴覆盖。

质量 gate 不是只数“7 对”：修订后会对**每个 episode**分别检查七个 axis 是否齐全，避免“7 个 pair 其实重复同一个 axis”的假阳性 PASS。

---

## 14. Spatial alignment 的最终检查

`build_accessibility_graphs.py` 现在会在 successful GIS build 后把 alignment 诊断写入：

```text
data/outputs/prepared/<split>/reports/
  graph_spatial_diagnostics.<city>.json
  graph_source.<city>.json
  graph_quality.<city>.json
```

判定：

- graph 不是空 crop；
- per episode nodes >= 100；
- per episode edges >= 150；
- GIS transformed bounds 与 selected route/scene corridor 有实际 overlap；
- 没有大面积被 crop 到 0 的 city；
- `ACCESSIBILITY_GRAPH_CHECK=PASS`。

如果某城大量 episode graph 为空：**首先怀疑 CRS/map-frame，而不是增大 snap radius 掩盖错误。**

---

## 15. PUDO candidate 现在真正接入主流水线

以前 Pittsburgh payment points、Vegas taxi zones、Singapore taxi stands 虽然能生成文件，但没有真正传给主 `build_pudo_evidence.py`。

现在 YAML 的：

```yaml
pudo_candidate_sources:
```

会被 `prepare_abilitybench_external.py` 显式传入：

```text
--pudo_candidate_source ...
```

新逻辑：

```text
public candidate
  -> route-radius filter
  -> snap pedestrian node
  -> independent regulation match
  -> independent curb/interface inventory match
  -> paper flags
```

绝不会：

```text
Taxi Zone exists          => legal_stop=true      (禁止)
Payment Point exists      => legal_stop=true      (禁止)
sidewalk_width            => deployment_clearance (禁止)
route lane                => legal_stop=true      (禁止)
```

---

## 16. 导出“最值得人工审计”的 PUDO shortlist

在 bootstrap `pudo` stage 后，例如：

```text
data/outputs/prepared/val/pudo/boston.jsonl
```

导出：

```bash
python scripts/export_pudo_audit_shortlist.py \
  --pudo_evidence_jsonl data/outputs/prepared/val/pudo/boston.jsonl \
  --georeference_json data/external/georeference/boston.json \
  --city boston \
  --max_candidates_per_episode 4 \
  --dedup_radius_m 5 \
  --output_csv data/external/audits/boston/pudo_audit_shortlist.csv
```

Pittsburgh/Vegas/Singapore 同理。

成功：

```text
PUDO_AUDIT_SHORTLIST_CHECK=PASS
```

shortlist 会：

- 优先已绑定 pedestrian node 的候选；
- 优先公共 candidate layer；
- 将 5 m 内多个 episode 的同一 curb site 合并，减少人工工作；
- 转回 WGS84，方便地图/现场核验；
- 把 `episode_ids / anchor_ids / evidence_status` 一并写出；
- 物理量和 legality 留空，不自动“补真值”。

---

## 17. 人工 PUDO/entrance audit 的最小内容

模板：

```text
data/external/schemas/manual_audit_template.csv
```

或直接编辑上一节生成的 shortlist。

核心字段：

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

可选：

```text
running_slope
cross_slope
surface
photo_ref
```

### 入口必须独立观测

修订后的代码不允许再把 curb 经纬度复制成 entrance 经纬度。

若确实审计了一个建筑入口，则同时填写：

```text
entrance_id
entrance_lon
entrance_lat
```

如果没有独立入口观测：

```text
entrance_id 留空
```

不要用 address point 冒充 physical entrance ground truth。

### 转成正式 evidence layers

```bash
python scripts/build_manual_audit_layers.py \
  --input_csv data/external/audits/boston/pudo_audit_shortlist.csv \
  --city boston \
  --external_root data/external
```

输出：

```text
normalized/curb_inventory/boston.jsonl
normalized/curb_regulations/boston.jsonl
normalized/entrances/boston.geojson   # 仅当有独立 entrance coordinate
audits/boston/manual_audit_manifest.jsonl
```

注意：如果同一城市已有其他正式 curb inventory，需要先做 reviewed merge，避免该脚本覆盖你想保留的源。

---

## 18. Pittsburgh Smart Loading Zone 怎么处理

官方 Smart Loading Zones 页面可以作为 targeted PUDO audit 的参考，但当前未找到一个足够稳定、明确、可直接纳入流水线的官方 citywide CSV/GeoJSON 下载资源，因此本代码**没有把 Zoning 或其他 proxy 替代它**。

做法：

1. 从 bootstrap shortlist 找到真正靠近 nuPlan route 的少量 PUDO；
2. 对这些点查官方 Smart Loading Zone map / curb signage / ordinance；
3. 把核验结果写入 manual audit CSV 的 `legal_stop/legal_basis/photo_ref`；
4. 找不到可靠规则时保持 `legal_stop=False/unknown`，不强行制造正样本。

---

## 19. provenance：paper 前必须冻结来源、license 和 hash

模板：

```text
data/external/schemas/provenance_registry.example.yaml
```

复制并扩展成例如：

```bash
cp data/external/schemas/provenance_registry.example.yaml \
   data/external/provenance_registry.yaml
```

至少为最终用于 paper 的每城 source 写：

```text
role
path
source_url
license
retrieved_at
evidence_tier
authoritative
```

生成 manifest：

```bash
for city in boston pittsburgh vegas singapore; do
  python scripts/build_provenance_manifest.py \
    --registry data/external/provenance_registry.yaml \
    --city "$city" \
    --output "data/external/manifests/${city}.json"
done
```

脚本会 SHA256 每个实际文件；缺 URL/license/path 会失败。

---

## 20. Paper external-source preflight

当 audited curb/regulation/entrance/provenance 准备好后：

```bash
python scripts/validate_external_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston,pittsburgh,vegas,singapore \
  --source_policy paper \
  --output data/external/reports/external.paper.json
```

必须出现：

```text
EXTERNAL_SOURCE_CHECK=PASS
```

Paper policy 检查：

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

如果 Pittsburgh/Vegas/Singapore 暂时没有 verified entrance 或 enough audited curb evidence，paper preflight FAIL 是正确行为。可以先做 Boston-only paper pilot：

```bash
--cities boston
```

然后再扩城。

---

## 21. Paper-mode 小样本构建

建议先 val，每城少量 scenes：

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split val \
  --source_policy paper \
  --cities boston \
  --max_scenarios_per_city 20 \
  --stages preflight,extract,graphs,pudo,service,dataset,merge
```

如果通过，再逐城扩展：

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split val \
  --source_policy paper \
  --cities boston+pittsburgh+vegas+singapore \
  --max_scenarios_per_city 100 \
  --stages preflight,extract,graphs,pudo,service,dataset,merge
```

最后再构建 train/test。

---

## 21.5. Train/val/test 必须保留 nuPlan DB-set 原始 split

对真实 nuPlan 构建，不能在 `train/val/test` DB set 内再次随机切分。修订后的 `build_dataset.py` 会把当前 `--split train|val|test` 的 episode **全部且只**写入对应的 `splits/<split>_episodes.txt`，另两个文件留空；`merge_datasets.py` 只合并上游 split，不再用 fallback 人工复制 episode 到其他 split，并显式检查 overlap。

因此推荐分别运行：

```bash
python scripts/prepare_abilitybench_external.py --config configs/abilitybench_nuplan_real.yaml --split train --source_policy paper --stages extract,graphs,pudo,service,dataset,merge
python scripts/prepare_abilitybench_external.py --config configs/abilitybench_nuplan_real.yaml --split val   --source_policy paper --stages extract,graphs,pudo,service,dataset,merge
python scripts/prepare_abilitybench_external.py --config configs/abilitybench_nuplan_real.yaml --split test  --source_policy paper --stages extract,graphs,pudo,service,dataset,merge
```

主训练/验证/测试分别使用 `abilitybench_av_train / abilitybench_av_val / abilitybench_av_test`；不要把某一个目录内部再重新划分成三份。

---

## 22. Dataset schema + quality gate

城市 dataset：

```bash
python scripts/validate_dataset.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_val_boston \
  --strict
```

应出现：

```text
DATASET_SCHEMA_CHECK=PASS
```

质量审计：

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

应出现：

```text
ABILITYBENCH_DATASET_CHECK=PASS
```

### 关键报告字段

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
```

**candidate missingness 不再作为 paper fail 的直接理由。** 真正检查的是有没有足够 evidence-complete 的合法 interface 支撑主实验。

`paper_evidence_complete/paper_eligible` 必须来自 `scripts/build_pudo_evidence.py` 的显式 evidence audit。修订后的 paper-mode 不再因为 `curb_height_m/sidewalk_width_m/deployment_clearance_m` 三个数“恰好非空”就自行推断 publication eligibility；这避免缺少独立 legality/interface provenance 的 PUDO 假阳性。

---

## 23. 一条命令做总检查

Bootstrap：

```bash
python scripts/check_abilitybench_pipeline.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --source_policy bootstrap \
  --splits train,val,test \
  --report_json data/external/reports/pipeline.bootstrap.json
```

有构建好的 dataset 时：

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

这个总 gate 串联：

```text
nuPlan DB-city check
+ external source preflight
+ dataset schema
+ dataset quality / publication readiness
```

---

## 24. 推荐的实际执行顺序（从你现在的进度继续）

你已经下载 nuPlan DB、四个 OSM PBF、Boston GIS，因此最省事的顺序：

### Phase A：纠正旧输出

```text
1. 先把 nuPlan map package 改成官方 devkit hierarchy：maps/nuplan-maps-v1.0.json + maps/<map>/<version>/map.gpkg
2. 用新 inspect_nuplan_db_cities 检查 train/val/test
3. 用新 inspect_nuplan_map_crs 重建四城 georeference
4. 用新 validate_georeference_alignment 做真正的 CRS/extent overlap check
5. 四城 OSM PBF 可复用；必要时重跑 prepare_osm_from_pbf
6. 重新归一化 Singapore LTA Footpath/Kerbline（旧版曾把 JSONL 写成 .geojson）
7. Pittsburgh Current Payment Points 采用坐标质量 gate + --skip_invalid
```

### Phase B：补/纠正推荐公共层

```text
8. fetch_recommended_public_sources.py
9. 若 WPRDC/LTA 自动失败，按 report.manual_fallback 手工下载
10. USGS 用 YAML AOI 重新检查/补齐 Boston/Pittsburgh/Vegas tiles，先 validate_dem_tiles
11. Copernicus 下载完整 Singapore N01_E103 GLO-30 DGED，先 validate_dem_tiles
12. sample_raster_dem.py 必须传对应 PASS 的 --tile_validation_report
13. bootstrap 时准备 example fleet；paper 时替换为 audited fleet
```

### Phase C：bootstrap 数据集

```text
10. bootstrap external preflight
11. val 10 scenes/city sanity build
12. graph/PUDO/schema/quality report 检查
```

### Phase D：针对论文 idea 的少量高质量 audit

```text
13. export_pudo_audit_shortlist.py
14. 针对 selected PUDOs 核验 curb height / width / deployment clearance / ramp / stopping legality
15. 对 selected OD 入口做独立 entrance audit
16. build_manual_audit_layers.py
17. build provenance manifests
```

### Phase E：paper dataset

```text
18. paper external preflight
19. paper val/test curated subset
20. audit_dataset_quality --paper_mode
21. 扩大训练/评估数据，同时保留 unknown candidates 作为 uncertainty cases
```

---

## 25. 哪些数据明确不需要继续追

只要论文 claim 保持当前 passenger-complete / uncertainty 设计，下列内容可以不作为 blocker：

```text
全城 OpenSidewalks                  可选
Overpass 全城抓取                   不需要
Pittsburgh HIN 作 curb ramp         禁止
Pittsburgh zoning 作 loading zone   禁止
每个 candidate 都有完整 curb 几何   不需要
每个 episode 都没有 unknown         不应该追求
DEM 推导 curb-ramp 微坡度           禁止
Address point 直接作为 physical entrance ground truth  禁止
```

正确策略是：

```text
大规模真实 scene + topology/candidates + uncertainty
                +
小规模/中规模 targeted audited interface subset
```

这样既能训练/测试“未知证据如何处理”，又能在 audited subset 上证明 passenger-complete planning 与纯 traffic-safe planning 的区别。

---

## 26. 官方资料清单（2026-08 核验）

nuPlan：

```text
https://github.com/motional/nuplan-devkit
```

OpenStreetMap tagging：

```text
https://wiki.openstreetmap.org/wiki/Key:sidewalk
https://wiki.openstreetmap.org/wiki/Tag:footway%3Dsidewalk
```

Boston Infrastructure OpenData：

```text
https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer
```

WPRDC：

```text
https://data.wprdc.org/dataset/sidewalk-to-street-walkability-ratio
https://data.wprdc.org/dataset/pittsburgh-parking-meters-and-payment-points
https://data.wprdc.org/dataset/street-closures
https://data.wprdc.org/dataset/right-of-way-permits
https://data.wprdc.org/dataset/allegheny-county-addressing-address-points2
```

Las Vegas Taxi Zones：

```text
https://mapdata.lasvegasnevada.gov/clvgis/rest/services/Transportation/CLV_ParkingServices_ParkingZones/MapServer/4
```

Singapore LTA static data：

```text
https://datamall.lta.gov.sg/content/datamall/en/static-data.html
```

USGS：

```text
https://apps.nationalmap.gov/downloader/
https://www.usgs.gov/faqs/what-types-elevation-datasets-are-available-what-formats-do-they-come-and-where-can-i-download
```

Copernicus：

```text
https://browser.dataspace.copernicus.eu/
https://documentation.dataspace.copernicus.eu/Data/Others/CCM.html
https://dataspace.copernicus.eu/news/2026-7-17-copernicus-dem-30m-view-service-license-acceptance
```

---

## 27. 最终论文数据冻结前的硬检查

```bash
# 代码回归
pytest -q

# DB 城市
for s in train val test; do
  python scripts/inspect_nuplan_db_cities.py \
    --config configs/abilitybench_nuplan_real.yaml \
    --split "$s" \
    --fail_on_unknown
done

# 外部证据 paper preflight
python scripts/validate_external_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --source_policy paper

# 最终 dataset
python scripts/validate_dataset.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_val \
  --strict

python scripts/audit_dataset_quality.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_val \
  --paper_mode \
  --fail_if_not_publication_ready
```

只有上述全部 PASS，才把该 dataset version 标为 paper evaluation dataset。



---

## 2026-08 实际运行补丁说明

- `inspect_nuplan_db_cities.py` 现在支持 `location_aliases`，其中 `las_vegas -> vegas`；并对每个 `db_dir` 递归计数，避免 train_pittsburgh 空目录被其它城市 DB 数量掩盖。
- `validate_georeference_alignment.py` 是 CRS metadata 之后的第二阶段空间重叠验证；不要手工把 false 改 true。
- `normalize_accessibility_evidence.py` 现在规范化 CSV 表头，支持 `latitude/longitude`, `lat/lon`, `lng/long` 等显式 WGS84 别名，拒绝无 CRS 的普通 projected x/y 猜测，并能识别误下载的 HTML。
- Pittsburgh `Sidewalks and Steps` 必须是 LineString/MultiLineString；如果下载成 blockgroup/tract 统计几何，归一化会直接 FAIL。
- Pittsburgh Address Points 自动入口改为当前 PASDA Allegheny County ArcGIS layer 32，自动查询使用 `outSR=4326`。若使用 PASDA 静态 NAD83 GeoJSON 快照，可在 normalizer 中显式传 `--input_crs EPSG:4269`。
- USGS 多个 1m TIFF 不要求物理合并；先跑 `validate_dem_tiles.py`，采样器会稳定排序 tiles 并保留 `source_tile` provenance。
- `sample_raster_dem.py` 支持 `--tile_validation_report`；推荐把 AOI coverage PASS 作为采样前置条件，避免“少量 candidate points 全命中”造成假 PASS。
- `external_validation.py` 的 OSM semantic gate 不再只检查前 500 个 feature，避免 Vegas/Singapore 线要素排在后面时误判无 pedestrian topology。
- LTA `Footpath/Kerbline` 输出已改为真正 GeoJSON FeatureCollection；DEM candidate collector 同时识别 `{city}_sidewalks.geojson`。
- `download_arcgis_layer.py` 对超长 `objectIds` 自动改 POST，避免 PASDA/ArcGIS 大图层 GET 404/414。
- nuPlan map CRS 检查现在要求官方 manifest + devkit 目录结构；递归找到一个 GPKG 不再算 PASS。
- paper-mode 会拒绝 example/unverified fleet，并硬检查 T4 same-scene same-OD 七轴 counterfactual coverage。


### 2026-08-12 additional correctness fixes

17. 保留 nuPlan 官方 DB split，不在真实 train/val/test 内再次随机切分；merge 禁止 fallback 复制 episode。
18. paper-mode PUDO eligibility 只接受显式审计 flags，不再从“字段非空”弱推断。
19. paper-mode vehicle interface 要求核心字段在原始 fleet row 中显式提供，默认值不能充当证据。
20. counterfactual quality gate 对每个 episode 检查七个 axis，而不是只检查 pair 数量。
21. bootstrap quality audit 不再仅因为 `source_policy != paper` 而错误 FAIL；publication gate 仍只在 `--paper_mode` 下启用。
