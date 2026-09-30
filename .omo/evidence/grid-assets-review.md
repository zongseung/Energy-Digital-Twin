# Grid asset code and data review

- codeQualityStatus: CLEAR
- recommendation: APPROVE
- reportPath: `.omo/evidence/grid-assets-review.md`
- blockers: none remaining in this code/data review scope; final browser visibility QA remains owned by root.
- Scope: `renderers/twin/build_grid.py`, `grid-spec.json`, and final generated grid manifest/GLB/verification. `grid.js` was also reviewed for the minimal overview-overlay correction.
- Reviewed GLB SHA-256: `9dbbc83168238d1ac59416082cd03493d577d2a5550184440ea1f177bb258173` (10,735,372 bytes).
- Base commit: `444deed3f9570495a6ea7cbcd17dd957a540e2cf`; working-tree review, not approval of an immutable implementation commit.

## Findings

### CRITICAL
None.

### HIGH
None.

### MEDIUM

**RESOLVED: Overview route strokes could disappear beneath the displayed terrain between original vertices.** `renderers/twin/build_grid.py:501` assigns native DSM + 8m at source vertices, but straight interpolation between those vertices is not terrain-following. The prior `renderers/twin/grid.js` built the display line from those points with depth testing enabled. Independent sampling at at most 20m spacing inside the displayed terrain crop found:

| Source route | Minimum gap above displayed terrain | Samples below terrain |
|---|---:|---:|
| `hub:power_line:3596` | -1.971m | 5 |
| `hub:power_line:3592` | -0.868m | 4 |

The original self-test checked physical conductors rather than these separate schematic route strokes. Checking source vertices alone would have missed the original display defect.

Root applied the smallest scoped fix in `renderers/twin/grid.js:48`: overview `LineMaterial` uses `depthTest:false` and `depthWrite:false`, and the `Line2` has `renderOrder=10`. The route detail explicitly identifies `GIS 경로 오버레이`. This prevents the schematic stroke from being rejected by terrain depth without changing physical conductor occlusion or claiming surveyed route altitude. Source coordinates and IDs remain unchanged; no resampling or asset rebuild was introduced.

Re-review independently inspected the changed construction and labels, ran `node --check renderers/twin/grid.js` (exit 0), and recalculated `scene.glb` SHA-256: it remains the reviewed `9dbbc83168238d1ac59416082cd03493d577d2a5550184440ea1f177bb258173`. The code-level MEDIUM finding is closed. Refreshed browser screenshots are being captured by root and are not claimed as reviewed here.

### LOW
No additional actionable findings.

## Verified evidence

Independently executed `uv run renderers/twin/build_grid.py --self-test` against the stated final artifact: PASS. Inspected the complete builder and specification and compared the generated metadata with source-facing invariants.

- 54 source line records, 5 `minor_line` records excluded, 3 exact forward/reverse geometry duplicates grouped, 46 displayed routes.
- Dedup key is type + voltage + the complete coordinate sequence modulo reversal. All grouped IDs and original source properties remain in the manifest.
- All three HVDC groups retain mainland endpoints and source coordinates; there is no AOI truncation.
- 13 substations preserve source IDs, coordinates, and properties. Same-name records are not merged by name or voltage.
- 3 distinct PV registration/geocoded coordinates are selected deterministically. Their coordinate multiplicities are disclosed (13, 3, 25 source records at the selected coordinates).
- PV source capacities are retained as source metadata (197.02, 27, 99 kW, data date 2025-09-15). Each separate 32-panel demonstration is explicitly estimated and independent of capacity; actual panel count/footprint/live solar power remain unknown/null.
- Shared frame remains UTM zone 52N (`EPSG:32652`), vertical `EPSG:3855` / EGM2008, metre scale 1, X east / Y up / Z south. Projection/inverse checks pass.
- Representative route 3596 preserves all 44 original vertices among 59 estimated pylon locations. Maximum span check passes; six physical conductors and one guard wire are present.
- Reopened GLB physical wire surface minimum above native DSM and the displayed-terrain sampling model: **8.180451m**. Exported PV panel surface minimum: **1.731633m**.
- GLB source hashes, calibration hash, output hash, finite vertices, unit normals, unique node names, expected child hierarchy, and source-preserving transforms pass.
- Terrain is a partial native DSM crop with two-pixel sampling; colours are explicitly synthetic landcover colours. Full-island coast is source-derived and its simplification tolerance is disclosed.
- No from/to connectivity, MW flow, load, current, turbine grid connection, or individual real-time PV output is invented.

## Programming and remove-ai-slops perspective

Both skill perspectives were consulted. Source preservation, coordinate inversion, mesh reopening, normal direction, and actual geometry clearance tests are meaningful behavior checks rather than deletion-only or prose-pinning tests. Fixed source counts are appropriate snapshot-integrity assertions, not arbitrary implementation constants. The builder's single-file size exception is documented and the code reuses existing geometry primitives; this review does not request an unrelated architecture rewrite.

The physical-wire clearance test and overview-overlay visibility concern different surfaces. The overlay correction was checked at its material/construction seam without adding an implementation-mirroring test; root owns final browser visibility evidence. Keep physical clearance and schematic overlay claims distinct.

## Bounded limitations

Clearance results are sampled visual checks against a coarse surface model, not continuous engineering safety clearance, a ground survey, or bare-earth validation. The display-height helper approximates the projected terrain triangles; the report does not claim survey precision. Estimated tower dimensions, uniform mast extension, sag, substation layouts, PV arrangement, and registration-coordinate uncertainty are disclosed and remain within the approved demonstration scope. No full survey is requested.

Native Kit integration and final browser visibility are owned by other agents and are not certified here. No source data or implementation file was edited by this reviewer.
