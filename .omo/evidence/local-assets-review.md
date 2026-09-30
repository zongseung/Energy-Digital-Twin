# Local combined asset review — 2026-09-30

Verdict: **Task 1 is functionally compliant; one non-blocking metadata correction remains.** Review was read-only except this report. I did not rerun the builder or mutate generated assets.

## Finding

- **P2 — stale terrain material description.** `renderers/twin/build_local.py:160` copies `grid["terrain"]` verbatim after `apply_imagery()` replaces all 23 grid terrain mesh materials with the VWorld JPEG. Consequently `var/rendering/local/manifest.json` still says `terrain.materials: "source landcover classes with synthetic colours; no photograph textures"`, which contradicts the combined GLB and the manifest's `imagery` section. Update this field in the local manifest to describe the imagery, while retaining landcover provenance elsewhere. This could mislead the browser UI and downstream provenance documentation. It does not affect rendered pixels.

## Evidence

- Source/output GLB JSON inspection: all 220 `T57*` wind nodes in the source GLB are present locally. All selected grid roots/descendants are present: `S888`, `P93955`, `P93709`, `P93187`, `L3596`. Local GLB has 513 nodes, 61 meshes, 23 grid terrain nodes, one embedded image; no twin `terrain_land` or `shore_basalt`. `L3596` includes 236 pylon descendant nodes. The 23 terrain nodes are chunks of one grid DSM, not 23 stacked terrains.
- Source/output manifests: 10 wind, 3 PV, 1 substation, 1 line within the grid extent; 7 routes with non-empty clipped `paths`, 90 clipped coast paths. Full route `points`, IDs, source IDs, and geographic coordinates are retained for provenance. Source `coordinateFrame` values and units match; output positions are unchanged. Builder verification reports PASS for reopened GLB, facility/rotor transforms, normals, source hashes, DSM vertices, and projected UVs.
- `prepare_imagery.py` places WMTS rows north-to-south. `build_local.py:112-115` calculates a south-origin V; trimesh's GLB exporter flips V when writing `TEXCOORD_0` (`trimesh/exchange/gltf.py:884-886`), so the JPEG's north-up row order is correct in the exported GLB. The builder's reopened UV check is consistent with that convention.
- Independently checked all 375 cached tile files: 375 unique z/x/y keys, no missing files, no SHA-256 mismatches against `var/rendering/imagery/manifest.json`; mosaic is JPEG 6400 × 3840. Visual inspection shows real geographic satellite imagery with recognizable shorelines/fields. Manifest records VWorld Satellite WMTS, source URL template, tile hashes, Web Mercator bounds, acquisition time, and unknown photography date; it does not claim surveyed asset accuracy.
- Checked the configured credential's bytes against `scene.glb`, local manifest, verification JSON, credits, mosaic JPEG, and imagery manifest: no match. Output URL template contains `{key}` rather than the credential.

Scope limit: this was an asset/source review, not browser or Kit runtime QA. The provenance checks establish consistency with cached VWorld-labeled tile files; they do not independently attest provider capture dates or redistribution rights.
