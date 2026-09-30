import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {Line2} from 'three/addons/lines/Line2.js';
import {LineGeometry} from 'three/addons/lines/LineGeometry.js';
import {LineMaterial} from 'three/addons/lines/LineMaterial.js';

const colors = {wind:'#e1e8e5', transmission:'#edc476', hvdc:'#bb9def', cable:'#83d5c1', substation:'#efb0ac', pv:'#9cbef5', coast:'#91afb0', selected:'#72cbd5'};
const labels = {transmission:'송전선', hvdc:'HVDC 경로', cable:'케이블', substation:'변전소·변환소'};
const voltage = (value) => value && Number.isFinite(Number(value)) ? `${Number(value) / 1000} kV (원천)` : '미상';
const vector = (position) => new THREE.Vector3(...position);
const bucket = (map, key) => map.get(key) || map.set(key, []).get(key);

// Near-camera groundcover (data.groundcover, scene metres [x, z]): zones actual; crops, rice, grass tufts and basalt
// field walls estimated, as four InstancedMeshes rebuilt 150 ms after the orbit target moves >15 m or the distance tier
// changes. Every instance is a pure function of its world position and the seed, so nothing reshuffles; caps drop the farthest.
const T = 8, CELL = 64, BAND = 4; // generation tile (divides CELL), coarse index cell, point-in-polygon edge band (m)
const cellKey = (x, z) => Math.floor(x / CELL) * 4096 + Math.floor(z / CELL);
function polygons(list) { // list[i] = rings, outer first then holes
  const cells = new Map(), boxes = [], bands = list.map((rings, i) => {
    const map = new Map(), b = boxes[i] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const ring of rings) ring.forEach(([ax, az], k) => {
      const [bx, bz] = ring[(k + 1) % ring.length];
      b[0] = Math.min(b[0], ax); b[1] = Math.min(b[1], az); b[2] = Math.max(b[2], ax); b[3] = Math.max(b[3], az);
      for (let n = Math.floor(Math.min(az, bz) / BAND); n <= Math.floor(Math.max(az, bz) / BAND); n++) bucket(map, n).push(ax, az, bx, bz);
    });
    for (let x = Math.floor(b[0] / CELL); x <= Math.floor(b[2] / CELL); x++) for (let z = Math.floor(b[1] / CELL); z <= Math.floor(b[3] / CELL); z++) bucket(cells, x * 4096 + z).push(i);
    return map;
  });
  const inside = (i, x, z) => { // even-odd over every ring, so holes need no special case
    const e = bands[i].get(Math.floor(z / BAND)) || []; let c = false;
    for (let k = 0; k < e.length; k += 4) if ((e[k + 1] > z) !== (e[k + 3] > z) && x < e[k] + (z - e[k + 1]) * (e[k + 2] - e[k]) / (e[k + 3] - e[k + 1])) c = !c;
    return c;
  };
  const near = (x, z) => cells.get(cellKey(x, z)) || [];
  return {boxes, near, inside, at: (x, z) => near(x, z).find((i) => inside(i, x, z)) ?? -1};
}
function surface(meshes, box) { // port of build_local.surface_height: displayed terrain/sea triangles, 30 m buckets, highest where they overlap
  const S = 30, buckets = new Map();
  for (const mesh of meshes) {
    mesh.updateWorldMatrix(true, false);
    const p = mesh.geometry.attributes.position.clone().applyMatrix4(mesh.matrixWorld).array, index = mesh.geometry.index?.array;
    for (let f = 0; f < (index ? index.length : p.length / 3); f += 3) {
      const t = [0, 1, 2].flatMap((c) => { const v = (index ? index[f + c] : f + c) * 3; return [p[v], p[v + 1], p[v + 2]]; });
      const x0 = Math.min(t[0], t[3], t[6]), x1 = Math.max(t[0], t[3], t[6]), z0 = Math.min(t[2], t[5], t[8]), z1 = Math.max(t[2], t[5], t[8]);
      if (x1 < box[0] || x0 > box[2] || z1 < box[1] || z0 > box[3]) continue;
      for (let x = Math.floor(x0 / S); x <= Math.floor(x1 / S); x++) for (let z = Math.floor(z0 / S); z <= Math.floor(z1 / S); z++) bucket(buckets, x * 4096 + z).push(t);
    }
  }
  return (x, z) => {
    let y = -Infinity;
    for (const [ax, ay, az, bx, by, bz, cx, cy, cz] of buckets.get(Math.floor(x / S) * 4096 + Math.floor(z / S)) || []) {
      const d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz), u = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d, v = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d;
      if (u >= -1e-9 && v >= -1e-9 && u + v <= 1 + 1e-9) y = Math.max(y, u * ay + v * by + (1 - u - v) * cy);
    }
    return y;
  };
}
function geometry(pos, shade, up) { // shade = per-vertex grey (fake occlusion) multiplied by the instance colour
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.Float32BufferAttribute(shade.flatMap((s) => [s, s, s]), 3));
  if (up) g.setAttribute('normal', new THREE.Float32BufferAttribute(pos.map((_, i) => +(i % 3 === 1)), 3)); else g.computeVertexNormals();
  return g;
}
function tuft(n, h, lean, w) { // n one-triangle blades, both windings so both sides light like the ground (up normals)
  const pos = [], shade = [];
  for (let k = 0; k < n; k++) {
    const a = (k / n + .1) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a), l = [-s * w, 0, c * w], r = [s * w, 0, -c * w], tip = [c * lean, h * (1 - .15 * (k % 2)), s * lean];
    pos.push(...l, ...r, ...tip, ...r, ...l, ...tip); shade.push(.35, .35, 1, .35, .35, 1);
  }
  return geometry(pos, shade, true);
}
function clump() { // six diamond leaves in a rosette, 12 triangles, top faces wound upward
  const pos = [], shade = [];
  for (let k = 0; k < 6; k++) {
    const a = k * Math.PI / 3 + (k % 2) * .3, c = Math.cos(a), s = Math.sin(a), L = k % 2 ? .3 : .24, w = .08, m = [c * L * .5, .2, s * L * .5];
    const tip = [c * L, k % 2 ? .12 : .22, s * L], l = [m[0] - s * w, m[1], m[2] + c * w], r = [m[0] + s * w, m[1], m[2] - c * w];
    pos.push(0, .02, 0, ...l, ...tip, 0, .02, 0, ...tip, ...r); shade.push(.7, .9, 1, .7, 1, .9);
  }
  return geometry(pos, shade, false);
}
function stones() { // ~1 m of dry-stone wall: a wide base stone and two smaller, turned stones on top (36 triangles)
  const pos = [], shade = [];
  for (const [sx, sy, sz, x, y, r] of [[1, .5, 1, 0, 0, 0], [.55, .45, .8, -.2, .5, .25], [.45, .38, .75, .27, .5, -.2]]) {
    const p = new THREE.BoxGeometry(sx, sy, sz).toNonIndexed().rotateY(r).translate(x, y + sy / 2, 0).attributes.position.array;
    pos.push(...p); for (let i = 1; i < p.length; i += 3) shade.push(p[i] < .05 ? .65 : 1);
  }
  return geometry(pos, shade, false);
}
export function groundcover(gc, surfaces, redraw) {
  const seed = gc.seed | 0, crops = new Set(['field', 'other_crop']);
  const hash = (a, b, c) => { let h = Math.imul(a ^ seed, 0x27d4eb2d) ^ Math.imul(b ^ 0x5bd1e995, 0x165667b1) ^ Math.imul(c, 0x9e3779b1); h = Math.imul(h ^ h >>> 15, 0x85ebca6b); h = Math.imul(h ^ h >>> 13, 0xc2b2ae35); return ((h ^ h >>> 16) >>> 0) / 4294967296; };
  const zones = polygons(gc.zones.map((z) => z.rings)), zoneClass = (x, z) => gc.zones[zones.at(x, z)]?.class;
  // row_angle_deg: row direction atan2(dz, dx) in scene x/z. Parcels are ignored unless row_source is cadastral.
  const parcels = gc.row_source === 'cadastral' ? gc.parcels || [] : [], parcel = polygons(parcels.map((p) => [p.ring]));
  const segs = [], wallCells = new Map();
  for (const line of gc.walls || []) for (let k = 1; k < line.length; k++) {
    const [ax, az] = line[k - 1], [bx, bz] = line[k], s = segs.push([ax, az, bx, bz]) - 1;
    for (let x = Math.floor(Math.min(ax, bx) / CELL); x <= Math.floor(Math.max(ax, bx) / CELL); x++) for (let z = Math.floor(Math.min(az, bz) / CELL); z <= Math.floor(Math.max(az, bz) / CELL); z++) bucket(wallCells, x * 4096 + z).push(s);
  }
  const box = zones.boxes.reduce((a, b) => [Math.min(a[0], b[0] - 300), Math.min(a[1], b[1] - 300), Math.max(a[2], b[2] + 300), Math.max(a[3], b[3] + 300)], [Infinity, Infinity, -Infinity, -Infinity]);
  const pal = (...hex) => hex.map((h) => new THREE.Color(h)), tint = (list, h, v) => { const c = list[Math.floor(h * list.length)]; return [c.r * v, c.g * v, c.b * v]; };
  const colors = {crop:pal('#4c7a2c', '#5b8a34', '#6a9440', '#447030', '#5f8f3a'), rice:pal('#b3a650', '#c2ae55', '#9ca047'), grass:pal('#6f8a3c', '#83934a', '#a09a5a'), wall:pal('#6a6760', '#5a5854', '#77726a', '#4f4d49')};
  // name: [geometry, side, cap, radius m]
  const kinds = {crop:[clump(), THREE.DoubleSide, 15000, 50], rice:[tuft(3, 1, .22, .03), THREE.FrontSide, 25000, 50], grass:[tuft(3, .45, .15, .06), THREE.FrontSide, 20000, 80], wall:[stones(), THREE.FrontSide, 20000, 250]};
  const meshes = Object.fromEntries(Object.entries(kinds).map(([name, [g, side, cap]]) => {
    const m = new THREE.InstancedMesh(g, new THREE.MeshLambertMaterial({vertexColors:true, side}), cap);
    m.name = `groundcover_${name}`; m.count = 0; m.visible = false; m.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(cap * 3), 3);
    return [name, m];
  }));
  const group = new THREE.Group(); group.name = 'groundcover'; group.add(...Object.values(meshes));
  const tiles = (tx, tz, R) => { // nearest first, so a type stops once its cap is full
    const list = [];
    for (let x0 = Math.floor((tx - R) / T) * T; x0 < tx + R; x0 += T) for (let z0 = Math.floor((tz - R) / T) * T; z0 < tz + R; z0 += T) {
      const d = Math.hypot(Math.max(x0 - tx, 0, tx - x0 - T), Math.max(z0 - tz, 0, tz - z0 - T)); if (d < R) list.push([d, x0, z0]);
    }
    return list.sort((a, b) => a[0] - b[0]);
  };
  const full = (kind, out) => out[kind].length >= kinds[kind][2]; // ponytail: tile-granular cap edge, hidden by the fade in write()
  function plants(tx, tz, out) { // row lattices anchored at the scene origin, rotated per parcel (else per 50 m cell)
    for (const [, x0, z0] of tiles(tx, tz, 50)) {
      const near = zones.near(x0, z0).map((i) => gc.zones[i].class);
      for (const [kind, du, dv, ok] of [['crop', .5, .8, (c) => crops.has(c)], ['rice', .4, .35, (c) => c === 'paddy']]) {
        if (!near.some(ok) || full(kind, out)) continue;
        const touching = parcel.near(x0, z0).filter((i) => { const b = parcel.boxes[i]; return b[0] < x0 + T + 1 && b[2] > x0 - 1 && b[1] < z0 + T + 1 && b[3] > z0 - 1; });
        const groups = touching.map((i) => ({id:i, a:parcels[i].row_angle_deg, inner:(x, z) => parcel.inside(i, x, z)}));
        for (let cx = Math.floor(x0 / 50); cx <= Math.floor((x0 + T - 1e-6) / 50); cx++) for (let cz = Math.floor(z0 / 50); cz <= Math.floor((z0 + T - 1e-6) / 50); cz++)
          // ponytail: rows turn at 50 m cell lines through one field when there is no parcel; cadastral rows fix it.
          groups.push({id:-1 - (cx + 2048) * 4096 - cz - 2048, a:hash(cx, cz, 1) * 180, cell:[cx, cz], inner:(x, z) => !touching.some((i) => parcel.inside(i, x, z))});
        for (const g of groups) {
          const a = g.a * Math.PI / 180, c = Math.cos(a), s = Math.sin(a), us = [], vs = [], look = hash(g.id, 7, 7), grow = .75 + .5 * hash(g.id, 8, 8);
          for (const [x, z] of [[x0, z0], [x0 + T, z0], [x0, z0 + T], [x0 + T, z0 + T]]) { us.push(x * c + z * s); vs.push(z * c - x * s); }
          const good = (x, z) => g.inner(x, z) && ok(zoneClass(x, z));
          for (let i = Math.ceil(Math.min(...us) / du); i * du <= Math.max(...us); i++) for (let j = Math.ceil(Math.min(...vs) / dv); j * dv <= Math.max(...vs); j++) {
            const x = i * du * c - j * dv * s, z = i * du * s + j * dv * c, d = Math.sqrt((x - tx) ** 2 + (z - tz) ** 2);
            if (x < x0 || x >= x0 + T || z < z0 || z >= z0 + T || d >= 50 || (g.cell && (Math.floor(x / 50) !== g.cell[0] || Math.floor(z / 50) !== g.cell[1])) || !good(x, z)) continue;
            const h = (k) => hash(i, j, g.id * 8 + k), jx = x + (h(1) - .5) * .1, jz = z + (h(2) - .5) * .1;
            // margin: grass instead of a crop. ponytail: field-margin grass only exists inside this 50 m pass and stops with its cap, not out to grass's 80 m.
            if ([[.6, 0], [-.6, 0], [0, .6], [0, -.6]].some(([ox, oz]) => !good(x + ox, z + oz))) {
              const sc = .7 + .6 * h(3); out.grass.push([d, jx, jz, h(4) * 6.28, sc, sc, sc, ...tint(colors.grass, h(5), .85 + .3 * h(6))]);
            } else if (kind === 'crop') { const sc = grow * (.85 + .5 * h(3)); out.crop.push([d, jx, jz, h(4) * 6.28, sc, sc * (.8 + .4 * h(6)), sc, ...tint(colors.crop, look, .8 + .4 * h(5))]); }
            else { const sc = .85 + .3 * h(3); out.rice.push([d, jx, jz, h(4) * 6.28, sc, sc, sc, ...tint(colors.rice, (look + .3 * h(5)) % 1, .85 + .3 * h(6))]); }
          }
        }
      }
    }
  }
  function grass(tx, tz, out) { // jittered 0.7 m grid; a point belongs to the tile holding its unjittered node
    for (const [, x0, z0] of tiles(tx, tz, 80)) {
      if (full('grass', out) || !zones.near(x0, z0).some((i) => gc.zones[i].class === 'grass')) continue;
      for (let i = Math.ceil(x0 / .7); i * .7 < x0 + T; i++) for (let j = Math.ceil(z0 / .7); j * .7 < z0 + T; j++) {
        const h = (k) => hash(i, j, k), x = (i + h(1) - .5) * .7, z = (j + h(2) - .5) * .7, d = Math.hypot(x - tx, z - tz);
        if (d >= 80 || zoneClass(x, z) !== 'grass') continue;
        const sc = .6 + .8 * h(3); out.grass.push([d, x, z, h(4) * 6.28, sc, sc * (.7 + .6 * h(6)), sc, ...tint(colors.grass, h(5), .8 + .4 * h(7))]);
      }
    }
  }
  function walls(tx, tz, out) { // ~1 m blocks along each segment, positions fixed by the segment alone
    const seen = new Set();
    for (let x = Math.floor((tx - 250) / CELL); x <= Math.floor((tx + 250) / CELL); x++) for (let z = Math.floor((tz - 250) / CELL); z <= Math.floor((tz + 250) / CELL); z++) for (const s of wallCells.get(x * 4096 + z) || []) {
      if (seen.has(s)) continue; seen.add(s);
      const [ax, az, bx, bz] = segs[s], L = Math.hypot(bx - ax, bz - az), n = Math.max(1, Math.round(L)), yaw = Math.atan2(az - bz, bx - ax);
      for (let k = 0; k < n; k++) {
        const px = ax + (bx - ax) * (k + .5) / n, pz = az + (bz - az) * (k + .5) / n, d = Math.hypot(px - tx, pz - tz), h = (i) => hash(s, k, i);
        if (d < 250) out.wall.push([d, px, pz, yaw + (h(1) - .5) * .12 + (h(7) > .5) * Math.PI, L / n * (.96 + .08 * h(2)), .85 + .4 * h(3), .4 + .1 * h(4), ...tint(colors.wall, h(5), .8 + .4 * h(6)), .15]);
      }
    }
  }
  let height, last = {tier:-1}, timer = 0;
  function write(m, list, R) { // reuse the capped buffers; items are [distance, x, z, yaw, sx, sy, sz, r, g, b, sink]
    if (list.length > m.instanceMatrix.count) R = list.sort((a, b) => a[0] - b[0])[(list.length = m.instanceMatrix.count) - 1][0];
    let n = 0;
    for (const [d, x, z, yaw, sx, sy, sz, r, g, b, sink = .03] of list) {
      const y = height(x, z), f = Math.min(1, (R - d) / (R * .3)), c = Math.cos(yaw) * f, s = Math.sin(yaw) * f; // shrink toward the (cap) radius, no hard edge
      if (y === -Infinity) continue;
      m.instanceMatrix.array.set([c * sx, 0, -s * sx, 0, 0, sy * f, 0, 0, s * sz, 0, c * sz, 0, x, y - sink, z, 1], n * 16); m.instanceColor.array.set([r, g, b], n++ * 3);
    }
    m.count = n; m.visible = n > 0; m.instanceMatrix.needsUpdate = m.instanceColor.needsUpdate = true; m.computeBoundingSphere();
  }
  // ponytail: one synchronous 25-130 ms task once the camera settles (first call also indexes the terrain); split per type over idle callbacks if the hitch shows.
  function regenerate(tx, tz, tier) {
    const start = performance.now(), out = {crop:[], rice:[], grass:[], wall:[]};
    height ||= surface(surfaces, box); last = {x:tx, z:tz, tier};
    if (tier === 0) { plants(tx, tz, out); grass(tx, tz, out); }
    if (tier < 2) walls(tx, tz, out);
    for (const name in out) write(meshes[name], out[name], kinds[name][3]);
    performance.measure('groundcover', {start, detail:Object.fromEntries(Object.entries(meshes).map(([k, m]) => [k, m.count]))});
    redraw();
  }
  return {group, update(camera, target) {
    const d = camera.position.distanceTo(target), tier = d < 180 ? 0 : d < 600 ? 1 : 2; // plants below 180 m, walls to 600 m
    if (tier === 2) for (const name in meshes) meshes[name].visible = false;
    if (tier === last.tier && (tier === 2 || Math.hypot(target.x - last.x, target.z - last.z) < 15)) return;
    clearTimeout(timer); timer = setTimeout(() => regenerate(target.x, target.z, tier), 150);
  }};
}

