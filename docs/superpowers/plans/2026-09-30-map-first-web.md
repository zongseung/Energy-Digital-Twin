# Map-first Web Implementation Plan

> Execute the approved design in this session; preserve the user's earlier authorization for parallel project work. No commits or new dependencies.

**Goal:** Full-width map with click details, honest operating/weather states, and responsive direct camera control.

**Architecture:** Retain native HTML/CSS/ES modules and the existing scene/data pipeline. One nonmodal panel switches between tools and selection; one frame loop serves camera damping and rotor demonstration.

**Tech Stack:** Installed Three.js 0.180.0, OrbitControls, native DOM, existing omowright/Chromium for real-browser checks.

**Spec:** [Approved design](../specs/2026-09-30-map-first-web-design.md).

## Global Constraints

- One existing geographic scene; no geometry rebuilding or coordinate changes.
- Initial viewport: 56px header, 28px status row, remaining full-width map; no automatic selection.
- One panel at a time, desktop360px/tablet320px, mobile40% expandable70% of map height.
- Actual operation/output remain unknown; retain measured weather and independent missing/delayed states.
- Preserve regional history/scenario and wind demonstration; no new API or irradiance assumption.
- Preserve unrelated edits, including ongoing groundcover work.

## Review Focus

- Hidden ancestor/layer must not remain pickable, including buildings and line meshes.
- Wheel on a panel scrolls that panel, never zooms the map; Ctrl/Meta-wheel preserves browser zoom.
- Rapid input interrupts focus movement; cancelled/multi-touch drags cannot become clicks.
- Geographic bounds and measured ground elevation prevent lost/nonfinite/belowground navigation.
- No current weather or rotor demonstration presented as historical/individual operational telemetry.

### Task 1: Real-surface regression and screen contract

**Files:** `tests/map-first-browser.mjs`, `renderers/twin/DESIGN.md`.

- [x] Write browser check for initial map ratio/no selection, facility/tool switching, weather visibility, true unknown state, mobile sheet, panelwheel isolation, layervisibility, reducedmotion.
- [x] Run against current localhost; record expected failure on old split layout.
- [x] Update DESIGN.md before frontend styling: tokens, primitives, focus, motion, responsive and scroll ownership.

### Task 2: Navigation and picking (parallel bounded ownership)

**Files:** `renderers/twin/navigation.mjs`, `tests/map-navigation.mjs`; `renderers/twin/grid.js`.

**Interfaces:** `createNavigation({camera,controls,canvas,bounds,groundHeight,reducedMotion})` -> `moveTo(view,scale),zoom(factor),north(),rotate(),cancel(),update(dt):boolean,dispose()`.

Grid produces `bounds:Box3`, `groundHeight(x,z):number`, `pick(camera,ndc,{firstOnly=false}):facility[]`.

- [x] Navigation worker: failing behavioral check, controls setup, cursorzoom/damping, interruptible focus, finite bounds/ground protection, green check.
- [x] Selection worker: hiddenancestor filtering, orderedunique candidates, cachedheight query; preserve existing scene.
- [x] Root integrates one frame loop, buttons/touch/click/drag/doubleclick, selectable DOMmarkers and overlap chooser.

### Task 3: Map shell and selected information

**Files:** `renderers/twin/index.html`, `style.css`, `app.js`.

- [x] Fullmap shell with compact tool buttons; one panel and independent body scroll.
- [x] Selection opens detail immediately, keeps camera, exposes unknown state/output/time, moves measured weather into visible detail.
- [x] Search/filter/list selection, weather/layers/analysis/source/help tools, explicit close/Esc/focus restoration.
- [x] Mobile expandable draggable sheet, safe controls, minimum44px targets, native keyboard alternatives.
- [x] Maintain all data/analysis handlers and one weather DOM source; cacheversion updated.

### Task 4: Integration verification and final review

**Evidence:** `.omo/evidence/map-first/`.

- [x] Node meaningful camera/wind/playback checks and syntax/diff checks.
- [x] Root drives actual localhost at375/768/1280px; records initial/detail/tool/sheet states and input outcomes/screenshots.
- [x] Test raycast facilityclick, rapidinput, layeroff, missingdata, historicalscope, panelwheel, reducedmotion, idleloop.
- [x] Fix failures; fresh finalsurface evidence.
- [x] Main session checks implementation against approvedspec and evidence, per Codex review-work override for ordinary implementation; no strict independent review requested. Verdict recorded in `.omo/evidence/map-first/gate-review.md`.
- [x] Deliver localhost link and concise controls summary.

Final implementation verification: `.omo/evidence/map-first/integration.md`,37 browser scenario captures and all applicable runtime checks passed. No new dependency. Fresh checks for the user-requested commit/push are recorded in `.omo/evidence/map-first/commit-preflight.md`.
