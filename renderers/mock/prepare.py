"""Prepare a shared, deliberately approximate GIS scene for the two mock renderers."""
import argparse
import hashlib
import json
import math
from pathlib import Path

ORIGIN = (126.5, 33.35)


def project(lon, lat, raw_height=0):
    if not all(math.isfinite(v) for v in (lon, lat, raw_height)):
        raise ValueError('nonfinite coordinate')
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValueError('invalid WGS84 coordinate')
    return [round((lon - ORIGIN[0]) * 111.195 * math.cos(math.radians(ORIGIN[1])), 5),
            round(max(0, raw_height) * 0.003, 5), round(-(lat - ORIGIN[1]) * 111.195, 5)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('.worktrees/data/var/data/geography/source-03e02ef'))
    parser.add_argument('--output', type=Path, default=Path('var/rendering/mock/scene.json'))
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        assert project(*ORIGIN) == [0, 0, 0]
        assert project(127, 34, 100)[0] > 0 and project(127, 34, 100)[2] < 0
        assert project(126.5, 33.35, 100)[1] == 0.3
        for bad in (float('nan'), 181):
            try:
                project(bad, 33)
            except ValueError:
                continue
            raise AssertionError('invalid position accepted')
        print('PASS projection, axis, mock height and invalid-coordinate checks')
        return
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import geometry_mask

    manifest_bytes = (args.source / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if not manifest['complete']:
        raise ValueError('source collection incomplete')
    used = ['dem_jeju.tif', 'vworld_admin_boundary.geojsonl', 'coastline.geojsonl',
            'power_line.geojsonl', 'substation.geojsonl', 'power_plant.geojsonl', 'pv_facility.geojsonl']
    for name in used:
        metadata = next(item for item in manifest['datasets'] if item['file'] == name)
        with (args.source / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != metadata['sha256']:
                raise ValueError(f'source hash mismatch: {name}')

    def features(name):
        with (args.source / (name + '.geojsonl')).open() as stream:
            for line in stream:
                yield json.loads(line)

    with rasterio.open(args.source / 'dem_jeju.tif') as dem:
        width, height = 241, 169
        grid = dem.read(1, out_shape=(height, width), resampling=Resampling.bilinear, masked=True)
        transform = dem.transform * dem.transform.scale(dem.width / width, dem.height / height)
        land = geometry_mask([f['geometry'] for f in features('vworld_admin_boundary')],
                             (height, width), transform, invert=True)
        valid = land & ~np.ma.getmaskarray(grid)
        heights = grid.filled(0)

        def position(lon, lat):
            col, row = (~transform) * (lon, lat)
            h = float(heights[int(row), int(col)]) if 0 <= row < height and 0 <= col < width else 0
            return project(lon, lat, h if math.isfinite(h) else 0)

        positions, indices = [], []
        for row in range(height):
            for col in range(width):
                lon, lat = transform * (col + .5, row + .5)
                positions.extend(project(lon, lat, float(heights[row, col])))
        for row in range(height - 1):
            for col in range(width - 1):
                i = row * width + col
                if valid[row:row + 2, col:col + 2].all():
                    indices.extend([i, i + width, i + 1, i + 1, i + width, i + width + 1])
        coast = []
        for feature in features('coastline'):
            paths = feature['geometry']['coordinates']
            for path in paths:
                # ponytail: overview sampling; use spatial LOD when close-up geography is required.
                stride = max(1, math.ceil(len(path) / 250))
                sampled = path[::stride]
                if sampled[-1] != path[-1]:
                    sampled.append(path[-1])
                coast.append([project(*p[:2]) for p in sampled])
        lines = []
        for feature in features('power_line'):
            lines.append({'id': str(feature['id']), 'name': feature['properties'].get('name'),
                          'points': [position(*p[:2]) for p in feature['geometry']['coordinates']]})
        facilities = []
        for kind in ('substation', 'power_plant', 'pv_facility'):
            for feature in features(kind):
                p = feature['properties']
                lon, lat = feature['geometry']['coordinates'][:2]
                facilities.append({'id': str(feature['id']), 'name': p.get('name') or str(feature['id']),
                                   'kind': kind, 'position': position(lon, lat),
                                   'coordinates': [lon, lat], 'source_properties': p})
    scene = {
        'schema_version': 1, 'data_kind': 'mock_geometry',
        'projection': {'kind': 'local_equirectangular', 'origin': ORIGIN, 'units': 'km', 'axes': 'x east, y up, z south'},
        'terrain': {'positions': positions, 'indices': indices, 'elevation_exaggeration': 3,
                    'height_note': 'DEM 원시값 기반 높이 연출 · 수직 기준/단위 미검증 · 실측 높이 아님'},
        'coast': coast, 'lines': lines, 'facilities': facilities,
        'metadata': {'source_commit': '03e02ef', 'source_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                     'source_generated_at': manifest['generated_at'], 'preview_bbox': [126, 33, 127, 33.7],
                     'land_mask': 'vworld_admin_boundary.geojsonl',
                     'highland_samples_outside_mask': int(((heights > 300) & ~land).sum()),
                     'notes': ['실제 GIS 위치 / 합성 수급 / 개략 시설 기호', '북쪽 부속도서는 별도 원천에 있으며 이번 카메라는 제주 본섬',
                               '시설별 실시간 발전량과 선로별 전력 흐름은 제공하지 않음',
                               '높이는 시각화용이며 DEM의 음수/결측을 이 표시물에서만 해수면으로 표현; 원본 유지'],
                     'sources': [{key: item.get(key) for key in ('file', 'source', 'license', 'sha256', 'quality_flags')}
                                 for item in manifest['datasets'] if item['file'] in used]}}
    assert indices and len(positions) % 3 == 0 and max(indices) < len(positions) // 3
    assert len({f['id'] for f in facilities}) == len(facilities)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scene, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
    print(f'PASS {args.output}: {len(indices)//3} terrain triangles, {len(facilities)} facility symbols, {len(lines)} lines')


if __name__ == '__main__':
    main()
