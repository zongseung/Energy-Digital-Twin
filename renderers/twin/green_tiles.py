#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Scene-wide green data in 1 km tiles: landcover zones, cadastral crop parcels and estimated 밭담 for the web viewer.

uv run renderers/twin/green_tiles.py --self-test   # offline synthetic check
uv run renderers/twin/green_tiles.py --collect     # LP_PA_CBND_BUBUN over the scene bbox -> var/rendering/green-cache/parcels.json (resumable)
uv run renderers/twin/green_tiles.py               # var/rendering/local/green/index.json + <i>_<j>.json
vworld_key is never printed or stored.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from itertools import product
import json
from pathlib import Path
import tempfile
import time

import numpy as np
import shapely

from build import sha
from groundcover import API, CLASSES as GROUND, CROPS, DOC, LAYER, PARAMS, boxes, cadastre, fetch_box, landcover, read_key, row_angle, tidy, xz
from roads import CLASS_WIDTH, SITE, aoi_polygon, load_roads, to_scene, width
from vegetation import CLASSES as TREES, LANDCOVER, SEED

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "var/rendering/local/manifest.json"
OUT = ROOT / "var/rendering/local/green"
CACHE = ROOT / "var/rendering/green-cache/parcels.json"
TILE, CAP = 1000, 600_000
CLASSES = TREES | GROUND  # landcover l2_code -> class; 330 mixed forest draws as broadleaf
ORDER = ["conifer", "broadleaf", "orchard", "greenhouse", "field", "paddy", "grass", "other_crop"]
ROWS = {"orchard", "greenhouse"}  # zones that carry row_angle_deg


def project(shapes, frame: dict):
    return shapely.make_valid(shapely.transform(shapes, lambda p: to_scene(p[:, 0], p[:, 1], frame)))


