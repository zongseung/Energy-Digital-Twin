#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Sinchang road ribbons from NGII centrelines (VWorld LT_L_N3A0020000), draped on the displayed terrain.

uv run renderers/twin/roads.py --self-test   # no network, planar height_at
uv run renderers/twin/roads.py --collect     # LT_L_N3A0020000 over the AOI -> var/rendering/roads/ngii.json
uv run renderers/twin/roads.py               # var/rendering/roads/preview.glb on var/rendering/local/scene.glb heights
vworld_key is never printed or stored.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
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
CACHE = OUT / "ngii.json"
BUDGET = 300_000  # triangles for the whole roads layer; edge lines go first
CELL = 6.8  # grid-cut square; its 9.6 m diagonal keeps triangle edges <= 10 m in 3D up to 29% slopes
# class_estimated fallbacks: OSM highway (fallback source) and NGII rddv (1/5,000 codes, 연속수치지형도 데이터 설명서 5.1.1 p.13-14)
CLASS_WIDTH = {"secondary": 7.0, "tertiary": 6.0, "secondary_link": 5.0, "tertiary_link": 5.0, "residential": 4.0,
               "RDD001": 7.0, "RDD002": 7.0, "RDD003": 7.0, "RDD008": 5.0, "RDD009": 4.0, "RDD000": 4.0}
# pvqt at 1/5,000 is only RDQ000 미분류, RDQ005 비포장, RDQ006 포장: unpaved is known, paved material is not.
PVQT = {"RDQ005": "unpaved"}
MATERIALS = {name: PBRMaterial(name=f"street_{name}", baseColorFactor=color, metallicFactor=0, roughnessFactor=rough)
             for name, color, rough in [("asphalt", [62, 64, 66, 255], .9), ("concrete", [158, 157, 150, 255], .85),
                                        ("unpaved", [139, 118, 92, 255], 1), ("center_line", [232, 178, 32, 255], .6),
                                        ("edge_line", [236, 236, 230, 255], .6)]}
LAYER, API = "LT_L_N3A0020000", "https://api.vworld.kr/req/data"
DOC = "https://www.vworld.kr/dev/v4dv_2ddataguide2_s002.do?svcIde=n3a0020000"
CODES = ("국토지리정보원, 연속수치지형도 데이터 설명서 Ver 5.1.1 (2014-12-30), 도로중심선 N3L_A0020000 pp.13-14, "
         "https://www.ngii.go.kr/other/file_down.do?sq=58476")
ATTRIBUTES = {"id": "ufid", "name": "name", "width": "rvwd", "lanes": "rdln", "class": "rddv", "pavement": "pvqt"}


def to_scene(lon, lat, frame: dict) -> np.ndarray:
    east, north = project_crs("EPSG:4326", frame["horizontal_crs"], lon, lat)
    e0, n0 = frame["origin_easting_northing"]
    return np.column_stack((np.asarray(east) - e0, n0 - np.asarray(north)))


def aoi_polygon(frame: dict, bbox: list[float]) -> shapely.Polygon:
    ring = np.asarray(shapely.segmentize(shapely.box(*bbox), 1e-3).exterior.coords)
    return shapely.Polygon(to_scene(ring[:, 0], ring[:, 1], frame))


def ngii_road(feature: dict, frame: dict) -> dict:
    """Provider strings kept as-is; width()/pavement() parse them."""
    assert feature["geometry"]["type"] == "MultiLineString"
    p = feature["properties"]
    return {"id": p["ufid"], "name": p["name"] or None, "width": p["rvwd"], "lanes": p["rdln"], "class": p["rddv"], "pvqt": p["pvqt"],
            "lines": [to_scene(*np.asarray(line, float).T, frame) for line in feature["geometry"]["coordinates"]]}


def osm_road(road: dict) -> dict:
    p = road["source_properties"]
    return {"id": road["id"], "name": p.get("name"), "width": None, "lanes": p.get("lanes"), "class": p["highway"], "pvqt": None,
            "lines": [np.asarray(path)[:, [0, 2]] for path in road["paths"]]}


