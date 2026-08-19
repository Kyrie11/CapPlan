# CapPlan four-city full paper dataset build — optimized 2026-08-18

This guide supersedes the operational sequence in `CapPlan-dataset-build-data0-paper-20260817.md` for the **four-city full paper build**. It preserves the earlier guide's core principle—separate a large bootstrap candidate corpus from a smaller publication-grade evidence subset—but fixes the failure modes found in the uploaded reports and makes the 10.1 audit workflow auditable and repeatable.

## 0. Executive conclusion

The paper needs **two related datasets, not one monolithic “all nuPlan scenes are ground truth” dataset**:

1. **Bootstrap candidate corpus**: enumerate all matching nuPlan scenes in the configured train/val/test DBs, attach traffic context, accessibility topology, PUDO candidates, DEM/GIS/OSM evidence and explicit missingness. This layer is useful for candidate mining, uncertainty/failure negatives and engineering coverage, but is not publication truth.
2. **Paper main subset**: retain only episodes with independently supported static/service facts, trusted entrances, verified vehicle interfaces and complete provenance. Publication episodes must also pass a physical-anchor split-leakage gate and a service-chain connectivity gate.

Do **not** fill unknown curb height, sidewalk width, deployment clearance, legal stopping, entrance identity or vehicle-interface facts with heuristics and then call them audited truth. Unknown evidence is a valid state in CapPlan and should remain unknown/fail-closed.

The correct full pipeline is:

`nuPlan DB identity -> georeference/OSM/GIS/DEM -> full bootstrap enumeration -> unique physical-site catalog -> authoritative prefill -> explicit row-level review / new evidence -> audited layers -> provenance + verified fleet -> full paper evidence recompute -> preliminary evidence-qualified episodes -> physical-anchor train/val/test deconfliction -> final allowlists -> strict paper dataset -> QA -> training/evaluation`.

---

## 1. What the paper actually needs to prove

The paper's central object is a **passenger-complete trip**, not merely a collision-free ego trajectory. The service chain is approximately:

`access -> wait -> board -> ride -> alight -> egress`

and feasibility depends jointly on legality, physical anchoring/interface compatibility, availability, capability requirements and evidence confidence. The main method can be summarized as:

- capability semantic compiler;
- CASA transition construction / typed demand and availability reasoning;
- TSBS typed safe-budget search;
- typed margins and failure certificates when completion is impossible.

The dataset therefore needs enough information to support the paper's result families rather than only a conventional nuPlan planner score:

### T1 — pickup/dropoff and first/last-meter feasibility

Needs independently evidenced PUDO and entrance facts:

- legal stopping evidence and basis;
- curb/interface geometry;
- sidewalk/deployment clearance;
- step-free / curb-ramp semantics;
- entrance identity and coordinates;
- pedestrian binding and reachability;
- evidence source, timestamp/as-of, confidence and provenance.

### T2 — capability-aware ride safety/comfort

Needs vehicle motion evidence and a verified service-vehicle interface. The current code computes reproducible nuPlan ego-history acceleration/jerk/lateral-acceleration/composite exposure surrogates. This is **not a full ISO-2631 frequency-weighted metric**. Strong vehicle-safety claims should use actual nuPlan closed-loop runs and should call the current exposure measure “benchmark motion surrogate” or “ISO-inspired” unless the standard-complete metric is implemented.

### T3 — end-to-end passenger completion

Needs T1 + T2 + a complete service chain. A paper episode must not be admitted merely because a graph has many nodes/edges; it must have trusted entrance-to-paper-eligible-PUDO connectivity.

### T4 — same-scene capability counterfactuals

The current dataset generator already constructs base plus seven configured capability axes. Counterfactual profiles for the same episode must remain in the same official split and should not be separated across train/val/test.

### T5 — diagnostic failure certificates

Needs evidence-complete positives **and** evidence-complete infeasible/negative cases with diversity across phases/resources. A pilot in which every request fails at `origin` is useful as a missing-evidence smoke test but cannot support a claim that the certificate mechanism diagnoses diverse failure modes.

---

## 2. Recommended dataset architecture

### Layer 0 — immutable nuPlan DB split identity

Keep the user's DB organization and configured split membership. The pipeline must never randomly re-split DBs after external evidence is added.

The uploaded reports show:

- train: 7351 DBs = Boston 1647, Pittsburgh 1560, Singapore 2394, Vegas 1750;
- val: 1381 DBs = Boston 192, Pittsburgh 174, Singapore 255, Vegas 760;
- test: 1310 DBs = Boston 152, Pittsburgh 160, Singapore 258, Vegas 740;
- DB basename intersection: train∩val=0, train∩test=0, val∩test=0.

