"""Place manually verified photo GLBs in the local metre-scale scene."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import sys

import numpy as np
import trimesh


ROOT = Path(__file__).resolve().parents[2]
# Standalone uv scripts reuse the inference runner's embedded-resource validator.
sys.path.insert(0, str(ROOT))
from inference.run import check_glb


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _point(value: object) -> np.ndarray:
    if not isinstance(value, (list, tuple)) or len(value) != 3 or any(type(v) not in (int, float) for v in value):
        raise ValueError("Expected a three-number point")
    point = np.asarray(value, dtype=float)
    if not np.isfinite(point).all():
        raise ValueError("Point must be finite")
    return point


def _local_path(root: Path, value: object, allowed: tuple[str, ...]) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("Asset path must be relative")
    try:
        path = (root / value).resolve(strict=True)
    except OSError as error:
        raise ValueError("Asset path is missing") from error
    if not any(path.is_relative_to(root / directory) for directory in allowed):
        raise ValueError("Asset path is outside approved data directories")
    return path


def load_placements(path: Path, facilities: list[dict], frame: dict,
                    root: Path = ROOT) -> dict[str, dict]:
    """Reject unverified inputs before any live scene node is replaced."""
    if not path.exists():
        return {}
    if any(frame.get(key) != value for key, value in {
        "horizontal_crs": "EPSG:32652", "vertical_crs": "EPSG:3855",
        "axes": "x east, y up, z south", "scale": 1.0,
    }.items()):
        raise ValueError("Photo asset coordinate frame differs from the local scene")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("assets"), list):
        raise ValueError("Invalid photo asset manifest")
    by_id = {facility["id"]: facility for facility in facilities}
    if len(by_id) != len(facilities):
        raise ValueError("Duplicate facility ID")
    accepted: dict[str, dict] = {}
    for item in data["assets"]:
        if not isinstance(item, dict) or item.get("status") != "verified_for_placement":
            raise ValueError("Photo asset is not verified for placement")
        identifier = item.get("facility_id")
        if not isinstance(identifier, str) or identifier not in by_id or identifier in accepted:
            raise ValueError("Unknown or duplicate facility ID")
        facility = by_id[identifier]
        if facility.get("kind") not in ("pv", "substation"):
            raise ValueError("Only static facilities may be replaced")
        _point(facility.get("position"))
        if not all(isinstance(item.get(key), str) and item[key].strip()
                   for key in ("license", "facility_match_evidence", "length_evidence", "orientation_evidence")):
            raise ValueError("Photo license, facility match, length and orientation evidence are required")
        if item.get("up_axis") != "Y" or type(item.get("yaw_degrees")) not in (int, float) or not math.isfinite(item["yaw_degrees"]):
            raise ValueError("Verified Y-up axis and finite yaw are required")
        if type(item.get("length_m")) not in (int, float) or not math.isfinite(item["length_m"]) or item["length_m"] <= 0:
            raise ValueError("Verified metric length is required")
        _point(item.get("asset_anchor_xyz"))
        endpoints = item.get("length_endpoints_xyz")
        if not isinstance(endpoints, list) or len(endpoints) != 2:
            raise ValueError("Two model length endpoints are required")
        model_length = math.dist(_point(endpoints[1]), _point(endpoints[0]))
        if not math.isfinite(model_length) or model_length <= 0:
            raise ValueError("Model reference length must be positive")
        scale = item["length_m"] / model_length
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("Derived scale must be positive and finite")
        generated = _local_path(root, item.get("generated_dir"), ("var/generated",))
        photo = _local_path(root, item.get("photo_path"), ("var/data/photos", ".worktrees/data/var/data/photos"))
        asset = generated / "asset.glb"
        metadata = generated / "metadata.json"
        if not asset.resolve().is_relative_to(generated) or not metadata.resolve().is_relative_to(generated):
            raise ValueError("Generated files must remain in their approved directory")
        if not asset.is_file() or not metadata.is_file() or not photo.is_file():
            raise ValueError("Photo generation files are incomplete")
        detail = json.loads(metadata.read_text())
        if not isinstance(detail, dict):
            raise ValueError("Invalid generation metadata")
        image = detail.get("image", {})
        if not isinstance(image, dict) or not isinstance(detail.get("asset"), dict):
            raise ValueError("Invalid generation metadata")
        if not isinstance(image.get("source"), str) or not image["source"].strip() or image.get("license") != item["license"]:
            raise ValueError("Inference photo source or license is missing or inconsistent")
        if not all(isinstance(item.get(key), str) and len(item[key]) == 64 for key in ("photo_sha256", "asset_sha256")):
            raise ValueError("Photo and asset hashes are required")
        if (item["photo_sha256"] != _sha(photo) or item["photo_sha256"] != image.get("sha256")
                or item["asset_sha256"] != _sha(asset) or item["asset_sha256"] != detail.get("asset", {}).get("sha256")):
            raise ValueError("Photo or asset hash mismatch")
        try:
            check_glb(asset)
        except (OSError, struct.error, KeyError, IndexError, TypeError) as error:
            raise ValueError("Invalid photo GLB") from error
        accepted[identifier] = item
    return accepted


def place_asset(dest: trimesh.Scene, facility: dict, placement: dict,
                root: Path = ROOT) -> list[str]:
    """Attach GLB geometry below the facility's existing scene node name."""
    asset = _local_path(root, placement["generated_dir"], ("var/generated",)) / "asset.glb"
    source = trimesh.load(asset, force="scene", process=False)
    if not source.geometry:
        raise ValueError("Photo GLB has no geometry")
    a, b = map(_point, placement["length_endpoints_xyz"])
    scale = placement["length_m"] / math.dist(a, b)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Derived scale must be positive and finite")
    anchor = _point(placement["asset_anchor_xyz"])
    transform = (trimesh.transformations.translation_matrix(_point(facility["position"]))
                 @ trimesh.transformations.rotation_matrix(math.radians(placement["yaw_degrees"]), [0, 1, 0])
                 @ np.diag([scale, scale, scale, 1.0])
                 @ trimesh.transformations.translation_matrix(-anchor))
    if not np.isfinite(transform).all():
        raise ValueError("Placement transform must be finite")
    node = facility["node"]
    if node in dest.graph.nodes:
        raise ValueError("Facility node already exists")
    dest.graph.update(frame_from=dest.graph.base_frame, frame_to=node, matrix=transform)
    names = []
    for index, original in enumerate(source.graph.nodes_geometry):
        matrix, geometry = source.graph[original]
        geometry_name = f"{node}_photo_geometry_{index}"
        child = f"{node}_photo_{index}"
        mesh = source.geometry[geometry].copy(include_cache=True)
        mesh.vertex_normals = mesh.vertex_normals.copy()
        dest.geometry[geometry_name] = mesh
        dest.graph.update(frame_from=node, frame_to=child, matrix=matrix, geometry=geometry_name)
        names.append(child)
    return names