def load_roads(site: dict, frame: dict, cache: Path = CACHE, fetch=None) -> tuple[list, dict]:
    """NGII centrelines from the cache (fetched once if missing); OSM site paths only when that fails."""
    try:
        data = json.loads(cache.read_text()) if cache.is_file() else (fetch or collect)()
    except Exception as error:  # network, key or cache failure only; parsing errors below stay loud
        return [osm_road(r) for r in site["roads"]], {"kind": "osm_fallback", "path": str(SITE.relative_to(ROOT)), "sha256": sha(SITE),
                                                       "reason": f"NGII {LAYER} unavailable ({type(error).__name__}); OSM centrelines used"}
    return [ngii_road(f, frame) for f in data["features"]], {
        "kind": "ngii_centerlines", "layer": LAYER, "source": API, "documentation": DOC, "code_documentation": CODES,
        "path": str(cache.relative_to(ROOT)) if cache.is_relative_to(ROOT) else str(cache), "sha256": sha(cache),
        "acquired_at": data["acquired_at"], "feature_count": len(data["features"]), "attribution": data["attribution"]}


def number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def width(road: dict) -> tuple[float, str]:
    """NGII rvwd when finite and > 0, else lanes x 3.25 m, else the class default."""
    provider, lanes = number(road["width"]), number(road["lanes"])
    return ((provider, "ngii_width") if math.isfinite(provider) and provider > 0 else
            (lanes * 3.25, "lanes_estimated") if math.isfinite(lanes) and lanes > 0 else (CLASS_WIDTH[road["class"]], "class_estimated"))


def pavement(road: dict, width_m: float) -> tuple[str, str]:
    known = PVQT.get(road["pvqt"])
    return (known, "ngii_pvqt") if known else ("asphalt" if width_m >= 6 else "concrete", "width_estimated")


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


def clearance(mesh: trimesh.Trimesh, height_at) -> float:
    """Lowest ribbon height above the terrain at triangle centres and edge midpoints (vertices are +0.3 m by construction)."""
    t = mesh.triangles
    samples = np.concatenate([t.mean(axis=1), ((t + np.roll(t, 1, axis=1)) / 2).reshape(-1, 3)])
    return float((samples[:, 1] - height_at(samples[:, 0], samples[:, 2])).min())


