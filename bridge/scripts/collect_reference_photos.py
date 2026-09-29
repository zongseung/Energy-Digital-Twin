"""Collect official place-gallery samples for local reference, with provenance."""
import asyncio
import hashlib
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path('/mnt/iscsi/energy-digital-twin/geography/jeju/reference_photos')
PLACES = [
    ('sinchang', 'CNTS_200000000007676'),
    ('sinchang_chagwi', 'CONT_000000000500403'),
    ('chuja', 'CNTS_000000000018441'),
]

def gallery(html, contents_id):
    match = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if match is None:
        raise ValueError('gallery_format_changed')
    flat = json.loads(match.group(1))

    def resolve(index, depth=0):
        if not isinstance(index, int) or index < 0:
            return None
        if depth > 16:
            raise ValueError('gallery_nesting_limit')
        value = flat[index]
        if isinstance(value, dict):
            return {key: resolve(index, depth + 1) for key, index in value.items()}
        if isinstance(value, list):
            return [resolve(index, depth + 1) for index in value]
        return value

    for value in flat:
        if isinstance(value, dict) and 'photo' in value and 'contentsid' in value:
            if resolve(value['contentsid']) == contents_id:
                photos = {}
                for item in resolve(value['photo']):
                    photo = item['photoid']
                    photos.setdefault(photo['photoid'], photo)
                return list(photos.values())
    raise ValueError('place_gallery_missing')

def fetch(url):
    with urllib.request.urlopen(url, timeout=45) as response:
        if urllib.parse.urlsplit(response.url).hostname not in {'www.visitjeju.net', 'api.cdn.visitjeju.net'}:
            raise ValueError('unexpected_photo_host')
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('photo_response_too_large')
    return raw

def write_json(path, value):
    part = Path(str(path) + '.part')
    with part.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    part.replace(path)

def save_photo(place, source_page, photo):
    url = photo['imgpath']
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'api.cdn.visitjeju.net':
        raise ValueError('unexpected_photo_url')
    photo_id = str(photo['photoid'])
    if not photo_id.isdecimal():
        raise ValueError('invalid_photo_id')
    path = ROOT / (place + '-' + photo_id + Path(parsed.path).suffix)
    metadata_path = Path(str(path) + '.metadata.json')
    if path.exists() and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        if metadata['sha256'] != hashlib.sha256(path.read_bytes()).hexdigest() or metadata['url'] != url:
            raise ValueError('photo_verification_failed')
        return metadata
    raw = fetch(url)
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        image_format = image.format
        image.verify()
    metadata = {
        'photo_id': photo_id, 'place': place, 'source_page': source_page, 'url': url,
        'file': path.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        'width': width, 'height': height, 'format': image_format,
        'collected_at': datetime.now(timezone.utc).isoformat(), 'complete': True,
        'captured_at': None, 'camera_pose': None,
        'license_status': 'per_image_terms_not_verified', 'use_status': 'local_reference_only',
        'reconstruction_eligible': False,
    }
    part = Path(str(path) + '.part')
    with part.open('wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    part.replace(path)
    write_json(metadata_path, metadata)
    return metadata

async def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    slots = asyncio.Semaphore(3)
    photos, errors = [], []

    async def collect(place, page, photo):
        async with slots:
            try:
                return await asyncio.to_thread(save_photo, place, page, photo)
            except Exception:
                errors.append(place + ':photo_collection_failed')
                return None

    for place, contents_id in PLACES:
        page = 'https://www.visitjeju.net/kr/detail/view?contentsid=' + contents_id
        try:
            html = (await asyncio.to_thread(fetch, page)).decode('utf-8')
            # ponytail: 12 gallery samples per place; expand only for a specific asset.
            selected = gallery(html, contents_id)[:12]
            if not selected:
                raise ValueError('empty_gallery')
            results = await asyncio.gather(*(collect(place, page, photo) for photo in selected))
            photos.extend(photo for photo in results if photo is not None)
        except Exception:
            errors.append(place + ':gallery_collection_failed')
    complete = bool(photos) and not errors
    write_json(ROOT / 'manifest.json', {'complete': complete, 'photos': photos, 'errors': errors,
        'notes': ['Gallery samples, not a validated multi-view reconstruction set',
                  'Place location is not camera position; CDN dates are not capture dates']})
    print(f'photos={len(photos)}; complete={complete}; errors={errors}')
    return 0 if complete else 1

def check():
    data = [{'contentsid': 1, 'photo': 2}, 'target', [3], {'photoid': 4},
            {'photoid': 5, 'imgpath': 6}, 7, 'https://api.cdn.visitjeju.net/content.webp',
            {'contentsid': 8, 'photo': 9}, 'other', [10], {'photoid': 11},
            {'photoid': 12, 'imgpath': 13}, 99, 'https://api.cdn.visitjeju.net/other.webp']
    html = '<script id="__NUXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'
    assert gallery(html, 'target') == [{'photoid': 7, 'imgpath': 'https://api.cdn.visitjeju.net/content.webp'}]
    data[2] = [3, 3]
    html = '<script id="__NUXT_DATA__" type="application/json">' + json.dumps(data) + '</script>'
    assert len(gallery(html, 'target')) == 1, 'duplicate gallery photos race on the same file'

if __name__ == '__main__':
    if sys.argv[1:] == ['--check']:
        check()
        print('gallery isolation check passed')
    elif sys.argv[1:]:
        raise SystemExit('usage: collect_reference_photos.py [--check]')
    else:
        raise SystemExit(asyncio.run(main()))
