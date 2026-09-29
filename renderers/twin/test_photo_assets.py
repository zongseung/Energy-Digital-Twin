#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy==2.2.6", "trimesh==4.7.4", "rasterio==1.4.3", "Pillow==11.3.0", "shapely==2.1.2"]
# ///
"""Small real-GLB checks for photo asset placement."""
import copy
import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import trimesh
from PIL import Image

from photo_assets import load_placements, place_asset
import build_local


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def photo_mesh():
    mesh = trimesh.creation.box(extents=[2, 1, 1])
    mesh.vertex_normals = trimesh.util.unitize(mesh.vertices)
    texture = Image.new("RGB", (2, 2))
    texture.putdata([(200, 70, 50), (20, 180, 30), (40, 60, 220), (220, 190, 10)])
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=np.column_stack(((mesh.vertices[:, 0] + 1) / 2, mesh.vertices[:, 1] + .5)),
        material=trimesh.visual.material.PBRMaterial(baseColorTexture=texture))
    return mesh


def fixture(root):
    generated = root / "var/generated/example"
    generated.mkdir(parents=True)
    photo = root / "var/data/photos/example.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"source photo")
    asset = generated / "asset.glb"
    trimesh.Scene(photo_mesh()).export(asset)
    meta = {"status": "generated_unvalidated", "image": {"sha256": digest(photo),
            "source": "fixture photo", "license": "CC-BY-SA-4.0"},
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
            "length_evidence": "https://example.org/measured-length",
            "orientation_evidence": "https://example.org/site-heading",
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


def main(keep_scene=None):
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
        original = trimesh.load(root / item["generated_dir"] / "asset.glb", force="scene", process=False)
        assert np.allclose(next(iter(scene.geometry.values())).vertex_normals,
                           next(iter(original.geometry.values())).vertex_normals)
        for field, value in (("facility_id", "other"), ("facility_id", []),
                             ("facility_match_evidence", ""),
                             ("length_evidence", ""), ("orientation_evidence", ""),
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
        bad = copy.deepcopy(item)
        bad["length_endpoints_xyz"] = [[-1e308, 0, 0], [1e308, 0, 0]]
        expect_rejected(manifest, facility, frame, root, bad)
        for kind in ("wind", "line"):
            expect_rejected(manifest, {**facility, "kind": kind}, frame, root, item)
        metadata_path = root / "var/generated/example/metadata.json"
        metadata = json.loads(metadata_path.read_text())
        asset = root / item["generated_dir"] / "asset.glb"
        payload = asset.read_bytes()
        json_length = struct.unpack_from("<I", payload, 12)[0]
        original_document = json.loads(payload[20:20 + json_length])
        for resource in ("images", "buffers"):
            document = copy.deepcopy(original_document)
            document[resource][0]["uri"] = "unhashed-external-resource.bin"
            if resource == "images":
                document[resource][0].pop("bufferView")
            chunk = json.dumps(document).encode()
            chunk += b" " * (-len(chunk) % 4)
            binary = payload[20 + json_length:]
            asset.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(chunk) + len(binary))
                              + struct.pack("<I4s", len(chunk), b"JSON") + chunk + binary)
            bad = {**item, "asset_sha256": digest(asset)}
            changed_metadata = copy.deepcopy(metadata)
            changed_metadata["asset"]["sha256"] = bad["asset_sha256"]
            metadata_path.write_text(json.dumps(changed_metadata))
            expect_rejected(manifest, facility, frame, root, bad)
        asset.write_bytes(payload)
        bad = {**item, "length_m": 1e308, "length_endpoints_xyz": [[0, 0, 0], [1e-308, 0, 0]]}
        metadata_path.write_text(json.dumps(metadata))
        expect_rejected(manifest, facility, frame, root, bad)
        metadata["image"]["license"] = "unverified"
        metadata_path.write_text(json.dumps(metadata))
        expect_rejected(manifest, facility, frame, root, item)
    publication()
    integration(keep_scene)
    print("photo asset placement: PASS")


def publication():
    with TemporaryDirectory() as directory:
        output = Path(directory) / "live"
        output.mkdir()
        for name in ("scene.glb", "manifest.json", "CREDITS.txt"):
            (output / name).write_bytes(b"previous")
        def build_fixture(target, *_):
            for name in ("scene.glb", "manifest.json", "CREDITS.txt"):
                (target / name).write_bytes(b"new")
            return {"files": [{"path": "scene.glb"}]}
        with patch.object(build_local, "build", side_effect=build_fixture), \
             patch.object(build_local, "verify", side_effect=ValueError("rejected scene")):
            try:
                build_local.build_verified(output, Path("texture"), Path("metadata"))
            except ValueError:
                pass
            else:
                raise AssertionError("Verification failure was ignored")
        assert all(path.read_bytes() == b"previous" for path in output.iterdir())
        with patch.object(build_local, "build", side_effect=build_fixture), \
             patch.object(build_local, "verify", return_value={"status": "PASS"}):
            build_local.build_verified(output, Path("texture"), Path("metadata"))
        assert (output / "scene.glb").read_bytes() == b"new"
        assert json.loads((output / "verification.json").read_text())["status"] == "PASS"


