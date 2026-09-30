"""Run with Python 3.11+: boundary/failure checks without loading models or CUDA."""

import argparse
import json
import struct
from pathlib import Path
import tempfile
from unittest.mock import patch

import run


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    image = root / "input.png"
    output = root / "output"
    args = argparse.Namespace(image=image, output=output, seed=42, faces=100_000)
    try:
        run.run(args)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Missing input must fail before creating output")
    assert not output.exists()
    image.write_bytes(b"source bytes must not change")
    output.mkdir()
    sentinel = output / "asset.glb"
    sentinel.write_bytes(b"existing asset")
    try:
        run.run(args)
    except FileExistsError:
        pass
    else:
        raise AssertionError("Existing output must never be overwritten")
    assert sentinel.read_bytes() == b"existing asset"
    args.output = root / "failed"
    with patch.object(run, "generate", side_effect=RuntimeError("inference failed")):
        try:
            run.run(args)
        except RuntimeError as error:
            assert str(error) == "inference failed"
        else:
            raise AssertionError("Inference failure must propagate")
    assert not (args.output / "metadata.json").exists()
    assert image.read_bytes() == b"source bytes must not change"
    payload = json.dumps({"asset":{"version":"2.0"}, "meshes":[{}],
                          "images":[{"mimeType":"image/png"}]}).encode()
    payload += b" " * (-len(payload) % 4)
    malformed = root / "malformed.glb"
    malformed.write_bytes(struct.pack("<4sIII4s", b"glTF", 2, len(payload) + 20,
                                      len(payload), b"JSON") + payload)
    try:
        run.check_glb(malformed)
    except (ValueError, struct.error):
        pass
    else:
        raise AssertionError("JSON-only GLB without geometry/texture payload must fail")
print("PASS: missing input, output collision, inference failure, malformed GLB rejection")
