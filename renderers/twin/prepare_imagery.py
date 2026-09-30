#!/usr/bin/env python3
"""Cache a bounded VWorld Satellite mosaic with its Web Mercator georeference.

python3 renderers/twin/prepare_imagery.py [--self-test]
python3 renderers/twin/prepare_imagery.py --roofs   # z19 roof atlas for the Sinchang footprints
python3 renderers/twin/prepare_imagery.py --terrain-detail   # z17 site AOI mosaic -> var/rendering/imagery-detail
python3 renderers/twin/prepare_imagery.py --harbour-detail   # z17 harbour mosaics -> var/rendering/imagery-harbours/<name> + index.json
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
from tempfile import TemporaryDirectory
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


def fetch_all(key, zoom, cache, jobs, workers=6):
    """Fetch tiles in parallel; failed or blank tiles are recorded, never filled."""
    def attempt(job):
        try:
            return fetch(key, zoom, cache, job), None
        except Exception as error:  # messages carry only z/y/x and the error type, never the key
            return (*job, None), str(error)

    tiles, records, failed = {}, [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for (x, y, payload), error in pool.map(attempt, sorted(jobs)):
            image = None if error else validate(payload)
            if image is not None and all(low == high for low, high in image.getextrema()):
                error = f'Satellite tile {zoom}/{y}/{x} is blank'
            if error:
                failed.append({'z': zoom, 'x': x, 'y': y, 'error': error})
                continue
            tiles[x, y] = image
            records.append({'z': zoom, 'x': x, 'y': y, 'sha256': hashlib.sha256(payload).hexdigest()})
    return tiles, records, failed


def mosaic(bbox, zoom, tiles):
    """North-up whole-tile mosaic; a missing tile stays neutral grey (callers record it)."""
    # ponytail: grey can bleed a few texels into neighbouring triangles at low mip levels; fill from z15 if failures persist.
    (x0, y0, x1, y1), bounds = coverage(bbox, zoom)
    image = Image.new('RGB', ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256), (128, 128, 128))
    for (x, y), tile_image in tiles.items():
        image.paste(tile_image, ((x - x0) * 256, (y - y0) * 256))
    return image, bounds


def write_mosaic(output, bbox, zoom, tiles, records, failed):
    """texture.jpg + manifest.json (the imagery-detail format) for one whole-tile mosaic; returns the metadata."""
    image, bounds = mosaic(bbox, zoom, tiles)
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'texture.jpg'
    image.save(path, quality=94, subsampling=0)
    w, s, e, n = bounds
    lonlat = [math.degrees(w / 6378137), math.degrees(math.atan(math.sinh(s / 6378137))),
              math.degrees(e / 6378137), math.degrees(math.atan(math.sinh(n / 6378137)))]
    metadata = {'source': 'VWorld Satellite WMTS', 'attribution': '공간정보 오픈플랫폼(브이월드) / 국토교통부',
                'documentation': 'https://www.vworld.kr/dev/v4dv_wmtsguide_s001.do',
                'source_url_template': 'https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{z}/{y}/{x}.jpeg',
                'crs': 'EPSG:3857', 'bounds': bounds, 'bbox_lon_lat': lonlat, 'requested_bbox_lon_lat': bbox,
                'width': image.width, 'height': image.height, 'zoom': zoom, 'tile_count': len(records),
                'failed_tile_count': len(failed),
                'acquired_at': datetime.now(timezone.utc).isoformat(), 'capture_date': None,
                'notice': 'Actual provider imagery; acquisition date is not photography date. Provider terms apply; local preview cache, not an open-data redistribution license.',
                'processing': 'North-up WMTS mosaic; original tile pixels retained, JPEG encoded at quality94. No synthetic fill; failed tiles stay neutral grey, are listed in failed_tiles and are never mapped.',
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'tiles': records, 'failed_tiles': failed}
    (output / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    return metadata


# Harbour clusters of the KHOA 2026 coastline in the scene (exa-results/harbour-sports-data-2026-09-30.md §3.2): centre lon/lat, name.
# Keys never contain '-': merged mosaic names join them with it.
HARBOURS = {'chagwido': (126.1506, 33.3111, '차귀도 선착장'), 'gosan': (126.1650, 33.3104, '고산항(자구내)'),
            'yongsu': (126.1653, 33.3230, '용수리포구'), 'sinchang_wind': (126.1648, 33.3374, '신창 풍력단지 옆 소형 포구(명칭 미확인)'),
            'sinchang': (126.1791, 33.3480, '신창항(신창리포구)'), 'dumo': (126.1806, 33.3568, '두모리포구'),
            'geumdeung': (126.1958, 33.3647, '금등 쪽 포구(명칭 미확인)'), 'panpo': (126.2003, 33.3662, '판포포구'),
            'wollyeong': (126.2156, 33.3795, '월령포구'), 'geumneung': (126.2276, 33.3908, '금능포구'),
            'biyang': (126.2300, 33.4052, '비양포구'), 'hyeopjae': (126.2433, 33.3986, '협재포구'),
            'ongpo': (126.2502, 33.4042, '옹포포구(옹포항)'), 'hallim': (126.2578, 33.4174, '한림항'),
            'suwon': (126.2633, 33.4253, '대수·평수포구(수원리)'), 'north_edge': (126.2725, 33.4351, 'bbox 북쪽 가장자리 포구(명칭 미확인)')}
HARBOUR_CATS = {11, 12, 13, 14, 16, 17}  # KHOA CAT_COA 방파제·방사제·잔교·부두·상륙계단·선가대; 방벽 (15) lines the open coast too


def harbour_of(lon, lat):
    return min(HARBOURS, key=lambda k: math.hypot((lon - HARBOURS[k][0]) * math.cos(math.radians(lat)), lat - HARBOURS[k][1]))


def harbour_boxes(features, detail_bbox, scene_bbox, buffer_m=400, zoom=17, max_px=4096):
    """{name: lon/lat box}: each harbour's lines + buffer_m, clipped to the scene; harbours wholly inside detail_bbox are
    skipped; overlapping boxes merge while the merged mosaic stays <= max_px on a side."""
    points = {}
    for f in features:
        if f['properties']['CAT_COA'] in HARBOUR_CATS:
            geom = f['geometry']
            for line in [geom['coordinates']] if geom['type'] == 'LineString' else geom['coordinates']:
                for lon, lat in line:
                    points.setdefault(harbour_of(lon, lat), []).append((lon, lat))
    w, s, e, n = detail_bbox

    def fits(box):
        x0, y0, x1, y1 = coverage(box, zoom)[0]
        return max(x1 - x0 + 1, y1 - y0 + 1) * 256 <= max_px
    boxes = []
    for name, p in points.items():
        if all(w <= lon <= e and s <= lat <= n for lon, lat in p):
            continue
        lons, lats = zip(*p)
        # ponytail: spherical metres-to-degrees like roofs(); geodesic precision is irrelevant for a 400 m context margin.
        dlat = buffer_m / 111320
        dlon = dlat / math.cos(math.radians(sum(lats) / len(lats)))
        box = [max(min(lons) - dlon, scene_bbox[0]), max(min(lats) - dlat, scene_bbox[1]),
               min(max(lons) + dlon, scene_bbox[2]), min(max(lats) + dlat, scene_bbox[3])]
        if not fits(box):
            raise ValueError(f'{name} harbour box exceeds {max_px} px')
        boxes.append(([name], box))
    merged = True
    while merged:  # ponytail: greedy pairwise merge in insertion order; fine for ~16 harbours
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i][1], boxes[j][1]
                union = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3] and fits(union):
                    boxes[i], merged = (boxes[i][0] + boxes.pop(j)[0], union), True
                    break
            if merged:
                break
    return {'-'.join(sorted(names, key=lambda k: HARBOURS[k][0])): box for names, box in boxes}


def harbour_detail(key, output, features, detail_bbox, scene_bbox):
    """z17 mosaics around the harbours outside the detail mosaic -> output/<name>/{texture.jpg,manifest.json} + index.json."""
    cache = output / 'tiles' / '17'
    cache.mkdir(parents=True, exist_ok=True)
    mosaics = []
    for name, bbox in harbour_boxes(features, detail_bbox, scene_bbox).items():
        (x0, y0, x1, y1), _ = coverage(bbox, 17)
        jobs = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
        if len(jobs) > 512:
            raise ValueError('Harbour mosaic exceeds the bounded tile budget')
        # ponytail: politeness is 2 parallel requests and the shared tile cache; add a delay if the provider asks for one.
        metadata = write_mosaic(output / name, bbox, 17, *fetch_all(key, 17, cache, jobs, workers=2))
        mosaics.append({'name': name, 'harbours': [HARBOURS[k][2] for k in name.split('-')],
                        'path': f'{name}/texture.jpg', 'manifest': f'{name}/manifest.json',
                        **{k: metadata[k] for k in ('bbox_lon_lat', 'requested_bbox_lon_lat', 'bounds', 'width', 'height',
                                                    'tile_count', 'failed_tile_count', 'sha256')}})
    index = {'source': 'VWorld Satellite WMTS', 'attribution': '공간정보 오픈플랫폼(브이월드) / 국토교통부', 'zoom': 17, 'crs': 'EPSG:3857',
             'buffer_m': 400, 'khoa_categories': sorted(HARBOUR_CATS), 'excluded_detail_bbox_lon_lat': detail_bbox,
             'selection': 'KHOA 2026 coastline (data.go.kr 15083948) harbour lines per nearest HARBOURS centre; harbours wholly inside '
                          'the Sinchang detail mosaic skipped; 400 m boxes clipped to the scene bbox and merged while overlapping and <= 4096 px.',
             'mosaics': mosaics, 'tile_count': sum(m['tile_count'] for m in mosaics),
             'failed_tile_count': sum(m['failed_tile_count'] for m in mosaics)}
    (output / 'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n')
    return index


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
    tiles, records, failed = fetch_all(key, zoom, cache, jobs)
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
    parser.add_argument('--terrain-detail', action='store_true', help='z17 terrain mosaic for the Sinchang site AOI')
    parser.add_argument('--harbour-detail', action='store_true', help='z17 mosaics around the harbours outside the Sinchang mosaic')
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
        # Synthetic z17 site mosaic from a local cache: a corrupt and a blank tile are recorded, never fetched or filled.
        aoi = [126.155, 33.325, 126.19, 33.36]
        (x0, y0, x1, y1), expected = coverage(aoi, 17)
        jobs = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
        with TemporaryDirectory() as cache:
            for x, y in jobs:
                image = Image.new('RGB', (256, 256), (x % 200, y % 200, 60))
                image.putpixel((0, 0), (255, 255, 255))
                image.save(Path(cache) / f'{x}-{y}.jpg')
            (Path(cache) / f'{x0}-{y0}.jpg').write_bytes(b'<Error>invalid key</Error>')
            Image.new('RGB', (256, 256), (9, 9, 9)).save(Path(cache) / f'{x1}-{y1}.jpg')
            tiles, records, failed = fetch_all('unused', 17, Path(cache), jobs)
        assert [(f['z'], f['x'], f['y']) for f in failed] == [(17, x0, y0), (17, x1, y1)] and len(records) == len(jobs) - 2
        image, bounds = mosaic(aoi, 17, tiles)
        assert bounds == expected and image.size == ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256) and max(image.size) <= 4096
        merc = [math.radians(aoi[0]) * 6378137, 6378137 * math.asinh(math.tan(math.radians(aoi[1]))),
                math.radians(aoi[2]) * 6378137, 6378137 * math.asinh(math.tan(math.radians(aoi[3])))]
        assert bounds[0] < merc[0] < merc[2] < bounds[2] and bounds[1] < merc[1] < merc[3] < bounds[3]
        assert image.getpixel((5, 5)) == image.getpixel(tuple(v - 5 for v in image.size)) == (128, 128, 128)
        assert image.getpixel((256 + 5, 5)) != (128, 128, 128)
        # Harbour z17 boxes: 400 m around each harbour's lines; lines inside the Sinchang mosaic, sea walls (15) and natural
        # coast are ignored; overlapping boxes merge only while the mosaic fits 4096 px; boxes are clipped to the scene.
        line = lambda cat, *points: {'properties': {'CAT_COA': cat}, 'geometry': {'type': 'LineString', 'coordinates': [list(p) for p in points]}}
        scene_bbox = [126.14486111111111, 33.30986111111111, 126.40013888888889, 33.43513888888889]
        feats = [line(11, (126.2560, 33.4170), (126.2580, 33.4180)), line(14, (126.2630, 33.4250), (126.2640, 33.4255)),  # hallim + suwon
                 line(11, (126.2003, 33.3662), (126.2010, 33.3665)),  # panpo, alone
                 line(14, (126.1790, 33.3480), (126.1800, 33.3490)),  # sinchang: inside the detail mosaic
                 line(15, (126.3000, 33.4300), (126.3100, 33.4310)), line(54, (126.3500, 33.4300), (126.3600, 33.4310)),
                 line(16, (126.2720, 33.4345), (126.2730, 33.4350))]  # north_edge: box clipped at the scene's north edge
        boxes = harbour_boxes(feats, aoi, scene_bbox)
        assert set(boxes) == {'hallim-suwon', 'panpo', 'north_edge'}, boxes
        w, s, e, n = boxes['panpo']
        assert math.isclose(n - 33.3665, 400 / 111320) and math.isclose((126.2003 - w) * 111320 * math.cos(math.radians(33.36635)), 400, rel_tol=1e-3)
        w, s, e, n = boxes['hallim-suwon']
        assert boxes['north_edge'][3] == scene_bbox[3] and w < 126.2560 and 126.2640 < e and s < 33.4170 and 33.4255 < n
        chain = [line(11, (126.2433, 33.3986), (126.2433, 33.3990)), line(11, (126.2502, 33.4042), (126.2502, 33.4046)),
                 line(11, (126.2578, 33.4174), (126.2578, 33.4178)), line(11, (126.2633, 33.4253), (126.2633, 33.4257))]
        chained = harbour_boxes(chain, aoi, scene_bbox)
        assert len(chained) >= 2 and all(max(x1 - x0 + 1, y1 - y0 + 1) * 256 <= 4096 for (x0, y0, x1, y1), _ in (coverage(b, 17) for b in chained.values()))
        # Offline harbour mosaic from a pre-filled cache (one failed tile): imagery-detail manifest schema plus an index.
        with TemporaryDirectory() as output:
            output = Path(output)
            (x0, y0, x1, y1), _ = coverage(boxes['panpo'], 17)
            (output / 'tiles' / '17').mkdir(parents=True)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    image = Image.new('RGB', (256, 256), (x % 200, y % 200, 90))
                    image.putpixel((0, 0), (255, 255, 255))
                    image.save(output / 'tiles' / '17' / f'{x}-{y}.jpg')
            (output / 'tiles' / '17' / f'{x0}-{y0}.jpg').write_bytes(b'<Error>invalid key</Error>')
            index = harbour_detail('unused', output, feats[2:3], aoi, scene_bbox)
            manifest = json.loads((output / 'panpo' / 'manifest.json').read_text())
            assert set(manifest) == {'source', 'attribution', 'documentation', 'source_url_template', 'crs', 'bounds', 'bbox_lon_lat',
                                     'requested_bbox_lon_lat', 'width', 'height', 'zoom', 'tile_count', 'failed_tile_count', 'acquired_at',
                                     'capture_date', 'notice', 'processing', 'sha256', 'tiles', 'failed_tiles'}, set(manifest)
            assert manifest['zoom'] == 17 and manifest['requested_bbox_lon_lat'] == boxes['panpo'] and manifest['failed_tile_count'] == 1
            assert manifest['tile_count'] == (x1 - x0 + 1) * (y1 - y0 + 1) - 1 and manifest['sha256'] == hashlib.sha256((output / 'panpo' / 'texture.jpg').read_bytes()).hexdigest()
            assert json.loads((output / 'index.json').read_text()) == index and [m['name'] for m in index['mosaics']] == ['panpo']
            assert index['mosaics'][0]['harbours'] == ['판포포구'] and index['tile_count'] == manifest['tile_count'] and index['failed_tile_count'] == 1
            assert 'unused' not in (output / 'index.json').read_text() + (output / 'panpo' / 'manifest.json').read_text()
        print('PASS tile order, geographic coverage, invalid-image rejection, roof crop/packing and failed-tile exclusion, z17 site mosaic, '
              'harbour boxes (skip/merge/4096 px split/clip) and offline harbour mosaic + index')
        return
    key = read_key(Path('.env'))
    if args.roofs:
        roofs(key, args.output or Path('var/rendering/roofs'))
        return
    if args.harbour_detail:
        index = harbour_detail(key, args.output or Path('var/rendering/imagery-harbours'),
                               json.loads(Path('var/survey/harbour/khoa_coast_bbox.geojson').read_text())['features'],
                               json.loads(Path('var/rendering/imagery-detail/manifest.json').read_text())['bbox_lon_lat'],
                               json.loads(Path('var/rendering/local/manifest.json').read_text())['terrain']['bbox_lon_lat'])
        print(json.dumps({'status': 'PASS', 'tiles': index['tile_count'], 'failed_tiles': index['failed_tile_count'],
                          'mosaics': {m['name']: [m['width'], m['height'], m['tile_count'], m['failed_tile_count']] for m in index['mosaics']}}))
        return
    detail = args.terrain_detail
    args.output = args.output or Path('var/rendering/imagery-detail' if detail else 'var/rendering/imagery')
    if detail:  # ~1 m/px over the Sinchang site AOI; build_local keeps z15 where these tiles fail
        bbox, zoom = json.loads(Path('var/rendering/site/scene.json').read_text())['aoi_bbox'], 17
    else:
        bbox, zoom = json.loads(Path('var/rendering/grid/manifest.json').read_text())['terrain']['bbox_lon_lat'], 15
    (x0, y0, x1, y1), bounds = coverage(bbox, zoom)
    jobs = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if len(jobs) > 512:
        raise ValueError('Preview request exceeds the bounded tile budget')
    if detail and max(x1 - x0 + 1, y1 - y0 + 1) * 256 > 4096:
        raise ValueError('Detail mosaic exceeds 4096 px')
    cache = args.output / 'tiles' / str(zoom)
    cache.mkdir(parents=True, exist_ok=True)
    tiles, records, failed = fetch_all(key, zoom, cache, jobs)
    if failed and not detail:
        raise RuntimeError(failed[0]['error'])  # the whole-area base has no fallback imagery
    metadata = write_mosaic(args.output, bbox, zoom, tiles, records, failed)
    print(json.dumps({'status': 'PASS', 'tiles': len(records), 'failed_tiles': len(failed), 'size': [metadata['width'], metadata['height']],
                      'sha256': metadata['sha256']}))

if __name__ == '__main__':
    main()
