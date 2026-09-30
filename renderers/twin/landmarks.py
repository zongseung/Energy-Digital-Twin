#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Photo-referenced Sinchang landmarks placed from z19 VWorld Satellite evidence.

uv run renderers/twin/landmarks.py --self-test   # no network
uv run renderers/twin/landmarks.py --locate      # cache z19 tiles, write evidence crops
uv run renderers/twin/landmarks.py               # var/rendering/landmarks/preview.glb in the local coordinateFrame
Commons photos are shape/proportion references only; no photo pixels become textures. vworld_key is never printed or stored.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import shapely
import trimesh
from PIL import Image, ImageDraw
from rasterio.warp import transform as project_crs
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from build import bar, normals, rotation, sha, translation
from prepare_imagery import HALF_WORLD, read_key, validate

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var/rendering/landmarks"
MATERIALS = {name: PBRMaterial(name=f"landmark_{name}", baseColorFactor=color, metallicFactor=metal, roughnessFactor=rough)
             for name, color, metal, rough in [  # colours: medians of the photo regions, timber lifted out of eave shadow
                 ("white_paint", [236, 242, 244, 255], .05, .45), ("lantern_glass", [150, 178, 190, 255], .3, .08),
                 ("steel", [150, 156, 160, 255], .7, .35), ("roof_tile", [64, 69, 75, 255], 0, .8),
                 ("timber", [92, 44, 32, 255], 0, .7), ("floor_wood", [80, 50, 36, 255], 0, .8),
                 ("stone", [120, 124, 126, 255], 0, .9)]}


def fetch(z: int, x: int, y: int) -> Image.Image:
    """Cached VWorld Satellite tile; the key only exists inside the request URL."""
    path = OUT / "tiles" / str(z) / f"{x}-{y}.jpg"
    if not path.is_file():
        url = f"https://api.vworld.kr/req/wmts/1.0.0/{read_key(ROOT / '.env')}/Satellite/{z}/{y}/{x}.jpeg"
        try:
            with urlopen(Request(url, headers={"Referer": "http://localhost:18080/", "User-Agent": "EnergyDigitalTwin-local-preview"}), timeout=30) as response:
                payload = response.read(2_000_001)
            validate(payload)
        except Exception as error:
            raise RuntimeError(f"Satellite tile {z}/{y}/{x} failed ({type(error).__name__})") from None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return validate(path.read_bytes())


def pixel_lon_lat(z: int, x: int, y: int, px: float, py: float) -> tuple[float, float]:
    size = 256 * 2 ** z
    mx = (x * 256 + px) / size * 2 * HALF_WORLD - HALF_WORLD
    my = HALF_WORLD - (y * 256 + py) / size * 2 * HALF_WORLD
    return math.degrees(mx / 6378137), math.degrees(math.atan(math.sinh(my / 6378137)))


def ring(sides: int, radius: float, y: float, phase: float = 0) -> np.ndarray:
    angle = math.radians(phase) + np.arange(sides) * 2 * np.pi / sides
    return np.column_stack((radius * np.cos(angle), np.full(sides, y), radius * np.sin(angle)))


def rect(x0: float, x1: float, z0: float, z1: float, y: float) -> np.ndarray:
    return np.array([[x0, y, z0], [x1, y, z0], [x1, y, z1], [x0, y, z1]], dtype=float)


def solid(*rings) -> trimesh.Trimesh:
    """Flat-shaded closed convex solid through rings of equal count; a ring may collapse to a ridge or an apex point."""
    rings = [np.broadcast_to(np.asarray(r, float), np.shape(rings[0])) for r in rings]
    v, n, top = np.vstack(rings), len(rings[0]), (len(rings) - 1) * len(rings[0])
    faces = [f for s in range(len(rings) - 1) for i in range(n) for f in
             ((s * n + i, s * n + (i + 1) % n, (s + 1) * n + (i + 1) % n), (s * n + i, (s + 1) * n + (i + 1) % n, (s + 1) * n + i))]
    faces += [(0, i, i + 1) for i in range(1, n - 1)] + [(top, top + i, top + i + 1) for i in range(1, n - 1)]
    tri = v[np.asarray(faces)]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    keep = np.linalg.norm(cross, axis=1) > 1e-9  # collapsed ridge/apex faces
    tri, cross = tri[keep], cross[keep]
    inward = np.einsum("ij,ij->i", cross, tri.mean(axis=1) - v.mean(axis=0)) < 0  # convex: outward = away from centroid
    tri[inward] = tri[inward][:, ::-1]
    return normals(trimesh.Trimesh(tri.reshape(-1, 3), np.arange(len(tri) * 3).reshape(-1, 3), process=False))


