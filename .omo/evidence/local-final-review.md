# Combined local scene final review — 2026-09-30

Verdict: **PASS — implementation, visual correction, and deployed final GPU captures verified. No remaining blocking finding in the reviewed scope.** Read-only review except this report. Scope is the requested combined partial scene, actual imagery/elevations, terrain camera, and truthful GPU/model claims; not a repository-wide audit.

## Resolved delivery finding

- **Resolved P1 delivery gap:** at initial review time `compose.yaml:24` mounts `var/rendering/omniverse/local-01` as `/srv/local-rtx`, while the final widened overview and terrain camera were rendered in `local-02`. `renderers/mock/nginx.conf` has no exact `/omniverse/local-terrain.png` route, so the documented terrain GPU URL falls through to 404. Switch the mount to local-02, add its terrain route, recreate/reload preview, and verify the advertised HTTP captures. Root was notified immediately. This does not invalidate the local-02 renders or browser surface.


Follow-up scoped recheck: Compose now mounts `local-02`, Nginx has the exact terrain PNG route, and the UI credits expose the terrain link. Independently fetched all four deployed PNG URLs from `http://127.0.0.1:8080`: each returned HTTP 200 and matched the corresponding final local-02 file SHA-256. Reviewed `var/verification/local/http-qa.json`, recording matching scene/manifest/credits/JS/USD/captures and five private-path 404 responses. The delivery finding is closed; no full browser rerun was needed for this mount/route/link correction.

## Specification and code

- Current `app.js` loads `loadGrid()` once; no wind/grid mode switch remains. Camera buttons move within the same scene; layer shortcuts restore required layers. One local GLB holds wind, PV, station, representative line, and the shared DSM. `grid.js` consumes only clipped `paths` for display while preserving source provenance in manifest. Point facilities and route records total 21.
- Reviewed current `build_local.py`, `prepare_imagery.py`, app/grid/index/style, local UI delta, allowed Kit camera arguments, Nginx/Compose, plan, asset-review report, and current docs. Builder preserves source transforms, DSM vertices, projected geographic UVs, and independent rotors, with runnable verification of clipping/reentry, hashes, heights, normals, and embedded JPEG. Imagery downloader validates JPEG dimensions and masks credential-bearing exceptions; metadata records placeholder credentials, acquisition time, unknown photography date, and provider attribution.
- Prior asset-review stale terrain-material finding is fixed: current manifest explicitly describes georeferenced VWorld Satellite JPEG and omits the palette. Old source assumptions remain separately attributed.
- Independently computed SHA-256 for current scene and manifest against local-02 evidence: both match. Scene SHA: `2e2ee1be025368f8945be770f209813b9180dd16a62977c0225263e7e950ecf7`. All four capture bytes match their evidence hashes. The source DSM range is -1.4475 to 759.5271 m; approximate 0–760 m documentation is reasonable. Terrain focus is an actual 511.37445 m source vertex, and vertical scale is 1.

## Direct visual review

Opened fresh 02:20 browser images `1280-combined-wind.png`, `375-combined-wind.png`, `1280-terrain-3d.png`, `375-region.png`, and actual GPU images `local-02/terrain.png` and `local-02/array.png`.

- Desktop and mobile expose a single set of scene cameras and layer controls; no mode split is visible. Mobile has readable wrapped controls and vertically flowing facility information. Whole-region camera fits the partial rectangle in the narrow viewport. Facilities are small at geographic scale; users have selection/inspection controls.
- Terrain view visibly shows hills, slopes, valleys and foreground occlusion under recognizable geographic imagery. It is not a photograph on a flat plane. The actual RTX terrain result agrees with browser relief; wind RTX shows the turbine array and neighboring real coast together.
- Sea imagery has conspicuous angular color/photography seams. These are visible in the shared source imagery and disclosed in docs; not a missing triangle or invented coast. No blocking visual defect found in reviewed views.
- Browser QA JSON reports PASS at 1280/768/375, 21 selectable records and one GLB request; root supplied lifecycle/manual QA. I inspected those artifacts rather than claiming an independent interactive browser run.

## GPU and downloaded-model honesty

Local-02 evidence records Kit 106.5 RTX, GPU1, 32.75 seconds, 418 mesh instances/922,830 faces, and the matching current source hashes. GPU1 sampling reaches 100% utilization and ~3401 MiB. Browser WebGL versus offline server GPU PNG versus real-time streaming is explicitly distinguished.

Photo inference evidence records actual GPU0 TRELLIS.2/DINOv3/RMBG/decoder execution on a real Commons Jeju photograph, but the result is dominated by the lighthouse and was not adopted as a turbine. Current docs accurately keep scene facility geometry classified as code-based estimates. No claim of model-generated scene reconstruction is justified or made.
