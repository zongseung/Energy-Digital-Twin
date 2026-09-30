#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["rasterio==1.4.3", "numpy==2.2.6", "shapely==2.1.2"]
# ///
# ─── How to run ───
# uv run renderers/site/prepare.py --self-test
# uv run renderers/site/prepare.py
# ──────────────────
"""Prepare measured-coordinate Sinchang source geometry without invented asset dimensions."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Final, Literal, assert_never

import numpy as np
import rasterio
from rasterio.warp import transform
from shapely.geometry import box, mapping, shape

AOI: Final = (126.155, 33.325, 126.19, 33.36)
ORIGIN: Final = (126.17217, 33.3430267)
CRS: Final = "EPSG:32652"
ORIGIN_E: Final = transform("EPSG:4326", CRS, [ORIGIN[0]], [ORIGIN[1]])[0][0]
ORIGIN_N: Final = transform("EPSG:4326", CRS, [ORIGIN[0]], [ORIGIN[1]])[1][0]


class PreparationError(ValueError):
    """A source or projection invariant failed before publishing the scene."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PreparationError(message)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def positive_height(value: str | int | float | None) -> float | None:
    """Keep finite positive provider heights; floors and unknown values supply no height."""
    if type(value) not in (str, int, float):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def project(lon: float, lat: float, height: float) -> list[float]:
    require(all(math.isfinite(v) for v in (lon, lat, height)), "nonfinite position")
    require(-180 <= lon <= 180 and -90 <= lat <= 90, "invalid WGS84 position")
    east, north = transform("EPSG:4326", CRS, [lon], [lat])
    return [east[0] - ORIGIN_E, height, ORIGIN_N - north[0]]


def pixel_index(lon: float, lat: float, affine: rasterio.Affine,
                width: int, height: int) -> tuple[int, int] | None:
    """Return the containing native pixel, never clamp an outside point to the edge."""
    if not all(math.isfinite(v) for v in (lon, lat)):
        return None
    col, row = (~affine) * (lon, lat)
    if not 0 <= col < width or not 0 <= row < height:
        return None
    return math.floor(row), math.floor(col)


