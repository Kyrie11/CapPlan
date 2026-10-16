# CapPlan Science V9：真实 PUDO 证据重建、实物审核与科学假设判定（2026-10-15）

## A. 总体决定：保留主线，先验证「可执行交接选项」

**中心科学问题（待证实）**：When should an AV system commit a passenger to a pickup handoff when curb evidence, human progress and vehicle stopping opportunities evolve asynchronously? 现有结果不能支持声称真实 A/B stranded 事件发生率，也不能支持声称任何通用 POMDP/shield 不能解决。当前选择“先从外部来源重建真实 PUDO”路线：对不真实的候选明确证伪，并寻找真实 PUDO 仍无法形成可执行接口的系统性案例。

历史 `nuPlan` 2021 轨迹 + 本次下载的 2026 GIS 必须分层：`contemporary site study + historically logged road context` 是合法研究方案，但不等于严格 2021 事件回放。不能将无匹配道路规则的候选标成合法停车，不能用 CARLA Town03 替代 nuPlan 四城现场验证。

## B. 数据来源、利用范围和研究边界

| id | 数据源及访问方式 | 能支持的证据 | 严格禁止的推理 |
|----|----|----|----|
| boston_sidewalk / boston_ramp / boston_curb | 官方 ArcGIS `https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer`（0、3、13层） | sidewalk polygon、坡道记录、curb polyline 的几何关联；侧线方向需审核 | 邻近即全部连续、坡度/宽度/合法 PUDO 在当前时段确定 |
| nevada_dot_ramp / nevada_dot_discontinuity | Nevada DOT `https://gis.dot.nv.gov/arcgis/rest/services/ArcGISOnline/ADA_ROW_Inventory/FeatureServer` | ADA 坡道/不连续设施记录（道路管辖范围需明确） | NDOT 管辖之外的数据缺失即设施不存在 |
| clark_ramp | Clark County `https://maps.clarkcountynv.gov/arcgis/rest/services/PW/pwEditLayers/FeatureServer/1` | 坡道点位及属性 | 轮椅可从道路停靠点通行或车辆具有合法上下客权 |
| pittsburgh_steps | `https://data.wprdc.org/dataset/city-steps` GeoJSON | stairs/stairways obstacle 候选 | 记录未覆盖处视为台阶不存在 |
| pittsburgh_crosswalk | `https://data.wprdc.org/dataset/city-of-pittsburgh-markings` GeoJSON | crossing/marking 候选，历史源更新有限 | 当前过街完全无障碍、nuPlan 当时状态确定 |
| pittsburgh_street_closures | `https://data.wprdc.org/dataset/street-closures` CSV | 带生效时间的施工封路线索 | 未与 nuPlan 2021 时间重合仍作为动态真值 |
| singapore_taxi_stands | LTA DataMall `https://datamall.lta.gov.sg/` TaxiStands API，需要账户 Key | location、Bfa、Type（Stand/Stop）及设施身份 | Bfa=Yes 即轮椅现场可登乘；Taxi Stand 允许无人驾驶车辆自由停靠 |
| singapore_passenger_pickup_bay | 已有 external normalized passenger pickup bay；需对齐最新 LTA 记录 | designated general-purpose pickup bay 候选 | 未审核车辆类别、限制与部署空间前就是可用无障碍 PUDO |

美国可用 US Access Board PROWAG `https://www.access-board.gov/prowag/complete.html` 作为字段设计参考；不能用规定值代替现场实测；Singapore 使用本地法规/运营许可，不能直接套用美国规范。

官方设施数据 ≠ 完整的 PUDO ground truth。获取成功只产生 `source_leads.jsonl`（外部空间证据线索）。5 个关键事实为 `sidewalk_to_curb_continuity`、`crossing_access`、`legal_stopping`、`deployment_region`、`stopping_pose`；没有可信来源逐项支持时必须保持 unknown。

## C. 环境准备（原始 hybrid 不破坏，不再重建全部 dataset）

```bash
conda activate capplan
export CAP_HOME=/home/senzeyu2/code/CapPlan
export DATA_ROOT=/data0/senzeyu2/dataset/CapPlan/data
export TEST_DATASET="$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test"
export STUDY="$CAP_HOME/outputs/handoff_science_v9"
export CHAIN="$CAP_HOME/outputs/handoff_science_v7/evidence_chain.json"
export GEOREF="$DATA_ROOT/external/georeference"
mkdir -p "$STUDY"
cd "$CAP_HOME"
python -m pytest -q tests/test_pudo_evidence_v9.py

# 先做空间配准检查。不匹配则禁止后续把点位当作现场对应位置。
python scripts/validate_georeference_alignment.py \
  --config configs/abilitybench_nuplan_real_data0.yaml \
  --report_json "$STUDY/georeference_alignment.json"
```

若 `CHAIN` 实际在别处，用 `find "$CAP_HOME/outputs" -name 'evidence_chain.json'` 获取，不能根据例子路径假装存在。GIS 仍需要靠真实路口/坡道控制点检查米级偏移，自动 AOI 重叠只是粗配准。