def box(x0: float, x1: float, z0: float, z1: float, y0: float, y1: float) -> trimesh.Trimesh:
    return solid(rect(x0, x1, z0, z1, y0), rect(x0, x1, z0, z1, y1))


def railing(outline: np.ndarray, y: float, height: float, step: float = 1.2) -> list[trimesh.Trimesh]:
    """Top and mid rails with posts along a closed XZ outline."""
    parts = []
    for a, b in zip(outline, np.roll(outline, -1, axis=0)):
        a, b = np.array([a[0], y, a[1]]), np.array([b[0], y, b[1]])
        parts += [bar(a + [0, h, 0], b + [0, h, 0], .03, 8) for h in (height, height / 2)]
        parts += [bar(p, p + [0, height, 0], .035, 8) for t in np.linspace(0, 1, max(1, math.ceil(np.linalg.norm(b - a) / step)), endpoint=False)
                  for p in [a + (b - a) * t]]
    return parts


def lighthouse(d: dict) -> list:
    w, b, deck, g = d["deck_width"] / 2, d["base_block_width"] / 2, d["deck_top"], d["gallery_floor"]
    octagon = lambda across_flats, y: ring(8, across_flats / 2 / math.cos(math.pi / 8), y, 22.5)
    return [("base", box(-b, b, -b, b, 0, deck - .4), "white_paint"),
            ("deck", box(-w, w, -w, w, deck - .4, deck), "white_paint"),
            # ponytail: photo shows a faceted near-octagonal shaft; 8 flat sides stand in for the slight taper.
            ("body", solid(octagon(d["body_width_bottom"], deck), octagon(d["body_width_top"], g - .9)), "white_paint"),
            ("gallery", solid(octagon(d["body_width_top"], g - .9), octagon(d["gallery_width"], g - .3), octagon(d["gallery_width"], g)), "white_paint"),
            ("railings", railing(rect(-w + .1, w - .1, -w + .1, w - .1, 0)[:, [0, 2]], deck, d["deck_railing"])
             + railing(octagon(d["gallery_width"] - .2, 0)[:, [0, 2]], g, d["gallery_railing"], .9), "white_paint"),
            ("lantern_base", [bar([0, g, 0], [0, g + .45, 0], .12)], "white_paint"),
            ("lantern", [bar([0, g + .45, 0], [0, g + .85, 0], .22, 16)], "lantern_glass"),
            ("lantern_cap", [solid(ring(12, .26, g + .85), [[0, g + d["lantern_height"], 0]])], "white_paint"),
            ("antenna", [bar([.9, g, -.6], [.9, d["height"], -.6], .03, 8)], "steel")]


def hip_roof(x0: float, x1: float, z0: float, z1: float, eave: float, ridge: float, inset: float = 1.2) -> tuple:
    """겹처마 hipped roof: two rafter slabs, a 15° eave band, then a planar hip to the ridge."""
    # ponytail: the concave Korean roof curve and upturned corners (추녀) are two planar pitches; loft a curve if close-ups need it.
    hx, hz, cx, cz = (x1 - x0) / 2, (z1 - z0) / 2, (x0 + x1) / 2, (z0 + z1) / 2
    dx, dz, band = max(hx - hz, 0), max(hz - hx, 0), eave + .3 + inset * math.tan(math.radians(15))
    assert ridge > band
    inner = rect(x0 + inset, x1 - inset, z0 + inset, z1 - inset, band)
    line = [[cx - dx, ridge, cz - dz], [cx + dx, ridge, cz - dz], [cx + dx, ridge, cz + dz], [cx - dx, ridge, cz + dz]]
    return ([box(x0 + .3, x1 - .3, z0 + .3, z1 - .3, eave, eave + .15), box(x0, x1, z0, z1, eave + .15, eave + .3)],
            [solid(rect(x0, x1, z0, z1, eave + .3), inner), solid(inner, line)],
            [box(cx - dx - .2, cx + dx + .2, cz - dz - .2, cz + dz + .2, ridge - .05, ridge + .25)])


