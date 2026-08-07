> **2026-08 数据语义修订：** 用于论文主实验时请优先执行 [`CapPlan-dataset-build-optimized.md`](CapPlan-dataset-build-optimized.md)。优化版修复了 OSM carriageway/sidewalk 混淆、PUDO legality/interface proxy、Boston deployment-clearance、train/val/test split、Pittsburgh/LTA 数据源和 paper PASS/FAIL gates。

# CapPlan / AbilityBench-AV 数据集准备与复现实验指南

> 适用项目路径：`/home/senzeyu2/code/CapPlan`  
> 统一数据根目录：`/home/senzeyu2/code/CapPlan/data`  
> 核查日期：2026-08-06

## 0. 结论先行

你现有方案可以作为 **bootstrap / 代码连通性测试方案**，但还不足以支撑论文的主结论。主要原因不是“GeoJSON 数量不够”，而是以下关键证据仍不闭环：

1. OSM/普通道路地图能提供步行拓扑候选，但不能自动成为路缘高度、坡度、净宽、部署空间和停车合法性的真值。
2. 将 OSM 导出文件命名为 `opensidewalks/*.geojson` 不等于它符合 OpenSidewalks Schema；正式使用前必须通过 OSW validator，或明确保留为普通 OSM GeoJSON。
3. DEM 只能提供地形先验。1 m DEM 尚且不能直接替代路缘坡道纵坡、横坡、平台坡度和路缘高差测量；30 m DSM 更不能作为这些属性的论文级真值。
4. 停车计费点、出租车站、乘客上下客湾都只能生成 PUDO 候选。它们对自动驾驶服务类别是否允许停车、何时允许、哪一侧开门，必须单独做法规/标志/现场审计。
5. 论文要比较同场景不同 capability contract，因此数据集必须以“原始交通场景”为分组单位生成多个反事实合同，并保证这些反事实不跨 train/val/test 泄漏。
6. 静态 GIS 无法证明临时施工、遮挡、天气、照明变化和临时停车冲突。此类变量应作为明确标注的 controlled synthetic overlay 或来自有时间戳的动态来源，不能伪装成实测真值。

因此建议把数据集分成两级：

- **Bootstrap 级**：nuPlan 真场景 + OSM/城市步行拓扑；缺失属性保持 unknown，并按 fail-closed 处理。用于调试、消融流程、快速 sanity check，不用于论文主表结论。
- **Paper 级**：在 Bootstrap 基础上加入真实 CRS、入口、物理路缘/人行道测量、停车法规、车辆接口、来源许可和人工审计；通过 `paper` preflight 后才进入论文主实验。

---

## 1. 从论文反推数据集必须包含什么

论文中的单个 benchmark episode 为：

```text
D_i = (S_i, o_i, d_i, P_i, V_i, K_i, Y_i)
```

对应到可落地数据：

| 组成 | 含义 | 必备内容 |
|---|---|---|
| `S_i` | nuPlan 交通场景 | ego/agents、路线、道路语义、交通灯、时间、map name、log name、闭环评测入口 |
| `o_i, d_i` | 起终点入口 | 入口点坐标、入口类别、可达侧、置信度、来源、时间戳；不能只用建筑质心代替 |
| `P_i` | 步行与路缘可达图 | sidewalk/crossing/footway/entrance/curb 节点边；宽度、坡度、横坡、表面、台阶、坡道、障碍、照明、遮蔽、时间戳、置信度 |
| PUDO | 上下客候选 | curb geometry、停车合法性、相邻人行空间宽度、部署净空、车门侧、动态占用、证据置信度 |
| `V_i` | 车辆接口 | 门侧、门宽、低地板、坡板/升降机、部署空间、kneeling、通信模态、dwell policy |
| `K_i` | capability contract | 最大 access/egress 距离、step-free、最小宽度、最大坡度/横坡、坡板/升降机需求、等待暴露、识别模态、运动/jerk 预算、最小证据置信度 |
| `Y_i` | 标签 | transition validity、typed resource 值、可行 skeleton、失败 phase/resource/source、signed margin |

### 1.1 为论文五类实验任务准备对应子集

1. **T1：PUDO 与 first/last meter**  
   需要同一场景存在多个候选 curb，并在距离、坡度、宽度、台阶、路缘坡道、等待位置和部署空间上有差异。

2. **T2：capability-aware ride**  
   需要真实或可复现的车辆运动轨迹，以及 acceleration、braking、jerk、motion exposure 标签。仅有 GIS 不够。

3. **T3：端到端 passenger-complete**  
   必须把 entrance → access → wait → board → ride → alight → egress 全链连起来；任何一段缺失都不能标为成功。

4. **T4：same-scene counterfactual**  
   每个原始 scene 建议配 6–12 个合同，包括：宽松、严格、单变量变化、接近可行边界以及明确不可行版本。

5. **T5：failure certificate**  
   必须由独立 verifier 生成或复核失败 phase、resource type、evidence source 和 signed margin；不能直接拿规划器自己的输出来当标签。

### 1.2 论文主张与必须出现的对照样本

