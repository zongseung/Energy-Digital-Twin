#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Combine the existing wind and grid GLBs on the grid's partial terrain.

uv run renderers/twin/build_local.py --self-test
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory

import numpy as np
import shapely
import trimesh
from PIL import Image
from rasterio.warp import transform as project_crs
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from build import normals, sha, translation
from photo_assets import load_placements, place_asset

ROOT = Path(__file__).resolve().parents[2]


def inside(point: list[float] | np.ndarray, box: list[list[float]]) -> bool:
    return box[0][0] - 1e-6 <= point[0] <= box[1][0] + 1e-6 and box[0][2] - 1e-6 <= point[2] <= box[1][2] + 1e-6


def clip_segment(a: np.ndarray, b: np.ndarray, box: list[list[float]]) -> tuple[np.ndarray, np.ndarray] | None:
    """Liang–Barsky clip in world X/Z; interpolate elevation at each boundary."""
    low, high = 0.0, 1.0
    delta = b - a
    for axis in (0, 2):
        for p, q in ((-delta[axis], a[axis] - box[0][axis]), (delta[axis], box[1][axis] - a[axis])):
            if abs(p) < 1e-12:
                if q < 0:
                    return None
            elif p < 0:
                low = max(low, q / p)
            else:
                high = min(high, q / p)
            if low > high:
                return None
    if high - low < 1e-10:  # one boundary touch is not a visible path
        return None
    return a + low * delta, a + high * delta


def clip_path(points: list[list[float]], box: list[list[float]]) -> list[list[list[float]]]:
    """Return separate visible pieces; never bridge a trip outside the terrain."""
    pieces: list[list[list[float]]] = []
    current: list[list[float]] = []
    for start, end in zip(points, points[1:]):
        pair = clip_segment(np.asarray(start, float), np.asarray(end, float), box)
        if pair is None:
            if len(current) > 1:
                pieces.append(current)
            current = []
            continue
        a, b = (point.tolist() for point in pair)
        if current and not np.allclose(current[-1], a, atol=1e-6):
            pieces.append(current)
            current = []
        if not current:
            current = [a]
        current.append(b)
        if not inside(end, box):
            pieces.append(current)
            current = []
    if len(current) > 1:
        pieces.append(current)
    return pieces


def copy_nodes(source: trimesh.Scene, dest: trimesh.Scene, roots: set[str], terrain: bool = False) -> None:
    """Copy source nodes and geometry references without flattening or changing transforms."""
    for parent, child, data in source.graph.to_edgelist():
        if child == source.graph.base_frame:
            continue
        if not (child in roots or any(child.startswith(root + "_") for root in roots) or
                (terrain and (child == "ocean_surface" or child.startswith("terrain_landcover_")))):
            continue
        geometry = data.get("geometry")
        if geometry and geometry not in dest.geometry:
            dest.geometry[geometry] = source.geometry[geometry]
        dest.graph.update(frame_to=child, frame_from=parent, matrix=np.asarray(data["matrix"]),
                          **({"geometry": geometry} if geometry else {}))


def apply_imagery(scene: trimesh.Scene, image_path: Path, metadata: dict, frame: dict) -> None:
    """Texture the existing DSM vertices with their actual map positions."""
    assert metadata["crs"] == "EPSG:3857"
    west, south, east, north = map(float, metadata["bounds"])
    assert west < east and south < north
    with Image.open(image_path) as source:
        image = source.copy()
        image.format = source.format  # trimesh preserves JPEG only when the format is retained.
    material = PBRMaterial(name="georeferenced_imagery", baseColorTexture=image,
                           metallicFactor=0, roughnessFactor=1)
    for name in list(scene.geometry):
        if name != "ocean_surface" and not name.startswith("terrain_landcover_"):
            continue
        mesh = scene.geometry[name].copy()
        vertices = mesh.vertices
        origin = frame["origin_easting_northing"]
        mx, my = project_crs(frame["horizontal_crs"], "EPSG:3857",
                             vertices[:, 0] + origin[0], origin[1] - vertices[:, 2])
        uv = np.column_stack(((np.asarray(mx) - west) / (east - west),
                              (np.asarray(my) - south) / (north - south)))
        assert np.isfinite(uv).all() and ((uv >= -0.02) & (uv <= 1.02)).all(), name
        mesh.visual = TextureVisuals(uv=uv, material=material)
        normals(mesh)
        scene.geometry[name] = mesh