def add_roads(scene: trimesh.Scene, frame: dict, height_at, roads: list | None = None, budget: int = BUDGET) -> dict:
    site = json.loads(SITE.read_text())
    assert all(site["projection"][key] == frame[key] for key in
               ("horizontal_crs", "vertical_crs", "origin_easting_northing", "axes", "scale"))
    roads, source = load_roads(site, frame) if roads is None else (roads, {"kind": "caller_supplied"})
    aoi = aoi_polygon(frame, site["aoi_bbox"])
    terrain = np.asarray(site["terrain"]["positions"]).reshape(-1, 3)
    ocean = np.asarray(site["terrain"]["water_classes"]) == 1
    tree = shapely.STRtree(shapely.points(terrain[:, 0], terrain[:, 2]))
    sea = lambda x, z: (height_at(x, z) <= .2) & ocean[tree.query_nearest(shapely.points(x, z), all_matches=False)[1]]
    surfaces, center, records = {"asphalt": [], "concrete": [], "unpaved": []}, [], []
    for road in roads:
        width_m, status = width(road)
        kind, kind_status = pavement(road, width_m)
        lanes = number(road["lanes"])
        lanes = int(lanes) if math.isfinite(lanes) and lanes > 0 else None
        lines = [shapely.LineString(line) for line in road["lines"]]
        # ponytail: round caps close the bends between NGII's short segments; they also round dead ends.
        surfaces[kind].extend(shapely.buffer(lines, width_m / 2))
        if kind != "unpaved" and ((lanes or 0) >= 2 or width_m >= 6):  # double yellow: two 0.15 m lines, 0.15 m apart
            center += [shapely.offset_curve(line, side * .15).buffer(.075, cap_style="flat") for line in lines for side in (-1, 1)]
        records.append({"id": road["id"], "name": road["name"], "width_m": width_m, "width_status": status, "lanes": lanes,
                        "pavement": kind, "pavement_status": kind_status,
                        "length_m": float(sum(line.intersection(aoi).length for line in lines))})
    # One union per surface; asphalt > concrete > unpaved at crossings (lower ones cut out), markings inlaid in the paved
    # surfaces at the same +0.3 m: nothing is stacked, so nothing z-fights. Markings come from the unclipped surfaces so
    # the AOI cut does not draw an edge line across a road.
    asphalt = shapely.union_all(surfaces["asphalt"])
    concrete = shapely.union_all(surfaces["concrete"]).difference(asphalt)
    unpaved = shapely.union_all(surfaces["unpaved"]).difference(asphalt | concrete)
    center = shapely.union_all(center).intersection(asphalt | concrete)
    edge = asphalt.buffer(-.3).boundary.buffer(.075, cap_style="flat").intersection(asphalt).difference(center)
    ribbons = lambda layers: {name: mesh for name, polygon in layers.items()
                              if (mesh := ribbon(polygon.intersection(aoi), height_at, sea)) is not None}
    meshes = ribbons({"asphalt": asphalt.difference(center | edge), "concrete": concrete.difference(center), "unpaved": unpaved,
                      "center_line": center, "edge_line": edge})
    if sum(len(m.faces) for m in meshes.values()) > budget:  # drop edge lines; their strips go back to the asphalt
        del meshes["edge_line"]
        meshes |= ribbons({"asphalt": asphalt.difference(center)})
    for name, mesh in meshes.items():
        mesh.visual = TextureVisuals(material=MATERIALS[name])
        scene.add_geometry(mesh, geom_name=f"street_{name}", node_name=f"street_{name}")
    faces = {f"street_{name}": len(mesh.faces) for name, mesh in meshes.items()}
    return {"count": len(records), "records": records, "triangles": sum(faces.values()), "meshes": faces, "budget": budget,
            "edge_lines": "edge_line" in meshes,
            "min_clearance_sampled_m": min(clearance(mesh, height_at) for mesh in meshes.values()),
            "source": source["kind"], "source_attributes": ATTRIBUTES if source["kind"] == "ngii_centerlines" else None,
            "pavement_mapping": {"RDQ005": "unpaved (ngii_pvqt)", "RDQ006/RDQ000/other": "asphalt if width >= 6 m else concrete (width_estimated)"},
            "sources": [source],
            "limits": ["Centrelines, widths (rvwd), lanes (rdln) and unpaved status (pvqt RDQ005) are NGII 1:5,000 continuous digital "
                       "topographic map attributes, not a field survey; rvwd may include sidewalks/medians. Missing widths fall back "
                       "to lanes x 3.25 m, then class defaults (estimated).",
                       "Paved material is not coded at 1:5,000 (RDQ006 포장), so asphalt (>= 6 m) versus grey concrete (< 6 m) is "
                       "estimated; the double yellow centre (paved, lanes >= 2 or width >= 6 m) and white edge lines are display estimates.",
                       "Ribbons sit 0.3 m above the displayed terrain at every vertex with <= 10 m triangle edges; between vertices they "
                       "are linear, so terrain creases lower or raise them. min_clearance_sampled_m samples triangle centres and edge "
                       "midpoints only; the exact minimum (at terrain-edge crossings) can be a few centimetres lower. Markings are "
                       "inlaid flush in the surface, not 0.05 m above it.",
                       "Short NGII segments are buffered with round caps, so dead ends are rounded; bridges are draped on the terrain.",
                       "Clipped to the site AOI; triangles over the sea (displayed height <= 0.2 m and nearest WBM sample ocean) "
                       "are removed per 6.8 m cell triangle, not along an exact coastline."]}


def collect() -> dict:
    """Documented LT_L_N3A0020000 GetFeature pages over the site AOI; the key exists only inside the request."""
    bbox = json.loads(SITE.read_text())["aoi_bbox"]
    params = {"service": "data", "version": "2.0", "request": "GetFeature", "data": LAYER, "format": "json", "crs": "EPSG:4326",
              "size": 1000, "geomFilter": "BOX({},{},{},{})".format(*bbox)}
    key, features, page, pages = read_key(ROOT / ".env"), [], 1, 1
    while page <= pages:
        try:
            with urlopen(API + "?" + urlencode({**params, "page": page, "key": key}), timeout=60) as response:
                payload = json.load(response)["response"]
        except Exception as error:
            raise RuntimeError(f"{LAYER} page {page} failed ({type(error).__name__})") from None
        assert payload["status"] == "OK", payload.get("error")
        features += payload["result"]["featureCollection"]["features"]
        page, pages = page + 1, int(payload["page"]["total"])
    assert len(features) == int(payload["record"]["total"]) == len({f["id"] for f in features})
    data = {"layer": LAYER, "source": API, "documentation": DOC, "code_documentation": CODES, "request": params,
            "acquired_at": datetime.now(timezone.utc).isoformat(), "record": payload["record"],
            "attribution": "공간정보 오픈플랫폼(브이월드) / 국토지리정보원", "features": features}
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False) + "\n")
    print(json.dumps({"layer": LAYER, "features": len(features), "pages": pages, "cache": str(CACHE.relative_to(ROOT))}))
    return data


