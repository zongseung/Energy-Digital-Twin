#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Estimated harbour structures (KHOA 2026 coastline) and sports facilities (OSM outlines) across the 탐라–한림 scene.

uv run renderers/twin/harbours.py --self-test   # offline: synthetic coast, harbour and stadium on a planar height_at
uv run renderers/twin/harbours.py               # var/rendering/harbours/preview.glb + harbours.json on the displayed terrain
Positions are real (KHOA lines, OSM outlines and seamarks); widths, heights and fittings are estimates ("heights" in the result).
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import time

import numpy as np
import shapely
import trimesh
from rasterio.warp import transform as project_crs
from shapely.affinity import affine_transform
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from build import bar, normals, sha
from landmarks import ring, solid
from prepare_imagery import HARBOURS, harbour_of
from roads import aoi_polygon, ribbon, to_scene
from vegetation import axes

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var/rendering/harbours"
MANIFEST = ROOT / "var/rendering/grid/manifest.json"  # same frame and terrain bbox as the local scene, without depending on its last build
KHOA, OSM = ROOT / "var/survey/harbour/khoa_coast_bbox.geojson", ROOT / "var/survey/harbour/osm.json"
PARCELS = ROOT / "var/rendering/green-cache/parcels.json"
BUILDINGS = ROOT / ".worktrees/data/var/data/geography/source-03e02ef/building_info.geojsonl"
BUDGET, SEED = 250_000, 20260930
# 한림항, 해양수산부 항만정보 (data.go.kr 15088273): chart-datum (DL) tide levels, design wave and lengths.
MOF = {"design_wave_m": 6.1, "hhw_dl_m": 2.82, "msl_dl_m": 1.41, "breakwater_m": 2248, "quay_m": 610, "small_craft_quay_m": 1500}
HHW = MOF["hhw_dl_m"] - MOF["msl_dl_m"]  # 약최고고조위 above mean sea level; EGM2008 0 is taken as mean sea level
CREST, QUAY, SEAWALL = round(HHW + .6 * MOF["design_wave_m"], 2), round(HHW + 1.5, 2), round(HHW + 1.0, 2)
PARAPET, FOOT = round(CREST + 1.2, 2), -2.0
CREST_W, APRON_W, ARMOUR_W, SEAWALL_W, PARAPET_W, STEP_RUN, STEPS = 9.0, 15.0, 15.0, .6, .8, .6, 5
HEIGHTS = {
    "datum": "EGM2008 0 m, taken as mean sea level (the displayed ocean is at 0 m); 한림항 chart-datum figures convert as DL - 1.41 m "
             "(평균해면 1.41 m DL). Korean vertical datum vs EGM2008 offsets of a few decimetres are ignored.",
    "breakwater_crest_m": [CREST, "약최고고조위 + 0.6 H1/3 = 2.82 + 0.6 x 6.1 = 6.48 m DL (한림항 MOF design wave 6.1 m, HHW 2.82 m) = +5.07 m; "
                                  "the overtopping-allowed crest rule of the Korean harbour design standard, applied to every harbour"],
    "parapet_m": [PARAPET, "crest + 1.2 m sea-side parapet along open-sea stretches (estimate)"],
    "quay_m": [QUAY, "약최고고조위 + 1.5 m (1.0-2.0 m for a tide range under 3 m) = 4.32 m DL = +2.91 m"],
    "seawall_m": [SEAWALL, "약최고고조위 + 1.0 m = +2.41 m: a 2.4 m wall above the displayed sea"],
    "steps_m": [[.3, QUAY], f"{STEPS} steps {STEP_RUN} m deep rising from +0.3 m at the KHOA line to the quay level"],
    "foot_m": [FOOT, "walls and the armour toe run to -2 m (or 1 m under the terrain), below the displayed 0 m sea; no bathymetry"],
    "terrain_rule": "every harbour top is max(design, height_at + 0.3 m) (sea wall + 1.0 m, steps per step), so the coarse DSM never "
                    "covers a structure; sports surfaces are draped 0.3 m over the displayed terrain (height_at)",
    "widths_m": {"breakwater_crest": [CREST_W, "land between the KHOA edges within 9 m of a 방파제 line (cadastral 제 strips 6.1-10 m; "
                                               "survey crest-to-opposite-edge median 12 m at 한림항)"],
                 "apron": [APRON_W, "land within 15 m behind a 부두 line (estimate)"],
                 "armour": [ARMOUR_W, "sea-side rubble/tetrapod band (survey: ~18 m at 신창 z17, 10-20 m at z15; 15 m used)"],
                 "seawall": [SEAWALL_W, "estimate"], "parapet": [PARAPET_W, "estimate"],
                 "steps": [STEP_RUN * STEPS, f"{STEPS} x {STEP_RUN} m (estimate)"]}}
# kind: surface node, goal, light poles, pole height m, marking maxima (end box depth, end box width, centre circle radius) m
SPORT = {"field": ("sports_turf", "soccer", 6, 15.0, (16.5, 40.3, 9.15)), "court": ("sports_court", "futsal", 4, 8.0, (6.0, 12.0, 3.0)),
         "basketball": ("sports_court", "hoop", 4, 8.0, (5.8, 4.9, 1.8)), "tennis": ("sports_court", "net", 4, 8.0, (5.49, 8.23, 0))}
SKIP_SPORT = {"golf": "golf course green, not a pitch", "horse_riding": "riding arena, not a pitch",
              "horse_racing": "horse-racing track, not a running track"}
LINE_W, TRACK_W, STAND_D = .15, 5.0, 12.0
COLOURS = {"red": (190, 38, 34), "white": (236, 236, 230), "green": (40, 150, 70), "yellow": (232, 190, 30), "black": (32, 32, 34)}
pbr = lambda name, rgb, rough=.85, metal=0., **kw: PBRMaterial(name=name, baseColorFactor=[*rgb, 255], metallicFactor=metal,
                                                                  roughnessFactor=rough, **kw)
MATERIALS = {name: pbr(name, rgb, *rest) for name, rgb, *rest in [
    ("harbour_breakwater_crest", (182, 180, 172)), ("harbour_breakwater_wall", (132, 130, 124)), ("harbour_parapet", (170, 168, 160)),
    ("harbour_armour", (150, 150, 146), .95), ("harbour_quay_apron", (168, 166, 158)), ("harbour_quay_wall", (132, 130, 124)),
    ("harbour_seawall", (140, 138, 130)), ("harbour_steps", (160, 158, 150)),
    ("sports_turf", (68, 120, 58), .9), ("sports_court", (56, 104, 124), .7), ("sports_lines", (238, 238, 232), .7),
    ("sports_track", (170, 70, 54), .9), ("sports_concourse", (172, 170, 162)),
    ("sports_stands", (150, 152, 158)), ("sports_goals", (235, 235, 235), .4, .1), ("sports_poles", (160, 164, 168), .4, .6)]}
