# Weather WebSocket backend

- Route: `GET /api/v1/jeju/wind/ws` upgrades to WebSocket. Each outbound text frame is the raw JSON shape of `GET /api/v1/jeju/wind/stations`; the initial frame reflects the current cache, and every completed source poll publishes the latest cache, including stale data and `source_fetch_failed` after a failed poll.
- The sole 60-second weather collector still owns upstream reads. HTTP and WebSocket clients only read the same RAM cache; the watch channel carries a latest-update signal and stores no history. Upgrades use the bridge's exact `Origin` allowlist behavior, 64 permits, 16 KiB inbound limits, 5-second bounded sends, 30-second pings, and a shutdown signal with bounded subscriber drain.
- Live socket test `weather::tests::websocket_fans_out_latest_cache_and_closes_on_shutdown` passed: two subscribers got the same initial state and changed poll, retained all 43 stations with stale status after source failure, reconnect got latest state, the source counter remained at three poll calls, allowed and forbidden origins were handled, excess capacity was rejected, a disconnected subscriber released capacity, and shutdown sent a Close frame and drained subscribers.
- `cargo fmt --all -- --check`: passed.
- `cargo test --all-targets`: passed (42 unit tests, 4 integration tests; 4 pre-existing Redis tests ignored without `TEST_REDIS_URL`).
- `cargo clippy --all-targets -- -D warnings`: passed.
