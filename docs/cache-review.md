# CPU Rust cache review — 2026-09-29

Reviewed `src/bridge/{cache,http,stream,protocol}.rs`, `src/bridge.rs`,
`src/health.rs`, simulation HTTP/core validation, tests, bridge-client docs,
and implementation-plan contracts. Production source was not changed by this
review. References and counts describe the baseline before the cache
optimization; later edits may move line numbers. Synthetic local fixtures do
not establish live database/tunnel freshness.

## Measured findings

| Scenario | Upstream payload reads | Health HTTP requests | New Redis connections |
|---|---:|---:|---:|
| One cold GIS request | 1 | 1 | 2 |
| Ten subsequent GIS hits | 0 | 10 | 10 |
| Sixteen concurrent cold GIS requests | 16 | 16 | 32 |

These counts came from the current handlers in an isolated source copy at
`/tmp/edt-cache-review-sol`, synthetic Axum endpoints, and an independently
created Redis 7.4.2 container (`edt-cache-review-sol`, random loopback port
32768). Each synthetic GIS fetch slept 200 ms to expose overlapping misses.
Health/payload handlers used atomic counters; a persistent diagnostic Redis
connection measured `INFO stats`'s `total_connections_received` delta. Both
assertion-based fixture tests passed in 0.49 s. The disposable container was
stopped and automatically removed. No existing Redis/container/DB was changed.

The reproduction source is `/tmp/edt-cache-review-sol/src/bridge/tests/cache.rs`,
SHA-256 `fcf017f4b69a729070b870a55ee78b46a628b9fbaaabea46019c0318c8eb6984`.
It contains `review_count_reads_connections_and_cold_stampede` and
`review_epoch_change_during_http_returns_inflight_old_value`. Recreate a
disposable Redis on loopback port 32768 (or adjust the fixture URL), then run:

```bash
CARGO_TARGET_DIR=/tmp/edt-cache-review-baseline-target \
  cargo test --locked --manifest-path /tmp/edt-cache-review-sol/Cargo.toml \
  review_ -- --nocapture --test-threads=1
```

Scratch files are local review artifacts; preserve the fixture before cleaning
`/tmp` if an enduring baseline reproduction is needed.

1. **Repeated Redis connection setup is confirmed.** `src/bridge/cache.rs:54`
   and `:72` open a new multiplexed connection for every GET and SET;
   `src/health.rs:67` does the same for readiness PING. Holding a `redis::Client`
   in `Bridge` does not pool connections. A miss establishes two connections,
   while every hit still establishes one. Smallest general fix: reuse a shared,
   reconnecting multiplexed connection through the existing redis crate's
   `ConnectionManager` feature. Keep lazy initialization and bounded failures:
   Redis must not become a startup requirement, and failures must still fall
   back to source. Share through Probe/Bridge if doing so does not create a
   second independent manager. Check: warm-hit connection deltas stop growing;
   cold miss uses the same connection for GET/SET; disposable Redis restart
   recovers without restarting the app; offline Redis still returns source data.

2. **Concurrent cold GIS misses duplicate source reads.** The check/fetch/write
   sequence in `src/bridge/http.rs:139`–`:148` has no coordination. State and
   timeline have the same pattern at `:63`–`:72` and `:109`–`:118`, but the
   measured reproduction covers GIS only. Smallest narrow fix: coordinate GIS
   misses within this app, then recheck the cache after acquiring ownership.
   Keep unrelated state/timeline/simulation requests independent. A plain mutex
   works for successful writes, but `Entry::get` currently conflates cache miss,
   Redis failure, and WS ineligibility into `None` (`src/bridge/cache.rs:47`,
   `:63`). During a Redis outage, mutex/recheck alone would serialize repeated
   source fetches without sharing their result. Either limit that lock to a
   confirmed healthy cache miss or share only the in-flight result among
   existing waiters. Do not add a distributed lock or persistent L1 cache.
   Check: sixteen cold callers get identical raw bytes with one source read;
   cancelling the leader releases ownership or wakes waiters; source failure
   reaches every waiter; offline Redis does not serialize sixteen 15-second
   source deadlines; a slow GIS miss does not block an observation request.

3. **Health HTTP remains on the hit path by contract.**
   `src/bridge/cache.rs:50` checks the source's ready/hub/demand fields before
   Redis, using the 3-second bounded probe in `src/health.rs:34`. The ten-hit
   fixture demonstrates ten health calls despite zero payload reads. This is
   deliberate fail-closed behavior documented in `docs/bridge-client.md`.
   Do not remove it or cache a ready result simply to improve hit latency.
   Any miss coordination must revalidate health after waiting before serving
   cached bytes. Check: warm GIS and observation entries return 503 immediately
   when the synthetic health source becomes unavailable; invalid health JSON
   never becomes ready. These checks already exist for ordinary requests.

