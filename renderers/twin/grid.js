import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {Line2} from 'three/addons/lines/Line2.js';
import {LineGeometry} from 'three/addons/lines/LineGeometry.js';
import {LineMaterial} from 'three/addons/lines/LineMaterial.js';

const colors = {wind:'#e1e8e5', transmission:'#edc476', hvdc:'#bb9def', cable:'#83d5c1', substation:'#efb0ac', pv:'#9cbef5', coast:'#91afb0', selected:'#72cbd5'};
const labels = {transmission:'송전선', hvdc:'HVDC 경로', cable:'케이블', substation:'변전소·변환소'};
const voltage = (value) => value && Number.isFinite(Number(value)) ? `${Number(value) / 1000} kV (원천)` : '미상';
const vector = (position) => new THREE.Vector3(...position);

export async function loadGrid() {
  const response = await fetch('/local/manifest.json', {signal:AbortSignal.timeout(20000), cache:'no-store'});
  if (!response.ok) throw new Error('Local scene manifest unavailable');
  const data = await response.json();
  if (!Array.isArray(data.routes) || !Array.isArray(data.facilities) || !data.facilities.some((f) => f.kind === 'wind')) throw new Error('Invalid local scene manifest');
  const gltf = await new GLTFLoader().loadAsync('/local/scene.glb');
  const object = new THREE.Group(); object.name = 'tamra_hallim'; object.add(gltf.scene);
  const records = [], pickables = [], materials = [], lines = new Map(), layers = new Map();
  const physical = new Map(), rotors = [], nacelles = [];
  for (const item of data.facilities) {
    const root = gltf.scene.getObjectByName(item.node);
    if (!root) throw new Error(`Network model missing ${item.node}`);
    physical.set(item.id, root);
    if (item.rotor_node) {
      const rotor = root.getObjectByName(item.rotor_node);
      if (!rotor) throw new Error(`Missing rotor ${item.rotor_node}`);
      rotor.userData.facilityId = item.id;
      rotors.push(rotor);
      const nacelle = root.getObjectByName(`${item.node}_nacelle`);
      if (!nacelle) throw new Error(`Missing nacelle ${item.node}`);
      nacelles.push({id:item.id, node:nacelle, original:nacelle.quaternion.clone()});
    }
  }
  function attach(record, child) {
    child.userData.facility = record; pickables.push(child);
    const children = layers.get(record.layer) || []; children.push(child); layers.set(record.layer, children);
  }
  for (const route of data.routes) {
    if (!Array.isArray(route.paths) || route.paths.some((path) => path.length < 2 || path.some((p) => p.length !== 3 || !p.every(Number.isFinite)))) throw new Error('Invalid local route positions');
    if (!route.paths.length) continue;
    const record = {...route, kind:route.kind, layer:'transmission'};
    record.name = route.name || `${labels[route.kind]} ${route.id.split(':').at(-1)}`;
    const bounds = new THREE.Box3().setFromPoints(route.paths.flat().map(vector));
    record.position = bounds.getCenter(new THREE.Vector3()).toArray();
    record.view = route.id === 'hub:power_line:3596' ? data.cameras.inspect : {
      position:vector(record.position).add(new THREE.Vector3(0, 1, 1).normalize().multiplyScalar(Math.max(450, bounds.getSize(new THREE.Vector3()).length() * 1.4))).toArray(),
      target:record.position, fov:48,
    };
    record.rows = [
      ['종류 / 전압', `${labels[record.kind]} · ${voltage(route.voltage)}`],
      ['원천 ID', (route.source_ids || [route.id]).join(', ')],
      ['형상 근거', route.id === 'hub:power_line:3596' ? 'GIS 경로 오버레이 / 근접 철탑·처짐: 추정' : 'GIS 경로 오버레이 / 굵기·높이: 시각화용'],
      ['전기적 연결 / 실측 흐름', '미확인 / —'],
    ];
    const routeLines = [];
    for (const path of route.paths) {
      const geometry = new LineGeometry(); geometry.setPositions(path.flat());
      // GIS strokes are map overlays; physical conductors retain normal terrain occlusion.
      const material = new LineMaterial({color:colors[record.kind], linewidth:2.5, depthTest:false, depthWrite:false});
      const line = new Line2(geometry, material); line.renderOrder = 10; line.computeLineDistances();
      object.add(line); materials.push(material); routeLines.push(line); attach(record, line);
    }
    lines.set(record.id, routeLines);
    if (physical.has(record.id)) attach(record, physical.get(record.id));
    records.push(record);
  }
  for (const station of data.facilities.filter((f) => f.kind !== 'line')) {
    const isPV = station.kind === 'pv', isWind = station.kind === 'wind';
    const photo = station.photo_asset;
    const record = {...station, layer:station.kind, name:station.name || station.source_properties?.name || station.source_properties?.name_en || `시설 ${station.id.split(':').at(-1)}`};
    record.view = station.inspect || {position:vector(station.position).add(new THREE.Vector3(-100, 85, 140)).toArray(), target:vector(station.position).add(new THREE.Vector3(0, 5, 0)).toArray(), fov:48};
    if (isWind) {
      const offset = vector(station.position).sub(vector(data.facilities.find((f) => f.kind === 'wind').position));
      record.view = {...data.cameras.wind, position:vector(data.cameras.wind.position).add(offset).toArray(), target:vector(data.cameras.wind.target).add(offset).toArray()};
    }
    record.rows = [
      ['원천 좌표 (경도, 위도)', station.coordinates.map((v) => Number(v).toFixed(6)).join(', ')],
      ...(isWind ? [
        ['단지 공개 제원', '3 MW · 회전자 직경 91.59 m'],
        ['형상 근거', '원천 위치 / 허브·나셀·블레이드·기초 추정'],
        ['개별 발전량', '— (계측 미연결)'],
      ] : isPV ? [
        ['등록 설비용량', Number.isFinite(station.source_properties?.capacity_kw) ? `${station.source_properties.capacity_kw} kW` : '미상'],
        ['자료 기준일', station.source_properties?.data_date || '미상'],
        ['형상 근거', photo ? '등록 위치 / 현장 사진 기반 추정 형상' : '등록 위치 / 패널 수·부지·설치 방식 추정'],
        ['개별 발전량', '— (계측 미연결)'],
      ] : [
      ['전압', voltage(station.source_properties?.voltage)],
      ['시설 구분', station.source_properties?.sub_type || '미상'],
      ['형상 근거', photo ? '원천 위치 / 현장 사진 기반 추정 형상' : '원천 위치 / 변압기·모선·배치·치수 추정'],
      ['실측 부하 / 연결 선로', '— / 미확인'],
      ]),
      ...(photo ? [
        ['사진 출처·이용 조건', `${photo.source} · ${photo.license}`],
        ['검증된 기준 길이', `${photo.verified_length_m} m · 다른 치수는 추정`],
      ] : []),
    ];
    attach(record, physical.get(record.id));
    const marker = new THREE.Points(new THREE.BufferGeometry().setFromPoints([vector(station.position).add(new THREE.Vector3(0, 15, 0))]), new THREE.PointsMaterial({color:colors[record.kind], size:9, sizeAttenuation:false}));
    object.add(marker); attach(record, marker); records.push(record);
  }
  for (const layer of ['terrain', 'buildings', 'sea']) layers.set(layer, []);
  gltf.scene.traverse((node) => {
    if (!node.isMesh) return;
    const layer = node.name.startsWith('building_') ? 'buildings' : node.name === 'ocean_surface' ? 'sea' : /terrain|shore|landcover|road/.test(node.name) ? 'terrain' : null;
    if (layer) layers.get(layer).push(node);
  });
  const visible = {wind:true, transmission:true, substation:true, terrain:true, pv:true, buildings:false, sea:true};
  for (const child of layers.get('buildings')) child.visible = false;
  const [longitude, latitude] = data.coordinateFrame.origin_lon_lat;
  // UTM52 meridian convergence at this small scene's origin; input wind is 16-point true-north bearing.
  const northOffset = -Math.atan(Math.tan(THREE.MathUtils.degToRad(longitude - 129)) * Math.sin(THREE.MathUtils.degToRad(latitude)));
  const up = new THREE.Vector3(0, 1, 0);
  return {
    object, data, records, pickables, visible, rotors,
    resize(width, height) { for (const material of materials) material.resolution.set(width, height); },
    setLayer(layer, enabled) { visible[layer] = enabled; for (const child of layers.get(layer) || []) child.visible = enabled; },
    setWindDirections(directions) {
      object.updateMatrixWorld(true);
      for (const {id, node, original} of nacelles) {
        const fromDegrees = directions.get(id) ?? null;
        if (fromDegrees === null) node.quaternion.copy(original);
        else {
          const world = new THREE.Quaternion().setFromAxisAngle(up, -Math.PI / 2 - THREE.MathUtils.degToRad(fromDegrees) - northOffset);
          node.quaternion.copy(node.parent.getWorldQuaternion(new THREE.Quaternion()).invert().multiply(world));
        }
      }
      object.updateMatrixWorld(true);
    },
    highlight(record) {
      for (const [id, routeLines] of lines) for (const line of routeLines) {
        const active = record?.id === id;
        line.material.color.set(active ? colors.selected : colors[line.userData.facility.kind]);
        line.material.linewidth = active ? 4.5 : 2.5;
      }
    },
    // Keep map-width GIS paths separate from actual-size demonstration conductors.
    update(camera) {
      const detailed = records.find((r) => r.id === 'hub:power_line:3596');
      if (detailed) for (const line of lines.get(detailed.id)) line.visible = visible.transmission && camera.position.distanceTo(vector(detailed.view.target)) > 2500;
      for (const node of pickables) if (node.isPoints) node.visible = visible[node.userData.facility.layer] && camera.position.distanceTo(vector(node.userData.facility.position)) > (node.userData.facility.kind === 'wind' ? 8000 : 1500);
    },
  };
}
