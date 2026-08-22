# Four-city evidence source matrix (2026-08-22)

Legend: **direct** = source semantics/field can directly support the stated fact after unit/linkage validation; **geometry** = real topology/geometry but not the numeric fact; **candidate** = useful candidate, not truth; **sim-only** = no suitable public site-level source verified in this review, so use hybrid simulated truth or further audit.

| City/source | pedestrian topology | sidewalk width | curb/ramp | curb height | PUDO legality | deployment clear area | entrance |
|---|---|---|---|---|---|---|---|
| Boston Sidewalk Inventory | geometry | direct field `SWK_WIDTH` (unit must be validated) | — | — | — | — | — |
| Boston Ramp Inventory | — | direct field `SWK_WIDTH` (unit must be validated) | direct ramp feature | `REVEAL` is a candidate source field; do not auto-map until semantic/unit verified | — | no | — |
| Boston PWD Cartegraph | geometry/physical | direct when per-record unit metadata exists | direct ramp feature | keep unknown unless source semantics explicitly establish curb reveal | — | no | — |
| Pittsburgh WPRDC Sidewalks & Steps | geometry | no site-width field verified | steps/sidewalk geometry | no | no | no | no |
| Pittsburgh PPA payment points | — | — | — | — | candidate parking context only | — | — |
| Pittsburgh PASDA address points | — | — | — | — | — | — | candidate/proxy only |
| Clark County `pwRamps` / `pwConcrete` | geometry | no measured width verified | geometry | no | no | no | no |
| Clark County Strip sidewalk line | geometry | no measured width verified | — | no | no | no | no |
| Singapore LTA Footpath / Kerbline | direct geometry | no public numeric field verified | curb geometry | no | no | no | — |
| Singapore LTA Passenger Pickup Bay | — | — | curbside candidate | no | **direct passenger pickup/dropoff designation** | no | — |
| Singapore LTA Train Station Exit Point | — | — | — | — | — | — | **direct station-exit location**, still only a candidate for trip-specific intended entrance |
| USGS 3DEP 1 m DEM | terrain geometry | no | no | raster too coarse for curb height | no | no | no |
| Copernicus GLO-30 | macro DSM | no | no | no | no | no | no |

The matrix is intentionally conservative. “Not verified” is not a proof that no government database exists; it means the current official/public machine-readable sources checked here do not establish a publication-grade site-level value for that field.