| 要论证的主张 | 数据中必须出现的样本 |
|---|---|
| traffic-safe 不等于 passenger-complete | 车辆安全到达但入口路径、路缘、接口或 egress 不可行的场景 |
| accessibility-only 不够 | 步行路径可行但坡板、门宽、门侧、部署空间或 ride motion 不可行的场景 |
| capability 不能只做软偏好 | 效率更高但违反硬阈值的近边界候选，与稍绕行但严格可行候选 |
| conservative uncertainty 有必要 | 同一设施有高/低置信证据或缺失字段，严格合同应拒绝低置信方案 |
| failure certificate 可复现 | 至少覆盖 access、wait、board、ride、alight、egress 各类主要失败原因 |

---

## 2. 推荐的数据量、分层和划分

以下是工程起点，不替代正式统计功效分析：

### 2.1 三阶段规模

1. **Smoke test**：每城 20–50 个 nuPlan 场景，3–4 个合同/场景。只验证流程与格式。
2. **Pilot**：每城 200–300 个独立 scene，6–8 个合同/scene，约 5,000–10,000 个 scene-contract episode。
3. **论文主实验**：建议至少 2,000–4,000 个独立 scene，四城均衡或按可用场景分层；每 scene 6–12 个合同。最终数量应根据 PCR、CVR、DF 等核心指标的置信区间和预期效果量做功效分析。

### 2.2 人工审计子集

建议每城先审计 150–300 个 PUDO/入口组合，并覆盖：

- 核心商业区、住宅区、医院/交通站点、酒店/大型设施；
- 正反例：可部署/不可部署、合法/不合法、有坡道/无坡道；
- 路面与坡度差异；
- 10%–20% 双人独立标注，用于计算一致性；
- 另留一部分完全不参与阈值调参的 held-out audit set，用于检查 GIS 推断与 confidence calibration。

### 2.3 防止数据泄漏

- 以 `nuPlan log_name` 为最小分组划分 train/val/test，而不是按 episode 随机切行。
- 同一个 scene 的所有 capability contracts、车辆接口变体、动态 overlay 必须进入同一 split。
- 同一路口/同一路段的相邻时间片尽量放在同一 split。
- 除常规随机城市内划分外，增加一次 cross-city held-out：例如训练三城、测试第四城。

修改后的 `build_dataset.py` 已改为按 `log_name` 稳定分组，并输出 split manifest。

---

## 3. 现有数据准备方案审查

### 3.1 合理的部分

- 已识别 OSM、OpenSidewalks、city GIS、curb inventory、entrance、DEM 和 georeference 等主要来源类型。
- 已意识到 paper-scale 不适合通过公共 Overpass 批量抓整座城市。
- 已提供 Shapefile/GeoPackage 转 GeoJSON 的思路。
- 已区分四个 nuPlan 地区。

### 3.2 需要修正的部分

1. **目录只按文件类型，不区分 raw / normalized / audit / provenance**。  
   一旦下载源更新或字段映射出错，无法追溯。

2. **要求每城同时有 OSM 和 OpenSidewalks**。  
   这两个是可替代/互补的拓扑来源，不应为了“文件齐全”把 OSM 复制成伪 OSW。

3. **没有定义字段语义和证据等级**。  
   `curb_ramp=true` 不等于已经知道坡度、平台尺寸和 curb reveal；parking meter 也不等于合法 PUDO。

4. **georeference 模板可能被误用为真实对齐**。  
   应从每个 nuPlan map GPKG 的 `meta.projectedCoordSystem` 或 GeoPackage SRS 表生成，并检查空间重叠。

5. **执行顺序有误**。  
   原 `执行指令.txt` 的第 1、2 步完全相同，而且未执行 `pudo,service,dataset,merge` 就审计不存在的最终数据集。

6. **下载失败没有 fail-fast**。  
   `prepare.txt` 的 `JSONDecodeError: line 1 column 1` 典型表示文件为空、返回 HTML 登录页/403/429、或扩展名为 JSON 但内容并非 JSON。

7. **没有法规、车辆接口、人工审计、动态 overlay、许可和校验和**。  
   这些都是论文端到端论证不可缺的组成。

---

## 4. 新目录结构

运行：

```bash
cd /home/senzeyu2/code/CapPlan
python scripts/create_data_layout.py
```

生成的核心结构：

```text
data/
├── nuplan/
│   ├── nuplan-v1.1/splits/...
│   └── maps/nuplan-maps-v1.0/...
├── external/
│   ├── raw/
│   │   ├── osm_pbf/
│   │   ├── osm_overpass/
│   │   ├── arcgis/{boston,pittsburgh,vegas,singapore}/
│   │   ├── wprdc/pittsburgh/
│   │   ├── lta/singapore/
│   │   ├── onemap/singapore/
│   │   ├── dem/{boston,pittsburgh,vegas,singapore}/
│   │   └── manual/{boston,pittsburgh,vegas,singapore}/
│   ├── normalized/
│   │   ├── osm/
│   │   ├── opensidewalks/
│   │   ├── city_gis/{boston,pittsburgh,vegas,singapore}/
│   │   ├── curb_inventory/
│   │   ├── curb_regulations/
│   │   ├── entrances/
│   │   ├── dem/
│   │   ├── fleet/
│   │   └── dynamic_overlays/
│   ├── audits/{boston,pittsburgh,vegas,singapore}/
│   ├── georeference/
│   ├── manifests/
│   ├── reports/
│   └── schemas/
└── outputs/
    ├── prepared/
    ├── datasets/
    └── nuplan_closed_loop_jobs/
```

