# Navigation implementation evidence

Scope: `renderers/twin/navigation.mjs`, `tests/map-navigation.mjs`. No app, HTML, CSS, grid, dependency, or commit changes by this worker.

Invocation (repository root): `node tests/map-navigation.mjs`

The test imports installed Three.js 0.180.0 and the actual OrbitControls class. Only DOM listener delivery, canvas geometry, and pointer capture are stubbed; camera projection, wheel anchoring, damping, touch interpretation, and orbit calculations execute production code. Browser integration and screenshots remain the root agent's responsibility.

| Scenario / criterion | Binary observable | Captured artifact |
|---|---|---|
| Test before implementation | Assertion fails: navigation module must exist | `.omo/evidence/map-first/navigation-red.log` |
| Left drag pan, Shift drag rotate, damping idle | Target moves on left drag, remains fixed on Shift drag while heading changes, update reaches false within 500 frames | `.omo/evidence/map-first/navigation-green.log` |
| Browser Ctrl/Meta-wheel, normal wheel | Modified wheel default not prevented and camera unchanged; plain wheel reduces camera distance | `.omo/evidence/map-first/navigation-green.log` |
| 350ms focus and input cancellation | Intermediate pose differs from endpoint, completed pose/FOV matches requested view; pointerdown cancels subsequent automatic movement | `.omo/evidence/map-first/navigation-green.log` |
| Hidden tab and reduced motion | Hidden update returns false; transition does not resume; live reduced preference disables damping and makes focus immediate | `.omo/evidence/map-first/navigation-green.log` |
| Bounds, terrain, obstacles, distance, heading tools | Target remains within 15% scene margin; camera clears ground/obstacle by 6 units; distance respects limits; north/rotate produce requested headings | `.omo/evidence/map-first/navigation-green.log` |
| Listener cleanup | After navigation and controls disposal, canvas/document/media listener counts equal zero | `.omo/evidence/map-first/navigation-green.log` |
| Touch input | One finger changes target; two-finger spreading reduces distance | `.omo/evidence/map-first/navigation-green.log` |
| Cursor anchoring and horizon wheel | Ground anchor stays within 0.02 NDC of cursor; repeated alternating horizon wheel keeps finite pose and ground clearance | `.omo/evidence/map-first/navigation-green.log` |

Integration contract: caller owns RAF and OrbitControls disposal. Call `update(deltaSeconds)` before rendering; continue camera frames only when it returns true. Existing controls change events wake the caller's loop. `moveTo` accepts existing array-valued position/target views and explicit scale; ordinary selection does not call it. `reducedMotion` accepts a boolean or live MediaQueryList. `cancel()` clears the focus transition and controls inertia without resetting the visible pose. Module does not call requestAnimationFrame.

Limit: collision clearance uses the supplied pointwise `groundHeight(x,z)` top surface; browser validation must verify its accuracy against actual terrain/facility meshes.
