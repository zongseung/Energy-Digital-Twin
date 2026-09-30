#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Sinchang trees, citrus orchards and vinyl greenhouses from the 2023 MoE landcover zones.

uv run renderers/twin/vegetation.py --self-test   # no network, flat height_at
uv run renderers/twin/vegetation.py               # var/rendering/vegetation/preview.glb on the displayed terrain
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import time

import numpy as np
import shapely
import trimesh
from PIL import Image
from rasterio.warp import transform as project_crs
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from build import normals, sha
from landmarks import ring, solid
from roads import SITE, to_scene

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var/rendering/vegetation"
LANDCOVER = ROOT / ".worktrees/data/var/data/geography/source-03e02ef/landcover.geojsonl"
AOI = (126.155, 33.325, 126.19, 33.36)  # site AOI (var/rendering/site/scene.json aoi_bbox)
BUDGET, SEED = 300_000, 20260930
CLASSES = {"320": "conifer", "310": "broadleaf", "330": "broadleaf", "240": "orchard", "230": "greenhouse"}
TREES = {"conifer": ((8, 15), (1, 2, 3)), "broadleaf": ((6, 12), (4, 5)), "orchard": ((2.5, 3), (6, 7))}  # height m, palette columns
PALETTE = Image.new("RGB", (8, 1))
PALETTE.putdata([(86, 64, 46), (34, 62, 40), (42, 72, 44), (30, 55, 36), (70, 100, 50), (84, 112, 58), (46, 90, 40), (58, 102, 46)])
# ponytail: open cones and trunks (caps dropped) drawn double-sided; add caps if under-canopy views show gaps.
FOLIAGE = PBRMaterial(name="vegetation_foliage", baseColorTexture=PALETTE, metallicFactor=0, roughnessFactor=.9, doubleSided=True)
# ponytail: one alpha-blended mesh, no per-tunnel sorting; overlapping film may sort wrongly from low angles.
FILM = PBRMaterial(name="vegetation_greenhouse_film", baseColorFactor=[236, 241, 238, 150], alphaMode="BLEND",
                   metallicFactor=0, roughnessFactor=.3, doubleSided=True)
ARCH = np.linspace(0, np.pi, 13)  # 12-segment semicircle


def shared(mesh: trimesh.Trimesh, caps: bool = False) -> trimesh.Trimesh:
    """Merge corners (smooth normals, a third of the vertices); drop flat caps on the ground or inside a crown unless caps."""
    keep = caps | (np.abs(mesh.face_normals[:, 1]) < .99)
    return normals(trimesh.Trimesh(mesh.vertices, mesh.faces[keep]))


def template(*parts) -> tuple:
    """Unit-height object: vertices, faces and a per-vertex crown flag (False = trunk)."""
    mesh = trimesh.util.concatenate([m for m, _ in parts])
    return mesh.vertices, mesh.faces, np.concatenate([np.full(len(m.vertices), crown) for m, crown in parts])


ICO = trimesh.creation.icosphere(subdivisions=0)
TEMPLATES = {  # 8-sided two-tier cone, dodecahedron crown, low citrus sphere
    "conifer": template((shared(solid(ring(4, .025, 0), ring(4, .02, .32))), False), (shared(solid(ring(8, .25, .18), [[0, .72, 0]])), True),
                        (shared(solid(ring(8, .18, .45, 22.5), [[0, 1, 0]])), True)),
    "broadleaf": template((shared(solid(ring(4, .03, 0), ring(4, .025, .45))), False),  # -36 deg lower rings make solid() split true pentagons
                          (shared(solid(ring(5, .243, 1), ring(5, .393, .752), ring(5, .393, .598, -36), ring(5, .243, .35, -36)), caps=True), True)),
    "orchard": template((normals(trimesh.Trimesh(ICO.vertices / ICO.vertices.max(axis=0) * [.55, .5, .55] + [0, .5, 0], ICO.faces, process=False)), True)),
}


def landcover() -> tuple[list, list]:
    """Vegetation classes that touch the AOI; add_vegetation replaces img_dates with the zoned ones."""
    from groundcover import landcover as load  # lazy: groundcover imports this module
    return load(AOI, CLASSES)