### 4.1 三个证据等级

- **A：authoritative / audited**：市政 GIS、正式法规、实地测量、经审核车辆规格。
- **B：community mapped**：OSM、经验证的 OSW；可用于拓扑和候选，但缺失字段保持 unknown。
- **C：inferred / proxy**：DEM 推坡度、建筑质心入口、停车点邻近 curb 等；必须记录推断方法和 uncertainty，不能直接当 ground truth。

---

## 5. 安装依赖

```bash
cd /home/senzeyu2/code/CapPlan

sudo apt-get update
sudo apt-get install -y \
  curl jq unzip gdal-bin osmium-tool sqlite3

# 使用你当前能运行 nuPlan 的 capplan 环境；不要同时混装多套相互冲突的 nuPlan 依赖。
pip install -e .
pip install -r requirements.txt
pip install -r requirements-data.txt
```

可选：只有拿到真正 OSW 数据时安装 validator：

```bash
pip install python-osw-validation
```

---

## 6. Step-by-step：从零到可运行数据集

## Step 1：人工下载并放置 nuPlan DB 与地图

nuPlan 官方数据需要登录/注册后下载，不建议写脚本绕过登录。官方仓库说明完整数据约 1,300 小时、15,000+ logs，覆盖 Las Vegas、Pittsburgh、Boston 和 Singapore。

下载后保持官方结构，至少需要：

```text
/home/senzeyu2/code/CapPlan/data/nuplan/nuplan-v1.1/splits/trainval/*.db
/home/senzeyu2/code/CapPlan/data/nuplan/maps/nuplan-maps-v1.0/.../*.gpkg
```

检查：

```bash
find data/nuplan/nuplan-v1.1/splits -name '*.db' | head
find data/nuplan/maps -name '*.gpkg' | head
```

对应 map names 已写入配置：

```text
Boston      us-ma-boston
Pittsburgh  us-pa-pittsburgh-hazelwood
Las Vegas   us-nv-las-vegas-strip
Singapore   sg-one-north
```

## Step 2：从真实 nuPlan GPKG 生成四城 CRS

不要直接使用旧的 UTM 示例文件。运行：

```bash
python scripts/inspect_nuplan_map_crs.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston+pittsburgh+vegas+singapore
```

输出：

```text
data/external/georeference/boston.json
data/external/georeference/pittsburgh.json
data/external/georeference/vegas.json
data/external/georeference/singapore.json
```

如果脚本找不到 GPKG，先检查 nuPlan 地图目录是否被多套了一层目录；不要手工猜 EPSG 继续跑。

## Step 3：下载 OSM PBF，离线裁剪四个场景区域

### 3.1 手工下载

在浏览器或 `wget` 中下载以下 `.osm.pbf`：

```text
Boston:
https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf

Pittsburgh:
https://download.geofabrik.de/north-america/us/pennsylvania-latest.osm.pbf

Las Vegas:
https://download.geofabrik.de/north-america/us/nevada-latest.osm.pbf

Singapore:
https://download.geofabrik.de/asia/malaysia-singapore-brunei-latest.osm.pbf
```

放到：

```text
data/external/raw/osm_pbf/massachusetts-latest.osm.pbf
data/external/raw/osm_pbf/pennsylvania-latest.osm.pbf
data/external/raw/osm_pbf/nevada-latest.osm.pbf
data/external/raw/osm_pbf/malaysia-singapore-brunei-latest.osm.pbf
```

建议同时下载 `.md5` 并校验。论文冻结数据时不要持续使用 `latest`；把下载日期和 SHA256 写入 provenance manifest。

### 3.2 离线裁剪与过滤

```bash
python scripts/prepare_osm_from_pbf.py \
  --input_pbf data/external/raw/osm_pbf/massachusetts-latest.osm.pbf \
  --bbox 42.30,-71.15,42.42,-70.98 \
  --output data/external/normalized/osm/boston_sidewalks.geojson \
  --overwrite

python scripts/prepare_osm_from_pbf.py \
  --input_pbf data/external/raw/osm_pbf/pennsylvania-latest.osm.pbf \
  --bbox 40.38,-80.04,40.48,-79.88 \
  --output data/external/normalized/osm/pittsburgh_sidewalks.geojson \
  --overwrite

python scripts/prepare_osm_from_pbf.py \
  --input_pbf data/external/raw/osm_pbf/nevada-latest.osm.pbf \
  --bbox 36.07,-115.23,36.20,-115.10 \
  --output data/external/normalized/osm/vegas_sidewalks.geojson \
  --overwrite

python scripts/prepare_osm_from_pbf.py \
  --input_pbf data/external/raw/osm_pbf/malaysia-singapore-brunei-latest.osm.pbf \
  --bbox 1.27,103.75,1.33,103.82 \
  --output data/external/normalized/osm/singapore_sidewalks.geojson \
  --overwrite
```

