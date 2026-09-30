# Sea/building correction review

Verdict: PASS — no blocker found for the requested correction.

Scope: restore the earlier sea imagery, stop displaying the rejected building extrusions by default, and describe the available building data accurately. No source, service, git, or GPU changes were made by this reviewer.

- `build_local.py:99–122` applies the original georeferenced JPEG and projected UVs to both the existing land meshes and `ocean_surface`. Independently invoked `verify(Path("var/rendering/local"))` without rebuilding: PASS, including source hashes, identical source sea faces/vertices, all sea UVs, decoded original-imagery pixels, and the embedded image/material checks.
- `grid.js:94–105` classifies the actual building meshes, sets all their visibility flags to false, and retains the explicit layer toggle. `app.js:145` does not enable buildings on village-camera selection. The HTML checkbox is unchecked and describes the simplified models as optional comparison geometry. The output manifest also has `default_visible: false`.
- Inspected `correction-qa.mjs` and its 1280/375px PASS results: 1,246 building meshes (two per building), zero initially visible, textured `georeferenced_imagery` ocean, village-button and opt-in checks, and no horizontal overflow. Inspected desktop sea/village and mobile coast screenshots; the building blocks are absent and the sea imagery is visible. This review did not rerun browser interactions.
- `node --check` passed for current app/grid modules. Independently hashed current GLB, manifest, app, grid, and index bytes; all match `correction-http.json`'s served-byte evidence.
- `collected-3d-audit.json` supports the bounded statement that no source 3D building meshes were found in the checked collected-data directory. The empty remote source-file list is consistent with the executor's successful remote extension search, not independent proof of wider supplier availability. Documentation correctly excludes locally generated GLBs as evidence of collected source 3D, and leaves external coverage unconfirmed.
- UI/docs identify the GPU images as the earlier `local-02` scene. No new GPU rendering or source photogrammetry is claimed or required to establish this browser correction.

Limit: this is a scoped correction review, not a whole-repository audit or certification of real building geometry, imagery acquisition date, or external 3D provider coverage.