## D. 按 physical site identity 从 61k anchors 提取实际 WGS84 点位

```bash
python scripts/pudo_evidence_v9.py sites \
  --chain "$CHAIN" \
  --anchors "$TEST_DATASET/pudo_anchors.jsonl" \
  --georef_dir "$GEOREF" \
  --output "$STUDY/site_index_v9.jsonl"

cat "$STUDY/site_index_v9.summary.json"
# 同时生成 site_index_v9.geojson，QGIS 可与官方资料叠加
```

该步骤只提取 64 个 A/B 候选端点所对应的约 55 个物理站点（精确数量依当前输入），不能估算自然失败比例。物理站点键下多时间片重复只计一次。投影不一致时返回 `CITY_BOUNDS_MISMATCH` / `CONFLICTING_POSES_FOR_SITE_KEY`，不会自动修正 CRS。

## E. 公共 ArcGIS 局部查询（按需授权联网）

```bash
# 指定联网开关；默认不发网络请求；只查询与选定站点相邻的小范围，不下载整座城市。
python scripts/pudo_evidence_v9.py fetch \
  --sites "$STUDY/site_index_v9.jsonl" \
  --out_dir "$STUDY/public_sources" \
  --enable_network \
  --only_sources boston_sidewalk,boston_ramp,boston_curb,nevada_dot_ramp,nevada_dot_discontinuity,clark_ramp

cat "$STUDY/public_sources/source_collection_summary.json"
```

注意：某官方服务可能拒绝 GeoJSON、限流或出现 HTTP 连接错误。程序将错误写入 `errors`；**失败不是 0 个设施**。对于服务端 count 与收到记录数不一致的结果，默认拒绝并报告 `TRUNCATED`，不接受不完整的官方清单。

`public_sources/raw_source_snapshots` 保存官方响应快照以便复核。`source_leads.jsonl` 是地图上的相关设施线索，不会自动产生 true/false 的 PUDO 结论。

## F. Singapore LTA 获取（需要 API Key）

访问 `https://datamall.lta.gov.sg/` 申请 DataMall API AccountKey，将密钥放入环境变量（不要上传或提交到 Git）：

```bash
export LTA_ACCOUNT_KEY='YOUR_ACCOUNT_KEY'
python scripts/download_pudo_official_v9.py lta \
  --output "$STUDY/lta_taxi_stands.json"
unset LTA_ACCOUNT_KEY

python scripts/pudo_evidence_v9.py fetch \
  --sites "$STUDY/site_index_v9.jsonl" \
  --out_dir "$STUDY/singapore_sources" \
  --only_sources singapore_taxi_stands \
  --local_source "singapore_taxi_stands=$STUDY/lta_taxi_stands.json"
```

LTA 获取文件中 `Bfa=Yes` 仅表示该来源声称设施 barrier-free，`Type=Stand/Stop` 是 taxi-service 定义；不可据此设置“自动驾驶车辆合法停靠=真”。若实际需要 general pickup bay，优先把之前构建出的 `external/normalized/candidates/singapore/passenger_pickup_bay.jsonl` 作为单独候选来源复核，查明来源与采集时间，再作为 `singapore_passenger_pickup_bay` 传入。

## G. Pittsburgh WPRDC 自动/手工来源

```bash
python scripts/download_pudo_official_v9.py wprdc \
  --package city-steps --out_dir "$STUDY/wprdc"
python scripts/download_pudo_official_v9.py wprdc \
  --package city-of-pittsburgh-markings --out_dir "$STUDY/wprdc"
```

脚本从 WPRDC CKAN API 查找 GeoJSON 资源，并按域名白名单下载；如果格式/域名不满足要求，会记录错误并要求人工确认 URL。因有的 package 多资源，应查看 `.provenance.json` 确认内容类别，再逐个绑定：

```bash
python scripts/pudo_evidence_v9.py fetch \
  --sites "$STUDY/site_index_v9.jsonl" \
  --out_dir "$STUDY/pittsburgh_sources" \
  --only_sources pittsburgh_steps \
  --local_source "pittsburgh_steps=/path/to/confirmed_city_steps.geojson"
```

动态道路许可记录必须具备实效日期和明确路段匹配。2026 年的施工数据与 2021 nuPlan 场景不重叠，不可当作当时的障碍发生事实。

## H. 合并来源线索、形成真实审核队列

```bash
python scripts/pudo_evidence_v9.py merge \
  --inputs "$STUDY/public_sources/source_leads.jsonl" \
           "$STUDY/singapore_sources/source_leads.jsonl" \
           "$STUDY/pittsburgh_sources/source_leads.jsonl" \
  --output "$STUDY/combined_source_leads.jsonl"
# 文件不存在会记录为未采集，不会被转换成“无设施”。

python scripts/pudo_evidence_v9.py triage \
  --sites "$STUDY/site_index_v9.jsonl" \
  --leads "$STUDY/combined_source_leads.jsonl" \
  --out_dir "$STUDY/triage"
```