This is a good upstream identity gate. The new QA checker rechecks these intersections from the generated reports.

### Layer 1 — full bootstrap candidate corpus

For every matching scene in all four cities and all three splits:

- nuPlan scene/context;
- accessibility graph;
- PUDO candidates;
- evidence source/missingness;
- no publication-grade truth assertion.

Use `max_scenarios_per_city=0` for this stage.

### Layer 2 — unique physical-site evidence catalog

De-duplicate PUDO candidates into physical sites in a local metric CRS. Audit at the **site** level, not separately for every episode that reuses the same curb location. Keep a mapping back to all episodes/splits.

The new `build_pudo_site_catalog.py` generates:

- `external/audits/<city>/pudo_site_catalog.csv`;
- a preliminary split-overlap diagnostic.

This drastically reduces audit work while preserving traceability.

### Layer 3 — audited/reviewed evidence snapshot

For each unique paper-relevant site, obtain the required facts from an independent authoritative source or an actual audit/observation. The minimum evidence for a paper PUDO should include:

- `curb_height_m`;
- `sidewalk_width_m`;
- `deployment_clearance_m`;
- `curb_ramp`;
- `legal_stop` + `legal_basis`;
- entrance identity and independent entrance coordinates;
- source IDs / evidence tier / distance or linkage method / evidence-as-of;
- explicit reviewer/auditor identity and offset-aware review/observation time when a human review is claimed.

Useful additional fields include running slope, cross-slope, surface, obstruction/blockage, lighting, shelter, source feature ID and measurement precision/method.

### Layer 4 — paper main episodes

A final paper episode is selected only when it has at least:

- 2 `paper_eligible` PUDOs;
- 2 distinct physical PUDO sites;
- 2 trusted non-proxy entrances;
- graph size floor (current defaults: 100 nodes, 150 edges);
- at least 2 distinct trusted-entrance ↔ paper-eligible-PUDO pairs reachable through the pedestrian/service graph;
- no final physical-anchor train/val/test leakage;
- verified fleet and publication provenance.

The final selector intentionally **does not treat a transit stop as a trip entrance** in paper mode.

### Layer 5 — capability counterfactuals and failure-certificate strata

For every selected physical scene, keep all capability-profile variants in the same split. Report T4 by episode/site cluster, not by pretending eight profiles are eight independent physical scenes.

For T5, explicitly report certificate counts by phase/resource/city and avoid a publication set that is completely label-degenerate.

### Layer 6 — closed-loop nuPlan evaluation subset

For strong T2/T3 traffic-safety claims, evaluate the learned planner in real nuPlan closed-loop simulation and import the resulting vehicle metrics. Recorded-ego motion summaries are valuable context/surrogates but do not establish closed-loop planner safety.

---

## 3. Diagnosis of the uploaded `reports.zip`

### 3.1 Correct upstream results

The following parts look correct in the uploaded reports:

- all train/val/test DB city-inspection reports PASS;
- no unknown or ambiguous city mappings;
- all DB basenames are disjoint across train/val/test;
- georeference alignment PASS for all four cities;
- reported map/AOI/OSM coverage ratios are 1.0 for all four cities;
- Boston, Vegas and Singapore DEM validation/sampling reports are on the data0 layout and PASS;
- the bootstrap pilot correctly remains publication-not-ready.

### 3.2 Boston PWD download failure: **code bug, not a bad external file**

`recommended_public_sources.json` reports exactly two failures:

- `boston / pwd_ada_ramps`;
- `boston / pwd_sidewalks`;

with `TypeError: 'NoneType' object is not iterable`.

Root cause: the old ArcGIS downloader assumed `returnIdsOnly` always returned an iterable `objectIds`. The Boston service can return `objectIds: null`, so the code failed before the normal query fallback.

Fix in this package: `scripts/download_arcgis_layer.py` now:

- handles `objectIds=null`;
- reads layer metadata / object-ID field / `maxRecordCount`;
- obtains a server-side count;
- paginates when ID enumeration is unavailable;
- detects repeated pages;
- verifies downloaded feature count against the server count;
- refuses to silently save a truncated “successful” file.

After applying the patch, run `fetch-public-force` to refresh the Boston files and all automatable public-source reports.

Expected Boston raw targets include:

- `$EXT/raw/arcgis/boston/pwd_ada_ramps.geojson`;
- `$EXT/raw/arcgis/boston/pwd_sidewalks.geojson`.

### 3.3 Pittsburgh DEM report: **stale report / sequence issue, not evidence of bad DEM data**

