#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Sinchang road ribbons draped on the displayed terrain.

uv run renderers/twin/roads.py --self-test   # no network, planar height_at
uv run renderers/twin/roads.py --collect     # VWorld LT_L_SPRD over the AOI -> var/rendering/roads/vworld.json
uv run renderers/twin/roads.py               # var/rendering/roads/preview.glb on var/rendering/local/scene.glb heights
vworld_key is never printed or stored.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import shapely
import trimesh
from rasterio.warp import transform as project_crs
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from build import normals, sha
from prepare_imagery import read_key

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "var/rendering/site/scene.json"
OUT = ROOT / "var/rendering/roads"
CELL = 3.4  # grid-cut square; its 4.81 m diagonal keeps triangle edges <= 5 m in 3D up to 28% slopes
CLASS_WIDTH = {"secondary": 7.0, "tertiary": 6.0, "secondary_link": 5.0, "tertiary_link": 5.0, "residential": 4.0}
ASPHALT, CENTER = {"secondary", "tertiary", "secondary_link", "tertiary_link"}, {"secondary", "tertiary"}
MATERIALS = {name: PBRMaterial(name=f"street_{name}", baseColorFactor=color, metallicFactor=0, roughnessFactor=rough)
             for name, color, rough in [("asphalt", [62, 64, 66, 255], .9), ("concrete", [158, 157, 150, 255], .85),
                                        ("center_line", [232, 178, 32, 255], .6), ("edge_line", [236, 236, 230, 255], .6)]}
LAYER, API = "LT_L_SPRD", "https://api.vworld.kr/req/data"
DOC = "https://www.vworld.kr/dev/v4dv_2ddataguide2_s002.do?svcIde=sprd"
NO_WIDTH = ("VWorld LT_L_SPRD (도로명주소 도로, 행정안전부) documents and returns only rn (road name) besides geometry; "
            "no width attribute, so no provider width is attached.")


def width(props: dict) -> tuple[float, str]:
    """lanes x 3.25 m, else the class default; VWorld has no width to try first (NO_WIDTH)."""
    lanes = int(props.get("lanes") or 0)
    return (lanes * 3.25, "lanes_estimated") if lanes > 0 else (CLASS_WIDTH[props["highway"]], "class_estimated")


def aoi_polygon(frame: dict, bbox: list[float]) -> shapely.Polygon:
    ring = np.asarray(shapely.segmentize(shapely.box(*bbox), 1e-3).exterior.coords)
    east, north = project_crs("EPSG:4326", frame["horizontal_crs"], ring[:, 0], ring[:, 1])
    e0, n0 = frame["origin_easting_northing"]
    return shapely.Polygon(np.column_stack((np.asarray(east) - e0, n0 - np.asarray(north))))


def ribbon(polygon, height_at, sea) -> trimesh.Trimesh | None:
    """Cut into CELL squares on a global grid, constrained-Delaunay each piece, put every vertex at height_at + 0.3 m."""
    if polygon.is_empty:
        return None
    x0, z0, x1, z1 = np.floor(np.asarray(polygon.bounds) / CELL).astype(int)
    xs, zs = (a.ravel() * CELL for a in np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1)))
    cells = shapely.box(xs, zs, xs + CELL, zs + CELL)
    shapely.prepare(polygon)
    parts = shapely.get_parts(shapely.intersection(cells[shapely.intersects(polygon, cells)], polygon))
    parts = parts[shapely.get_type_id(parts) == 3]  # drop line/point touches
    xz = shapely.get_coordinates(shapely.get_parts(shapely.constrained_delaunay_triangles(parts))).reshape(-1, 4, 2)[:, :3]
    a, b, c = xz[:, 0], xz[:, 1], xz[:, 2]
    # GEOS emits clockwise (x, z) triangles, i.e. +y normals (self-test checks). Cut slivers under 1 mm high are dropped:
    # float32 GLB positions (~0.1 mm at 1.5 km) would collapse them into zero-area faces; the gap left is < 1 mm.
    twice_area = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    xz = xz[twice_area / np.linalg.norm(xz - np.roll(xz, 1, axis=1), axis=2).max(axis=1) > 1e-3]
    centre = xz.mean(axis=1)
    # ponytail: sea is cut per triangle (CELL squares), not along an exact coastline; clip to a coastline polygon if it shows.
    xz = xz[~sea(centre[:, 0], centre[:, 1])]
    if not len(xz):
        return None
    flat = xz.reshape(-1, 2)
    vertices = np.column_stack((flat[:, 0], height_at(flat[:, 0], flat[:, 1]) + .3, flat[:, 1]))
    return normals(trimesh.Trimesh(vertices, np.arange(len(vertices)).reshape(-1, 3)))  # merges shared cut vertices


