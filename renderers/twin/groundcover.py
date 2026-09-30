#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Sinchang fields, paddies, grass, cadastral crop parcels and estimated field stone walls (밭담).

uv run renderers/twin/groundcover.py --self-test   # no network, synthetic zones and parcels
uv run renderers/twin/groundcover.py --collect     # VWorld LP_PA_CBND_BUBUN over the AOI -> var/rendering/groundcover/parcels.json
uv run renderers/twin/groundcover.py               # var/rendering/groundcover/groundcover.json (data only; the web viewer draws it)
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
from rasterio.warp import transform as project_crs

from build import sha
from prepare_imagery import read_key
from roads import SITE, aoi_polygon, load_roads, to_scene, width
from vegetation import AOI, LANDCOVER, SEED, axes

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var/rendering/groundcover"
CACHE = OUT / "parcels.json"
CLASSES = {"220": "field", "210": "paddy", "410": "grass", "420": "grass", "250": "other_crop"}
CROPS, WALLED = {"field", "paddy", "other_crop"}, {"전", "과"}
FARM = {"전", "답", "과", "목"}  # 지목 that carries crop rows
LAYER, API = "LP_PA_CBND_BUBUN", "https://api.vworld.kr/req/data"
DOC = "https://www.vworld.kr/dev/v4dv_2ddataguide2_s002.do?svcIde=cadastral"
STEP, PAUSE = .0125, .3  # box edge <= 0.0125 deg (<= 1.62 km2; documented geomFilter limit 2 km2), seconds between requests


def landcover(bbox=AOI, classes: dict = CLASSES) -> tuple[list, list]:
    """Stream the 290 MB GeoJSONL; keep `classes` that touch the lon/lat bbox."""
    meta = json.loads(LANDCOVER.with_name(LANDCOVER.name + ".metadata.json").read_text())
    assert sha(LANDCOVER) == meta["sha256"], "landcover.geojsonl does not match its metadata"
    aoi = shapely.box(*bbox)
    with LANDCOVER.open(encoding="utf-8") as lines:
        features = [f for line in lines if '"제주"' in line for f in [json.loads(line)]
                    if f["properties"]["l2_code"] in classes and aoi.intersects(shapely.geometry.shape(f["geometry"]))]
    return features, [{"kind": "landcover_zones", "path": str(LANDCOVER.relative_to(ROOT)),
                       **{key: meta[key] for key in ("sha256", "source", "collected_at", "license")},
                       "attribution": "환경부 토지피복지도 중분류", "classes": classes,
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


def row_angle(shape) -> float:
    """Long axis of the minimum rotated rectangle, degrees from +x toward +z in [0, 180)."""
    _, long, _ = axes(shape)
    return round(math.degrees(math.atan2(long[1], long[0])), 1) % 180


def zones(features: list, frame: dict, aoi, built) -> list:
    """[(class, polygon)] in scene x/z: AOI-clipped, buildings (+1 m) removed, tidied; slivers under 1 m2 dropped."""
    out = []
    for f in features:
        if cls := CLASSES.get(f["properties"]["l2_code"]):
            parts = shapely.get_parts(tidy(scene_shape(f, frame).intersection(aoi).difference(built)))
            out += [(cls, p) for p in parts if p.geom_type == "Polygon" and p.area >= 1]
    return out


def cadastre(shapes: list, aoi, crop, blocked, scale: float = 1) -> tuple[list, list, Counter]:
    """Crop parcels [{id, jimok, row_angle_deg, ring}], wall polylines and the AOI 지목 distribution from [(properties, scene shape)].
    scale > 1 simplifies rings and walls harder (tile size cap)."""
    inside = [(p, s) for p, s in shapes if s.intersects(aoi)]
    parcels = []
    for p, s in inside:
        # ponytail: farm 지목 at least half inside crop zones only (1.5 MB budget); other lots fall back to cell estimates.
        clipped = s.intersection(aoi)
        if jimok(p["jibun"]) not in FARM or clipped.area < 1 or shapely.intersection(clipped, crop).area < .5 * clipped.area:
            continue
        # ponytail: ring simplified <= 2 m (budget) as a row-angle lookup outline; walls and zones carry the edges.
        part = max(shapely.get_parts(tidy(clipped, 2 * scale)), key=lambda q: q.area, default=None)
        if part is not None and part.area >= 1:  # clip-edge slivers can collapse on the 0.1 m grid
            # Angle of the whole parcel: the same rows on both sides of a clip edge.
            parcels.append({"id": p["pnu"], "jimok": jimok(p["jibun"]), "ring": xz(part.exterior)[:-1], "row_angle_deg": row_angle(s)})
    # Unclipped boundaries, so the AOI edge is no wall; union on the 0.1 m grid dissolves shared edges.
    # ponytail: neighbours that disagree by more than the grid stay double; snap harder if double walls show.
    walled = [s for p, s in inside if jimok(p["jibun"]) in WALLED]
    lines = shapely.union_all(shapely.boundary(shapely.set_precision(walled, .1))).intersection(aoi).difference(blocked)
    walls = [w for w in shapely.get_parts(tidy(shapely.line_merge(lines), scale)) if w.length >= 2]  # ponytail: < 2 m stubs dropped
    return parcels, walls, Counter(jimok(p["jibun"]) for p, _ in inside)


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


def add_groundcover(frame: dict, features: list | None = None, parcels: list | None = None, roads: list | None = None) -> dict:
    site = json.loads(SITE.read_text())
    features, sources = landcover() if features is None else (features, [])
    parcels, parcel_source = load_parcels() if parcels is None else (parcels, {"kind": "caller_supplied"})
    roads, road_source = load_roads(site, frame) if roads is None else (roads, {"kind": "caller_supplied"})
    from build_local import footprint_polygons  # lazy: build_local imports this module
    built = shapely.union_all([p for b in site["buildings"] for p in footprint_polygons(b)]).buffer(1, join_style="mitre")
    aoi = aoi_polygon(frame, AOI)
    zoned = zones(features, frame, aoi, built)
    road_area = shapely.union_all([shapely.LineString(line).buffer(width(r)[0] / 2 + .5) for r in roads for line in r["lines"]])
    plots, walls, jimoks = cadastre([(f["properties"], scene_shape(f, frame)) for f in parcels], aoi, shapely.union_all([p for c, p in zoned if c in CROPS]), built | road_area)
    area, row_source = Counter(), "cadastral" if plots else "cell50_estimated"
    for c, p in zoned:
        area[c] += p.area
    counts = {"zones": dict(Counter(c for c, _ in zoned)), "zone_area_m2": {c: round(a) for c, a in area.items()},
              "parcels": len(plots), "parcel_jimok": dict(Counter(p["jimok"] for p in plots)), "cadastral_aoi_jimok": dict(jimoks),
              "walls": len(walls), "wall_length_m": round(sum(w.length for w in walls))}
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
                       "No paddy water surface: an opaque slab hid the current imagery (late-September paddies, and greenhouses now inside part of a 2023 paddy zone)."]}