`dem_tiles_pittsburgh.json` contains old project-local paths under `/home/senzeyu2/code/CapPlan/data/...`, while the later Pittsburgh local DEM sampling report points to the new `/data0/...` location and passes.

Action: rerun `validate-dems` after migration. This overwrites the stale current report. The new readiness checker flags stale paths in active reports while ignoring deliberately archived historical reports.

### 3.4 `external.paper.json`: **stale pre-data0 paper preflight**

The uploaded `external.paper.json` still contains many `/home/.../data` paths. It should not be used to judge the current data0 state. Rebuild it only after reviewed audit layers, verified fleet and provenance are ready.

### 3.5 Why `paper_eligible=0` in val×10 is expected

Uploaded val×10 PUDO evidence contains:

- Boston: 586 PUDO rows, 0 paper-eligible;
- Pittsburgh: 1416, 0;
- Vegas: 1569, 0;
- Singapore: 5894, 0.

The three core interface dimensions are almost entirely absent from independent audited inventory evidence, and authoritative legality/entrance evidence has not yet been imported. Therefore the current fail-closed behavior is correct.

Likewise all 10 episodes/city currently have zero feasible skeleton labels and all 80 certificates/city collapse to the `origin` phase. This is a useful bootstrap diagnostic, but **not a publishable T1/T3/T5 result**.

The same pilot does show healthy T4 mechanics: each city has 10/10 episode coverage and 70 same-scene pairs over seven configured axes.

### 3.6 The old val×10 manual shortlist is only a pilot

The uploaded shortlist reports contain only:

- Boston: 30 de-duplicated sites;
- Pittsburgh: 11;
- Vegas: 19;
- Singapore: 19.

That is useful for checking the workflow but is not the right audit basis for the four-city full dataset. The optimized pipeline first enumerates **all split candidates**, then de-duplicates full-split physical sites.

---

## 4. What step 10.1 can and cannot automate

### 4.1 Safe automation implemented

`prepare_pudo_audit_worklist.py` automatically performs conservative evidence prefill when the normalized source contains the **same semantic field** and suitable provenance:

- curb height;
- sidewalk width;
- deployment clearance;
- curb ramp;
- running/cross slope;
- surface;
- legal-stop boolean and legal basis from an authoritative regulation source;
- nearest trusted entrance as a **candidate only**;
- field-level source, tier, match distance and evidence-as-of.

`review_pudo_audit_worklist.py` then classifies rows into:

- source-complete rows waiting for explicit human review;
- entrance-linkage review rows;
- rows needing genuinely new evidence/manual audit.

### 4.2 Why fully automatic “human audit” is scientifically invalid

Code may discover or spatially join a source, but it cannot truthfully assert that:

- a person inspected the site;
- a nearest entrance is the intended service entrance;
- a current parking/taxi/curb layer proves legal PUDO for the service class and scene time;
- a width-like OSM tag is a measured deployment clearance;
- a 2026 rule represents a 2021 nuPlan scene state;
- a manufacturer/operator vehicle interface value was verified when only an example file exists.

The optimized workflow therefore **never stamps a reviewer automatically**.

For a source-complete candidate, the reviewer only needs to check the row and set:

- `review_accept=true`;
- `entrance_linkage_approved=true` when the row relies on `entrance_candidate_*` and the candidate is truly the intended trip entrance.

Only after that explicit action can the row be imported as `reviewed_audit` evidence.

### 4.3 Rows that remain unresolved

`pudo_audit_unresolved.csv` is the high-value manual worklist. Obtain new authoritative records or actual observations for the missing fields. Do not fill them with a heuristic.

For genuinely observed/manual rows, create:

`$EXT/audits/<city>/pudo_audit_manual_completed.csv`

with factual `auditor_id`, offset-aware `observed_at`, required physical fields, legality/basis, entrance ID/coordinates and provenance/measurement metadata.

---

## 5. Important code corrections in this package

The patch contains the following dataset-integrity changes.

### 5.1 Download integrity

- `download_arcgis_layer.py`: null-safe ArcGIS ID handling, pagination, count validation, repeated-page protection.

### 5.2 Full-split audit workflow

New:

- `build_pudo_site_catalog.py`;
- `prepare_pudo_audit_worklist.py`;
- `review_pudo_audit_worklist.py`.

### 5.3 Evidence provenance correctness

- `build_pudo_evidence.py` now prefers the matched audited/authoritative inventory for paper-core physical values.
- The three paper-core dimensions must come from the **same matched audited/authoritative inventory record** before `paper_evidence_complete=true`.
- Authoritative/audited legality is required independently.
- This prevents a community/graph width from being combined with one audited field and then mislabeled under the audited inventory source.

### 5.4 Entrance semantics