输出 `physical_review_with_source_leads_v9.csv` 每个 physical site × 五项事实一行，并包含 `suggested_source_leads`、WGS84 坐标。审查应优先覆盖：

1. 官方数据似乎显示障碍或冲突的案例（**可能是实际物理失败**），以及邻近设施完好的对照；
2. 正式指定上下客设施附近的站点，不再仅选择失败挖掘器筛选的 A/B；
3. 四城分层、物理站点独立，控制道路管辖与交通方向；
4. 需要 ramp/lift、不同距离/等待能力合同的有意义差异案例。

在 QGIS 加载 `site_index_v9.geojson` 和官方 layers，先由审核人员目视检查地理对齐。照片/街景要带来源和采集时间；**仅靠街景不能签认停车法律条款**。市政法规、时段与车辆服务类别，优先由可追溯的官方记录或当地交管部门确认。

人工填写 `fact,value,source_id,source_kind,checked_at_utc,evidence_uri,reviewer_id`；未核验必须保持 `value=unknown`。然后：

```bash
python scripts/import_physical_review_v8.py \
  --review_csv "$STUDY/triage/physical_review_with_source_leads_v9.csv" \
  --output_jsonl "$STUDY/independent_physical_review.jsonl"
python scripts/audit_handoff_physical_v5.py \
  --claims "$STUDY/independent_physical_review.jsonl" \
  --output "$STUDY/physical_source_claims_audit.json"
```

即使 V5 metadata audit 给出 `metadata_complete_positive_sites`，也不是论文中的独立现场真值：仍须核查原始来源、实际停止姿态及乘客功能合同。新增证据不能直接覆盖原始 `pudo_anchors.jsonl`。建议将 audited facts 作为 append-only sidecar，保留原 hybrid 值和冲突记录。

## I. 自动研究分支与小规模结果包

```bash
python scripts/study_pudo_evidence_v9.py \
  --sites "$STUDY/site_index_v9.jsonl" \
  --leads "$STUDY/combined_source_leads.jsonl" \
  --review_claims "$STUDY/independent_physical_review.jsonl" \
  --carla_paired_summary "$CAP_HOME/outputs/handoff_science_v8/carla_speed20_preview/paired_summary.json" \
  --output "$STUDY/research_verdict_v9.json"

# 如果未审核，可以直接省略 --review_claims；报告会明确 NOT_SUBMITTED。

python scripts/sample_unscreened_handoff_controls_v4.py \
  --dataset_dir "$TEST_DATASET" --per_city 20 --seed 13 \
  --output "$STUDY/unscreened_episodes.jsonl"

cd "$STUDY"
zip -r -q science_v9_review_bundle.zip \
 site_index_v9.jsonl site_index_v9.summary.json site_index_v9.geojson \
 combined_source_leads.jsonl triage research_verdict_v9.json \
 public_sources/source_collection_summary.json \
 unscreened_episodes.jsonl
```

不存在的可选文件无需加入压缩包；`zip` 提示缺失时，先检查文件实际生成。请回传 `science_v9_review_bundle.zip`；不要上传 LTA AccountKey、带敏感信息的完整照片、全部 nuPlan SQLite 或无关的大型 graph。

## J. 下一轮科学论证/分支判定（不可用模拟数据强行使主张成立）

| 层 | 最低证据要求 | 若失败如何调整 |
|---|---|---|
| L0 数据结构 | nuPlan real scene+GIS spatial alignment、site identity、provenance 分层 | 修正 CRS/schema/同步，不做真实发生率判断 |
| L1 真实候选 | 官方来源同地理空间关联、独立负对照 | 仅 source lead 不算物理可行；优先审核 |
| L2 物理接驳 | ped route↔curb↔真实法定 stop segment/pose↔设备净空（时间、车种、能力） | 如果 A/B 多为非法，则旧失效案例前提被证伪，从真实合法 PUDO 重挖 |
| L3 车辆执行 | Clear/Blocked/Redirect 同控变量、真实 stop/dwell、无异常碰撞和目标姿态正确；道路时空映射 | CARLA Town 是机制性可控实验，不能冒称 Boston 等地历史现场发生 |
| L4 乘客进度 | 真实或有标定的不确定移动速度/确认时延、等待与途中改向、第三 PUDO、乘客功能合同 | 若多数可以恢复，转向状态依赖的服务成本/证据时效，不再称普遍 stranded |
| L5 强基线与可忽略性 | 独立搜索 legal PUDO 机会集合、固定站点、最优核验、shield/TAMP 公平预算，盲抽分母 | 若无差距，转向证据保障或可证明高效 viability 求解，不声称决策不可替代 |

论文短期应聚焦 `Evidence-Grounded Executable Handoff Options` + `When to Commit Under Evidence Expiry and Passenger Progress`，只有 L2–L5 的证据闭合后才将联合作为最终核心贡献。最关键的负对照是 **经过官方实物审核的、没有通过失败挖掘器筛选的合法接驳点**。
