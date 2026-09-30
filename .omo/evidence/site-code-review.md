# VWorld site code review

- codeQualityStatus: CLEAR
- recommendation: APPROVE
- blockers: none remaining in code review scope
- Reviewed at: 2026-09-30 Asia/Seoul
- Base HEAD: `444deed3f9570495a6ea7cbcd17dd957a540e2cf`; this review covers the current uncommitted files, not an immutable commit approval.
- Scope: `renderers/site/{DESIGN.md,index.html,app.js,style.css,configure.py,prepare.py}`, `renderers/mock/nginx.conf`, `compose.yaml`, supporting ignore and API contracts.
- Production files were not edited by this reviewer. Only this report was written. Actual `.env` and probe HTML were not read; no authentication value was printed.

## Findings by severity

### CRITICAL
None.

### HIGH
Resolved during review: exact `/` and `/site/` routes used a file alias for trailing-slash requests. The nginx index module treated the alias as a directory and produced a path ending `index.htmlindex.html`, returning HTTP 500. Reproduced in a disposable network-isolated container of the same pinned nginx image with dummy HTML, then reported to root. Root changed both routes to `root /srv/site-data; try_files /index.html =404`. Independent live checks now return 200, `text/html`, `Cache-Control: no-store` for both paths. No remaining blocker.

### MEDIUM
None established.

### LOW
None requiring code changes for this scope.

## Skill-perspective check

Consulted OMO `programming`, its Rust/Python references, and `remove-ai-slops` perspectives. Checked tests and production code for deletion-only or implementation-mirroring assertions, brittle prose tests, unjustified abstractions, and unnecessary parsing. No material overfit/slop finding. The config self-test exercises special-character URL encoding, HTML attribute safety, missing/duplicate placeholders, and invalid keys; preparation self-tests exercise coordinate direction, positive-height handling, and raster bounds. Script style is deliberately smaller than a general Python application; no framework or general configuration library is warranted for the single-key renderer.

## Independent verification

- `node --check renderers/site/app.js`: exit 0.
- `python3 renderers/site/configure.py --self-test`: passed.
- `uv run renderers/site/prepare.py --self-test`: passed metre projection, unexaggerated height, unknown height, and pixel bounds.
- Live HEAD requests: `/`, `/site/`, `/mock/` 200 HTML; `/site/app.js` 200 JavaScript; `/site/buildings.geojson` 200 GeoJSON.
- Live HEAD requests: `/.env`, `/site/configure.py`, `/site/prepare.py`, `/site/scene.json` all 404.
- Live building export parsed independently: 623 unique IDs; all heights finite and positive; all height status values `provider_positive_unverified`; only Polygon/MultiPolygon geometries.
- Runtime HTML parsed without printing URL values: one external HTTPS script on official `map.vworld.kr`; query fields only `version` and `apiKey`; version 3.0; nonempty browser key; no unresolved placeholder.
- Temporary fake env fixture with unrelated server-password and database sentinels: `read_key` selects only `vworld_key`, and generated HTML contains neither unrelated sentinel. Real `.env` not used by this test.
- Runtime output is under `/var/`, already excluded by `.gitignore`. Nginx serves individual intended site artifacts and proxies the local API; it does not expose the source directory as an unrestricted root.

## Correctness and disclosure assessment

The app reads actual Rust assets/state routes, filters AOI Point records, preserves source feature IDs, distinguishes wind plant versus individual turbine records, and inserts source text through `textContent`. Missing/failed observation values become a dash rather than invented numbers. The map and API failure paths are separate. SDK navigation reuses `vw.Map` and its viewer camera; there is no custom turbine marker generation or synthetic timeline.

Building extrusion consumes only finite positive provider heights and labels the result as monochrome source-footprint LOD1, not photo-textured reconstruction. The preparation artifact retains unverified-height status. Browser access to the intended VWorld client key is by design; it is not treated as a server secret.

## Limits

The parent owns actual browser QA. This report does not certify real SDK imagery/terrain readiness, visual fidelity, building supply/height accuracy, keyboard/mobile behavior, or historical imagery dates from source reading alone. No key domain registration was changed or bypassed. Photo-based facility reconstruction remains incomplete and is disclosed in the UI. Runtime HTML contents and tokens are deliberately omitted from this artifact.
