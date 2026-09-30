#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3"]
# ///
# noqa: SIZE_OK -- one self-contained modelling script; split only if another asset needs these shapes.
# ─── How to run ───
# uv run renderers/twin/build.py
# uv run renderers/twin/build.py --self-test
# ──────────────────
"""Build editable, photo-informed estimated turbine geometry at source GIS positions."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import struct

import numpy as np
import rasterio
from rasterio.windows import Window, from_bounds
from rasterio.warp import transform as project_crs
import trimesh
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def translation(x: float = 0, y: float = 0, z: float = 0) -> np.ndarray:
    matrix = np.eye(4)
    matrix[:3, 3] = (x, y, z)
    return matrix


def rotation(degrees: float, axis: tuple[float, float, float]) -> np.ndarray:
    return trimesh.transformations.rotation_matrix(math.radians(degrees), axis)


def normals(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Angle-weight normals with NumPy, avoiding trimesh's optional sparse-matrix dependency."""
    summed = np.zeros_like(mesh.vertices)
    for corner in range(3):
        np.add.at(summed, mesh.faces[:, corner], mesh.face_normals * mesh.face_angles[:, corner, None])
    length = np.linalg.norm(summed, axis=1)
    assert np.all(length > 0), "unreferenced vertex or degenerate surface"
    mesh.vertex_normals = summed / length[:, None]
    return mesh


def loft(rings: list, cap: bool = True) -> trimesh.Trimesh:
    """Join closed section rings into a smooth, capped mechanical surface."""
    vertices = np.asarray(rings, dtype=float).reshape(-1, 3)
    count = len(rings[0])
    faces = []
    for section in range(len(rings) - 1):
        for i in range(count):
            a, b = section * count + i, section * count + (i + 1) % count
            faces.extend([[a, b, b + count], [a, b + count, a + count]])
    if cap:
        vertices = np.vstack((vertices, np.mean(rings[0], axis=0), np.mean(rings[-1], axis=0)))
        for i in range(count):
            faces.append([len(vertices) - 2, (i + 1) % count, i])
            faces.append([len(vertices) - 1, (len(rings) - 1) * count + i,
                          (len(rings) - 1) * count + (i + 1) % count])
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
    mesh.fix_normals(multibody=False)
    return normals(mesh)


def tube(y0: float, y1: float, radius0: float, radius1: float,
         thickness: float, sections: int) -> trimesh.Trimesh:
    """A hollow tapered shell, including annular end faces."""
    angle = np.arange(sections) * 2 * np.pi / sections
    rings = []
    for y, radius in ((y0, radius0), (y1, radius1),
                      (y1, radius1 - thickness), (y0, radius0 - thickness), (y0, radius0)):
        rings.append(np.column_stack((radius * np.cos(angle), np.full(sections, y), radius * np.sin(angle))))
    return loft(rings, cap=False)


def bar(start: list, end: list, radius: float, sections: int = 12) -> trimesh.Trimesh:
    return normals(trimesh.creation.cylinder(radius=radius, segment=[start, end], sections=sections))


def block(extents: list, center: list) -> trimesh.Trimesh:
    return normals(trimesh.creation.box(extents=extents, transform=translation(*center)))


