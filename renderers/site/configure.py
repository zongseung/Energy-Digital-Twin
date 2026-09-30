#!/usr/bin/env python3
"""Render the public browser SDK URL without copying other environment values."""
import argparse
import html
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlencode, urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = '@@VWORLD_INIT_URL@@'

def render(template: str, key: str) -> str:
    if not key or any(ord(c) < 32 for c in key):
        raise ValueError('vworld_key must be nonempty and contain no control characters')
    if template.count(PLACEHOLDER) != 1:
        raise ValueError('SDK URL placeholder must occur exactly once')
    url = 'https://map.vworld.kr/js/webglMapInit.js.do?' + urlencode({'version': '3.0', 'apiKey': key})
    return template.replace(PLACEHOLDER, html.escape(url, quote=True))

def read_key(path: Path) -> str:
    for line in path.read_text().splitlines():
        match = re.match(r'^\s*vworld_key\s*=\s*(.*?)\s*$', line)
        if match:
            value = match[1]
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    raise ValueError('vworld_key is missing')

def self_test() -> None:
    fixture = 'test-value<&" +'
    result = render('<script src="' + PLACEHOLDER + '"></script>', fixture)
    url = html.unescape(result.split('src="', 1)[1].split('"', 1)[0])
    assert urlparse(url).netloc == 'map.vworld.kr'
    assert parse_qs(urlparse(url).query) == {'version': ['3.0'], 'apiKey': [fixture]}
    assert '&amp;' in result and PLACEHOLDER not in result
    for template, key in [('no placeholder', fixture), (PLACEHOLDER * 2, fixture), (PLACEHOLDER, ''), (PLACEHOLDER, 'invalid\nkey')]:
        try:
            render(template, key)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid configuration accepted')
    print('configure self-test passed')

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    rendered = render((ROOT / 'renderers/site/index.html').read_text(), read_key(ROOT / '.env'))
    output = ROOT / 'var/rendering/site/index.html'
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=output.parent, prefix='.index-')
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(rendered)
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)

if __name__ == '__main__':
    main()