- Paper mode trusts `manual_audit:` / `reviewed_audit:` entrance sources by default, plus only explicitly configured extra trusted sources.
- `transit_stop` is no longer promoted to a paper OD entrance merely because it is a graph node.
- A spatially nearest authoritative entrance remains a candidate until explicit linkage review.

### 5.5 Paper episode selection

New `select_paper_episodes.py` checks evidence, distinct PUDO sites, trusted entrances, graph quality and actual entrance↔PUDO reachability.

### 5.6 Physical site leakage

New `build_paper_anchor_leakage.py` checks **physical PUDO and entrance reuse** across split-qualified paper episodes. It resolves conflicts with priority:

`test > val > train`

and only among evidence-qualified preliminary paper episodes. This avoids deleting train episodes merely because an ineligible bootstrap candidate happens to be near a test candidate.

### 5.7 Allowlisted strict build

`build_dataset.py`, `build_service_layer.py` and `prepare_abilitybench_external.py` accept an episode allowlist. The final strict paper build therefore materializes only evidence-qualified, site-disjoint episodes without changing official DB split membership.

### 5.8 Preflight / QA consistency

- `external_validation.py` reports row-level authoritative/audited core/legality/entrance counts.
- data0 config enables `require_audited_core_evidence: true` for all cities.
- `check_four_city_paper_readiness.py` creates one compact, uploadable readiness report and checks DB split identity, stale active paths, audit layers, fleet, provenance, allowlists and final physical leakage.
- `package_capplan_qa_bundle.py` creates a compact QA zip without huge DB/GIS/raster/graph payloads.

### 5.9 Model-claim guardrail

The current `hgt`/`rgcn` implementation in `capplan/models/casa_torch.py` is a relation-aware transition embedding surrogate (action/source-phase/target-phase embeddings plus MLP heads), **not an actual heterogeneous graph message-passing HGT/RGCN over graph tensors**. Training it is valid as a surrogate baseline, but the paper should not describe this implementation as a full HGT graph network unless that architecture is implemented.

---

## 6. Apply the optimized code

Back up the working tree first. If using the provided patch zip, unzip it over the repository root.

```bash
cd /home/senzeyu2/code
cp -a CapPlan "CapPlan.backup.$(date +%Y%m%d-%H%M%S)"
cd CapPlan
# unzip -o /path/to/CapPlan_dataset_pipeline_patch_20260818.zip -d "$PWD"
```

The delivered full optimized zip can also be unpacked separately and diffed before replacing the working tree.

Run the code regression suite after applying:

```bash
cd /home/senzeyu2/code/CapPlan
pytest -q
python -m compileall -q capplan scripts
bash -n scripts/build_abilitybench_data0_20260817.sh
```

Reference validation for this delivered patch: `119 passed`, plus `compileall` PASS and `bash -n` PASS.

---

## 7. Environment

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

