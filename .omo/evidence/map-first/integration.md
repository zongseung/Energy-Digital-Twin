# Map-first integration verification

**PASS** — final localhost surface, 2026-09-30 08:19 UTC. Main session drove and inspected the actual application. Browser checks:37 scenario captures across375/768/1280px; all exit0. No application exceptions in normal use. Base HEAD: `cf3a9e9e355aa3cbd43b379f20fe8ed4608ce1a8`; verification covers the dirty tree identified below, not a committed release.

## Surface and commands

`node tests/map-first-browser.mjs 375`, `node tests/map-first-browser.mjs 768`, `node tests/map-first-browser.mjs 1280` — all target `http://localhost:8080/`, height900px. Owned disposable Chromium profiles through omowright, real native/CDP mouse, wheel, keyboard and touch inputs. All profiles closed and removed. Renderer query reports ANGLE/NVIDIA Vulkan/RTX A6000. No new dependencies or production browser flags.

Reports: [375px](375-qa.json), [768px](768-qa.json), [1280px](1280-qa.json). Each report records assertions, scenario screenshots, viewport bounds, runtime exceptions and WebGL draw counters.

| Scenario | Observed result | Evidence |
|---|---|---|
| Initial map / selection | Full width;816px map height out of900px; no selected facility or open panel; no page overflow | `375-initial.png`, `768-initial.png`, `1280-initial.png`; all three reports |
| Facility detail / operating truth | Immediate detail; actual operating state 확인 불가; output/time —; nearby measured weather and unavailable irradiance | `375-detail.png`, `768-detail.png`, `1280-detail.png`, `*-weather.png` |
| Mobile sheet |40% initially; expands to70%; camera controls above sheet and below tool rail | `375-expanded.png`;375 report |
| Native map click / overlap | Map marker mouse click chooses or opens candidate list, then detail | `*-map-click.png`; all reports |
| Actual mesh click / close-up | Explicit close-up completes; click on canvas mesh with DOM marker interception disabled opens detail | `1280-mesh-click.png`;1280 report |
| Layer off | Hidden wind markers cannot be selected; tool panel does not require a selected facility | `*-layers.png`, `*-wind-hidden.png`; all reports |
| Search / keyboard | Empty search feedback; reset restores list; Enter selects; Escape closes and restores origin/map focus | `*-keyboard.png`; all reports |
| Camera inputs | Real cursor wheel, left pan, right orbit alter projected pose; no accidental selection after drag; actual one-finger touch pan also asserted at375px | `*-navigation.png`; all reports |
| Panel scroll | Wheel scrolls information body while camera pose stays unchanged | `*-panel-scroll.png`; all reports |
| Historical analysis | Existing regional history remains available; current weather explicitly separate | `*-analysis.png`; all reports |
| Reduced motion / idle | Live reduced-motion preference supported; camera settles;350ms idle draw count unchanged at every width | `*-final.png`; report `idleDrawCalls` |
| Scene failure | Lost WebGL retains facility/weather UI; deliberately blocked GLB retains source catalog/weather, disables camera; exactly one expected load error | `1280-context-lost.png`, `1280-load-failed.png`;1280 report |

`*` in artifact names means375,768 and1280; each named file exists. Main session visually inspected final mobile expanded sheet, tablet detail, desktop map and the error layout. Panel content scrolls on mobile; expanding the sheet gives more reading space.

## Regression checks

All commands exited0:

- `node tests/map-navigation.mjs`:8 behavioral groups using actual installed OrbitControls — pan/Shift orbit, damping idle, Ctrl/Meta wheel, interrupted350ms focus, visibility/reduced motion, bounds/ground clearance/tools, disposal, touch pinch and cursor/horizon zoom. [Evidence](navigation.md).
- `node tests/map-grid.mjs`: actual Three.js raycasting/Line2; bounds, top terrain/roof elevation, spatial culling, hidden ancestors/children/layers, unique hit ordering. [Evidence](selection.md).
- `node tests/wind-estimate.mjs`, `node tests/playback.mjs`: existing model/playback regressions.
- `node .omo/evidence/real-weather/ui-check.mjs`: actual `showWind` with null/stale/weather-only values, negative temperature and zero rain; measured values cannot become rotor estimates.
- `node --check --input-type=module < renderers/twin/app.js`, `node --check tests/map-first-browser.mjs`, `git diff --check`.
- `docker compose --profile preview exec -T preview nginx -t`: configuration valid. `curl -I http://localhost:8080/twin/navigation.mjs`:200/application/javascript. Missing navigation route was fixed and nginx reloaded before final tests.

No TypeScript/Biome server is installed; installation was previously declined. Runtime/syntax/real-surface checks were used. No Lighthouse score is claimed. Existing API/Rust behavior was preserved; backend checks from the earlier weather task are not presented as fresh redesign checks.

## QA environment failure and resolution

Some repeat browser launches stalled before HTTP dispatch. Controlled A/B isolated the default Linux password-store backend. QA now adds `--password-store=basic` to fresh, disposable profiles. [Diagnosis and exact proof](browser-diagnosis.md). No application workaround was made. Final reports preserve Chrome's unrelated background GCM endpoint/quota stderr separately from application errors. The blocked-GLB test also preserves its expected error separately; it does not suppress unexpected errors.

## Verified tree SHA256

| File | SHA256 |
|---|---|
| `renderers/twin/app.js` | `a22ccde425810182261a7310c2930a03cf40641b07006ddea18962d8ba917e8b` |
| `renderers/twin/style.css` | `a9e4be7547ca0fc529980ef40d8dd2bcb0e3840612abf7570fb8801541afa370` |
| `renderers/twin/index.html` | `7c8a9b52a815a69f70548707d0a2148c8f3be499d9b194bb1ea72236e788daba` |
| `renderers/twin/navigation.mjs` | `7361a8e07067ea78637bd82656d7ff2098a86ef17ae835c7e512b5c6520fa5f7` |
| `renderers/twin/grid.js` | `2e8c3b9b64ca348509e824e634c586fd6025d7db37e4895b96617c69d86618d2` |
| `renderers/mock/nginx.conf` | `9e7bb059e4f159ba964f695c971a6c580c3d681873d1a5af01da200209ecc21f` |
| `tests/map-first-browser.mjs` | `bd25ed083d4cfc41186959255e65e418c1b3cbb860c0713f0343785e9584609f` |

Camera/selection workers owned bounded files. Existing imagery, geometry and concurrent green-layer work were preserved. No commit, push or deployment beyond the authorized local preview was performed.
