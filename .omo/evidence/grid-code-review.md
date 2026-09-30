# Grid/PV expansion code review

codeQualityStatus: WATCH
recommendation: APPROVE
blockers: []
Verdict: PASS for scoped code review; real-browser/GPU and generated asset verification remain separate.

## Scope and criteria

Read current renderers/twin/app.js, grid.js, index.html, style.css, renderers/mock/nginx.conf and compose.yaml; consulted DESIGN.md and docs/superpowers/plans/2026-09-30-grid-terrain.md. Scope is transmission/HVDC/substations, partial terrain and three estimated PV installations; no distribution, fabricated connectivity or actual output inference. Full current source was inspected because these files remain untracked additions and no immutable before/after diff was supplied. No notepad was supplied. ulw-loop status returned ULW_LOOP_PLAN_MISSING, so this is the fallback report artifact. No product files were edited.

## CRITICAL
None.

## HIGH
None.

## MEDIUM

- renderers/twin/app.js:1: the orchestration module now has 271 nonblank/noncomment lines, above the remove-ai-slops skill's 250-line threshold. It owns scene setup, DOM selection, mode switching and observation networking. This is a maintainability perspective departure, not a demonstrated regression or approval blocker. Grid geometry is already separated appropriately. On the next substantial expansion, move the existing observation stream/UI responsibility intact to its own module; do not add a generic event/state framework or split by arbitrary line count.

## LOW

None open. Prior HVDC shortcut inconsistency is resolved: the action now checks the HVDC checkbox, enables that layer, refreshes the list and changes camera.

## Independent checks

- `node --check renderers/twin/app.js` and `node --check renderers/twin/grid.js`: PASS.
- `docker compose --env-file .env.example --profile preview config --quiet`: PASS.
- Existing `node var/verification/twin/code-review-replay.mjs`: PASS, including 20 rapid tab switches, active connection reuse, stale callback suppression, current close recovery, lost-context animation/render suppression and wind GLB load continuation guard.
- New durable `node var/verification/grid/lazy-load-replay.mjs`: PASS. Executes current switchMode function against a controllable asynchronous loader. Confirms one in-flight load, failure retaining wind visibility, enabled retry, successful retry, cached scene reuse across mode switches, and context loss during loading preventing mode entry while controls remain disabled. Output: var/verification/grid/lazy-load-replay.log. This is a deterministic code-lifecycle check, not a browser run.

## Correctness and scope review

- Grid manifest/GLB load occurs on first grid transition; concurrent clicks are gated. Failure retains wind scene and supplies retry; loaded scene is reused. Observation API/WS flow retains its earlier reviewed lifecycle protections.
- Mode switching changes visible scene roots, fog, far plane and orbit distance, stops the rotor demo, rebuilds mode-appropriate lists and restores wind camera behavior.
- Layer controls affect physical roots, line objects and marker objects; list filtering uses matching record layers. Hidden-root pickables are filtered before raycasting, and distance-hidden GIS markers/representative line are excluded by their visibility. Physical root userData supports parent traversal from component hits.
- Selection IDs drive list state and details. Source route IDs, including deduplication IDs, are displayed. PV capacity_kw is labeled registered capacity, actual generation remains unavailable, and estimated panel count/layout is clearly disclosed. No grid connectivity or output calculation was added.
- All source strings use textContent; paths are fixed same-origin assets. Added Nginx grid paths are exact allowlisted resources, backed by read-only volume mounts. No directory-wide grid serving or secret-file exposure route was introduced.
- Native buttons/checkboxes preserve keyboard alternatives and inherited focus/44px target styles. Selected list items have aria-pressed state. Full screen-reader/contrast and mobile visual review are not claimed here.

## Skill-perspective check

Consulted the previously loaded omo:programming and omo:remove-ai-slops skills for this review. No deletion-only, requested-removal, prose/prompt, tautological or implementation-constant tests were added to production. Reviewer lifecycle replay controls outcomes and verifies externally meaningful transitions; it does not assert copied UI prose. Route validation occurs at the loaded-data boundary and coordinate/bounds conversion directly serves rendering and selection. No speculative abstraction or redundant production parsing pipeline found. Module size is the explicit slop-perspective departure recorded above. Duplicated render color literals are minor and do not warrant a separate blocker or design-system rewrite.

## Limits

Generated grid assets were pending during this review; absence was deliberately not treated as a finding. Route/facility counts, exact unit alignment, terrain node discovery, physical geometry, GPU rendering and real picking remain to be checked with the finished artifacts by their owners/QA. No measured frame rate, complete accessibility result or final product completion is claimed. No separate lint/typecheck/security configuration exists for this plain-JS scope; those automated gates are not claimed as passed.

## Final scoped delta recheck

Disposition remains APPROVE / WATCH, blockers []. The existing nonblocking module-size note remains; no new correctness finding in this delta.

- Confirmed HVDC restores its disabled layer. New terrain-overview action restores terrain, uses its own camera and is included in mode visibility and context-loss disabled-control sets.
- revealSelection is limited to grid mode; desktop scrolls the sidebar to details, narrow/short layouts bring the viewport into view. It does not alter source selection IDs or wind selection behavior.
- GIS Line2 paths explicitly use depthTest:false/depthWrite:false/renderOrder:10; the physical conductor meshes retain normal depth handling. UI text now describes overview paths as overlays and avoids presenting their display height/thickness as physical dimensions.
- Generalized scene/canvas aria-labels cover both wind and grid. Added GPU links correspond exactly to the Nginx allowlisted PNG paths. /omniverse/grid.usda points to estimated-scene.usda. The grid-01 directory is mounted read-only; no broader directory route is exposed.
- Reran JS syntax checks, lazy-load regression replay and actual preview nginx -t: all PASS.
- Inspected final browser-qa.json: PASS at 1280/768/375, including count checks (46 routes, 13 stations, 3 PV), layer restoration and mode switching; failure/context-loss checks are specifically identified as 1280 runs. Inspected http-qa.json: seven 200 artifact records with hashes, five private/directory paths returning 404 and API health 200. These are inspected parent QA artifacts, not independently repeated browser/HTTP hash tests in this narrow delta review.

The earlier generated-assets-pending limitation describes the initial review time; final runtime artifacts now exist and were inspected as noted here. GPU geometry review remains owned by its separate reviewer.
