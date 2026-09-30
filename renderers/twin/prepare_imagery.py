#!/usr/bin/env python3
"""Cache a bounded VWorld Satellite mosaic with its Web Mercator georeference.

python3 renderers/twin/prepare_imagery.py [--self-test]
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('var/rendering/imagery'))
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
        print('PASS tile order, geographic coverage and invalid-image rejection')
        return
    key = read_key(Path('.env'))
    bbox = json.loads(Path('var/rendering/grid/manifest.json').read_text())['terrain']['bbox_lon_lat']
    zoom = 15
    (x0, y0, x1, y1), bounds = coverage(bbox, zoom)
    jobs = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if len(jobs) > 512:
        raise ValueError('Preview request exceeds the bounded tile budget')
    cache = args.output / 'tiles' / str(zoom)
    cache.mkdir(parents=True, exist_ok=True)

    def fetch(job):
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

    mosaic = Image.new('RGB', ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256))
    records = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for x, y, payload in pool.map(fetch, jobs):
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
