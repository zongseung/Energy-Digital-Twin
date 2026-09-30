#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Sinchang fields, paddies, grass, cadastral crop parcels and estimated field stone walls (밭담).

uv run renderers/twin/groundcover.py --self-test   # no network, flat height_at, synthetic zones and parcels
uv run renderers/twin/groundcover.py --collect     # VWorld LP_PA_CBND_BUBUN over the AOI -> var/rendering/groundcover/parcels.json
uv run renderers/twin/groundcover.py               # groundcover.json + preview.glb on var/rendering/local/scene.glb heights
vworld_key is never printed or stored.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from itertools import pairwise
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
from roads import SITE, aoi_polygon, load_roads, to_scene, width
from vegetation import AOI, LANDCOVER, SEED, axes

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var/rendering/groundcover"
CACHE = OUT / "parcels.json"
CLASSES = {"220": "field", "210": "paddy", "410": "grass", "420": "grass", "250": "other_crop"}
CROPS, WALLED = {"field", "paddy", "other_crop"}, {"전", "과"}
FARM = {"전", "답", "과", "목"}  # 지목 that carries crop rows
CELL = 3.5  # paddy-water grid cut: 4.95 m cell diagonal keeps plan edges <= 5 m
LAYER, API = "LP_PA_CBND_BUBUN", "https://api.vworld.kr/req/data"
DOC = "https://www.vworld.kr/dev/v4dv_2ddataguide2_s002.do?svcIde=cadastral"
TILES = 3  # 3 x 3 AOI boxes of ~1.4 km2; the documented geomFilter area limit is 2 km2
WATER = PBRMaterial(name="groundcover_paddy_water", baseColorFactor=[96, 104, 88, 255], metallicFactor=0, roughnessFactor=.15)


def landcover() -> tuple[list, list]:
    """Stream the 290 MB GeoJSONL; keep groundcover classes that touch the AOI."""
    meta = json.loads(LANDCOVER.with_name(LANDCOVER.name + ".metadata.json").read_text())
    assert sha(LANDCOVER) == meta["sha256"], "landcover.geojsonl does not match its metadata"
    aoi = shapely.box(*AOI)
    with LANDCOVER.open(encoding="utf-8") as lines:
        features = [f for line in lines if '"제주"' in line for f in [json.loads(line)]
                    if f["properties"]["l2_code"] in CLASSES and aoi.intersects(shapely.geometry.shape(f["geometry"]))]
    return features, [{"kind": "landcover_zones", "path": str(LANDCOVER.relative_to(ROOT)),
                       **{key: meta[key] for key in ("sha256", "source", "collected_at", "license")},
                       "attribution": "환경부 토지피복지도 중분류", "classes": CLASSES,
                       "img_dates": sorted({str(f["properties"].get("img_date")) for f in features})}]


def scene_shape(feature: dict, frame: dict):
    return shapely.transform(shapely.geometry.shape(feature["geometry"]), lambda p: to_scene(p[:, 0], p[:, 1], frame))


def tidy(geometry, tolerance: float = .5):
    """Simplify <= tolerance, then snap to the 0.1 m output grid."""
    return shapely.set_precision(shapely.simplify(geometry, tolerance), .1)


def xz(line) -> list:
    return np.round(np.asarray(line.coords), 1).tolist()


def jimok(jibun: str) -> str:
    """지목 is VWorld jibun's one-letter suffix ("2-1대", "산12임"); '' when absent."""
    return jibun.strip()[-1:].strip("0123456789")


def zones(features: list, frame: dict, aoi, built) -> list:
    """[(class, polygon)] in scene x/z: AOI-clipped, buildings (+1 m) removed, tidied; slivers under 1 m2 dropped."""
    out = []
    for f in features:
        if cls := CLASSES.get(f["properties"]["l2_code"]):
            parts = shapely.get_parts(tidy(scene_shape(f, frame).intersection(aoi).difference(built)))
            out += [(cls, p) for p in parts if p.geom_type == "Polygon" and p.area >= 1]
    return out


