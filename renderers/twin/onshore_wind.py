#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Hangyeong/Sinchang onshore wind turbines at their source GIS points, reusing build.py's turbine prototype.

uv run renderers/twin/onshore_wind.py --self-test   # offline: GIS file + planar height_at
uv run renderers/twin/onshore_wind.py               # var/rendering/onshore-wind/{preview.glb,onshore_wind.json} on local scene heights
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import trimesh
from rasterio.warp import transform as project_crs
from trimesh.visual.material import PBRMaterial
from trimesh.visual.texture import TextureVisuals

from build import glb_tree, normals, prototype, rotation, sha, translation
from roads import to_scene

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "var/rendering/onshore-wind"
GIS = ROOT / ".worktrees/data/var/data/geography/source-03e02ef"
SPEC = json.loads(Path(__file__).with_name("spec.json").read_text())  # Tamra's estimated detailing and yaw
MUSEUM = "https://archive.much.go.kr/data/01/folderView.do?jobdirSeq=1383"
KOSPO = "https://www.kospo.co.kr/kospo/194/subview.do"
# One 3 MW prototype (Tamra's estimated detailing, rotor 90 m, tower top 80 m), uniformly scaled per class so rotor spin
# stays a pure rotation. scale = rotor / 90; hub = 81.8 * scale (build.prototype ends the tower 1.8 m below the hub).
# ponytail: uniform scale puts the 1.5 MW tower top 2 m above the published 62 m; build one prototype per class if that matters.
CLASSES = {
    "3mw": {"scale": 1.0, "rated_power_kw": 3000, "farm": "한경풍력", "stage": "2단계 (한경5~9호기)",
            "basis": f"Tower 80 m and blade 44 m for the five 2008 units ({MUSEUM}, secondary); 90 m rotor is the V90-3.0 "
                     "brochure value (exa-results/vestas-models-2026-09-30.md) and the model code itself is tertiary. Hub = tower top + 1.8 m, estimated."},
    "1.5mw": {"scale": .8, "rated_power_kw": 1500, "farm": "한경풍력", "stage": "1단계 (한경1~3호기; 4호기 absent from KOSPO unit data)",
              "basis": f"Tower 62 m and blade ~35 m for the 2004 units ({MUSEUM}); rotor 72 m = 0.8 x 90. Uniform scaling puts the "
                       "tower top at 64 m, 2 m above the published 62 m; estimated."},
    "850kw": {"scale": 52 / 90, "rated_power_kw": 850, "farm": "신창소규모풍력", "stage": "제주에너지공사 1,700 kW (2기), inferred",
              "basis": "Vestas 850 kW (secondary operating table) -> V52 52 m rotor is inferred; hub 47 m is an estimate from the uniform "
                       "scale (z17 imagery shows visibly smaller rotors and shadows than the other eight)."},
}
# ponytail: per-turbine class from OSM plant_output where it is a Hankyung nameplate (1.5/3 MW, unverified); "3.75 MW" is
# none (KOSPO: 1.5x4, 3x5), so those default to the majority 3 MW class. One of them is probably a 1.5 MW unit; fix
# when KOSPO publishes unit positions. Untagged 5679/5769 are the two small turbines in the z17 mosaic.
TURBINES = {5674: "3mw", 5675: "3mw", 5676: "1.5mw", 5677: "1.5mw", 5678: "3mw", 5679: "850kw",
            5680: "3mw", 5681: "3mw", 5682: "3mw", 5769: "850kw"}
PAD = PBRMaterial(name="onshore_pad_concrete", baseColorFactor=[168, 164, 152, 255], metallicFactor=0, roughnessFactor=.9)


def turbine_prototype() -> trimesh.Scene:
    proto, _ = prototype({**SPEC, "verified_operator_spec": {"rotor_diameter_m": 90.0},  # tower base 0.9 m = pad top
                          "estimated": {**SPEC["estimated"], "hub_height_m": 81.8, "deck_height_m": .45}})
    # Octagonal pad ~22 m across, as on hub:power_plant:5674 in the z19 tiles; 1.5 m thick, top at the tower base.
    pad = normals(trimesh.creation.cylinder(radius=11, height=1.5, sections=8, transform=translation(0, .15, 0)))
    pad.visual = TextureVisuals(material=PAD)
    proto.geometry["pad"] = pad
    return proto


