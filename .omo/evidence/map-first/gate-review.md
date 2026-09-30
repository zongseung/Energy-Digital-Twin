# Map-first final review

**Verdict: APPROVE. Confidence: HIGH for the requested local map interaction.** Main session review, not an independent code-review claim. The Codex-specific review-work instruction uses the main session's checklist unless strict review is explicitly requested. The user requested implementation and parallel work; camera, picking and the later browser diagnosis were delegated, while the main session owned real-surface QA and this final review.

Coverage is the base SHA and dirty-tree SHA256 table in [integration evidence](integration.md). Final surface evidence is the08:19UTC report set; previous stale or failed launches are not counted as PASS.

| User goal / approved constraint | Assessment / evidence |
|---|---|
| Maximize map area | ACHIEVED: full width,816/900px, initial panel closed and no auto-selection; three viewport reports |
| Click for information and operating state | ACHIEVED: native map marker/candidate and actual mesh click; detail shows honest unknown output/state/time;1280 mesh-click and all detail captures |
| Dynamic wheel and free movement | ACHIEVED: cursor zoom, left pan, right/Shift orbit, damping; real input captures plus actual OrbitControls checks |
| One focused geographic scene | ACHIEVED: existing GLTF/manifest/cameras retained; no asset rebuilding in redesign scope |
| One nonmodal panel, accessible controls | ACHIEVED: tools/detail share one panel; native search/buttons, focus restoration, Escape,44px minimum; mobile40/70% sheet and independent scroll |
| Measured weather / missing truth | ACHIEVED: visible temperature/humidity/rain fields, same station/time; unavailable solar and individual telemetry labelled; VM null/stale checks and real screenshots |
| Preserve existing analysis/demo | ACHIEVED: regional history remains separate from current weather; estimate/playback checks pass; rotation remains explicitly a demonstration |
| Picking/bounds/failure/idle | ACHIEVED: hidden ancestors/layers ignored, unique candidates, ground/obstacle queries and bounded navigation; real context/asset failures retain text/weather; idle draw counters unchanged |

Checked representative flows: initial map → marker/candidate → detail → tool/back/close; facility search → keyboard selection → Escape; explicit close-up → mesh click → pan/orbit; weather scroll → regional history; context loss and initial GLB failure. Edge coverage includes null/zero/stale weather, empty search, hidden layers, cancelled/multi-touch clicks, horizon wheel, reduced motion and interrupted focus.

No blocking issues found. New names/rows use textContent; SVG paths are local constants. No new dependency or data API. Single shared weather DOM avoids diverging copies; one RAF serves camera damping and optional rotor demonstration, with hidden/context-loss/idle stop. Added navigation module is served by an explicit nginx JavaScript route. Other contributors' edits were preserved.

Limits: individual operational telemetry and irradiance remain unconnected as explicitly planned. Existing terrain/imagery coverage seams and estimated facility geometry remain source limitations. QA does not claim physical phone testing, a Lighthouse score or fresh backend regression results. Browser password-store A/B proves the QA launch fix; the underlying OS keyring wait was not traced further because it is outside the requested UI change.
