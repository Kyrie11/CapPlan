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
│   └── maps/nuplan-maps-v1.0/...
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

含义：每个 DB 中的 location/map metadata 都能够映射到 YAML 中的 Boston/Pittsburgh/Vegas/Singapore map name。

若 FAIL：先看 JSON 的 unknown location；**不要猜城市，也不要复制 DB 到另一个城市目录。** 先确认实际 `log.location/map_version`。

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
export BOS_PBF=/path/to/your/boston_or_massachusetts_latest.osm.pbf
export PIT_PBF=/path/to/your/pittsburgh_or_pennsylvania_latest.osm.pbf
export VEG_PBF=/path/to/your/las_vegas_or_nevada_latest.osm.pbf
export SG_PBF=/path/to/your/singapore_region_latest.osm.pbf
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

Current 保存例如：

```text
data/external/raw/wprdc/pittsburgh/payment_points_current.csv
```

归一化：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/wprdc/pittsburgh/payment_points_current.csv \
  --output data/external/normalized/candidates/pittsburgh/payment_points_current.jsonl \
  --profile pittsburgh_parking_meter \
  --source "Pittsburgh Parking Authority via WPRDC"
```

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
data/external/raw/wprdc/pittsburgh/address_points.geojson
```

归一化：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/wprdc/pittsburgh/address_points.geojson \
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

采样：

```bash
python scripts/sample_raster_dem.py \
  --external_root "$CAP_ROOT/data/external" \
  --city boston \
  --rasters "$CAP_ROOT"/data/external/raw/dem/boston/*.tif \
  --vertical_datum NAVD88 \
  --source_name USGS_3DEP_1m \
  --nominal_resolution_m 1 \
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
Dataset: COP-DEM_GLO-30-DGED / 2024_1
Grid ID: N01_E103
```

下载 DEM GeoTIFF/native DGED product；质量 mask 当前 pipeline 不强制。

落盘：

```text
data/external/raw/dem/singapore/*.tif
```

然后：

```bash
python scripts/sample_raster_dem.py \
  --external_root "$CAP_ROOT/data/external" \
  --city singapore \
  --rasters "$CAP_ROOT"/data/external/raw/dem/singapore/*.tif \
  --vertical_datum EGM2008 \
  --source_name COPERNICUS_GLO30_DSM \
  --nominal_resolution_m 30 \
  --include_city_gis
```

注意它是 DSM，包含建筑/基础设施/植被表面；只作为大尺度 terrain prior。

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
1. 用新 inspect_nuplan_db_cities 检查 train/val/test
2. 用新 inspect_nuplan_map_crs 重建四城 georeference
3. 用新 prepare_osm_from_pbf 重建四城 OSM GeoJSON
4. Boston raw 用新 normalizer 重跑
```

### Phase B：补推荐公共层

```text
5. fetch_recommended_public_sources.py
6. 若 WPRDC/LTA 自动失败，按 report.manual_fallback 手工下载
7. USGS 手工下载 3DEP Boston/Pittsburgh/Vegas
8. Copernicus 手工下载 Singapore N01_E103 GLO-30 DGED
9. sample_raster_dem.py
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