MATERIALS |= {f"harbour_light_{c}": pbr(f"harbour_light_{c}", rgb, .5) for c, rgb in COLOURS.items()}
MATERIALS |= {f"harbour_lantern_{c}": pbr(f"harbour_lantern_{c}", rgb, .2, emissiveFactor=[v / 255 for v in rgb]) for c, rgb in COLOURS.items()}
CREDITS = [
    {"source": "국립해양조사원(KHOA) 2026 해안선 SHP, 해양수산부 국립해양조사원_해안선_20251231 (data.go.kr 15083948), 2024 직접측량",
     "use": "harbour line positions and categories (CAT_COA 방파제 11, 부두 14, 방벽 15, 상륙계단 16)",
     "license": "공공데이터포털 이용허락범위 제한 없음; attribution given", "url": "https://www.data.go.kr/data/15083948/fileData.do"},
    {"source": "© OpenStreetMap contributors (Overpass mirror, osm_base 2026-06-01T08:52Z)",
     "use": "sports outlines (leisure=pitch/track/stadium) and seamark light positions, colours and focal heights",
     "license": "ODbL 1.0: attribution required; a publicly used derived database must be offered under ODbL",
     "url": "https://www.openstreetmap.org/copyright"},
    {"source": "해양수산부 항만정보 (data.go.kr 15088273), 한림항 row",
     "use": "design wave 6.1 m, 약최고고조위 2.82 m, 평균해면 1.41 m (heights); 방파제 2,248 m, 안벽 610 m, 물양장 1,500 m (length check)",
     "license": "공공데이터포털 이용허락범위 제한 없음", "url": "https://www.data.go.kr/data/15088273/fileData.do"},
    {"source": "공간정보 오픈플랫폼(브이월드) 연속지적도 LP_PA_CBND_BUBUN and 건축물정보 LT_C_BLDGINFO (local caches)",
     "use": "supporting evidence in metadata only (지목 제/체/학 parcels, usability 13xxx buildings); no geometry",
     "license": "VWorld provider terms; attribution required; commercial use needs operator consent", "url": "https://www.vworld.kr/v4po_prcint_a001.do"}]


def const(value):
    return value if callable(value) else (lambda x, z: np.full(np.shape(x), float(value)))


def at(f, xz: np.ndarray) -> np.ndarray:
    """(..., 2) x/z points lifted to (..., 3) at y = f(x, z)."""
    y = np.asarray(const(f)(xz[..., 0].ravel(), xz[..., 1].ravel()), float).reshape(xz.shape[:-1])
    return np.stack((xz[..., 0], y, xz[..., 1]), axis=-1)


def facing(tri: np.ndarray, want) -> np.ndarray:
    """Triangles (n, 3, 3) wound so their normal agrees with want; slivers under 1 mm high dropped (float32 GLB, see roads.ribbon)."""
    if not len(tri):
        return tri.reshape(0, 3, 3)
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    tri = np.where((np.einsum("ij,ij->i", cross, np.broadcast_to(want, cross.shape)) < 0)[:, None, None], tri[:, ::-1], tri)
    longest = np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2).max(axis=1)
    return tri[np.linalg.norm(cross, axis=1) / np.maximum(longest, 1e-12) > 1e-3]


def polygons(geom) -> np.ndarray:
    parts = shapely.get_parts(shapely.get_parts(geom))
    return parts[(shapely.get_type_id(parts) == 3) & (shapely.area(parts) > 1e-4)]


def cap(geom, y, down: bool = False) -> np.ndarray:
    """Flat-shaded surface of polygon(s) at y(x, z): constrained Delaunay on the given vertices, facing +y (or -y)."""
    parts = polygons(geom)
    if not len(parts):
        return np.zeros((0, 3, 3))
    xz = shapely.get_coordinates(shapely.get_parts(shapely.constrained_delaunay_triangles(parts))).reshape(-1, 4, 2)[:, :3]
    return facing(at(y, xz), [0, -1 if down else 1, 0])


def walls(geom, top, bottom) -> np.ndarray:
    """Vertical faces on every ring edge from bottom(x, z) to top(x, z), facing out of the polygon."""
    out = []
    for poly in polygons(geom):
        for r in (poly.exterior, *poly.interiors):
            c = np.asarray(r.coords)
            a, b = c[:-1], c[1:]
            d = b - a
            normal = np.column_stack((d[:, 1], -d[:, 0])) / np.maximum(np.linalg.norm(d, axis=1), 1e-12)[:, None]
            normal[shapely.contains_xy(poly, *((a + b) / 2 + 1e-3 * normal).T)] *= -1
            a0, a1, b0, b1 = at(bottom, a), at(top, a), at(bottom, b), at(top, b)
            want = np.column_stack((normal[:, 0], np.zeros(len(a)), normal[:, 1]))
            out.append(facing(np.concatenate([np.stack((a0, b0, b1), 1), np.stack((a0, b1, a1), 1)]), np.concatenate([want, want])))
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def prism(geom, top, bottom, closed: bool = False, step: float = 5.0) -> tuple:
    """(top, walls, bottom) triangles of polygon(s) segmentized to <= step m; bottom is empty unless closed."""
    geom = shapely.segmentize(geom, step)
    return cap(geom, top), walls(geom, top, bottom), cap(geom, bottom, down=True) if closed else np.zeros((0, 3, 3))


def load_evidence(frame: dict) -> dict:
    """Local cadastral 제/체/학 parcels and usability 13xxx buildings (supporting evidence only)."""
    parcels = json.loads(PARCELS.read_text())["features"]
    keep = [f for f in (parcels.values() if isinstance(parcels, dict) else parcels) if (f["properties"]["jibun"] or "").strip()[-1:] in "제체학"]
    with BUILDINGS.open(encoding="utf-8") as lines:
        buildings = [json.loads(line) for line in lines if '"usability":"13' in line]
    return {"parcels": keep, "buildings": buildings,
            "sources": [{"kind": "cadastral_parcels", "path": str(PARCELS.relative_to(ROOT)), "sha256": sha(PARCELS), "jimok": "제/체/학"},
                        {"kind": "building_info", "path": str(BUILDINGS.relative_to(ROOT)), "sha256": sha(BUILDINGS), "usability": "13xxx"}]}