def cadastre(features: list, frame: dict, aoi, crop, blocked) -> tuple[list, list, Counter]:
    """Crop parcels [{id, jimok, row_angle_deg, ring}], wall polylines and the AOI 지목 distribution."""
    inside = [(f["properties"], s) for f in features if (s := scene_shape(f, frame)).intersects(aoi)]
    parcels = []
    for p, s in inside:
        # ponytail: farm 지목 at least half inside crop zones only (1.5 MB budget); other lots fall back to cell estimates.
        clipped = s.intersection(aoi)
        if jimok(p["jibun"]) not in FARM or shapely.intersection(clipped, crop).area < .5 * clipped.area:
            continue
        _, long, _ = axes(clipped)
        # ponytail: ring simplified <= 2 m (budget) as a row-angle lookup outline; walls and zones carry the edges.
        part = max(shapely.get_parts(tidy(clipped, 2)), key=lambda q: q.area)
        parcels.append({"id": p["pnu"], "jimok": jimok(p["jibun"]), "ring": xz(part.exterior)[:-1],
                        "row_angle_deg": round(math.degrees(math.atan2(long[1], long[0])), 1) % 180})
    # Unclipped boundaries, so the AOI edge is no wall; union on the 0.1 m grid dissolves shared edges.
    # ponytail: neighbours that disagree by more than the grid stay double; snap harder if double walls show.
    walled = [s for p, s in inside if jimok(p["jibun"]) in WALLED]
    lines = shapely.union_all(shapely.boundary(shapely.set_precision(walled, .1))).intersection(aoi).difference(blocked)
    walls = [w for w in shapely.get_parts(tidy(shapely.line_merge(lines), 1)) if w.length >= 2]  # ponytail: < 2 m stubs dropped
    return parcels, walls, Counter(jimok(p["jibun"]) for p, _ in inside)


def water(polygons: list, height_at) -> trimesh.Trimesh | None:
    """Paddy surfaces cut into CELL squares, constrained-Delaunay per piece, every vertex at height_at + 0.15 m."""
    if not polygons:
        return None
    polygon = shapely.union_all(polygons)
    x0, z0, x1, z1 = np.floor(np.asarray(polygon.bounds) / CELL).astype(int)
    xs, zs = (a.ravel() * CELL for a in np.meshgrid(np.arange(x0, x1 + 1), np.arange(z0, z1 + 1)))
    cells = shapely.box(xs, zs, xs + CELL, zs + CELL)
    parts = shapely.get_parts(shapely.intersection(cells[shapely.intersects(polygon, cells)], polygon))
    parts = parts[shapely.get_type_id(parts) == 3]
    tri = shapely.get_coordinates(shapely.get_parts(shapely.constrained_delaunay_triangles(parts))).reshape(-1, 4, 2)[:, :3]
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]  # drop cut slivers under 1 mm high, as roads.ribbon does (float32 GLB)
    twice_area = np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    flat = tri[twice_area / np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2).max(axis=1) > 1e-3].reshape(-1, 2)
    mesh = trimesh.Trimesh(np.column_stack((flat[:, 0], height_at(flat[:, 0], flat[:, 1]) + .15, flat[:, 1])),
                           np.arange(len(flat)).reshape(-1, 3))  # GEOS clockwise (x, z) triangles face +y; merges cut vertices
    mesh.visual = TextureVisuals(material=WATER)
    return normals(mesh)


def load_parcels(cache: Path = CACHE, fetch=None) -> tuple[list, dict]:
    """Cadastral features from the cache (fetched once if missing); [] and the reason when that fails."""
    try:
        data = json.loads(cache.read_text()) if cache.is_file() else (fetch or collect)()
    except Exception as error:  # network, key or cache failure only
        return [], {"kind": "cell50_estimated", "layer": LAYER,
                    "reason": f"VWorld {LAYER} unavailable ({type(error).__name__}); no parcels or walls, row angles estimated"}
    return data["features"], {"kind": "vworld_cadastral", "layer": LAYER, "source": API, "documentation": DOC,
                              "path": str(cache.relative_to(ROOT)), "sha256": sha(cache), "acquired_at": data["acquired_at"],
                              "feature_count": len(data["features"]), "attribution": data["attribution"]}