def integration(keep_scene=None):
    root = build_local.ROOT
    (root / "var/generated").mkdir(parents=True, exist_ok=True)
    (root / "var/data/photos").mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="photo-placement-", dir=root / "var/generated") as generated_name, \
         TemporaryDirectory(prefix="photo-placement-", dir=root / "var/data/photos") as photo_name, \
         TemporaryDirectory(prefix="photo-placement-output-") as output_name:
        generated, photo_dir, output = map(Path, (generated_name, photo_name, output_name))
        photo = photo_dir / "source.jpg"
        photo.write_bytes(b"photo fixture")
        asset = generated / "asset.glb"
        trimesh.Scene(photo_mesh()).export(asset)
        (generated / "metadata.json").write_text(json.dumps({
            "status": "generated_unvalidated", "image": {"sha256": digest(photo),
            "source": "fixture photo", "license": "CC-BY-SA-4.0"},
            "asset": {"sha256": digest(asset)}}))
        original = json.loads((root / "var/rendering/grid/manifest.json").read_text())
        facility = next(f for f in original["facilities"] if f["kind"] == "substation"
                        and build_local.inside(f["position"], original["terrain"]["extent"]))
        placement = {"facility_id": facility["id"], "generated_dir": str(generated.relative_to(root)),
                     "photo_path": str(photo.relative_to(root)), "photo_sha256": digest(photo),
                     "asset_sha256": digest(asset), "license": "CC-BY-SA-4.0",
                     "facility_match_evidence": "https://example.org/fixture",
                     "length_evidence": "https://example.org/measured-length",
                     "orientation_evidence": "https://example.org/site-heading",
                     "asset_anchor_xyz": [1, 0.5, 0],
                     "length_endpoints_xyz": [[-1, 0, 0], [1, 0, 0]], "length_m": 10,
                     "up_axis": "Y", "yaw_degrees": 90, "status": "verified_for_placement"}
        manifest_path = output / "placements.json"
        manifest_path.write_text(json.dumps({"schema_version": 1, "assets": [placement]}))
        result = build_local.build(output, root / "var/rendering/imagery/texture.jpg",
                                   root / "var/rendering/imagery/manifest.json",
                                   placements_path=manifest_path)
        adopted = next(f for f in result["facilities"] if f["id"] == facility["id"])
        assert adopted["node"] == facility["node"]
        assert adopted["photo_asset"]["verified_length_m"] == 10
        assert adopted["photo_asset"]["status"] == "photo_derived_estimated_geometry"
        assert adopted["photo_asset"]["source"] == "fixture photo"
        assert adopted["photo_asset"]["length_evidence"] == placement["length_evidence"]
        assert adopted["photo_asset"]["orientation_evidence"] == placement["orientation_evidence"]
        scene = trimesh.load(output / "scene.glb", force="scene")
        with (output / "scene.glb").open("rb") as stream:
            stream.read(12)
            length, kind = struct.unpack("<I4s", stream.read(8))
            assert kind == b"JSON"
            gltf = json.loads(stream.read(length))
        assert len(gltf["images"]) >= 2
        assert facility["node"] in scene.graph.nodes
        assert sum(n == facility["node"] for n in scene.graph.nodes) == 1
        assert any(n.startswith(facility["node"] + "_photo_") for n in scene.graph.nodes_geometry)
        photo_node = next(n for n in scene.graph.nodes_geometry if n.startswith(facility["node"] + "_photo_"))
        actual_mesh = scene.geometry[scene.graph[photo_node][1]]
        source_scene = trimesh.load(asset, force="scene", process=False)
        source_mesh = next(iter(source_scene.geometry.values()))
        assert np.allclose(actual_mesh.visual.uv, source_mesh.visual.uv)
        assert (actual_mesh.visual.material.baseColorTexture.tobytes()
                == source_mesh.visual.material.baseColorTexture.tobytes())
        transform = scene.graph[facility["node"]][0]
        anchor = transform @ np.array([*placement["asset_anchor_xyz"], 1])
        assert np.allclose(anchor[:3], facility["position"], atol=1e-5)
        assert np.allclose(transform[:3, :3] @ [2, 0, 0], [0, 0, -10], atol=1e-5)
        assert build_local.verify(output)["status"] == "PASS"
        if keep_scene is not None:
            shutil.copytree(output, keep_scene)
        manifest_path.write_text(json.dumps({"schema_version": 1, "assets": [placement, placement]}))
        try:
            build_local.build(output, root / "var/rendering/imagery/texture.jpg",
                              root / "var/rendering/imagery/manifest.json", placements_path=manifest_path)
        except ValueError:
            pass
        else:
            raise AssertionError("Duplicate photo placement accepted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-scene", type=Path, help="Copy the synthetic QA scene to a fresh directory for engine checks")
    main(parser.parse_args().keep_scene)