def blade(spec: dict) -> trimesh.Trimesh:
    """Loft twisted cambered airfoil sections, with swept/prebent tips and a thick root."""
    estimated, detail = spec["estimated"], spec["mesh"]
    radius = spec["verified_operator_spec"]["rotor_diameter_m"] / 2
    theta = np.arange(detail["blade_profile_sections"]) * 2 * np.pi / detail["blade_profile_sections"]
    fraction = (1 - np.cos(theta)) / 2
    rings = []
    for t in np.linspace(0, 1, detail["blade_span_sections"]):
        span = estimated["blade_root_radius_m"] + (radius - estimated["blade_root_radius_m"]) * t
        chord = np.interp(t, [0, .08, .2, .55, .82, 1], [2.0, 3.2, estimated["blade_max_chord_m"], 2.7, 1.35, .045])
        ratio = .30 - .18 * t
        thickness = 5 * ratio * (.2969 * np.sqrt(fraction) - .1260 * fraction
                                  - .3516 * fraction**2 + .2843 * fraction**3 - .1036 * fraction**4)
        camber = .025 * np.sin(np.pi * fraction)
        x = chord * (np.sign(np.sin(theta)) * thickness + camber)
        z = chord * (fraction - .28)
        blend = min(t / .085, 1)
        x = (1 - blend) * .86 * np.cos(theta) + blend * x
        z = (1 - blend) * .86 * np.sin(theta) + blend * z
        twist = math.radians(estimated["blade_root_twist_deg"] * (1 - t)**1.6)
        xx = x * math.cos(twist) - z * math.sin(twist) - estimated["blade_prebend_m"] * t**2.5
        zz = x * math.sin(twist) + z * math.cos(twist) + estimated["blade_sweep_m"] * t**2
        rings.append(np.column_stack((xx, np.full_like(xx, span), zz)))
    mesh = loft(rings)
    # The published diameter is the swept radius in the rotor plane, independent of prebend.
    mesh.vertices[:, 1:] *= radius / np.linalg.norm(mesh.vertices[:, 1:], axis=1).max()
    return normals(mesh)