4. **A cache hit still parses and validates the payload.**
   `src/bridge/http.rs:66`, `:112`, and `:142` parse snapshot, timeline, and GIS
   cache bytes respectively. GIS validation at `src/bridge/protocol.rs:131`
   constructs a full `serde_json::Value`, traverses geometry, and checks IDs;
   the original bytes are returned unchanged. This can cost CPU near the 16 MiB
   limit, but no representative GIS CPU measurement was available. Retain
   validation of Redis bytes as a trust boundary. Consider a narrower validator
   only after profiling proves it matters; a Rust implementation alone does
   not remove I/O or parsing. Check: malformed cache entries cause validated
   source fallback; raw GIS bytes, IDs/properties/full geometry, null and zero
   remain unchanged.

## Epochs, races, and cancellation

- Dynamic entries require the captured epoch to remain current and `live.kind`
  to be `Snapshot` (`src/bridge/cache.rs:39`). GET rechecks after its await
  (`:62`), so a correction/disconnect during GET discards the cached result.
  GIS is intentionally independent of WS epochs and has a 3600-second TTL.
- Every validated WS text increments the epoch before publishing the envelope
  (`src/bridge/stream.rs:63`); disconnect increments again (`:98`). Ping/pong
  does not increment. Therefore an unchanged valid snapshot also invalidates
  dynamic entries. Reducing this to `state_version` alone is unsafe: versions
  reset on restart and status/quality changes matter. Preserve conservative
  invalidation unless measured repeated identical snapshots justify an exact
  semantic comparison. Current tests verify corrections and restart versions,
  but do not verify cache epochs for repeated text versus ping/pong.
- PUT checks eligibility before awaiting Redis, not afterward
  (`src/bridge/cache.rs:68`). An epoch can change during SET; the stale key can
  still be written but is isolated under the old epoch and expires. This is
  wasted storage, not demonstrated stale reuse. Keep the captured epoch/key
  throughout a fetch: constructing a new entry after fetching would label old
  bytes with the new generation and introduce a real invalidation bug.
- The HTTP miss path returns an already fetched result without checking the
  epoch again (`src/bridge/http.rs:73`, `:119`). A gated fixture changed the
  epoch and published disconnected status while the source response was
  pending; the request still returned its captured demand of 88. This is an
  in-flight response observation, not evidence of a contract violation:
  these APIs do not promise linearization against WS updates. Future cache
  reads remain isolated. Do not introduce retries or a global lock for this
  alone. A strict freshness promise would require a stronger source contract.
