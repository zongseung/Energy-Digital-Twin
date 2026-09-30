#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3"]
# ///
# noqa: SIZE_OK -- one asset builder reusing twin primitives, with its runnable verification.
"""Build source transmission routes, estimated equipment and a partial Jeju DSM scene.

uv run renderers/twin/build_grid.py
uv run renderers/twin/build_grid.py --self-test
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import bounds as geometry_bounds, rasterize
from rasterio.windows import Window, from_bounds
from rasterio.warp import transform as project_crs
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from build import bar, block, glb_tree, normals, rotation, sha, translation

BASE = Path(".worktrees/data")
GIS = BASE / "var/data/geography/source-03e02ef"
DEM = BASE / "var/data/terrain/Copernicus_DSM_COG_10_N33_00_E126_00_DEM.tif"
WATER = DEM.with_name(DEM.name.replace("_DEM", "_WBM"))


def read_features(name: str) -> list[dict]:
    with (GIS / f"{name}.geojsonl").open() as stream:
        return [json.loads(line) for line in stream]


def checked_sources() -> tuple[list[dict], dict]:
    geo = json.loads((GIS / "manifest.json").read_text())
    terrain = json.loads((BASE / "data/terrain/manifest.json").read_text())
    sources = []
    for name in ("power_line", "substation", "pv_facility", "landcover", "coastline"):
        path = GIS / f"{name}.geojsonl"
        entry = next(d for d in geo["datasets"] if d["file"] == path.name)
        assert sha(path) == entry["sha256"], f"source hash mismatch: {path}"
        sources.append({"kind": name, "path": str(path), "sha256": entry["sha256"],
                        "provider": entry["source"], "license": entry["license"]})
    for path in (DEM, WATER):
        entry = next(d for d in terrain["artifacts"] if Path(d["path"]).name == path.name)
        assert sha(path) == entry["sha256"], f"source hash mismatch: {path}"
        sources.append({"kind": "DSM" if path == DEM else "water_mask", "path": str(path),
                        "sha256": entry["sha256"], "source_url": entry["source_url"],
                        "license": terrain["license"]["name"]})
    return sources, terrain


def group_routes(features: list[dict]) -> tuple[list[list[dict]], list[str]]:
    groups, excluded = {}, []
    for f in features:
        p = f["properties"]
        if p["power_type"] == "minor_line":
            excluded.append(f["id"])
            continue
        assert p["power_type"] in ("line", "cable") and f["geometry"]["type"] == "LineString"
        coordinates = tuple(map(tuple, f["geometry"]["coordinates"]))
        assert len(coordinates) >= 2 and np.isfinite(coordinates).all()
        key = p["power_type"], p["voltage"], min(coordinates, coordinates[::-1])
        groups.setdefault(key, []).append(f)
    return list(groups.values()), excluded


def project(coordinates: list | np.ndarray, frame: dict, heights: float | np.ndarray = 0) -> np.ndarray:
    coordinates = np.asarray(coordinates)
    east, north = project_crs("EPSG:4326", frame["horizontal_crs"], coordinates[:, 0], coordinates[:, 1])
    origin = frame["origin_easting_northing"]
    return np.column_stack((np.asarray(east) - origin[0], np.broadcast_to(heights, len(east)), origin[1] - np.asarray(north)))


def inverse(points: np.ndarray, frame: dict) -> np.ndarray:
    origin = frame["origin_easting_northing"]
    lon, lat = project_crs(frame["horizontal_crs"], "EPSG:4326", points[:, 0] + origin[0], origin[1] - points[:, 2])
    return np.column_stack((lon, lat))


def elevation(coordinates: np.ndarray, heights: np.ndarray, affine: rasterio.Affine) -> np.ndarray:
    """Bilinear native DSM samples; out-of-tile mainland display routes use explicit zero."""
    col, row = (~affine) * (coordinates[:, 0], coordinates[:, 1])
    col, row = col - .5, row - .5
    inside = (col >= 0) & (row >= 0) & (col <= heights.shape[1] - 1) & (row <= heights.shape[0] - 1)
    c, r = np.clip(col, 0, heights.shape[1] - 1), np.clip(row, 0, heights.shape[0] - 1)
    c0, r0 = c.astype(int), r.astype(int)
    c1, r1 = np.minimum(c0 + 1, heights.shape[1] - 1), np.minimum(r0 + 1, heights.shape[0] - 1)
    dc, dr = c - c0, r - r0
    sampled = (heights[r0, c0] * (1-dc) + heights[r0, c1] * dc) * (1-dr) + (heights[r1, c0] * (1-dc) + heights[r1, c1] * dc) * dr
    return np.where(inside, sampled, 0)


def displayed_elevation(coordinates: np.ndarray, heights: np.ndarray, affine: rasterio.Affine, spec: dict) -> np.ndarray:
    """Evaluate the same two planar triangles as the cropped, strided terrain mesh."""
    desired = from_bounds(*spec["terrain_bbox_lon_lat"], affine)
    left, top, stride = math.floor(desired.col_off), math.floor(desired.row_off), spec["terrain_sample_stride"]
    col, row = (~affine) * (coordinates[:, 0], coordinates[:, 1])
    c, r = (col - .5 - left) / stride, (row - .5 - top) / stride
    c0 = np.clip(np.floor(c).astype(int) * stride + left, 0, heights.shape[1] - 1 - stride)
    r0 = np.clip(np.floor(r).astype(int) * stride + top, 0, heights.shape[0] - 1 - stride)
    dc, dr = c - np.floor(c), r - np.floor(r)
    a, b, cc, d = heights[r0, c0], heights[r0, c0+stride], heights[r0+stride, c0], heights[r0+stride, c0+stride]
    interpolated = np.where(dc + dr <= 1, a + dc*(b-a) + dr*(cc-a), d + (1-dc)*(cc-d) + (1-dr)*(b-d))
    west, south, east, north = spec["terrain_bbox_lon_lat"]
    inside = (coordinates[:, 0] >= west) & (coordinates[:, 0] <= east) & (coordinates[:, 1] >= south) & (coordinates[:, 1] <= north)
    return np.where(inside, interpolated, elevation(coordinates, heights, affine))


def material(name: str, color: list[int], metal: float = 0, roughness: float = .75) -> PBRMaterial:
    return PBRMaterial(name=name, baseColorFactor=color, metallicFactor=metal, roughnessFactor=roughness)


def add_mesh(scene: trimesh.Scene, name: str, meshes: list[trimesh.Trimesh], surface: PBRMaterial,
             parent: str = "world") -> None:
    mesh = trimesh.util.concatenate(meshes)
    normals(mesh)
    mesh.visual = TextureVisuals(material=surface)
    scene.add_geometry(mesh, geom_name=name, node_name=name, parent_node_name=parent)


def equipment(spec: dict) -> dict[str, trimesh.Trimesh]:
    e = spec["estimated"]
    scene = trimesh.Scene()
    steel, porcelain, concrete = material("galvanized_steel", [152, 166, 171, 255], .72, .38), material("glass_insulators", [93, 130, 111, 255], .15, .25), material("concrete", [152, 145, 127, 255])
    h, half = e["pylon_height_m"], e["pylon_base_width_m"] / 2
    levels = np.linspace(.8, h, 9)
    corners = np.array([[-1, -1], [-1, 1], [1, 1], [1, -1]])
    def point(corner: np.ndarray, y: float) -> list[float]:
        x, z = corner * np.interp(y, [.8, h], [half, e["pylon_top_width_m"] / 2])
        return [x, y, z]
    members = [bar(point(c, .8), point(c, h), .10, 6) for c in corners]
    for low, high in zip(levels[:-1], levels[1:]):
        for c, d in zip(corners, np.roll(corners, -1, axis=0)):
            members.extend([bar(point(c, low), point(d, high), .055, 6),
                            bar(point(d, low), point(c, high), .055, 6),
                            bar(point(c, high), point(d, high), .065, 6)])
    insulators = []
    for y in e["crossarm_heights_m"]:
        for side in (-1, 1):
            end = side * e["crossarm_half_width_m"]
            for z in (-.45, .45):
                members.extend([bar([0, y, z], [end, y, z], .095, 6),
                                bar([0, y - 2.4, z], [end, y, z], .07, 6)])
            members.append(bar([end, y, -.45], [end, y, .45], .09, 6))
            bottom = y - e["insulator_length_m"]
            insulators.append(bar([end, bottom, 0], [end, y, 0], .09, 8))
            insulators += [bar([end, yy, 0], [end, yy + .085, 0], .22, 10)
                           for yy in np.linspace(bottom + .10, y - .18, 9)]
    add_mesh(scene, "pylon_steel", members, steel)
    add_mesh(scene, "pylon_insulators", insulators, porcelain)
    add_mesh(scene, "pylon_footings", [block([1.7, 1.2, 1.7], [x * half, .2, z * half]) for x, z in corners], concrete)
    width, depth = e["substation_pad_width_depth_m"]
    add_mesh(scene, "station_pad", [block([width, .65, depth], [0, -.1, 0])], concrete)
    tanks, fins, bushings, gantries = [], [], [], []
    for x in (-8, 8):
        tanks.extend([block([5, 4.2, 3.5], [x, 2.45, 1]),
                      bar([x - 2, 5, 1], [x + 2, 5, 1], .65, 16)])
        fins += [block([.15, 3, 1.1], [x + offset, 2.5, z]) for offset in np.linspace(-2.3, 2.3, 15) for z in (-1.3, 3.3)]
        for dx in (-1.5, 0, 1.5):
            bushings.append(bar([x + dx, 4.5, 0], [x + dx, 7, 0], .13, 10))
            bushings += [bar([x + dx, y, 0], [x + dx, y + .08, 0], .29, 10) for y in np.arange(4.6, 6.9, .25)]
    for z in (-8, 8):
        for x in (-14, 0, 14):
            gantries.extend([bar([x, .2, z], [x, 10, z], .19, 8),
                             bar([x - .5, 0, z], [x + .5, 10, z], .065, 6)])
        gantries.extend([bar([-14, 10, z], [14, 10, z], .18, 8), bar([-14, 9, z], [14, 9, z], .10, 8)])
        for x in np.arange(-14, 14, 2):
            gantries.append(bar([x, 9, z], [x + 2, 10, z], .065, 6))
    add_mesh(scene, "station_transformers", tanks, material("transformer_tanks", [100, 120, 115, 255], .35, .55))
    add_mesh(scene, "station_radiators", fins, steel)
    add_mesh(scene, "station_bushings", bushings, porcelain)
    add_mesh(scene, "station_gantries", gantries, steel)
    add_mesh(scene, "station_cabinets", [block([2.5, 2.4, 1.5], [x, 1.45, 10]) for x in (-8, -4, 4, 8)], material("cabinet", [204, 209, 196, 255], .2))
    pv = spec["pv_example"]
    width, length = pv["panel_width_length_m"]
    panels, frames, cells, supports = [], [], [], []
    for row in range(pv["rows"]):
        for column in range(pv["columns"]):
            x = (column - (pv["columns"] - 1)/2) * pv["column_spacing_m"]
            z = (row - (pv["rows"] - 1)/2) * pv["row_spacing_m"]
            matrix = translation(x, pv["center_height_m"], z) @ rotation(pv["tilt_deg"], (1, 0, 0))
            panel = block([width, .045, length], [0, 0, 0])
            panel.apply_transform(matrix)
            panels.append(panel)
            edging = [block([.035, .055, length], [side * width/2, .005, 0]) for side in (-1, 1)]
            edging += [block([width, .055, .035], [0, .005, side * length/2]) for side in (-1, 1)]
            grid = [block([.009, .012, length], [xx, .045, 0]) for xx in np.linspace(-width/2, width/2, 7)[1:-1]]
            grid += [block([width, .012, .009], [0, .045, zz]) for zz in np.linspace(-length/2, length/2, 13)[1:-1]]
            for edge in edging:
                edge.apply_transform(matrix)
            frames.extend(edging)
            for cell in grid:
                cell.apply_transform(matrix)
            cells.extend(grid)
            for dz in (-.6, .6):
                top = pv["center_height_m"] - dz * math.sin(math.radians(pv["tilt_deg"]))
                supports.append(bar([x, -3, z + dz], [x, top, z + dz], .055, 6))
            supports.append(bar([x, .15, z -.6], [x, pv["center_height_m"], z + .6], .035, 6))
    add_mesh(scene, "pv_panels", panels, material("PV_blue_cells", [22, 52, 87, 255], .15, .4))
    add_mesh(scene, "pv_frames", frames, steel)
    add_mesh(scene, "pv_cell_grid", cells, material("cell_divisions", [58, 84, 116, 255], .05, .65))
    add_mesh(scene, "pv_supports", supports, steel)
    add_mesh(scene, "pv_inverter", [block([.8, 1.3, .45], [6, .8, 0]), block([.65, .4, .02], [6, 1, .235])], material("inverter", [209, 214, 205, 255], .15))
    return dict(scene.geometry)


def wire_mesh(points: np.ndarray, radius: float) -> trimesh.Trimesh:
    tangent = np.gradient(points, axis=0)
    right = np.cross(tangent, [0, 1, 0])
    right /= np.linalg.norm(right, axis=1)[:, None]
    up = np.cross(right, tangent)
    up /= np.linalg.norm(up, axis=1)[:, None]
    angle = np.arange(6) * 2 * np.pi / 6
    vertices = points[:, None] + radius * (np.cos(angle)[None, :, None] * right[:, None] + np.sin(angle)[None, :, None] * up[:, None])
    i = np.arange(len(points) - 1)[:, None] * 6 + np.arange(6)
    j = (np.arange(6) + 1) % 6 + np.arange(len(points) - 1)[:, None] * 6
    faces = np.concatenate((np.stack((i, j, i + 6), axis=-1).reshape(-1, 3), np.stack((j, j + 6, i + 6), axis=-1).reshape(-1, 3)))
    return normals(trimesh.Trimesh(vertices=vertices.reshape(-1, 3), faces=faces[:, ::-1], process=False))


def physical_line(scene: trimesh.Scene, route: dict, spec: dict, frame: dict,
                  heights: np.ndarray, affine: rasterio.Affine) -> dict:
    e = spec["estimated"]
    xy = project(route["coordinates"], frame)
    positions = []
    for start, end in zip(xy[:-1], xy[1:]):
        count = math.ceil(np.linalg.norm((end-start)[[0, 2]]) / e["max_span_m"])
        positions.extend(start + (end-start) * t for t in np.linspace(0, 1, count, endpoint=False))
    positions = np.asarray([*positions, xy[-1]])
    ground = displayed_elevation(inverse(positions, frame), heights, affine, spec)
    positions[:, 1] = ground
    directions = np.gradient(positions, axis=0)
    directions[:, 1] = 0
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    right = np.cross([0, 1, 0], directions)
    attachments = []
    for side in (-1, 1):
        for y in e["crossarm_heights_m"]:
            attachments.append(positions + right * side * e["crossarm_half_width_m"] + [0, y - e["insulator_length_m"], 0])
    attachments.append(positions + [0, e["pylon_height_m"], 0])
    cables, clearance = [], math.inf
    for wire, attachment in enumerate(attachments):
        spans = []
        for start, end in zip(attachment[:-1], attachment[1:]):
            span = np.linalg.norm((end-start)[[0, 2]])
            t = np.linspace(0, 1, max(3, math.ceil(span / e["wire_sample_spacing_m"]) + 1))
            points = start + (end-start) * t[:, None]
            sag = e["sag_at_max_span_m"] * (span / e["max_span_m"])**2 * (.55 if wire == 6 else 1)
            points[:, 1] -= 4 * sag * t * (1-t)
            ll = inverse(points, frame)
            surface = np.maximum(elevation(ll, heights, affine), displayed_elevation(ll, heights, affine, spec))
            clearance = min(clearance, float(np.min(points[:, 1] - surface)))
            spans.append(points)
        cables.append(spans)
    # ponytail: one route-wide mast extension; use per-span engineering design if measured towers arrive.
    lowest_attachment = min(e["crossarm_heights_m"]) - e["insulator_length_m"]
    extension = max(0, e["minimum_clearance_m"] - clearance + .2) * e["pylon_height_m"] / lowest_attachment
    root = np.asarray(route["position"])
    scene.graph.update(frame_to="L3596", frame_from="world", matrix=translation(*root))
    towers = []
    for index, (position, direction, across) in enumerate(zip(positions, directions, right)):
        matrix = np.eye(4)
        matrix[:3, :3] = np.column_stack((across, [0, 1, 0], direction))
        matrix[:3, 3] = position - root
        matrix[1, 1] = (e["pylon_height_m"] + extension) / e["pylon_height_m"]
        name = f"L3596_pylon_{index:03d}"
        scene.graph.update(frame_to=name, frame_from="L3596", matrix=matrix)
        for part in ("steel", "insulators", "footings"):
            scene.graph.update(frame_to=f"{name}_{part}", frame_from=name, matrix=np.eye(4), geometry=f"pylon_{part}")
        towers.append({"node": name, "position": position.tolist(), "coordinates": inverse(position[None], frame)[0].tolist(), "height_m": e["pylon_height_m"] + extension})
    # Attachments follow the same uniform vertical scale as each estimated pylon.
    sampled_clearance = math.inf
    sampled_points = 0
    for wire, spans in enumerate(cables):
        meshes = []
        attach_height = e["pylon_height_m"] if wire == 6 else e["crossarm_heights_m"][wire % 3] - e["insulator_length_m"]
        for points in spans:
            points[:, 1] += extension * attach_height / e["pylon_height_m"]
            ll = inverse(points, frame)
            surface = np.maximum(elevation(ll, heights, affine), displayed_elevation(ll, heights, affine, spec))
            sampled_clearance = min(sampled_clearance, float(np.min(points[:, 1] - surface)))
            sampled_points += len(points)
            meshes.append(wire_mesh(points - root, e["guard_wire_radius_m"] if wire == 6 else e["conductor_radius_m"]))
        add_mesh(scene, f"L3596_{'guard_wire' if wire == 6 else f'conductor_{wire + 1}'}", meshes,
                 material("aluminium_wire", [100, 113, 120, 255], .8, .38), "L3596")
    assert sampled_clearance >= e["minimum_clearance_m"], f"wire below estimated clearance: {sampled_clearance}"
    target = np.asarray(towers[-7]["position"])
    offset = right[-7] * 60 + directions[-7] * 85 + [0, 35, 0]
    return {"pylons": towers, "source_vertices": len(xy), "conductors": 6, "guard_wires": 1,
            "minimum_sampled_dsm_clearance_m": sampled_clearance, "clearance_sample_count": sampled_points,
            "clearance_sample_spacing_max_m": e["wire_sample_spacing_m"], "mast_extension_m": extension,
            "inspect": camera(target, offset, [0, 19, 0])}


def camera(center: np.ndarray, offset: list, target: list) -> dict:
    return {"position": (center + offset).tolist(), "target": (center + target).tolist(), "fov": 48, "near": .15, "far": 400000}


def select_pv(features: list[dict], spec: dict, frame: dict) -> list[list[dict]]:
    groups = {}
    west, south, east, north = spec["terrain_bbox_lon_lat"]
    for f in sorted(features, key=lambda f: f["properties"]["source_id"]):
        lon, lat = f["geometry"]["coordinates"]
        if west < lon < east and south < lat < north:
            groups.setdefault((lon, lat), []).append(f)
    hallim = next(f for f in read_features("substation") if f["id"] == "hub:substation:888")
    anchor = project([hallim["geometry"]["coordinates"]], frame)[0]
    return sorted(groups.values(), key=lambda group: float(np.linalg.norm(project([group[0]["geometry"]["coordinates"]], frame)[0] - anchor)))[:spec["pv_example"]["sample_count"]]


def terrain_mesh(scene: trimesh.Scene, spec: dict, frame: dict) -> dict:
    with rasterio.open(DEM) as dem, rasterio.open(WATER) as water:
        desired = from_bounds(*spec["terrain_bbox_lon_lat"], dem.transform)
        left, top = math.floor(desired.col_off), math.floor(desired.row_off)
        window = Window(left, top, math.ceil(desired.col_off + desired.width) - left, math.ceil(desired.row_off + desired.height) - top)
        heights, wet = dem.read(1, window=window), water.read(1, window=window)
        affine = dem.window_transform(window)
        bbox = list(rasterio.windows.bounds(window, dem.transform))
    polygons, landcover_counts = [], Counter()
    with (GIS / "landcover.geojsonl").open() as stream:
        for line in stream:
            f = json.loads(line)
            west, south, east, north = geometry_bounds(f["geometry"])
            if east < bbox[0] or north < bbox[1] or west > bbox[2] or south > bbox[3]:
                continue
            code = int(f["properties"]["l2_code"])
            polygons.append((f["geometry"], code))
            landcover_counts[code] += 1
    cover = rasterize(polygons, out_shape=heights.shape, transform=affine, fill=0, dtype="uint16")
    stride = spec["terrain_sample_stride"]
    rows, cols = np.meshgrid(np.r_[np.arange(0, heights.shape[0]-1, stride), heights.shape[0]-1],
                             np.r_[np.arange(0, heights.shape[1]-1, stride), heights.shape[1]-1], indexing="ij")
    lon, lat = rasterio.transform.xy(affine, rows.ravel(), cols.ravel())
    sampled_heights = np.where(wet[rows, cols] == 1, 0, heights[rows, cols])
    vertices = project(np.column_stack((lon, lat)), frame, sampled_heights.ravel())
    h, w = rows.shape
    i = np.arange(h-1)[:, None] * w + np.arange(w-1)
    faces = np.stack((np.stack((i, i+w, i+1), axis=-1), np.stack((i+1, i+w, i+w+1), axis=-1)), axis=2).reshape(-1, 3)
    classes = cover[rows, cols][:-1, :-1].copy()
    classes[wet[rows, cols][:-1, :-1] == 1] = 999
    face_classes = np.repeat(classes.ravel(), 2)
    palette = {0: [114, 127, 87], 100: [147, 141, 126], 200: [140, 153, 85], 210: [119, 147, 89],
               220: [157, 165, 101], 230: [175, 181, 164], 240: [97, 132, 71], 250: [157, 145, 92],
               300: [59, 101, 65], 400: [120, 146, 82], 500: [88, 127, 119], 600: [143, 125, 96],
               700: [39, 100, 125], 999: [29, 76, 100]}
    for code in np.unique(face_classes):
        color = palette.get(int(code), palette.get(int(code)//100*100, palette[0]))
        name = "ocean_surface" if code == 999 else f"terrain_landcover_{code}"
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces[face_classes == code], process=False)
        mesh.remove_unreferenced_vertices()
        add_mesh(scene, name, [mesh], material(name, color + [255], roughness=.30 if code == 999 else 1))
    return {"bbox_lon_lat": bbox, "extent": [vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()],
            "native_shape": list(heights.shape), "mesh_shape": list(rows.shape), "sample_stride": stride,
            "height_range_m": [float(heights.min()), float(heights.max())], "vertical_datum": "EGM2008",
            "sample_method": "native pixel centres, every 2 pixels plus final edge; no vertical exaggeration",
            "landcover_feature_count": len(polygons), "landcover_class_counts": dict(landcover_counts),
            "palette": palette, "materials": "source landcover classes with synthetic colours; no photograph textures",
            "water": "Copernicus WBM=1 ocean displayed at 0 m EGM2008; other heights unchanged", "bathymetry_available": False}


def simplify(points: np.ndarray, tolerance: float) -> np.ndarray:
    keep, stack = {0, len(points)-1}, [(0, len(points)-1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        segment = points[end] - points[start]
        delta = points[start+1:end] - points[start]
        t = np.clip(delta @ segment / max(float(segment @ segment), 1e-20), 0, 1)
        distances = np.linalg.norm(delta - t[:, None] * segment, axis=1)
        index = int(np.argmax(distances))
        if distances[index] > tolerance:
            middle = start + 1 + index
            keep.add(middle)
            stack.extend(((start, middle), (middle, end)))
    return points[sorted(keep)]


def verify(output: Path, spec: dict, source_lines: list[dict], source_stations: list[dict]) -> dict:
    manifest = json.loads((output / "manifest.json").read_text())
    frame = manifest["coordinateFrame"]
    assert frame == json.loads(Path("var/rendering/twin/manifest.json").read_text())["coordinateFrame"]
    assert manifest["units"] == "m" and frame["scale"] == 1 and frame["vertical_crs"] == "EPSG:3855"
    for source in manifest["sources"]:
        assert sha(Path(source["path"])) == source["sha256"]
    assert manifest["assetassumptions"]["calibration_sha256"] == sha(Path(manifest["assetassumptions"]["calibration_file"]))
    for entry in manifest["files"]:
        assert sha(output / entry["path"]) == entry["sha256"]
    groups, excluded = group_routes(source_lines)
    assert len(source_lines) == 54 and len(groups) == 46 and len(excluded) == 5
    assert excluded == manifest["audit"]["excluded_minor_line_ids"]
    assert len(manifest["routes"]) == 46
    for route, group in zip(manifest["routes"], groups):
        assert route["source_ids"] == [f["id"] for f in group]
        assert route["coordinates"] == group[0]["geometry"]["coordinates"]
        assert len(route["points"]) == len(route["coordinates"])
        assert np.allclose(inverse(np.asarray(route["points"]), frame), route["coordinates"], atol=2e-8, rtol=0)
        assert route["source_records"] == [{"id": f["id"], "properties": f["properties"]} for f in group]
    assert Counter(r["kind"] for r in manifest["routes"]) == {"transmission": 42, "hvdc": 3, "cable": 1}
    hvdc = [r for r in manifest["routes"] if r["kind"] == "hvdc"]
    assert {tuple(r["source_ids"]) for r in hvdc} == {("hub:power_line:3631", "hub:power_line:4156"), ("hub:power_line:3632", "hub:power_line:4157"), ("hub:power_line:3633", "hub:power_line:4158")}
    assert all(max(c[1] for c in r["coordinates"]) > 34 for r in hvdc)
    stations = [f for f in manifest["facilities"] if f["kind"] == "substation"]
    assert {f["id"] for f in stations} == {f["id"] for f in source_stations} and len(stations) == 13
    pv = [f for f in manifest["facilities"] if f["kind"] == "pv"]
    selected = select_pv(read_features("pv_facility"), spec, frame)
    assert len(pv) == 3 and len({tuple(f["coordinates"]) for f in pv}) == 3
    for entry, group in zip(pv, selected):
        assert entry["id"] == group[0]["id"] and entry["coordinates"] == group[0]["geometry"]["coordinates"]
        assert entry["source_properties"] == group[0]["properties"] and entry["solar_power"] is None
        assert entry["coordinate_source_ids"] == [f["id"] for f in group]
        assert entry["coordinate_multiplicity"] == len(group)
        assert entry["display_panel_count"] == spec["pv_example"]["rows"] * spec["pv_example"]["columns"]
        assert np.allclose(inverse(np.asarray([entry["position"]]), frame)[0], entry["coordinates"], atol=2e-8, rtol=0)
    scene = trimesh.load(output / "scene.glb", force="scene", process=False)
    tree = glb_tree(output / "scene.glb")
    nodes = {n["name"]: n for n in tree["nodes"]}
    assert len(nodes) == len(tree["nodes"])
    for f in manifest["facilities"]:
        assert "rotor_node" not in f and f["node"] in nodes
        assert np.allclose(scene.graph[f["node"]][0][:3, 3], f["position"], atol=.002)
        if f["kind"] == "substation":
            source = next(s for s in source_stations if s["id"] == f["id"])
            assert f["coordinates"] == source["geometry"]["coordinates"] and f["source_properties"] == source["properties"]
            assert np.allclose(inverse(np.asarray([f["position"]]), frame)[0], f["coordinates"], atol=2e-8, rtol=0)
            assert all(f"{f['node']}_{part}" in nodes for part in ("pad", "transformers", "radiators", "bushings", "gantries", "cabinets"))
    for geometry in scene.geometry.values():
        assert len(geometry.faces) and np.isfinite(geometry.vertices).all() and np.isfinite(geometry.vertex_normals).all()
        assert np.allclose(np.linalg.norm(geometry.vertex_normals, axis=1), 1, atol=2e-3)
    line = manifest["physical_line"]
    assert all(f"L3596_conductor_{i}" in nodes for i in range(1, 7)) and "L3596_guard_wire" in nodes
    assert all(len(nodes[p["node"]]["children"]) == 3 for p in line["pylons"])
    locations = np.asarray([p["position"] for p in line["pylons"]])
    assert np.linalg.norm(np.diff(locations[:, [0, 2]], axis=0), axis=1).max() <= spec["estimated"]["max_span_m"] + 1e-6
    original = project(next(f for f in source_lines if f["id"] == spec["representative_source_id"])["geometry"]["coordinates"], frame)
    assert all(np.linalg.norm(locations[:, [0, 2]] - point[[0, 2]], axis=1).min() < 1e-6 for point in original)
    with rasterio.open(DEM) as dem:
        heights, affine = dem.read(1), dem.transform
    pv_clearance = math.inf
    for f in pv:
        assert all(f"{f['node']}_{part}" in nodes for part in ("panels", "frames", "cell_grid", "supports", "inverter"))
        matrix, geometry = scene.graph[f"{f['node']}_panels"]
        points = trimesh.transform_points(scene.geometry[geometry].vertices, matrix)
        ll = inverse(points, frame)
        surface = np.maximum(elevation(ll, heights, affine), displayed_elevation(ll, heights, affine, spec))
        pv_clearance = min(pv_clearance, float(np.min(points[:, 1] - surface)))
    assert pv_clearance > 0
    minimum = math.inf
    for name in [f"L3596_conductor_{i}" for i in range(1, 7)] + ["L3596_guard_wire"]:
        matrix, geometry = scene.graph[name]
        points = trimesh.transform_points(scene.geometry[geometry].vertices, matrix)
        ll = inverse(points, frame)
        surface = np.maximum(elevation(ll, heights, affine), displayed_elevation(ll, heights, affine, spec))
        minimum = min(minimum, float(np.min(points[:, 1] - surface)))
    assert minimum >= spec["estimated"]["minimum_clearance_m"] - .05
    probe = wire_mesh(np.array([[0., 0, 0], [0, 0, 5], [0, 0, 10]]), .02)
    assert np.all(np.sum(probe.vertex_normals[:, :2] * probe.vertices[:, :2], axis=1) > 0), "wire normals must face outward"
    assert manifest["terrain"]["landcover_feature_count"] > 0 and len(manifest["coast"]) > 0
    return {"status": "PASS", "source_lines": 54, "display_routes": 46, "substations": 13,
            "pv_examples": len(pv), "minimum_exported_pv_panel_dsm_and_displayed_terrain_clearance_m": pv_clearance,
            "physical_facility_roots": len(manifest["facilities"]), "pylons": len(locations),
            "reopened_meshes": len(scene.geometry), "minimum_exported_wire_surface_dsm_and_displayed_terrain_clearance_m": minimum,
            "checks": ["immutable GIS/DSM hashes and output hash", "exact type/voltage/forward-reverse grouping and all IDs", "five minor lines excluded and three full mainland HVDC ends", "all thirteen source substations, including same-name different-voltage records", "three distinct deterministic PV samples with registration-coordinate multiplicity, source metadata and null live power", "estimated PV panel surfaces above DSM", "source vertices retained, projection/inverse and matching twin coordinate frame", "metre units and EGM2008 heights", "finite mesh vertices and unit normals", "pylon/insulator/footing hierarchy, six conductors and guard wire", "source vertices among estimated pylons and maximum span", "exported wire surfaces above bilinear native DSM", "partial DSM terrain and source landcover classes"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=Path("renderers/twin/grid-spec.json"))
    parser.add_argument("--output", type=Path, default=Path("var/rendering/grid"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    assert args.output.resolve() != Path("var/rendering/twin").resolve(), "keep existing turbine assets separate"
    spec = json.loads(args.spec.read_text())
    e = spec["estimated"]
    assert all(math.isfinite(v) and v > 0 for value in e.values() for v in (value if isinstance(value, list) else [value]))
    assert e["pylon_height_m"] > max(e["crossarm_heights_m"]) and min(e["crossarm_heights_m"]) > e["insulator_length_m"]
    sources, terrain_source = checked_sources()
    lines, stations = read_features("power_line"), read_features("substation")
    if args.self_test:
        print(json.dumps(verify(args.output, spec, lines, stations), indent=2))
        return
    frame = json.loads(Path("var/rendering/twin/manifest.json").read_text())["coordinateFrame"]
    with rasterio.open(DEM) as dem:
        heights, affine = dem.read(1), dem.transform
    groups, excluded = group_routes(lines)
    routes = []
    for group in groups:
        f, p = group[0], group[0]["properties"]
        coordinates = f["geometry"]["coordinates"]
        kind = "hvdc" if p["source_id"] in spec["hvdc_source_ids"] else "transmission" if p["power_type"] == "line" else "cable"
        points = project(coordinates, frame, np.maximum(0, elevation(np.asarray(coordinates), heights, affine)) + e["route_display_elevation_m"])
        name = p["name"] or f"{'HVDC' if kind == 'hvdc' else '송전 경로' if kind == 'transmission' else '케이블 경로'} {p['source_id']}"
        routes.append({"id": f["id"], "source_ids": [item["id"] for item in group], "kind": kind, "name": name,
                       "voltage": p["voltage"], "coordinates": coordinates, "points": points.tolist(),
                       "position": points[len(points)//2].tolist(), "physical_node": "L3596" if f["id"] == spec["representative_source_id"] else None,
                       "source_properties": p, "source_records": [{"id": row["id"], "properties": row["properties"]} for row in group],
                       "name_status": "source name or source-ID display label", "vertical_position_status": "DSM + display offset, outside DSM zero + offset; not surveyed line altitude"})
    scene = trimesh.Scene(base_frame="world")
    scene.geometry.update(equipment(spec))
    facilities = []
    for f in stations:
        p, coordinates = f["properties"], f["geometry"]["coordinates"]
        position = project([coordinates], frame, displayed_elevation(np.asarray([coordinates]), heights, affine, spec))[0]
        node = f"S{p['source_id']}"
        scene.graph.update(frame_to=node, frame_from="world", matrix=translation(*position) @ rotation(e["substation_yaw_deg"], (0, 1, 0)))
        for part in ("pad", "transformers", "radiators", "bushings", "gantries", "cabinets"):
            scene.graph.update(frame_to=f"{node}_{part}", frame_from=node, matrix=np.eye(4), geometry=f"station_{part}")
        facilities.append({"id": f["id"], "node": node, "kind": "substation", "name": p["name"] or p["name_en"] or node,
                           "coordinates": coordinates, "position": position.tolist(), "source_properties": p,
                           "dimensions_estimated": True, "layout_surveyed": False, "telemetry": None,
                           "inspect": camera(position, [-45, 30, 48], [0, 4, 0])})
    pv_groups = select_pv(read_features("pv_facility"), spec, frame)
    for group in pv_groups:
        f, p = group[0], group[0]["properties"]
        coordinates = f["geometry"]["coordinates"]
        position = project([coordinates], frame)[0]
        footprint = np.array([[x, 0, z] for x in (-6, 0, 6) for z in (-7, 0, 7)]) + position
        ll = inverse(footprint, frame)
        position[1] = float(np.maximum(elevation(ll, heights, affine), displayed_elevation(ll, heights, affine, spec)).max()) + .1
        node = f"P{p['source_id']}"
        scene.graph.update(frame_to=node, frame_from="world", matrix=translation(*position))
        for part in ("panels", "frames", "cell_grid", "supports", "inverter"):
            scene.graph.update(frame_to=f"{node}_{part}", frame_from=node, matrix=np.eye(4), geometry=f"pv_{part}")
        facilities.append({"id": f["id"], "node": node, "kind": "pv", "name": p["name"] or node,
                           "coordinates": coordinates, "position": position.tolist(), "source_properties": p,
                           "coordinate_multiplicity": len(group), "coordinate_source_ids": [row["id"] for row in group],
                           "coordinate_status": "registration/geocoded source point shared by multiple records; not surveyed parcel",
                           "dimensions_estimated": True, "layout_surveyed": False, "display_panel_count": spec["pv_example"]["rows"] * spec["pv_example"]["columns"],
                           "actual_panel_count": None, "actual_footprint_m2": None, "solar_power": None, "telemetry": None,
                           "height_status": "estimated level display mount above local DSM maximum; not surveyed foundation",
                           "inspect": camera(position, [-17, 16, 22], [0, 1, 0])})
    route = next(r for r in routes if r["physical_node"])
    physical = physical_line(scene, route, spec, frame, heights, affine)
    facilities.append({"id": route["id"], "node": "L3596", "kind": "line", "name": "한림 대표 송전 경로 3596",
                       "coordinates": route["coordinates"], "position": route["position"], "source_properties": route["source_properties"],
                       "dimensions_estimated": True, "tower_positions_surveyed": False, "telemetry": None, "inspect": physical["inspect"]})
    terrain = terrain_mesh(scene, spec, frame)
    coast = []
    for f in read_features("coastline"):
        parts = f["geometry"]["coordinates"] if f["geometry"]["type"] == "MultiLineString" else [f["geometry"]["coordinates"]]
        coast.extend(simplify(project(part, frame), spec["coast_simplify_tolerance_m"]).tolist() for part in parts if len(part) >= 2)
    center = np.mean(np.asarray(terrain["extent"]), axis=0)
    network_center = project([[126.535, 33.37]], frame)[0]
    full_points = np.concatenate([r["points"] for r in routes])
    hvdc_center = (full_points.min(axis=0) + full_points.max(axis=0)) / 2
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "scene.glb").write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))
    manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "units": "m", "coordinateFrame": frame,
                "facilities": facilities, "routes": routes, "coast": coast, "terrain": terrain, "physical_line": physical,
                "cameras": {"inspect": physical["inspect"], "overview": camera(center, [-9000, 12000, 12500], [0, 0, 0]),
                            "pv": next(f["inspect"] for f in facilities if f["kind"] == "pv"),
                            "network": camera(network_center, [0, 85000, 42000], [0, 0, 0]),
                            "hvdc": camera(hvdc_center, [-10000, 210000, 110000], [0, 0, 0])},
                "assetassumptions": {"user_authorized_estimation": True, "estimated": e, "pv_example": spec["pv_example"], "limits": spec["claim_limits"],
                                     "calibration_file": str(args.spec), "calibration_sha256": sha(args.spec)},
                "audit": {"source_lines": len(lines), "display_routes": len(routes), "substations": len(stations), "pv_examples": len(pv_groups),
                          "route_kinds": dict(Counter(r["kind"] for r in routes)), "excluded_minor_line_ids": excluded,
                          "deduplicated_groups": [r["source_ids"] for r in routes if len(r["source_ids"]) > 1],
                          "coast_simplify_tolerance_m": spec["coast_simplify_tolerance_m"], "electrical_topology_inferred": False},
                "sources": sources, "files": [{"path": "scene.glb", "bytes": (args.output / "scene.glb").stat().st_size, "sha256": sha(args.output / "scene.glb")}],
                "status": "source_GIS_with_estimated_equipment", "surveyed_asset": False, "photo_texture_copied": False,
                "license_note": "GIS and terrain retain source conditions; equipment is original generic estimated geometry. No source photographs used."}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    credit = "GIS: Energy-hub public.power_line, public.substation, public.pv_facility, public.landcover; original source dataset terms apply.\nCoastline: VWorld LT_L_TOISDEPCNTAH, https://api.vworld.kr/req/data ; VWorld provider terms and attribution apply.\n"
    credit += terrain_source["license"]["required_modified_notice"] + "\n" + terrain_source["license"]["required_liability_notice"] + "\n"
    credit += "Changes: partial DSM crop sampled at two-pixel spacing; source landcover rasterized into synthetic colours; source route coordinates projected to UTM52N; exact duplicate routes grouped with all IDs retained; generic estimated pylons, wires and substation equipment added. No photographic textures, surveyed layouts, bathymetry, telemetry or inferred electrical connections.\n"
    (args.output / "CREDITS.txt").write_text(credit)
    result = verify(args.output, spec, lines, stations)
    (args.output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "files": manifest["files"], "verification": result}, indent=2))


if __name__ == "__main__":
    main()