export async function loadGrid(redraw = () => {}) {
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
  for (const layer of ['terrain', 'buildings', 'sea', 'roads', 'vegetation', 'groundcover']) layers.set(layer, []);
  const surfaces = [];
  const buildings = new Map((data.buildings?.records || []).map((b) => [b.node, b]));
  const heightBasis = {provider:'원천 높이', floors_estimated:'층수×3 m 추정', default_estimated:'속성 없음 · 1층 3.5 m 추정'};
  const roofBasis = {gable_house:'박공 20° 추정', gable_shed:'박공 10° 추정', flat_parapet:'평지붕·난간 추정'};
  gltf.scene.traverse((node) => {
    if (!node.isMesh) return;
    if (node.material?.map) node.material.map.anisotropy = 8; // sharper low-angle imagery; WebGL clamps to the device maximum
    const layer = node.name.startsWith('street_') ? 'roads' : node.name.startsWith('vegetation_') ? 'vegetation' : node.name.startsWith('groundcover_') ? 'groundcover' : /^(building|landmark)_/.test(node.name) ? 'buildings' : node.name.startsWith('ocean_surface') ? 'sea' : /terrain|shore|landcover|road/.test(node.name) ? 'terrain' : null;
    if (layer) layers.get(layer).push(node);
    if (/^(terrain_landcover_|ocean_surface)/.test(node.name)) surfaces.push(node);
    const b = layer === 'buildings' && buildings.get(node.parent?.name);
    if (!b) return;
    // Clicking a building shows its per-building actual/estimated basis in the existing detail list.
    node.userData.facility = b.record ||= {id:b.id, kind:'building', layer:'buildings', name:`건물 ${b.id.split('.').at(-1)}`, position:b.position,
      view:{position:vector(b.position).add(new THREE.Vector3(-35, 30, 45)).toArray(), target:vector(b.position).add(new THREE.Vector3(0, b.walls_m / 2, 0)).toArray(), fov:48},
      rows:[['원천 ID · 위치·윤곽', `${b.id} · 실제`], ['벽 높이 근거', `${b.walls_m} m · ${heightBasis[b.height_status]}`],
        ['지붕', `${roofBasis[b.roof_rule]} · ${b.roof_texture === 'vworld_z19' ? 'VWorld z19 영상 실제' : '영상 없음(회색)'}`], ['외벽·창', '절차적 추정']]};
    pickables.push(node);
  });
  const visible = {wind:true, transmission:true, substation:true, terrain:true, pv:true, buildings:true, sea:true, roads:true, vegetation:true};
  const cover = data.groundcover && groundcover(data.groundcover, surfaces, redraw);
  if (cover) { object.add(cover.group); layers.get('groundcover').push(cover.group); visible.groundcover = true; }
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
    update(camera, target) {
      if (cover && visible.groundcover && target) cover.update(camera, target);
      const detailed = records.find((r) => r.id === 'hub:power_line:3596');
      if (detailed) for (const line of lines.get(detailed.id)) line.visible = visible.transmission && camera.position.distanceTo(vector(detailed.view.target)) > 2500;
      for (const node of pickables) if (node.isPoints) node.visible = visible[node.userData.facility.layer] && camera.position.distanceTo(vector(node.userData.facility.position)) > (node.userData.facility.kind === 'wind' ? 8000 : 1500);
    },
  };
}