def add_groundcover(scene: trimesh.Scene, frame: dict, height_at, features: list | None = None,
                    parcels: list | None = None, roads: list | None = None) -> dict:
    site = json.loads(SITE.read_text())
    features, sources = landcover() if features is None else (features, [])
    parcels, parcel_source = load_parcels() if parcels is None else (parcels, {"kind": "caller_supplied"})
    roads, road_source = load_roads(site, frame) if roads is None else (roads, {"kind": "caller_supplied"})
    from build_local import footprint_polygons  # lazy: build_local imports this module
    built = shapely.union_all([p for b in site["buildings"] for p in footprint_polygons(b)]).buffer(1, join_style="mitre")
    aoi = aoi_polygon(frame, AOI)
    zoned = zones(features, frame, aoi, built)
    road_area = shapely.union_all([shapely.LineString(line).buffer(width(r)[0] / 2 + .5) for r in roads for line in r["lines"]])
    plots, walls, jimoks = cadastre(parcels, frame, aoi, shapely.union_all([p for c, p in zoned if c in CROPS]), built | road_area)
    mesh = water([p for c, p in zoned if c == "paddy"], height_at)
    if mesh:
        scene.add_geometry(mesh, geom_name="groundcover_paddy_water", node_name="groundcover_paddy_water")
    area, row_source = Counter(), "cadastral" if plots else "cell50_estimated"
    for c, p in zoned:
        area[c] += p.area
    counts = {"zones": dict(Counter(c for c, _ in zoned)), "zone_area_m2": {c: round(a) for c, a in area.items()},
              "parcels": len(plots), "parcel_jimok": dict(Counter(p["jimok"] for p in plots)), "cadastral_aoi_jimok": dict(jimoks),
              "walls": len(walls), "wall_length_m": round(sum(w.length for w in walls)), "paddy_water_triangles": len(mesh.faces) if mesh else 0}
    return {"zones": [{"class": c, "rings": [xz(r)[:-1] for r in (p.exterior, *p.interiors)]} for c, p in zoned],
            "parcels": plots, "walls": [xz(w) for w in walls], "row_source": row_source, "seed": SEED, "counts": counts,
            "sources": [*sources, parcel_source, {**road_source, "use": "wall mask: NGII centreline width / 2 + 0.5 m"}],
            "limits": ["Zones are actual 환경부 토지피복지도 중분류 polygons (2023 aerial orthophoto): 220 field, 210 paddy, 410/420 grass, "
                       "250 other crop; AOI-clipped, building footprints + 1 m removed, simplified <= 0.5 m, coordinates rounded to 0.1 m. "
                       "Rings are open (the closing point is not repeated).",
                       "Parcels are VWorld 연속지적도 (LP_PA_CBND_BUBUN) lots of farm 지목 (전/답/과/목) with at least half their "
                       "AOI-clipped area in field/other crop/paddy zones (size budget); crop-zone ground in other lots (임, 도, 대, "
                       "묘 ...) has no parcel. Ring: a row-angle lookup outline (largest part only, holes dropped, simplified "
                       "<= 2 m). 지목 is the legal category (jibun suffix), not the current use. row_angle_deg is the long axis of "
                       "the minimum rotated rectangle (degrees from +x toward +z, 0-180): crop row direction is estimated, not observed."
                       if row_source == "cadastral" else
                       f"No cadastral parcels ({parcel_source.get('reason', 'none in the AOI')}): row_source cell50_estimated, "
                       "parcels and walls empty.",
                       "Walls are an estimate: real 밭담 positions are unknown. They follow every 전/과 parcel boundary once "
                       "(shared edges dissolved on a 0.1 m grid), clipped to the AOI, cut 0.5 m clear of NGII road ribbons and 1 m "
                       "clear of buildings, simplified <= 1 m, pieces under 2 m dropped; gates, gaps and walls around other lots "
                       "are not modelled.",
                       f"Paddy water is flat per vertex at displayed terrain + 0.15 m on a {CELL} m grid cut (plan edges <= 5 m); "
                       "the coarse DSM can put it above or below the actual paddy floor."]}


def collect() -> dict:
    """Documented LP_PA_CBND_BUBUN GetFeature pages over TILES x TILES AOI boxes; the key exists only inside the request."""
    frame = json.loads(SITE.read_text())["projection"]
    lon, lat = np.linspace(AOI[0], AOI[2], TILES + 1), np.linspace(AOI[1], AOI[3], TILES + 1)
    boxes = [(w, s, e, n) for s, n in pairwise(lat.tolist()) for w, e in pairwise(lon.tolist())]
    assert max(aoi_polygon(frame, b).area for b in boxes) < 2e6, "tile exceeds the documented 2 km2 geomFilter limit"
    params = {"service": "data", "version": "2.0", "request": "GetFeature", "data": LAYER, "format": "json", "crs": "EPSG:4326", "size": 1000}
    key, features, records = read_key(ROOT / ".env"), {}, []
    for box in boxes:
        query, page, pages, got = {**params, "geomFilter": "BOX({},{},{},{})".format(*box)}, 1, 1, []
        while page <= pages:
            try:
                with urlopen(API + "?" + urlencode({**query, "page": page, "key": key}), timeout=60) as response:
                    payload = json.load(response)["response"]
            except Exception as error:
                raise RuntimeError(f"{LAYER} page {page} failed ({type(error).__name__})") from None
            if payload["status"] == "NOT_FOUND":  # a tile with no parcels (sea)
                break
            assert payload["status"] == "OK", payload.get("error")
            got += payload["result"]["featureCollection"]["features"]
            page, pages = page + 1, int(payload["page"]["total"])
        assert payload["status"] == "NOT_FOUND" or len(got) == int(payload["record"]["total"]), box
        records.append({"box": box, "features": len(got)})
        features |= {f["id"]: f for f in got}  # parcels crossing tile edges come back once per tile
    data = {"layer": LAYER, "source": API, "documentation": DOC, "request": params, "tiles": records,
            "acquired_at": datetime.now(timezone.utc).isoformat(), "attribution": "공간정보 오픈플랫폼(브이월드) / 국토교통부",
            "features": list(features.values())}
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False) + "\n")
    print(json.dumps({"layer": LAYER, "features": len(features), "tiles": records, "cache": str(CACHE.relative_to(ROOT))}))
    return data