mkdir -p "$REPORTS/commands" "$REPORTS/build" "$REPORTS/model" "$REPORTS/eval"
```

Before continuing, verify that the config paths point to the intended four train directories and the existing val/test directories, rather than assuming directory names imply official nuPlan split names.

---

## 8. Repair and revalidate the state already produced on data0

Because migration is already complete, do not blindly run `migrate` again. Re-run the integrity stages in the new environment:

```bash
bash scripts/build_abilitybench_data0_20260817.sh inspect-nuplan
bash scripts/build_abilitybench_data0_20260817.sh prepare-osm
bash scripts/build_abilitybench_data0_20260817.sh fetch-public-force
bash scripts/build_abilitybench_data0_20260817.sh validate-dems
bash scripts/build_abilitybench_data0_20260817.sh bootstrap-preflight
bash scripts/build_abilitybench_data0_20260817.sh qa-snapshot
```

Expected gates:

1. `nuplan_db_cities.{train,val,test}.json`: PASS, zero unknown/ambiguous, zero DB-basename overlap.
2. `georeference_spatial_alignment.json`: all four PASS and ratios >= configured 0.95 threshold.
3. `recommended_public_sources.json`: all automatable downloads expected by the config PASS. Boston PWD should no longer fail with `objectIds=null`.
4. `dem_tiles_<city>.json`: active paths under `$CAP_DATA`, coverage >=0.99.
5. `external.bootstrap.json`: bootstrap-ready may PASS while publication-ready remains false; that is expected before audit/fleet/provenance.

If Boston PWD still fails, the new downloader error should now distinguish HTTP/service/query/count/truncation problems instead of producing the old null-iteration exception. Do not use a partially written file as evidence.

---

## 9. Build the **full** four-city bootstrap candidate corpus

This is the first expensive stage and intentionally uses all matching scenarios:

```bash
bash scripts/build_abilitybench_data0_20260817.sh bootstrap-candidates-full
```

Outputs include full split/city PUDO evidence under approximately:

```text
$CAP_DATA/outputs/prepared/train/pudo/<city>.jsonl
$CAP_DATA/outputs/prepared/val/pudo/<city>.jsonl
$CAP_DATA/outputs/prepared/test/pudo/<city>.jsonl
```

At this point, `paper_eligible` may still be low or zero. That is not a failure of bootstrap construction.

---

## 10. Build one unique-site audit catalog across train/val/test

```bash
bash scripts/build_abilitybench_data0_20260817.sh site-catalogs
```

For each city inspect:

```text
$EXT/audits/<city>/pudo_site_catalog.csv
$REPORTS/paper_site_catalog.<city>.json
```

The site catalog is the correct replacement for auditing val×10 episode rows one-by-one.

---

## 11. Automated 10.1 prefill and classification

Run:

```bash
bash scripts/build_abilitybench_data0_20260817.sh prefill-audits
bash scripts/build_abilitybench_data0_20260817.sh classify-audits
```

Per city, the important files are:

```text
$EXT/audits/<city>/pudo_audit_worklist.csv
$EXT/audits/<city>/pudo_audit_review_status.csv
$EXT/audits/<city>/source_complete_review_candidates.csv
$EXT/audits/<city>/pudo_audit_unresolved.csv
```

Interpretation:

- `source_complete_review_candidates.csv`: the program believes the required **source facts** are present, but a person still has to validate the row/source relation.
- `pudo_audit_unresolved.csv`: at least one true fact is missing; obtain new evidence rather than guessing.

### 11.1 Minimal reviewer action for source-complete candidates

Open each city's `source_complete_review_candidates.csv` and inspect the source/match columns. For each row:

- set `review_accept=true` only after actual review;
- if `entrance_candidate_*` is used, set `entrance_linkage_approved=true` only after confirming that candidate is the intended service entrance;
- correct/reject dubious matches rather than approving them in bulk.

Then run:

```bash
export REVIEWER_ID="reviewer-001"          # stable pseudonymous project ID is fine
export CONFIRM_SOURCE_REVIEW=YES
bash scripts/build_abilitybench_data0_20260817.sh review-source-complete-audits
bash scripts/build_abilitybench_data0_20260817.sh import-source-complete-audits
```

The script will not accept a row solely because the environment variable is set: row-level `review_accept` is still required, and candidate entrance linkage needs row-level approval when applicable.

### 11.2 Resolve rows that truly need new evidence

For each city, use:

```text
$EXT/audits/<city>/pudo_audit_unresolved.csv
```

as the acquisition/checklist. If an actual audit/measurement or newly discovered authoritative record resolves the row, write the completed factual rows to:

```text
$EXT/audits/<city>/pudo_audit_manual_completed.csv
```

and import them:

```bash
bash scripts/build_abilitybench_data0_20260817.sh import-completed-manual-audits
```

You may repeat `prefill-audits -> classify-audits -> review/import` after adding better normalized evidence. The import is designed to merge audit records safely rather than treating every rerun as a fresh unrelated truth source.

---

## 12. Verified service-vehicle interface: mandatory for paper mode

Bootstrap may install an **example** fleet file only to keep engineering stages executable. Do not use that example as publication evidence.

Replace:

```text
$EXT/normalized/fleet/vehicle_interfaces.jsonl
```

with independently verified manufacturer/operator/measurement-backed interface data. Required semantics include, as applicable:

- door side;
- ramp/lift/low-floor availability;
- door width;
- deployment clearance;
- notification modalities;
- dwell characteristics;
- kneeling/interface behavior;
- explicit source/provenance and which fields the source actually provides.

The readiness checker and strict dataset build reject example/synthetic/proxy fleet evidence in paper mode.

---

## 13. Provenance registry

If not already created:

```bash
cp "$CAP_HOME/data/external/schemas/provenance_registry.data0.paper.example.yaml" \
   "$EXT/provenance_registry.yaml"
