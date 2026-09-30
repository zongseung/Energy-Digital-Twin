#!/usr/bin/env python3
"""Cache a bounded VWorld Satellite mosaic with its Web Mercator georeference.

python3 renderers/twin/prepare_imagery.py [--self-test]
python3 renderers/twin/prepare_imagery.py --roofs   # z19 roof atlas for the Sinchang footprints
Uses the existing vworld_key; credentials are never written to output metadata.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import sys
from urllib.request import Request, urlopen

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'site'))
from configure import read_key

HALF_WORLD = math.pi * 6378137


def tile(lon, lat, zoom):
    if not (-180 <= lon < 180 and -85.05112878 < lat < 85.05112878):
        raise ValueError('Coordinate outside Web Mercator')
    n = 2 ** zoom
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def coverage(bbox, zoom):
    w, s, e, n = bbox
    if not w < e or not s < n:
        raise ValueError('Invalid geographic bounds')
    x0, y0 = tile(w, n, zoom)
    x1, y1 = tile(e, s, zoom)
    step = 2 * HALF_WORLD / 2 ** zoom
    bounds = [x0 * step - HALF_WORLD, HALF_WORLD - (y1 + 1) * step,
              (x1 + 1) * step - HALF_WORLD, HALF_WORLD - y0 * step]
    return (x0, y0, x1, y1), bounds


def validate(payload):
    if not payload.startswith(b'\xff\xd8\xff'):
        raise ValueError('Satellite service did not return JPEG')
    with Image.open(BytesIO(payload)) as image:
        image.load()
        if image.size != (256, 256):
            raise ValueError('Unexpected satellite tile dimensions')
        return image.convert('RGB')


def fetch(key, zoom, cache, job):
    x, y = job
    path = cache / f'{x}-{y}.jpg'
    if path.is_file():
        payload = path.read_bytes()
        validate(payload)
    else:
        url = f'https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{zoom}/{y}/{x}.jpeg'
        try:
            request = Request(url, headers={'Referer': 'http://localhost:18080/', 'User-Agent': 'EnergyDigitalTwin-local-preview'})
            with urlopen(request, timeout=30) as response:
                payload = response.read(2_000_001)
            if len(payload) > 2_000_000:
                raise ValueError('Tile response too large')
            validate(payload)
        except Exception as error:
            raise RuntimeError(f'Satellite tile {zoom}/{y}/{x} failed ({type(error).__name__})') from None
        path.write_bytes(payload)
    return x, y, payload


def roof_atlas(bboxes, tiles, width=4096):
    """Crop each lon/lat bbox from z19 tiles and shelf-pack the crops with 2 px gutters.

    A crop that needs any missing tile gets no entry, so no roof borrows another area's imagery.
    """
    crops = {}
    for key, bbox in bboxes.items():
        (x0, y0, x1, y1), bounds = coverage(bbox, 27)  # z19 pixel grid == z27 tile grid
        needed = [(x, y) for y in range(y0 >> 8, (y1 >> 8) + 1) for x in range(x0 >> 8, (x1 >> 8) + 1)]
        if all(job in tiles for job in needed):
            crop = Image.new('RGB', (x1 - x0 + 1, y1 - y0 + 1))
            for x, y in needed:
                crop.paste(tiles[x, y], (x * 256 - x0, y * 256 - y0))
            crops[key] = crop, bounds
    x = y = shelf = 0
    entries = {}
    for key in sorted(crops, key=lambda k: -crops[k][0].height):
        w, h = crops[key][0].size
        if w > width:
            raise ValueError('Roof crop wider than the atlas')
        if x + w > width:
            x, y, shelf = 0, y + shelf + 2, 0
        entries[key] = {'atlas_px': [x, y, x + w, y + h], 'mercator_bbox': crops[key][1]}
        x, shelf = x + w + 2, max(shelf, h)
    if y + shelf > width:
        raise ValueError('Roof atlas exceeds 4096 px; reduce margin or split the atlas')
    atlas = Image.new('RGB', (width, max(y + shelf, 1)), (128, 128, 128))
    for key, entry in entries.items():
        atlas.paste(crops[key][0], tuple(entry['atlas_px'][:2]))
    return atlas, entries


def roofs(key, output):
    site = json.loads(Path('var/rendering/site/scene.json').read_text())
    bboxes = {}
    for building in site['buildings']:
        points = [p for polygon in building['source_geometry']['coordinates'] for ring in polygon for p in ring]
        lons, lats = [p[0] for p in points], [p[1] for p in points]
        # ponytail: spherical 1 m margin; exact geodesic offsets are irrelevant at ~0.25 m/px.
        dlat = 1 / 111320
        dlon = dlat / math.cos(math.radians(lats[0]))
        bboxes[building['id']] = [min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat]
    zoom = 19
    jobs = set()
    for bbox in bboxes.values():
        (x0, y0, x1, y1), _ = coverage(bbox, zoom)
        jobs.update((x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1))
    if len(jobs) > 4000:
        raise ValueError('Roof request exceeds the bounded tile budget')
    cache = output / 'tiles' / str(zoom)
    cache.mkdir(parents=True, exist_ok=True)

    def attempt(job):
        try:
            return fetch(key, zoom, cache, job), None
        except Exception as error:  # messages carry only z/y/x and the error type, never the key
            return (*job, None), str(error)

    tiles, records, failed = {}, [], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (x, y, payload), error in pool.map(attempt, sorted(jobs)):
            image = None if error else validate(payload)
            if image is not None and all(low == high for low, high in image.getextrema()):
                error = f'Satellite tile {zoom}/{y}/{x} is blank'
            if error:
                failed.append({'z': zoom, 'x': x, 'y': y, 'error': error})
                continue
            tiles[x, y] = image
            records.append({'z': zoom, 'x': x, 'y': y, 'sha256': hashlib.sha256(payload).hexdigest()})
    atlas, entries = roof_atlas(bboxes, tiles)
    path = output / 'atlas.jpg'
    atlas.save(path, quality=92, subsampling=0)
    metadata = {'source': 'VWorld Satellite WMTS', 'attribution': '공간정보 오픈플랫폼(브이월드) / 국토교통부',
                'documentation': 'https://www.vworld.kr/dev/v4dv_wmtsguide_s001.do',
                'source_url_template': 'https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{z}/{y}/{x}.jpeg',
                'zoom': zoom, 'crs': 'EPSG:3857', 'width': atlas.width, 'height': atlas.height,
                'margin_m': 1, 'gutter_px': 2, 'building_count': len(bboxes), 'entry_count': len(entries),
                'missing_entry_ids': sorted(set(bboxes) - set(entries)),
                'tile_count': len(records), 'failed_tile_count': len(failed),
                'acquired_at': datetime.now(timezone.utc).isoformat(), 'capture_date': None,
                'notice': 'Actual provider imagery; acquisition date is not photography date. Provider terms apply; local preview cache, not an open-data redistribution license.',
                'processing': 'Per-building z19 crops (footprint bbox + 1 m, snapped to the z19 pixel grid), original pixels, shelf-packed; atlas_px edges are [x0, y0, x1, y1) from the top-left. No synthetic fill.',
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'tiles': sorted(records, key=lambda r: (r['y'], r['x'])), 'failed_tiles': failed, 'entries': entries}
    (output / 'atlas.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'tiles': len(records), 'failed_tiles': len(failed), 'entries': len(entries),
                      'buildings': len(bboxes), 'size': atlas.size, 'sha256': metadata['sha256']}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--roofs', action='store_true', help='z19 roof atlas for the Sinchang footprints')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_test:
        assert tile(0, 0, 1) == (1, 1)
        r, b = coverage([126.145, 33.31, 126.40, 33.435], 15)
        assert r == (27865, 13151, 27889, 13165)
        assert b[0] < 6378137 * math.radians(126.145) < b[2]
        try:
            validate(b'<Error>invalid key</Error>')
        except ValueError:
            pass
        else:
            raise AssertionError('Non-image response accepted')
        # Synthetic z19 tiles: (x0, y0) red, (x0+1, y0) blue, (x0, y0+1) failed.
        x0, y0 = tile(126.17, 33.34, 19)
        tiles = {(x0, y0): Image.new('RGB', (256, 256), (255, 0, 0)), (x0 + 1, y0): Image.new('RGB', (256, 256), (0, 0, 255))}
        lon = lambda x: x / 2 ** 27 * 360 - 180  # z19 pixel column -> longitude
        lat = lambda y: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** 27))))
        px0, py0 = x0 * 256, y0 * 256
        bboxes = {'inside': [lon(px0 + 10.5), lat(py0 + 40.5), lon(px0 + 60.5), lat(py0 + 10.5)],
                  'across': [lon(px0 + 200.5), lat(py0 + 90.5), lon(px0 + 300.5), lat(py0 + 20.5)],
                  'failed': [lon(px0 + 20.5), lat(py0 + 300.5), lon(px0 + 40.5), lat(py0 + 200.5)]}
        atlas, entries = roof_atlas(bboxes, tiles)
        assert set(entries) == {'inside', 'across'} and atlas.width == 4096 and atlas.height <= 4096
        (a, b) = (entries[k]['atlas_px'] for k in sorted(entries))
        assert a[2] + 2 <= b[0] or b[2] + 2 <= a[0] or a[3] + 2 <= b[1] or b[3] + 2 <= a[1], 'cells need a 2 px gutter'
        x, y, x1, y1 = entries['across']['atlas_px']
        assert (x1 - x, y1 - y) == (101, 71)  # pixels 200..300 by 20..90 inclusive
        assert atlas.getpixel((x + 55, y + 5)) == (255, 0, 0) and atlas.getpixel((x + 56, y + 5)) == (0, 0, 255)
        (w, s, e, n) = entries['across']['mercator_bbox']
        assert math.isclose(e - w, 101 * 2 * HALF_WORLD / 2 ** 27) and math.isclose(n - s, 71 * 2 * HALF_WORLD / 2 ** 27)
        assert w < math.radians(bboxes['across'][0]) * 6378137 < math.radians(bboxes['across'][2]) * 6378137 < e
        print('PASS tile order, geographic coverage, invalid-image rejection, roof crop/packing and failed-tile exclusion')
        return
    key = read_key(Path('.env'))
    if args.roofs:
        roofs(key, args.output or Path('var/rendering/roofs'))
        return
    args.output = args.output or Path('var/rendering/imagery')
    bbox = json.loads(Path('var/rendering/grid/manifest.json').read_text())['terrain']['bbox_lon_lat']
    zoom = 15
    (x0, y0, x1, y1), bounds = coverage(bbox, zoom)
    jobs = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if len(jobs) > 512:
        raise ValueError('Preview request exceeds the bounded tile budget')
    cache = args.output / 'tiles' / str(zoom)
    cache.mkdir(parents=True, exist_ok=True)

    mosaic = Image.new('RGB', ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256))
    records = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for x, y, payload in pool.map(lambda job: fetch(key, zoom, cache, job), jobs):
            mosaic.paste(validate(payload), ((x - x0) * 256, (y - y0) * 256))
            records.append({'z': zoom, 'x': x, 'y': y, 'sha256': hashlib.sha256(payload).hexdigest()})
    path = args.output / 'texture.jpg'
    mosaic.save(path, quality=94, subsampling=0)
    w, s, e, n = bounds
    lonlat = [math.degrees(w / 6378137), math.degrees(math.atan(math.sinh(s / 6378137))),
              math.degrees(e / 6378137), math.degrees(math.atan(math.sinh(n / 6378137)))]
    metadata = {'source': 'VWorld Satellite WMTS', 'attribution': '공간정보 오픈플랫폼(브이월드) / 국토교통부',
                'documentation': 'https://www.vworld.kr/dev/v4dv_wmtsguide_s001.do',
                'source_url_template': 'https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{z}/{y}/{x}.jpeg',
                'crs': 'EPSG:3857', 'bounds': bounds, 'bbox_lon_lat': lonlat, 'requested_bbox_lon_lat': bbox,
                'width': mosaic.width, 'height': mosaic.height, 'zoom': zoom, 'tile_count': len(records),
                'acquired_at': datetime.now(timezone.utc).isoformat(), 'capture_date': None,
                'notice': 'Actual provider imagery; acquisition date is not photography date. Provider terms apply; local preview cache, not an open-data redistribution license.',
                'processing': 'North-up WMTS mosaic; original tile pixels retained, JPEG encoded at quality94. No synthetic fill.',
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'tiles': records}
    (args.output / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'tiles': len(records), 'size': mosaic.size, 'sha256': metadata['sha256']}))


if __name__ == '__main__':
    main()
