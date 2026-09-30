# Twin viewer code review

Verdict: PASS (re-review after fixes)
codeQualityStatus: CLEAR
recommendation: APPROVE
blockers: []

## Scope

Reviewed complete current contents of renderers/twin/app.js, index.html, style.css, DESIGN.md; renderers/mock/nginx.conf; compose.yaml. Consulted inherited renderers/site/style.css and existing Rust HTTP/WS protocol to verify contracts. These additions are untracked, so no historical full diff is available. No product files were changed. No notepad supplied. ulw-loop status reports ULW_LOOP_PLAN_MISSING; report uses requested fallback location.

## CRITICAL
None.

## HIGH
None.

## MEDIUM

No open findings. The following original findings are resolved:

1. **Stale socket close callback creates duplicate live connections.** renderers/twin/app.js:175–183. Hide closes socket A asynchronously. If the user returns while A is CLOSING, visibilitychange opens B. A's later onclose callback sees a visible document and schedules connectState, which opens C without closing B. Repeating this creates additional subscriptions, resource use, and competing status callbacks. Minimal fix: bind handlers to their socket instance and ignore callbacks/timers belonging to an obsolete connection; keep connectState idempotent for an existing OPEN/CONNECTING socket. Check rapid hide/show before the previous close callback fires.

   Independently replayed the exact connectState source in Node vm with controlled WebSocket close ordering and timers: output `{"created":3,"open":2,"expectedOpen":1}`. This is a deterministic lifecycle reproduction, not a claim of real-browser execution. Original source was read from disk and executed unchanged within that seam.

2. **Context-loss shutdown can be restarted by ordinary controls.** renderers/twin/app.js:90–92, 129–136, 183. The context-loss handler cancels only the current animation frame. It leaves rotor-demo checked/enabled, does not disable OrbitControls/camera buttons, and records no unavailable state. A subsequent hide/show immediately schedules animate again; toggling the checkbox also restarts it. The loop continues rotating/rendering while the UI says the 3D connection is lost and requires reload. Minimal fix: mark 3D unavailable, disable camera/demo controls and prevent render/animate restart until the intended recovery. Keep facility DOM selection and observation reading usable. Verify context loss with demo enabled, then hide/show and toggle attempts.

## LOW
None requiring action in this bounded review.

## Positive checks and limits

- `node --check renderers/twin/app.js`: PASS.
- `docker compose --env-file .env.example --profile preview config --quiet`: PASS.
- Read actual GET /api/v1/jeju/state: field names, null/finite handling and observed_at match showState's expectations. Existing Rust WS snapshot/status envelope matches the client branch names.
- Source strings enter DOM via textContent; no HTML interpolation found. GLB/manifest paths are fixed same-origin paths. Nginx uses exact twin asset routes, read-only mounts, non-root execution and loopback binding; no new arbitrary filesystem route was introduced.
- Native facility/camera buttons and demo checkbox provide keyboard alternatives to canvas interaction; focus/44px control styling is inherited from site/style.css. Layout places scene first on narrow screens. No full accessibility or contrast audit claimed.
- Actual data vs estimated turbine shape and optional synthetic RPM are explicitly distinguished. No per-turbine dispatch inference is introduced.
- Renderer is event-driven unless the user requests the demo; hidden tabs stop it in the normal lifecycle.
- Assets were still being generated at review time. Missing manifest/scene assets were explicitly excluded as findings. Full GLB compatibility, visual geometry, browser loading, actual GPU behavior and runtime accessibility must be verified by the parent/QA once assets exist.
- JS/Python-specific lint, typecheck and security scanners are not configured for this scope; no such automated gate is claimed. No new tests were present for this viewer yet.

## Skill-perspective check

Consulted the already-loaded omo:programming and omo:remove-ai-slops skills from this review session. Applied their read-only test/overfit and production-complexity perspectives. No deletion-only, natural-language prompt, tautological or implementation-constant tests were introduced. No needless extraction, parsing pipeline or speculative abstraction warrants removal. Existing boundary JSON parsing and failure UI serve real inputs. The lifecycle findings are correctness/resource issues, not a request for a framework or broad refactor. No separate slop violation found.

## Fix verification

Re-read the updated app.js. Socket callbacks now capture their connection, ignore obsolete callbacks, and do not create another connection while one is CONNECTING/OPEN. Context loss now marks the renderer unavailable, unchecks/disables demo and native camera controls, disables OrbitControls, stops RAF, blocks render/inspect/animate, and exits after pending GLB load before any success state can overwrite the error. Both original findings are resolved; no new blocker found.

Durable regression replay: `node var/verification/twin/code-review-replay.mjs`; output: `var/verification/twin/code-review-replay.log`. Independent run passed 20 quick hide/show lifecycle sequences, CONNECTING/OPEN reuse, stale message suppression, current-close reconnect, lost-context render/rotor/RAF suppression, and context loss while awaiting GLB load. The replay executes current production function bodies with controlled event ordering; it is reviewer evidence rather than a replacement for real-browser QA. No product test framework or abstraction was added.

`node --check renderers/twin/app.js` passed again. Assets now exist: independently parsed scene.glb JSON chunk and manifest.json; all 10 facility and rotor names exist, and expected camera/spec metadata is present. Geometry builder internals remain outside scope; full real-browser and GPU QA remain with their assigned reviewers.