def source_path(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def footprint_polygons(record: dict) -> list[Polygon]:
    return [Polygon(np.asarray(rings[0])[:, [0, 2]],
                    [np.asarray(ring)[:, [0, 2]] for ring in rings[1:]]) for rings in record["polygons"]]


def building_meshes(polygons: list[Polygon], height: float, center: np.ndarray) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    """Extrude exact rings and constrained roof triangles, including courtyard holes."""
    assert np.isfinite(height) and height > 0
    walls, roofs = [], []
    for polygon in polygons:
        assert polygon.is_valid and polygon.area > 0
        polygon = orient(polygon, sign=1)
        for ring in [polygon.exterior, *polygon.interiors]:
            points = np.asarray(ring.coords) - center
            for a, b in zip(points[:-1], points[1:]):
                if np.array_equal(a, b):
                    continue
                walls.append([[a[0], 0, a[1]], [b[0], 0, b[1]],
                              [b[0], height, b[1]], [a[0], height, a[1]]])
        triangles = shapely.constrained_delaunay_triangles(polygon)
        assert np.isclose(sum(t.area for t in triangles.geoms), polygon.area, atol=1e-6)
        for triangle in triangles.geoms:
            points = np.asarray(triangle.exterior.coords)[:3] - center
            vertices = np.column_stack((points[:, 0], np.full(3, height), points[:, 1]))
            if np.cross(vertices[1] - vertices[0], vertices[2] - vertices[0])[1] < 0:
                vertices = vertices[::-1]
            roofs.append(vertices)
    wall_faces = (np.arange(len(walls))[:, None, None] * 4 + np.array([[0, 3, 2], [0, 2, 1]])).reshape(-1, 3)
    wall_mesh = normals(trimesh.Trimesh(vertices=np.asarray(walls).reshape(-1, 3), faces=wall_faces, process=False))
    roof_mesh = normals(trimesh.Trimesh(vertices=np.asarray(roofs).reshape(-1, 3),
                                      faces=np.arange(len(roofs) * 3).reshape(-1, 3), process=False))
    return wall_mesh, roof_mesh


def add_buildings(scene: trimesh.Scene, frame: dict) -> tuple[dict, dict]:
    path = ROOT / "var/rendering/site/scene.json"
    site = json.loads(path.read_text())
    assert all(site["projection"][key] == frame[key] for key in
               ("horizontal_crs", "vertical_crs", "origin_easting_northing", "axes", "scale"))
    provider = next(s for s in site["metadata"]["sources"] if s.get("file") == "building_info.geojsonl")
    provider_path = ROOT / ".worktrees/data/var/data/geography/source-03e02ef" / provider["file"]
    assert sha(provider_path) == provider["sha256"]
    sources = [{**provider, "path": source_path(provider_path)},
               {"path": source_path(path), "sha256": sha(path), "kind": "prepared_clipped_footprints"}]
    for name in ("sinchang_dem.tif", "sinchang_wbm.tif"):
        entry = next(s for s in site["metadata"]["sources"] if Path(s.get("path", "")).name == name)
        source = ROOT / ".worktrees/data" / entry["path"]
        assert sha(source) == entry["sha256"]
        sources.append({**entry, "path": source_path(source)})
    polygons = {b["id"]: footprint_polygons(b) for b in site["buildings"]}
    assert all(p.is_valid and p.area > 0 for parts in polygons.values() for p in parts)
    terrain = np.asarray(site["terrain"]["positions"]).reshape(-1, 3)
    covered = shapely.intersects_xy(shapely.union_all([p for parts in polygons.values() for p in parts]),
                                    terrain[:, 0], terrain[:, 2])
    samples = terrain[(np.asarray(site["terrain"]["water_classes"]) == 0) & ~covered]
    assert np.isfinite(samples).all()
    wall_material = PBRMaterial(name="building_neutral_walls", baseColorFactor=[192, 190, 179, 255],
                                metallicFactor=0, roughnessFactor=.9, alphaMode="OPAQUE")
    roof_material = PBRMaterial(name="building_neutral_flat_roofs", baseColorFactor=[133, 139, 136, 255],
                                metallicFactor=0, roughnessFactor=.9, alphaMode="OPAQUE")
    records = []
    for building in site["buildings"]:
        height = building["height_m"]
        if height is None:
            assert not building["extrusion_allowed"]
            continue
        assert np.isfinite(height) and height > 0 and height == float(building["source_properties"]["height"])
        parts = polygons[building["id"]]
        center = np.asarray(shapely.union_all(parts).representative_point().coords[0])
        local = samples[np.sum((samples[:, [0, 2]] - center) ** 2, axis=1) <= 60 ** 2]
        assert len(local) >= 3, f"insufficient local ground samples: {building['id']}"
        # ponytail: local low DSM percentile is not a DTM; replace with surveyed ground when available.
        ground = float(np.percentile(local[:, 1], 20))
        node = "building_" + building["id"].replace(".", "_").replace(":", "_")
        position = [float(center[0]), ground, float(center[1])]
        scene.graph.update(frame_to=node, frame_from="world", matrix=translation(*position))
        for suffix, mesh, material in zip(("walls", "roof"), building_meshes(parts, height, center),
                                           (wall_material, roof_material)):
            mesh.visual = TextureVisuals(material=material)
            scene.add_geometry(mesh, geom_name=f"{node}_{suffix}", node_name=f"{node}_{suffix}", parent_node_name=node)
        records.append({"id": building["id"], "node": node, "position": position, "height_m": height,
                        "ground_m": ground, "ground_sample_count": len(local),
                        "source_dsm_anchor_m": building["base_height_m"],
                        "footprint_area_m2": sum(p.area for p in parts), "polygon_count": len(parts),
                        "hole_count": sum(len(p.interiors) for p in parts),
                        "height_status": "provider_positive_unverified"})
    assert records
    centers = np.asarray([r["position"] for r in records])
    nearby = np.sum((centers[:, None, [0, 2]] - centers[None, :, [0, 2]]) ** 2, axis=-1) <= 100 ** 2
    focus = centers[np.argmax(nearby.sum(axis=1))]
    target = (focus + [0, 4, 0]).tolist()
    camera = {"position": (focus + [-100, 95, 130]).tolist(), "target": target, "fov": 48, "near": .15, "far": 70000}
    return {"count": len(records), "source_count": len(site["buildings"]),
            "display_label": "단순 높이 모형", "default_visible": False,
            "excluded_unknown_height_count": len(site["buildings"]) - len(records),
            "bbox_lon_lat": site["aoi_bbox"], "sources": sources, "records": records,
            "height_policy": "Only finite positive VWorld LT_C_BLDGINFO provider heights in metres; zero/missing heights excluded. No floor-count inference.",
            "ground_policy": "20th percentile of native Copernicus DSM land sample centres within 60 m of each footprint representative point, excluding centres covered by any of the 2661 source footprints; constant base per building.",
            "materials": "Neutral opaque PBR walls and flat roofs; footprint rings and courtyard holes retained.",
            "limits": ["Provider heights are not field-survey verified; flat roofs and neutral colours are display choices, not measured roof forms or facades.",
                       "DSM includes roofs and vegetation. Local low-percentile ground is approximate, not a DTM, and cannot remove all roof contamination.",
                       "Underlying DSM is unchanged; some walls may intersect terrain and visible height can be less than the provider extrusion height.",
                       "Bounded Sinchang coverage only; unknown heights and source overlaps remain unresolved."]}, camera


def sea_metadata(scene: trimesh.Scene, grid: dict) -> dict:
    mesh = scene.geometry["ocean_surface"]
    return {"node": "ocean_surface", "material": "georeferenced_imagery", "level_m": 0,
            "appearance": "Restored original VWorld Satellite JPEG and EPSG:3857 UV mapping, shared with land terrain.",
            "vertical_datum": "EGM2008", "source": next(s for s in grid["sources"] if s["kind"] == "water_mask"),
            "extent_policy": "Unchanged source WBM-classified ocean mesh; no additional plane or coverage over land.",
            "nonzero_coastal_vertex_count": int(np.count_nonzero(mesh.vertices[:, 1])),
            "height_range_m": mesh.bounds[:, 1].tolist(),
            "limits": ["Nominal offshore level is 0 m EGM2008; shared coastal cell vertices retain adjacent source DSM heights.",
                       "Imagery is a static surface image, not measured current tides, waves or bathymetry."]}


def local_assumptions(grid: dict, wind: dict) -> tuple[dict, dict]:
    grid_active = {**grid["assetassumptions"]}
    grid_active["limits"] = [
        ("VWorld satellite imagery textures the partial DSM and unchanged WBM ocean mesh; image acquisition time is not photography date. Nominal offshore level is zero EGM2008 and coastal edges retain source DSM heights."
         if limit.startswith("Landcover classes drive synthetic surface colours") else
         "Overview route widths and vertical offsets are display styling, not measured conductor diameter, underground depth or surveyed altitude. Routes are clipped to the partial terrain; full source coordinates remain in provenance."
         if limit.startswith("Overview route widths and vertical offsets") else limit)
        for limit in grid_active["limits"]
    ]
    wind_active = {**wind["assetassumptions"]}
    wind_active["limits"] = [
        ("The original wind ground and sea colours are absent here; the shared grid DSM uses georeferenced VWorld satellite imagery. Turbine materials remain estimated PBR visualization materials."
         if limit.startswith("Land/shore/ocean colours are synthetic") else limit)
        for limit in wind_active["limits"]
    ]
    return grid_active, wind_active


def terrain_focus(scene: trimesh.Scene, frame: dict) -> tuple[list[float], list[float]]:
    """Select the highest source DSM vertex within the visible western hill patch."""
    points = np.vstack([mesh.vertices for name, mesh in scene.geometry.items()
                        if name == "ocean_surface" or name.startswith("terrain_landcover_")])
    origin = frame["origin_easting_northing"]
    lon, lat = project_crs(frame["horizontal_crs"], "EPSG:4326",
                           points[:, 0] + origin[0], origin[1] - points[:, 2])
    region = ((np.asarray(lon) >= 126.35) & (np.asarray(lon) <= 126.365) &
              (np.asarray(lat) >= 33.36) & (np.asarray(lat) <= 33.372))
    assert region.any()
    selected = np.flatnonzero(region)[np.argmax(points[region, 1])]
    return points[selected].tolist(), [float(lon[selected]), float(lat[selected])]


def build(output: Path, imagery_path: Path, imagery_metadata_path: Path,
          placements_path: Path | None = None) -> dict:
    wind_dir, grid_dir = ROOT / "var/rendering/twin", ROOT / "var/rendering/grid"
    wind = json.loads((wind_dir / "manifest.json").read_text())
    grid = json.loads((grid_dir / "manifest.json").read_text())
    assert wind["coordinateFrame"] == grid["coordinateFrame"] and wind["units"] == grid["units"] == "m"
    box = grid["terrain"]["extent"]
    wind_scene = trimesh.load(wind_dir / "scene.glb", force="scene")
    grid_scene = trimesh.load(grid_dir / "scene.glb", force="scene")
    facilities = [{**f, "kind": "wind"} for f in wind["facilities"]]
    facilities += [f for f in grid["facilities"] if inside(f["position"], box)]
    if placements_path is None:
        placements_path = ROOT / "var/rendering/photo-assets/manifest.json"
    placements = load_placements(placements_path, facilities, grid["coordinateFrame"], root=ROOT)
    assert len([f for f in facilities if f["kind"] == "wind"]) == 10
    assert len([f for f in facilities if f["kind"] == "pv"]) == 3
    assert len([f for f in facilities if f["kind"] == "substation"]) == 1
    assert len([f for f in facilities if f["kind"] == "line"]) == 1
    scene = trimesh.Scene(base_frame="world")
    copy_nodes(grid_scene, scene, {f["node"] for f in facilities if f["kind"] != "wind" and f["id"] not in placements}, terrain=True)
    copy_nodes(wind_scene, scene, {f["node"] for f in facilities if f["kind"] == "wind"})
    for facility in facilities:
        placement = placements.get(facility["id"])
        if placement is None:
            continue
        place_asset(scene, facility, placement, root=ROOT)
        generated = ROOT / placement["generated_dir"]
        inference = json.loads((generated / "metadata.json").read_text())
        facility["photo_asset"] = {
            "status": "photo_derived_estimated_geometry", "source": inference["image"]["source"],
            "license": placement["license"], "photo_sha256": placement["photo_sha256"],
            "asset_sha256": placement["asset_sha256"],
            "facility_match_evidence": placement["facility_match_evidence"],
            "length_evidence": placement["length_evidence"],
            "orientation_evidence": placement["orientation_evidence"],
            "verified_length_m": placement["length_m"],
            "asset_anchor_xyz": placement["asset_anchor_xyz"],
            "length_endpoints_xyz": placement["length_endpoints_xyz"],
        }
    imagery_metadata = json.loads(imagery_metadata_path.read_text())
    apply_imagery(scene, imagery_path, imagery_metadata, grid["coordinateFrame"])
    sea = sea_metadata(scene, grid)
    buildings, building_camera = add_buildings(scene, grid["coordinateFrame"])
    grid_assumptions, wind_assumptions = local_assumptions(grid, wind)
    terrain = {**grid["terrain"], "materials": "Georeferenced VWorld Satellite JPEG mapped to the unchanged DSM and WBM ocean mesh via EPSG:3857 UV coordinates"}
    terrain.pop("palette", None)
    terrain["vertical_exaggeration"] = 1
    peak, peak_lon_lat = terrain_focus(grid_scene, grid["coordinateFrame"])
    terrain["camera_focus_source_dsm"] = {"position": peak, "coordinates": peak_lon_lat,
                                          "selection": "highest grid DSM mesh vertex in lon 126.35–126.365, lat 33.36–33.372"}
    routes = [{**r, "paths": clip_path(r["points"], box)} for r in grid["routes"]]
    coast = [path for part in grid["coast"] for path in clip_path(part, box)]
    output.mkdir(parents=True, exist_ok=True)
    glb = output / "scene.glb"
    scene.export(glb)
    center = np.mean(box, axis=0)
    cameras = {
        "inspect": grid["cameras"]["inspect"],
        "overview": {"position": [float(center[0]-16000), float(center[1]+18000), float(center[2]+18000)],
                     "target": [float(center[0]), 100, float(center[2])], "fov": 48, "near": .15, "far": 70000},
        "array": wind["cameras"]["array"],
        "pv": grid["cameras"]["pv"],
        "wind": wind["cameras"]["inspect"],
        "buildings": building_camera,
        "sea": {"position": [-1100, 230, -600], "target": [0, 25, -1550], "fov": 48, "near": .15, "far": 70000},
        "terrain": {"position": [peak[0]-1200, peak[1]+700, peak[2]+1500],
                    "target": [peak[0], peak[1]-100, peak[2]], "fov": 48, "near": .5, "far": 70000},
    }
    manifest = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "units": "m",
        "coordinateFrame": grid["coordinateFrame"], "facilities": facilities, "routes": routes,
        "coast": coast, "terrain": terrain, "buildings": buildings, "sea": sea, "physical_line": grid["physical_line"],
        "cameras": cameras, "assetassumptions": grid_assumptions,
        "wind_assetassumptions": wind_assumptions,
        "source_assetassumptions": {"grid": grid["assetassumptions"], "wind": wind["assetassumptions"]},
        "sources": {"wind": wind["sources"], "grid": grid["sources"]},
        "source_assets": {kind: {name: sha(directory / name) for name in ("scene.glb", "manifest.json")}
                          for kind, directory in (("wind", wind_dir), ("grid", grid_dir))},
        "imagery": {"path": source_path(imagery_path), "sha256": sha(imagery_path),
                    "metadata_path": source_path(imagery_metadata_path),
                    "metadata_sha256": sha(imagery_metadata_path), **imagery_metadata},
        "audit": {"electrical_topology_inferred": False, "terrain_count": 1,
                  "display_route_count": sum(bool(r["paths"]) for r in routes)},
        "files": [{"path": "scene.glb", "bytes": glb.stat().st_size, "sha256": sha(glb)}],
        "status": "combined_estimated_partial_scene", "surveyed_asset": False,
        "license_note": "Existing wind and grid assets combined without repositioning or inferred connections. See CREDITS.txt.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    credits = ((wind_dir / "CREDITS.txt").read_text() + "\n--- Grid source ---\n" +
               (grid_dir / "CREDITS.txt").read_text() + "\n--- Imagery source ---\n" +
               f"{imagery_metadata['source']} — {imagery_metadata['attribution']}\n" +
               f"{imagery_metadata['documentation']}\n{imagery_metadata['notice']}\n" +
               "Changes: original georeferenced mosaic restored to both existing land DSM and unchanged WBM sea geometry in EPSG:3857 UV coordinates.\n" +
               "\n--- Building source ---\nVWorld LT_C_BLDGINFO — https://api.vworld.kr/req/data\n" +
               "VWorld provider terms; source attribution required. Prepared Sinchang footprints, positive provider heights only.\n" +
               "Optional comparison only, hidden by default in the local viewer: simplified footprint/height extrusions, not textured 3D building reconstruction. Ground is an approximate local lower-percentile Copernicus DSM value, not surveyed ground or DTM.\n")
    for facility in facilities:
        if "photo_asset" in facility:
            photo = facility["photo_asset"]
            credits += (f"\n--- Photo asset {facility['id']} ---\n{photo['source']}\n"
                        f"{photo['license']} · photo-derived estimated geometry; only the stated reference length is validated.\n")
    (output / "CREDITS.txt").write_text(credits)
    return manifest