def add_roads(scene: trimesh.Scene, frame: dict, height_at, roads: list | None = None) -> dict:
    site = json.loads(SITE.read_text())
    assert all(site["projection"][key] == frame[key] for key in
               ("horizontal_crs", "vertical_crs", "origin_easting_northing", "axes", "scale"))
    roads = site["roads"] if roads is None else roads
    aoi = aoi_polygon(frame, site["aoi_bbox"])
    terrain = np.asarray(site["terrain"]["positions"]).reshape(-1, 3)
    ocean = np.asarray(site["terrain"]["water_classes"]) == 1
    tree = shapely.STRtree(shapely.points(terrain[:, 0], terrain[:, 2]))
    sea = lambda x, z: (height_at(x, z) <= .2) & ocean[tree.query_nearest(shapely.points(x, z), all_matches=False)[1]]
    asphalt, concrete, center, records = [], [], [], []
    for road in roads:
        props = road["source_properties"]
        width_m, status = width(props)
        lines = [shapely.LineString(np.asarray(path)[:, [0, 2]]) for path in road["paths"]]
        (asphalt if props["highway"] in ASPHALT else concrete).extend(shapely.buffer(lines, width_m / 2, cap_style="flat"))
        if props["highway"] in CENTER:  # double yellow: two 0.15 m lines, 0.15 m apart
            center += [shapely.offset_curve(line, side * .15).buffer(.075, cap_style="flat") for line in lines for side in (-1, 1)]
        records.append({"id": road["id"], "name": props.get("name"), "highway": props["highway"], "width_m": width_m,
                        "width_status": status, "length_m": float(sum(line.intersection(aoi).length for line in lines))})
    # Same-class surfaces are one union; asphalt wins crossings by cutting residential concrete out, and markings are
    # inlaid (cut out of the asphalt, same +0.3 m): nothing is stacked, so nothing z-fights. A +0.05 m overlay was
    # measured to dip up to 0.16 m under the asphalt where the two meshes' linear chords cross a terrain crease.
    # Markings come from the unclipped asphalt so the AOI cut does not draw an edge line across the road.
    asphalt = shapely.union_all(asphalt)
    center = shapely.union_all(center).intersection(asphalt)
    edge = asphalt.buffer(-.3).boundary.buffer(.075, cap_style="flat").intersection(asphalt).difference(center)
    layers = {"asphalt": asphalt.difference(center | edge), "concrete": shapely.union_all(concrete).difference(asphalt),
              "center_line": center, "edge_line": edge}
    triangles = 0
    for name, polygon in layers.items():
        mesh = ribbon(polygon.intersection(aoi), height_at, sea)
        if mesh is None:
            continue
        mesh.visual = TextureVisuals(material=MATERIALS[name])
        scene.add_geometry(mesh, geom_name=f"street_{name}", node_name=f"street_{name}")
        triangles += len(mesh.faces)
    road_source = next(s for s in site["metadata"]["sources"] if s.get("file") == "road.geojsonl")
    return {"count": len(records), "records": records, "triangles": triangles,
            "sources": [{**road_source, "kind": "osm_derived_centerlines"},
                        {"path": str(SITE.relative_to(ROOT)), "sha256": sha(SITE), "kind": "prepared_clipped_scene_paths"},
                        {"kind": "vworld_road_name_segments", "layer": LAYER, "source": API, "documentation": DOC,
                         "used_for_width": False, "reason": NO_WIDTH}],
            "limits": ["Paths are actual OSM-derived centrelines; widths are estimates: lanes x 3.25 m, else secondary 7, tertiary 6, "
                       "links 5, residential 4 m. " + NO_WIDTH,
                       "Pavement (asphalt on secondary/tertiary and links, grey concrete on residential) and the double yellow centre "
                       "and white edge lines are display estimates, not surveyed materials or markings; OSM surface tags are not used.",
                       "Ribbons sit 0.3 m above the displayed terrain at every vertex with <= 5 m triangle edges; between vertices they "
                       "are linear, so terrain creases shift them by about +-0.12 m. Markings are inlaid flush in the asphalt "
                       "(not 0.05 m above it) so they cannot sink under it.",
                       "The double yellow line is drawn on every secondary/tertiary way, including one-way carriageways of divided "
                       "roads (the source has no oneway tag). OSM bridge ways are draped on the terrain like every other road.",
                       "Clipped to the site AOI; triangles over the sea (displayed height <= 0.2 m and nearest WBM sample ocean) "
                       "are removed per 3.4 m cell triangle, not along an exact coastline."]}


