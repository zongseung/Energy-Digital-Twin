"""Local, offline TRELLIS.2 batch runner; output dimensions are not surveyed metres."""

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import struct
import shutil
import tempfile
import time


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_glb(path: Path) -> dict:
    with path.open("rb") as stream:
        magic, version, size = struct.unpack("<4sII", stream.read(12))
        if (magic, version, size) != (b"glTF", 2, path.stat().st_size):
            raise ValueError("Invalid GLB header")
        length, kind = struct.unpack("<I4s", stream.read(8))
        if kind != b"JSON" or length > size - 20:
            raise ValueError("Invalid GLB JSON chunk")
        document = json.loads(stream.read(length))
        binary_length, binary_kind = struct.unpack("<I4s", stream.read(8))
        if binary_kind != b"BIN\0" or binary_length != size - 28 - length:
            raise ValueError("GLB must contain its binary payload")
        binary = stream.read(binary_length)
    if not document.get("meshes") or not document.get("images"):
        raise ValueError("GLB must contain mesh and textures")
    if any(image.get("mimeType") not in ("image/png", "image/jpeg") or "uri" in image
           for image in document["images"]):
        raise ValueError("GLB textures must be embedded PNG/JPEG")
    if any("uri" in buffer for buffer in document.get("buffers", [])):
        raise ValueError("GLB buffers must be embedded")
    buffers = document.get("buffers", [])
    if len(buffers) != 1 or not 0 <= binary_length - buffers[0]["byteLength"] <= 3:
        raise ValueError("Invalid GLB buffer length")
    from PIL import Image
    for image in document["images"]:
        index = image.get("bufferView")
        views = document.get("bufferViews", [])
        if type(index) is not int or not 0 <= index < len(views):
            raise ValueError("GLB image has no valid buffer view")
        view = views[index]
        offset, length = view.get("byteOffset", 0), view["byteLength"]
        if view.get("buffer") != 0 or offset < 0 or length <= 0 or offset + length > buffers[0]["byteLength"]:
            raise ValueError("GLB image buffer view is out of bounds")
        with Image.open(io.BytesIO(binary[offset:offset + length])) as texture:
            if texture.format != {"image/png": "PNG", "image/jpeg": "JPEG"}[image["mimeType"]]:
                raise ValueError("GLB texture payload does not match its MIME type")
            texture.verify()
    import numpy as np
    import trimesh
    scene = trimesh.load(path, force="scene", process=False)
    if not scene.geometry or any(
        not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0
        or not np.isfinite(mesh.vertices).all()
        or mesh.faces.min() < 0 or mesh.faces.max() >= len(mesh.vertices)
        for mesh in scene.geometry.values()
    ):
        raise ValueError("GLB contains no valid finite triangle mesh")
    return document


def run(args: argparse.Namespace) -> None:
    image_path = args.image.resolve(strict=True)
    if not image_path.is_file():
        raise ValueError("Input image must be a file")
    if not 0 <= args.seed < 2**32:
        raise ValueError("Seed must be an unsigned 32-bit integer")
    if not 1000 <= args.faces <= 1_000_000:
        raise ValueError("Face budget must be between 1000 and 1000000")
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = generate(args, image_path)
    pending = args.output / "metadata.json.partial"
    pending.write_text(json.dumps(metadata, indent=2) + "\n")
    pending.replace(args.output / "metadata.json")
    print(json.dumps(metadata, indent=2))


def generate(args: argparse.Namespace, image_path: Path) -> dict:
    # FlexGEMM locks even read-only cache loads; keep the pinned seed in writable tmpfs.
    shutil.copyfile("/opt/flexgemm/autotune_cache.json", "/tmp/flex-gemm.json")
    # Imports follow input/output checks so invalid jobs do not initialize CUDA.
    import torch
    from PIL import Image, ImageOps
    from trellis2.pipelines import Trellis2ImageTo3DPipeline
    import o_voxel

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("Expose exactly one CUDA GPU to this batch container")
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    with args.manifest.open() as stream:
        models = list(csv.DictReader(
            (line for line in stream if not line.startswith("#")),
            fieldnames=("key", "repo", "revision", "access", "files"), delimiter="\t",
        ))
    paths = {model["key"]: (args.models / model["key"] / model["revision"]).resolve(strict=True)
             for model in models}
    with tempfile.TemporaryDirectory(prefix="trellis-pipeline-") as temporary:
        local = Path(temporary)
        config = json.loads((paths["trellis2"] / "pipeline.json").read_text())
        (local / "ckpts").symlink_to(paths["trellis2"] / "ckpts", target_is_directory=True)
        (local / "decoder").symlink_to(paths["decoder"], target_is_directory=True)
        config["args"]["models"]["sparse_structure_decoder"] = "decoder/ckpts/ss_dec_conv3d_16l8_fp16"
        config["args"]["image_cond_model"]["args"]["model_name"] = str(paths["dinov3"])
        config["args"]["rembg_model"]["args"]["model_name"] = str(paths["rmbg"])
        config["args"]["low_vram"] = True
        (local / "pipeline.json").write_text(json.dumps(config))
        pipeline = Trellis2ImageTo3DPipeline.from_pretrained(str(local))
        pipeline.cuda()
        with Image.open(image_path) as original:
            image = ImageOps.exif_transpose(original)
            mesh = pipeline.run(image, seed=args.seed, pipeline_type=args.pipeline)[0]
        mesh.simplify(16_777_216)
        asset = o_voxel.postprocess.to_glb(
            vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
            coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
            aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
            decimation_target=args.faces, texture_size=args.texture_size,
            remesh=False, verbose=True,
        )
        asset_path = args.output / "asset.glb"
        asset.export(asset_path, extension_webp=False)
        document = check_glb(asset_path)
        torch.cuda.synchronize()
        metadata = {
            "schema_version": 1, "status": "generated_unvalidated",
            "trellis_code_revision": "75fbf0183001ed9876c8dbb35de6b68552ee08bd",
            "models": models, "image": {"name": image_path.name, "sha256": sha256(image_path),
                                       "source": args.image_source, "license": args.image_license},
            "seed": args.seed, "pipeline": args.pipeline,
            "samplers": {name: config["args"][name] for name in
                         ("sparse_structure_sampler", "shape_slat_sampler", "tex_slat_sampler")},
            "requested_faces": args.faces, "triangles": len(asset.faces),
            "texture_size": args.texture_size, "remesh": False,
            "elapsed_seconds": time.perf_counter() - started,
            "peak_allocated_vram_bytes": torch.cuda.max_memory_allocated(),
            "peak_reserved_vram_bytes": torch.cuda.max_memory_reserved(),
            "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__, "cuda": torch.version.cuda,
            "asset": {"file": "asset.glb", "sha256": sha256(asset_path),
                      "bytes": asset_path.stat().st_size, "meshes": len(document["meshes"])},
            "facility_id": None, "metres_per_unit": None, "axis_alignment": "unvalidated",
            "estimated_unseen_surfaces": True, "rotor_pivot": None,
            "engine_import_validated": False,
        }
        return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--models", type=Path, default=Path("/models"))
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.tsv"))
    parser.add_argument("--image-source", required=True)
    parser.add_argument("--image-license", required=True)
    parser.add_argument("--pipeline", choices=("512", "1024", "1024_cascade", "1536_cascade"), default="512")
    # ponytail: conservative first asset budget; calibrate using measured renderer FPS.
    parser.add_argument("--faces", type=int, default=100_000)
    parser.add_argument("--texture-size", type=int, choices=(512, 1024, 2048, 4096), default=1024)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