def pavilion(d: dict) -> list:
    o, e, f, parts, columns, frames = d["eave_overhang"], d["eave"], d["floor"], {}, [], []
    for (x0, x1, z0, z1), ridge in zip(d["wings_eave_xz"], d["ridges"]):
        c = (x0 + o, x1 - o, z0 + o, z1 - o)
        frames.append(shapely.box(c[0], c[2], c[1], c[3]))
        nx, nz = (max(1, math.ceil((hi - lo) / d["bay_max"])) for lo, hi in ((c[0], c[1]), (c[2], c[3])))
        for x in np.linspace(c[0], c[1], nx + 1):  # shared wing edges: keep one column per metre
            for z in np.linspace(c[2], c[3], nz + 1):
                if (x in (c[0], c[1]) or z in (c[2], c[3])) and all(math.dist((x, z), q) > 1 for q in columns):
                    columns.append((float(x), float(z)))
        parts.setdefault("floor", []).append(box(c[0] - .15, c[1] + .15, c[2] - .15, c[3] + .15, f - .2, f))
        parts.setdefault("beams", []).extend(box(*span, e - .25, e) for span in
                                             ((c[0] - .12, c[1] + .12, c[2] - .12, c[2] + .12), (c[0] - .12, c[1] + .12, c[3] - .12, c[3] + .12),
                                              (c[0] - .12, c[0] + .12, c[2] - .12, c[3] + .12), (c[1] - .12, c[1] + .12, c[2] - .12, c[3] + .12)))
        for name, meshes in zip(("rafters", "roof", "ridge"), hip_roof(x0, x1, z0, z1, e, ridge)):
            parts.setdefault(name, []).extend(meshes)
    p = d["plinth"]
    parts["plinths"] = [box(x - .2, x + .2, z - .2, z + .2, 0, p) for x, z in columns]
    parts["columns"] = [bar([x, p, z], [x, e - .25, z], d["column_diameter"] / 2) for x, z in columns]
    # ponytail: rails only along the outer floor edge; balusters, steps and entrance gaps omitted.
    parts["railings"] = railing(np.asarray(shapely.union_all(frames).exterior.coords)[:-1], f, .6)
    material = {"floor": "floor_wood", "beams": "timber", "rafters": "timber", "roof": "roof_tile", "ridge": "roof_tile",
                "plinths": "stone", "columns": "timber", "railings": "timber"}
    return [(name, meshes, material[name]) for name, meshes in parts.items()]


def hex_pavilion(d: dict) -> list:
    r, big, e, f = d["column_vertex_radius"], d["eave_vertex_radius"], d["eave"], d["floor"]
    apex, corners = e + d["roof_rise"], ring(6, r, 0)
    band = ring(6, big - 1.2, e + .3 + 1.2 * math.tan(math.radians(15)))
    return [("plinths", [box(x - .18, x + .18, z - .18, z + .18, 0, .25) for x, _, z in corners], "stone"),
            ("columns", [bar([x, .25, z], [x, e - .2, z], .12) for x, _, z in corners], "timber"),
            ("beams", [bar(a + [0, e - .1, 0], b + [0, e - .1, 0], .1, 8) for a, b in zip(corners, np.roll(corners, -1, axis=0))], "timber"),
            ("floor", [solid(ring(6, r + .2, f - .2), ring(6, r + .2, f))], "floor_wood"),
            ("railings", railing(corners[:, [0, 2]], f, d["railing"]), "timber"),
            ("rafters", [solid(ring(6, big - .3, e), ring(6, big - .3, e + .15)), solid(ring(6, big, e + .15), ring(6, big, e + .3))], "timber"),
            ("roof", [solid(ring(6, big, e + .3), band), solid(band, [[0, apex, 0]])], "roof_tile"),
            ("finial", [solid(ring(8, .15, apex - .1), ring(8, .15, apex + .35), [[0, d["height"], 0]])], "roof_tile")]


