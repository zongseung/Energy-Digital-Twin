# Local buildings and sea asset evidence

Date: 2026-09-30. Builder: `renderers/twin/build_local.py`. Outputs: `var/rendering/local/{scene.glb,manifest.json,verification.json,CREDITS.txt}`.

Correction after user feedback: the blue-teal sea material was rejected and removed. This report supersedes the earlier opaque-sea asset evidence. The original imagery appearance is restored. Buildings remain only as optional **단순 높이 모형** comparison geometry (`default_visible: false`), not textured 3D reconstruction; the parent task owns the viewer's hidden default.

## Implemented and sourced

- Reused `var/rendering/site/scene.json` prepared Sinchang footprints, bbox `[126.155,33.325,126.19,33.36]`, in the exact existing EPSG:32652 metre frame and EPSG:3855 / EGM2008 vertical datum.
- Rendered 623 of 2,661 source buildings. All 623 have finite positive VWorld `LT_C_BLDGINFO` provider heights; 2,038 unknown/zero heights are excluded, without height inferred from floors. Height range is 2.4–13 m. Raw provider source: `.worktrees/data/var/data/geography/source-03e02ef/building_info.geojsonl`, SHA256 `1dd37571e665cc303111d0b58da551d265ba1de97253b8a5ec773d90988fc1a8`.
- Each `building_LT_C_BLDGINFO_<id>` root contains neutral opaque PBR `_walls` and `_roof` geometry. Roofs are flat display geometry. Shapely 2.1.2, already used by the source preparation script, provides constrained roof triangulation. Exact footprint rings, concavity and any interior courtyards are preserved; no guessed facade details, roof pitch, or textures were added.
- Approximate ground is the 20th percentile of native land DSM sample centres within 60 m of each footprint representative point. Samples inside any of the 2,661 prepared footprints and all non-land WBM classes are excluded. Each building has 6–16 remaining samples; base elevations span 1.168–31.329 m EGM2008. Native crop hashes are recorded and checked.
- `ocean_surface` retains all source vertices and faces and again uses the original `georeferenced_imagery` material: the same VWorld Satellite JPEG as land, metalness 0, roughness 1, and the original EPSG:3857 UV projection. `sea_metadata` only describes the surface and does not mutate it. Its WBM extent is unchanged, with no added sea plane. WBM SHA256: `d72601fce09824caba10d3daa1e08127131f28a1f17581048440f43fba9eb38c`.
- Manifest adds `buildings` (counts, source hashes, per-building records, height and ground policies, limits), `sea`, `cameras.buildings` and `cameras.sea`. Buildings camera selects the densest 100 m cluster of rendered source footprints; sea camera looks across the wind coast.

## Verification

`uv run renderers/twin/build_local.py --self-test` passes after the correction and reports `sea_original_imagery_restored: true`. The written verification reports 15 unchanged facility roots, 10 unchanged rotor transforms, 623 optional building models, 2,038 excluded heights, seven displayed routes, 90 coast paths and 1,307 meshes. The regenerated GLB is 20,337,336 bytes; SHA256 `5c243005a6dd64b2bafe9097aa7cb3d0d655f65881dec68770b6210fa70992d6`.

The runnable check reopens the exported GLB and verifies:

- source files and derived imagery hashes; one embedded JPEG, correctly georeferenced land UVs;
- unchanged source DSM vertices, faces, metre heights, min/max, facility and rotor transforms;
- finite geometry and finite unit face/vertex normals; every exported primitive has explicit normal attributes, including the sea;
- all 623 source IDs, complete raw source properties and full source geometries against the original provider GeoJSONL, plus exact positive extrusion heights;
- exported roof area, containment in source polygons, upward roof normals, horizontal wall normals;
- a concave synthetic footprint with a courtyard, including uncovered courtyard and outward outer/inner wall normals;
- byte-equivalent sea face indices, numerically unchanged vertices, and water-source hash;
- restored sea material name and parameters, all 26,648 sea UV coordinates against the original EPSG:3857 mapping, and exact decoded pixel equality to the source imagery through the original JPEG export step;
- the sea and every land terrain mesh share identical texture pixels. The source JPEG is 6,400 × 3,840, SHA256 `6a45bc2bc801a5f9f72cb28260a700f4658655112f355a7d6c16004b9b510377`. The existing exporter re-encodes JPEG; the check compares against that same original export, not a claim that compressed input bytes are preserved.

A separate diagnostic reused `build_grid.displayed_elevation` against the original DSM and grid sampling spec: all 623 roof elevations exceed the unchanged displayed DSM at their representative points. Clearance at those points is 0.181–11.596 m (median 3.315 m); this is not a guarantee about every point on a sloping footprint. No terrain was flattened or exaggerated.

## Explicit limits

- Ground remains an approximation derived from DSM, not surveyed ground or a DTM. Coarse pixels can retain roofs/vegetation even when their centres are outside footprints; this policy reduces direct roof-on-roof placement but cannot eliminate double counting. Underlying terrain can intersect lower walls.
- Provider heights have not been field verified. Neutral colours and flat roofs are display choices, not photogrammetric reconstruction. Source overlaps remain, and only the bounded Sinchang subset is represented.
- The source sea mesh is not wholly flat: 26,038 of 26,648 vertices are zero; 610 shared coastal vertices retain adjacent DSM elevations, spanning -1.448 to 52.313 m overall. They were preserved to maintain source geometry. Zero is a nominal offshore EGM2008 level, not observed tide; no waves, tides or bathymetry are claimed.
- Browser/GPU appearance and interaction verification belongs to the parent integration task; this evidence covers generated assets and geometry checks.
