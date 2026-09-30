#!/usr/bin/env python3
"""Read vworld_key from the project .env without copying other environment values.

python3 renderers/site/configure.py --self-test
"""
from pathlib import Path
import re
import tempfile

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
    with tempfile.TemporaryDirectory() as directory:
        env = Path(directory) / '.env'
        for text, expected in [('OTHER=1\n vworld_key = "a=b" \n', 'a=b'), ("vworld_key='x'", 'x'), ('vworld_key=plain', 'plain')]:
            env.write_text(text)
            assert read_key(env) == expected, text
        env.write_text('OTHER=1\n')
        try:
            read_key(env)
        except ValueError:
            print('configure self-test passed')
            return
    raise AssertionError('missing vworld_key accepted')

if __name__ == '__main__':
    self_test()