PHOTO = {n: {"title": f"Sinchang Windmill Coastal Road {n}.jpg", "path": f".worktrees/data/var/data/photos/sinchang/commons_sinchang_{n}.jpg",
             "source_page": f"https://commons.wikimedia.org/wiki/File:Sinchang_Windmill_Coastal_Road_{n}.jpg", "sha256": digest,
             "author": "Grapesurgeon", "license": "CC-BY-SA-4.0", "image_pixels_in_model": False}
         for n, digest in (("09", "421fd03636614761c3fe4308f7bbaae385524ff3ad59188f882e191e4dce4566"),
                           ("01", "cf10254bdf243fc9dc67826c7983c028bd71d15e3f1f44221c0ca923b6c32971"))}
TAMRA = ("Resection of commons_sinchang_09 from the bearings of all ten visible Tamra turbines (GIS hub:power_plant:5722-5731, "
         "0.06° rms, 24 mm-equivalent lens) puts the camera about 18 m from this point")
LANDMARKS = [
    {"id": "sinchang_white_lighthouse", "name": "신창 해안 흰 등대", "model": lighthouse, "yaw_deg": 0,
     "location_evidence": {"tile_zxy": [19, 445891, 210581], "pixel": [152.5, 218], "crop": "var/rendering/landmarks/evidence/sinchang_white_lighthouse.png",
                           "feature": "White square deck 31x33 px (7.7x8.2 m) with a round white top and a ~10 m shadow to the NE, on grass at the basalt coast where the sea walkway starts; path to the south. Deck centre marked.",
                           "cross_check": TAMRA + "; VWorld place search for 신창등대 returned NOT_FOUND."},
     "dimensions_m": {"height": 14.2, "deck_top": 2.4, "deck_width": 7.8, "base_block_width": 6.0, "deck_railing": .8,
                      "body_width_bottom": 3.1, "body_width_top": 2.9, "gallery_floor": 11.0, "gallery_width": 3.9,
                      "gallery_railing": 1.0, "lantern_height": 1.05},
     "dimension_basis": ("commons_sinchang_09 full-res crop (3000,870)-(3960,1920); two front visitors on the deck 123 px and 115 px = 1.7 m -> 70 px/m, "
                         "±10%. Ground to deck top 170 px = 2.4 m; deck slab 30 px = 0.4 m; deck 531 px = 7.6 m (z19 square 7.7-8.2 m; 7.8 m used); "
                         "base block 415 px = 5.9 m (6.0 used); deck railing 57 px = 0.8 m; shaft 220 -> 200 px = 3.1 -> 2.9 m across flats; "
                         "deck to gallery floor 602 px = 8.6 m (floor 11.0 m); gallery 275 px = 3.9 m; gallery railing 68 px = 1.0 m; "
                         "light unit 74x30 px = 1.05x0.43 m; antenna top 223 px above gallery floor = 3.2 m (overall 14.2 m). "
                         "Horizon cross-check (horizon row 915, camera ~1.1 m) gives deck 2.3 m, gallery 11.3 m, top 14.6 m. "
                         "Assumes visitors 1.7 m at the shaft depth. Solar panel, side stairs, ground fence and red-white marker post omitted."),
     "source_photo": PHOTO["09"]},
    {"id": "singyemul_park_pavilion", "name": "싱계물공원 기와 정자", "model": pavilion, "yaw_deg": 45,
     "location_evidence": {"tile_zxy": [19, 445898, 210585], "pixel": [3.6, 167.0], "crop": "var/rendering/landmarks/evidence/singyemul_park_pavilion.png",
                           "feature": "L-shaped dark tiled hipped roof (sun-lit SW slopes grey) at 45°: eave outline 15.2x5.9 m plus 14.3x9.6 m; "
                                      "marked point is the centre of its bounding box. Red-brown sheds adjoin; hexagonal pavilion 48 m NNW.",
                           "cross_check": "Photo 01 shows the same L-shaped tiled pavilion with the hexagonal pavilion far behind and a KOSPO turbine behind "
                                          "its left wing; GIS turbine hub:power_plant:5674 stands 120 m W. VWorld place search for 싱계물공원 returned "
                                          "NOT_FOUND, so the park name follows the design spec, not a provider POI."},
     "dimensions_m": {"height": 5.45, "wings_eave_xz": [[-7.6, 7.6, -7.2, -1.3], [-7.6, 2.0, -7.2, 7.2]], "ridges": [4.3, 5.2],
                      "eave_overhang": 1.2, "eave": 2.9, "floor": .8, "plinth": .3, "column_diameter": .26, "bay_max": 2.6},
     "dimension_basis": ("Plan: z19 roof outline in local axes x=NE, z=SE (0.249 m/px): wing A 61x23.5 px, wing B 38.5x57.5 px. "
                         "Heights: commons_sinchang_01 full-res crop (0,1100)-(1420,1900); visitor beside the front column 258 px = 1.7 m "
                         "-> 152 px/m, depth 17.7 m; sea horizon row 610 -> camera ~1.1 m. Plinth 48 px = 0.3 m; column 358 px = 2.35 m, 40 px = 0.26 m dia; "
                         "floor top 128 px = 0.8 m; eave line 2.9 m; ridge ends from horizon geometry at assumed depths 21-27 m: near wing 5.2 m, "
                         "far wing 4.3 m (+ 0.25 m ridge cap). ±10% (ridges ±15%). Eave overhang 1.2 m taken from the hexagonal pavilion ratio; "
                         "bays <= 2.6 m assumed because columns cannot all be counted."),
     "source_photo": PHOTO["01"]},
    {"id": "singyemul_park_hexagonal_pavilion", "name": "싱계물공원 육각정", "model": hex_pavilion, "yaw_deg": 0,
     "location_evidence": {"tile_zxy": [19, 445897, 210585], "pixel": [202.5, 19.0], "crop": "var/rendering/landmarks/evidence/singyemul_park_hexagonal_pavilion.png",
                           "feature": "Dark hexagonal tiled roof 27-28 px (6.7-7.0 m) across, flat N/S edges (vertices E/W), on the park loop path. Centre marked.",
                           "cross_check": "Photo 01 shows it behind the L pavilion with the white sea-walkway railing beyond, consistent with its seaward position."},
     "dimensions_m": {"height": 5.35, "column_vertex_radius": 1.85, "eave_vertex_radius": 3.5, "eave": 2.7, "floor": .55,
                      "railing": .55, "roof_rise": 1.95},
     "dimension_basis": ("commons_sinchang_01 full-res crop (1600,1480)-(2000,1800); visitor on the same ground row 68 px = 1.7 m -> 40 px/m, ±10%. "
                         "Seen corner-on: side columns ±65 px = 1.6 m = 0.866 R -> column vertex radius 1.85 m; eave 242 px = 6.05 m across flats -> "
                         "eave vertex radius 3.5 m (z19 roof 6.7-7.0 m); ground to beam top 107 px = 2.7 m; floor 22 px = 0.55 m; railing 22 px = 0.55 m; "
                         "eave to roof apex 77 px = 1.95 m; finial 27 px = 0.7 m (overall 5.35 m)."),
     "source_photo": PHOTO["01"]},
]


