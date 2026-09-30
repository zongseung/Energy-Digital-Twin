# Grid navigation and selection evidence

Scope: `renderers/twin/grid.js`, `tests/map-grid.mjs`. No asset, material, groundcover generation, imagery, or existing route-highlight geometry was replaced. No dependencies installed. `ulw-loop status --json` reported `ULW_LOOP_PLAN_MISSING`; this evidence therefore lives under `.omo/evidence/`.

## Contract

- `grid.bounds`: world-space `THREE.Box3` of the loaded static scene.
- `grid.groundHeight(x,z)`: finite top terrain/sea/local obstacle elevation; uncovered/nonfinite input returns 0. Static terrain retains collision when its display layer is hidden. Obstacle visibility includes ancestors. Existing 30m terrain triangle indexing is reused once; static mesh bounds are cached in64m cells and only local intersecting meshes receive downward rays. Rotating rotor geometry receives expanded cached bounds.
- `grid.pick(camera,ndc,{firstOnly=false}={})`: unique facility records in increasing hit-distance order, including buildings. `firstOnly` returns an array of zero or one record. Hidden ancestor/child/material and disabled facility layers are filtered; camera layer mask and `raycaster.camera` are supplied for native mesh/Line2 behavior.

## Executed scenarios

Invocation: `node tests/map-grid.mjs`. The runnable test uses real installed Three.js meshes, raycasting, Box3 and Line2. Only HTTP/GLB loading is replaced with a controlled tiny scene. Tests catch missing bounds, wrong top elevation, spatial raycast leakage, hidden selection, duplicate/wrong-order selection and omitted Line2 camera handling.

Expected binary observables: highest stacked building roof12m; exposed terrain1m; uncovered/nonfinite coordinate0m; distant raycast count0; ordered IDs building:1,pv,wind; hidden building removes its roof and hit; disabled PV/hidden wind remove their hits; Line2 route remains selectable.

Before implementation, the same invocation exited1:

```text
AssertionError [ERR_ASSERTION]: grid exposes scene bounds
    at file:///home/user/Energy-Digital-Twin/tests/map-grid.mjs:33:10
actual: false
expected: true
```

After implementation, full relevant invocation:

```sh
node --check renderers/twin/grid.js && node tests/map-grid.mjs && git diff --check -- renderers/twin/grid.js tests/map-grid.mjs
```

Actual output, exit0:

```text
PASS map grid: bounds, roof/terrain/fallback heights, spatial culling, hidden ancestors/children/layers, ordered unique picking, firstOnly, Line2
```

No TypeScript language server is installed; the environment says installation was previously declined. Syntax/runtime checks above passed. Full real-browser scene performance, camera collision integration, and UI selection verification belong to the root agent's browser QA and are not claimed by this focused check.