def collect() -> None:
    """One documented LT_L_SPRD GetFeature over the site AOI; the key exists only inside the request."""
    bbox = json.loads(SITE.read_text())["aoi_bbox"]
    params = {"service": "data", "version": "2.0", "request": "GetFeature", "data": LAYER, "format": "json", "crs": "EPSG:4326",
              "size": 1000, "page": 1, "geomFilter": "BOX({},{},{},{})".format(*bbox)}
    try:
        with urlopen(API + "?" + urlencode({**params, "key": read_key(ROOT / ".env")}), timeout=60) as response:
            payload = json.load(response)["response"]
    except Exception as error:
        raise RuntimeError(f"{LAYER} request failed ({type(error).__name__})") from None
    assert payload["status"] == "OK", payload.get("error")
    # ponytail: single page; the AOI returns 165 segments, page through if it ever exceeds 1000.
    assert payload["page"]["total"] == "1", payload["page"]
    features = payload["result"]["featureCollection"]["features"]
    attributes = sorted({key for f in features for key in f["properties"]})
    assert attributes == ["rn"], f"{LAYER} now returns {attributes}; review width support before use"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "vworld.json").write_text(json.dumps(
        {"layer": LAYER, "source": API, "documentation": DOC, "documented_attributes": ["rn", "ag_geom"],
         "returned_attributes": attributes, "width_attribute": None, "reason": NO_WIDTH, "request": params,
         "acquired_at": datetime.now(timezone.utc).isoformat(), "record": payload["record"],
         "attribution": "공간정보 오픈플랫폼(브이월드) / 행정안전부", "features": features}, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"layer": LAYER, "features": len(features), "attributes": attributes, "width_attribute": None}))


