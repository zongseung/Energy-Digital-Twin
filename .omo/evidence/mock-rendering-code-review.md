# Mock rendering code review

Verdict: PASS with minor maintainability notes.
codeQualityStatus: WATCH
recommendation: APPROVE
blockers: []

## Scope and evidence

Read the complete new browser renderer (prepare.py, app.js, model.js, model.test.js, index.html, style.css, nginx.conf, package manifests), preview Compose service, DESIGN.md and docs/mock-rendering.md. These are untracked additions, so their full contents, rather than a tracked-file diff, were reviewed. Inspected var/verification/mock/browser-qa.mjs, browser-qa.json, browser-qa.log, nginx-proxy.log, 1280-selected.png and var/rendering/omniverse/verified-v2/evidence.json. Full screenshot review and GPU execution belong to the separate reviewers.

No ulw-loop plan exists: status command returned ULW_LOOP_PLAN_MISSING, hence this fallback report path. No notepad was supplied or found in .omo. No product files were modified during review.

## Findings by severity

### CRITICAL
None.

### HIGH
None.

### MEDIUM
None.

### LOW
- renderers/mock/prepare.py:11: coordinate projection and nested preparation helpers lack type annotations. This departs from the programming skill's typed-function perspective, but no concrete correctness failure was observed. Adding annotations during the next substantive edit would make axis/unit contracts easier to follow. The existing stdlib CLI and externally documented pinned dependencies are sufficient for this small tool; no framework migration is requested.
- renderers/mock/model.test.js:5: one test groups profile invariants, time formatting, and invalid slots, reducing failure reporting granularity. Assertions still exercise meaningful behavior; this is not a useless or deletion-only test. Splitting the three behaviors is optional.

## Independently checked

- `npm test --prefix renderers/mock`: PASS, 1 test, zero failures.
- `python3 renderers/mock/prepare.py --self-test`: PASS projection, axes, mock elevation scaling, invalid coordinates.
- `docker compose --env-file .env.example exec preview nginx -t`: PASS.
- Actual HTTP /scene.json bytes equal local scene bytes; SHA-256 equals Omniverse evidence input hash 6fef331dcbd13bb37c678ccf95223520f7fc165e795adcd89caf9afdca9d2485.
- Actual PNG SHA-256 equals evidence hash 2794d89961777c47e3d4766dfd0dcf3a6c240887e4e44aac7db2f1515f202e67.
- All 2,694 IDs are unique and match the three facility source datasets; every stored longitude/latitude equals its source feature coordinates.
- Actual HTTP requests to /.env, /prepare.py, /package.json, /nginx.conf return 404; /../.env returns 400. Initial review script expected 404 for every rejection and failed on the valid 400 rejection; corrected the assertion to accept either rejection and reran successfully.
- WebGL uses actual BufferGeometry terrain, line segments, instanced facility geometry and ray picking; no screenshot is substituted for the live view. CSS custom properties feed both DOM styling and mesh colors.
- Selected facility IDs drive list state, details and highlight; search/layer filtering clears hidden selection and hides markers. Strings are inserted with textContent. Native controls support keyboard interaction.
- Synthetic time profiles are prominently and consistently labeled; facility dispatch is explicitly unavailable. No synthetic values are written to Rust/Redis.
- Demand-driven drawing, bounded DPR, instancing, fixed terrain sampling and initial 60-row paging limit the current dataset's cost. No animation polling or frame-by-frame data reconstruction.
- WebGL failure leaves DOM facilities/time available; context loss disables view controls with retry UI. QA script exercises that fallback. Startup network failure is handled in code; not independently browser-injected in this review.

## Skill-perspective check

Loaded omo:remove-ai-slops, omo:programming and its Python reference before judging test relevance/maintainability. Ran the read-only slop/overfit perspective over production code and tests. No deletion-only tests, requested-removal tests, prose/prompt pins, implementation-constant-only tests, untyped escape casts, speculative abstractions or unnecessary parsing/normalization were found. Projection, source hashing and JSON preparation directly serve the shared scene requirement. The small meaningful numeric checks do not duplicate a complex formula as expected output. The two LOW notes above are the limited programming-perspective departures. No slop violation warrants revision. No lint/typecheck/security tool is configured specifically for this new plain-JS/Python renderer; those automated gates were not claimed as passed.

## Limits

Rust sources and existing Compose infrastructure were already untracked, and no pre-task hashes exist. Neither Git nor this review can prove byte-for-byte preservation. The parent reports no Rust edits/builds during this task and an unchanged running API image; this is explicitly reported context, not independently proven preservation. This review approves the scoped renderer addition, not an immutable historical Rust diff. Browser QA results were inspected, not rerun here; full visual QA is separate. No measured FPS or Lighthouse claim is made.
