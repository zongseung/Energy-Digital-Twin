"""Render the shared GIS/mock payload in actual Omniverse Kit; no live power data."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time
import traceback


def build(stage, data):
    from pxr import Gf, Sdf, UsdGeom, UsdLux

    if data["schema_version"] != 1:
        raise ValueError("Unsupported scene schema")
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1000)  # Horizontal scene units are kilometres.
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    world.GetPrim().SetCustomData({"preview": "MOCK geometry and exaggerated DEM; no live power values",
                                  "heightNote": data["terrain"].get("height_note", "unvalidated")})

    def color(prim, rgb):
        prim.CreateDisplayColorAttr([Gf.Vec3f(*rgb)])

    terrain = data["terrain"]
    vertices = terrain["positions"]
    indices = terrain["indices"]
    if len(vertices) % 3 or len(indices) % 3 or not indices:
        raise ValueError("Terrain must contain indexed triangles")
    mesh = UsdGeom.Mesh.Define(stage, "/World/Terrain")
    mesh.CreatePointsAttr([Gf.Vec3f(*vertices[i:i + 3]) for i in range(0, len(vertices), 3)])
    mesh.CreateFaceVertexCountsAttr([3] * (len(indices) // 3))
    mesh.CreateFaceVertexIndicesAttr(indices)
    mesh.CreateSubdivisionSchemeAttr("none")
    mesh.CreateDoubleSidedAttr(True)
    color(mesh, (0.16, 0.34, 0.24))
    sea = UsdGeom.Cube.Define(stage, "/World/Sea")
    sea.AddTranslateOp().Set(Gf.Vec3d(0, -0.12, 0))
    sea.AddScaleOp().Set(Gf.Vec3f(200, 0.08, 200))
    color(sea, (0.04, 0.15, 0.24))

    def curves(path, lines, rgb, width):
        lines = [line for line in lines if len(line) >= 2]
        if not lines:
            return
        prim = UsdGeom.BasisCurves.Define(stage, path)
        prim.CreateTypeAttr("linear")
        prim.CreateCurveVertexCountsAttr([len(line) for line in lines])
        prim.CreatePointsAttr([Gf.Vec3f(x, y + 0.08, z) for line in lines for x, y, z in line])
        prim.CreateWidthsAttr([width])
        prim.SetWidthsInterpolation("constant")
        color(prim, rgb)

    curves("/World/Coast", data["coast"], (0.40, 0.85, 0.78), 0.07)
    curves("/World/GridLines", [line["points"] for line in data["lines"]], (0.95, 0.63, 0.18), 0.09)
    palettes = {"wind": (0.40, 0.83, 0.96), "pv_facility": (0.96, 0.73, 0.20),
                "substation": (0.91, 0.38, 0.27)}
    for i, facility in enumerate(data["facilities"]):
        # ponytail: visible regional markers, replace with surveyed assets for facility views.
        marker = UsdGeom.Cube.Define(stage, f"/World/Facilities/F{i}")
        x, y, z = facility["position"]
        marker.AddTranslateOp().Set(Gf.Vec3d(x, y + 0.18, z))
        marker.AddScaleOp().Set(Gf.Vec3f(0.12, 0.18, 0.12))
        kind = "wind" if facility.get("source_properties", {}).get("plant_source") == "wind" else facility["kind"]
        color(marker, palettes.get(kind, (0.78, 0.82, 0.86)))
        marker.GetPrim().CreateAttribute("facilityId", Sdf.ValueTypeNames.String).Set(str(facility["id"]))
        marker.GetPrim().CreateAttribute("mockShape", Sdf.ValueTypeNames.Bool).Set(True)
    UsdLux.DomeLight.Define(stage, "/World/Sky").CreateIntensityAttr(500)
    sun = UsdLux.DistantLight.Define(stage, "/World/Sun")
    sun.CreateIntensityAttr(2500)
    sun.AddRotateXYZOp().Set(Gf.Vec3f(-50, -25, 0))
    camera = UsdGeom.Camera.Define(stage, "/World/Camera")
    camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(
        Gf.Vec3d(20, 80, 85), Gf.Vec3d(0, 0, 0), Gf.Vec3d(0, 1, 0)).GetInverse())
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.1, 500))
    camera.CreateFocalLengthAttr(28)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, default=Path("var/rendering/mock/scene.json"))
    parser.add_argument("--output", type=Path, default=Path("var/rendering/omniverse"))
    parser.add_argument("--gpu", type=int, default=1)
    args = parser.parse_args()
    args.scene = args.scene.resolve(strict=True)
    args.output = args.output.resolve()
    data = json.loads(args.scene.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    if any((args.output / name).exists() for name in ("jeju-mock.usda", "jeju-mock.png", "evidence.json")):
        raise FileExistsError("Choose a fresh output directory; existing render artifacts are preserved")
    from omni.kit_app import KitApp
    app = KitApp()
    app.startup([str(Path(__file__).with_name("mock.kit").resolve()), "--no-window",
                 f"--/renderer/activeGpu={args.gpu}", "--/renderer/multiGpu/enabled=false"])
    import omni.usd
    from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport
    context = omni.usd.get_context()
    context.new_stage()
    stage = context.get_stage()
    build(stage, data)
    usd_path = args.output / "jeju-mock.usda"
    stage.GetRootLayer().Export(str(usd_path))
    from pxr import Usd, UsdGeom
    saved = Usd.Stage.Open(str(usd_path))
    saved_mesh = UsdGeom.Mesh(saved.GetPrimAtPath("/World/Terrain"))
    assert len(saved_mesh.GetFaceVertexIndicesAttr().Get()) == len(data["terrain"]["indices"])
    assert len(saved.GetPrimAtPath("/World/Facilities").GetChildren()) == len(data["facilities"])

    async def capture():
        import omni.kit.app
        for _ in range(120):
            await omni.kit.app.get_app().next_update_async()
        viewport = get_active_viewport()
        if viewport is None:
            raise RuntimeError("Kit has no rendering viewport")
        viewport.camera_path = "/World/Camera"
        viewport.resolution = (1600, 1000)
        for _ in range(120):
            await omni.kit.app.get_app().next_update_async()
        result = capture_viewport_to_file(viewport, str((args.output / "jeju-mock.png").resolve()))
        await result.wait_for_result(completion_frames=60)
        image = args.output / "jeju-mock.png"
        while not image.is_file() or image.stat().st_size < 1000:
            await omni.kit.app.get_app().next_update_async()

    task = asyncio.ensure_future(capture())
    started = time.monotonic()
    try:
        while not task.done() and time.monotonic() - started < 180:
            app.update()
        if not task.done():
            task.cancel()
            raise TimeoutError("Kit capture did not finish within 180 seconds")
        task.result()
        image = args.output / "jeju-mock.png"
        if not image.is_file() or image.stat().st_size < 1000:
            raise RuntimeError("Kit did not produce a PNG")
        evidence = {"status": "rendered_mock", "renderer": "Omniverse Kit 106.5.0.162521 RTX",
                    "gpu_index": args.gpu, "scene_sha256": hashlib.sha256(args.scene.read_bytes()).hexdigest(),
                    "source_metadata": data.get("metadata"), "mock": True,
                    "terrain_triangles": len(data["terrain"]["indices"]) // 3,
                    "facility_markers": len(data["facilities"]),
                    "png_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                    "elapsed_seconds": time.monotonic() - started}
        (args.output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(evidence, indent=2))
    except Exception:
        traceback.print_exc()
        import omni.kit.app
        omni.kit.app.get_app().post_quit(1)
        raise
    finally:
        app.shutdown()


if __name__ == "__main__":
    main()
