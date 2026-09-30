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
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
import json
import math
from pathlib import Path
import random
import struct
from tempfile import TemporaryDirectory

import numpy as np
import shapely
import trimesh
from PIL import Image, ImageDraw
from rasterio.warp import transform as project_crs
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from build import normals, sha, translation
from landmarks import add_landmarks
from roads import add_roads
from vegetation import add_vegetation
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


def surface_height(scene: trimesh.Scene):
    """Return height(x, z) of the displayed terrain/sea triangles (highest where they overlap)."""
    tris = []
    for name, mesh in scene.geometry.items():
        if name.startswith(("ocean_surface", "terrain_landcover_")):
            transform = scene.graph[scene.graph.geometry_nodes[name][0]][0]
            tris.append(trimesh.transform_points(mesh.vertices, transform)[mesh.faces])
    tris = np.concatenate(tris)
    tree = shapely.STRtree(shapely.polygons(tris[:, :, [0, 2]]))

    def height(x, z):
        x, z = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(z, float))
        point_index, tri_index = tree.query(shapely.points(x, z), predicate="intersects")
        a, b, c = (tris[tri_index, k] for k in range(3))
        d = (b[:, 2] - c[:, 2]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 2] - c[:, 2])
        u = ((b[:, 2] - c[:, 2]) * (x[point_index] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (z[point_index] - c[:, 2])) / d
        v = ((c[:, 2] - a[:, 2]) * (x[point_index] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (z[point_index] - c[:, 2])) / d
        y = np.full(len(x), -np.inf)
        np.maximum.at(y, point_index, u * a[:, 1] + v * b[:, 1] + (1 - u - v) * c[:, 1])
        assert np.isfinite(y).all(), "point outside displayed terrain"
        return y
    return height


def mercator(vertices: np.ndarray, frame: dict) -> tuple[np.ndarray, np.ndarray]:
    origin = frame["origin_easting_northing"]
    mx, my = project_crs(frame["horizontal_crs"], "EPSG:3857", vertices[:, 0] + origin[0], origin[1] - vertices[:, 2])
    return np.asarray(mx), np.asarray(my)


def tile_box(tile: dict):
    """EPSG:3857 footprint of one WMTS tile record {z, x, y}."""
    half = math.pi * 6378137
    step = 2 * half / 2 ** tile["z"]
    return shapely.box(tile["x"] * step - half, half - (tile["y"] + 1) * step, (tile["x"] + 1) * step - half, half - tile["y"] * step)


def imagery_material(image_path: Path, name: str) -> PBRMaterial:
    with Image.open(image_path) as source:
        image = source.copy()
        image.format = source.format  # trimesh preserves JPEG only when the format is retained.
    return PBRMaterial(name=name, baseColorTexture=image, metallicFactor=0, roughnessFactor=1)


def apply_imagery(scene: trimesh.Scene, image_path: Path, metadata: dict, frame: dict,
                  detail_path: Path | None = None, detail_metadata: dict | None = None) -> int:
    """Texture the existing DSM vertices with their actual map positions.

    Land and sea triangles wholly inside the detail mosaic and clear of its failed tiles move, unchanged, to
    `<name>_detail` meshes with the detail texture. Returns the number of moved triangles.
    """
    assert metadata["crs"] == "EPSG:3857"
    west, south, east, north = map(float, metadata["bounds"])
    assert west < east and south < north
    material = imagery_material(image_path, "georeferenced_imagery")
    if detail_path:
        assert detail_metadata["crs"] == "EPSG:3857"
        detail_material = imagery_material(detail_path, "georeferenced_imagery_z17")
        failed = shapely.union_all([tile_box(t) for t in detail_metadata["failed_tiles"]])
    moved = 0
    for name in list(scene.geometry):
        if name != "ocean_surface" and not name.startswith("terrain_landcover_"):
            continue
        mesh = scene.geometry[name].copy()
        mx, my = mercator(mesh.vertices, frame)
        uv = np.column_stack(((mx - west) / (east - west), (my - south) / (north - south)))
        assert np.isfinite(uv).all() and ((uv >= -0.02) & (uv <= 1.02)).all(), name
        mesh.visual = TextureVisuals(uv=uv, material=material)
        normals(mesh)
        scene.geometry[name] = mesh
        if not detail_path:
            continue
        w, s, e, n = detail_metadata["bounds"]
        detail_uv = np.column_stack(((mx - w) / (e - w), (my - s) / (n - s)))
        # ponytail: whole triangle inside the tile-aligned mosaic, not centroid-in-AOI, so detail UVs stay in [0, 1]; edge triangles keep z15.
        selected = ((detail_uv >= 0) & (detail_uv <= 1)).all(axis=1)[mesh.faces].all(axis=1)
        fx, fy = mx[mesh.faces], my[mesh.faces]
        selected &= ~shapely.intersects(failed, shapely.box(fx.min(1), fy.min(1), fx.max(1), fy.max(1)))
        if not selected.any():
            continue
        assert not selected.all(), f"{name} lies wholly in the detail area"  # ponytail: never seen; move the whole mesh if it happens
        for part, mask, part_uv, part_material in ((name, ~selected, uv, material),
                                                   (name + "_detail", selected, detail_uv, detail_material)):
            used = np.unique(mesh.faces[mask])
            piece = trimesh.Trimesh(mesh.vertices[used], np.searchsorted(used, mesh.faces[mask]), process=False)
            piece.visual = TextureVisuals(uv=part_uv[used], material=part_material)
            piece.vertex_normals = mesh.vertex_normals[used]  # whole-mesh normals: no shading seam at the split
            if part == name:
                scene.geometry[name] = piece
            else:
                scene.add_geometry(piece, geom_name=part, node_name=part,
                                   transform=scene.graph[scene.graph.geometry_nodes[name][0]][0])
        moved += int(selected.sum())
    return moved


def source_path(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def footprint_polygons(record: dict) -> list[Polygon]:
    return [Polygon(np.asarray(rings[0])[:, [0, 2]],
                    [np.asarray(ring)[:, [0, 2]] for ring in rings[1:]]) for rings in record["polygons"]]


PITCH = {"gable_house": 20, "gable_shed": 10}


def building_rule(props: dict, parts: list[Polygon]) -> tuple[str, float, str]:
    """Height source (provider > floors x 3 m > one 3.5 m storey) and roof form from footprint attributes."""
    height = float(props.get("height") or 0)
    floors = int(props.get("grnd_flr") or 0)
    status, walls = (("provider", height) if math.isfinite(height) and height > 0 else
                     ("floors_estimated", floors * 3.0) if floors > 0 else ("default_estimated", 3.5))
    union = shapely.union_all(parts)
    rectangular = len(parts) == 1 and not parts[0].interiors and union.area / union.minimum_rotated_rectangle.area >= .85
    use, strct = props.get("usability", ""), props.get("strct_cd", "")
    low = strct in ("11", "12", "13", "32", "33") or (not strct and max(floors, 1) == 1)
    if rectangular and low and use in ("01000", "", None):
        return status, walls, "gable_house"
    if rectangular and low and use in ("18000", "21000"):
        return status, walls, "gable_shed"
    return status, walls, "flat_parapet"


def sides(polygon: Polygon, y0: float, y1: float, center: np.ndarray) -> tuple[list, list]:
    """Vertical quads on every oriented ring; u is metres along the ring."""
    quads, along = [], []
    for ring in [polygon.exterior, *polygon.interiors]:
        points, start = np.asarray(ring.coords) - center, 0.0
        for a, b in zip(points[:-1], points[1:]):
            length = float(np.linalg.norm(b - a))
            if length == 0:
                continue
            quads.append([[a[0], y0, a[1]], [b[0], y0, b[1]], [b[0], y1, b[1]], [a[0], y1, a[1]]])
            along += [start, start + length, start + length, start]
            start += length
    return quads, along


def cap(polygon: Polygon, y: float, center: np.ndarray) -> list:
    """Upward constrained Delaunay triangles, courtyard holes kept."""
    triangles = shapely.constrained_delaunay_triangles(polygon)
    assert np.isclose(sum(t.area for t in triangles.geoms), polygon.area, atol=1e-6)
    result = []
    for triangle in triangles.geoms:
        points = np.asarray(triangle.exterior.coords)[:3] - center
        vertices = np.column_stack((points[:, 0], np.full(3, y), points[:, 1]))
        result.append(vertices if np.cross(vertices[1] - vertices[0], vertices[2] - vertices[0])[1] > 0 else vertices[::-1])
    return result


def to_mesh(quads: list, triangles: list) -> trimesh.Trimesh:
    faces = [(np.arange(len(quads))[:, None, None] * 4 + np.array([[0, 3, 2], [0, 2, 1]])).reshape(-1, 3),
             np.arange(len(triangles) * 3).reshape(-1, 3) + len(quads) * 4]
    return trimesh.Trimesh(vertices=np.concatenate([np.reshape(quads, (-1, 3)), np.reshape(triangles, (-1, 3))]),
                           faces=np.concatenate(faces), process=False)


def building_meshes(polygons: list[Polygon], walls: float, center: np.ndarray, rule: str,
                    storey: float) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    """Exact footprint walls to the eave line, then a gable over the minimum rectangle or a flat roof with a parapet.

    Wall UV is in metres: u = along / 4 m, v = height / 4 storeys, so each storey is one window row.
    """
    assert np.isfinite(walls) and walls > 0 and storey > 0
    wall_quads, wall_u, wall_triangles, triangle_u, roof_quads, roof_triangles = [], [], [], [], [], []
    for polygon in polygons:
        assert polygon.is_valid and polygon.area > 0
        polygon = orient(polygon, sign=1)
        quads, along = sides(polygon, 0, walls, center)
        wall_quads += quads
        wall_u += along
        if rule == "flat_parapet":
            roof_triangles += cap(polygon, walls, center)
            for part in shapely.get_parts(polygon.difference(polygon.buffer(-.2, join_style="mitre"))):
                part = orient(part, sign=1)  # 0.2 m thick, 0.6 m high parapet along every ring
                roof_quads += sides(part, walls, walls + .6, center)[0]
                roof_triangles += cap(part, walls + .6, center)
    if rule != "flat_parapet":
        rect = orient(shapely.union_all(polygons).minimum_rotated_rectangle, sign=1)
        corners = np.asarray(rect.exterior.coords)[:4] - center
        edges = np.roll(corners, -1, axis=0) - corners
        lengths = np.linalg.norm(edges, axis=1)
        i = int(np.argmax(lengths))  # ridge along the long side
        axis, across = edges[i] / lengths[i], edges[(i + 1) % 4] / lengths[(i + 1) % 4]
        half_long, half_short = lengths[i] / 2 + .4, lengths[(i + 1) % 4] / 2 + .4  # 0.4 m eaves
        slope = np.tan(np.radians(PITCH[rule]))
        plate, ridge = walls + .4 * slope, walls + half_short * slope
        mid = corners.mean(axis=0)
        # Close wall top to roof underside on the rectangle, then the two gable-end triangles.
        quads, along = sides(rect, walls, plate, center)
        wall_quads += quads
        wall_u += along
        starts = np.concatenate(([0], np.cumsum(lengths)))
        for j in ((i + 1) % 4, (i + 3) % 4):
            a, b = corners[j], corners[(j + 1) % 4]
            apex = (a + b) / 2
            vertices = np.array([[a[0], plate, a[1]], [b[0], plate, b[1]], [apex[0], ridge, apex[1]]])
            u = [starts[j], starts[j] + lengths[j], starts[j] + lengths[j] / 2]
            if np.dot(np.cross(vertices[1] - vertices[0], vertices[2] - vertices[0])[[0, 2]], apex - mid) < 0:
                vertices, u = vertices[::-1], u[::-1]
            wall_triangles.append(vertices)
            triangle_u += u
        for side in (1, -1):
            eave0, eave1 = mid - axis * half_long + side * across * half_short, mid + axis * half_long + side * across * half_short
            ridge0, ridge1 = mid - axis * half_long, mid + axis * half_long
            for triangle in (((eave0, walls), (eave1, walls), (ridge1, ridge)), ((eave0, walls), (ridge1, ridge), (ridge0, ridge))):
                vertices = np.array([[p[0], y, p[1]] for p, y in triangle])
                roof_triangles.append(vertices if np.cross(vertices[1] - vertices[0], vertices[2] - vertices[0])[1] > 0 else vertices[::-1])
    wall_mesh = to_mesh(wall_quads, wall_triangles)
    wall_mesh.visual = TextureVisuals(uv=np.column_stack((np.asarray(wall_u + triangle_u) / 4,
                                                         wall_mesh.vertices[:, 1] / (4 * storey))))
    return normals(wall_mesh), normals(to_mesh(roof_quads, roof_triangles))


def facade(kind: str) -> Image.Image:
    """512 px = 4 m wide x 4 storeys (128 px rows, ground floor at the image bottom where v = 0)."""
    image = Image.new("RGB", (512, 512), {"plaster": (224, 219, 206), "metal": (188, 196, 200), "basalt": (74, 74, 76)}[kind])
    draw, rng = ImageDraw.Draw(image), random.Random(13)
    if kind == "metal":
        for x in range(0, 512, 16):
            draw.rectangle([x, 0, x + 5, 511], fill=(160, 170, 176))
    if kind == "basalt":
        for top in range(0, 512, 26):
            x = -rng.randrange(40)
            while x < 512:
                w, shade = rng.randrange(34, 70), rng.randrange(52, 100)
                draw.rounded_rectangle([x + 2, top + 2, x + w - 2, top + 24], 7, fill=(shade, shade, shade + 3))
                x += w
    frame, glass, door = (240, 238, 232), (58, 72, 84), (104, 76, 52)
    for row in range(4):
        base = 511 - row * 128  # storey floor line in image rows
        draw.rectangle([0, base - 127, 511, base - 124], fill=(150, 146, 138) if kind != "basalt" else (60, 60, 62))
        if kind == "metal":
            continue
        windows = [(260, 460)] if row == 0 else [(51, 205), (307, 461)]
        if kind == "basalt":
            windows = [(300, 400)]
        for x0, x1 in windows:  # sill ~0.9 m, head ~2.2 m of a 3 m storey
            draw.rectangle([x0, base - 94, x1, base - 38], fill=frame)
            draw.rectangle([x0 + 5, base - 89, x1 - 5, base - 43], fill=glass)
            draw.line([(x0 + x1) // 2, base - 89, (x0 + x1) // 2, base - 43], fill=frame, width=4)
        if row == 0:  # ground-floor door at one side
            draw.rectangle([40, base - 90, 155, base], fill=frame)
            draw.rectangle([46, base - 85, 149, base], fill=door)
    return image


def add_buildings(scene: trimesh.Scene, frame: dict) -> tuple[dict, dict]:
    path = ROOT / "var/rendering/site/scene.json"
    site = json.loads(path.read_text())
    assert all(site["projection"][key] == frame[key] for key in
               ("horizontal_crs", "vertical_crs", "origin_easting_northing", "axes", "scale"))
    provider = next(s for s in site["metadata"]["sources"] if s.get("file") == "building_info.geojsonl")
    provider_path = ROOT / ".worktrees/data/var/data/geography/source-03e02ef" / provider["file"]
    assert sha(provider_path) == provider["sha256"]
    roofs = ROOT / "var/rendering/roofs"
    atlas = json.loads((roofs / "atlas.json").read_text())
    assert atlas["zoom"] == 19 and atlas["crs"] == "EPSG:3857" and sha(roofs / "atlas.jpg") == atlas["sha256"]
    sources = [{**provider, "path": source_path(provider_path)},
               {"path": source_path(path), "sha256": sha(path), "kind": "prepared_clipped_footprints"},
               {"path": source_path(roofs / "atlas.jpg"), "sha256": atlas["sha256"], "kind": "vworld_z19_roof_atlas"},
               {"path": source_path(roofs / "atlas.json"), "sha256": sha(roofs / "atlas.json"), "kind": "vworld_z19_roof_atlas_index"}]
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
    with Image.open(roofs / "atlas.jpg") as source:
        atlas_image = source.copy()
        atlas_image.format = source.format  # keep JPEG in the GLB
    roof_imagery = PBRMaterial(name="roof_imagery", baseColorTexture=atlas_image, metallicFactor=0,
                               roughnessFactor=.9, doubleSided=True)
    neutral_roof = PBRMaterial(name="building_neutral_roof", baseColorFactor=[133, 139, 136, 255],
                               metallicFactor=0, roughnessFactor=.9, doubleSided=True)
    wall_materials = {kind: PBRMaterial(name=f"building_wall_{kind}", baseColorTexture=facade(kind), metallicFactor=0,
                                        roughnessFactor=.6 if kind == "metal" else .9) for kind in ("plaster", "metal", "basalt")}
    origin = frame["origin_easting_northing"]
    records, clamped = [], 0
    for building in site["buildings"]:
        props = building["source_properties"]
        parts = polygons[building["id"]]
        status, walls, rule = building_rule(props, parts)
        floors = int(props.get("grnd_flr") or 0)
        floors_used = floors if floors > 0 else max(1, round(walls / 3)) if status == "provider" else 1
        storey = walls / floors_used
        # ponytail: provider height is used as the eave (wall) height because the source does not define it; replace with photogrammetry (②).
        center = np.asarray(shapely.union_all(parts).representative_point().coords[0])
        local = samples[np.sum((samples[:, [0, 2]] - center) ** 2, axis=1) <= 60 ** 2]
        assert len(local) >= 3, f"insufficient local ground samples: {building['id']}"
        # ponytail: local low DSM percentile is not a DTM; replace with surveyed ground when available.
        ground = float(np.percentile(local[:, 1], 20))
        node = "building_" + building["id"].replace(".", "_").replace(":", "_")
        position = [float(center[0]), ground, float(center[1])]
        scene.graph.update(frame_to=node, frame_from="world", matrix=translation(*position))
        wall_mesh, roof_mesh = building_meshes(parts, walls, center, rule, storey)
        wall_kind = "basalt" if props.get("strct_cd") == "13" else "metal" if props.get("usability") in ("18000", "21000") else "plaster"
        wall_mesh.visual.material = wall_materials[wall_kind]
        entry = atlas["entries"].get(building["id"])
        if entry:
            world = roof_mesh.vertices[:, [0, 2]] + center
            mx, my = project_crs(frame["horizontal_crs"], "EPSG:3857", world[:, 0] + origin[0], origin[1] - world[:, 1])
            (x0, y0, x1, y1), (west, south, east, north) = entry["atlas_px"], entry["mercator_bbox"]
            px = x0 + (np.asarray(mx) - west) / (east - west) * (x1 - x0)
            py = y0 + (north - np.asarray(my)) / (north - south) * (y1 - y0)
            # ponytail: eave corners beyond the 1 m crop margin are clamped to the crop edge, never into a neighbour's cell.
            clipped = np.clip(px, x0 + .5, x1 - .5), np.clip(py, y0 + .5, y1 - .5)
            clamped += int(np.count_nonzero((np.abs(clipped[0] - px) > 1) | (np.abs(clipped[1] - py) > 1)))
            roof_mesh.visual = TextureVisuals(uv=np.column_stack((clipped[0] / atlas["width"], 1 - clipped[1] / atlas["height"])),
                                              material=roof_imagery)
        else:
            roof_mesh.visual = TextureVisuals(material=neutral_roof)
        for suffix, mesh in (("walls", wall_mesh), ("roof", roof_mesh)):
            scene.add_geometry(mesh, geom_name=f"{node}_{suffix}", node_name=f"{node}_{suffix}", parent_node_name=node)
        records.append({"id": building["id"], "node": node, "position": position, "height_status": status,
                        "walls_m": walls, "roof_top_m": float(roof_mesh.bounds[1, 1]), "floors_used": floors_used,
                        "storey_m": storey, "roof_rule": rule, "roof_texture": "vworld_z19" if entry else "unavailable",
                        "wall_texture": wall_kind, "ground_m": ground, "ground_sample_count": len(local),
                        "source_dsm_anchor_m": building["base_height_m"],
                        "footprint_area_m2": sum(p.area for p in parts), "polygon_count": len(parts),
                        "hole_count": sum(len(p.interiors) for p in parts)})
    centers = np.asarray([r["position"] for r in records])
    nearby = np.sum((centers[:, None, [0, 2]] - centers[None, :, [0, 2]]) ** 2, axis=-1) <= 100 ** 2
    focus = centers[np.argmax(nearby.sum(axis=1))]
    target = (focus + [0, 4, 0]).tolist()
    camera = {"position": (focus + [-100, 95, 130]).tolist(), "target": target, "fov": 48, "near": .15, "far": 70000}
    return {"count": len(records), "source_count": len(site["buildings"]),
            "display_label": "건물 · 위치·윤곽·지붕영상 실제 / 형태·외벽·일부 높이 추정", "default_visible": True,
            "counts": {key: dict(Counter(r[key] for r in records)) for key in ("height_status", "roof_rule", "roof_texture", "wall_texture")},
            "roof_atlas": {key: atlas[key] for key in ("zoom", "width", "height", "tile_count", "failed_tile_count", "acquired_at", "attribution")}
            | {"uv_clamped_vertex_count": clamped},
            "bbox_lon_lat": site["aoi_bbox"], "sources": sources, "records": records,
            "height_policy": "Eave (wall) height: finite positive VWorld LT_C_BLDGINFO provider height (provider); else ground floors x 3.0 m (floors_estimated); else one 3.5 m storey (default_estimated).",
            "roof_policy": "Gable along the minimum-rotated-rectangle long axis with 0.4 m eaves (20 deg houses, 10 deg warehouses/animal-plant buildings) only for single-ring footprints filling >= 85% of that rectangle with masonry/light-steel structure or no attributes and one storey; otherwise flat roof with a 0.2 m x 0.6 m parapet.",
            "ground_policy": "20th percentile of native Copernicus DSM land sample centres within 60 m of each footprint representative point, excluding centres covered by any of the 2661 source footprints; constant base per building.",
            "materials": "Roofs: one shared VWorld Satellite z19 atlas projected top-down in EPSG:3857 (neutral grey when no complete crop). Walls: procedural 512 px plaster-with-windows, corrugated metal or basalt textures in metre UVs (4 m x 4 storeys).",
            "limits": ["Footprint position/outline and roof imagery are actual; roof shape, facade colour, windows/doors and non-provider heights are estimates.",
                       "Provider height meaning (eave or ridge) is undefined in the source; it is used as the eave height.",
                       "Roof imagery is a top-down orthophoto crop; leaning or tall objects and neighbouring ground can appear on roof edges.",
                       "DSM includes roofs and vegetation. Local low-percentile ground is approximate, not a DTM; some walls may intersect terrain.",
                       "Bounded Sinchang coverage only; source footprint overlaps remain unresolved."]}, camera


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
                        if name.startswith(("ocean_surface", "terrain_landcover_"))])
    origin = frame["origin_easting_northing"]
    lon, lat = project_crs(frame["horizontal_crs"], "EPSG:4326",
                           points[:, 0] + origin[0], origin[1] - points[:, 2])
    region = ((np.asarray(lon) >= 126.35) & (np.asarray(lon) <= 126.365) &
              (np.asarray(lat) >= 33.36) & (np.asarray(lat) <= 33.372))
    assert region.any()
    selected = np.flatnonzero(region)[np.argmax(points[region, 1])]
    return points[selected].tolist(), [float(lon[selected]), float(lat[selected])]


def build(output: Path, imagery_path: Path, imagery_metadata_path: Path,
          placements_path: Path | None = None, detail_imagery_path: Path | None = None) -> dict:
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
    # Required like the roof atlas: `prepare_imagery.py --terrain-detail` writes it.
    detail_dir = detail_imagery_path or ROOT / "var/rendering/imagery-detail"
    detail_metadata = json.loads((detail_dir / "manifest.json").read_text())
    assert sha(detail_dir / "texture.jpg") == detail_metadata["sha256"]
    moved = apply_imagery(scene, imagery_path, imagery_metadata, grid["coordinateFrame"],
                          detail_dir / "texture.jpg", detail_metadata)
    sea = sea_metadata(scene, grid)
    buildings, building_camera = add_buildings(scene, grid["coordinateFrame"])
    landmarks = add_landmarks(scene, grid["coordinateFrame"])
    landmark_by_id = {r["id"]: r for r in landmarks["records"]}
    height_at = surface_height(scene)
    roads = add_roads(scene, grid["coordinateFrame"], height_at)
    vegetation = add_vegetation(scene, grid["coordinateFrame"], height_at)
    grid_assumptions, wind_assumptions = local_assumptions(grid, wind)
    terrain = {**grid["terrain"], "materials": "Georeferenced VWorld Satellite JPEG mapped to the unchanged DSM and WBM ocean mesh via EPSG:3857 UV coordinates; "
               "land and coastal sea triangles inside the Sinchang z17 mosaic use it (~1 m/px) as *_detail meshes"}
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
        **{name: {"position": (np.asarray(landmark_by_id[i]["position"]) + offset).tolist(),
                  "target": (np.asarray(landmark_by_id[i]["position"]) + [0, lift, 0]).tolist(), "fov": 48, "near": .15, "far": 70000}
           for name, i, offset, lift in (("lighthouse", "sinchang_white_lighthouse", [38, 20, 48], 7),
                                         ("pavilion", "singyemul_park_pavilion", [40, 22, 55], 3)) if i in landmark_by_id},
        "sea": {"position": [-1100, 230, -600], "target": [0, 25, -1550], "fov": 48, "near": .15, "far": 70000},
        "terrain": {"position": [peak[0]-1200, peak[1]+700, peak[2]+1500],
                    "target": [peak[0], peak[1]-100, peak[2]], "fov": 48, "near": .5, "far": 70000},
    }
    manifest = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "units": "m",
        "coordinateFrame": grid["coordinateFrame"], "facilities": facilities, "routes": routes,
        "coast": coast, "terrain": terrain, "buildings": buildings, "landmarks": landmarks, "roads": roads, "vegetation": vegetation, "sea": sea, "physical_line": grid["physical_line"],
        "cameras": cameras, "assetassumptions": grid_assumptions,
        "wind_assetassumptions": wind_assumptions,
        "source_assetassumptions": {"grid": grid["assetassumptions"], "wind": wind["assetassumptions"]},
        "sources": {"wind": wind["sources"], "grid": grid["sources"]},
        "source_assets": {kind: {name: sha(directory / name) for name in ("scene.glb", "manifest.json")}
                          for kind, directory in (("wind", wind_dir), ("grid", grid_dir))},
        "imagery": {"path": source_path(imagery_path), "sha256": sha(imagery_path),
                    "metadata_path": source_path(imagery_metadata_path),
                    "metadata_sha256": sha(imagery_metadata_path), **imagery_metadata},
        "imagery_detail": {"path": source_path(detail_dir / "texture.jpg"), "sha256": sha(detail_dir / "texture.jpg"),
                           "metadata_path": source_path(detail_dir / "manifest.json"),
                           "metadata_sha256": sha(detail_dir / "manifest.json"), **detail_metadata,
                           "triangle_count": moved, "material": "georeferenced_imagery_z17",
                           "policy": "Land and sea triangles wholly inside the tile-aligned z17 mosaic and not touching a failed z17 tile move unchanged to <mesh>_detail meshes (terrain_landcover_<class>_detail, ocean_surface_detail); all others keep the z15 mosaic."},
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
               f"Sinchang site terrain: {detail_metadata['source']} z{detail_metadata['zoom']} mosaic (~1 m/px, same provider and terms) on land and sea triangles inside it; "
               "triangles touching failed tiles keep the base mosaic.\n" +
               "\n--- Building source ---\nVWorld LT_C_BLDGINFO — https://api.vworld.kr/req/data\n" +
               "VWorld provider terms; source attribution required. Prepared Sinchang footprints (all 2661); provider heights where positive, otherwise floors x 3 m or one 3.5 m storey (recorded per building).\n" +
               "Roof imagery: VWorld Satellite WMTS z19 crops (공간정보 오픈플랫폼(브이월드) / 국토교통부), local preview cache. Roof forms, parapets, facade textures and windows are procedural estimates, not a textured 3D building reconstruction. Ground is an approximate local lower-percentile Copernicus DSM value, not surveyed ground or DTM.\n" +
               "\n--- Landmark references ---\n" +
               "".join(f"{s['title']} — {s['author']}, Wikimedia Commons ({s['source_page']}), {s['license']}. Used only as shape/proportion reference; not a texture.\n"
                       for s in landmarks["sources"] if s["kind"] == "shape_proportion_photo") +
               "Landmark positions from VWorld Satellite z19 imagery; geometry is photo-referenced code modelling (estimated, not surveyed).\n" +
               "\n--- Roads and vegetation ---\n" +
               "".join(f"{s.get('attribution') or s.get('source')} — {s.get('source', '')} ({s.get('license') or 'provider terms apply'})\n"
                       for s in roads["sources"] + vegetation["sources"] if s.get("attribution") or s.get("source")) +
               "Road ribbons: path from source, width/pavement per record status (source or estimated). Vegetation: zones from 2023 landcover; individual trees and greenhouses are procedural estimates.\n")
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
    detail = manifest["imagery_detail"]
    assert sha(ROOT / detail["path"]) == detail["sha256"] and sha(ROOT / detail["metadata_path"]) == detail["metadata_sha256"]
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
    assert len([n for n in reopened.graph.nodes if n.startswith(("terrain_landcover_", "ocean_surface"))]) > 0
    assert not any(n in reopened.graph.nodes for n in ("shore_basalt", "terrain_land"))
    terrain_meshes = {name: m for name, m in reopened.geometry.items()
                      if name.startswith(("ocean_surface", "terrain_landcover_"))}
    assert terrain_meshes and all(isinstance(m.visual, TextureVisuals) and len(m.visual.uv) == len(m.vertices)
                                  for m in terrain_meshes.values())
    assert all(np.isfinite(m.vertices).all() and np.isfinite(m.face_normals).all() and np.isfinite(m.vertex_normals).all() and
               np.allclose(np.linalg.norm(m.face_normals, axis=1), 1, atol=.02) and
               np.allclose(np.linalg.norm(m.vertex_normals, axis=1), 1, atol=.02)
               for m in reopened.geometry.values())
    originals = {kind: trimesh.load(ROOT / "var/rendering" / kind / "scene.glb", force="scene")
                 for kind in ("twin", "grid")}
    expected_peak, expected_lon_lat = terrain_focus(originals["grid"], manifest["coordinateFrame"])
    assert np.allclose(manifest["terrain"]["camera_focus_source_dsm"]["position"], expected_peak, atol=.002)
    assert np.allclose(manifest["terrain"]["camera_focus_source_dsm"]["coordinates"], expected_lon_lat, atol=1e-6)
    detail_faces = 0
    for name, actual in terrain_meshes.items():
        split = terrain_meshes.get(name + "_detail")
        if name.endswith("_detail"):
            assert actual.visual.material.name == "georeferenced_imagery_z17"
            assert ((actual.visual.uv >= 0) & (actual.visual.uv <= 1)).all(), name
            detail_faces += len(actual.faces)
        elif split is None:
            source = originals["grid"].geometry[name]
            assert len(source.faces) == len(actual.faces) and len(source.vertices) == len(actual.vertices)
            assert np.array_equal(source.faces, actual.faces)
            assert np.allclose(source.vertices, actual.vertices, atol=.002)
        else:  # the base and z17 parts hold every source triangle, vertex heights included, exactly once
            source = originals["grid"].geometry[name]
            ordered = lambda t: t[np.lexsort(t.reshape(len(t), -1).T)]
            assert len(source.faces) == len(actual.faces) + len(split.faces), name
            assert np.allclose(ordered(source.vertices[source.faces]),
                               ordered(np.concatenate([actual.vertices[actual.faces], split.vertices[split.faces]])), atol=.002)
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
                       for n, m in terrain_meshes.items() if not n.endswith("_detail"))
            assert manifest["sea"]["nonzero_coastal_vertex_count"] == np.count_nonzero(actual.vertices[:, 1])
            assert np.allclose(actual.bounds[:, 1], manifest["sea"]["height_range_m"])
        sample_indices = (np.arange(len(actual.vertices)) if name == "ocean_surface" else
                          np.linspace(0, len(actual.vertices)-1, min(20, len(actual.vertices)), dtype=int))
        vertices = actual.vertices[sample_indices]
        origin = manifest["coordinateFrame"]["origin_easting_northing"]
        mx, my = project_crs("EPSG:32652", "EPSG:3857", vertices[:, 0]+origin[0], origin[1]-vertices[:, 2])
        west, south, east, north = manifest["imagery_detail" if name.endswith("_detail") else "imagery"]["bounds"]
        expected = np.column_stack(((np.asarray(mx)-west)/(east-west), (np.asarray(my)-south)/(north-south)))
        assert np.allclose(actual.visual.uv[sample_indices], expected, atol=1e-5)
    assert detail_faces == detail["triangle_count"] > 0
    # The split changes no displayed height: 1,000 samples over the z17 area.
    parts = [m for n, m in terrain_meshes.items() if n.endswith("_detail")]
    low, high = np.min([m.bounds[0] for m in parts], axis=0), np.max([m.bounds[1] for m in parts], axis=0)
    x, z = (np.random.default_rng(0).uniform(low[i], high[i], 1000) for i in (0, 2))
    assert np.allclose(surface_height(reopened)(x, z), surface_height(originals["grid"])(x, z), atol=.002)
    # A failed z17 tile keeps every triangle touching it on the base imagery.
    detail_metadata = json.loads((ROOT / detail["metadata_path"]).read_text())
    mx, my = mercator(parts[0].vertices[:1], manifest["coordinateFrame"])
    failed = next(t for t in detail_metadata["tiles"] if shapely.intersects_xy(tile_box(t), mx[0], my[0]))
    probe = trimesh.Scene(base_frame="world")
    copy_nodes(originals["grid"], probe, set(), terrain=True)
    moved = apply_imagery(probe, ROOT / manifest["imagery"]["path"], manifest["imagery"], manifest["coordinateFrame"],
                          ROOT / detail["path"], {**detail_metadata, "failed_tiles": [failed]})
    assert 0 < moved < detail["triangle_count"]
    for name in (n for n in probe.geometry if n.endswith("_detail")):
        mx, my = (a[probe.geometry[name].faces] for a in mercator(probe.geometry[name].vertices, manifest["coordinateFrame"]))
        assert not shapely.intersects(tile_box(failed), shapely.box(mx.min(1), my.min(1), mx.max(1), my.max(1))).any()
    actual_heights = np.concatenate([m.vertices[:, 1] for name, m in reopened.geometry.items()
                                     if name.startswith(("ocean_surface", "terrain_landcover_"))])
    source_heights = np.concatenate([m.vertices[:, 1] for name, m in originals["grid"].geometry.items()
                                     if name.startswith(("ocean_surface", "terrain_landcover_"))])
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
    assert len(source_records) == len(prepared) == buildings["source_count"] == 2661
    assert [r["id"] for r in buildings["records"]] == [b["id"] for b in site["buildings"]]
    assert buildings["count"] == len(buildings["records"]) == 2661 and buildings["default_visible"] is True
    for key in ("height_status", "roof_rule", "roof_texture"):
        counts = Counter(r[key] for r in buildings["records"])
        assert counts == buildings["counts"][key] and sum(counts.values()) == 2661, key
    assert set(buildings["counts"]["height_status"]) <= {"provider", "floors_estimated", "default_estimated"}
    assert set(buildings["counts"]["roof_rule"]) <= {"gable_house", "gable_shed", "flat_parapet"}
    atlas = json.loads((ROOT / "var/rendering/roofs/atlas.json").read_text())
    for record in buildings["records"]:
        building = prepared[record["id"]]
        source = source_records[record["id"]]
        assert source["properties"] == building["source_properties"] and source["geometry"] == building["source_geometry"]
        parts = footprint_polygons(building)
        status, walls_m, rule = building_rule(source["properties"], parts)
        assert (record["height_status"], record["walls_m"], record["roof_rule"]) == (status, walls_m, rule)
        assert record["storey_m"] == (3.0 if status == "floors_estimated" else walls_m / record["floors_used"])
        node = record["node"]
        position = np.asarray(record["position"])
        assert np.allclose(reopened.graph[node][0][:3, 3], position, atol=1e-6)
        wall, roof = (reopened.geometry[f"{node}_{suffix}"] for suffix in ("walls", "roof"))
        # Metre UVs: u = metres along the wall / 4, v = height / 4 storeys (one window row per storey).
        assert np.allclose(wall.visual.uv[:, 1] * 4 * record["storey_m"], wall.vertices[:, 1], atol=1e-4)
        low = wall.vertices[:, 1] < walls_m - 1e-6
        assert np.isclose(wall.visual.uv[low, 0].max(), shapely.union_all(parts).exterior.length / 4, atol=1e-3)
        assert np.isclose(wall.bounds[0, 1], 0, atol=1e-5) and np.isclose(roof.bounds[0, 1], walls_m, atol=1e-5)
        footprint = shapely.union_all(parts)
        roof_xz = roof.vertices[:, [0, 2]] + position[[0, 2]]
        if rule == "flat_parapet":
            assert np.isclose(roof.bounds[1, 1], walls_m + .6, atol=1e-5)  # 0.6 m parapet
            assert shapely.contains_xy(footprint.buffer(.001), roof_xz[:, 0], roof_xz[:, 1]).all(), record["id"]
            assert np.isclose(roof.area_faces[roof.face_normals[:, 1] > .999].sum(), footprint.area + sum(
                p.difference(p.buffer(-.2, join_style="mitre")).area for p in parts), rtol=1e-4)
        else:
            assert roof.bounds[1, 1] > walls_m and np.isclose(record["roof_top_m"], roof.bounds[1, 1], atol=1e-5)
            rect = footprint.minimum_rotated_rectangle
            assert shapely.contains_xy(rect.buffer(.401, join_style="mitre"), roof_xz[:, 0], roof_xz[:, 1]).all()
            assert not shapely.contains_xy(rect.buffer(.39, join_style="mitre"), roof_xz[:, 0], roof_xz[:, 1]).all()
            assert np.allclose(roof.face_normals[:, 1], np.cos(np.radians(20 if rule == "gable_house" else 10)), atol=1e-6)
        if record["roof_texture"] == "vworld_z19":
            x0, y0, x1, y1 = atlas["entries"][record["id"]]["atlas_px"]
            px, py = roof.visual.uv[:, 0] * atlas["width"], (1 - roof.visual.uv[:, 1]) * atlas["height"]
            assert ((roof.visual.uv >= 0) & (roof.visual.uv <= 1)).all()
            assert (px >= x0).all() and (px <= x1).all() and (py >= y0).all() and (py <= y1).all(), record["id"]
            assert roof.visual.material.name == "roof_imagery"
        else:
            assert record["id"] not in atlas["entries"] and roof.visual.material.name == "building_neutral_roof"
    # Zero, NaN and infinite provider heights fall through to floors, then the one-storey default.
    square = [Polygon([(0, 0), (8, 0), (8, 6), (0, 6)])]
    for height in ("0", "nan", "inf", "-3", ""):
        assert building_rule({"height": height, "grnd_flr": "2"}, square)[:2] == ("floors_estimated", 6.0)
        assert building_rule({"height": height, "grnd_flr": "0"}, square)[:2] == ("default_estimated", 3.5)
    assert building_rule({"height": "4", "grnd_flr": "1", "strct_cd": "12", "usability": "01000"}, square) == ("provider", 4.0, "gable_house")
    assert building_rule({"grnd_flr": "1", "strct_cd": "12", "usability": "18000"}, square)[2] == "gable_shed"
    assert building_rule({"grnd_flr": "3", "strct_cd": "21", "usability": "01000"}, square)[2] == "flat_parapet"
    # A concave footprint with an interior courtyard is never gabled; walls and parapet keep outward normals.
    example = Polygon([(0, 0), (12, 0), (12, 4), (8, 4), (8, 12), (0, 12)],
                      holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]])
    assert building_rule({"strct_cd": "12", "grnd_flr": "1"}, [example])[2] == "flat_parapet"
    walls, roof = building_meshes([example], 7.5, np.zeros(2), "flat_parapet", 3.75)
    assert np.allclose(walls.bounds[:, 1], [0, 7.5]) and np.allclose(roof.bounds[:, 1], [7.5, 8.1])
    assert all(example.buffer(1e-9).covers(Polygon(t[:, [0, 2]])) for t in roof.triangles if abs(np.ptp(t[:, 1])) < 1e-9)
    ring = example.difference(example.buffer(-.2, join_style="mitre"))
    for mesh, solid in ((walls, example), (roof, ring)):
        side = np.abs(mesh.face_normals[:, 1]) < 1e-6
        outside = mesh.triangles_center[side][:, [0, 2]] + mesh.face_normals[side][:, [0, 2]] * .01
        assert side.any() and not shapely.intersects_xy(solid, outside[:, 0], outside[:, 1]).any()
    # A 10 x 6 m house: 20 degree gable along the long axis, 0.4 m eaves, closed gable ends.
    walls, roof = building_meshes([Polygon([(0, 0), (10, 0), (10, 6), (0, 6)])], 3.0, np.zeros(2), "gable_house", 3.0)
    assert np.isclose(roof.bounds[1, 1], 3 + 3.4 * np.tan(np.radians(20))) and np.isclose(walls.bounds[1, 1], roof.bounds[1, 1])
    assert np.allclose(roof.bounds[:, [0, 2]], [[-.4, -.4], [10.4, 6.4]]) and len(roof.faces) == 4
    ridge = roof.vertices[np.isclose(roof.vertices[:, 1], roof.bounds[1, 1])]
    assert np.allclose(ridge[:, 2], 3) and np.all(roof.face_normals[:, 1] > 0)
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
            "buildings": buildings["count"], "building_counts": buildings["counts"],
            "sea_source_geometry_unchanged": True, "sea_original_imagery_restored": True, "detail_triangles": detail_faces,
            "display_routes": sum(bool(r["paths"]) for r in manifest["routes"]), "coast_paths": len(manifest["coast"]),
            "reopened_meshes": len(reopened.geometry), "checks": ["source hashes", "reopened GLB", "frame and units",
            "facility and rotor transforms", "unaltered DSM vertices and projected UV", "embedded satellite JPEG",
            "source DSM min/max and 100+ distinct heights without exaggeration", "finite vertices and unit normals",
            "source route IDs/coordinates", "path/coast clipping and reentry",
            "2661 building IDs/properties/geometries audited against raw provider source; height status and roof rule recomputed",
            "gable ridge/eaves within 0.4 m of the rectangle, 0.6 m parapets, metre wall UVs, roof UVs inside each building's atlas cell",
            "zero/NaN/infinite provider heights fall through; courtyard parapet and outward normals",
            "unchanged WBM sea vertices/faces, restored source imagery pixels and all sea UVs, water-source hash",
            "z17 split keeps every source triangle and vertex height, detail UVs in [0,1], surface_height unchanged at 1,000 samples, failed-tile triangles stay on z15"]}


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