def buildings():
    """Site building footprints (scene x/z) grown 3 m, so trees and tunnels clear the walls."""
    # ponytail: 3 m clearance is below the widest crown (4.7 m); big broadleaf crowns can brush eaves. Grow per class if seen.
    from build_local import footprint_polygons  # lazy: build_local imports this module
    site = json.loads(SITE.read_text())
    return shapely.union_all([p for b in site["buildings"] for p in footprint_polygons(b)]).buffer(3)


def zones(features: list, frame: dict, built) -> list:
    """[(class, polygons clipped to the AOI minus buildings, in scene x/z metres, properties)] per source feature."""
    aoi, out = shapely.box(*AOI).buffer(-6e-5, join_style="mitre"), []  # >= 5.6 m: widest crown 4.7 m
    for f in features:
        cls, geom = CLASSES.get(f["properties"]["l2_code"]), shapely.geometry.shape(f["geometry"])
        if cls and geom.intersects(aoi):
            parts = shapely.get_parts(shapely.difference(shapely.transform(shapely.intersection(geom, aoi), lambda p: to_scene(p[:, 0], p[:, 1], frame)), built))
            if parts := [p for p in parts if p.geom_type == "Polygon" and p.area > 0]:
                out.append((cls, parts, f["properties"]))
    return out


def axes(poly) -> tuple:
    """Corner, long side and short side of the minimum rotated rectangle."""
    p0, p1, p2 = np.asarray(shapely.minimum_rotated_rectangle(poly).exterior.coords)[:3]
    return (p0, *sorted((p1 - p0, p2 - p1), key=lambda side: -np.linalg.norm(side)))


