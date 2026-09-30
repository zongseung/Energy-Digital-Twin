# Weather implementation evidence — 2026-09-30

Owner: /root/weather_implementation. Repository: /home/user/Energy-Digital-Twin.
Changed product files: src/weather.rs, renderers/twin/app.js, renderers/twin/index.html. Fixture unchanged. No dependency, polling interval, route, geometry, irradiance, PV modeling, or deployment changes.

## Contract

All three existing routes (`/api/v1/jeju/wind`, `/api/v1/jeju/wind/stations`, `/api/v1/jeju/wind/ws`) serialize additive nullable `temperature_c`, `relative_humidity_percent`, `precipitation_1h_mm`, and `weather_status` (fresh/stale/unavailable). Rain is accumulated over the preceding 60 minutes, not instantaneous intensity. The original `status` continues to mean wind validity/freshness. Weather-only observations have null wind speed and unavailable wind status; they never supply rotor direction or estimated power/RPM.

Each row uses one source station and observation/receipt timestamp. Missing/bad new fields do not discard valid wind or other new fields. A new valid row replaces its previous fields rather than copying missing measurements into a new timestamp. Whole missing/invalid observations retain prior values as stale through the existing cache. Temperature -90..60°C is an application plausibility guard, not documented KMA QC; it preserves real subzero values and rejects common negative sentinels. Humidity is 0..100%, rain >=0, all finite and parsed from source strings. The source trouble flag/timestamp gates remain in force. No unsupported numeric sentinel code is claimed official.

UI IDs: `weather-values` (temperature/humidity/previous60min rain), `weather-status` (role=status); station and `wind-time` are shared. Selection preserves nearest fresh wind first, then nearest fresh weather when no fresh wind exists, otherwise nearest row. The weather fallback is explicitly labeled. Disconnect/source failure/age expiry stays visibly delayed; null values render as —, zero rain as 0 mm. Existing note typography/spacing and textContent rendering are reused without CSS/design changes.

## Scenarios, exact invocations, binary observables and artifacts

| Scenario | Invocation | Binary observable | Captured artifact |
| --- | --- | --- | --- |
| New parser/serialization regression is red before implementation | `cargo test weather_fields_are_independent_and_share_observation_freshness -- --nocapture` | exit101; expected -2.5 but received null | `.omo/evidence/real-weather/parser-red.log` |
| Subzero temperature, 0/100 humidity, 0/positive rain, null/missing/malformed/nonfinite/sentinel independence; weather-only rows; missing timestamp; receipt-age/fetch-failure stale serialization | `cargo test weather:: -- --nocapture` | exit0; 8 weather tests pass | `.omo/evidence/real-weather/weather-tests.log` |
| Existing HTTP routes expose weather and same timestamps from one cache with no extra upstream calls | same `cargo test weather:: -- --nocapture` | `both_routes_read_one_batch_cache_without_upstream_requests ... ok` | `.omo/evidence/real-weather/weather-tests.log` |
| Two real websocket clients receive shared weather updates and stale last values after upstream failure; reconnect/shutdown preserved | same `cargo test weather:: -- --nocapture` | `websocket_fans_out_latest_cache_and_closes_on_shutdown ... ok` | `.omo/evidence/real-weather/weather-tests.log` |
| New actual showWind render regression red before UI implementation | `node .omo/evidence/real-weather/ui-check.mjs` | exit1; expected -2.5°C text absent | `.omo/evidence/real-weather/ui-red.log` |
| Actual showWind produces measured values/zero rain, null labels, delayed disconnect/age, weather-only fallback and zero rotor estimates | `node .omo/evidence/real-weather/ui-check.mjs` | exit0; PASS line | `.omo/evidence/real-weather/ui-green.log`; runnable check `.omo/evidence/real-weather/ui-check.mjs` |
| Rust formatting | `cargo fmt --all -- --check` | exit0 | `.omo/evidence/real-weather/format.log` |
| All-target lint | `cargo clippy --all-targets -- -D warnings` | exit0 | `.omo/evidence/real-weather/clippy.log` |
| JavaScript module syntax | `node --check --input-type=module < renderers/twin/app.js` | exit0 | `.omo/evidence/real-weather/js-syntax.log` |
| Owned diff whitespace check | `git diff --check -- src/weather.rs renderers/twin/app.js renderers/twin/index.html` | exit0 | `.omo/evidence/real-weather/diff-check.log` |

The Node scenario evaluates the real showWind/closestWind/stationDistance functions with an in-memory DOM and the real estimateWind function; it does not claim browser layout verification. Root owns actual runtime/HTTP/browser/mobile QA and deployment. Full repository test suite is also root-owned; weather-filtered integration test binaries with zero matching tests are not counted as verified scenarios here.

Local self-review recorded in `.omo/evidence/real-weather/scope-review.txt`. No shared work reverted. No active currentAttemptDir was returned by `omo-agent-toolkit ulw-loop status --json`, so evidence is stored here.
