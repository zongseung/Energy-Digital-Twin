# Task 5 batch wrapper review

2026-09-29, read-only review of `inference/{Dockerfile,run.py,check.py,README.md}` and `models/manifest.tsv`. No production files changed and no GPU inference launched by this reviewer. This is upstream TRELLIS.2 batch execution, not vLLM serving.

## Follow-up verdict

**Original P2 resolved; no remaining concrete blocker found in the narrow validation fix.** Current `check_glb` requires a correctly sized BIN chunk and one bounded embedded buffer, checks image bufferView references/ranges, verifies actual PNG/JPEG payloads through Pillow, and reloads finite nonempty indexed triangle geometry through the already installed trimesh. This remains structural/content validation, not full glTF conformance or engine import validation.

Executed `python3 inference/check.py`: passed boundary checks and rejection of the original JSON-only malformed GLB. Also ran a CPU-only, no-network, nonroot, read-only container with current source mounted read-only: generated a textured box GLB using trimesh/Pillow and confirmed acceptance; removed its image bufferView and confirmed rejection. No CUDA device or model was exposed. Root still owns final-image rebuild and checking the actual exported inference GLB; those steps are not asserted complete by this review.

## Finding

**Historical P2, now resolved — `inference/run.py:19`: malformed GLB could pass the embedded-texture/mesh check.** The original checker accepted a JSON-only GLB containing `{"asset":{"version":"2.0"},"meshes":[{}],"images":[{"mimeType":"image/png"}]}`. It had no mesh primitives, image `bufferView`, buffer, or BIN chunk, so neither geometry nor texture data existed. A temporary-file reproduction returned that document successfully. Thus the README's statement that embedded PNG/JPEG textures were checked was stronger than the original check. A truncated/invalid export could become `generated_unvalidated` with completed metadata.

Minimum correction: verify the BIN chunk exists and fits the GLB length; image bufferView indexes, buffer references and byte ranges must address that chunk and payload signatures must agree with PNG/JPEG MIME types. Require nonempty primitives and valid geometry references, or use the already installed trimesh to confirm geometry can be read. Add the malformed JSON-only file to the existing CUDA-free self-check. This does not replace engine import validation or a full glTF conformance validator.

Historical reproduction (current code rejects this; run from repository root):

```python
import importlib.util, json, pathlib, struct, tempfile
spec = importlib.util.spec_from_file_location("batch", "inference/run.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)
with tempfile.TemporaryDirectory() as temporary:
    path = pathlib.Path(temporary) / "broken.glb"
    document = {"asset": {"version": "2.0"}, "meshes": [{}],
                "images": [{"mimeType": "image/png"}]}
    data = json.dumps(document).encode()
    data += b" " * (-len(data) % 4)
    path.write_bytes(struct.pack("<4sIII4s", b"glTF", 2, 20 + len(data),
                                 len(data), b"JSON") + data)
    print(batch.check_glb(path))  # Original implementation unexpectedly succeeded.
```

## Checks and limits

- `python3 inference/check.py` passed missing input, existing output preservation, propagation of inference failure, absence of completed metadata on that failure, and preservation of input bytes. Output creation uses `exist_ok=False`; source input is resolved before creating the output. Completed metadata is written through a partial file only after generation and GLB checks return. A failed output is intentionally preserved and must use a fresh directory for retry.
- The Docker base digest and source/native-extension commits are pinned; manifest revisions match recorded metadata. Runtime redirects TRELLIS checkpoints/decoder, DINOv3 and RMBG to local revision directories. Offline Hugging Face flags and the documented `--network none` run prevent network fallback from becoming a successful unpinned load. Model contents are verified by `scripts/models.sh verify`; the batch runner itself does not rehash every model file, so metadata records the supplied manifest rather than independently attesting model bytes.
- FlexGEMM's pinned cache is copied to writable container tmpfs before importing native modules. The documented separate single-job container avoids shared-path worker races. The Dockerfile pins added kornia packages and runs `uv pip check`; those dependency checks do not establish GPU or mesh export execution.
- The README clearly labels output `generated_unvalidated`, leaves facility ID/metres/axis/pivot and engine import unvalidated, and does not claim vLLM integration, USD conversion, survey accuracy, or rendered FPS. Native extensions target SM 8.6; actual engine/geometry/material compatibility remains separate verification.
- At review, `var/verification/trellis-inference-03.log` showed sparse/attention backend initialization only. Its final runtime result and peak VRAM/export behavior were still pending under the root agent's manual execution. No complete-inference success is asserted here.