PARAMS = {"service": "data", "version": "2.0", "request": "GetFeature", "data": LAYER, "format": "json", "crs": "EPSG:4326", "size": 1000}


def boxes(bbox, frame: dict) -> list[tuple]:
    """The lon/lat bbox as a grid of equal boxes, each under the documented 2 km2 geomFilter limit."""
    nx, ny = (math.ceil((bbox[k + 2] - bbox[k]) / STEP - 1e-9) for k in (0, 1))
    lon, lat = np.linspace(bbox[0], bbox[2], nx + 1), np.linspace(bbox[1], bbox[3], ny + 1)
    out = [(w, s, e, n) for s, n in pairwise(lat.tolist()) for w, e in pairwise(lon.tolist())]
    assert max(aoi_polygon(frame, b).area for b in out) < 2e6, "box exceeds the documented 2 km2 geomFilter limit"
    return out


def fetch_box(box, key: str) -> list:
    """All LP_PA_CBND_BUBUN pages of one box ([] when NOT_FOUND); errors name the page or VWorld code, never the key."""
    query, page, pages, got = {**PARAMS, "geomFilter": "BOX({},{},{},{})".format(*box)}, 1, 1, []
    while page <= pages:
        try:
            with urlopen(API + "?" + urlencode({**query, "page": page, "key": key}), timeout=60) as response:
                payload = json.load(response)["response"]
        except Exception as error:
            raise RuntimeError(f"{LAYER} page {page} failed ({type(error).__name__} {getattr(error, 'code', '')})".rstrip()) from None
        time.sleep(PAUSE)  # polite throttle
        if payload["status"] == "NOT_FOUND":  # no parcels (sea)
            return []
        if payload["status"] != "OK":  # e.g. OVER_REQUEST_LIMIT
            raise RuntimeError(f"{LAYER} page {page}: {(payload.get('error') or {}).get('code', payload['status'])}")
        got += payload["result"]["featureCollection"]["features"]
        page, pages = page + 1, int(payload["page"]["total"])
    assert len(got) == int(payload["record"]["total"]), box
    return got


def collect() -> dict:
    """Documented LP_PA_CBND_BUBUN GetFeature pages over the AOI boxes; the key exists only inside the request."""
    frame = json.loads(SITE.read_text())["projection"]
    key, features, records = read_key(ROOT / ".env"), {}, []
    for box in boxes(AOI, frame):
        got = fetch_box(box, key)
        records.append({"box": box, "features": len(got)})
        features |= {f["id"]: f for f in got}  # parcels crossing tile edges come back once per tile
    data = {"layer": LAYER, "source": API, "documentation": DOC, "request": PARAMS, "tiles": records,
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
    block = add_groundcover(frame, features, parcels, roads)
    json.dumps(block)
    assert block["seed"] == SEED == 20260930  # data only; plants are drawn by the web viewer

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

    # Cadastral layer unavailable: cell50_estimated rows, no parcels or walls, reason recorded.
    missing, source = load_parcels(Path("/nonexistent/parcels.json"), lambda: 1 / 0)
    assert missing == [] and "ZeroDivisionError" in source["reason"]
    fallback = add_groundcover(frame, features, missing, roads)
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
    frame = json.loads((ROOT / "var/rendering/grid/manifest.json").read_text())["coordinateFrame"]
    start = time.perf_counter()
    block = add_groundcover(frame)
    seconds = round(time.perf_counter() - start, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    text = json.dumps(block, ensure_ascii=False, separators=(",", ":"))
    (OUT / "groundcover.json").write_text(text + "\n")
    print(json.dumps({"json_bytes": len(text.encode()), "seconds": seconds,
                      "row_source": block["row_source"], **block["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