def prototype(spec: dict) -> tuple[trimesh.Scene, dict]:
    """Build fewer than 50 semantic parts; repeated static rails/braces/rungs share a merged part."""
    e, detail = spec["estimated"], spec["mesh"]
    hub_h, deck = e["hub_height_m"], e["deck_height_m"]
    materials = {name: PBRMaterial(name=name, baseColorFactor=color, metallicFactor=metal, roughnessFactor=rough)
                 for name, color, metal, rough in [
                     ("paint_white", [235, 237, 235, 255], .10, .36),
                     ("support_yellow", [231, 173, 27, 255], .18, .48),
                     ("galvanized", [141, 153, 159, 255], .70, .34),
                     ("submerged_steel", [57, 65, 68, 255], .65, .65),
                     ("vent_dark", [50, 60, 66, 255], .35, .68),
                     ("beacon_red", [186, 37, 28, 255], .05, .25) ]}
    scene = trimesh.Scene(base_frame="asset")
    scene.graph.update(frame_to="TEMPLATE", frame_from="asset", matrix=np.eye(4))

    def add(name: str, mesh: trimesh.Trimesh, material: str = "paint_white", parent: str = "TEMPLATE") -> None:
        normals(mesh)
        mesh.visual = TextureVisuals(material=materials[material])
        scene.add_geometry(mesh, geom_name=name, node_name=name, parent_node_name=parent)

    add("tower", tube(deck + .45, hub_h - 1.8, e["tower_base_radius_m"], e["tower_top_radius_m"],
                      e["tower_shell_thickness_m"], detail["round_sections"]))
    flanges = [tube(y, y + .13, radius + .045, radius + .045, .075, detail["round_sections"])
               for t in (0, 1/3, 2/3, .99)
               for y, radius in [(deck + .55 + t * (hub_h - 2.35 - deck),
                                   e["tower_base_radius_m"] * (1 - t) + e["tower_top_radius_m"] * t)]]
    add("tower_flanges", trimesh.util.concatenate(flanges), "galvanized")
    add("tower_hatch", block([.10, 2.05, .85], [e["tower_base_radius_m"] + .03, deck + 1.85, 0]), "vent_dark")
    length, width, height = e["nacelle_length_width_height_m"]
    profile = []
    for center_y, center_z, start in [(height / 2 - .42, width / 2 - .42, 0),
                                       (-height / 2 + .42, width / 2 - .42, 90),
                                       (-height / 2 + .42, -width / 2 + .42, 180),
                                       (height / 2 - .42, -width / 2 + .42, 270)]:
        for angle in np.linspace(start, start + 90, 5, endpoint=False):
            theta = math.radians(angle)
            profile.append([center_y + .42 * math.cos(theta), center_z + .42 * math.sin(theta)])
    rings = [[[x, hub_h + scale * y, scale * z] for y, z in profile]
             for x, scale in [(-length / 2 + 1, .76), (-length / 2 + 1.55, 1),
                              (length / 2 + .4, 1), (length / 2 + 1, .87)]]
    add("nacelle", loft(rings))
    panels = [block([2.4, .11, .10], [2.0, hub_h - .85 + row * .27, side * (width / 2 + .01)])
              for side in (-1, 1) for row in range(6)]
    add("vent_panels", trimesh.util.concatenate(panels), "vent_dark", "nacelle")
    add("maintenance_hatch", block([1.5, .08, 1.15], [1.0, hub_h + height / 2 + .045, 0]), "galvanized", "nacelle")
    add("weather_mast", bar([4.1, hub_h + 1.5, 0], [4.1, hub_h + 4.0, 0], .04), "galvanized", "nacelle")
    beacon = trimesh.creation.icosphere(subdivisions=2, radius=.16)
    beacon.apply_translation([4.1, hub_h + 4.1, 0])
    add("warning_beacon", beacon, "beacon_red", "nacelle")
    add("shaft", bar([e["rotor_offset_x_m"], hub_h, 0], [-3.0, hub_h, 0], .63, 24), "galvanized", "nacelle")
    scene.graph.update(frame_to="rotor", frame_from="nacelle",
                       matrix=translation(e["rotor_offset_x_m"], hub_h, 0) @ rotation(e["rotor_phase_deg"], (1, 0, 0)))
    hub = trimesh.creation.icosphere(subdivisions=3, radius=1)
    hub.apply_scale([2.0, e["hub_radius_m"], e["hub_radius_m"]])
    hub.apply_translation([-.25, 0, 0])
    add("hub", hub, parent="rotor")
    blade_bearings = []
    for index in range(3):
        bearing = bar([0, e["hub_radius_m"] * .78, 0], [0, e["blade_root_radius_m"] + .18, 0], .89, 24)
        bearing.apply_transform(rotation(index * 120, (1, 0, 0)))
        blade_bearings.append(normals(bearing))
    add("pitch_bearings", trimesh.util.concatenate(blade_bearings), parent="rotor")
    airfoil = blade(spec)
    airfoil.visual = TextureVisuals(material=materials["paint_white"])
    scene.geometry["blade_airfoil"] = airfoil
    for index in range(3):
        scene.graph.update(frame_to=f"blade_{index + 1}", frame_from="rotor", geometry="blade_airfoil",
                           matrix=rotation(index * 120, (1, 0, 0)))
    scene.graph.update(frame_to="foundation", frame_from="TEMPLATE", matrix=np.eye(4))
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]

    def leg_point(corner: tuple, y: float) -> list:
        half = np.interp(y, [-e["sea_depth_m"], deck], [e["jacket_base_width_m"] / 2, e["jacket_top_width_m"] / 2])
        return [corner[0] * half, y, corner[1] * half]

    for name, low, high, material in [("jacket_submerged", -e["sea_depth_m"], 0, "submerged_steel"),
                                      ("jacket_above_water", 0, deck, "support_yellow")]:
        members = [bar(leg_point(c, low), leg_point(c, high), e["jacket_leg_radius_m"]) for c in corners]
        levels = np.linspace(low, high, 3)
        for lower, upper in zip(levels[:-1], levels[1:]):
            for i, corner in enumerate(corners):
                other = corners[(i + 1) % 4]
                members.extend([bar(leg_point(corner, lower), leg_point(other, upper), e["brace_radius_m"]),
                                bar(leg_point(other, lower), leg_point(corner, upper), e["brace_radius_m"]),
                                bar(leg_point(corner, upper), leg_point(other, upper), e["brace_radius_m"])])
        add(name, trimesh.util.concatenate(members), material, "foundation")
    add("deck", block([e["deck_width_m"], .45, e["deck_width_m"]], [0, deck + .225, 0]), "galvanized", "foundation")
    half, top, rails = e["deck_width_m"] / 2, deck + .45, []
    for i, c in enumerate(corners):
        d = corners[(i + 1) % 4]
        for y in (top + .6, top + e["railing_height_m"]):
            rails.append(bar([c[0] * half, y, c[1] * half], [d[0] * half, y, d[1] * half], .045, 8))
        for t in np.linspace(0, 1, 6, endpoint=False):
            x, z = (np.asarray(c) * (1 - t) + np.asarray(d) * t) * half
            rails.append(bar([x, top, z], [x, top + e["railing_height_m"], z], .055, 8))
    add("railings", trimesh.util.concatenate(rails), "paint_white", "foundation")
    ladder = [bar([half + .12, 1, z], [half + .12, top + 1.1, z], .05, 8) for z in (-.35, .35)]
    ladder += [bar([half + .12, y, -.35], [half + .12, y, .35], .035, 8) for y in np.arange(1, top + 1, .32)]
    add("access_ladder", trimesh.util.concatenate(ladder), "support_yellow", "foundation")
    return scene, {"mesh_parts": len(scene.graph.nodes_geometry), "blade_radius_m": float(np.linalg.norm(airfoil.vertices[:, 1:], axis=1).max()),
                   "triangles_single_turbine": int(sum(len(scene.geometry[g].faces) for _, g in (scene.graph[n] for n in scene.graph.nodes_geometry)))}