- Existing GET/SET operations use 1-second outer deadlines. Dropping a Redis
  request future does not guarantee the command already sent to Redis was
  cancelled. A shared connection remains appropriate for nonblocking GET/SET;
  do not hold an app mutex around each command. The installed redis 0.32.7
  source documents cloning and cancellation semantics; the current
  [redis-rs API documentation](https://docs.rs/redis/latest/redis/aio/struct.MultiplexedConnection.html)
  states the same behavior. Reconnection must remain bounded.

## Simulation HTTP source/concurrency review

`src/bridge/http.rs:18` deliberately performs uncached exact-time reads.
`src/simulation/http.rs:141` limits each run to two simultaneous reads;
the shared semaphore at `:125` allows two runs (four source requests maximum).
The profile collection has a 30-second deadline at `:143`; each HTTP operation
also retains the 15-second client timeout. A day needs up to 288 exact-time
requests, so latency may produce a 503 rather than a complete result. This is
bounded behavior, not invented history, and the stated deadline is a source
acquisition deadline rather than a whole-response deadline.

404 observations are retained as missing intervals; null D/W/S remains
incomplete; incorrect timestamps/schema and invalid numeric values are
rejected. The sorted observation hash and `individual_uncached_http_reads`
provenance honestly describe non-atomic acquisition. Quality flags are
preserved but are not used to reject profiles, an explicit limitation in
`docs/simulation.md`; a later source-usability policy should be defined before
changing that behavior. No source-version consistency across separate HTTP
reads is claimed.

The permit moves into `spawn_blocking` (`src/simulation/http.rs:160`), so
cancelling the caller cannot free a slot while CPU calculation continues.
This correctly preserves the concurrency bound. Once started, blocking work
cannot be aborted, as documented by
[Tokio](https://docs.rs/tokio/latest/tokio/task/fn.spawn_blocking.html).
Bounded 288-point work does not justify a custom cancellation executor.

## Verification and recommended order

Ran six focused existing bridge tests and all four simulation HTTP tests;
all passed. The scratch-copy fixtures additionally verified the counted hit /
miss behavior and the in-flight epoch observation. Existing ignored Redis
tests cover TTL, correction invalidation, UTC normalization, and health
failure, but do not assert connection reuse, concurrent misses, cancelled
leaders, or epoch changes while a Redis operation is pending.

Implement shared reconnecting Redis I/O first, then the measured GIS miss
coordination with cancellation/outage checks. Preserve 3600/30/300 TTLs,
health gating, raw GIS payloads, null/zero, corrections, and uncached simulation
provenance. Add no history-version protocol, distributed lock, persistent L1,
or parser rewrite without evidence requiring it.

## After-change review

The updated implementation shares a lazily initialized Redis
`ConnectionManager` through `Probe` (`src/health.rs:38`) and shares only a
currently running GIS future (`src/bridge/http.rs:143`). Each GIS caller still
checks health before joining (`:144`), and finished futures are not reused
(`:152`). State/timeline TTLs, epochs and uncached simulation acquisition are
unchanged.

| Scenario | Upstream payload reads | Health HTTP requests | New Redis connections |
|---|---:|---:|---:|
| One cold GIS request | 1 | 1 | 1 |
| Ten subsequent GIS hits | 0 | 10 | 0 |
| Sixteen concurrent cold GIS requests, manager already connected | 1 | 16 | 0 |

The same counted fixture with current source also verifies:

- Sixteen concurrent source-failure requests share one source read and all
  return 503. A new request immediately after source recovery performs a new
  read and returns 200; no persistent negative cache is introduced.
- Cancelling the original direct-handler task leaves a remaining waiter able
  to drive the shared future to completion. Cancelling every owner expires the
  stored weak reference, and a later caller starts a new fetch successfully.
- A state request completes while a GIS source response is deliberately held;
  the short mutex protects only flight selection, not source or Redis I/O.
- A late caller after the source health flips unavailable returns 503, while
  the earlier in-flight source request can complete with 200. This preserves
  baseline gating. The initial shared-flight draft omitted the late caller's
  check; that review finding was corrected before this final validation.

Weak storage avoids a Bridge/future ownership cycle. `Bytes` clones preserve
the original GIS payload while sharing its storage. Dropping all Shared owners
drops the app's pending future; as with ordinary HTTP, that cannot promise
remote database work already started is cancelled. There is no detached
background task or new cache lifetime.

Reproduction source:
`/tmp/edt-cache-review-sol-after/src/bridge/tests/cache.rs`, SHA-256
`7cc1e70901c10b1561681c8df7ff08c3610f992c88ec4414cfe6bb6552ae6c69`.
Use a completely separate target directory:

```bash
CARGO_TARGET_DIR=/tmp/edt-cache-review-target \
  cargo test --locked --manifest-path /tmp/edt-cache-review-sol-after/Cargo.toml \
  review_ -- --nocapture --test-threads=1
```

Earlier scratch builds mistakenly shared the repository target directory and
caused a subsequent broad repository test command to reuse the scratch test
binary. That contaminated broad run is not evidence for the repository tests;
the root agent is rebuilding it. Independent fixture runs use their own target
directory. The four simulation HTTP scenarios were also rerun: exact-time
acquisition/provenance, missing/null/failure handling, request validation/body
limits, and semaphore saturation/release all passed. Inspection found no
correctness blocker in the combined cache/simulation delta. Actual source,
renderer, and tunnel integration remains outside this synthetic review.

Final independent verification: six review fixtures passed in 1.00 s using
`/tmp/edt-cache-review-target`; all four simulation HTTP tests passed in 0.01 s
using the same separate target. Fixture output is saved at
`/tmp/edt-cache-review-sol-after/review-after.log`. The review-owned Redis
container was stopped and automatically removed again. Verdict: no remaining
correctness blocker identified for these deltas; retain permanent cancellation,
health-flip, coalescing, and Redis-recovery regression checks in the root change.

Root validation after rebuilding the uncontaminated repository: `var/verification/cache-final-tests.log` — 39 unit + 1 process-shutdown + 3 CLI tests passed, Clippy clean. Durable copies of reviewer evidence: `var/verification/cache-sol-review.log` and `var/verification/cache-sol-reproduction.rs`.
