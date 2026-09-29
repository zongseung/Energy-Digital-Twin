"""Crop the existing DEM with installed GDAL/rasterio; preserve heights and NoData."""
import json
import sys

import rasterio
from rasterio.windows import Window, from_bounds

with rasterio.open(sys.argv[1]) as source:
    if source.crs.to_epsg() != 4326:
        raise ValueError("unexpected DEM CRS")
    window = from_bounds(126, 33, 127, 33.7, source.transform)
    window = window.round_offsets().round_lengths().intersection(
        Window(0, 0, source.width, source.height)
    )
    values = source.read(1, window=window)
    profile = source.profile.copy()
    profile.update(
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        transform=source.window_transform(window),
        compress="deflate",
        tiled=True,
        blockxsize=256,
        blockysize=256,
    )
    with rasterio.open(sys.argv[2], "w", **profile) as target:
        target.write(values, 1)
        metadata = {
            "width": target.width,
            "height": target.height,
            "pixel_size_degrees": list(target.res),
            "raster_bounds": list(target.bounds),
            "nodata": target.nodata,
            "dtype": target.dtypes[0],
            "feature_count": None,
        }
print(json.dumps(metadata))