def add_harbours(scene: trimesh.Scene, frame: dict, height_at, coast=None, osm=None, evidence=None, budget: int = BUDGET) -> dict:
    """Harbour structures from KHOA coastline categories and sports facilities from OSM outlines, as harbour_*/sports_* nodes."""
    bbox = json.loads(MANIFEST.read_text())["terrain"]["bbox_lon_lat"]
    coast = json.loads(KHOA.read_text())["features"] if coast is None else coast
    osm = json.loads(OSM.read_text())["elements"] if osm is None else osm
    evidence = load_evidence(frame) if evidence is None else evidence
    # ponytail: 30 m inset keeps every vertex over the displayed terrain (its mesh stops ~15 m inside the bbox at pixel centres).
    area = aoi_polygon(frame, bbox).buffer(-30, join_style="mitre")
    project = lambda geometry: shapely.transform(shapely.geometry.shape(geometry), lambda c: to_scene(c[:, 0], c[:, 1], frame))
    parcels = [(f["properties"]["pnu"], f["properties"]["jibun"].strip(), project(f["geometry"])) for f in evidence["parcels"]]
    buildings = [(b["id"], b["properties"], project(b["geometry"])) for b in evidence["buildings"] if b["properties"]["usability"].startswith("13")]
    bucket, (e0, n0) = defaultdict(list), frame["origin_easting_northing"]
    lonlat = lambda x, z: [float(v[0]) for v in project_crs(frame["horizontal_crs"], "EPSG:4326", [x + e0], [n0 - z])]
    lift = lambda f, pad: (lambda x, z: np.maximum(f, height_at(x, z) + pad))
    foot = lambda x, z: np.minimum(FOOT, height_at(x, z) - 1)

    # Land = coastline faces clear of the scene edge (islands, rocks, breakwater bodies) plus edge faces whose displayed
    # terrain is mostly above 0.5 m (the mainland, edge-clipped islands); the displayed ocean sits at 0 m.
    lines = [(f["properties"]["CAT_COA"], shapely.LineString(to_scene(*np.asarray(part, float).T, frame)))
             for f in coast for part in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "LineString" else f["geometry"]["coordinates"])]
    edge = area.exterior
    faces = shapely.get_parts(shapely.polygonize(shapely.get_parts(
        shapely.union_all([*shapely.intersection([g for _, g in lines], area), edge], grid_size=1e-3))))
    def high(face) -> bool:
        x0, z0, x1, z1 = face.bounds
        grid = np.stack(np.meshgrid(np.linspace(x0, x1, 12), np.linspace(z0, z1, 12)), -1).reshape(-1, 2)
        grid = np.vstack([grid[shapely.contains_xy(face, *grid.T)], shapely.get_coordinates(face.representative_point())])
        return float(np.median(height_at(grid[:, 0], grid[:, 1]))) > .5
    land = shapely.union_all([f for f in faces if not f.intersects(edge) or high(f)])
    shapely.prepare(land)
    # ponytail: 0.2 m simplification of KHOA lines and zones (dense survey vertices); visually lossless, 3-4x fewer triangles.
    by_cat = lambda *cats: [shapely.simplify(g, .2) for c, g in lines if c in cats and g.length > 1]
    strip = lambda lines_, width: shapely.union_all(shapely.buffer(lines_, width, cap_style="flat", join_style="mitre"))
    zone = lambda cat, width: strip(by_cat(cat), width).intersection(land).intersection(area).simplify(.2)
    crest = zone(11, CREST_W)
    steps = zone(16, STEP_RUN * STEPS).difference(crest)
    apron = zone(14, APRON_W).difference(crest | steps)
    seawall = strip(by_cat(15), SEAWALL_W / 2).intersection(area).difference(crest | apron | steps)
    for top_node, wall_node, geom, top in (("harbour_breakwater_crest", "harbour_breakwater_wall", crest, lift(CREST, .3)),
                                           ("harbour_quay_apron", "harbour_quay_wall", apron, lift(QUAY, .3))):
        t, w, _ = prism(geom, top, foot)
        bucket[top_node].append(t)
        bucket[wall_node].append(w)
    bucket["harbour_seawall"] += prism(seawall, lift(SEAWALL, 1.0), foot)[:2]
    for k in range(STEPS):  # step k: the band k..k+1 runs inland of the KHOA line, rising from +0.3 m to the quay level
        band = steps.intersection(strip(by_cat(16), (k + 1) * STEP_RUN)).difference(strip(by_cat(16), k * STEP_RUN))
        bucket["harbour_steps"] += prism(band, lift(.3 + (QUAY - .3) * k / (STEPS - 1), .05 * (k + 1)), foot)[:2]

    # Armour and parapet along open-sea stretches of the breakwater lines: >= 4 of 7 rays (+-45 deg) run 600 m clear of land.
    rng, open_m, rays = np.random.default_rng(SEED), 0.0, np.radians(np.arange(-45, 46, 15))
    for line in by_cat(11):
        s = np.append(np.arange(0, line.length, 2.5), line.length)
        p = shapely.get_coordinates(shapely.line_interpolate_point(line, s))
        t = np.gradient(p, axis=0)
        n = np.column_stack((-t[:, 1], t[:, 0])) / np.maximum(np.linalg.norm(t, axis=1), 1e-9)[:, None]
        plus, minus = shapely.contains_xy(land, *(p + 1.5 * n).T), shapely.contains_xy(land, *(p - 1.5 * n).T)
        sea = np.where(plus[:, None], -n, n)
        turn = lambda a: np.column_stack((sea[:, 0] * np.cos(a) - sea[:, 1] * np.sin(a), sea[:, 0] * np.sin(a) + sea[:, 1] * np.cos(a)))
        clear = sum(~shapely.intersects(land, shapely.linestrings(np.stack((p + 3 * sea, p + 600 * turn(a)), 1))) for a in rays)
        exposed = (plus != minus) & (clear >= 4)
        pair = exposed[:-1] & exposed[1:]
        if not pair.any():
            continue
        open_m += float((s[1:] - s[:-1])[pair].sum())
        # ponytail: displaced rubble band (cheaper than tetrapod blocks): 6 cells across, +-0.45 m jitter, no per-block shapes.
        frac = np.linspace(0, 1, 7)
        xz = p[:, None] + ARMOUR_W * frac[None, :, None] * sea[:, None] + np.where(frac[None, :, None] > 0, rng.uniform(-.4, .4, (len(p), 7, 2)), 0)
        y = CREST - .5 - (CREST - .5 - FOOT) * frac + np.where(frac > 0, rng.uniform(-.45, .45, (len(p), 7)), 0)
        v = np.dstack((xz[..., 0], y, xz[..., 1]))
        i = np.flatnonzero(pair)[:, None]
        j = np.arange(6)[None, :]
        quad = [v[i, j], v[i + 1, j], v[i + 1, j + 1], v[i, j + 1]]
        tri = np.concatenate([np.stack(quad[:3], -2), np.stack((quad[0], quad[2], quad[3]), -2)]).reshape(-1, 3, 3)
        centre = tri.mean(axis=1)[:, [0, 2]]
        tri = tri[shapely.contains_xy(area, *centre.T) & ~shapely.contains_xy(land, *centre.T)]
        bucket["harbour_armour"].append(facing(tri, [0, 1, 0]))
        for run in np.split(np.arange(len(p)), np.flatnonzero(np.diff(exposed)) + 1):
            if exposed[run[0]] and len(run) > 1:
                wall = strip([shapely.LineString(p[run])], PARAPET_W).intersection(crest)
                bucket["harbour_parapet"] += prism(wall, lambda x, z: lift(CREST, .3)(x, z) + 1.2, lift(CREST, .3))[:2]

    # Harbour lights from OSM seamarks: tower bands per beacon colour tag, else red for a red light and white otherwise
    # (Korean practice: red towers show red lights, white towers green or white ones); lantern at the tagged focal height.
    lights = []
    heads = crest.buffer(-1.5)
    for e in osm:
        tags = e.get("tags", {})
        colour = tags.get("seamark:light:colour")
        if e["type"] != "node" or not colour or tags.get("seamark:type") == "landmark":
            continue
        point = shapely.Point(to_scene([e["lon"]], [e["lat"]], frame)[0])
        if not area.contains(point):
            continue
        kind = tags.get("seamark:type", "lighthouse")
        bands = [c for c in tags.get(f"seamark:{kind}:colour", "").split(";") if c in COLOURS] or ["red" if colour == "red" else "white"]
        snapped = 0.0
        if not heads.is_empty and 0 < heads.distance(point) <= 60:  # breakwater-head light charted a little off the structure
            target = shapely.Point(shapely.shortest_line(heads, point).coords[0])
            snapped, point = point.distance(target), target
        x, z = point.x, point.y
        on_crest = heads.buffer(1e-6).contains(point)
        base = float((lift(CREST, .3) if on_crest else height_at)([x], [z])[0]) if on_crest or land.contains(point) else FOOT
        focal = float(tags.get("seamark:light:height", 10))
        top, clamped = HHW + focal - .6, False  # lantern centred at the focal height above 약최고고조위
        if top - base < 3:  # ponytail: DSM above the charted focal plane (hill-top lights): a 6 m tower instead
            top, clamped = base + 6, True
        edges = np.linspace(base, top, len(bands) + 1)
        for c, y0, y1 in zip(bands, edges[:-1], edges[1:]):
            bucket[f"harbour_light_{c}"].append(solid(ring(8, 1.1, y0), ring(8, 1.1, y1)).triangles + [x, 0, z])
        lantern = colour.split(";")[0] if colour.split(";")[0] in COLOURS else "white"
        bucket[f"harbour_lantern_{lantern}"].append(solid(ring(8, .55, top), ring(8, .55, top + 1.2)).triangles + [x, 0, z])
        bucket[f"harbour_light_{bands[-1]}"].append(solid(ring(8, .75, top + 1.2), [[0, top + 1.7, 0]]).triangles + [x, 0, z])
        lights.append({"osm": f"node/{e['id']}", "name": tags.get("seamark:name") or tags.get("name"), "seamark_type": kind,
                       "light_colour": colour, "bands": bands, "focal_height_m": focal, "position": [x, base, z], "lon_lat": lonlat(x, z),
                       "base_m": base, "top_m": top + 1.7, "tower_height_m": top - base, "snapped_m": round(snapped, 2), "clamped": clamped})

    # Per-harbour records: KHOA lines by category and cadastral 제 parcels touching the crest.
    names = {11: "breakwater", 14: "quay", 15: "seawall", 16: "landing_steps", 17: "slipway_not_modelled"}
    stats, own = defaultdict(lambda: defaultdict(lambda: [0, 0.0])), defaultdict(list)
    for cat, g in lines:
        if cat in names and g.length > 1:
            lon, lat = lonlat(g.centroid.x, g.centroid.y)
            key = harbour_of(lon, lat)
            key = key if np.hypot((lon - HARBOURS[key][0]) * 93000, (lat - HARBOURS[key][1]) * 111000) <= 1500 else "open_coast"
            stats[key][names[cat]][0] += 1
            stats[key][names[cat]][1] += g.length
            own[key].append(g)
    harbours = []
    for key, cats in sorted(stats.items()):
        where = shapely.union_all(own[key]).buffer(APRON_W + 1)  # this harbour's own structures only
        harbours.append({"name": key, "name_ko": HARBOURS[key][2] if key in HARBOURS else "항만 밖 해안 방벽",
                         "khoa_lines": {c: {"count": n, "length_m": round(m, 1)} for c, (n, m) in cats.items()},
                         "crest_m2": round(crest.intersection(where).area), "apron_m2": round(apron.intersection(where).area),
                         "evidence_je_parcels": [{"pnu": pnu, "jibun": jibun, "area_m2": round(g.area)} for pnu, jibun, g in parcels
                                                 if jibun.endswith("제") and g.intersects(crest.intersection(where).buffer(20))]})

    # Sports: OSM outlines only, draped 0.3 m over the displayed terrain (roads.ribbon: <= 10 m edges). The outermost outline
    # (stadium > track > pitch) groups the rest: track rings, infields, pitches, lines, stands and concourse are cut from each
    # other and inlaid at the same height, never stacked.
    no_sea = lambda x, z: np.zeros(len(x), bool)
    drape = lambda geom: m.triangles if (m := ribbon(shapely.make_valid(geom), height_at, no_sea)) is not None else np.zeros((0, 3, 3))
    ground = lambda x, z: np.asarray(height_at(x, z), float) + .3
    y_at = lambda p: float(ground([p[0]], [p[1]])[0])
    sports, skipped, feats = [], [], []
    for e in osm:
        tags = e.get("tags", {})
        if tags.get("leisure") not in ("pitch", "track", "stadium"):
            continue
        rec = {"osm": f"{e['type']}/{e['id']}", "leisure": tags["leisure"], "sport": tags.get("sport"), "name": tags.get("name")}
        coords = to_scene([p["lon"] for p in e.get("geometry", [])], [p["lat"] for p in e.get("geometry", [])], frame) if e["type"] == "way" else None
        reason = ("point only: no outline to model" if coords is None else SKIP_SPORT.get(tags.get("sport")) or
                  ("open way" if len(coords) < 4 or not np.allclose(coords[0], coords[-1]) else None))
        poly = None if reason else shapely.make_valid(shapely.Polygon(coords))
        if poly is not None and not area.contains(poly):
            reason = "outline leaves the scene bbox (30 m inset)"
        if reason:
            skipped.append({**rec, "reason": reason})
        else:
            feats.append((rec, poly))
    groups = []
    for rec, poly in sorted(feats, key=lambda f: -f[1].area):
        group = next((g for g in groups if g[0][1].contains(poly.representative_point())), None)
        (group.append((rec, poly)) if group else groups.append([(rec, poly)]))
    for group in groups:
        outer = group[0][1]
        (x0, z0, x1, z1) = outer.bounds
        grid = np.stack(np.meshgrid(np.arange(x0, x1, 5), np.arange(z0, z1, 5)), -1).reshape(-1, 2)
        samples = np.concatenate([grid[shapely.contains_xy(outer, *grid.T)], shapely.get_coordinates(shapely.segmentize(outer.exterior, 2))])
        terrain = height_at(samples[:, 0], samples[:, 1])
        members = [(r, p.intersection(outer)) for r, p in group]
        pitches = shapely.union_all([p for r, p in members if r["leisure"] == "pitch"])
        tracks = sorted([m for m in members if m[0]["leisure"] == "track"], key=lambda m: -m[1].area)
        used, inner_of = shapely.union_all([p for _, p in tracks]), {}
        for i, (r, t) in enumerate(tracks):
            if i in inner_of.values():
                continue
            j = next((j for j in range(i + 1, len(tracks)) if t.contains(tracks[j][1].representative_point())), None)
            inner = tracks[j][1] if j is not None else t.buffer(-TRACK_W)
            inner_of[i] = j
            bucket["sports_track"].append(drape(t.difference(inner).difference(pitches)))
            bucket["sports_turf"].append(drape(inner.difference(pitches)))
            r |= {"inner_edge": f"OSM {tracks[j][0]['osm']}" if j is not None else f"{TRACK_W} m lanes (estimate)"}
            if j is not None:
                tracks[j][0]["role"] = f"inner edge of {r['osm']}"
        stands = []
        for r, s in members:
            if r["leisure"] != "stadium":
                continue
            p0, d, n = axes(s)
            u, v, length, width = d / np.linalg.norm(d), n / np.linalg.norm(n), np.linalg.norm(d), np.linalg.norm(n)
            local = lambda geom: affine_transform(geom, [u[0], v[0], u[1], v[1], *p0])
            for t0, t1 in ((0, STAND_D), (width - STAND_D, width)):  # both long sides, middle 60 %, rising away from the field
                stand = local(shapely.box(.2 * length, t0, .8 * length, t1)).intersection(s).difference(used.buffer(1)).difference(pitches)
                if stand.area < 10:
                    continue
                back = 0 if t0 == 0 else width
                rise = lambda x, z, back=back: .4 + .5 * np.clip(STAND_D - np.abs((x - p0[0]) * v[0] + (z - p0[1]) * v[1] - back), 0, STAND_D)
                bucket["sports_stands"] += prism(stand, lambda x, z, rise=rise: ground(x, z) + rise(x, z), lambda x, z: height_at(x, z) - 1)[:2]
                stands.append(stand)
            bucket["sports_concourse"].append(drape(s.difference(used).difference(pitches).difference(shapely.union_all(stands))))
            r |= {"stands": len(stands), "stand_basis": f"{STAND_D} m deep along the middle 60 % of both long sides, rising 0.4-6.4 m (estimate)"}
        for r, p in members:
            if r["leisure"] != "pitch":
                continue
            kind = r["sport"] if r["sport"] in ("basketball", "tennis") else "field" if r["sport"] == "soccer" or p.area >= 2000 else "court"
            surface, goal, poles, pole_h, (box_d, box_w, radius) = SPORT[kind]
            p0, d, n = axes(p)
            u, v, length, width = d / np.linalg.norm(d), n / np.linalg.norm(n), np.linalg.norm(d), np.linalg.norm(n)
            xz = lambda s, t: p0 + np.multiply.outer(s, u) + np.multiply.outer(t, v)
            local = lambda geom: affine_transform(geom, [u[0], v[0], u[1], v[1], *p0])
            m = min(1.5, .03 * width)
            ll, ww = length - 2 * m, width - 2 * m
            bd, bw, rr = min(box_d, .16 * ll), min(box_w, .6 * ww), min(radius, .13 * ww)
            marks = [shapely.box(m, m, length - m, width - m).exterior, shapely.LineString([(length / 2, m), (length / 2, width - m)]),
                     shapely.box(m, (width - bw) / 2, m + bd, (width + bw) / 2).exterior,
                     shapely.box(length - m - bd, (width - bw) / 2, length - m, (width + bw) / 2).exterior]
            marks += [shapely.Point(length / 2, width / 2).buffer(rr, quad_segs=12).exterior] if rr else []
            painted = local(shapely.union_all(shapely.buffer(marks, LINE_W / 2, cap_style="flat"))).intersection(p)
            bucket[surface].append(drape(p.difference(painted)))
            bucket["sports_lines"].append(drape(painted))
            # ponytail: goals, hoops and net are bare frames (no net mesh, no rims); poles carry one flat lamp box each.
            fittings = []
            if goal in ("soccer", "futsal"):
                gw = min(7.32, .16 * ww) if goal == "soccer" else min(3.0, .2 * ww)
                gh = min(2.44, gw / 3) if goal == "soccer" else 2.0
                for s_ in (m, length - m):
                    a, b = xz(s_, width / 2 - gw / 2), xz(s_, width / 2 + gw / 2)
                    ya, yb = y_at(a), y_at(b)
                    fittings += [bar([a[0], ya, a[1]], [a[0], ya + gh, a[1]], .06, 6).triangles, bar([b[0], yb, b[1]], [b[0], yb + gh, b[1]], .06, 6).triangles,
                                 bar([a[0], ya + gh, a[1]], [b[0], yb + gh, b[1]], .06, 6).triangles]
                goals = 2
            elif goal == "hoop":
                for s_, inward in ((.4, 1), (length - .4, -1)):
                    a, b = xz(s_, width / 2), xz(s_ + inward * .8, width / 2)
                    ya, lo_hi = y_at(a), sorted((s_ + inward * .8, s_ + inward * .85))
                    fittings += [bar([a[0], ya, a[1]], [a[0], ya + 3.5, a[1]], .07, 6).triangles,
                                 bar([a[0], ya + 3.4, a[1]], [b[0], ya + 3.4, b[1]], .05, 6).triangles,
                                 np.concatenate(prism(local(shapely.box(lo_hi[0], width / 2 - .9, lo_hi[1], width / 2 + .9)), ya + 3.95, ya + 2.9, closed=True))]
                goals = 2
            else:  # tennis net across the middle
                net = local(shapely.box(length / 2 - .015, m - .5, length / 2 + .015, width - m + .5))
                fittings += [np.concatenate(prism(net, lambda x, z: ground(x, z) + .95, ground, closed=True))]
                goals = 1
            bucket["sports_goals"] += fittings
            spots = [(-2, -2), (length + 2, -2), (length + 2, width + 2), (-2, width + 2)] + ([(length / 2, -2), (length / 2, width + 2)] if poles == 6 else [])
            for s_, t_ in spots:
                x, z = xz(s_, t_)
                base = float(height_at([x], [z])[0])
                head = local(shapely.box(s_ - .7, t_ - .2, s_ + .7, t_ + .2))
                bucket["sports_poles"] += [bar([x, base - .5, z], [x, base + pole_h, z], .15, 6).triangles,
                                           np.concatenate(prism(head, base + pole_h + .6, base + pole_h - .2, closed=True))]
            r |= {"kind": kind, "surface": surface, "goals": goals, "goal_type": goal, "light_poles": len(spots), "pole_height_m": pole_h,
                  "size_m": [round(length, 1), round(width, 1)], "markings": "outline, halfway, end boxes and centre circle scaled to the outline (estimate)"}
        for r, p in members:
            near = [(b_id, props, g.distance(p)) for b_id, props, g in buildings if g.distance(p) <= 150]
            r |= {"ground_m": round(float(terrain.mean()), 2), "terrain_relief_m": round(float(np.ptp(terrain)), 2), "area_m2": round(p.area),
                  "position": [p.centroid.x, float(terrain.mean()), p.centroid.y], "lon_lat": lonlat(p.centroid.x, p.centroid.y),
                  "evidence": {"parcels": [{"pnu": pnu, "jibun": jibun, "overlap_m2": round(g.intersection(p).area)} for pnu, jibun, g in parcels
                                           if jibun[-1:] in "체학" and g.intersects(p)],
                               "buildings_13xxx": [{"id": b_id, "usability": props["usability"], "bld_nm": props.get("bld_nm") or None,
                                                    "height_m": float(props.get("height") or 0) or None, "distance_m": round(dist, 1)}
                                                   for b_id, props, dist in sorted(near, key=lambda b: b[2])]}}
            sports.append(r)

    meshes = {}
    for name, parts in bucket.items():
        tris = np.concatenate([np.asarray(t, float).reshape(-1, 3, 3) for t in parts])
        if len(tris):
            mesh = trimesh.Trimesh(tris.reshape(-1, 3), np.arange(len(tris) * 3).reshape(-1, 3), process=False)
            mesh.visual = TextureVisuals(material=MATERIALS[name])
            meshes[name] = normals(mesh)
    triangles = sum(len(m.faces) for m in meshes.values())
    if triangles > budget:
        raise ValueError(f"harbours/sports need {triangles} triangles, over the {budget} budget")
    for name, mesh in meshes.items():
        scene.add_geometry(mesh, geom_name=name, node_name=name)
    counts = {"breakwater_lines": len(by_cat(11)), "breakwater_crest_m2": round(crest.area), "armour_open_sea_m": round(open_m),
              "quay_lines": len(by_cat(14)), "apron_m2": round(apron.area), "seawall_lines": len(by_cat(15)),
              "seawall_m": round(sum(g.length for g in by_cat(15))), "landing_steps": len(by_cat(16)), "lights": len(lights),
              "stadiums": sum(r["leisure"] == "stadium" for r in sports), "tracks": sum(r["leisure"] == "track" and "role" not in r for r in sports),
              "pitches": sum(r["leisure"] == "pitch" for r in sports), "stands": sum(r.get("stands", 0) for r in sports),
              "goals": sum(r.get("goals", 0) for r in sports), "light_poles": sum(r.get("light_poles", 0) for r in sports),
              "skipped_sports": len(skipped), "slipways_not_modelled": len(by_cat(17))}
    hallim = crest.intersection(shapely.Point(to_scene([HARBOURS["hallim"][0]], [HARBOURS["hallim"][1]], frame)[0]).buffer(1500))
    return {"counts": counts, "triangles": triangles, "budget": budget, "meshes": {n: len(m.faces) for n, m in meshes.items()},
            "heights": HEIGHTS, "mof_hallim": MOF | {"crest_half_perimeter_m": round(hallim.length / 2),
                                                    "note": "MOF breakwater length vs modelled 한림항 crest half-perimeter (length check only)"},
            "harbours": harbours, "lights": lights, "sports": sports, "skipped": skipped,
            "sources": [{"kind": "khoa_coastline", "path": str(KHOA.relative_to(ROOT)), "sha256": sha(KHOA) if KHOA.is_file() else None},
                        {"kind": "osm_overpass", "path": str(OSM.relative_to(ROOT)), "sha256": sha(OSM) if OSM.is_file() else None},
                        *evidence.get("sources", [])],
            "credits": CREDITS,
            "limits": ["Positions follow the KHOA 2026 coastline (2024 direct survey) and OSM outlines/seamarks; every width, height, "
                       "section, fitting and colour is an estimate (see heights). NGII 1:5,000 제방/부두 layers with HEIG were not available.",
                       "Breakwater crest = land between KHOA edges within 9 m of a 방파제 line; where only one edge is charted the crest is "
                       "9 m wide. Armour is a displaced rubble band, 15 m wide, on stretches whose sea side faces open water (ray test), "
                       "not individual tetrapods; the harbour-basin side gets none.",
                       "Quay aprons are the land within 15 m behind 부두 lines, level at +2.91 m; 선가대 (slipways) are not modelled; "
                       "floating pontoons and moored boats are left out.",
                       "Harbour light positions come from OSM seamarks (many from NGA Pub. 112, ~0.1' precision); lights within 60 m of a "
                       "breakwater crest are snapped onto it (snapped_m). Tower colours follow beacon colour tags, else red/white by light colour.",
                       "Sports use OSM pitch/track/stadium outlines only; point-only sites, golf greens, riding arenas and horse-racing tracks "
                       "are skipped. Surfaces, markings, goals, 4-6 light poles and stands are estimates; tracks without an OSM inner edge "
                       "get 5 m lanes. Surfaces follow the coarse displayed DSM 0.3 m above it, so real level fields show its slope "
                       "(terrain_relief_m) and DSM canopy/roof bumps.",
                       "Cadastral 제/체/학 parcels and building_info 13xxx buildings are recorded as evidence only (13xxx = 운동시설 is an "
                       "inference from 한림체육관); they shape nothing.",
                       "Harbour heights assume EGM2008 0 = mean sea level and apply 한림항 figures to every 포구; smaller harbours are "
                       "probably lower."]}