该脚本输出普通 `osm_geojson`，不会伪装成 OpenSidewalks。

### 3.3 Overpass 只用于小样本备选

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split train \
  --stages queries,download \
  --cities boston \
  --source_policy bootstrap
```

下载过程先写 `.part`，会拒绝空文件、HTML、403/429 错误页，然后转换为标准 GeoJSON。不要用公共 Overpass 抓四座完整城市。

## Step 4：Boston 官方 GIS

Boston 是四城中最适合先做 paper pilot 的城市。官方 ArcGIS 提供 Sidewalk Inventory、Ramp Inventory、Sidewalk Centerline、Curbs 等图层；Sidewalk Inventory 包含 `SWK_WIDTH`、`SWK_SLOPE`、材料和检查日期等字段。

### 4.1 下载

```bash
python scripts/download_arcgis_layer.py \
  --layer_url https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/0 \
  --bbox 42.30,-71.15,42.42,-70.98 \
  --output data/external/raw/arcgis/boston/sidewalk_inventory.geojson

python scripts/download_arcgis_layer.py \
  --layer_url https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/3 \
  --bbox 42.30,-71.15,42.42,-70.98 \
  --output data/external/raw/arcgis/boston/ramp_inventory.geojson

python scripts/download_arcgis_layer.py \
  --layer_url https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/5 \
  --bbox 42.30,-71.15,42.42,-70.98 \
  --output data/external/normalized/city_gis/boston/sidewalk_centerline.geojson

python scripts/download_arcgis_layer.py \
  --layer_url https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/6 \
  --bbox 42.30,-71.15,42.42,-70.98 \
  --output data/external/normalized/city_gis/boston/curbs.geojson
```

### 4.2 保守归一化

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/arcgis/boston/sidewalk_inventory.geojson \
  --output data/external/normalized/city_gis/boston/sidewalk_inventory.geojson \
  --profile boston_sidewalk \
  --source 'City of Boston Sidewalk Inventory'

python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/arcgis/boston/ramp_inventory.geojson \
  --output data/external/normalized/city_gis/boston/ramp_inventory.jsonl \
  --profile boston_ramp \
  --source 'City of Boston Ramp Inventory' \
  --skip_invalid
```

注意：Boston Ramp Inventory 可以证明“已登记坡道位置/条件”，但若某些记录没有实测 reveal、坡度、净宽，则不能直接充当 `curb_inventory/boston.jsonl` 的完整物理真值。论文级 `curb_inventory` 仍由 Step 8 的人工审计生成或由确实包含这些字段的官方测量表生成。

## Step 5：Pittsburgh 数据

目前公开且稳定可自动下载的 WPRDC “Parking Meters and Payment Points” 含位置与坐标，可用于生成 curb/PUDO 候选；它不是自动驾驶停车合法性真值。Pittsburgh DOMI 管理 sidewalk/curb/right-of-way，并公开标准、施工和部分地图，但未核实到一个能覆盖 Hazelwood nuPlan 区域且包含全部物理坡道字段的统一公开图层。因此 paper 级物理属性需人工审计或向 DOMI/WPRDC 索取数据。

### 5.1 使用 CKAN API 下载，避免抓 HTML 页面

```bash
python scripts/download_ckan_resource.py \
  --portal https://data.wprdc.org \
  --package_id pittsburgh-parking-meters-and-payment-points \
  --prefer_formats CSV \
  --output_dir data/external/raw/wprdc/pittsburgh \
  --output_name parking_payment_points.csv
```

如果包内有多个 CSV，先访问该数据集页面查看 `resource_id`，再增加：

```text
--resource_id <复制的资源 UUID>
```

归一化为候选：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/wprdc/pittsburgh/parking_payment_points.csv \
  --output data/external/normalized/city_gis/pittsburgh/parking_payment_points.jsonl \
  --profile pittsburgh_parking_meter \
  --source 'Pittsburgh Parking Authority payment points' \
  --skip_invalid
