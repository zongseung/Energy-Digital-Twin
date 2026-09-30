# Commit verification — 2026-09-30

Fresh checks run for the user's explicit commit and push request, after implementation verification. Existing integration/review reports retain their original dates and source hashes.

- `cargo fmt --check` and `cargo clippy --locked --all-targets -- -D warnings`: exit0.
- `cargo test --locked`:50 passed across unit/process/CLI tests;4 ignored tests require dedicated `TEST_REDIS_URL`. No failures.
- `node tests/map-navigation.mjs`, `node tests/map-grid.mjs`, `node tests/wind-estimate.mjs`, `node tests/playback.mjs`: exit0.
- `node .omo/evidence/real-weather/ui-check.mjs`: same-station weather, zero/null/stale values and weather-only fallback PASS.
- `node tests/preview-ws-origins.mjs http://localhost:8080`: both WebSocket routes accept all four advertised preview origins and reject foreign origins.
- `node tests/map-first-browser.mjs 1280 900` and `node tests/map-first-browser.mjs 375 812`:26 scenarios PASS at08:36UTC, no application errors in normal use. Desktop additionally checks deliberate WebGL loss and initial scene-load failure. Updated reports: `1280-qa.json`, `375-qa.json`;768px report is from earlier implementation verification.
- JavaScript syntax checks for app/grid/navigation/browser harness and `git diff --check`: exit0.
- `python3 .omo/evidence/planning-spec-check.py`:16/16 document/source consistency checks PASS.

Screenshots and diagnostic logs are local generated evidence and are not included in these commits. Browser checks regenerate map screenshots/reports; earlier weather screenshots remain local historical artifacts. Reports do not claim physical facility geometry, irradiance or individual operating telemetry have been acquired.

Commit scope: weather API/local preview origins; proposal/source documentation; approved map interface, navigation, picking and their runnable checks. Concurrent scene-generation changes and other nginx routes remain separate working-tree changes.