def self_test() -> None:
    site = json.loads(SITE.read_text())
    frame = site["projection"]
    height_at = lambda x, z: 10 + .01 * np.asarray(x, float)
    road = lambda i, highway, lanes, *points: {"id": i, "source_properties": {"highway": highway, "lanes": lanes, "name": None},
                                               "paths": [[[x, 0, z] for x, z in points]]}
    roads = [road("secondary", "secondary", "2", (-200, 0), (200, 0)), road("tertiary", "tertiary", None, (0, 0), (0, 300)),
             road("residential", "residential", None, (-60, 150), (2000, 150))]  # crosses the tertiary and the east AOI edge
    scene = trimesh.Scene(base_frame="world")
    result = add_roads(scene, frame, height_at, roads)
    assert {r["id"]: (r["width_m"], r["width_status"]) for r in result["records"]} == {
        "secondary": (6.5, "lanes_estimated"), "tertiary": (6.0, "class_estimated"), "residential": (4.0, "class_estimated")}
    aoi = aoi_polygon(frame, site["aoi_bbox"])
    inside = shapely.intersection(shapely.LineString([(-60, 150), (2000, 150)]), aoi).length
    assert 1500 < inside < 1900 and np.allclose([r["length_m"] for r in result["records"]], [400, 300, inside])
    nodes = [n for n in scene.graph.nodes if n != "world"]
    assert nodes and all(n.startswith("street_") for n in nodes), nodes
    meshes = {n: scene.geometry[scene.graph[n][1]] for n in scene.graph.nodes_geometry}
    assert set(meshes) == {"street_asphalt", "street_concrete", "street_center_line", "street_edge_line"}
    assert result["triangles"] == sum(len(m.faces) for m in meshes.values())
    for node, mesh in meshes.items():
        v = mesh.vertices
        assert np.allclose(v[:, 1], height_at(v[:, 0], v[:, 2]) + .3, atol=.01), node
        assert mesh.edges_unique_length.max() <= 5, node
        assert np.isfinite(v).all() and np.isfinite(mesh.vertex_normals).all() and (mesh.face_normals[:, 1] > 0).all(), node
        assert shapely.contains_xy(aoi.buffer(.01), v[:, 0], v[:, 2]).all(), node
    # Flat caps and one asphalt union (400 x 6.5 secondary plus the tertiary beyond its 3.25 m half width), markings
    # inlaid: asphalt + lines add up to it exactly, so nothing is stacked (no z-fighting).
    assert np.isclose(sum(meshes[n].area for n in ("street_asphalt", "street_center_line", "street_edge_line")),
                      400 * 6.5 + 6 * (300 - 3.25), rtol=1e-3)
    # Asphalt wins crossings: the residential surface is cut out under the tertiary, never stacked (no z-fighting).
    assert np.isclose(meshes["street_concrete"].area, 4 * (inside - 6), rtol=1e-3)
    centre = meshes["street_concrete"].triangles_center[:, [0, 2]]
    assert not shapely.contains_xy(shapely.LineString([(0, 0), (0, 300)]).buffer(2.99, cap_style="flat"), centre[:, 0], centre[:, 1]).any()
    # Double yellow centre line only on secondary/tertiary: every vertex within 0.3 m of one of those centrelines.
    line = meshes["street_center_line"].vertices[:, [0, 2]]
    near = [shapely.dwithin(shapely.LineString(np.asarray(r["paths"][0])[:, [0, 2]]), shapely.points(line), .3) for r in roads[:2]]
    assert (near[0] | near[1]).all() and near[0].any() and near[1].any()
    # A 0.2 mm cut sliver (float32 GLB positions would collapse it to a zero-area face) is dropped: one cell, two triangles.
    assert len(ribbon(shapely.box(0, 0, CELL + 2e-4, 3), height_at, lambda x, z: np.zeros(len(x), bool)).faces) == 2
    # Sea: displayed height <= 0.2 m over an ocean WBM sample is cut; the same low height over land is kept.
    terrain = np.asarray(site["terrain"]["positions"]).reshape(-1, 3)
    water = np.asarray(site["terrain"]["water_classes"])
    tree = shapely.STRtree(shapely.points(terrain[:, 0], terrain[:, 2]))
    pure = lambda cls: next(p for p, c in zip(terrain, water) if c == cls and (water[tree.query(shapely.Point(p[0], p[2]).buffer(80))] == cls).all()
                            and shapely.contains_xy(aoi.buffer(-80), p[0], p[2]))
    sea, land = pure(1), pure(0)
    low = trimesh.Scene(base_frame="world")
    add_roads(low, frame, lambda x, z: np.zeros(np.shape(x)), [road(f"low{i}", "residential", None, (p[0] - 20, p[2]), (p[0] + 20, p[2]))
                                                               for i, p in enumerate((sea, land))])
    kept = low.geometry["street_concrete"].vertices
    assert np.hypot(kept[:, 0] - land[0], kept[:, 2] - land[2]).max() < 25 and len(kept)
    print(f"PASS {result['count']} roads, {result['triangles']} triangles: width rules, +0.3 m drape, <=5 m edges, "
          "flat-cap union areas, inlaid markings, asphalt-over-concrete cut, centre line classes, street_ nodes, upward normals, AOI and sea clipping")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.collect:
        return collect()
    from build_local import surface_height  # lazy: build_local imports this module
    frame = json.loads((ROOT / "var/rendering/local/manifest.json").read_text())["coordinateFrame"]
    height_at = surface_height(trimesh.load(ROOT / "var/rendering/local/scene.glb", force="scene"))
    scene, start = trimesh.Scene(base_frame="world"), time.perf_counter()
    result = add_roads(scene, frame, height_at)
    seconds = time.perf_counter() - start
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "preview.glb").write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))
    (OUT / "roads.json").write_text(json.dumps({"coordinateFrame": frame, **result}, ensure_ascii=False, indent=2) + "\n")
    drift = {}  # ribbon height at triangle centres vs the terrain there: chord error of the linear ribbon
    for name in ("street_asphalt", "street_concrete"):
        centre = scene.geometry[name].triangles_center
        drift[name] = np.percentile(centre[:, 1] - height_at(centre[:, 0], centre[:, 2]) - .3, [0, 50, 100]).round(3).tolist()
    print(json.dumps({"glb": str((OUT / "preview.glb").relative_to(ROOT)), "sha256": sha(OUT / "preview.glb"),
                      "roads": result["count"], "triangles": result["triangles"], "seconds": round(seconds, 1),
                      "width_status": Counter(r["width_status"] for r in result["records"]),
                      "faces": {n: len(g.faces) for n, g in scene.geometry.items()},
                      "max_edge_m": max(float(g.edges_unique_length.max()) for g in scene.geometry.values()),
                      "centre_offset_min_median_max": drift}))


if __name__ == "__main__":
    main()