def self_test() -> None:
    site = json.loads(SITE.read_text())
    frame = site["projection"]
    height_at = lambda x, z: 10 + .01 * np.asarray(x, float)
    road = lambda i, width, lanes, cls, pvqt, *points: {"id": i, "name": None, "width": width, "lanes": lanes, "class": cls,
                                                         "pvqt": pvqt, "lines": [np.asarray(points, float)]}
    roads = [road("wide", "7.9", "2.000000000000000", "RDD003", "RDQ006", (-200, 0), (200, 0)),
             road("lanes", "", "2", "RDD000", "RDQ006", (0, 0), (0, 300)),  # T into the wide road
             road("class", "inf", None, "RDD009", "RDQ006", (-60, 150), (2000, 150)),  # crosses "lanes" and the east AOI edge
             road("unpaved", "5", "2", "RDD000", "RDQ005", (-100, -100), (-100, 100)),  # crosses the wide road
             road("narrow", "5", "2", "RDD000", "RDQ006", (-200, 60), (-20, 60))]  # 2-lane concrete, crosses the unpaved road
    scene = trimesh.Scene(base_frame="world")
    result = add_roads(scene, frame, height_at, roads)
    assert {r["id"]: (r["width_m"], r["width_status"], r["lanes"], r["pavement"]) for r in result["records"]} == {
        "wide": (7.9, "ngii_width", 2, "asphalt"), "lanes": (6.5, "lanes_estimated", 2, "asphalt"),
        "class": (4.0, "class_estimated", None, "concrete"), "unpaved": (5.0, "ngii_width", 2, "unpaved"),
        "narrow": (5.0, "ngii_width", 2, "concrete")}
    aoi = aoi_polygon(frame, site["aoi_bbox"])
    lines = {r["id"]: shapely.LineString(r["lines"][0]) for r in roads}
    inside = lines["class"].intersection(aoi).length
    assert 1500 < inside < 1900 and np.allclose([r["length_m"] for r in result["records"]], [400, 300, inside, 200, 180])
    nodes = [n for n in scene.graph.nodes if n != "world"]
    assert nodes and all(n.startswith("street_") for n in nodes), nodes
    meshes = {n: scene.geometry[scene.graph[n][1]] for n in scene.graph.nodes_geometry}
    assert set(meshes) == {"street_asphalt", "street_concrete", "street_unpaved", "street_center_line", "street_edge_line"}
    assert result["triangles"] == sum(len(m.faces) for m in meshes.values()) == sum(result["meshes"].values())
    for node, mesh in meshes.items():
        v = mesh.vertices
        assert np.allclose(v[:, 1], height_at(v[:, 0], v[:, 2]) + .3, atol=.01), node
        assert mesh.edges_unique_length.max() <= 10, node
        assert np.isfinite(v).all() and np.isfinite(mesh.vertex_normals).all() and (mesh.face_normals[:, 1] > 0).all(), node
        assert shapely.contains_xy(aoi.buffer(.01), v[:, 0], v[:, 2]).all(), node
    assert np.isclose(result["min_clearance_sampled_m"], .3, atol=.01)  # planar terrain: the ribbon is exactly 0.3 m above
    # Round-capped buffers, one surface per pavement, markings inlaid: all meshes add up to the union exactly (nothing stacked).
    union = shapely.union_all([lines[i].buffer(w / 2) for i, w in (("wide", 7.9), ("lanes", 6.5), ("class", 4), ("unpaved", 5), ("narrow", 5))])
    assert np.isclose(sum(m.area for m in meshes.values()), union.intersection(aoi).area, rtol=1e-3)
    # Higher surfaces win crossings: no concrete under the "lanes" asphalt, no unpaved under the wide asphalt or the concrete.
    for node, over in (("street_concrete", lines["lanes"].buffer(3.24)), ("street_unpaved", lines["wide"].buffer(3.94)),
                       ("street_unpaved", lines["narrow"].buffer(2.49))):
        centre = meshes[node].triangles_center[:, [0, 2]]
        assert not shapely.contains_xy(over, centre[:, 0], centre[:, 1]).any(), node
    # Double yellow only on paved roads with lanes >= 2 or width >= 6 m: every vertex within 0.3 m of "wide", "lanes" or "narrow".
    line = shapely.points(meshes["street_center_line"].vertices[:, [0, 2]])
    near = np.array([shapely.dwithin(lines[i], line, .3) for i in ("wide", "lanes", "narrow")])
    assert near.any(axis=0).all() and near.any(axis=1).all()
    # Over budget: edge lines are dropped and their strips go back to asphalt.
    lean = trimesh.Scene(base_frame="world")
    small = add_roads(lean, frame, height_at, roads, budget=result["triangles"] - 1)
    assert "street_edge_line" not in lean.graph.nodes and small["edge_lines"] is False and result["edge_lines"] is True
    assert np.isclose(lean.geometry["street_asphalt"].area, meshes["street_asphalt"].area + meshes["street_edge_line"].area, rtol=1e-3)
    # NGII feature -> scene record: the frame origin lon/lat lands on (0, 0); lanes/width/name parsed from the provider strings.
    lon, lat = frame["origin_lon_lat"]
    feature = {"geometry": {"type": "MultiLineString", "coordinates": [[[lon, lat], [lon + .001, lat + .001]]]},
               "properties": {"ufid": "U1", "name": "", "rvwd": "7.9", "rdln": "2.000000000000000", "rddv": "RDD003", "pvqt": "RDQ006"}}
    converted = ngii_road(feature, frame)
    assert {k: converted[k] for k in ("id", "name", "width", "lanes", "class", "pvqt")} == {
        "id": "U1", "name": None, "width": "7.9", "lanes": "2.000000000000000", "class": "RDD003", "pvqt": "RDQ006"}
    (x0, z0), (x1, z1) = converted["lines"][0]
    assert np.allclose([x0, z0], 0, atol=.01) and 94 < x1 < 98 and -111 < z1 < -106  # east +x, north -z (1.5° grid convergence)
    # NGII unavailable (no cache, fetch fails): OSM centrelines, recorded as a fallback.
    fallback, source = load_roads(site, frame, Path("/nonexistent/ngii.json"), lambda: 1 / 0)
    assert len(fallback) == len(site["roads"]) and source["kind"] == "osm_fallback" and "ZeroDivisionError" in source["reason"]
    assert width(fallback[0])[1] in ("lanes_estimated", "class_estimated")
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
    add_roads(low, frame, lambda x, z: np.zeros(np.shape(x)), [road(f"low{i}", "4", None, "RDD000", "RDQ006", (p[0] - 20, p[2]), (p[0] + 20, p[2]))
                                                               for i, p in enumerate((sea, land))])
    kept = low.geometry["street_concrete"].vertices
    assert np.hypot(kept[:, 0] - land[0], kept[:, 2] - land[2]).max() < 25 and len(kept)
    print(f"PASS {result['count']} roads, {result['triangles']} triangles: NGII/lanes/class widths and pavement, +0.3 m drape and "
          "clearance, <=10 m edges, round-cap union areas, inlaid markings, surface priority, centre-line rule, edge-line budget drop, "
          "NGII parsing, OSM fallback, street_ nodes, upward normals, AOI and sea clipping")


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
    print(json.dumps({"glb": str((OUT / "preview.glb").relative_to(ROOT)), "sha256": sha(OUT / "preview.glb"), "source": result["source"],
                      "roads": result["count"], "length_km": round(sum(r["length_m"] for r in result["records"]) / 1000, 2),
                      "width_status": Counter(r["width_status"] for r in result["records"]),
                      "pavement": Counter(f"{r['pavement']}/{r['pavement_status']}" for r in result["records"]),
                      "meshes": result["meshes"], "triangles": result["triangles"], "edge_lines": result["edge_lines"],
                      "min_clearance_sampled_m": round(result["min_clearance_sampled_m"], 3), "seconds": round(seconds, 1),
                      "max_edge_m": round(max(float(g.edges_unique_length.max()) for g in scene.geometry.values()), 2)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