def add_onshore_wind(scene: trimesh.Scene, frame: dict, height_at) -> dict:
    plants = GIS / "power_plant.geojsonl"
    manifest = json.loads((GIS / "manifest.json").read_text())
    assert sha(plants) == next(d["sha256"] for d in manifest["datasets"] if d["file"] == plants.name)
    ids = {f"hub:power_plant:{i}" for i in TURBINES}
    with plants.open() as stream:
        features = sorted((f for line in stream if (f := json.loads(line))["id"] in ids), key=lambda f: f["properties"]["source_id"])
    assert len(features) == len(TURBINES)
    proto = turbine_prototype()
    lon, lat = np.array([f["geometry"]["coordinates"] for f in features]).T
    xz = to_scene(lon, lat, frame)
    ground = height_at(xz[:, 0], xz[:, 1])
    facilities, counts = [], Counter()
    for f, (x, z), y in zip(features, xz, ground):
        props = f["properties"]
        name = TURBINES[props["source_id"]]
        c = CLASSES[name]
        counts[c["farm"]] += 1
        node, k = f"T{props['source_id']}", c["scale"]
        scene.graph.update(frame_to=node, frame_from="world",
                           matrix=translation(x, y, z) @ rotation(SPEC["estimated"]["yaw_deg"], (0, 1, 0)) @ np.diag([k, k, k, 1]))
        for parent, child, data in proto.graph.to_edgelist():
            if child == "TEMPLATE" or parent == "foundation":  # ponytail: prototype() also builds Tamra's jacket/deck; dropped here
                continue
            geometry = "pad" if child == "foundation" else data.get("geometry")
            if geometry:
                scene.geometry.setdefault(f"onshore_{geometry}", proto.geometry[geometry])
            scene.graph.update(frame_to=f"{node}_{child}", frame_from=node if parent == "TEMPLATE" else f"{node}_{parent}",
                               matrix=data["matrix"], **({"geometry": f"onshore_{geometry}"} if geometry else {}))
        nameplate = f"{c['rated_power_kw'] / 1000:g} MW"
        facilities.append({
            "id": f["id"], "kind": "wind", "subtype": "onshore", "node": node, "rotor_node": f"{node}_rotor", "rotor_axis": "x",
            "coordinates": f["geometry"]["coordinates"], "position": [float(x), float(y), float(z)],
            "name": f"{c['farm']} {counts[c['farm']]:02d}", "name_status": "display sequence, not the operator unit number",
            "farm": c["farm"], "farm_status": "inferred from imagery size class and operator unit counts; not an operator record",
            "class": name, "class_basis": f"OSM plant_output {props['plant_output']!r} (unverified)" if props["plant_output"] == nameplate
            else f"default; OSM plant_output {props['plant_output']!r} is not a Hankyung nameplate",
            "rated_power_kw": c["rated_power_kw"], "rotor_diameter_m": round(90 * k, 2), "hub_height_m": round(81.8 * k, 2),
            "source_properties": props, "dimensions_estimated": True, "model_matching": "unverified", "telemetry": None,
            **({"generation": {"plant_id": 51, "api": "/api/v1/jeju/pv/generation?plant_id=51",
                                "scope": "farm total (KOSPO data.go.kr 15043410 matches plant 51); unit-to-position mapping "
                                         "unpublished, so no value belongs to this turbine"}} if c["farm"] == "한경풍력" else {})})
    return {"count": len(facilities), "facilities": facilities,
            "classes": {n: {"rotor_diameter_m": round(90 * c["scale"], 2), "hub_height_m": round(81.8 * c["scale"], 2), "tower_top_m": round(80 * c["scale"], 2),
                            **{key: c[key] for key in ("rated_power_kw", "farm", "stage", "basis")}} for n, c in CLASSES.items()},
            "sources": [{"kind": "GIS_positions", "path": str(plants.relative_to(ROOT)), "sha256": sha(plants)},
                        {"kind": "operator_capacity", "url": KOSPO, "fact": "한경풍력 1.5x4, 3x5 MW, VESTAS, '07.11"},
                        {"kind": "dimensions", "url": MUSEUM, "fact": "2004 units 1.5 MW, tower 62 m, blade ~35 m; 2008 units tower 80 m, blade 44 m"},
                        {"kind": "identity_notes", "path": "exa-results/hangyoung-identity-2026-09-30.md"}],
            "limits": ["Positions are OSM-derived GIS points (all ten sit on turbines visible in the z17 VWorld mosaic); not surveyed.",
                       "Unit-to-position mapping (한경1~3, 5~9호기) is unknown; per-turbine class follows unverified OSM output tags.",
                       "Nacelle, hub, blade section, tower taper and pads are estimated shapes from build.py's Tamra prototype, uniformly scaled.",
                       "Yaw is a fixed estimate; the viewer may re-yaw nacelles from observed wind."]}