def poisson(poly, density: float, rng, cells: dict, spacing: float = 5.0) -> list:
    """Dart throwing: uniform candidates kept >= spacing from every kept tree (cells is shared) until the density is met."""
    # ponytail: 10x random candidates, not Bridson; strips narrower than ~5 m can fall short of the density.
    x0, z0, x1, z1 = poly.bounds
    target, kept = round(poly.area * density), []
    xz = rng.uniform((x0, z0), (x1, z1), (int(10 * (x1 - x0) * (z1 - z0) * density) + 10, 2))
    for x, z in xz[shapely.contains_xy(poly, xz[:, 0], xz[:, 1])].tolist():
        if len(kept) >= target:
            break
        i, j = int(x // spacing), int(z // spacing)
        if all(math.hypot(x - a, z - b) >= spacing for di in (-1, 0, 1) for dj in (-1, 0, 1) for a, b in cells.get((i + di, j + dj), ())):
            cells.setdefault((i, j), []).append((x, z))
            kept.append((x, z))
    return kept


def orchard(poly, spacing: float) -> list:
    """Citrus lattice: rows 5 m apart along the long axis, trees every `spacing` m, 1.5 m inside the edge."""
    p0, d, n = axes(poly)
    u, v = np.meshgrid(np.arange(spacing / 2, np.linalg.norm(d), spacing), np.arange(2.5, np.linalg.norm(n), 5))
    xz = p0 + u.reshape(-1, 1) * d / np.linalg.norm(d) + v.reshape(-1, 1) * n / np.linalg.norm(n)
    return xz[shapely.contains_xy(poly.buffer(-1.5), xz[:, 0], xz[:, 1])].tolist()


def tunnels(poly) -> list:
    """7 m tunnels 1.5 m apart along the long axis, wholly inside the polygon, >= 5 m long: [ax, az, bx, bz]."""
    p0, d, n = axes(poly)
    inner, out = poly.buffer(-3.5), []
    for v in np.arange(4.25, np.linalg.norm(n), 8.5):
        start = p0 + v * n / np.linalg.norm(n)
        for part in shapely.get_parts(inner.intersection(shapely.LineString([start, start + d]))):
            if part.length >= 5:
                out.append([*part.coords[0], *part.coords[-1]])
    return out


def place(zoned: list, factor: float = 1.0, seed: int = SEED) -> dict:
    """Trees [x, z, yaw, height, palette column] and tunnels [ax, az, bx, bz]; factor < 1 thins forests and orchards."""
    rng, cells, found = np.random.default_rng(seed), {}, {cls: [] for cls in (*TREES, "greenhouse")}
    for cls, parts, _ in zoned:
        for poly in parts:
            found[cls] += (tunnels(poly) if cls == "greenhouse" else orchard(poly, 4 / factor) if cls == "orchard"
                           else poisson(poly, factor / 60, rng, cells))
    placed = {"greenhouse": np.reshape(found["greenhouse"], (-1, 4))}
    for cls, ((low, high), shades) in TREES.items():
        xz = np.reshape(found[cls], (-1, 2))
        placed[cls] = np.column_stack((xz, rng.uniform(0, 2 * np.pi, len(xz)), rng.uniform(low, high, len(xz)), rng.choice(shades, len(xz))))
    return placed


def trees(cls: str, rows: np.ndarray, height_at) -> trimesh.Trimesh:
    """One merged mesh of vertical template instances standing on height_at at each anchor."""
    # ponytail: trees are not conformed to slopes; the downhill trunk edge floats by radius x slope (cm on this DSM).
    vertices, faces, crown = TEMPLATES[cls]
    x, z, yaw, size, shade = rows.T
    v, cos, sin = vertices * size[:, None, None], np.cos(yaw)[:, None], np.sin(yaw)[:, None]
    v = np.stack((v[..., 0] * cos - v[..., 2] * sin + x[:, None], v[..., 1] + height_at(x, z)[:, None],
                  v[..., 0] * sin + v[..., 2] * cos + z[:, None]), axis=-1).reshape(-1, 3)
    mesh = trimesh.Trimesh(v, (faces + len(vertices) * np.arange(len(rows))[:, None, None]).reshape(-1, 3), process=False)
    u = (np.where(crown, shade[:, None], 0).ravel() + .5) / PALETTE.width
    mesh.visual = TextureVisuals(uv=np.column_stack((u, np.full(len(u), .5))), material=FOLIAGE)
    return normals(mesh)


def greenhouses(rows: np.ndarray, height_at) -> trimesh.Trimesh:
    """Semicircular film tunnels with end walls, draped on height_at at stations <= 10 m apart."""
    points, faces, start = [], [], 0
    fan = np.arange(1, 12)
    for ax, az, bx, bz in rows:
        axis = np.array([bx - ax, bz - az])
        side = np.array([-axis[1], axis[0]]) / np.linalg.norm(axis)
        t = np.linspace(0, 1, math.ceil(np.linalg.norm(axis) / 10) + 1)
        xz = ([ax, az] + t[:, None, None] * axis + (3.5 * np.cos(ARCH))[:, None] * side).reshape(-1, 2)
        points.append(np.column_stack((xz, np.tile(3.5 * np.sin(ARCH), len(t)))))
        k, last = start + (np.arange(len(t) - 1)[:, None] * 13 + np.arange(12)).ravel(), start + 13 * (len(t) - 1)
        faces += [np.column_stack((k, k + 14, k + 1)), np.column_stack((k, k + 13, k + 14)),  # outward, checked in the self-test
                  np.column_stack((np.full(11, start), start + fan, start + fan + 1)), np.column_stack((np.full(11, last), last + fan + 1, last + fan))]
        start += len(xz)
    p = np.vstack(points)
    mesh = trimesh.Trimesh(np.column_stack((p[:, 0], p[:, 2] + height_at(p[:, 0], p[:, 1]), p[:, 1])), np.vstack(faces), process=False)
    mesh.visual = TextureVisuals(material=FILM)
    return normals(mesh)


def add_vegetation(scene: trimesh.Scene, frame: dict, height_at, features: list | None = None,
                   budget: int = BUDGET, seed: int = SEED) -> dict:
    features, sources = landcover() if features is None else (features, [])
    zoned, factor = zones(features, frame, buildings()), 1.0
    while True:
        placed = place(zoned, factor, seed)
        meshes = {cls: greenhouses(rows, height_at) if cls == "greenhouse" else trees(cls, rows, height_at)
                  for cls, rows in placed.items() if len(rows)}
        triangles = sum(len(m.faces) for m in meshes.values())
        if triangles <= budget:
            break
        assert any(len(placed[cls]) for cls in TREES), "greenhouses alone exceed the vegetation triangle budget"
        # ponytail: one thinning factor for forests and orchards, greenhouses kept; per-class LOD if dense stands matter.
        factor *= min(.95, budget / triangles)
    for cls, mesh in meshes.items():
        scene.add_geometry(mesh, geom_name=f"vegetation_{cls}", node_name=f"vegetation_{cls}")
    polygons, factor = Counter(cls for cls, _, _ in zoned), round(factor, 3)
    density = {"factor": factor, "forest_m2_per_tree": round(60 / factor, 1), "forest_min_spacing_m": 5, "orchard_row_m": 5,
               "orchard_in_row_m": round(4 / factor, 2), "greenhouse_width_m": 7, "greenhouse_gap_m": 1.5, "budget_triangles": budget}
    return {"counts": {cls: len(rows) for cls, rows in placed.items()}, "polygons": {cls: polygons[cls] for cls in placed},
            "triangles": triangles, "density": density, "seed": seed,
            "sources": [{**s, "img_dates": sorted({p.get("img_date") for _, _, p in zoned})} for s in sources],
            "limits": ["Zones are actual (환경부 토지피복지도 중분류, 2023 aerial orthophoto, clipped to the site AOI); individual trees, "
                       "their form, height and colour, and the greenhouse layout are estimates, not a tree or facility survey.",
                       f"Forest: 320 conifers as two-tier cones 8-15 m; 310/330 broadleaf and mixed as round crowns 6-12 m; "
                       f"dart-thrown at about 1 tree per {density['forest_m2_per_tree']} m2 and >= 5 m apart.",
                       f"Orchard (240): citrus 2.5-3 m in rows 5 m apart along the minimum-rotated-rectangle long axis, every "
                       f"{density['orchard_in_row_m']} m, 1.5 m inside the edge; windbreak hedges are not modelled.",
                       "Greenhouse (230): 7 m semicircular film tunnels 1.5 m apart along the long axis, >= 5 m long and wholly inside "
                       "the polygon; real house form, size and count are unknown.",
                       "Trees stand on height_at (displayed coarse DSM terrain) at the trunk; tunnels are draped every <= 10 m. "
                       "The DSM includes canopy and roofs, so bases can float or sink."]
                      + ([f"Triangle budget {budget}: forest and orchard density reduced by factor {factor}."] if factor < 1 else [])}


def self_test() -> None:
    frame = {"horizontal_crs": "EPSG:32652", "origin_easting_northing": [236820.23997262522, 3692887.4836922204]}
    (e0, n0) = frame["origin_easting_northing"]
    height_at = lambda x, z: 10 + .01 * np.asarray(x, float)  # flat test terrain

    def feature(code: str, x0: float, z0: float, width: float, depth: float) -> dict:
        xs, zs = np.array([x0, x0 + width, x0 + width, x0, x0]), np.array([z0, z0, z0 + depth, z0 + depth, z0])
        lon, lat = project_crs(frame["horizontal_crs"], "EPSG:4326", xs + e0, n0 - zs)
        return {"properties": {"l2_code": code, "img_date": "2023-12-30Z"},
                "geometry": {"type": "Polygon", "coordinates": [list(zip(lon, lat))]}}

    (west,), _ = project_crs("EPSG:4326", frame["horizontal_crs"], [AOI[0]], [33.343])
    features = [feature("320", 0, 0, 100, 100),  # village edge: crosses site building footprints
                feature("240", -1400, -1000, 100, 50), feature("230", -1400, -900, 50, 40),
                feature("310", west - e0 - 50, 0, 100, 100),  # straddles the AOI west edge
                feature("610", 400, 0, 100, 100)]  # bare land: ignored
    area_of = lambda zoned, cls: sum(p.area for c, parts, _ in zoned if c == cls for p in parts)
    built = buildings()
    zoned = zones(features, frame, built)
    assert area_of(zoned, "conifer") < 10000 - 169, "building footprints not removed"
    area = {cls: area_of(zoned, cls) for cls, *_ in zoned}
    assert sorted(area) == ["broadleaf", "conifer", "greenhouse", "orchard"] and 4000 < area["broadleaf"] < 6000, area  # about half: tilted meridian, 5.6 m inset
    placed = place(zoned)
    for cls in ("conifer", "broadleaf", "orchard"):
        x, z = placed[cls][:, 0], placed[cls][:, 1]
        region = shapely.union_all([p for c, parts, _ in zoned if c == cls for p in parts])
        assert len(x) and shapely.contains_xy(region, x, z).all() and not shapely.intersects_xy(built, x, z).any(), cls
        lon, lat = project_crs(frame["horizontal_crs"], "EPSG:4326", x + e0, n0 - z)
        assert (AOI[0] <= np.min(lon)) and np.max(lon) <= AOI[2] and AOI[1] <= np.min(lat) and np.max(lat) <= AOI[3], cls
    for cls in ("conifer", "broadleaf"):  # forest: ~1 tree / 60 m2, >= 5 m apart
        xz = placed[cls][:, :2]
        gap = np.linalg.norm(xz[:, None] - xz[None], axis=-1) + np.eye(len(xz)) * 1e9
        assert gap.min() >= 5 and .95 * area[cls] / 60 <= len(xz) <= area[cls] / 60 + 1, (cls, gap.min(), len(xz))
    rows = np.unique(np.round(placed["orchard"][:, 1], 3))  # 100 x 50 m field: rows run along x, 5 m apart
    assert len(rows) >= 5 and np.allclose(np.diff(rows), 5), rows
    runs = placed["greenhouse"]  # 50 x 40 m field: tunnels along x, 7 m + 1.5 m apart
    assert len(runs) >= 3 and np.allclose(runs[:, 1], runs[:, 3]) and np.allclose(np.diff(np.sort(runs[:, 1])), 8.5), runs
    field = next(p for c, parts, _ in zoned if c == "greenhouse" for p in parts).buffer(1e-6)
    assert all(field.contains(shapely.LineString([t[:2], t[2:]]).buffer(3.5, cap_style="flat")) for t in runs)

    scene = trimesh.Scene()
    result = add_vegetation(scene, frame, height_at, features)
    assert result["density"]["factor"] == 1 and result["seed"] == SEED and result["polygons"]["broadleaf"] == 1
    assert result["counts"] == {cls: len(placed[cls]) for cls in placed}, result["counts"]
    assert set(scene.graph.nodes_geometry) == {f"vegetation_{cls}" for cls in placed}
    for cls, count in result["counts"].items():
        mesh = scene.geometry[f"vegetation_{cls}"]
        assert np.isfinite(mesh.vertices).all() and np.allclose(np.linalg.norm(mesh.vertex_normals, axis=1), 1, atol=2e-3)
        v = mesh.vertices
        lon, lat = project_crs(frame["horizontal_crs"], "EPSG:4326", v[:, 0] + e0, n0 - v[:, 2])  # whole crowns, not just anchors
        assert AOI[0] <= np.min(lon) and np.max(lon) <= AOI[2] and AOI[1] <= np.min(lat) and np.max(lat) <= AOI[3], cls
        if cls == "greenhouse":  # draped: every base vertex on height_at, nothing below it
            lift = v[:, 1] - height_at(v[:, 0], v[:, 2])
            assert lift.min() > -1e-6 and np.isclose(lift, 0).sum() >= 4 * count and lift.max() < 3.5 + 1e-6
            assert mesh.face_normals[:, 1].min() > -.05, "tunnel film faces inward"
        else:  # vertical trees: object base on height_at at its anchor
            base = v.reshape(count, -1, 3)[:, :, 1].min(axis=1)
            assert np.allclose(base, height_at(placed[cls][:, 0], placed[cls][:, 1])), cls
    assert result["triangles"] == sum(len(g.faces) for g in scene.geometry.values())
    again = trimesh.Scene()
    add_vegetation(again, frame, height_at, features)
    assert all(np.array_equal(scene.geometry[n].vertices, again.geometry[n].vertices) for n in scene.geometry)
    small = add_vegetation(trimesh.Scene(), frame, height_at, features, budget=result["triangles"] // 2)
    assert small["triangles"] <= result["triangles"] // 2 and small["density"]["factor"] < 1
    assert small["counts"]["conifer"] < result["counts"]["conifer"] and any("reduced" in s for s in small["limits"])
    print(f"PASS vegetation {result['counts']} {result['triangles']} triangles; budget {result['triangles'] // 2} -> "
          f"factor {small['density']['factor']:.3f}, {small['triangles']} triangles")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    if parser.parse_args().self_test:
        return self_test()
    from build_local import surface_height  # not at the top: build_local imports this module at integration
    frame = json.loads((ROOT / "var/rendering/local/manifest.json").read_text())["coordinateFrame"]
    height_at = surface_height(trimesh.load(ROOT / "var/rendering/local/scene.glb", force="scene"))
    start, scene = time.perf_counter(), trimesh.Scene()
    result = add_vegetation(scene, frame, height_at)
    seconds = round(time.perf_counter() - start, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "preview.glb").write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))
    (OUT / "vegetation.json").write_text(json.dumps({"coordinateFrame": frame, **result, "seconds": seconds}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"glb": str((OUT / "preview.glb").relative_to(ROOT)), "sha256": sha(OUT / "preview.glb"), "seconds": seconds,
                      **{key: result[key] for key in ("counts", "polygons", "triangles", "density")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
