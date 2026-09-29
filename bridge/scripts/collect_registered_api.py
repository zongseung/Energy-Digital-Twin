"""Checkpointed ASOS/BuildingHUB preparation; product services remain Rust."""
import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import collect_power_data as files

GEO = Path('/mnt/iscsi/energy-digital-twin/geography/jeju')
ROOT = GEO / 'registered_api'
ENDPOINTS = {
    'asos': 'https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList',
    'buildings': 'https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo',
}
STATIONS = {184: '제주', 185: '고산', 188: '성산', 189: '서귀포'}


def read_key(path):
    for line in path.read_text().splitlines():
        name, sep, value = line.strip().partition('=')
        if sep and name.strip() == 'api_key':
            key = urllib.parse.unquote(value.strip().strip('"\''))
            if key and not any(c.isspace() for c in key):
                return key
    raise ValueError('api_key_missing')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('redirect_blocked')


def reject_key_echo(raw, key):
    if key and any(value.encode() in raw for value in (key, urllib.parse.quote(key, safe=''), urllib.parse.quote_plus(key))):
        raise ValueError('credential_echo')


def fetch(kind, params, key):
    url = ENDPOINTS[kind] + '?' + urllib.parse.urlencode(dict(params, serviceKey=key))
    # ponytail: socket timeout, not a whole-response deadline; isolate downloads if slow servers stall batches.
    for attempt in range(3):
        try:
            with urllib.request.build_opener(NoRedirect).open(url, timeout=30) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('response_size_limit')
            reject_key_echo(raw, key)
            return raw
        except urllib.error.HTTPError as error:
            if attempt == 2 or error.code not in (429, 500, 502, 503, 504):
                raise ValueError('http_' + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise ValueError('request_failed') from None
        time.sleep(attempt + 1)


def parse_page(raw, page, size):
    try:
        value = json.loads(raw)['response']
    except (ValueError, KeyError, TypeError):
        try:
            code = ET.fromstring(raw).findtext('.//returnReasonCode') or 'unknown'
        except ET.ParseError:
            code = 'unknown'
        raise ValueError('api_error_' + (code if re.fullmatch(r'\d{1,3}', code) else 'unknown')) from None
    code = str(value.get('header', {}).get('resultCode', 'unknown'))
    if code != '00':
        raise ValueError('api_error_' + (code if re.fullmatch(r'\d{1,3}', code) else 'unknown'))
    body = value['body']
    total = int(body['totalCount'])
    items = body.get('items') or {}
    rows = items.get('item', []) if isinstance(items, dict) else []
    if isinstance(rows, dict):
        rows = [rows]
    if (total < 0 or int(body['pageNo']) != page or int(body['numOfRows']) != size
            or not isinstance(rows, list) or len(rows) != min(size, max(0, total - (page - 1) * size))
            or (page > 1 and (page - 1) * size >= total)):
        raise ValueError('page_incomplete_or_changed')
    return rows, total


def collect_series(kind, label, params, key, *, check=False):
    source = {'endpoint': ENDPOINTS[kind], 'params': params}
    name = label + '.jsonl'
    old = files.reused(name, source, root=ROOT)
    if check and not old:
        raise ValueError('dataset_missing')
    path = ROOT / (name + '.part')
    seen, hours, counts, digest = set(), set(), Counter(), hashlib.sha256()
    qc = {'wsQcflg': Counter(), 'wdQcflg': Counter()}
    page, total = 1, None
    with path.open('wb') if not old and not check else contextlib.nullcontext() as output:
        while True:
            query = dict(params, pageNo=page)
            page_name = f'pages/{label}/{page:05d}.json'
            page_source = {'endpoint': ENDPOINTS[kind], 'params': query}
            cached = files.reused(page_name, page_source, root=ROOT)
            if not cached:
                if check:
                    raise ValueError('checkpoint_missing')
                raw = fetch(kind, query, key)
                rows, current_total = parse_page(raw, page, params['numOfRows'])
                # ponytail: three workers with a short pause; reduce concurrency if the provider throttles.
                time.sleep(0.2)
            else:
                raw = (ROOT / page_name).read_bytes()
                reject_key_echo(raw, key)
                if not old and not check:
                    fresh = fetch(kind, query, key)
                    if hashlib.sha256(fresh).hexdigest() != cached['sha256']:
                        raise ValueError('checkpoint_source_changed')
                rows, current_total = parse_page(raw, page, params['numOfRows'])
            if total is not None and total != current_total:
                raise ValueError('total_changed')
            total = current_total
            for row in rows:
                if kind == 'buildings':
                    if str(row['sigunguCd']) != params['sigunguCd'] or str(row['bjdongCd']) != params['bjdongCd']:
                        raise ValueError('foreign_building')
                    identity = str(row['mgmBldrgstPk'])
                    if not re.fullmatch(r'[0-9]+', identity) or int(identity) <= 0:
                        raise ValueError('building_registry_pk_invalid')
                    height = row.get('heit')
                    positive = height not in (None, '') and math.isfinite(float(height)) and float(height) > 0
                    counts['positive_height_count' if positive else 'missing_height_count'] += 1
                else:
                    stamp = datetime.strptime(row['tm'], '%Y-%m-%d %H:%M')
                    if (str(row['stnId']) != str(params['stnIds']) or row['stnNm'] != STATIONS[params['stnIds']]
                            or stamp.minute != 0 or not params['startDt'] <= stamp.strftime('%Y%m%d') <= params['endDt']):
                        raise ValueError('weather_identity_or_time_invalid')
                    identity = row['tm']
                    hours.add(stamp)
                    for field in ('ws', 'wd'):
                        if row[field] in (None, ''):
                            counts['missing_' + field + '_count'] += 1
                        elif not math.isfinite(float(row[field])):
                            raise ValueError('weather_nonfinite')
                        else:
                            counts['missing_' + field + '_count'] += 0
                        qc[field + 'Qcflg'][str(row[field + 'Qcflg'])] += 1
                if not identity.strip() or identity in seen:
                    raise ValueError('duplicate_or_empty_identity')
                seen.add(identity)
                encoded = (json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode()
                digest.update(encoded)
                if output:
                    output.write(encoded)
            if not cached:
                files.publish(page_name, raw, {'source': page_source}, root=ROOT)
            if page * params['numOfRows'] >= total:
                break
            page += 1
            if page > 1000:
                raise ValueError('page_limit')
        if output:
            output.flush()
            os.fsync(output.fileno())
    if len(seen) != total:
        raise ValueError('row_count_mismatch')
    if old:
        if (old['row_count'] != total or old['sha256'] != digest.hexdigest()
                or any(old.get(field, 0) != count for field, count in counts.items())
                or (kind == 'asos' and old.get('qc_counts') != qc)):
            raise ValueError('checkpoint_output_mismatch')
        return old
    metadata = {'source': source, 'format': 'JSONL', 'row_count': total, 'page_count': page, **counts}
    if kind == 'asos':
        days = (datetime.strptime(params['endDt'], '%Y%m%d') - datetime.strptime(params['startDt'], '%Y%m%d')).days + 1
        metadata.update(timezone='Asia/Seoul', expected_hour_count=days * 24,
                        missing_hour_count=days * 24 - len(hours), qc_counts=qc,
                        first_time=str(min(hours)) if hours else None, last_time=str(max(hours)) if hours else None,
                        units={'ws': 'm/s', 'wd': 'provider wind direction encoding'},
                        quality_flags=['ground_station_not_hub_height', 'keep_source_qc_and_blank_values'])
    else:
        metadata.update(height_unit='m', quality_flags=['registered_not_surveyed_height', 'zero_or_blank_is_unavailable',
                        'no_verified_geometry_join', 'pagination_not_atomic_provider_snapshot'])
    return files.publish(name, None, metadata, root=ROOT)


def building_scope():
    codes, sources = set(), []
    for path in (GEO / 'buildings.geojsonl', GEO / 'supplements/northern_islands/buildings.geojsonl'):
        meta = json.loads(Path(str(path) + '.metadata.json').read_text())
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for line in stream:
                digest.update(line)
                number = str(json.loads(line)['properties'].get('bd_mgt_sn', ''))
                if len(number) != 25 or not number.isdecimal():
                    raise ValueError('building_management_number_invalid')
                if number[:5] in ('50110', '50130'):
                    codes.add(number[:10])
        if meta.get('complete') is not True or digest.hexdigest() != meta['sha256']:
            raise ValueError('scope_source_hash_mismatch')
        sources.append({'file': str(path), 'sha256': meta['sha256']})
    if not codes:
        raise ValueError('scope_empty')
    return sorted(codes), sources


def write_manifest(value):
    files.publish('manifest.json', json.dumps(value, ensure_ascii=False, indent=2).encode(),
                  {'source': 'registered_jeju_api_collector'}, root=ROOT)


def jobs_for(year, codes):
    jobs = [('asos', f'asos_{year}_{station}', dict(numOfRows=999, dataType='JSON', dataCd='ASOS', dateCd='HR',
            startDt=f'{year}0101', startHh='00', endDt=f'{year}1231', endHh='23', stnIds=station)) for station in STATIONS]
    return jobs + [('buildings', 'buildings_' + code, dict(numOfRows=100, _type='json', sigunguCd=code[:5], bjdongCd=code[5:])) for code in codes]


async def main(args):
    if args.check:
        if not files.reused('manifest.json', 'registered_jeju_api_collector', root=ROOT):
            raise ValueError('manifest_missing')
        manifest = json.loads((ROOT / 'manifest.json').read_text())
        if manifest['complete'] is not True:
            raise ValueError('collection_incomplete')
        scope = manifest['scope']
        codes, year = scope['building_legal_codes'], scope['asos_year']
        if (not isinstance(year, int) or not 1900 <= year < date.today().year or not codes
                or codes != sorted(set(codes)) or any(not re.fullmatch(r'(50110|50130)[0-9]{5}', c) for c in codes)
                or scope['stations'] != {str(k): v for k, v in STATIONS.items()}):
            raise ValueError('manifest_scope_invalid')
        expected = {label: (kind, params) for kind, label, params in jobs_for(year, codes)}
        datasets = manifest['datasets']
        if (manifest['expected_datasets'] != len(expected) or len(datasets) != len(expected)
                or {Path(item['file']).stem for item in datasets} != set(expected)):
            raise ValueError('manifest_jobs_missing_or_duplicated')
        for item in manifest['datasets']:
            label = Path(item['file']).stem
            kind, params = expected[label]
            if item['file'] != label + '.jsonl' or item['source'] != {'endpoint': ENDPOINTS[kind], 'params': params}:
                raise ValueError('manifest_query_invalid')
            actual = collect_series(kind, label, params, '', check=True)
            if actual != item:
                raise ValueError('manifest_metadata_mismatch')
        print(f'verified_datasets={len(manifest["datasets"])}', flush=True)
        return 0
    key = read_key(Path(__file__).resolve().parents[2] / '.env')
    codes, sources = building_scope()
    jobs = jobs_for(args.year, codes)
    manifest = {'complete': False, 'started_at': datetime.now(timezone.utc).isoformat(), 'datasets': [], 'errors': [],
                'scope': {'asos_year': args.year, 'stations': STATIONS, 'building_legal_codes': codes, 'building_scope_sources': sources},
                'expected_datasets': len(jobs), 'electrical_twin_ready': False,
                'notes': ['Building scope follows legal codes observed in collected footprints, not certified all-Jeju coverage',
                          'Registry PK and GIS feature ID are different; heights are not joined to footprints',
                          'ASOS ground observations are not forecast or turbine hub-height wind']}
    write_manifest(manifest)
    slots = asyncio.Semaphore(3)

    async def run(kind, label, params):
        async with slots:
            try:
                item = await asyncio.to_thread(collect_series, kind, label, params, key)
                manifest['datasets'].append(item)
                print(f'{label}: rows={item["row_count"]}', flush=True)
            except Exception as error:
                reason = str(error) if isinstance(error, ValueError) and re.fullmatch(r'[a-z_0-9]+', str(error)) else type(error).__name__
                manifest['errors'].append(label + ':' + reason)
                print(label + ':' + reason, flush=True)
            write_manifest(manifest)

    await asyncio.gather(*(run(*job) for job in jobs))
    manifest['complete'] = not manifest['errors'] and len(manifest['datasets']) == len(jobs)
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    write_manifest(manifest)
    print(f'collection_complete={manifest["complete"]}; datasets={len(manifest["datasets"])}; errors={len(manifest["errors"])}', flush=True)
    return 0 if manifest['complete'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', type=int, default=2025)
    parser.add_argument('--snapshot', default=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'\d{8}', args.snapshot) or not 1900 <= args.year < date.today().year:
        parser.error('use a YYYYMMDD snapshot and a completed observation year')
    ROOT = ROOT / args.snapshot
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        with (ROOT / '.collector.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise SystemExit(asyncio.run(main(args)))
    except Exception as error:
        # Never expose key-bearing URLs, HTTP bodies or library exception text.
        raise SystemExit('collection_failed:' + type(error).__name__) from None