def self_test() -> None:
    assert np.allclose(project(*ORIGIN, 12.5), [0, 12.5, 0], atol=1e-7)
    assert project(ORIGIN[0] + .001, ORIGIN[1], 0)[0] > 90
    assert project(ORIGIN[0], ORIGIN[1] + .001, 0)[2] < -110
    assert positive_height("12.5") == 12.5
    assert all(positive_height(v) is None for v in (None, "0", -1, "NaN", "inf", True))
    affine = rasterio.Affine(1, 0, 126, 0, -1, 34)
    assert pixel_index(126.5, 33.5, affine, 1, 1) == (0, 0)
    assert pixel_index(127, 33.5, affine, 1, 1) is None
    assert pixel_index(float("nan"), 33.5, affine, 1, 1) is None
    try:
        project(181, 33, 0)
    except PreparationError:
        print("PASS: metre projection, unexaggerated height, unknown height and pixel bounds")
        return
    raise AssertionError("invalid coordinate was accepted")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path(".worktrees/data"))
    parser.add_argument("--output", type=Path, default=Path("var/rendering/site/scene.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    source = args.data_root / "var/data/geography/source-03e02ef"
    source_manifest = json.loads((source / "manifest.json").read_text())
    terrain_manifest = json.loads((args.data_root / "data/terrain/manifest.json").read_text())
    require(source_manifest["complete"], "geography collection incomplete")
    used: list[Literal["building_info.geojsonl", "road.geojsonl", "power_plant.geojsonl"]] = [
        "building_info.geojsonl", "road.geojsonl", "power_plant.geojsonl"]
    provenance = []
    for name in used:
        metadata = next(item for item in source_manifest["datasets"] if item["file"] == name)
        require(digest(source / name) == metadata["sha256"], f"source hash mismatch: {name}")
        provenance.append(metadata)
    for name in ("sinchang_dem.tif", "sinchang_wbm.tif"):
        metadata = next(a for a in terrain_manifest["artifacts"] if Path(a["path"]).name == name)
        require(digest(args.data_root / metadata["path"]) == metadata["sha256"], f"terrain hash mismatch: {name}")
        provenance.append(metadata)
    with rasterio.open(args.data_root / "var/data/terrain/sinchang_dem.tif") as dem, \
            rasterio.open(args.data_root / "var/data/terrain/sinchang_wbm.tif") as water:
        heights = dem.read(1)
        water_classes = water.read(1)
        require(dem.shape == water.shape == (127, 127), "unexpected native crop shape")
        require(dem.transform == water.transform and dem.crs == water.crs, "unaligned water mask")
        require(dem.crs.to_epsg() == 4326 and np.isfinite(heights).all(), "invalid terrain grid")
        require(np.all(dem.read_masks(1) != 0), "terrain crop contains masked pixels")
        affine, width, height = dem.transform, dem.width, dem.height
        bounds = list(dem.bounds)

    def surface(lon: float, lat: float) -> float | None:
        index = pixel_index(lon, lat, affine, width, height)
        return float(heights[index]) if index is not None else None

    rows, cols = np.indices((height, width))
    lons, lats = rasterio.transform.xy(affine, rows.ravel(), cols.ravel())
    east, north = transform("EPSG:4326", CRS, lons, lats)
    positions = np.column_stack((np.asarray(east) - ORIGIN_E, heights.ravel(),
                                 ORIGIN_N - np.asarray(north))).ravel().tolist()
    uvs = np.column_stack((cols.ravel() / (width - 1), 1 - rows.ravel() / (height - 1))).ravel().tolist()
    indices = []
    for row in range(height - 1):
        for col in range(width - 1):
            i = row * width + col
            indices.extend([i, i + width, i + 1, i + 1, i + width, i + width + 1])
    aoi = box(*AOI)
    buildings, roads, turbines, facilities = [], [], [], []
    building_features = []
    for name in used:
        with (source / name).open() as stream:
            for line in stream:
                feature = json.loads(line)
                geometry = shape(feature["geometry"])
                if not geometry.intersects(aoi):
                    continue
                require(geometry.is_valid, f"invalid geometry: {feature['id']}")
                clipped = geometry.intersection(aoi)
                record = {"id": str(feature["id"]), "source_properties": feature["properties"],
                          "source_geometry": feature["geometry"]}
                match name:
                    case "building_info.geojsonl":
                        polygons = [clipped] if clipped.geom_type == "Polygon" else list(clipped.geoms)
                        require(all(p.geom_type == "Polygon" for p in polygons), "unexpected building intersection")
                        anchor = clipped.representative_point()
                        base = surface(anchor.x, anchor.y)
                        provider_height = positive_height(feature["properties"].get("height"))
                        require(base is not None, "building anchor outside DSM crop")
                        record.update(polygons=[[[project(x, y, base) for x, y in ring.coords]
                                                 for ring in [polygon.exterior, *polygon.interiors]]
                                                for polygon in polygons],
                                      base_height_m=base, height_m=provider_height,
                                      base_height_status="dsm_surface_proxy_unverified",
                                      height_status="provider_positive_unverified" if provider_height else "unknown",
                                      extrusion_allowed=provider_height is not None)
                        buildings.append(record)
                        if provider_height is not None:
                            building_features.append({"type": "Feature", "id": record["id"],
                                "geometry": mapping(clipped), "properties": {
                                    "source_id": record["id"], "height_m": provider_height,
                                    "height_status": "provider_positive_unverified"}})
                    case "road.geojsonl":
                        paths = [clipped] if clipped.geom_type == "LineString" else list(clipped.geoms)
                        require(all(p.geom_type == "LineString" for p in paths), "unexpected road intersection")
                        coordinates = [list(p.coords) for p in paths]
                        require(all(surface(x, y) is not None for path in coordinates for x, y in path),
                                "road intersection outside DSM crop")
                        record.update(paths=[[project(x, y, surface(x, y)) for x, y in path]
                                             for path in coordinates], width_m=None,
                                      height_status="draped_on_dsm_surface_proxy")
                        roads.append(record)
                    case "power_plant.geojsonl":
                        lon, lat = feature["geometry"]["coordinates"][:2]
                        base = surface(lon, lat)
                        require(base is not None, "facility outside DSM crop")
                        index = pixel_index(lon, lat, affine, width, height)
                        record.update(position=project(lon, lat, base), coordinates=[lon, lat],
                                      water_class=int(water_classes[index]),
                                      base_height_status="dsm_surface_proxy_unverified",
                                      asset_dimensions_m=None, measured_model_available=False)
                        target = turbines if feature["properties"].get("plant_method") == "wind_turbine" else facilities
                        target.append(record)
                    case unexpected:
                        assert_never(unexpected)
    scene = {
        "schema_version": 1, "data_kind": "georeferenced_site_geometry", "aoi_bbox": AOI,
        "projection": {"horizontal_crs": CRS, "vertical_crs": "EPSG:3855", "vertical_datum": "EGM2008",
                       "origin_lon_lat": ORIGIN, "origin_easting_northing": [ORIGIN_E, ORIGIN_N],
                       "origin_height_m": 0, "units": "m", "axes": "x east, y up, z south", "scale": 1.0},
        "terrain": {"width": width, "height": height, "bounds_lon_lat": bounds,
                    "positions": positions, "indices": indices, "uvs": uvs,
                    "water_classes": water_classes.ravel().tolist(), "water_class_legend": terrain_manifest["water_body_mask"]["values"],
                    "uv_origin": "southwest (u east, v north); source raster rows run north to south",
                    "elevation_exaggeration": 1.0, "texture": None},
        "buildings": buildings, "roads": roads, "turbines": turbines, "facilities": facilities,
        "metadata": {"prepared_at": datetime.now(timezone.utc).isoformat(), "source_commit": "03e02ef",
                     "source_manifest_sha256": digest(source / "manifest.json"), "sources": provenance,
                     "copernicus_license": terrain_manifest["license"],
                     "attribution": [terrain_manifest["license"]["required_modified_notice"],
                                     "Building footprints and height attributes: VWorld LT_C_BLDGINFO",
                                     "Road and power facility records: Energy-hub source tables; original properties retained"],
                     "geometry_contract": "Building polygons[polygon][ring][vertex[x,H,z]], outer ring first; roads paths[path][vertex[x,H,z]]. Display geometries clipped to AOI; full source_geometry retained.",
                     "height_contract": "Building base is nearest native DSM pixel at clipped footprint representative point, not surveyed ground. Positive provider height permits approximate extrusion; null height forbids extrusion.",
                     "photo_reconstruction_complete": False,
                     "limitations": ["Coarse DSM includes vegetation and structures; a building added on DSM can double-count roof height.",
                                     "No true-colour orthophoto, building colour, facade/roof texture, road width or turbine dimensions are supplied.",
                                     "Source footprint overlaps remain; no cross-layer building merge or guessed geometry is performed.",
                                     "Water class and zero DSM ocean height do not establish a current tide or underwater terrain.",
                                     "GIS generator IDs have not been matched to the turbines in reference photographs.",
                                     "This source-geometry preparation is not completed photographic scene reconstruction."]}}
    require(len({b["id"] for b in buildings}) == len(buildings), "duplicate building IDs")
    require(all(b["extrusion_allowed"] == (b["height_m"] is not None) for b in buildings), "unknown-height extrusion")
    require(max(indices) < len(positions) // 3 and np.isfinite(positions).all(), "invalid terrain mesh")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scene, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    buildings_output = args.output.with_name("buildings.geojson")
    buildings_output.write_text(json.dumps({"type": "FeatureCollection", "features": building_features},
                                          ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    print(f"PASS {args.output}: {len(indices)//3} triangles, {len(buildings)} footprints "
          f"({sum(b['extrusion_allowed'] for b in buildings)} with provider height), {len(roads)} roads, "
          f"{len(turbines)} turbine points, {len(facilities)} other facility points")
    print(f"PASS {buildings_output}: {len(building_features)} positive-height EPSG:4326 footprints")


if __name__ == "__main__":
    main()
