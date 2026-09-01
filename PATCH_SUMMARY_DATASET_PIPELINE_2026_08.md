# CapPlan / AbilityBench-AV 数据集流水线优化摘要（2026-08）

本补丁面向论文的 passenger-complete / capability-aware planning 数据集构建。核心原则：**unknown 保持 unknown；candidate 只生成候选；只有独立、可审计的物理与法规证据才能晋升为 paper evidence。**

## 主要语义修复

- nuPlan split 改为真实目录：`train_boston/train_pittsburgh/train_vegas/train_singapore`，并完整支持 `val/test`。
- 新增 DB 城市检查，不再按目录名猜 val/test 城市，也不要求物理拆 DB。
- nuPlan CRS 从 map GPKG metadata 读取，并区分 CRS metadata validation 与真实 spatial alignment validation。
- OSM 不再把 `sidewalk=*` 的机动车道路中心线当成人行道；未知线不再默认 sidewalk。
- Boston sidewalk polygon 只作物理属性 inventory，Sidewalk Centerline 才作为 routable topology。
- Boston ramp 不再把 `SWK_WIDTH` 复制为 `deployment_clearance_m`；单位不再按数值大小猜测。
- Pittsburgh blockgroup walkability ratio 不作为 pedestrian geometry；增加 `Sidewalks and Steps SHP` 支持。
- Pittsburgh High Injury Network 不再映射 curb ramp；zoning 不再映射 loading zone。
- Pittsburgh Address Points 明确标记为 `entrance_proxy`，不能直接作为 physical entrance ground truth。
- route/lane geometry 不再自动授予 `legal_stop=True`。
- sidewalk width 不再自动推导 ramp/lift deployment clearance。
- manual audit 中 entrance 必须有独立 `entrance_lon/entrance_lat`，不再复用 curb/PUDO 坐标。

## 新增公开数据入口

- Boston ArcGIS：Sidewalk Inventory / Ramp Inventory / Sidewalk Centerline / Curbs。
- Pittsburgh WPRDC：Sidewalks and Steps、Current/Archive/Rates Payment Points、Street Closures、Address Points。
- Las Vegas：Taxi Zones candidate layer。
- Singapore LTA：Passenger Pickup Bay、Taxi Stand、Footpath、Kerbline；下载脚本动态发现当前版本 ZIP。
- CKAN 下载支持精确 resource name / resource ID、断连重试、`.part`、流式 SHA256、大文件流式下载。
- vector normalizer 支持 GeoJSON/JSON/JSONL/CSV/SHP/GPKG/SQLite/ZIP，并对大型 GeoJSON 使用 GDAL GeoJSONSeq 流式处理。
- WPRDC Street Closures CSV 支持 WKT `POINT` / `LINESTRING` 几何。
- DEM sampler 增加 source coverage 检查、分辨率单位记录、nominal resolution 元数据；DEM 只作为 terrain prior。

## 新增 PUDO evidence 机制

公共 payment/taxi/pickup-bay 层通过 `--pudo_candidate_source` 真正接入 PUDO 主流水线，但：

- candidate source 本身不能赋予 stopping legality；
- candidate source 本身不能赋予 curb height / clearance / sidewalk width；
- 需要独立 regulation + interface/curb evidence 后才可 `paper_eligible=true`。

默认 `paper_eligible` 需要：

- `curb_height_m`
- `sidewalk_width_m`
- `deployment_clearance_m`
- pedestrian-node binding
- `legal_stop == True`
- 非 heuristic 的独立 legality source
- 可审计 interface/curb evidence source
- 非 synthetic/proxy 冒充 ground truth

## 新增质量检测/PASS gate

- `scripts/inspect_nuplan_db_cities.py` → `NUPLAN_DB_CITY_CHECK=PASS`
- `scripts/prepare_osm_from_pbf.py` → OSM 语义/几何检查
- `scripts/build_accessibility_graphs.py` → `ACCESSIBILITY_GRAPH_CHECK=PASS`
- `scripts/sample_raster_dem.py` → DEM coverage check
- `scripts/validate_external_sources.py` → `EXTERNAL_SOURCE_CHECK=PASS/FAIL`
- `scripts/validate_dataset.py` → `DATASET_SCHEMA_CHECK=PASS/FAIL`
- `scripts/audit_dataset_quality.py` → `ABILITYBENCH_DATASET_CHECK=PASS/FAIL`
- `scripts/check_abilitybench_pipeline.py` → `ABILITYBENCH_PIPELINE_CHECK=PASS/FAIL`
- `scripts/export_pudo_audit_shortlist.py` → 导出最值得人工审计的 PUDO shortlist。

默认 paper quality gate：

- 每个 paper episode 至少 2 个 `paper_eligible` PUDO；
- 至少 80% 的审计数据集 episode 满足该 PUDO gate；
- graph 至少 100 nodes / 150 edges per episode；
- failure certificate 至少覆盖 2 个不同 service phase；
- edge/skeleton label positive rate 默认至少 0.10，避免退化全正/全负数据；
- schema/provenance/external-source preflight 均需通过。

## 回归验证

当前代码包执行：

```bash
python -m compileall -q capplan scripts tests
pytest -q
```

结果：

```text
83 passed
```

并已对 `test` split + 四城 + `stages=all` 做 dry-run，完整命令构造通过；该 dry-run 只验证代码/配置串联，不代表用户机器上的真实数据已经通过质量门槛。

## 使用文档

详细执行顺序、自动下载、手工 fallback、目录、命令和 PASS 标准见：

```text
docs/CapPlan-dataset-build-optimized.md
```