def verify(output: Path) -> dict:
    manifest = json.loads((output / "manifest.json").read_text())
    for kind, files in manifest["source_assets"].items():
        for name, digest in files.items():
            assert sha(ROOT / "var/rendering" / ("twin" if kind == "wind" else "grid") / name) == digest
    assert sha(output / "scene.glb") == manifest["files"][0]["sha256"]
    assert sha(ROOT / manifest["imagery"]["path"]) == manifest["imagery"]["sha256"]
    assert sha(ROOT / manifest["imagery"]["metadata_path"]) == manifest["imagery"]["metadata_sha256"]
    buildings = manifest["buildings"]
    for source in buildings["sources"] + [manifest["sea"]["source"]]:
        assert sha(ROOT / source["path"]) == source["sha256"]
    assert "VWorld Satellite JPEG" in manifest["terrain"]["materials"] and "palette" not in manifest["terrain"]
    assert manifest["terrain"]["vertical_exaggeration"] == 1
    focus = manifest["terrain"]["camera_focus_source_dsm"]["position"]
    assert np.allclose(manifest["cameras"]["terrain"]["target"], [focus[0], focus[1]-100, focus[2]])
    assert manifest["source_assetassumptions"]["grid"]["limits"] != manifest["assetassumptions"]["limits"]
    assert manifest["source_assetassumptions"]["wind"]["limits"] != manifest["wind_assetassumptions"]["limits"]
    assert all("no aerial" not in item.lower() and "Full mainland HVDC" not in item
               for item in manifest["assetassumptions"]["limits"])
    box = manifest["terrain"]["extent"]
    # Crossing, reentry, and wholly outside paths cannot create false joins.
    sample = [[box[0][0]-10, 2, 0], [box[0][0]+10, 4, 0], [box[0][0]-10, 6, 0],
              [box[0][0]-20, 8, 0], [box[0][0]+10, 10, 0]]
    assert len(clip_path(sample, box)) == 2
    assert not clip_path([[box[0][0]-20, 0, 0], [box[0][0]-10, 0, 0]], box)
    through = clip_path([[box[0][0]-10, 0, 0], [box[1][0]+10, 10, 0]], box)
    assert len(through) == 1 and np.allclose([through[0][0][0], through[0][-1][0]], [box[0][0], box[1][0]])
    for path in [p for r in manifest["routes"] for p in r["paths"]] + manifest["coast"]:
        assert len(path) >= 2 and all(inside(p, box) for p in path)
    assert len(manifest["coast"]) > 0
    reopened = trimesh.load(output / "scene.glb", force="scene")
    assert len([n for n in reopened.graph.nodes if n.startswith("terrain_landcover_") or n == "ocean_surface"]) > 0
    assert not any(n in reopened.graph.nodes for n in ("shore_basalt", "terrain_land"))
    terrain_meshes = [m for name, m in reopened.geometry.items()
                      if name == "ocean_surface" or name.startswith("terrain_landcover_")]
    assert terrain_meshes and all(isinstance(m.visual, TextureVisuals) and len(m.visual.uv) == len(m.vertices)
                                  for m in terrain_meshes)
    assert all(np.isfinite(m.vertices).all() and np.isfinite(m.face_normals).all() and np.isfinite(m.vertex_normals).all() and
               np.allclose(np.linalg.norm(m.face_normals, axis=1), 1, atol=.02) and
               np.allclose(np.linalg.norm(m.vertex_normals, axis=1), 1, atol=.02)
               for m in reopened.geometry.values())
    originals = {kind: trimesh.load(ROOT / "var/rendering" / kind / "scene.glb", force="scene")
                 for kind in ("twin", "grid")}
    expected_peak, expected_lon_lat = terrain_focus(originals["grid"], manifest["coordinateFrame"])
    assert np.allclose(manifest["terrain"]["camera_focus_source_dsm"]["position"], expected_peak, atol=.002)
    assert np.allclose(manifest["terrain"]["camera_focus_source_dsm"]["coordinates"], expected_lon_lat, atol=1e-6)
    for name in (n for n in reopened.geometry if n == "ocean_surface" or n.startswith("terrain_landcover_")):
        source = originals["grid"].geometry[name]
        actual = reopened.geometry[name]
        assert len(source.faces) == len(actual.faces) and len(source.vertices) == len(actual.vertices)
        assert np.array_equal(source.faces, actual.faces)
        assert np.allclose(source.vertices, actual.vertices, atol=.002)
        if name == "ocean_surface":
            material = actual.visual.material
            assert material.name == manifest["sea"]["material"] == "georeferenced_imagery"
            assert material.metallicFactor == 0 and material.roughnessFactor == 1
            assert material.baseColorTexture is not None
            # Match the original imagery export, including its existing JPEG encoding step.
            with Image.open(ROOT / manifest["imagery"]["path"]) as image, BytesIO() as encoded:
                image.save(encoded, format="JPEG")
                encoded.seek(0)
                with Image.open(encoded) as expected_image:
                    assert material.baseColorTexture.size == expected_image.size
                    assert material.baseColorTexture.convert("RGB").tobytes() == expected_image.convert("RGB").tobytes()
            assert all(m.visual.material.name == material.name and
                       m.visual.material.baseColorTexture.convert("RGB").tobytes() == material.baseColorTexture.convert("RGB").tobytes()
                       for m in terrain_meshes)
            assert manifest["sea"]["nonzero_coastal_vertex_count"] == np.count_nonzero(actual.vertices[:, 1])
            assert np.allclose(actual.bounds[:, 1], manifest["sea"]["height_range_m"])
        sample_indices = (np.arange(len(actual.vertices)) if name == "ocean_surface" else
                          np.linspace(0, len(actual.vertices)-1, min(20, len(actual.vertices)), dtype=int))
        vertices = actual.vertices[sample_indices]
        origin = manifest["coordinateFrame"]["origin_easting_northing"]
        mx, my = project_crs("EPSG:32652", "EPSG:3857", vertices[:, 0]+origin[0], origin[1]-vertices[:, 2])
        west, south, east, north = manifest["imagery"]["bounds"]
        expected = np.column_stack(((np.asarray(mx)-west)/(east-west), (np.asarray(my)-south)/(north-south)))
        assert np.allclose(actual.visual.uv[sample_indices], expected, atol=1e-5)
    actual_heights = np.concatenate([m.vertices[:, 1] for name, m in reopened.geometry.items()
                                     if name == "ocean_surface" or name.startswith("terrain_landcover_")])
    source_heights = np.concatenate([m.vertices[:, 1] for name, m in originals["grid"].geometry.items()
                                     if name == "ocean_surface" or name.startswith("terrain_landcover_")])
    assert np.isclose(actual_heights.min(), source_heights.min(), atol=.002)
    assert np.isclose(actual_heights.max(), source_heights.max(), atol=.002)
    assert actual_heights.max() - actual_heights.min() > 700 and len(np.unique(np.round(actual_heights, 1))) > 100
    with (output / "scene.glb").open("rb") as stream:
        stream.read(12)
        length, kind = struct.unpack("<I4s", stream.read(8))
        assert kind == b"JSON"
        gltf = json.loads(stream.read(length))
    assert gltf["images"] and any(image["mimeType"] == "image/jpeg" for image in gltf["images"])
    assert all("NORMAL" in primitive["attributes"] for mesh in gltf["meshes"] for primitive in mesh["primitives"])
    site = json.loads((ROOT / "var/rendering/site/scene.json").read_text())
    prepared = {b["id"]: b for b in site["buildings"]}
    source_records = {}
    with (ROOT / buildings["sources"][0]["path"]).open() as stream:
        for line in stream:
            record = json.loads(line)
            if record["id"] in prepared:
                source_records[record["id"]] = record
    assert len(source_records) == len(prepared) == buildings["source_count"]
    assert {r["id"] for r in buildings["records"]} == {b["id"] for b in prepared.values() if b["height_m"] is not None}
    assert buildings["count"] == len(buildings["records"]) == 623
    assert buildings["excluded_unknown_height_count"] == buildings["source_count"] - buildings["count"] == 2038
    for record in buildings["records"]:
        building = prepared[record["id"]]
        source = source_records[record["id"]]
        assert source["properties"] == building["source_properties"] and source["geometry"] == building["source_geometry"]
        assert record["height_m"] == building["height_m"] == float(source["properties"]["height"]) > 0
        node = record["node"]
        assert np.allclose(reopened.graph[node][0][:3, 3], record["position"], atol=1e-6)
        wall, roof = (reopened.geometry[f"{node}_{suffix}"] for suffix in ("walls", "roof"))
        assert np.allclose(wall.bounds[:, 1], [0, record["height_m"]], atol=1e-5)
        assert np.allclose(roof.vertices[:, 1], record["height_m"], atol=1e-5)
        assert np.all(roof.face_normals[:, 1] > .999) and np.allclose(wall.face_normals[:, 1], 0, atol=1e-6)
        assert np.isclose(roof.area, record["footprint_area_m2"], rtol=1e-5, atol=.002)
        polygons = shapely.union_all(footprint_polygons(building))
        for triangle in roof.triangles:
            world = triangle[:, [0, 2]] + np.asarray(record["position"])[[0, 2]]
            assert polygons.buffer(.001).covers(Polygon(world)), record["id"]
    # A concave footprint with an interior courtyard must retain its hole and outward wall normals.
    example = Polygon([(0, 0), (12, 0), (12, 4), (8, 4), (8, 12), (0, 12)],
                      holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]])
    walls, roof = building_meshes([example], 7.5, np.zeros(2))
    assert np.isclose(roof.area, example.area) and np.allclose(walls.bounds[:, 1], [0, 7.5])
    assert all(example.covers(Polygon(t[:, [0, 2]])) for t in roof.triangles)
    outside = walls.triangles_center[:, [0, 2]] + walls.face_normals[:, [0, 2]] * .01
    assert not shapely.intersects_xy(example, outside[:, 0], outside[:, 1]).any()
    assert np.all(roof.face_normals[:, 1] > .999)
    for f in manifest["facilities"]:
        original = originals["twin" if f["kind"] == "wind" else "grid"]
        assert f["node"] in reopened.graph.nodes
        actual = reopened.graph[f["node"]][0]
        if "photo_asset" in f:
            photo = f["photo_asset"]
            anchor = np.asarray(photo["asset_anchor_xyz"])
            a, b = (np.asarray(p) for p in photo["length_endpoints_xyz"])
            assert np.allclose(actual[:3, :3] @ anchor + actual[:3, 3], f["position"], atol=.002)
            assert np.isclose(np.linalg.norm(actual[:3, :3] @ (b - a)), photo["verified_length_m"], atol=.002)
            assert any(node.startswith(f["node"] + "_photo_") for node in reopened.graph.nodes_geometry)
        else:
            assert np.allclose(actual, original.graph[f["node"]][0], atol=.002)
            assert np.allclose(actual[:3, 3], f["position"], atol=.002)
        if f["kind"] == "wind":
            assert f["rotor_node"] in reopened.graph.nodes
            assert np.allclose(reopened.graph[f["rotor_node"]][0], original.graph[f["rotor_node"]][0], atol=.002)
    assert len([f for f in manifest["facilities"] if f["kind"] == "wind"]) == 10
    assert sorted(f["kind"] for f in manifest["facilities"] if f["kind"] != "wind") == ["line", "pv", "pv", "pv", "substation"]
    source_routes = json.loads((ROOT / "var/rendering/grid/manifest.json").read_text())["routes"]
    assert all(a["id"] == b["id"] and a["source_ids"] == b["source_ids"] and
               a["coordinates"] == b["coordinates"] and a["points"] == b["points"]
               for a, b in zip(manifest["routes"], source_routes))
    return {"status": "PASS", "facilities": len(manifest["facilities"]), "rotors": 10,
            "buildings": buildings["count"], "excluded_unknown_building_heights": buildings["excluded_unknown_height_count"],
            "sea_source_geometry_unchanged": True, "sea_original_imagery_restored": True,
            "display_routes": sum(bool(r["paths"]) for r in manifest["routes"]), "coast_paths": len(manifest["coast"]),
            "reopened_meshes": len(reopened.geometry), "checks": ["source hashes", "reopened GLB", "frame and units",
            "facility and rotor transforms", "unaltered DSM vertices and projected UV", "embedded satellite JPEG",
            "source DSM min/max and 100+ distinct heights without exaggeration", "finite vertices and unit normals",
            "source route IDs/coordinates", "path/coast clipping and reentry",
            "623 building IDs/properties/geometries and heights audited against raw provider source",
            "building roof area, concavity, courtyard hole and outward normals",
            "unchanged WBM sea vertices/faces, restored source imagery pixels and all sea UVs, water-source hash"]}