def add_landmarks(scene: trimesh.Scene, frame: dict, landmarks: list | None = None) -> dict:
    landmarks = LANDMARKS if landmarks is None else landmarks
    for spec in landmarks:  # review focus 5: no imagery evidence -> not placed, reported as failure
        if not spec.get("location_evidence"):
            raise ValueError(f"{spec['id']}: no imagery location evidence; landmark not placed")
    site_path = ROOT / "var/rendering/site/scene.json"
    site = json.loads(site_path.read_text())
    projection = site["projection"]
    assert all(projection[key] == frame[key] for key in ("horizontal_crs", "vertical_crs", "axes", "scale"))
    (se, sn), (fe, fn) = projection["origin_easting_northing"], frame["origin_easting_northing"]
    terrain = np.asarray(site["terrain"]["positions"]).reshape(-1, 3)
    land = terrain[np.asarray(site["terrain"]["water_classes"]) == 0]
    records = []
    for spec in landmarks:
        evidence = spec["location_evidence"]
        lon, lat = pixel_lon_lat(*evidence["tile_zxy"], *evidence["pixel"])
        (east,), (north,) = project_crs("EPSG:4326", frame["horizontal_crs"], [lon], [lat])
        local = land[np.hypot(land[:, 0] + se - east, sn - land[:, 2] - north) <= 60]
        assert len(local) >= 3, f"insufficient local ground samples: {spec['id']}"
        # ponytail: add_buildings' 60 m / 20th-percentile DSM rule without its footprint mask; replace with surveyed ground.
        ground = float(np.percentile(local[:, 1], 20))
        node, position = "landmark_" + spec["id"], [east - fe, ground, fn - north]
        scene.graph.update(frame_to=node, frame_from="world", matrix=translation(*position) @ rotation(spec["yaw_deg"], (0, 1, 0)))
        faces = 0
        for part, meshes, material in spec["model"](spec["dimensions_m"]):
            mesh = normals(trimesh.util.concatenate(meshes))
            mesh.visual = TextureVisuals(material=MATERIALS[material])
            scene.add_geometry(mesh, geom_name=f"{node}_{part}", node_name=f"{node}_{part}", parent_node_name=node)
            faces += len(mesh.faces)
        records.append({**{k: v for k, v in spec.items() if k != "model"}, "node": node, "lon_lat": [lon, lat], "position": position,
                        "ground_m": ground, "ground_sample_count": len(local), "face_count": faces,
                        "status": "photo_reference_estimated_geometry"})
    return {"count": len(records), "records": records,
            "sources": [{"kind": "location_imagery", "source": "VWorld Satellite WMTS z19 (~0.25 m/px)", "cache": "var/rendering/landmarks/tiles/19",
                         "attribution": "공간정보 오픈플랫폼(브이월드) / 국토교통부", "notice": "Provider terms apply; acquisition date is not photography date."},
                        *[{"kind": "shape_proportion_photo", **photo} for photo in PHOTO.values()],
                        {"kind": "ground_DSM", "path": str(site_path.relative_to(ROOT)), "sha256": sha(site_path)}],
            "limits": ["Estimated geometry from single Commons photos with visitors as 1.7 m scale (about ±10%); not surveyed or operator drawings.",
                       "Positions are visual identifications in z19 satellite tiles (~0.25 m/px, tall parts may lean off-nadir); not surveyed.",
                       "Ground is the 20th percentile of Copernicus DSM land samples within 60 m, not a DTM; bases may float or sink by a metre or more.",
                       "Omitted: lighthouse solar panel, stairs and ground fence; pavilion balusters, steps, ridge ornaments and upturned eave corners.",
                       "Park name 싱계물공원 follows the design spec; the VWorld place search returned no POI."]}