def self_test() -> None:
    frame = {"horizontal_crs": "EPSG:32652", "origin_easting_northing": [236820.23997262522, 3692887.4836922204]}
    height_at = lambda x, z: 5 + .01 * np.asarray(x, float)
    scene = trimesh.Scene(base_frame="world")
    sentinel = normals(trimesh.creation.box())
    scene.geometry["tower"] = sentinel  # the local scene already holds Tamra's un-prefixed "tower" etc.
    result = add_onshore_wind(scene, frame, height_at)
    assert scene.geometry["tower"] is sentinel, "onshore geometry must not replace existing scene geometry"
    with (GIS / "power_plant.geojsonl").open() as stream:
        source = {f["id"]: f for line in stream if (f := json.loads(line))["properties"]["plant_method"] == "wind_turbine"}
    facilities = result["facilities"]
    ids = [f"hub:power_plant:{i}" for i in (5674, 5675, 5676, 5677, 5678, 5679, 5680, 5681, 5682, 5769)]
    assert [f["id"] for f in facilities] == ids and result["count"] == 10
    assert Counter(f["class"] for f in facilities) == {"3mw": 6, "1.5mw": 2, "850kw": 2}
    assert Counter(f["farm"] for f in facilities)["한경풍력"] == 8
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.glb"
        path.write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))
        reopened = trimesh.load(path, force="scene", process=False)
        tree = glb_tree(path)
    names = [n["name"] for n in tree["nodes"]]
    assert len(names) == len(set(names)), "semantic node names must be unique"
    nodes = {n["name"]: n for n in tree["nodes"]}
    assert all(g == "tower" or g.startswith("onshore_") for g in reopened.geometry), sorted(reopened.geometry)
    for f in facilities:
        c = result["classes"][f["class"]]
        x, y, z = f["position"]
        assert f["coordinates"] == source[f["id"]]["geometry"]["coordinates"] and f["source_properties"] == source[f["id"]]["properties"]
        assert np.isclose(y, height_at(x, z))
        o = frame["origin_easting_northing"]
        lon, lat = project_crs("EPSG:32652", "EPSG:4326", [x + o[0]], [o[1] - z])
        assert np.allclose([lon[0], lat[0]], f["coordinates"], atol=1e-8, rtol=0)
        node = f["node"]
        assert node == f"T{f['id'].split(':')[-1]}" and f["rotor_node"] == f"{node}_rotor" and f"{node}_nacelle" in nodes
        assert {f"{node}_blade_{i}" for i in (1, 2, 3)} <= {names[i] for i in nodes[f["rotor_node"]]["children"]}
        assert f"{node}_foundation" in nodes and f"{node}_jacket_submerged" not in nodes and f"{node}_deck" not in nodes
        rotor = reopened.graph[f["rotor_node"]][0]
        assert abs(rotor[1, 3] - y - c["hub_height_m"]) < .01, (f["id"], rotor[1, 3] - y)
        axis = rotor[:3, 0] / np.linalg.norm(rotor[:3, 0])
        matrix, geometry = reopened.graph[f"{node}_blade_1"]
        v = trimesh.transform_points(reopened.geometry[geometry].vertices, matrix) - rotor[:3, 3]
        radius = np.linalg.norm(v - np.outer(v @ axis, axis), axis=1).max()
        assert abs(2 * radius - c["rotor_diameter_m"]) < .02, (f["id"], 2 * radius)
    for g in reopened.geometry.values():
        assert np.isfinite(g.vertices).all() and np.allclose(np.linalg.norm(g.vertex_normals, axis=1), 1, atol=2e-3)
    print(f"PASS {result['count']} onshore turbines ({dict(Counter(f['class'] for f in facilities))}): source ids/coordinates, "
          "1e-8 deg round trip, ground from height_at, T<id>/_rotor/_nacelle/_blade_1..3/_foundation nodes, no jacket, "
          "hub height and rotor diameter per class, prefixed geometry, unique names, unit normals")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true")
    if parser.parse_args().self_test:
        return self_test()
    from build_local import surface_height  # lazy, as in roads.py: build_local may import this module
    frame = json.loads((ROOT / "var/rendering/local/manifest.json").read_text())["coordinateFrame"]
    height_at = surface_height(trimesh.load(ROOT / "var/rendering/local/scene.glb", force="scene"))
    scene = trimesh.Scene(base_frame="world")
    result = add_onshore_wind(scene, frame, height_at)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "preview.glb").write_bytes(trimesh.exchange.gltf.export_glb(scene, include_normals=True))
    (OUT / "onshore_wind.json").write_text(json.dumps({"coordinateFrame": frame, **result}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"glb": str((OUT / "preview.glb").relative_to(ROOT)), "sha256": sha(OUT / "preview.glb"),
                      "turbines": [{k: f[k] for k in ("id", "name", "class", "coordinates", "position")} for f in result["facilities"]]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