```

### 5.2 手工搜索/联系内容

在 Pittsburgh DOMI Maps、WPRDC 和 OneStop/obstruction map 中搜索：

```text
sidewalk inventory
ADA curb ramp
curb ramp upgrade
right-of-way obstruction
loading zone
smart loading zone
street closure / construction permit
building entrance / address points
```

下载优先格式：GeoJSON、GeoPackage、Shapefile ZIP、CSV with lon/lat。所有文件放在：

```text
data/external/raw/wprdc/pittsburgh/
```

再转换到：

```text
data/external/normalized/city_gis/pittsburgh/
```

DOMI 的设计标准只能说明新建设施应满足的标准，不能证明现状每个 curb 实际满足标准。

## Step 6：Las Vegas / Clark County 数据

nuPlan 的 map 是 `us-nv-las-vegas-strip`。Strip 的主要路段（尤其 Sahara 以南）由 Clark County 管理，因此只下载 City of Las Vegas 图层会产生明显覆盖偏差。

### 6.1 City Taxi Zones 作为候选层

官方 Taxi Zones 图层说明其位置多为近似数字化，且服务类别是 taxi；因此只能当 PUDO 候选。

```bash
python scripts/download_arcgis_layer.py \
  --layer_url https://mapdata.lasvegasnevada.gov/clvgis/rest/services/Transportation/CLV_ParkingServices_ParkingZones/MapServer/4 \
  --bbox 36.07,-115.23,36.20,-115.10 \
  --output data/external/raw/arcgis/vegas/taxi_zones.geojson

python scripts/normalize_accessibility_evidence.py \
  --input data/external/raw/arcgis/vegas/taxi_zones.geojson \
  --output data/external/normalized/city_gis/vegas/taxi_zones.jsonl \
  --profile vegas_parking_zone \
  --source 'City of Las Vegas Taxi Zones' \
  --skip_invalid