def locate() -> None:
    """Cache 3x3 z19 tiles around each evidence pixel and write a marked 4x crop."""
    for spec in LANDMARKS:
        evidence = spec["location_evidence"]
        z, x, y = evidence["tile_zxy"]
        mosaic = Image.new("RGB", (768, 768))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                mosaic.paste(fetch(z, x + dx, y + dy), (256 + 256 * dx, 256 + 256 * dy))
        cx, cy = 256 + evidence["pixel"][0], 256 + evidence["pixel"][1]
        left, top = int(cx) - 64, int(cy) - 64
        crop = mosaic.crop((left, top, left + 128, top + 128)).resize((512, 512), Image.NEAREST)
        px, py = (cx - left) * 4, (cy - top) * 4
        draw = ImageDraw.Draw(crop)
        draw.line([(px - 24, py), (px - 6, py)], fill=(255, 0, 0), width=2)
        draw.line([(px + 6, py), (px + 24, py)], fill=(255, 0, 0), width=2)
        draw.line([(px, py - 24), (px, py - 6)], fill=(255, 0, 0), width=2)
        draw.line([(px, py + 6), (px, py + 24)], fill=(255, 0, 0), width=2)
        (ROOT / evidence["crop"]).parent.mkdir(parents=True, exist_ok=True)
        crop.save(ROOT / evidence["crop"])
        print(json.dumps({"id": spec["id"], "tile_zxy": evidence["tile_zxy"], "pixel": evidence["pixel"],
                          "lon_lat": pixel_lon_lat(z, x, y, *evidence["pixel"]), "crop": evidence["crop"]}, ensure_ascii=False))