def self_test() -> None:
    manifest = json.loads(MANIFEST.read_text())
    frame, bbox = manifest["coordinateFrame"], manifest["terrain"]["bbox_lon_lat"]
    (e0, n0), crs = frame["origin_easting_northing"], frame["horizontal_crs"]
    height_at = lambda x, z: np.where(np.asarray(z) > -3001, .5 + .001 * np.asarray(x, float), 0.)  # sea north of z = -3000 at 0 m

    def lonlat(xz) -> list:
        xz = np.asarray(xz, float)
        lon, lat = project_crs(crs, "EPSG:4326", xz[:, 0] + e0, n0 - xz[:, 1])
        return np.column_stack((lon, lat)).tolist()
    line = lambda cat, *p: {"properties": {"CAT_COA": cat}, "geometry": {"type": "LineString", "coordinates": lonlat(p)}}
    area = lambda *p: {"type": "Polygon", "coordinates": [lonlat([*p, p[0]])]}
    way = lambda i, tags, p: {"type": "way", "id": i, "tags": tags, "geometry": [{"lon": a, "lat": b} for a, b in lonlat([*p, p[0]])]}
    node = lambda i, tags, x, z: {"type": "node", "id": i, "tags": tags, **dict(zip(("lon", "lat"), lonlat([[x, z]])[0]))}
    rect = lambda x0, z0, x1, z1: [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    # Mainland coast along z = -3000 across the whole scene (land to the south), with a U-shaped breakwater (12 m body,
    # 150 m long) going north, a 100 m quay, a 180 m sea wall and 10 m of landing steps.
    y = -3000
    coast = [line(54, (-3000, y), (-200, y)), line(15, (-200, y), (-20, y)), line(54, (-20, y), (-6, y)),
             line(11, (-6, y), (-6, y - 150), (6, y - 150), (6, y)), line(54, (6, y), (20, y)), line(14, (20, y), (120, y)),
             line(54, (120, y), (130, y)), line(16, (130, y), (140, y)), line(54, (140, y), (23000, y))]
    oval = lambda r: np.asarray(shapely.LineString([(1085, -1100), (1215, -1100)]).buffer(r, quad_segs=8).exterior.coords)[:-1]
    osm = [node(1, {"seamark:type": "light_minor", "seamark:light:colour": "red", "seamark:light:height": "13"}, 0, y - 165),
           node(2, {"seamark:type": "beacon_cardinal", "seamark:light:colour": "white", "seamark:light:height": "16",
                    "seamark:beacon_cardinal:colour": "yellow;black;yellow"}, -400, y - 300),
           node(3, {"man_made": "lighthouse", "seamark:light:colour": "white", "seamark:light:height": "20"}, 300, y + 100),
           node(4, {"seamark:type": "landmark", "seamark:landmark:category": "windmotor"}, 500, y - 500),
           way(10, {"leisure": "stadium", "name": "경기장"}, rect(1020, -1160, 1280, -1040)),
           way(11, {"leisure": "track", "sport": "running", "area": "yes"}, oval(48)),
           way(19, {"leisure": "track", "sport": "running"}, oval(42)),  # OSM inner edge of way/11
           way(12, {"leisure": "pitch", "sport": "soccer"}, rect(1095, -1130, 1205, -1070)),
           way(13, {"leisure": "pitch", "sport": "basketball"}, rect(1400, -1108, 1428, -1093)),
           way(14, {"leisure": "pitch", "sport": "tennis"}, rect(1450, -1110, 1486, -1092)),
           way(15, {"leisure": "pitch", "sport": "golf"}, rect(1600, -1100, 1700, -1000)),
           way(16, {"leisure": "track", "sport": "horse_racing"}, rect(1800, -1200, 2000, -1000)),
           node(17, {"leisure": "pitch", "name": "점만 있는 운동장"}, 1150, -1100),
           way(18, {"leisure": "pitch"}, rect(-2660, -1000, -2600, -960))]  # crosses the scene edge
    evidence = {"parcels": [{"properties": {"pnu": "P-je", "jibun": "914-4제"}, "geometry": area(*rect(-6, y - 150, 6, y))},
                            {"properties": {"pnu": "P-che", "jibun": "1 체"}, "geometry": area(*rect(1000, -1170, 1300, -1030))},
                            {"properties": {"pnu": "P-jeon", "jibun": "5전"}, "geometry": area(*rect(1000, -1170, 1300, -1030))}],
                "buildings": [{"id": "B-gym", "properties": {"usability": "13000", "bld_nm": "체육관", "height": "12.25"},
                               "geometry": area(*rect(1300, -1200, 1330, -1180))},
                              {"id": "B-house", "properties": {"usability": "01000", "bld_nm": "", "height": "0"},
                               "geometry": area(*rect(1300, -1200, 1310, -1190))},
                              {"id": "B-far", "properties": {"usability": "13000", "bld_nm": "", "height": "0"},
                               "geometry": area(*rect(6000, -1200, 6010, -1190))}],
                "sources": []}
    scene = trimesh.Scene(base_frame="world")
    result = add_harbours(scene, frame, height_at, coast, osm, evidence)

    # Schema and credits.
    assert {"counts", "triangles", "budget", "meshes", "heights", "harbours", "lights", "sports", "skipped", "sources", "credits",
            "limits"} <= set(result), set(result)
    credits = json.dumps(result["credits"], ensure_ascii=False)
    assert all(s in credits for s in ("국립해양조사원", "KHOA", "OpenStreetMap", "ODbL", "해양수산부", "15088273", "15083948")), credits
    # Node prefixes, per-structure meshes, triangle budget.
    meshes = {n: scene.geometry[scene.graph[n][1]] for n in scene.graph.nodes_geometry}
    assert meshes and all(n.startswith(("harbour_", "sports_")) for n in meshes), sorted(meshes)
    assert {"harbour_breakwater_crest", "harbour_breakwater_wall", "harbour_armour", "harbour_parapet", "harbour_quay_apron",
            "harbour_quay_wall", "harbour_seawall", "harbour_steps", "harbour_light_red", "harbour_light_white", "harbour_light_yellow",
            "harbour_light_black", "sports_turf", "sports_court", "sports_lines", "sports_track", "sports_concourse", "sports_stands",
            "sports_goals", "sports_poles"} <= set(meshes), sorted(meshes)
    assert result["triangles"] == sum(len(m.faces) for m in meshes.values()) == sum(result["meshes"].values()) <= BUDGET
    try:
        add_harbours(trimesh.Scene(base_frame="world"), frame, height_at, coast, osm, evidence, budget=100)
    except ValueError:
        pass
    else:
        raise AssertionError("triangle budget not enforced")
    # Valid geometry: finite, unit normals, no zero-area faces; caps face up; everything inside the scene bbox.
    for name, mesh in meshes.items():
        assert np.isfinite(mesh.vertices).all() and np.isfinite(mesh.vertex_normals).all(), name
        assert np.allclose(np.linalg.norm(mesh.vertex_normals, axis=1), 1, atol=2e-3) and mesh.area_faces.min() > 1e-7, name
        if name in ("harbour_breakwater_crest", "harbour_quay_apron", "sports_turf", "sports_court", "sports_lines", "sports_track",
                    "sports_concourse", "harbour_armour"):
            assert (mesh.face_normals[:, 1] > .2).all(), name
        lon, lat = project_crs(crs, "EPSG:4326", mesh.vertices[:, 0] + e0, n0 - mesh.vertices[:, 2])
        assert bbox[0] < np.min(lon) and np.max(lon) < bbox[2] and bbox[1] < np.min(lat) and np.max(lat) < bbox[3], name
    # Outward normals: a closed prism of a holed polygon is watertight, consistently wound and of positive volume.
    top, sides, bottom = prism(shapely.Polygon(rect(0, 0, 30, 20), [rect(10, 5, 20, 12)[::-1]]), lambda x, z: 3 + .1 * x, -1, closed=True)
    solid_ = trimesh.Trimesh(np.concatenate([top, sides, bottom]).reshape(-1, 3), np.arange(3 * (len(top) + len(sides) + len(bottom))).reshape(-1, 3))
    assert solid_.is_watertight and solid_.is_winding_consistent and np.isclose(solid_.volume, (600 - 70) * (4 + 1.5), rtol=1e-6), solid_.volume
    # Heights: crest, apron, sea wall and steps from the documented basis on low terrain.
    h = result["heights"]
    crest, apron = meshes["harbour_breakwater_crest"].vertices, meshes["harbour_quay_apron"].vertices
    assert np.allclose(crest[:, 1], h["breakwater_crest_m"][0]) and 5 <= h["breakwater_crest_m"][0] <= 7
    assert np.allclose(apron[:, 1], h["quay_m"][0]) and np.isclose(meshes["harbour_seawall"].vertices[:, 1].max(), h["seawall_m"][0])
    assert np.isclose(meshes["harbour_parapet"].vertices[:, 1].max(), h["parapet_m"][0]) and meshes["harbour_armour"].vertices[:, 1].min() < 0
    steps = meshes["harbour_steps"]
    assert len(np.unique(np.round(steps.triangles[steps.face_normals[:, 1] > .99][:, :, 1], 1))) == 5
    # Crest follows the land between the KHOA edges; armour stays on the sea side within its width.
    assert np.isclose(meshes["harbour_breakwater_crest"].area, 12 * 150, rtol=.02), meshes["harbour_breakwater_crest"].area
    a = meshes["harbour_armour"].triangles_center[:, [0, 2]]
    body = shapely.box(-6, y - 150, 6, y)
    assert not shapely.contains_xy(body, a[:, 0], a[:, 1]).any() and (a[:, 1] < y).all()
    assert shapely.dwithin(body, shapely.points(a), h["widths_m"]["armour"][0] + 1).all()
    # Lights: red light snapped onto the breakwater head, beacon banded per tag, lighthouse on land, landmark ignored.
    lights = {r["osm"]: r for r in result["lights"]}
    assert set(lights) == {"node/1", "node/2", "node/3"}, lights
    assert lights["node/1"]["bands"] == ["red"] and 0 < lights["node/1"]["snapped_m"] <= 60 and np.isclose(lights["node/1"]["base_m"], h["breakwater_crest_m"][0])
    assert lights["node/2"]["bands"] == ["yellow", "black", "yellow"] and lights["node/2"]["snapped_m"] == 0
    assert lights["node/3"]["bands"] == ["white"] and np.isclose(lights["node/3"]["base_m"], height_at(300, y + 100)[()])
    # Sports: outlines only; golf, horse racing, point-only and scene-edge features skipped with reasons.
    skipped = {s["osm"]: s["reason"] for s in result["skipped"]}
    assert set(skipped) == {"way/15", "way/16", "node/17", "way/18"}, skipped
    sports = {r["osm"]: r for r in result["sports"]}
    assert set(sports) == {"way/10", "way/11", "way/12", "way/13", "way/14", "way/19"}, sports
    assert sports["way/11"]["inner_edge"] == "OSM way/19" and sports["way/19"]["role"] == "inner edge of way/11" and result["counts"]["tracks"] == 1
    ring_area = shapely.Polygon(oval(48)).difference(shapely.Polygon(oval(42))).difference(shapely.box(1095, -1130, 1205, -1070)).area
    assert np.isclose(meshes["sports_track"].area, ring_area, rtol=1e-3), (meshes["sports_track"].area, ring_area)
    assert sports["way/12"]["kind"] == "field" and sports["way/12"]["goals"] == 2 and sports["way/12"]["light_poles"] == 6
    assert sports["way/13"]["goals"] == 2 and sports["way/14"]["goals"] == 1 and sports["way/13"]["light_poles"] == 4
    stands = meshes["sports_stands"].vertices
    assert sports["way/10"]["stands"] == 2 and (stands[:, 1] - height_at(stands[:, 0], stands[:, 2])).max() > 5
    for name in ("sports_turf", "sports_court", "sports_lines", "sports_track", "sports_concourse"):  # draped 0.3 m over the terrain
        v = meshes[name].vertices
        assert np.allclose(v[:, 1], height_at(v[:, 0], v[:, 2]) + .3, atol=1e-6) and meshes[name].edges_unique_length.max() <= 10, name
    surfaces = shapely.union_all([shapely.union_all(shapely.polygons(meshes[n].triangles[:, :, [0, 2]]).tolist()) for n in
                                  ("sports_turf", "sports_court", "sports_lines", "sports_track", "sports_concourse")])
    stadium, ground = shapely.box(1020, -1160, 1280, -1040), shapely.union_all(shapely.polygons(meshes["sports_stands"].triangles[:, :, [0, 2]]).tolist())
    assert np.isclose(surfaces.intersection(stadium).area + ground.area, stadium.area, rtol=2e-3)  # stadium fully covered, nothing stacked
    lines = shapely.union_all(shapely.polygons(meshes["sports_lines"].triangles[:, :, [0, 2]]))
    pitch_rects = shapely.union_all([shapely.box(1095, -1130, 1205, -1070), shapely.box(1400, -1108, 1428, -1093), shapely.box(1450, -1110, 1486, -1092)])
    assert lines.area > 50 and pitch_rects.buffer(.01).contains(lines)
    assert sports["way/10"]["evidence"]["parcels"][0]["pnu"] == "P-che" and len(sports["way/10"]["evidence"]["parcels"]) == 1
    assert [b["id"] for b in sports["way/10"]["evidence"]["buildings_13xxx"]] == ["B-gym"]
    assert [p["pnu"] for r in result["harbours"] for p in r["evidence_je_parcels"]] == ["P-je"]
    print(f"PASS harbours: {result['counts']} {result['triangles']} triangles <= {BUDGET}; nodes, validity, outward prisms, bbox, "
          "design heights, crest/armour sides, light snapping/bands, sports kinds/skips/stands/lines/evidence, credits")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    if parser.parse_args().self_test:
        return self_test()
    from build_local import surface_height  # lazy: build_local will import this module at integration
    frame = json.loads(MANIFEST.read_text())["coordinateFrame"]
    height_at = surface_height(trimesh.load(ROOT / "var/rendering/local/scene.glb", force="scene"))
    start, scene = time.perf_counter(), trimesh.Scene(base_frame="world")
    result = add_harbours(scene, frame, height_at)
    seconds = round(time.perf_counter() - start, 1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "preview.glb").write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))
    (OUT / "harbours.json").write_text(json.dumps({"coordinateFrame": frame, **result, "seconds": seconds}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"glb": str((OUT / "preview.glb").relative_to(ROOT)), "sha256": sha(OUT / "preview.glb"), "seconds": seconds,
                      **{k: result[k] for k in ("counts", "triangles", "meshes")},
                      "lights": Counter("+".join(r["bands"]) for r in result["lights"]),
                      "sports": Counter(r.get("kind", r["leisure"]) for r in result["sports"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
