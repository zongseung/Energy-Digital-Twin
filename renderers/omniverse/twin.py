"""Native GLB-to-USD conversion and RTX captures; source positions, estimated geometry."""
import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import struct
import time
import traceback
from urllib.request import urlopen


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def glb_summary(path, facilities=()):
    payload = path.read_bytes()
    magic, version, length = struct.unpack_from('<4sII', payload)
    if magic != b'glTF' or version != 2 or length != len(payload):
        raise ValueError('Invalid GLB header')
    chunk_length, chunk_type = struct.unpack_from('<II', payload, 12)
    if chunk_type != 0x4E4F534A:
        raise ValueError('Missing GLB JSON chunk')
    data = json.loads(payload[20:20 + chunk_length])
    meshes = data.get('meshes', [])
    return {'nodes': len(data.get('nodes', [])), 'meshes': len(meshes),
            'materials': len(data.get('materials', [])),
            'mesh_instances': sum(1 for node in data.get('nodes', []) if 'mesh' in node),
            'named_facilities': [n['name'] for n in data.get('nodes', []) if n.get('name') in {f['node'] for f in facilities}]}


def inspect_stage(stage):
    from pxr import Usd, UsdGeom, UsdShade
    meshes = []
    points_total = faces_total = 0
    materials = shaders = 0
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Xformable):
            matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            if any(not math.isfinite(value) for row in matrix for value in row):
                raise ValueError(f'Nonfinite transform: {prim.GetPath()}')
        if prim.IsA(UsdShade.Material):
            materials += 1
        if prim.IsA(UsdShade.Shader):
            shaders += 1
            for attr in prim.GetAttributes():
                value = attr.Get()
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(f'Nonfinite shader value: {prim.GetPath()}')
                if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
                    if any(isinstance(v, (int, float)) and not math.isfinite(v) for v in value):
                        raise ValueError(f'Nonfinite shader vector: {prim.GetPath()}')
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if not points or not counts or not indices or sum(counts) != len(indices):
            raise ValueError(f'Empty or invalid mesh: {prim.GetPath()}')
        if any(not math.isfinite(v) for point in points for v in point):
            raise ValueError(f'Nonfinite mesh: {prim.GetPath()}')
        if any(count < 3 for count in counts):
            raise ValueError(f'Invalid mesh face size: {prim.GetPath()}')
        if any(i < 0 or i >= len(points) for i in indices):
            raise ValueError(f'Out of range mesh indices: {prim.GetPath()}')
        points_total += len(points)
        faces_total += len(counts)
        meshes.append(str(prim.GetPath()))
    if not meshes:
        raise ValueError('Converted USD has no meshes')
    return {'meshes': len(meshes), 'points': points_total, 'faces': faces_total,
            'materials': materials, 'shaders': shaders,
            'prim_count': sum(1 for _ in stage.Traverse()),
            'metres_per_unit': UsdGeom.GetStageMetersPerUnit(stage),
            'up_axis': str(UsdGeom.GetStageUpAxis(stage)), 'mesh_paths': meshes[:20]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gpu', type=int, default=1)
    parser.add_argument('--views', help='Comma-separated manifest cameras, at most five; default preserves close/array turbine views')
    parser.add_argument('--convert-only', action='store_true', help='Import smoke test, no Jeju/facility claims')
    args = parser.parse_args()
    args.asset = args.asset.resolve(strict=True)
    args.output = args.output.resolve()
    if args.output.exists():
        raise FileExistsError('Choose a fresh output directory; existing outputs are preserved')
    if not args.convert_only and args.manifest is None:
        raise ValueError('An asset manifest is required for the estimated facility scene')
    manifest = json.loads(args.manifest.resolve(strict=True).read_text()) if args.manifest else None
    views = [value.strip() for value in args.views.split(',')] if args.views else None
    if views is not None and (not 1 <= len(views) <= 5 or len(set(views)) != len(views) or
                              any(view not in {'inspect', 'overview', 'array', 'network', 'hvdc', 'pv', 'terrain', 'buildings', 'sea'} for view in views)):
        raise ValueError('Choose one to five distinct supported camera views')
    if views and (not manifest or any(view not in manifest.get('cameras', {}) for view in views)):
        raise ValueError('Requested camera is absent from the manifest')
    source = glb_summary(args.asset, manifest.get('facilities', []) if manifest else [])
    asset_hash = digest(args.asset)
    manifest_hash = digest(args.manifest.resolve()) if args.manifest else None
    args.output.mkdir(parents=True)
    from omni.kit_app import KitApp
    app = KitApp()
    app.startup([str(Path(__file__).with_name('twin.kit').resolve()), '--no-window',
                 f'--/renderer/activeGpu={args.gpu}', '--/renderer/multiGpu/enabled=false'])
    import omni.kit.app
    import omni.kit.asset_converter
    import omni.usd
    from pxr import Usd, UsdGeom, UsdLux, Gf, Sdf
    from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
    started = time.monotonic()

    async def execute():
        converter_context = omni.kit.asset_converter.AssetConverterContext()
        converter_context.ignore_camera = True
        converter_context.ignore_light = True
        converter_context.export_preview_surface = True
        converter_context.use_meter_as_world_unit = True
        converter_context.single_mesh = True
        converter_context.merge_all_meshes = False
        converted = args.output / 'converted.usda'
        conversion = omni.kit.asset_converter.get_instance().create_converter_task(
            str(args.asset), str(converted), None, converter_context)
        if not await conversion.wait_until_finished():
            raise RuntimeError(f'Native asset conversion failed ({conversion.get_status()}): {conversion.get_error_message()}')
        imported = Usd.Stage.Open(str(converted))
        before = inspect_stage(imported)
        if before['meshes'] != source['mesh_instances']:
            raise ValueError('Native USD mesh count differs from GLB mesh instances')
        if args.convert_only:
            return {'status': 'native_glb_import_smoke', 'input_glb': source, 'converted_usd': before}
        context = omni.usd.get_context()
        await context.open_stage_async(str(converted))
        stage = context.get_stage()
        if UsdGeom.GetStageUpAxis(stage) != UsdGeom.Tokens.y:
            raise ValueError('Native GLB import must preserve y-up')
        if UsdGeom.GetStageMetersPerUnit(stage) != 1:
            raise ValueError('Native GLB import must use metres_per_unit=1')
        world = UsdGeom.Xform.Define(stage, '/World')
        stage.SetDefaultPrim(world.GetPrim())
        world.GetPrim().CreateAttribute('assetSha256', Sdf.ValueTypeNames.String).Set(asset_hash)
        world.GetPrim().CreateAttribute('manifestSha256', Sdf.ValueTypeNames.String).Set(manifest_hash)
        world.GetPrim().CreateAttribute('estimatedGeometry', Sdf.ValueTypeNames.Bool).Set(True)
        world.GetPrim().CreateAttribute('assetManifestJson', Sdf.ValueTypeNames.String).Set(json.dumps(manifest, ensure_ascii=False))
        roots = [prim for prim in stage.Traverse() if prim.GetName() in source['named_facilities']]
        if not roots or len(roots) != len(source['named_facilities']):
            raise ValueError('Native converter did not preserve stable facility root names')
        records = {facility['node']: facility for facility in manifest['facilities']}
        if len(records) != len(manifest['facilities']):
            raise ValueError('Manifest facility node names must be unique')
        if set(records) != {prim.GetName() for prim in roots}:
            raise ValueError('Manifest and converted facility root names differ')
        for prim in roots:
            record = records[prim.GetName()]
            transform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            photo = record.get('photo_asset')
            position = transform.Transform(Gf.Vec3d(*(photo['asset_anchor_xyz'] if photo else (0, 0, 0))))
            if any(abs(position[i] - record['position'][i]) > .01 for i in range(3)):
                raise ValueError(f'Converted facility position differs from manifest: {prim.GetName()}')
            if photo:
                a, b = (transform.Transform(Gf.Vec3d(*point)) for point in photo['length_endpoints_xyz'])
                if abs((b - a).GetLength() - photo['verified_length_m']) > .01:
                    raise ValueError(f'Converted photo reference length differs from manifest: {prim.GetName()}')
            if record.get('rotor_node') and not any(child.GetName() == record['rotor_node'] for child in Usd.PrimRange(prim)):
                raise ValueError(f'Independent rotor node lost: {prim.GetName()}')
            prim.CreateAttribute('sourcePosition', Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(*record['position']))
            prim.CreateAttribute('sourceCoordinatesJson', Sdf.ValueTypeNames.String).Set(json.dumps(record['coordinates']))
            prim.CreateAttribute('individualActualOutputJson', Sdf.ValueTypeNames.String).Set('null')
            prim.CreateAttribute('facilityId', Sdf.ValueTypeNames.String).Set(str(record['id']))
            prim.CreateAttribute('geometryStatus', Sdf.ValueTypeNames.String).Set(str(photo['status'] if photo else record.get('geometryStatus', 'estimated_geometry_real_source_position')))
            prim.CreateAttribute('facilityKind', Sdf.ValueTypeNames.String).Set(str(record.get('kind', 'wind_turbine')))
            prim.CreateAttribute('individualActualOutputStatus', Sdf.ValueTypeNames.String).Set('unavailable_null')
        observation = UsdGeom.Xform.Define(stage, '/World/Observations').GetPrim()
        observation.CreateAttribute('scope', Sdf.ValueTypeNames.String).Set('region_only_not_individual_facility_output')
        try:
            with urlopen('http://127.0.0.1:8090/api/v1/jeju/state', timeout=10) as response:
                state = json.load(response)
            observation.CreateAttribute('snapshotJson', Sdf.ValueTypeNames.String).Set(json.dumps(state, ensure_ascii=False))
            observation.CreateAttribute('source', Sdf.ValueTypeNames.String).Set(str(state.get('source', 'unavailable')))
            observation.CreateAttribute('qualityFlags', Sdf.ValueTypeNames.StringArray).Set(state.get('quality_flags', []))
            observation.CreateAttribute('observedAt', Sdf.ValueTypeNames.String).Set(state['observed_at'])
            for key in ('demand_mw', 'supply_capacity_mw', 'wind_mw', 'solar_mw', 'renewable_total_mw'):
                value = state.get(key)
                if isinstance(value, (int, float)) and math.isfinite(value):
                    observation.CreateAttribute(key, Sdf.ValueTypeNames.Double).Set(value)
        except Exception as error:
            state = {'status': 'snapshot_unavailable', 'error_type': type(error).__name__}
            observation.CreateAttribute('snapshotStatus', Sdf.ValueTypeNames.String).Set('unavailable')
        UsdLux.DomeLight.Define(stage, '/World/Sky').CreateIntensityAttr(250)
        sun = UsdLux.DistantLight.Define(stage, '/World/Sun')
        sun.CreateIntensityAttr(1800)
        sun.AddRotateXYZOp().Set(Gf.Vec3f(-50, -35, 0))
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default', 'render'], useExtentsHint=False)
        bounds = cache.ComputeWorldBound(roots[0]).ComputeAlignedBox()
        target = bounds.GetMidpoint()
        size = bounds.GetSize()
        radius = max(size[0], size[1], size[2], 1)
        whole = cache.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedBox()
        whole_target = whole.GetMidpoint()
        whole_size = whole.GetSize()
        array_radius = max(whole_size[0], whole_size[1], whole_size[2], 1)
        cameras = [('Close', None, target + Gf.Vec3d(radius * 1.0, radius * .5, radius * 1.8), target, 45),
                   ('Array', None, whole_target + Gf.Vec3d(array_radius * .65, array_radius * .65, array_radius * 1.0), whole_target, 35)]
        if manifest.get('cameras'):
            requested = [(key.title(), key) for key in views] if views else [
                ('Close', 'inspect'), ('Array', 'array' if 'array' in manifest['cameras'] else 'overview')]
            cameras = [(name, key, Gf.Vec3d(*manifest['cameras'][key]['position']),
                        Gf.Vec3d(*manifest['cameras'][key]['target']), 35) for name, key in requested]
        for name, key, eye, aim, focal in cameras:
            if name == 'Close':
                eye = aim + (eye - aim) * 1.35
            camera = UsdGeom.Camera.Define(stage, f'/World/Camera{name}')
            camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, aim, Gf.Vec3d(0, 1, 0)).GetInverse())
            config = manifest['cameras'][key] if key else {}
            near, far = config.get('near', .1), config.get('far', max(10000, array_radius * 10))
            if not 0 < near < far:
                raise ValueError('Camera clipping range is invalid')
            camera.CreateClippingRangeAttr(Gf.Vec2f(near, far))
            if key:
                fov = config.get('fov', 48)
                if not 1 < fov < 170:
                    raise ValueError('Camera vertical FOV out of range')
                camera.CreateHorizontalApertureAttr(36)
                camera.CreateVerticalApertureAttr(22.5)
                focal = 22.5 / (2 * math.tan(math.radians(fov) / 2))
            camera.CreateFocalLengthAttr(focal)
        scene_path = args.output / ('estimated-scene.usda' if views else 'estimated-turbines.usda')
        stage.GetRootLayer().Export(str(scene_path))
        reopened = Usd.Stage.Open(str(scene_path))
        validated = inspect_stage(reopened)
        for key in ('meshes', 'points', 'faces', 'materials', 'metres_per_unit', 'up_axis'):
            if validated[key] != before[key]:
                raise ValueError(f'USD roundtrip changed imported asset {key}')
        viewport = get_active_viewport()
        if viewport is None:
            raise RuntimeError('Kit has no RTX viewport')
        viewport.resolution = (1600, 1000)
        images = {}
        for name, *_ in cameras:
            viewport.camera_path = f'/World/Camera{name}'
            for _ in range(180):
                await omni.kit.app.get_app().next_update_async()
            image = args.output / f'{name.lower()}.png'
            capture = capture_viewport_to_file(viewport, str(image))
            await capture.wait_for_result(completion_frames=60)
            while not image.is_file() or image.stat().st_size < 1000:
                await omni.kit.app.get_app().next_update_async()
            header = image.read_bytes()[:24]
            if header[:8] != b'\x89PNG\r\n\x1a\n' or struct.unpack('>II', header[16:24]) != (1600, 1000):
                raise ValueError('Capture must be a 1600x1000 PNG')
            images[name.lower()] = {'path': str(image), 'sha256': digest(image), 'resolution': [1600, 1000]}
        return {'status': 'estimated_geometry_real_source_positions', 'input_glb': source,
                'converted_usd': before, 'exported_usd': validated,
                'facility_ids': [prim.GetAttribute('facilityId').Get() for prim in roots],
                'assumptions': manifest, 'regional_observation': state, 'individual_actual_output': None,
                'usd_sha256': digest(scene_path), 'captures': images}

    task = asyncio.ensure_future(execute())
    try:
        while not task.done() and time.monotonic() - started < 240:
            app.update()
        if not task.done():
            task.cancel()
            raise TimeoutError('Native conversion/capture exceeded 240 seconds')
        evidence = task.result()
        if digest(args.asset) != asset_hash or (args.manifest and digest(args.manifest.resolve()) != manifest_hash):
            raise ValueError('Input asset/manifest changed during conversion')
        evidence.update({'renderer': 'Omniverse Kit 106.5.0.162521 RTX', 'asset_converter': '2.8.9',
                         'gpu_index': args.gpu, 'asset_sha256': asset_hash, 'manifest_sha256': manifest_hash,
                         'elapsed_seconds': time.monotonic() - started})
        (args.output / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': evidence['status'], 'output': str(args.output), 'gpu_index': args.gpu}))
    except Exception:
        traceback.print_exc()
        omni.kit.app.get_app().post_quit(1)
        raise
    finally:
        app.shutdown()


if __name__ == '__main__':
    main()