def self_test() -> None:
    frame = {"horizontal_crs": "EPSG:32652", "vertical_crs": "EPSG:3855", "origin_easting_northing": [236000.0, 3693000.0],
             "axes": "x east, y up, z south", "scale": 1.0}
    scene = trimesh.Scene()
    result = add_landmarks(scene, frame)
    assert result["count"] == len(result["records"]) >= 1
    for record in result["records"]:
        assert record["node"] in scene.graph.nodes and record["location_evidence"] and record["dimension_basis"]
        top, faces = -np.inf, 0
        for name in (n for n in scene.graph.nodes_geometry if n.startswith(record["node"] + "_")):
            matrix, geometry = scene.graph[name]
            mesh = scene.geometry[geometry]
            assert np.isfinite(mesh.vertices).all() and np.isfinite(mesh.vertex_normals).all() and len(mesh.faces)
            assert np.allclose(np.linalg.norm(mesh.vertex_normals, axis=1), 1, atol=2e-3)
            top, faces = max(top, trimesh.transform_points(mesh.vertices, matrix)[:, 1].max()), faces + len(mesh.faces)
        assert 0 < faces <= 20000, (record["id"], faces)
        assert abs(top - record["position"][1] - record["dimensions_m"]["height"]) < .01, (record["id"], top - record["position"][1])
        x, _, z = record["position"]
        lon, lat = project_crs("EPSG:32652", "EPSG:4326", [x + 236000.0], [3693000.0 - z])
        assert np.hypot((lon[0] - record["lon_lat"][0]) * 93000, (lat[0] - record["lon_lat"][1]) * 111000) < 1
    cube = box(-1, 1, -1, 1, 0, 2)  # outward normals: every face points away from the centre
    assert trimesh.Trimesh(cube.vertices, cube.faces).is_watertight and np.all(np.einsum("ij,ij->i", cube.face_normals, cube.triangles_center - [0, 1, 0]) > 0)
    try:
        add_landmarks(trimesh.Scene(), frame, [{**LANDMARKS[0], "location_evidence": None}])
    except ValueError:
        pass
    else:
        raise AssertionError("landmark without location evidence accepted")
    print(f"PASS {result['count']} landmarks: nodes, finite unit normals, <=20k faces, heights, 1 m round trip, evidence required")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--locate", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.locate:
        return locate()
    frame = json.loads((ROOT / "var/rendering/local/manifest.json").read_text())["coordinateFrame"]
    scene = trimesh.Scene()
    result = add_landmarks(scene, frame)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "preview.glb").write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))
    (OUT / "landmarks.json").write_text(json.dumps({"coordinateFrame": frame, **result}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"glb": str((OUT / "preview.glb").relative_to(ROOT)), "sha256": sha(OUT / "preview.glb"),
                      "landmarks": [{k: r[k] for k in ("id", "lon_lat", "position", "face_count")} for r in result["records"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