def build(region, zoned: list, parcels: list, built, roads, cap: int = CAP) -> dict:
    """{"i_j": (bounds, tile, encoded bytes, simplification scale)} for non-empty 1 km tiles of the scene region.
    zoned [(class, scene polygon, row angle | None)], parcels [(properties, scene shape)], built / roads: arrays of mask polygons."""
    trees = [shapely.STRtree(np.array(g, dtype=object)) for g in ([s for _, s, _ in zoned], [s for _, s in parcels], built, roads)]
    (x0, z0, x1, z1), out = (np.asarray(region.bounds) // TILE).astype(int), {}
    for i, j in product(range(x0, x1 + 1), range(z0, z1 + 1)):
        box = shapely.box(i * TILE, j * TILE, (i + 1) * TILE, (j + 1) * TILE).intersection(region)
        zk, pk, bk, rk = (t.query(box, predicate="intersects") for t in trees)
        if not len(zk) + len(pk):  # sea or empty
            continue
        near = shapely.union_all(built[bk])
        for scale in (1, 2, 4, 8, 16):  # coarser simplification until the tile fits the cap
            pieces = [(c, p, a) for k in zk for c, s, a in [zoned[k]] for p in shapely.get_parts(tidy(s.intersection(box).difference(near), .5 * scale))
                      if p.geom_type == "Polygon" and p.area >= 1]
            # ponytail: walls are clipped per tile before the < 2 m stub drop, so a wall end within 2 m of a tile edge can go missing.
            plots, walls, _ = cadastre([parcels[k] for k in pk], box, shapely.union_all([p for c, p, _ in pieces if c in CROPS]),
                                       near | shapely.union_all(roads[rk]), scale)
            tile = {"zones": [{"class": c, "rings": [xz(r)[:-1] for r in (p.exterior, *p.interiors)], "row_angle_deg": a} for c, p, a in pieces],
                    "parcels": plots, "walls": [xz(w) for w in walls]}
            data = json.dumps(tile, ensure_ascii=False, separators=(",", ":")).encode()
            if len(data) <= cap:
                break
        else:
            raise AssertionError(f"tile {i}_{j} is {len(data)} bytes at 16x simplification (cap {cap})")
        if any(tile.values()):
            out[f"{i}_{j}"] = ([i * TILE, j * TILE, (i + 1) * TILE, (j + 1) * TILE], tile, data, scale)
    return out


def write(tiles: dict, out: Path, bounds: list, row_source: str, sources: list, limits: list) -> dict:
    """<i>_<j>.json per tile and index.json; tiles left over from an earlier run are removed."""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    for name, (_, _, data, _) in tiles.items():
        (out / f"{name}.json").write_bytes(data)
    index = {"schema_version": 1, "tile_size_m": TILE, "bounds": bounds,
             "tiles": [{"id": n, "bounds": b, "bytes": len(d), "sha256": hashlib.sha256(d).hexdigest(), "counts": {k: len(v) for k, v in t.items()}}
                       for n, (b, t, d, _) in sorted(tiles.items())],
             "classes": ORDER, "row_source": row_source, "seed": SEED, "sources": sources, "limits": limits}
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n")
    return index


def road_masks(ngii: list, osm: list, aoi) -> np.ndarray:
    """Wall masks, width / 2 + 0.5 m: NGII ribbons inside the AOI (the NGII cache covers it only), OSM ribbons outside it."""
    ribbon = lambda r: shapely.union_all([shapely.LineString(line).buffer(width(r)[0] / 2 + .5) for line in r["lines"]])
    masks = [ribbon(r).intersection(aoi) for r in ngii] + [ribbon(r).difference(aoi) for r in osm]
    return np.array([m for m in masks if not m.is_empty], dtype=object)


def osm_roads(frame: dict, bbox) -> tuple[list, dict]:
    path, area = LANDCOVER.with_name("road.geojsonl"), shapely.box(*bbox)
    meta = json.loads(path.with_name(path.name + ".metadata.json").read_text())
    assert sha(path) == meta["sha256"], "road.geojsonl does not match its metadata"
    with path.open(encoding="utf-8") as lines:
        found = [f for line in lines for f in [json.loads(line)] if area.intersects(shapely.geometry.shape(f["geometry"]))]
    lines = lambda g: g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
    return [{"width": None, "lanes": f["properties"]["lanes"], "class": f["properties"]["highway"],
             "lines": [to_scene(*np.asarray(line, float).T, frame) for line in lines(f["geometry"])]} for f in found], {
        "kind": "osm_roads", "path": str(path.relative_to(ROOT)), "sha256": meta["sha256"], "source": meta["source"],
        "feature_count": len(found), "use": "wall mask outside the Sinchang AOI: lanes x 3.25 m or class width (estimated) / 2 + 0.5 m",
        "class_width_m": {k: v for k, v in CLASS_WIDTH.items() if not k.startswith("RDD")}}


def buildings(frame: dict, bbox) -> tuple[np.ndarray, dict]:
    """Every building_info footprint touching the bbox (height known or not), scene x/z, grown 1 m."""
    path = LANDCOVER.with_name("building_info.geojsonl")
    meta = json.loads(path.with_name(path.name + ".metadata.json").read_text())
    assert sha(path) == meta["sha256"], "building_info.geojsonl does not match its metadata"
    with path.open(encoding="utf-8") as lines:
        shapes = shapely.from_geojson(lines.readlines())
    shapes = shapes[shapely.intersects(shapes, shapely.box(*bbox))]
    return shapely.buffer(project(shapes, frame), 1, join_style="mitre"), {
        "kind": "building_footprints", "path": str(path.relative_to(ROOT)), "sha256": meta["sha256"], "source": meta["source"],
        "layer": meta["source_layer"], "feature_count": len(shapes), "use": "removed from zones and walls: footprint + 1 m"}


def load_parcels(frame: dict, cache: Path = CACHE) -> tuple[list, dict, str]:
    """[(properties, scene shape)], source and row_source: cadastral (complete fetch), mixed (partial) or cell50_estimated (none)."""
    if not cache.is_file():
        return [], {"kind": "cell50_estimated", "reason": f"no {LAYER} cache (run --collect)"}, "cell50_estimated"
    data = json.loads(cache.read_text())
    features = list(data["features"].values())
    shapes = project(shapely.from_geojson([json.dumps(f["geometry"]) for f in features]), frame) if features else []
    row = "cell50_estimated" if not features else "cadastral" if data["complete"] else "mixed"
    return [(f["properties"], s) for f, s in zip(features, shapes)], {
        "kind": "vworld_cadastral", "layer": LAYER, "source": API, "documentation": DOC, "path": str(cache.relative_to(ROOT)),
        "sha256": sha(cache), "acquired_at": data["acquired_at"], "feature_count": len(features),
        "boxes_fetched": len(data["boxes"]), "boxes_planned": data["planned"], "stopped": data["stopped"], "attribution": data["attribution"]}, row


def collect(bbox, frame: dict, cache: Path = CACHE, fetch=fetch_box, key: str | None = None) -> dict:
    """Resumable LP_PA_CBND_BUBUN fetch over <= 2 km2 boxes: done boxes are skipped, the first error or quota stops the run,
    and complete boxes are always saved. The key exists only inside the request."""
    data = json.loads(cache.read_text()) if cache.is_file() else {
        "layer": LAYER, "source": API, "documentation": DOC, "request": PARAMS, "attribution": "공간정보 오픈플랫폼(브이월드) / 국토교통부",
        "boxes": {}, "features": {}}
    plan, key, data["stopped"] = boxes(bbox, frame), key or read_key(ROOT / ".env"), None
    name = lambda box: ",".join(f"{v:.7f}" for v in box)

    def save() -> None:
        data.update(planned=len(plan), complete=all(name(b) in data["boxes"] for b in plan), acquired_at=datetime.now(timezone.utc).isoformat())
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(".tmp")  # atomic: an interrupted write never corrupts the cache
        temporary.write_text(json.dumps(data, ensure_ascii=False) + "\n")
        temporary.replace(cache)
    try:
        for box in plan:
            if name(box) in data["boxes"]:
                continue
            got = fetch(box, key)
            data["features"] |= {f["id"]: {"id": f["id"], "type": "Feature", "geometry": f["geometry"],
                                           "properties": {k: f["properties"][k] for k in ("pnu", "jibun")}} for f in got}
            data["boxes"][name(box)] = len(got)  # parcels crossing box edges come back once per box
            if len(data["boxes"]) % 25 == 0:
                save()
    except Exception as error:  # network, quota or VWorld error: stop, keep complete boxes
        data["stopped"] = str(error).replace(key, "<key>")
    finally:
        save()
    print(json.dumps({"layer": LAYER, "features": len(data["features"]), "boxes": f"{len(data['boxes'])}/{len(plan)}",
                      "complete": data["complete"], "stopped": data["stopped"], "cache": str(cache)}, ensure_ascii=False))
    return data


def self_test() -> None:
    box = shapely.box
    region = box(-1500, -1000, 1450, 1000)  # 4 x 2 tiles; the east edge cuts tiles 1_*
    rect = lambda cx, cz, length, depth, deg: shapely.affinity.rotate(box(cx - length / 2, cz - depth / 2, cx + length / 2, cz + depth / 2), deg)
    house = box(-510, 240, -490, 260)  # inside the grass, across P4's north edge
    zoned = [("field", box(-1200, -400, -800, -200), None),  # crosses x = -1000: pieces in -2_-1 and -1_-1
             ("orchard", rect(0, -500, 300, 60, 30), 30.0),  # rows at 30 deg on both sides of x = 0
             ("greenhouse", box(300, 100, 340, 300), 90.0),  # long along z
             ("conifer", box(1300, -500, 1600, -400).intersection(region), None),  # clipped by the scene edge
             ("grass", box(-600, 100, -400, 300), None)]  # a house inside
    parcels = [({"pnu": "P1", "jibun": "1전"}, box(-1200, -400, -990, -300)),  # field lot; a 10 m sliver crosses the x = -1000 tile edge
               ({"pnu": "P2", "jibun": "2전"}, box(-990, -400, -800, -300)),  # shares x = -990 with P1
               ({"pnu": "P3", "jibun": "3대"}, box(-1200, -300, -800, -200)),  # house lot: no parcel, no wall
               ({"pnu": "P4", "jibun": "4전"}, box(-560, 150, -440, 250))]  # field lot in grass (no crop zone): walls only
    built = shapely.buffer(np.array([house]), 1, join_style="mitre")
    road = shapely.LineString([(-1300, -350), (-700, -350)]).buffer(3.5)  # crosses P1/P2 and the tile edge
    tiles = build(region, zoned, parcels, built, np.array([road]))
    assert set(tiles) == {"-2_-1", "-1_-1", "-1_0", "0_-1", "0_0", "1_-1"}, sorted(tiles)  # 1_0 and -2_0 are empty (sea)

    temporary = tempfile.TemporaryDirectory()  # removed at exit
    out = Path(temporary.name)
    index = write(tiles, out, bounds=list(region.bounds), row_source="cadastral", sources=[], limits=["x"])
    assert set(index) == {"schema_version", "tile_size_m", "bounds", "tiles", "classes", "row_source", "seed", "sources", "limits"}
    assert index["classes"] == ORDER and index["seed"] == SEED and index["tile_size_m"] == TILE and index["schema_version"] == 1
    assert json.loads((out / "index.json").read_text()) == index and len(list(out.glob("*.json"))) == len(tiles) + 1
    pieces, plots, walls = [], [], []
    for entry in index["tiles"]:
        assert set(entry) == {"id", "bounds", "bytes", "sha256", "counts"}
        data = (out / f"{entry['id']}.json").read_bytes()
        assert entry["bytes"] == len(data) <= CAP and entry["sha256"] == hashlib.sha256(data).hexdigest()
        tile = json.loads(data)
        assert set(tile) == {"zones", "parcels", "walls"} and entry["counts"] == {k: len(v) for k, v in tile.items()}
        i, j = map(int, entry["id"].split("_"))
        assert entry["bounds"] == [i * TILE, j * TILE, (i + 1) * TILE, (j + 1) * TILE]
        inside = shapely.box(*entry["bounds"]).intersection(region).buffer(.06)
        for z in tile["zones"]:
            assert set(z) == {"class", "rings", "row_angle_deg"} and z["class"] in ORDER
            assert all(len(r) >= 3 and r[0] != r[-1] for r in z["rings"]), "rings must be open"
            assert (z["row_angle_deg"] is None) == (z["class"] not in ("orchard", "greenhouse"))
            pieces.append((entry["id"], z["class"], shapely.Polygon(z["rings"][0], z["rings"][1:]), z["row_angle_deg"]))
        for p in tile["parcels"]:
            assert set(p) == {"id", "jimok", "row_angle_deg", "ring"} and len(p["ring"]) >= 3 and p["ring"][0] != p["ring"][-1]
            assert 0 <= p["row_angle_deg"] < 180
            plots.append((entry["id"], p))
        walls += [(entry["id"], shapely.LineString(w)) for w in tile["walls"]]
        coords = np.concatenate([np.concatenate(z["rings"]) for z in tile["zones"]] + [p["ring"] for p in tile["parcels"]] + tile["walls"])
        assert np.allclose(coords, np.round(coords, 1)) and shapely.contains_xy(inside, *coords.T).all(), entry["id"]

    # Tiling: the field splits at x = -1000 with its area kept; the orchard keeps its whole-zone 30 deg rows on both sides.
    field = [(t, p) for t, c, p, _ in pieces if c == "field"]
    assert {t for t, _ in field} == {"-2_-1", "-1_-1"} and abs(sum(p.area for _, p in field) - 80000) < 1
    assert {(t, a) for t, c, _, a in pieces if c in ("orchard", "greenhouse")} == {("-1_-1", 30.0), ("0_-1", 30.0), ("0_0", 90.0)}
    assert abs(sum(p.area for _, c, p, _ in pieces if c == "conifer") - 150 * 100) < 1  # scene-edge clip
    # Buildings: footprint + 1 m removed from zones (a hole in the grass) and from walls.
    grass = [p for _, c, p, _ in pieces if c == "grass"]
    assert len(grass) == 1 and len(grass[0].interiors) == 1 and grass[0].intersection(house.buffer(.9)).area < .01
    # Parcels: farm 지목 half inside crop zones, clipped per tile; the P1 sliver (10 x 100) keeps the whole lot's x rows (0 deg).
    assert sorted((t, p["id"], p["jimok"], p["row_angle_deg"]) for t, p in plots) == [
        ("-1_-1", "P1", "전", 0.0), ("-1_-1", "P2", "전", 0.0), ("-2_-1", "P1", "전", 0.0)]
    # Walls: 전 boundaries once each (shared x = -990 dissolved), split at x = -1000, 7 m road gaps at x = -1200/-990/-800,
    # P4 cut 22 m by the house + 1 m; none around the 대 lot P3.
    length = {t: round(sum(w.length for u, w in walls if u == t)) for t, _ in walls}
    assert length == {"-2_-1": 200 + 200 + 93, "-1_-1": 200 + 200 + 93 + 93, "-1_0": 440 - 22}, length
    assert all(w.intersection(road.buffer(-.05)).length < .01 and w.intersection(house.buffer(.95)).length < .01 for _, w in walls)
    # Size cap: a many-vertex zone over a small cap is simplified harder until the tile fits.
    circle = shapely.Point(500, 500).buffer(300, quad_segs=512)
    fat = build(region, [("grass", circle, None)], [], built[:0], built[:0])["0_0"]
    lean = build(region, [("grass", circle, None)], [], built[:0], built[:0], cap=len(fat[2]) // 2)["0_0"]
    assert fat[3] == 1 and lean[3] > 1 and len(lean[2]) <= len(fat[2]) // 2 and abs(shapely.Polygon(json.loads(lean[2])["zones"][0]["rings"][0]).area / circle.area - 1) < .05

    # Roads: NGII ribbons only inside the AOI, OSM only outside it (class widths from roads.py).
    aoi = box(0, 0, 100, 100)
    ngii = [{"width": "8", "lanes": None, "class": "RDD000", "lines": [np.array([[-100., 50.], [200., 50.]])]}]
    osm = [{"width": None, "lanes": None, "class": "residential", "lines": [np.array([[50., -100.], [50., 200.]])]}]
    mask = shapely.union_all(road_masks(ngii, osm, aoi))
    hit = lambda x, z: mask.contains(shapely.Point(x, z))
    assert hit(30, 50) and hit(30, 54.4) and not hit(30, 54.6) and not hit(-30, 50)  # NGII 8 m + 0.5 m, inside the AOI only
    assert hit(50, -30) and hit(52.4, -30) and not hit(52.6, -30) and not hit(50, 30)  # OSM residential 4 m + 0.5 m, outside only

    # Collect: stops on a VWorld error with the partial cache kept, resumes without refetching, never stores the key.
    frame = json.loads((ROOT / "var/rendering/site/scene.json").read_text())["projection"]
    bbox, cache, calls = (126.155, 33.325, 126.19, 33.36), out / "cache.json", []

    def fetch(b, key):
        calls.append(b)
        if len(calls) == 3:
            raise RuntimeError("LP_PA_CBND_BUBUN page 1: OVER_REQUEST_LIMIT")
        return [{"id": f"F{len(calls)}", "type": "Feature", "geometry": None, "properties": {"pnu": str(len(calls)), "jibun": "1전", "addr": "x"}}]
    first = collect(bbox, frame, cache, fetch, "SECRET")
    assert first["complete"] is False and len(first["boxes"]) == 2 and "OVER_REQUEST_LIMIT" in first["stopped"]
    second = collect(bbox, frame, cache, lambda b, key: calls.append(b) or [], "SECRET")
    assert second["complete"] is True and len(calls) == 3 + 7 and len(set(calls)) == 9 and len(second["features"]) == 2
    assert "SECRET" not in cache.read_text() and set(second["features"]["F1"]["properties"]) == {"pnu", "jibun"}
    print(f"PASS green_tiles {len(tiles)} tiles, {len(pieces)} zone pieces, {len(plots)} parcel pieces, {len(walls)} walls; "
          f"cap {len(fat[2])} -> {len(lean[2])} bytes at scale {lean[3]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    manifest = json.loads(MANIFEST.read_text())
    frame, bbox = manifest["coordinateFrame"], manifest["terrain"]["bbox_lon_lat"]
    if args.collect:
        return collect(bbox, frame)
    start, region = time.perf_counter(), aoi_polygon(frame, bbox)
    features, sources = landcover(bbox, CLASSES)
    shapes = shapely.intersection(project(shapely.from_geojson([json.dumps(f["geometry"]) for f in features]), frame), region)
    zoned = [(CLASSES[f["properties"]["l2_code"]], part) for f, s in zip(features, shapes) for part in shapely.get_parts(s)
             if part.geom_type == "Polygon" and part.area >= 1]
    zoned = [(c, part, row_angle(part) if c in ROWS else None) for c, part in zoned]  # whole zone: the same rows in every tile
    parcels, parcel_source, row_source = load_parcels(frame)
    built, building_source = buildings(frame, bbox)
    site = json.loads(SITE.read_text())
    ngii, ngii_source = load_roads(site, frame, fetch=lambda: 1 / 0)  # cache only: no network in a build
    osm, osm_source = osm_roads(frame, bbox)
    tiles = build(region, zoned, parcels, built, road_masks(ngii, osm, aoi_polygon(frame, site["aoi_bbox"])))
    coarse = {n: s for n, (_, _, _, s) in tiles.items() if s > 1}
    coverage = {"cadastral": "Parcel coverage: every scene box was fetched.",
                "mixed": f"Parcel coverage is partial ({parcel_source.get('boxes_fetched')}/{parcel_source.get('boxes_planned')} boxes; "
                         f"stopped: {parcel_source.get('stopped')}): outside it there are no parcels or walls and rows fall back to hashed 50 m cells.",
                "cell50_estimated": f"No cadastral parcels ({parcel_source.get('reason')}): parcels and walls empty, rows from hashed 50 m cells."}[row_source]
    limits = ["Zones are 환경부 토지피복지도 중분류 2023 polygons (aerial orthophoto), not current use: 320 conifer, 310 broadleaf, 330 mixed "
              "(drawn as broadleaf), 240 orchard, 230 greenhouse, 220 field, 210 paddy, 410/420 grass, 250 other crop. Clipped to the "
              "scene bbox and 1 km tiles (pieces repeat across tiles), building_info footprints + 1 m removed (height known or not), "
              "simplified <= 0.5 m, 0.1 m grid, open rings (outer first, then holes), pieces under 1 m2 dropped. Orchard and greenhouse "
              "row_angle_deg is the long axis of the whole zone polygon's minimum rotated rectangle (degrees from +x toward +z, "
              "0-180): estimated, not observed.",
              "Parcels are VWorld 연속지적도 (LP_PA_CBND_BUBUN) lots whose legal 지목 (jibun suffix, not current use) is 전/답/과/목 "
              "and that have at least half their tile-clipped area in field/paddy/other-crop zones. Ring: the largest tile-clipped part, "
              "holes dropped, simplified <= 2 m. row_angle_deg is the whole lot's minimum-rotated-rectangle long axis: crop row "
              "direction is estimated. " + coverage,
              "Walls are estimates: real 밭담 positions are unknown. They follow every 전/과 lot boundary once (shared edges dissolved "
              "on a 0.1 m grid), cut 1 m clear of building footprints and 0.5 m clear of road ribbons, then simplified <= 1 m (which "
              "can take back part of the 0.5 m road clearance on curves, never the ribbon itself), split at tile edges, pieces "
              "under 2 m dropped; gates, gaps and walls around other lots are not modelled.",
              "Road ribbons for the wall mask: NGII 1:5,000 centreline widths (rvwd, else lanes x 3.25 m, else class) inside the "
              "Sinchang AOI; OSM road.geojsonl outside it, where widths are estimates (lanes x 3.25 m, else secondary 7, tertiary 6, "
              "links 5, residential 4 m) and unmapped farm tracks are not masked."]
    limits += [f"Tiles simplified harder to stay <= {CAP} bytes (tolerance x scale): {coarse}"] if coarse else []
    index = write(tiles, OUT, [round(v, 1) for v in region.bounds], row_source,
                  [*sources, parcel_source, building_source, {**ngii_source, "use": "wall mask inside the Sinchang AOI: width / 2 + 0.5 m"},
                   osm_source], limits)
    area, pieces, ids, walls = Counter(), Counter(), set(), []
    for _, tile, _, _ in tiles.values():
        for z in tile["zones"]:
            area[z["class"]] += shapely.Polygon(z["rings"][0], z["rings"][1:]).area
            pieces[z["class"]] += 1
        ids |= {p["id"] for p in tile["parcels"]}
        walls += [shapely.LineString(w).length for w in tile["walls"]]
    largest = max(index["tiles"], key=lambda t: t["bytes"])
    print(json.dumps({"tiles": len(index["tiles"]), "largest": {largest["id"]: largest["bytes"]}, "total_bytes": sum(t["bytes"] for t in index["tiles"]),
                      "row_source": row_source, "coarsened": coarse, "zone_pieces": dict(pieces), "zone_area_km2": {c: round(a / 1e6, 3) for c, a in area.items()},
                      "parcels": len(ids), "parcel_pieces": sum(t["counts"]["parcels"] for t in index["tiles"]), "wall_pieces": len(walls),
                      "wall_km": round(sum(walls) / 1000, 1), "buildings": building_source["feature_count"], "roads_osm": osm_source["feature_count"],
                      "seconds": round(time.perf_counter() - start, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