def instance(scene: trimesh.Scene, prototype_scene: trimesh.Scene, node: str, matrix: np.ndarray) -> None:
    scene.graph.update(frame_to=node, frame_from="world", matrix=matrix)
    for source, target, data in prototype_scene.graph.to_edgelist():
        if target == "TEMPLATE":
            continue
        parent = node if source == "TEMPLATE" else f"{node}_{source}"
        child = f"{node}_{target}"
        kwargs = {key: value for key, value in data.items() if key in ("matrix", "geometry")}
        scene.graph.update(frame_to=child, frame_from=parent, **kwargs)


def glb_tree(path: Path) -> dict:
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data)
    assert magic == b"glTF" and version == 2 and length == len(data)
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    assert chunk_type == 0x4E4F534A
    return json.loads(data[20:20 + chunk_length])


def verify(output: Path, spec: dict, source_features: list) -> dict:
    """Check exported/reopened files, published radius, source placement, hierarchy and real mesh normals."""
    manifest = json.loads((output / "manifest.json").read_text())
    assert len(manifest["facilities"]) == 10
    ids = {f["id"] for f in source_features}
    assert {f["id"] for f in manifest["facilities"]} == ids
    for entry in manifest["files"]:
        assert sha(output / entry["path"]) == entry["sha256"]
    scene = trimesh.load(output / "scene.glb", force="scene", process=False)
    asset = trimesh.load(output / "turbine.glb", force="scene", process=False)
    assert all(np.isfinite(g.vertices).all() and np.isfinite(g.vertex_normals).all() and len(g.faces) > 0 for g in scene.geometry.values())
    assert all(np.allclose(np.linalg.norm(g.vertex_normals, axis=1), 1, atol=2e-3) for g in scene.geometry.values())
    blade_mesh = next(g for name, g in asset.geometry.items() if name.startswith("blade_airfoil"))
    radius = float(np.linalg.norm(blade_mesh.vertices[:, 1:], axis=1).max())
    assert abs(radius * 2 - spec["verified_operator_spec"]["rotor_diameter_m"]) < 1e-4
    assert len(blade_mesh.vertices) > 500 and blade_mesh.is_watertight
    assert len([n for n in asset.graph.nodes if n.startswith("TEMPLATE_blade_")]) == 3
    assert asset.bounds[0][1] < -spec["estimated"]["sea_depth_m"] + .1
    tree = glb_tree(output / "scene.glb")
    nodes = {n["name"]: n for n in tree["nodes"]}
    assert len(nodes) == len(tree["nodes"]), "semantic node names must be unique"
    assert manifest["model"]["mesh_parts"] < 50
    origin = manifest["coordinateFrame"]["origin_easting_northing"]
    west, south, east, north = manifest["terrain"]["bbox_lon_lat"]
    for f in manifest["facilities"]:
        source = next(row for row in source_features if row["id"] == f["id"])
        assert f["coordinates"] == source["geometry"]["coordinates"]
        assert west < f["coordinates"][0] < east and south < f["coordinates"][1] < north
        matrix, _ = scene.graph[f["node"]]
        assert np.allclose(matrix[:3, 3], f["position"], atol=.002)
        lon, lat = project_crs("EPSG:32652", "EPSG:4326", [matrix[0, 3] + origin[0]], [origin[1] - matrix[2, 3]])
        assert np.allclose([lon[0], lat[0]], f["coordinates"], atol=2e-8, rtol=0)
        rotor = nodes[f["rotor_node"]]
        child_names = {tree["nodes"][i]["name"] for i in rotor["children"]}
        assert {f"{f['node']}_blade_{i}" for i in (1, 2, 3)} <= child_names
        assert f"{f['node']}_tower" in nodes and f"{f['node']}_foundation" in nodes
        root_matrix, _ = scene.graph[f["node"]]
        rotor_matrix, _ = scene.graph[f["rotor_node"]]
        rotor_local = np.linalg.inv(root_matrix) @ rotor_matrix
        assert np.allclose(rotor_local[:3, 3], [spec["estimated"]["rotor_offset_x_m"], spec["estimated"]["hub_height_m"], 0])
    return {"status": "PASS", "facilities": 10, "reopened_meshes": len(scene.geometry),
            "rotor_diameter_m": radius * 2, "checks": ["GLB binary/header and reopen", "finite geometry/unit normals", "closed curved blade mesh and rotor diameter", "three independent rotor child blades per source turbine", "ten source-coordinate placements and inverse projection", "native terrain contains all ten positions", "rotor centre at specified hub height", "unique semantic hierarchy and file hashes"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=Path("renderers/twin/spec.json"))
    parser.add_argument("--output", type=Path, default=Path("var/rendering/twin"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    base = Path(".worktrees/data")
    source = base / "var/data/geography/source-03e02ef"
    geo_manifest = json.loads((source / "manifest.json").read_text())
    plants_path = source / "power_plant.geojsonl"
    assert sha(plants_path) == next(x["sha256"] for x in geo_manifest["datasets"] if x["file"] == plants_path.name)
    ids = {f"hub:power_plant:{i}" for i in range(5722, 5732)}
    with plants_path.open() as stream:
        facilities_source = [f for line in stream if (f := json.loads(line))["id"] in ids]
    assert len(facilities_source) == 10 and len({f["id"] for f in facilities_source}) == 10
    if args.self_test:
        print(json.dumps(verify(args.output, spec, facilities_source), indent=2))
        return
    e = spec["estimated"]
    assert e["hub_height_m"] > spec["verified_operator_spec"]["rotor_diameter_m"] / 2 + e["deck_height_m"]
    assert all(math.isfinite(value) and value > 0 for key, v in e.items()
               if key.endswith("_m") and key not in ("rotor_offset_x_m", "ocean_level_egm2008_m")
               for value in (v if isinstance(v, list) else [v]))
    prototype_scene, model_stats = prototype(spec)
    asset = trimesh.Scene(base_frame="world")
    asset.geometry.update(prototype_scene.geometry)
    instance(asset, prototype_scene, "TEMPLATE", np.eye(4))
    scene = trimesh.Scene(base_frame="world")
    scene.geometry.update(prototype_scene.geometry)
    origin_lon, origin_lat = spec["coordinate_frame"]["origin_lon_lat"]
    origin_e, origin_n = project_crs("EPSG:4326", "EPSG:32652", [origin_lon], [origin_lat])
    entries = []
    for f in facilities_source:
        lon, lat = f["geometry"]["coordinates"]
        east, north = project_crs("EPSG:4326", "EPSG:32652", [lon], [lat])
        position = [east[0] - origin_e[0], e["ocean_level_egm2008_m"], origin_n[0] - north[0]]
        node = f"T{f['properties']['source_id']}"
        instance(scene, prototype_scene, node, translation(*position) @ rotation(e["yaw_deg"], (0, 1, 0)))
        entries.append({"id": f["id"], "node": node, "rotor_node": f"{node}_rotor", "rotor_axis": "x",
                        "coordinates": [lon, lat], "position": position, "name": f"탐라 해상풍력 {len(entries) + 1:02d}",
                        "name_status": "display sequence, not verified operator turbine number", "source_properties": f["properties"],
                        "dimensions_estimated": True, "model_matching": "tentative", "telemetry": None})
    terrain_manifest = json.loads((base / "data/terrain/manifest.json").read_text())
    dem_path = base / "var/data/terrain/Copernicus_DSM_COG_10_N33_00_E126_00_DEM.tif"
    wbm_path = base / "var/data/terrain/Copernicus_DSM_COG_10_N33_00_E126_00_WBM.tif"
    for path in (dem_path, wbm_path):
        assert sha(path) == next(a["sha256"] for a in terrain_manifest["artifacts"] if Path(a["path"]).name == path.name)
    with rasterio.open(dem_path) as dem, rasterio.open(wbm_path) as wbm:
        desired = from_bounds(*spec["terrain_bbox_lon_lat"], dem.transform)
        left, top = math.floor(desired.col_off), math.floor(desired.row_off)
        window = Window(left, top, math.ceil(desired.col_off + desired.width) - left,
                        math.ceil(desired.row_off + desired.height) - top)
        heights, water = dem.read(1, window=window), wbm.read(1, window=window)
        affine = dem.window_transform(window)
        bounds = list(rasterio.windows.bounds(window, dem.transform))
        rows, cols = np.indices(heights.shape)
        lon, lat = rasterio.transform.xy(affine, rows.ravel(), cols.ravel())
        east, north = project_crs("EPSG:4326", "EPSG:32652", lon, lat)
        vertices = np.column_stack((np.asarray(east) - origin_e[0], heights.ravel(), origin_n[0] - np.asarray(north)))
        terrain_faces, shore_faces, ocean_faces = [], [], []
        height, width = heights.shape
        for row in range(height - 1):
            for col in range(width - 1):
                i = row * width + col
                group = ocean_faces if water[row:row+2, col:col+2].mean() >= .75 else (shore_faces if heights[row:row+2, col:col+2].mean() < 2.5 else terrain_faces)
                group.extend([[i, i + width, i + 1], [i + 1, i + width, i + width + 1]])
        for name, faces, color, rough in [("terrain_land", terrain_faces, [105, 118, 64, 255], 1.0),
                                         ("shore_basalt", shore_faces, [78, 73, 57, 255], .94),
                                         ("ocean_surface", ocean_faces, [24, 77, 111, 255], .24)]:
            mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
            mesh.remove_unreferenced_vertices()
            normals(mesh)
            mesh.visual = TextureVisuals(material=PBRMaterial(name=name, baseColorFactor=color, roughnessFactor=rough, metallicFactor=.05))
            scene.add_geometry(mesh, geom_name=name, node_name=name)
    photo_manifest = json.loads((base / "data/photos/manifest.json").read_text())
    photo = next(p for p in photo_manifest["photos"] if p["photo_id"] == "commons-sinchang-09")
    drawing = Path("var/research/drawings/tamra-operator-elevation.png")
    assert sha(base / photo["local_path"]) == photo["sha256"]
    first = np.asarray(entries[0]["position"])
    camera = lambda offset, target: {"position": (first + offset).tolist(), "target": (first + target).tolist(), "fov": 48, "near": .15, "far": 18000}
    args.output.mkdir(parents=True, exist_ok=True)
    for filename, value in [("turbine.glb", asset), ("scene.glb", scene)]:
        (args.output / filename).write_bytes(trimesh.exchange.gltf.export_glb(value, include_normals=True))
    manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "units": "m",
                "coordinateFrame": {**spec["coordinate_frame"], "origin_easting_northing": [origin_e[0], origin_n[0]], "vertical_origin_m": 0},
                "assetassumptions": {"user_authorized_estimation": True, "verified_operator_spec": spec["verified_operator_spec"],
                                     "estimated": e, "limits": spec["claim_limits"], "calibration_file": str(args.spec), "calibration_sha256": sha(args.spec)},
                "facilities": entries, "cameras": {"overview": camera([-290, 140, 260], [240, 52, -160]),
                                                      "inspect": camera([-115, 102, 92], [0, 62, 0]),
                                                      "array": camera([-1300, 1000, 1400], [1000, 35, -730])},
                "model": model_stats, "terrain": {"bbox_lon_lat": bounds, "native_shape": list(heights.shape),
                                                  "height_range_m": [float(heights.min()), float(heights.max())], "vertical_datum": "EGM2008",
                                                  "materials": "synthetic brown/green/blue; not surveyed surface colours", "bathymetry_available": False},
                "sources": [{"kind": "GIS_positions", "path": str(plants_path), "sha256": sha(plants_path), "facility_model_match_verified": False},
                            {"kind": "operator_specification", "url": spec["verified_operator_spec"]["source"], "confirmed_fields": ["rotor_diameter_m", "rated_power_mw"]},
                            {"kind": "operator_schematic_reference_only", "path": str(drawing), "sha256": sha(drawing), "license": "not verified; no image texture embedded"},
                            {"kind": "photo_informed_shape_reference", "path": str(base / photo["local_path"]), "sha256": photo["sha256"], "source_page": photo["source_page"], "license": photo["license"], "attribution": photo["attribution"], "image_pixels_in_model": False},
                            {"kind": "DSM", "path": str(dem_path), "sha256": sha(dem_path), "notice": terrain_manifest["license"]["required_modified_notice"]},
                            {"kind": "water_mask", "path": str(wbm_path), "sha256": sha(wbm_path)}],
                "files": [{"path": name, "bytes": (args.output / name).stat().st_size, "sha256": sha(args.output / name)} for name in ("turbine.glb", "scene.glb")],
                "status": "estimated_photo_informed_reconstruction", "surveyed_asset": False,
                "photo_texture_copied": False, "source_photo_license": "CC-BY-SA-4.0", "visual_geometry_license": "CC-BY-SA-4.0",
                "license_note": "Model geometry is shared under CC BY-SA 4.0 conservatively for photo-informed adaptation; retain credit and change notice. Source DSM and GIS retain their own conditions."}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    credit = photo["attribution"] + "\nChanges: photo-informed manually parameterized estimated geometry; no image pixels used as textures.\nCC BY-SA 4.0: https://creativecommons.org/licenses/by-sa/4.0\n" + terrain_manifest["license"]["required_modified_notice"] + "\n" + terrain_manifest["license"]["required_liability_notice"] + "\nGIS source: Energy-hub public.power_plant; source dataset terms still apply.\nOperator schematic was inspected as reference, not reproduced in textures.\n"
    (args.output / "CREDITS.txt").write_text(credit)
    result = verify(args.output, spec, facilities_source)
    (args.output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "files": manifest["files"], "verification": result}, indent=2))


if __name__ == "__main__":
    main()
