# Bridge code review — 2026-09-29

Verdict after focused fix re-review: **APPROVE** for the reviewed working-tree bridge scope. Both important findings below are resolved. This is not a live-source integration approval or an approval of later changes.

Scope: uncommitted GPU bridge/client infrastructure on `feat/gpu-infra`, HEAD `444deed`; canonical upstream reviewed with `git show 9a94596ee74addf3652341cf23ae9fd293b1b245:bridge/src/main.rs`, `bridge/README.md`, and `bridge/src/assets.sql`. No secrets or `.env` files were read. No product files were edited by the reviewer.

## Resolved important findings

1. **Process shutdown cancels detached WebSocket subscribers before they close.** Reviewed `src/main.rs:85–92` signals `bridge.stop()` and awaits Axum's HTTP server, then only shuts down the upstream `JoinSet`. The subscribers created by `src/bridge/stream.rs` through `on_upgrade` are detached and unjoined. Actual debug binary, 32 clients, SIGTERM: eight runs delivered a Close frame to only 6–10 clients; the other 22–26 received EOF. A ninth run recorded 26 `CLOSED` messages with close code 1006 and `ClientConnectionResetError`. The existing WebSocket test signals `bridge.stop()` while its runtime remains alive and therefore misses process exit. Recommendation: retain the Bridge and boundedly drain subscriber tasks/permits before returning from main; add a subprocess SIGTERM test.

2. **Malformed GeoJSON line/ring shapes pass boundary validation.** Reviewed `src/bridge/protocol.rs:170–189` checks coordinate nesting and nonempty arrays but omits geometry-specific cardinality and ring closure. Actual debug binary against a local aiohttp source served HTTP 200 for `{"type":"LineString","coordinates":[[126.5,33.4]]}`. That is not a valid LineString. The same path accepts single-point or unclosed polygon rings. Recommendation: enforce line/ring constraints in the shared validator and add a small regression check; preserve original valid response bytes.

### Fix re-review

- Shutdown: `src/main.rs` now retains the Bridge after `axum::serve` returns and awaits `Bridge::drain_subscribers`. That method signals stop and waits up to 11 seconds to acquire all 32 subscriber permits, keeping the runtime alive until detached subscriber handlers release their permits. `cargo test --locked --test bridge_shutdown` passed on independent reviewer execution; its real subprocess SIGTERM check requires all 32 clients to receive Close.
- Geometry: the shared validator now checks line length ≥2, ring length ≥4 with matching endpoints, nested geometry structure, and longitude/latitude bounds. Valid response bytes are still forwarded unchanged. `cargo test --locked bridge::tests::malformed_protocol_is_rejected_at_boundary` passed on independent reviewer execution, covering the single-point line, unclosed ring, and out-of-bounds latitude.
- No remaining important finding was established in the focused fixes. Full final checks and Docker rebuild are owned by root and are not represented here as reviewer-executed post-fix checks.

## Executed validation

- `cargo test --locked`: 28 unit tests passed, 3 ignored; 3 simulation CLI tests passed.
- `TEST_REDIS_URL=redis://127.0.0.1:6380/0 cargo test --locked -- --ignored`: all 3 dedicated Redis tests passed.
- `cargo clippy --locked --all-targets -- -D warnings`: passed.
- Real process shutdown checks and HTTP malformed-geometry reproduction above used local synthetic sources only. No reverse-tunnel/source integration claim is made.

## Runnable shutdown reproduction

Run from the repository root with the current debug binary built. Requires the already-installed Python aiohttp package. No source files are modified.

```python
import asyncio, os, socket, aiohttp
from collections import Counter

async def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    proc = await asyncio.create_subprocess_exec(
        './target/debug/jeju-twin',
        env=dict(os.environ, BIND_ADDR=f'127.0.0.1:{port}',
                 BRIDGE_BASE_URL='', REDIS_URL='redis://127.0.0.1:1/0'),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        async with aiohttp.ClientSession() as session:
            for _ in range(50):
                try:
                    async with session.get(f'http://127.0.0.1:{port}/health/live') as response:
                        if response.status == 200:
                            break
                except aiohttp.ClientError:
                    await asyncio.sleep(.02)
            sockets = [await session.ws_connect(
                f'http://127.0.0.1:{port}/api/v1/jeju/ws') for _ in range(32)]
            await asyncio.gather(*(ws.receive() for ws in sockets))
            proc.terminate()
            messages = await asyncio.gather(*(ws.receive(timeout=2) for ws in sockets))
            await proc.wait()
            print(dict(Counter(message.type.name for message in messages)))
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()

asyncio.run(main())
```

One observed output: `{'CLOSED': 26, 'CLOSE': 6}`. Repeat to expose scheduling differences.

## Additional assessment

The reviewed observation cache isolates process sessions and epochs, invalidates on received upstream transitions, bypasses while status/disconnected, validates hits, and bounds Redis operations. Version reset after upstream restart is intentionally accepted. HTTP errors redact source bodies and response sizes are bounded. No further important findings were established within the reviewed scope.
