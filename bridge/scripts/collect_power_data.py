"""Collect Jeju power inputs; preparation only, product services remain Rust."""
import asyncio
import csv
import fcntl
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('/mnt/iscsi/energy-digital-twin/geography/jeju/power')
PORTAL = 'https://www.data.go.kr'
PAPER = 'https://www.mdpi.com/1996-1073/16/15/5699'
# ponytail: bounded public archives in memory; stream if a source exceeds 32 MiB.
LIMIT = 32 * 1024 * 1024


class SourceRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in {'www.data.go.kr', 'www.kpx.or.kr'}:
            raise ValueError('unexpected_source_redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, form=None):
    if urllib.parse.urlsplit(url).scheme != 'https' or urllib.parse.urlsplit(url).hostname not in {'www.data.go.kr', 'www.kpx.or.kr'}:
        raise ValueError('unexpected_source_url')
    data = urllib.parse.urlencode(form).encode() if form else None
    with urllib.request.build_opener(SourceRedirect).open(url, data=data, timeout=45) as response:
        if urllib.parse.urlsplit(response.url).hostname not in {'www.data.go.kr', 'www.kpx.or.kr'}:
            raise ValueError('unexpected_source_redirect')
        raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('source_size_limit')
    return raw


def csv_summary(raw, kind):
    for encoding in ('utf-8-sig', 'cp949'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('csv_encoding_unknown')
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2 or len(rows[0]) < 2 or any(len(row) != len(rows[0]) for row in rows[1:]):
        raise ValueError('csv_invalid_or_empty')
    header, records = rows[0], rows[1:]
    summary = {'encoding': encoding, 'columns': header, 'row_count': len(records)}
    if kind == 'generation':
        if header != ['거래일자', '거래시간', '연료원', '전력거래량(MWh)']:
            raise ValueError('generation_schema_changed')
        keys = set()
        fuels, days = set(), set()
        negative_count = 0
        for day, hour, fuel, value in records:
            parsed = date.fromisoformat(day)
            hour = int(hour)
            value = float(value)
            key = (day, hour, fuel)
            if key in keys or not 1 <= hour <= 24 or not fuel or not math.isfinite(value):
                raise ValueError('generation_observation_invalid')
            negative_count += value < 0
            keys.add(key)
            fuels.add(fuel)
            days.add(parsed)
        first, last = min(days), max(days)
        if len(keys) != ((last - first).days + 1) * 24 * len(fuels):
            raise ValueError('generation_incomplete_window')
        summary.update(first_date=str(first), last_date=str(last), fuels=sorted(fuels),
                       negative_energy_count=negative_count,
                       unit='MWh', hour_labels='1..24; provider interval convention unverified',
                       timezone='provider local date/hour; offset not supplied')
    elif kind == 'wind':
        if header[:3] != ['발전소명', '설비용량(MW)', '설치장소']:
            raise ValueError('wind_schema_changed')
        if any(not math.isfinite(float(row[1])) or float(row[1]) < 0 for row in records):
            raise ValueError('wind_capacity_invalid')
        summary.update(unit='MW installed capacity', source_date='2024-12-31')
    elif kind == 'curtailment':
        if header[:3] != ['일자', '제어지역', '누적 시행회차'] or len(header) != 27:
            raise ValueError('curtailment_schema_changed')
        if header[3:] != [f'{hour}시' for hour in range(1, 25)]:
            raise ValueError('curtailment_hours_changed')
        days = [date.fromisoformat(row[0]) for row in records]
        summary.update(first_date=str(min(days)), last_date=str(max(days)),
                       regions=sorted({row[1] for row in records}),
                       unit='source-specific control markers or wind MWh; keep original cells')
    return summary


def routes(text):
    records, numbers = [], set()
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if not line.startswith('|') or not cells[0].isdigit():
            continue
        if len(cells) not in (3, 6):
            raise ValueError('route_table_changed')
        for offset in range(0, len(cells), 3):
            number, name, rating = cells[offset:offset + 3]
            number, rating = int(number), float(rating)
            endpoints = name.split('—')
            if number in numbers or len(endpoints) != 2 or not math.isfinite(rating) or rating <= 0:
                raise ValueError('route_record_invalid')
            numbers.add(number)
            records.append({'number': number, 'name': name, 'from_label': endpoints[0],
                            'to_label': endpoints[1], 'rating_mva': rating})
    if not records:
        raise ValueError('route_table_empty')
    return sorted(records, key=lambda row: row['number'])


def archive_csvs(raw):
    result = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 100 or sum(info.file_size for info in entries) > LIMIT:
            raise ValueError('archive_size_limit')
        for info in entries:
            name = info.filename
            if not info.flag_bits & 0x800:
                name = name.encode('cp437').decode('cp949')
            if name.startswith('제주 ') and name.lower().endswith('.csv'):
                result.append((name, archive.read(info)))
    if len(result) != 2:
        raise ValueError('jeju_archive_members_changed')
    return result


def reused(name, source):
    path = ROOT / name
    meta = Path(str(path) + '.metadata.json')
    if not path.exists() or not meta.exists():
        return None
    value = json.loads(meta.read_text())
    if value.get('complete') is not True or value.get('source') != source or value.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError('existing_dataset_verification_failed')
    return value


def publish(name, raw, metadata):
    path = ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(path) + '.part')
    if raw is not None:
        with part.open('wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    metadata.update(schema_version=1, complete=True, file=name, bytes=part.stat().st_size,
                    sha256=hashlib.sha256(part.read_bytes()).hexdigest(),
                    collected_at=datetime.now(timezone.utc).isoformat())
    part.replace(path)
    meta = Path(str(path) + '.metadata.json')
    part = Path(str(meta) + '.part')
    with part.open('w') as stream:
        json.dump(metadata, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    part.replace(meta)
    return metadata


def collect_connections():
    name = 'kepco_connections_' + datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d') + '.jsonl'
    source = 'energy-hub-db.research.kepco_grid'
    old = reused(name, source)
    if old:
        return [old]
    part = ROOT / (name + '.part')
    sql = """BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
    SET LOCAL statement_timeout='120s';
    SELECT to_jsonb(k) FROM research.kepco_grid k
    WHERE addr_do='제주특별자치도' ORDER BY id; COMMIT;"""
    with part.open('wb') as stream:
        subprocess.run(['docker', 'exec', 'energy-hub-db', 'psql', '-U', 'energy_user',
                        '-d', 'energy_hub', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-c', sql],
                       stdout=stream, stderr=subprocess.PIPE, check=True, timeout=150)
        stream.flush()
        os.fsync(stream.fileno())
    ids, stations, transformers, feeders, dates = set(), set(), set(), set(), []
    for line in part.open():
        row = json.loads(line)
        if row['id'] in ids or row['addr_do'] != '제주특별자치도':
            raise ValueError('connection_record_invalid')
        keys = tuple(row[key] for key in ('subst_cd', 'mtr_no', 'dl_cd'))
        if any(not isinstance(key, str) or not key.strip() for key in keys):
            raise ValueError('connection_code_missing')
        ids.add(row['id'])
        stations.add(keys[0])
        transformers.add(keys[:2])
        feeders.add(keys)
        dates.append(row['crawled_at'])
    if not ids:
        raise ValueError('connection_dataset_empty')
    return [publish(name, None, {'source': source, 'format': 'JSONL', 'row_count': len(ids),
        'substation_code_count': len(stations), 'transformer_code_pair_count': len(transformers),
        'feeder_code_triplet_count': len(feeders), 'source_time_range': [min(dates), max(dates)],
        'selection': "addr_do='제주특별자치도'", 'geometry_available': False,
        'quality_flags': ['connection_clues_not_electrical_topology', 'capacity_units_unverified',
                          'address_records_not_metered_loads', 'cached_snapshot_not_live']})]


def file_url(pk, detail):
    info = json.loads(fetch(PORTAL + '/tcs/dss/selectFileDataDownload.do', {
        'publicDataPk': pk, 'publicDataDetailPk': detail,
        'fileDetailSn': '1', 'publicDataTyCode': 'PR0051'}))
    if info.get('status') is not True or not re.fullmatch(r'FILE_\d+', info.get('atchFileId', '')):
        raise ValueError('public_download_unavailable')
    return PORTAL + '/cmm/cmm/fileDownload.do?' + urllib.parse.urlencode({
        'atchFileId': info['atchFileId'], 'fileDetailSn': info['fileDetailSn'], 'insertDataPrcus': 'N'})


def collect_catalogue(pk, kind):
    source = f'{PORTAL}/data/{pk}/fileData.do'
    html = fetch(source).decode('utf-8')
    title = re.search(r'<title>(.*?)</title>', html, re.S).group(1).split(' | ')[0]
    suffix = re.search(r'_(\d{8})$', title).group(1)
    match = re.search(r'"contentUrl"\s*:\s*"([^"]+)"', html)
    detail = re.search(r'id="publicDataDetailPk"[^>]*value="([^"]+)"', html).group(1)
    url = match.group(1) if match else file_url(pk, detail)
    name = f'{kind}_{suffix}.' + ('zip' if kind == 'curtailment' else 'csv')
    old = reused(name, source)
    raw = (ROOT / name).read_bytes() if old else fetch(url)
    metadata = {'source': source, 'source_detail': detail, 'download_url': url,
                'catalogue_title': title, 'source_date': suffix, 'format': 'CSV',
                'license': 'see official catalogue', 'quality_flags': []}
    if kind != 'curtailment':
        metadata.update(csv_summary(raw, kind))
        if kind == 'generation':
            if (metadata['first_date'], metadata['last_date']) != ('2025-01-01', '2025-12-31'):
                raise ValueError('expected_2025_generation_not_available')
            metadata['quality_flags'] = ['market_settlement_energy_not_plant_scada',
                'direct_ppa_and_self_generation_excluded', 'hour_interval_convention_unverified']
            if metadata['negative_energy_count']:
                metadata['quality_flags'].append('negative_settlement_energy_preserved_meaning_unverified')
        else:
            metadata['quality_flags'] = ['anonymized_plant_names_preserved', 'address_not_coordinates',
                                          'installed_capacity_not_operating_power']
        return [old or publish(name, raw, metadata)]
    members = archive_csvs(raw)
    metadata.update(format='ZIP', member_count=len(members),
                    quality_flags=['member_dates_can_precede_catalogue_date', 'do_not_sum_archive_versions'])
    datasets = [old or publish(name, raw, metadata)]
    for index, (member, data) in enumerate(members, 1):
        name = f'curtailment_{suffix}/{index:02}.csv'
        summary = csv_summary(data, kind)
        if summary['regions'] != ['제주']:
            raise ValueError('curtailment_region_invalid')
        summary.update(source=source, source_member=member, source_archive=datasets[0]['file'],
                       format='CSV', license='see official catalogue',
                       quality_flags=['pv_curtailed_energy_not_reported', 'control_days_not_continuous_series',
                                      'provider_values_preserved_not_interpolated'])
        summary['unit'] = ('MWh in hourly wind control cells; cumulative counter preserved'
                           if '풍력' in member else 'hourly PV control markers; cumulative counter preserved')
        datasets.append(reused(name, source) or publish(name, data, summary))
    return datasets


def collect_pdf():
    source = 'https://www.kpx.or.kr/boardDownload.es?bid=0159&list_no=74566&seq=1'
    name = 'kpx_jeju_operations_2024.pdf'
    old = reused(name, source)
    if old:
        return [old]
    raw = fetch(source)
    if not raw.startswith(b'%PDF-'):
        raise ValueError('operations_pdf_invalid')
    return [publish(name, raw, {'source': source, 'source_year': 2024, 'format': 'PDF',
        'quality_flags': ['historical_operations_report_not_complete_network_case']})]


def collect_routes(path):
    name = 'routes_2023.jsonl'
    old = reused(name, PAPER)
    if old:
        source = reused('routes_2023_source_table.md', PAPER)
        if source is None:
            raise ValueError('routes_source_missing')
        return [source, old]
    text = path.read_text()
    rows = routes(text)
    if [row['number'] for row in rows] != list(range(1, 40)):
        raise ValueError('historical_route_table_incomplete')
    source = publish('routes_2023_source_table.md', text.encode(),
                     {'source': PAPER, 'format': 'Markdown table', 'source_year': 2023})
    raw = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode()
    metadata = publish(name, raw, {'source': PAPER, 'source_table': source['file'], 'format': 'JSONL',
        'row_count': len(rows), 'source_year': 2023, 'unit': 'MVA',
        'quality_flags': ['historical_not_current_grid', 'rx_and_bus_ids_not_provided',
                          'source_spelling_and_repeated_names_preserved', 'gis_matching_unverified']})
    return [source, metadata]


async def main(route_path):
    ROOT.mkdir(parents=True, exist_ok=True)
    jobs = [('connections', collect_connections),
            ('generation', lambda: collect_catalogue('15100214', 'generation')),
            ('wind', lambda: collect_catalogue('15047557', 'wind')),
            ('curtailment', lambda: collect_catalogue('15132422', 'curtailment')),
            ('operations', collect_pdf), ('routes', lambda: collect_routes(route_path))]
    slots = asyncio.Semaphore(3)

    async def run(label, job):
        async with slots:
            try:
                datasets = await asyncio.to_thread(job)
                print(label + ': ' + ', '.join(str(item.get('row_count', item['bytes'])) for item in datasets))
                return datasets, None
            except Exception as error:
                # Never print command arguments, config values or response bodies.
                reason = str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z_]+', str(error)) else type(error).__name__
                return [], label + ':' + reason

    results = await asyncio.gather(*(run(label, job) for label, job in jobs))
    datasets = [dataset for batch, _ in results for dataset in batch]
    errors = [error for _, error in results if error]
    manifest = {'complete': not errors, 'generated_at': datetime.now(timezone.utc).isoformat(),
                'datasets': datasets, 'errors': errors, 'electrical_twin_ready': False,
                'pending': ['verified_current_bus_line_transformer_connectivity', 'line_rx_b_and_ratings',
                            'transformer_impedances_and_taps', 'bus_p_q_and_hvdc_operating_measurements',
                            'jeju_wind_observations', 'building_register_height_enrichment',
                            'surveyed_multi_view_facility_photos'],
                'notes': ['complete applies only to these collection jobs',
                          'archive row counts and names do not prove present physical asset counts']}
    part = ROOT / 'manifest.json.part'
    with part.open('w') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    part.replace(ROOT / 'manifest.json')
    print(f'collection_complete={not errors}; datasets={len(datasets)}; errors={errors}')
    return 1 if errors else 0


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--routes':
        route_path = Path(sys.argv[2])
    elif len(sys.argv) == 1:
        route_path = ROOT / 'routes_2023_source_table.md'
    else:
        raise SystemExit('usage: collect_power_data.py [--routes source-table.md]')
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / '.collector.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('collection_already_running')
        raise SystemExit(asyncio.run(main(route_path)))