```

Review every entry. Replace all `REPLACE`, `VERIFY`, `TODO` or placeholder metadata with factual information. Record hashes/licensing/source version/acquisition timestamp and temporal interpretation. Do not claim a present-day dynamic or legal layer is scene-time 2021 truth unless the source actually supports that temporal statement.

Build manifests and run strict paper preflight:

```bash
bash scripts/build_abilitybench_data0_20260817.sh build-provenance
bash scripts/build_abilitybench_data0_20260817.sh paper-preflight
```

Do not proceed to final paper selection if `external.paper.json` is FAIL.

---

## 14. Recompute paper evidence on all splits after the audit snapshot is frozen

```bash
bash scripts/build_abilitybench_data0_20260817.sh rebuild-paper-evidence-full
```

This recomputes graph/PUDO evidence against the reviewed/audited sources, rather than reusing stale bootstrap evidence.

Core paper PUDO logic now requires:

- three core dimensions present;
- all three from the same matched audited/authoritative inventory record;
- pedestrian binding;
- independent audited/authoritative legality evidence;
- auditable candidate source;
- legal stop true for a `paper_eligible` positive.

Evidence-complete legal negatives remain useful negative examples; they are not silently dropped from diagnostics.

---

## 15. Build preliminary and final paper allowlists with physical-anchor leakage control

```bash
bash scripts/build_abilitybench_data0_20260817.sh select-paper-allowlists
```

For every city this creates:

```text
$EXT/audits/<city>/paper_allowlists/train.pre_site.txt
$EXT/audits/<city>/paper_allowlists/val.pre_site.txt
$EXT/audits/<city>/paper_allowlists/test.pre_site.txt
$EXT/audits/<city>/paper_anchor_site_disjoint_exclusions.json
$EXT/audits/<city>/paper_allowlists/train.txt
$EXT/audits/<city>/paper_allowlists/val.txt
$EXT/audits/<city>/paper_allowlists/test.txt
```

The procedure is intentionally two-pass:

1. select evidence/graph-qualified episodes;
2. cluster their physical paper PUDO + trusted entrance anchors;
3. resolve cross-split anchor conflicts with `test > val > train`;
4. reselect final allowlists.

A final paper set should have **zero residual physical-anchor leakage** across train/val/test.

If a city/split allowlist becomes empty, treat that as an evidence-coverage failure to solve, not as a reason to disable the gate.

---

## 16. Strict allowlisted paper build

```bash
bash scripts/build_abilitybench_data0_20260817.sh qa-snapshot
bash scripts/build_abilitybench_data0_20260817.sh paper-build-allowlisted
bash scripts/build_abilitybench_data0_20260817.sh merge-all
bash scripts/build_abilitybench_data0_20260817.sh qa-strict
bash scripts/build_abilitybench_data0_20260817.sh qa-bundle
```

`paper-full` is now only an alias for the allowlisted second-pass build. Do not run it immediately after bootstrap and expect all scenes to pass strict paper evidence.

Expected final datasets:

```text
$CAP_DATA/outputs/datasets/abilitybench_av_train
$CAP_DATA/outputs/datasets/abilitybench_av_val
$CAP_DATA/outputs/datasets/abilitybench_av_test
$CAP_DATA/outputs/datasets/abilitybench_av_all
```

The merged directory keeps split files; model training must use those split definitions rather than randomly splitting the merged samples.

---

## 17. Compact QA artifacts to upload for remote checking

After the full run, the most useful small artifact is:

```text
$REPORTS/capplan_paper_qa_bundle.zip
```

It intentionally excludes huge nuPlan DBs, GIS geometries, DEM rasters and graph payloads.

At minimum, upload or inspect these reports:

```text
$REPORTS/nuplan_db_cities.train.json
$REPORTS/nuplan_db_cities.val.json
$REPORTS/nuplan_db_cities.test.json
$REPORTS/georeference_spatial_alignment.json
$REPORTS/recommended_public_sources.json
$REPORTS/dem_tiles_boston.json
$REPORTS/dem_tiles_pittsburgh.json
$REPORTS/dem_tiles_vegas.json
$REPORTS/dem_tiles_singapore.json
$REPORTS/external.paper.json
$REPORTS/pudo_audit_prefill.<city>.json
$REPORTS/pudo_audit_classify.<city>.json
$REPORTS/pudo_audit_source_review.<city>.json        # if used
$REPORTS/manual_audit_layers.source.<city>.json      # if used
$REPORTS/manual_audit_layers.manual.<city>.json      # if used
$REPORTS/paper_select_pre_site.<city>.<split>.json
$REPORTS/paper_select.<city>.<split>.json
$EXT/audits/<city>/paper_anchor_site_disjoint_exclusions.json
$REPORTS/dataset_quality.paper.train.json
$REPORTS/dataset_quality.paper.val.json
$REPORTS/dataset_quality.paper.test.json
$REPORTS/dataset_quality.paper.all.json
$REPORTS/four_city_paper_readiness.strict.json
```

The QA bundle packager gathers the key small artifacts automatically.

---

## 18. Hard acceptance gates for a publication build

### Source / identity gates

- DB city unknown = 0;
- DB city ambiguous = 0;
- DB basename cross-split intersections = 0;
- active report paths do not point to stale `$CAP_HOME/data` storage;
- georeference alignment passes all configured coverage gates;
- DEM coverage >= 0.99;
- required public-source fetch/normalization reports PASS;
- provenance placeholders = 0 and hashes/source metadata are present.

### Static/service evidence gates

For a publication PUDO:

- core physical fields are present and same-inventory authoritative/audited;
- legal-stop evidence is independently authoritative/audited;
- pedestrian binding exists;
- entrance source is reviewed/manual or another explicitly trusted entrance source;
- candidate/source is not mock/synthetic/proxy;
- evidence time/provenance is retained.

For a publication episode:

- >=2 paper-eligible PUDOs;
- >=2 distinct PUDO sites;
- >=2 trusted entrances;
- >=2 reachable trusted entrance/PUDO pairs;
- graph node/edge floors pass;
- no final site exclusion removes the episode.

For final split integrity:

- final allowlists non-empty for every required city/split;
- physical PUDO/entrance residual leakage = 0;
- capability profiles for the same physical episode remain in one split.

### Label/claim health gates

Do not publish a main set with:

- zero feasible skeletons everywhere;
- zero positive passenger-complete labels everywhere;
- all failure certificates at one phase/resource;
- no binding capability counterfactuals;
- unverified example vehicle interface.

Report per-city and pooled label distributions, failure phases/resources and capability-axis binding rates.

### Statistical reporting

There is no universal magic episode count because samples are clustered by physical scene/site and capability variants are correlated. Choose sample size based on the paper's primary endpoint and desired confidence/power, and use scene/site-grouped bootstrap or clustered inference.

As a rough **independent Bernoulli** reference only, a worst-case 95% confidence interval with ±5 percentage-point half-width needs about 385 independent observations; ±10 points needs about 96. Do not treat multiple capability profiles or repeated visits to the same physical curb as independent observations when reporting uncertainty.

---

## 19. Train the current implementation without overclaiming the architecture

After `qa-strict` PASS:

```bash
bash scripts/build_abilitybench_data0_20260817.sh train-surrogate
```

This trains the current relation-aware CASA surrogate with balanced sampling and typed auxiliary heads. Name it accordingly in experiments, for example:

`CASA relation-aware transition surrogate`

Do not call the present code a full graph HGT/RGCN implementation. To make the paper's graph-network claim literal, add explicit heterogeneous graph tensors/message passing over node/edge types and then add an ablation against this surrogate.

---

## 20. nuPlan closed-loop evaluation for strong T2/T3 claims

The repository includes a dataset-bound wrapper, but the real nuPlan simulation command/config still has to be provided for the user's installed nuPlan environment.

Example wrapper shape:

```bash
python scripts/run_nuplan_closed_loop_pipeline.py \
  --dataset_dir "$CAP_DATA/outputs/datasets/abilitybench_av_test" \
  --output_dir "$CAP_DATA/outputs/eval/nuplan_test" \
  --stages export,run,import,eval \
  --nuplan_run_command '<YOUR_REAL_NUPLAN_SIM_COMMAND using {job_dir} {dataset_dir} {output_dir}>' \
  --casa_mode learned \
  --casa_checkpoint "$CAP_DATA/outputs/models/casa_relation_surrogate/checkpoint.pt"