```

若返回零 features，不是脚本错误，通常说明 City 图层与 nuPlan Strip bbox 覆盖很少；保留报告并转向 Clark County 数据与人工审计。

### 6.2 Clark County 必须补充/人工审计

在 Clark County GIS/Open Data/Public Works 中搜索：

```text
Las Vegas Boulevard sidewalk
ADA ramp
curb and gutter
pedestrian bridge / elevator
right-of-way
construction / special event permit
loading zone / passenger pickup
```

重点核查：

- 酒店/赌场主入口和实际 ridehail/taxi pickup 区；
- 行人天桥、电梯、自动扶梯的开放时间与故障状态；
- 围栏、护栏导致的不可达 curb；
- 车道侧和门侧；
- 大型活动/施工导致的临时 PUDO 冲突。

Clark County Public Works 明确负责 Las Vegas Boulevard south of Sahara 以及路缘、人行道和 ADA ramps 的建设维护，但公开 GIS 不一定提供论文所需的全部逐点测量字段。缺失部分用 Step 8 的人工审计补齐。

## Step 7：Singapore LTA / OneMap

LTA DataMall 的 geospatial datasets 使用 ESRI Shapefile；data.gov.sg 也发布了部分 LTA GeoJSON。对 `sg-one-north`，先在当期目录中搜索并下载实际存在的以下**类型**，不要假设每个名称在每个版本都存在：

```text
Kerb Line（已确认有 GeoJSON）
Taxi Stop / Taxi Stand / Taxi Pick Up-Drop Off（已确认有 GeoJSON）
Footpath / sidewalk 类线数据（若当期目录提供）
Covered Linkway（若当期目录提供）
Passenger Pickup Bay（若当期目录提供）
Pedestrian Overhead Bridge / Underpass 或 station exit（若当期目录提供）
Lamp Post / lighting（仅作照明设施位置先验）
```

注意：data.gov.sg 中名为 `Pedestrian Facilities` 的公开表是年度设施数量统计 CSV，不是逐设施 GIS 图层，不能拿来构建步行图。

入口：

```text
https://datamall.lta.gov.sg/content/datamall/en/static-data.html
```

把 ZIP 原样放到：

```text
data/external/raw/lta/singapore/
```

解压：

```bash
mkdir -p data/external/raw/lta/singapore/unpacked
for f in data/external/raw/lta/singapore/*.zip; do
  unzip -o "$f" -d data/external/raw/lta/singapore/unpacked
 done
```

查看图层和 CRS：

```bash
find data/external/raw/lta/singapore/unpacked -type f \
  \( -name '*.shp' -o -name '*.gpkg' \) -print

ogrinfo -al -so /path/to/Footpath.shp
```

转换到 WGS84 GeoJSON；按你实际文件名替换：

```bash
ogr2ogr -f GeoJSON -t_srs EPSG:4326 \
  data/external/normalized/city_gis/singapore/footpath.geojson \
  /path/to/Footpath.shp

ogr2ogr -f GeoJSON -t_srs EPSG:4326 \
  data/external/normalized/city_gis/singapore/kerbline.geojson \
  /path/to/Kerbline.shp

ogr2ogr -f GeoJSON -t_srs EPSG:4326 \
  data/external/normalized/city_gis/singapore/passenger_pickup_bay.geojson \
  /path/to/PassengerPickupBay.shp

ogr2ogr -f GeoJSON -t_srs EPSG:4326 \
  data/external/normalized/city_gis/singapore/taxi_stand.geojson \
  /path/to/TaxiStand.shp
```

Taxi Stand / Passenger Pickup Bay 表示特定管理语义下的设施，不能直接推断“自动驾驶车辆在任意时间均合法停车”。归一化为候选：

```bash
python scripts/normalize_accessibility_evidence.py \
  --input data/external/normalized/city_gis/singapore/taxi_stand.geojson \
  --output data/external/normalized/city_gis/singapore/taxi_stand_candidates.jsonl \
  --profile lta_taxi_stand \
  --source 'Singapore LTA Taxi Stand' \
  --skip_invalid
```

### 7.1 OneMap 可选补充

OneMap Themes API 需要 bearer token，并可能返回 429。先在 OneMap 获取 token，再：

```bash
export ONEMAP_TOKEN='你的 token'

python scripts/download_onemap_theme.py \
  --query_name '<从 Get All Themes Info 得到的 queryName>' \
  --bbox 1.27,103.75,1.33,103.82 \
  --output data/external/raw/onemap/singapore/<theme>.geojson
```

不要猜 queryName；先用官方 `getAllThemesInfo` 查询。脚本已实现重试和 HTML/错误响应检查。

## Step 8：DEM / elevation

### 8.1 美国三城

在 USGS The National Map Downloader 中按 bbox 下载 3DEP 1 m DEM（GeoTIFF），分别放到：

```text
data/external/raw/dem/boston/*.tif
data/external/raw/dem/pittsburgh/*.tif
data/external/raw/dem/vegas/*.tif
```

USGS 3DEP 免费且无使用限制；若某区域没有 1 m 产品，记录实际产品分辨率，不能静默换成低分辨率仍标为 1 m。

### 8.2 Singapore

可使用 Copernicus DEM GLO-30 Cloud Optimized GeoTIFF 作为地形 prior，放到：

```text
data/external/raw/dem/singapore/*.tif
```

Copernicus GLO-30 是 30 m DSM，包含建筑、基础设施和植被影响，只能用于大尺度地形/高程先验，不用于 curb ramp 或横坡真值。

### 8.3 从本地 raster 对图顶点采样

```bash
python scripts/sample_raster_dem.py \
  --external_root data/external \
  --city boston \
  --rasters data/external/raw/dem/boston/*.tif \
  --vertical_datum NAVD88 \
  --source_name USGS_3DEP \
  --include_city_gis

python scripts/sample_raster_dem.py \
  --external_root data/external \
  --city pittsburgh \
  --rasters data/external/raw/dem/pittsburgh/*.tif \
  --vertical_datum NAVD88 \
  --source_name USGS_3DEP \
  --include_city_gis

python scripts/sample_raster_dem.py \
  --external_root data/external \
  --city vegas \
  --rasters data/external/raw/dem/vegas/*.tif \
  --vertical_datum NAVD88 \
  --source_name USGS_3DEP \
  --include_city_gis

python scripts/sample_raster_dem.py \
  --external_root data/external \
  --city singapore \
  --rasters data/external/raw/dem/singapore/*.tif \
  --vertical_datum EGM2008 \
  --source_name COPERNICUS_GLO30_DSM \
  --include_city_gis \
  --allow_partial_coverage
```

输出：

```text
data/external/normalized/dem/<city>.jsonl
```

## Step 9：人工审计生成 paper 级 curb、legality 和 entrance

复制模板：

```bash
cp data/external/schemas/manual_audit_template.csv \
  data/external/raw/manual/boston/audit.csv
```

删除示例行，然后逐点填写。必填项包括：

```text
audit_id, city, lon, lat,
curb_height_m, sidewalk_width_m, deployment_clearance_m,
curb_ramp, legal_stop, legal_basis,
observed_at, auditor_id
```

建议测量协议：

- WGS84 位置；
- 最窄有效通行宽度，而不是名义 sidewalk 宽度；
- ramp running slope、cross slope、landing；
- curb reveal；
- 车辆坡板部署所需净空；
- 表面、台阶、障碍；
- 停车标志/路缘颜色/管理规则照片；
- `legal_stop` 必须针对论文中定义的 `service_class` 和时间窗；
- 对照片做隐私处理，不采集人脸/车牌；`auditor_id` 使用去标识化 ID。

生成四类标准化文件：

```bash
python scripts/build_manual_audit_layers.py \
  --input_csv data/external/raw/manual/boston/audit.csv \
  --city boston \
  --external_root data/external
```

分别替换 city 运行四次。输出：

```text
normalized/curb_inventory/<city>.jsonl
normalized/curb_regulations/<city>.jsonl
normalized/entrances/<city>.geojson
audits/<city>/manual_audit_manifest.jsonl
```

## Step 10：车辆接口、capability contract 和动态 overlay

### 10.1 车辆接口

```bash
cp data/external/schemas/vehicle_interfaces.example.jsonl \
  data/external/normalized/fleet/vehicle_interfaces.jsonl
```

必须替换为论文使用车辆的实测或经制造商/运营商核实规格。不要把 example 的值直接写进论文。

### 10.2 capability contracts

模板：

```text
data/external/schemas/capability_contract.example.jsonl
```

合同是经同意的功能条件，不是医学诊断或人口标签。应同时生成：

- 单变量严格化 pair；
- 不影响当前场景的 irrelevant change；
- 需要改 path/interface 的 change；
- 无可行解、应返回 certificate 的 change。

### 10.3 动态 overlay

模板：

```text
data/external/schemas/dynamic_overlay.example.jsonl
```

施工、临时障碍、PUDO 占用、天气/照明干预必须包含：

```text
observed=false
synthetic_intervention=true
seed
active_interval
source=controlled_counterfactual
```

如果来自真实动态数据，则改为有来源和时间戳的 observed record。

## Step 11：构建 provenance manifest

复制并编辑：

```bash
cp data/external/schemas/provenance_registry.example.yaml \
  data/external/manifests/provenance_registry.yaml
```

对每个来源填写：

```text
role
path
source_url
license / portal terms
retrieved_at
evidence_tier
authoritative
```

生成包含 SHA256 的 manifest：

```bash
for city in boston pittsburgh vegas singapore; do
  python scripts/build_provenance_manifest.py \
    --registry data/external/manifests/provenance_registry.yaml \
    --city "$city" \
    --output "data/external/manifests/${city}.json"
done
```

## Step 12：先做来源 preflight

### 12.1 Bootstrap

```bash
python scripts/validate_external_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston+pittsburgh+vegas+singapore \
  --source_policy bootstrap \
  --output data/external/reports/preflight.bootstrap.json
```

Bootstrap 至少要求：

- 有可用的 pedestrian topology；
- georeference 可解析；
- 文件不是空文件/HTML/错误页/零记录。

### 12.2 Paper

```bash
python scripts/validate_external_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --cities boston+pittsburgh+vegas+singapore \
  --source_policy paper \
  --output data/external/reports/preflight.paper.json
```

Paper 还要求：

- 真实且 validated 的 georeference；
- 物理 curb/sidewalk 测量；
- PUDO 停车法规；
- entrance；
- elevation/坡度证据；
- 市政或人工审计证据；
- provenance/license manifest。

脚本会明确拒绝：零字节、HTML、403/429 页面、malformed JSON、零 feature、只有布尔 `curb_ramp` 而无物理尺寸、缺乏法律依据的 `legal_stop`、伪 OSW。

## Step 13：正确执行数据集流水线

### 13.1 先跑 Boston 20 场景 bootstrap

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split train \
  --stages map_crs,preflight,extract,graphs,pudo,service,dataset \
  --cities boston \
  --max_scenarios_per_city 20 \
  --source_policy bootstrap
```

注意：`all` **不会**自动联网下载，避免在长流水线中途被反爬/限流打断。

### 13.2 四城 bootstrap

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split train \
  --stages map_crs,preflight,extract,graphs,pudo,service,dataset,merge \
  --cities boston+pittsburgh+vegas+singapore \
  --max_scenarios_per_city 200 \
  --source_policy bootstrap
```

### 13.3 Paper 模式

只有 `preflight.paper.json` 无 blocker 后执行：

```bash
python scripts/prepare_abilitybench_external.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --split train \
  --stages map_crs,preflight,extract,graphs,pudo,service,dataset,merge \
  --cities boston+pittsburgh+vegas+singapore \
  --source_policy paper
```

输出位于：

```text
data/outputs/prepared/train/
data/outputs/datasets/abilitybench_av_train_<city>/
data/outputs/datasets/abilitybench_av_train/
```

## Step 14：审计和 nuPlan closed-loop job

```bash
python scripts/audit_dataset_quality.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_train \
  --output data/outputs/datasets/abilitybench_av_train/audit_report.json
```

Paper 模式加入强制门控：

```bash
python scripts/audit_dataset_quality.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_train \
  --output data/outputs/datasets/abilitybench_av_train/audit_report.paper.json \
  --paper_mode \
  --fail_if_not_publication_ready
```

导出闭环场景选择任务：

```bash
python scripts/export_nuplan_closed_loop_jobs.py \
  --dataset_dir data/outputs/datasets/abilitybench_av_train \
  --output_dir data/outputs/nuplan_closed_loop_jobs/train
```

---

## 7. 质量验收清单

### 7.1 空间与拓扑

- 外部图层与 nuPlan route corridor 有实际交集；
- WGS84 → local map frame 后中位偏差合理；
- sidewalk/crossing/curb 连接关系可路由；
- entrance 能连接到步行图，不通过长距离虚拟边跨越道路/围栏；
- curb ramp 位于 crossing/sidewalk 接口附近；
- 每 episode 节点、边数量超过门槛。

### 7.2 物理字段

- 宽度单位统一为 m；坡度统一为 ratio；
- 原始单位和转换方法写入 metadata；
- DEM-derived slope 与 measured ramp slope 分开字段；
- 缺失不是 0，不是默认可行；
- confidence 与 timestamp 保留。

### 7.3 PUDO 和法规

- 候选点与 route lane/curb 的几何关系正确；
- `legal_stop` 有 `legal_basis`、service class 和 time window；
- taxi/loading/parking 设施没有被自动泛化为 AV 合法；
- door side 与行车方向/curb side 匹配；
- 部署空间不是仅用 sidewalk centerline 距离近似。

### 7.4 标签与实验

- verifier 与 planner 代码路径尽可能独立；
- positive/negative skeleton 都有足够数量；
- 每个主要失败 phase 有覆盖；
- same-scene counterfactual 不跨 split；
- 严格合同的可行集满足单调性；
- 报告 PCR、TSPIR、CVR、CSM、FLF、BAF、MER/MVR、SBR、IR、DF、SME、CRsp、ECA 与标准 nuPlan 指标。

---

## 8. 原报错的诊断与处理

原错误：

```text
json.decoder.JSONDecodeError: Expecting value: line 1 column 1
```

它发生在 `load_gis_features -> _read_any -> load_json`，说明某个以下输入不是合法 JSON：

```text
osm_source
opensidewalks_source
city_gis_dir 中的 .json/.geojson
curb_inventory_source
entrance_source
elevation_source
```

快速排查：

```bash
find data/external -type f -size 0 -print

find data/external -type f \
  \( -name '*.json' -o -name '*.geojson' \) \
  -exec sh -c 'echo "== $1"; head -c 80 "$1"; echo' _ {} \;

python scripts/validate_external_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --source_policy bootstrap \
  --no_fail
```

若文件开头是：

```text
<html
<!DOCTYPE html
Access Denied
Too Many Requests
```

删除该文件，改用官方 API、CKAN API、ArcGIS REST 分页脚本或手工下载。不要把错误页重命名为 `.json` 后继续跑。

修改后的代码同时做两层保护：

1. 下载阶段采用 `.part` 原子写入并验证；
2. preflight 阶段在 GIS 构图之前集中阻断无效来源。

---

## 9. 四城 paper-readiness 现实评估

| 城市 | 公开拓扑 | 官方物理属性 | PUDO/法规 | 建议 |
|---|---|---|---|---|
| Boston | OSM + Boston sidewalk centerline/inventory | 相对最好，但 ramp 物理字段仍需逐字段检查 | 市政 curb/parking + 人工法规审计 | 最先完成 paper pilot |
| Pittsburgh | OSM，DOMI 管理/地图资源 | 未确认统一、完整公开 curb physical inventory | meter/loading/permit 可作候选；人工审计重要 | 以 Hazelwood corridor 为中心定向审计 |
| Las Vegas | OSM；城市/县道路资源 | Strip 多由 Clark County 管理，公开字段不完整 | taxi zone 仅候选，活动/酒店入口复杂 | 必须做 Clark County + 酒店 PUDO 现场审计 |
| Singapore | OSM + LTA Footpath/Kerbline 等 | LTA 静态图层丰富，但未必含坡道尺寸/横坡 | Pickup Bay/Taxi Stand 只代表特定服务语义 | LTA 图层打底，One-North 定向人工审计 |

---

## 10. 本次代码修改摘要

新增：

```text
capplan/data/external_validation.py
scripts/validate_external_sources.py
scripts/download_arcgis_layer.py
scripts/download_ckan_resource.py
scripts/download_onemap_theme.py
scripts/prepare_osm_from_pbf.py
scripts/normalize_overpass_json.py
scripts/normalize_accessibility_evidence.py
scripts/sample_raster_dem.py
scripts/inspect_nuplan_map_crs.py
scripts/build_manual_audit_layers.py
scripts/build_provenance_manifest.py
scripts/create_data_layout.py
requirements-data.txt
data/external/schemas/*
```

主要修复：

- 所有数据路径统一到项目 `data/`；
- OSM PBF 离线裁剪，Overpass 仅作显式 bootstrap 下载；
- 不再把 OSM 派生文件伪装成 OSW；
- ArcGIS/CKAN/OneMap 下载重试、分页、原子写入、HTML/空文件检查；
- 从 nuPlan GPKG 元数据生成真实 georeference；
- paper/bootstrap 分级 preflight；
- 以证据类别验收，而不是要求重复来源文件全部存在；
- dataset split 按 `log_name` 分组，防止反事实泄漏；
- per-city graph/PUDO/source diagnostics；
- DEM 改为本地 raster 批量采样；
- 人工审计与 provenance 进入正式流水线。

测试状态：

```text
75 passed
python -m compileall -q capplan scripts 通过
```

---

## 11. 参考入口

- nuPlan devkit / dataset：`https://github.com/motional/nuplan-devkit`
- OpenSidewalks Schema：`https://taskarcenteratuw.github.io/tcat-wiki/opensidewalks/schema/`
- Geofabrik：`https://download.geofabrik.de/`
- Boston ArcGIS Infrastructure/OpenData：`https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer`
- WPRDC：`https://data.wprdc.org/`
- Pittsburgh DOMI：`https://www.pittsburghpa.gov/Business-Development/Mobility-and-Infrastructure`
- Clark County Public Works：`https://www.clarkcountynv.gov/government/departments/public_works_department/`
- Singapore LTA DataMall static datasets：`https://datamall.lta.gov.sg/content/datamall/en/static-data.html`
- OneMap Themes API：`https://www.onemap.gov.sg/apidocs/themes`
- USGS The National Map / 3DEP：`https://www.usgs.gov/tools/national-map-viewer`
- Copernicus DEM：`https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM`
