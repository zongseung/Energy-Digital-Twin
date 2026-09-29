#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4"]
# ///
"""Small real-GLB checks for photo asset placement."""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import trimesh

from photo_assets import load_placements, place_asset


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(root):
    generated = root / "var/generated/example"
    generated.mkdir(parents=True)
    photo = root / "var/data/photos/example.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"source photo")
    asset = generated / "asset.glb"
    trimesh.Scene(trimesh.creation.box(extents=[2, 1, 1])).export(asset)
    meta = {"status": "generated_unvalidated", "image": {"sha256": digest(photo)},
            "asset": {"sha256": digest(asset)}}
    (generated / "metadata.json").write_text(json.dumps(meta))
    facility = {"id": "hub:substation:example", "kind": "substation", "node": "station_example",
                "position": [100.0, 5.0, -20.0], "coordinates": [126.17, 33.34]}
    frame = {"horizontal_crs": "EPSG:32652", "vertical_crs": "EPSG:3855",
             "axes": "x east, y up, z south", "scale": 1.0}
    item = {"facility_id": facility["id"], "generated_dir": "var/generated/example",
            "photo_path": "var/data/photos/example.jpg", "photo_sha256": digest(photo),
            "asset_sha256": digest(asset), "license": "CC-BY-SA-4.0",
            "facility_match_evidence": "https://example.org/site-check",
            "asset_anchor_xyz": [0, 0, 0], "length_endpoints_xyz": [[-1, 0, 0], [1, 0, 0]],
            "length_m": 10.0, "up_axis": "Y", "yaw_degrees": 0,
            "status": "verified_for_placement"}
    manifest = root / "var/rendering/photo-assets/manifest.json"
    manifest.parent.mkdir(parents=True)
    return manifest, facility, frame, item


def expect_rejected(manifest, facility, frame, root, item):
    manifest.write_text(json.dumps({"schema_version": 1, "assets": [item]}))
    try:
        load_placements(manifest, [facility], frame, root=root)
    except ValueError:
        return
    raise AssertionError(f"accepted invalid placement: {item}")


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        manifest, facility, frame, item = fixture(root)
        assert load_placements(manifest, [facility], frame, root=root) == {}
        manifest.write_text(json.dumps({"schema_version": 1, "assets": []}))
        assert load_placements(manifest, [facility], frame, root=root) == {}
        manifest.write_text(json.dumps({"schema_version": 1, "assets": [item]}))
        placement = load_placements(manifest, [facility], frame, root=root)[facility["id"]]
        scene = trimesh.Scene(base_frame="world")
        names = place_asset(scene, facility, placement, root=root)
        assert names and facility["node"] in scene.graph.nodes
        assert np.allclose(scene.bounds[:, 0], [95, 105], atol=1e-5)
        assert np.allclose(scene.bounds[:, 1], [2.5, 7.5], atol=1e-5)
        assert np.allclose(scene.bounds[:, 2], [-22.5, -17.5], atol=1e-5)
        for field, value in (("facility_id", "other"), ("facility_match_evidence", ""),
                             ("license", ""), ("length_m", 0), ("length_m", -2),
                             ("length_m", float("nan")), ("yaw_degrees", None),
                             ("photo_sha256", "0" * 64), ("asset_sha256", "0" * 64),
                             ("status", "generated_unvalidated"),
                             ("generated_dir", "../../outside")):
            bad = copy.deepcopy(item)
            bad[field] = value
            expect_rejected(manifest, facility, frame, root, bad)
        bad = copy.deepcopy(item)
        bad.pop("yaw_degrees")
        expect_rejected(manifest, facility, frame, root, bad)
        bad = copy.deepcopy(item)
        bad["length_endpoints_xyz"] = [[0, 0, 0], [0, 0, 0]]
        expect_rejected(manifest, facility, frame, root, bad)
        print("photo asset placement: PASS")


if __name__ == "__main__":
    main()