```

If nuPlan simulation is run separately and produces metrics:

```bash
python scripts/run_closed_loop_eval.py \
  --dataset_dir "$CAP_DATA/outputs/datasets/abilitybench_av_test" \
  --output_dir "$CAP_DATA/outputs/eval/capplan_full" \
  --trajectory_mode nuplan_closed_loop \
  --casa_mode learned \
  --casa_checkpoint "$CAP_DATA/outputs/models/casa_relation_surrogate/checkpoint.pt" \
  --paper_mode \
  --import_nuplan_metrics_from '<NUPLAN_METRICS_FILE_OR_DIR>' \
  --vehicle_metrics "$REPORTS/eval/vehicle_metrics.full.json" \
  --passenger_metrics "$REPORTS/eval/passenger_metrics.full.json"
```

A paper-mode run should fail rather than silently replace closed-loop vehicle evidence with a smoke/mock/proxy trajectory.

---

## 21. T1–T5 result matrix to report

### T1

Report passenger access/egress + PUDO feasibility on the audited paper subset, stratified by city, capability profile and evidence confidence. Include feasibility and typed failure reasons.

### T2

Report vehicle-only closed-loop safety/comfort metrics separately from passenger-complete metrics. Keep the current motion surrogate clearly named as a surrogate unless ISO-2631 is actually implemented.

### T3

Report full completion rate and end-to-end failure phase/resource, requiring the whole entrance/PUDO/vehicle chain.

### T4

Report same-scene capability counterfactual deltas over the seven configured axes; cluster confidence intervals by physical episode/site.

### T5

Report certificate correctness/coverage/diversity, including phase/resource distributions and representative evidence-backed failure cases. Avoid using the all-origin bootstrap pilot as the main diagnostic result.

Recommended core ablations:

- full model;
- no capability compiler;
- no service automaton;
- no CASA transition scoring;
- no typed resource ledger;
- no conservative margins;
- no completion-value guidance;
- soft-only capability constraints;
- full graph model vs current relation-aware surrogate, if the graph model is implemented.

---

## 22. Final practical sequence

Run these in order, stopping when a gate fails:

```bash
# A. repair + base integrity
bash scripts/build_abilitybench_data0_20260817.sh inspect-nuplan
bash scripts/build_abilitybench_data0_20260817.sh prepare-osm
bash scripts/build_abilitybench_data0_20260817.sh fetch-public-force
bash scripts/build_abilitybench_data0_20260817.sh validate-dems
bash scripts/build_abilitybench_data0_20260817.sh bootstrap-preflight
bash scripts/build_abilitybench_data0_20260817.sh qa-snapshot