def self_test() -> None:
    site = json.loads(SITE.read_text())
    frame = site["projection"]
    (e0, n0), aoi = frame["origin_easting_northing"], aoi_polygon(frame, AOI)
    height_at = lambda x, z: np.full(np.shape(x), 10.0)  # flat test terrain

    def box(x0, z0, x1, z1) -> dict:  # scene-metre box as a lon/lat MultiPolygon
        lon, lat = project_crs(frame["horizontal_crs"], "EPSG:4326", np.array([x0, x1, x1, x0, x0]) + e0, n0 - np.array([z0, z0, z1, z1, z0]))
        return {"type": "MultiPolygon", "coordinates": [[list(map(list, zip(lon, lat)))]]}
    zone = lambda code, *b: {"properties": {"l2_code": code}, "geometry": box(*b)}
    parcel = lambda pnu, jibun, *b: {"id": f"LP_PA_CBND_BUBUN.{pnu}", "properties": {"pnu": pnu, "jibun": jibun}, "geometry": box(*b)}
    from build_local import footprint_polygons
    houses = [p for b in site["buildings"] for p in footprint_polygons(b)]
    built = shapely.union_all(houses)
    house = min(houses, key=lambda p: p.centroid.distance(shapely.Point(0, 0)))  # a village house near the origin
    cx, cz = house.centroid.x, house.centroid.y
    west = to_scene([AOI[0]], [33.343], frame)[0, 0]
    features = [zone("220", -1400, -1000, -1200, -900),  # field under parcels P1-P3
                zone("210", -1200, -800, -1140, -760),  # paddy 60 x 40
                zone("420", 0, 0, 100, 100),  # grass over village houses
                zone("410", west - 50, 0, west + 50, 100),  # grass straddling the AOI west edge
                zone("250", -1000, -800, -950, -750),  # other crop 50 x 50
                zone("310", 400, 0, 500, 100)]  # forest: not groundcover
    parcels = [parcel("P1", "1-1전", -1400, -1000, -1300, -960),  # 100 x 40: rows along x
               parcel("P2", "2전", -1300, -1000, -1260, -900),  # 40 x 100: rows along z
               parcel("P3", "산3과", -1400, -960, -1300, -900),
               parcel("P4", "4대", -1100, -1000, -1000, -900),  # house lot, no crop zone
               parcel("P5", "5답", -1200, -800, -1140, -760),  # paddy parcel: no walls
               parcel("P6", "6전", cx - 50, cz, cx + 50, cz + 40),  # field lot whose north edge crosses a house
               parcel("P7", "7도", -1260, -1000, -1250, -900),  # road lot inside the field zone: not a crop parcel
               parcel("P8", "8전", -1220, -1000, -1150, -960)]  # field lot 29% inside crop zones: walls, no parcel
    assert shapely.LineString([(cx - 50, cz), (cx + 50, cz)]).intersects(house)
    roads = [{"width": "6", "lanes": None, "class": "RDD000", "lines": [np.array([[-1500., -930.], [-1100., -930.]])]}]
    scene = trimesh.Scene(base_frame="world")
    block = add_groundcover(scene, frame, height_at, features, parcels, roads)
    json.dumps(block)
    assert set(scene.graph.nodes_geometry) == {"groundcover_paddy_water"} and block["seed"] == SEED == 20260930

    # Zones: classes mapped, AOI-clipped, buildings + 1 m removed, 0.1 m grid, open rings (outer first, then holes).
    polygons = [(z["class"], shapely.Polygon(z["rings"][0], z["rings"][1:])) for z in block["zones"]]
    area = {cls: sum(p.area for c, p in polygons if c == cls) for cls, _ in polygons}
    assert np.allclose([area["field"], area["paddy"], area["other_crop"]], [20000, 2400, 2500], atol=1), area
    village = shapely.union_all([p for c, p in polygons if c == "grass" and p.centroid.x > 0])
    assert 10000 - 169 - 500 < village.area < 10000 - 169 and village.intersection(built.buffer(.4)).area < .01, village.area
    assert 4000 < area["grass"] - village.area < 6000, area  # west-edge grass: about half inside
    assert all(p.is_valid for _, p in polygons)
    for z in block["zones"]:
        c = np.concatenate(z["rings"])
        assert np.allclose(c, np.round(c, 1)) and shapely.contains_xy(aoi.buffer(.1), *c.T).all()
        assert all(ring[0] != ring[-1] for ring in z["rings"])

    # Parcels: farm 지목 at least half inside field/other_crop/paddy zones (not P4 house lot, P7 road, P8 29%), rows along the long side.
    plots = {p["id"]: p for p in block["parcels"]}
    assert {i: p["jimok"] for i, p in plots.items()} == {"P1": "전", "P2": "전", "P3": "과", "P5": "답"}, plots.keys()
    angle = {i: p["row_angle_deg"] for i, p in plots.items()}
    assert min(angle["P1"], 180 - angle["P1"]) < .1 and abs(angle["P2"] - 90) < .1 and 0 <= angle["P1"] < 180, angle
    assert len(plots["P2"]["ring"]) == 4 and np.isclose(shapely.Polygon(plots["P2"]["ring"]).area, 4000, atol=1)
    assert block["row_source"] == "cadastral" and jimok("산12-3 임") == "임" and jimok("123") == ""

    # Walls: 전/과 boundaries once each (680 m for P1-P3, 220 m for P8), cut 0.5 m clear of the 6 m road and 1 m clear of houses.
    walls = shapely.MultiLineString(block["walls"])
    near = sum(shapely.LineString(w).length for w in block["walls"] if max(x for x, _ in w) < -1000)
    assert abs(near - (680 - 3 * 2 * 3.5 + 220)) < .5, near
    assert walls.intersection(shapely.LineString(roads[0]["lines"][0]).buffer(3.45)).length < .01
    assert walls.intersection(built.buffer(.45)).length < .01 and walls.intersection(shapely.box(cx - 51, cz - 1, cx + 51, cz + 41)).length > 200
    assert shapely.contains_xy(aoi.buffer(.1), *np.concatenate(block["walls"]).T).all()
    assert block["counts"]["walls"] == len(block["walls"]) and abs(block["counts"]["wall_length_m"] - walls.length) < 1

    # Paddy water: 60 x 40 m at height_at + 0.15, plan edges <= 5 m, upward, muddy semi-reflective PBR.
    water = scene.geometry["groundcover_paddy_water"]
    assert np.allclose(water.vertices[:, 1], 10.15) and np.isclose(water.area, 2400, rtol=1e-3)
    assert np.linalg.norm(np.diff(water.triangles[:, [0, 1, 2, 0]][:, :, [0, 2]], axis=1), axis=2).max() <= 5
    assert (water.face_normals[:, 1] > .99).all() and np.isfinite(water.vertex_normals).all()
    material = water.visual.material
    assert material.metallicFactor == 0 and material.roughnessFactor == .15 and block["counts"]["paddy_water_triangles"] == len(water.faces)

    # Cadastral layer unavailable: cell50_estimated rows, no parcels or walls, reason recorded.
    missing, source = load_parcels(Path("/nonexistent/parcels.json"), lambda: 1 / 0)
    assert missing == [] and "ZeroDivisionError" in source["reason"]
    fallback = add_groundcover(trimesh.Scene(base_frame="world"), frame, height_at, features, missing, roads)
    assert fallback["row_source"] == "cell50_estimated" and fallback["parcels"] == fallback["walls"] == [], fallback["row_source"]
    print(f"PASS groundcover {block['counts']}")


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
    block = add_groundcover(scene, frame, height_at)
    seconds = round(time.perf_counter() - start, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "preview.glb").write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))
    text = json.dumps(block, ensure_ascii=False, separators=(",", ":"))
    (OUT / "groundcover.json").write_text(text + "\n")
    print(json.dumps({"json_bytes": len(text.encode()), "glb_bytes": (OUT / "preview.glb").stat().st_size, "seconds": seconds,
                      "row_source": block["row_source"], **block["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