def build_verified(output: Path, imagery_path: Path, imagery_metadata_path: Path,
                   placements_path: Path | None = None) -> tuple[dict, dict]:
    """Validate a scratch build before replacing any served files."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".local-scene-", dir=output.parent) as directory:
        staged = Path(directory)
        manifest = build(staged, imagery_path, imagery_metadata_path, placements_path)
        result = verify(staged)
        (staged / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
        output.mkdir(parents=True, exist_ok=True)
        # ponytail: per-file replacement preserves the bind mount; use versioned URLs if readers need one atomic snapshot.
        for name in ("scene.glb", "CREDITS.txt", "verification.json", "manifest.json"):
            (staged / name).replace(output / name)
    return manifest, result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "var/rendering/local")
    parser.add_argument("--imagery", type=Path, default=ROOT / "var/rendering/imagery/texture.jpg")
    parser.add_argument("--imagery-metadata", type=Path, default=ROOT / "var/rendering/imagery/manifest.json")
    parser.add_argument("--placements", type=Path, default=ROOT / "var/rendering/photo-assets/manifest.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    manifest, result = build_verified(args.output, args.imagery, args.imagery_metadata, args.placements)
    print(json.dumps({"file": manifest["files"][0], "verification": result}, indent=2))


if __name__ == "__main__":
    main()