# B. enumerate all candidates and compress audit work by physical site
bash scripts/build_abilitybench_data0_20260817.sh bootstrap-candidates-full
bash scripts/build_abilitybench_data0_20260817.sh site-catalogs
bash scripts/build_abilitybench_data0_20260817.sh prefill-audits
bash scripts/build_abilitybench_data0_20260817.sh classify-audits

# C. HUMAN: review source_complete_review_candidates.csv row by row
#    set review_accept=true; set entrance_linkage_approved=true when justified
export REVIEWER_ID="reviewer-001"
export CONFIRM_SOURCE_REVIEW=YES
bash scripts/build_abilitybench_data0_20260817.sh review-source-complete-audits
bash scripts/build_abilitybench_data0_20260817.sh import-source-complete-audits

# D. HUMAN / new authoritative evidence only for unresolved rows
#    populate actual pudo_audit_manual_completed.csv as needed
bash scripts/build_abilitybench_data0_20260817.sh import-completed-manual-audits

# E. replace example fleet and complete provenance registry before paper gates
bash scripts/build_abilitybench_data0_20260817.sh build-provenance
bash scripts/build_abilitybench_data0_20260817.sh paper-preflight

# F. freeze evidence -> select site-disjoint paper subset -> strict build
bash scripts/build_abilitybench_data0_20260817.sh rebuild-paper-evidence-full
bash scripts/build_abilitybench_data0_20260817.sh select-paper-allowlists
bash scripts/build_abilitybench_data0_20260817.sh qa-snapshot
bash scripts/build_abilitybench_data0_20260817.sh paper-build-allowlisted
bash scripts/build_abilitybench_data0_20260817.sh merge-all
bash scripts/build_abilitybench_data0_20260817.sh qa-strict
bash scripts/build_abilitybench_data0_20260817.sh qa-bundle

# G. current surrogate baseline
bash scripts/build_abilitybench_data0_20260817.sh train-surrogate
```

If `qa-strict` fails, fix the reported evidence/coverage/leakage issue and rerun from the earliest affected stage. Do not lower a publication gate merely to make the build finish.

---

## 23. External nuPlan facts used to sanity-check this design

The official Motional nuPlan devkit documents the v1.1 dataset update (improved route plan, traffic-light status and mission goal), the `nuplan-v1.1` / `nuplan-maps-v1.0` setup, and an explicit `db_files` scenario-builder option that can override `data_root`. The mature nuPlan benchmark paper reports a 1282-hour, four-city dataset and emphasizes closed-loop simulation/evaluation for planner behavior. These facts support using the user's DB files as the driving benchmark substrate while keeping CapPlan's passenger-service evidence as a separately provenance-controlled extension.

---

## 24. What this optimized pipeline deliberately does **not** claim to solve

- It cannot create missing real-world physical measurements from code.
- It cannot infer legal PUDO truth from a generic curb/parking candidate.
- It cannot make nearest-entrance matching semantically true without a trusted source relation or review.
- It cannot turn a current dynamic/legal layer into historical nuPlan scene-time truth.
- It cannot turn an example vehicle-interface file into verified fleet evidence.
- It does not yet make the current CASA `hgt/rgcn` implementation a true heterogeneous graph neural network.
- It does not replace an actual nuPlan closed-loop planner run for strong traffic-safety claims.

Those are scientific boundaries, not implementation inconveniences. Keeping them explicit makes the final dataset and paper much more defensible.
