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

// Near-camera green layers (scene metres [x, z]): zones actual; conifer and broadleaf trees, citrus rows, greenhouse tunnels, crops,
// rice, grass tufts and basalt field walls estimated, as eight InstancedMeshes (one draw call each) rebuilt 150 ms after the orbit
// target moves or the distance tier changes. Zones come from the /local/green/ 1 km tiles (index.json once, tiles on demand into an LRU)
// or else the manifest groundcover block. Every instance is a pure function of its world position and the seed, and every candidate
// point is tested only against the tile that holds it, so tile borders neither double nor reshuffle anything; caps drop the farthest.
const T = 8, CELL = 64, BAND = 4, LRU = 40; // generation tile (divides CELL), coarse index cell, point-in-polygon edge band (m), cached tiles
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
const indexed = new WeakMap(); // one lazy surface index per mesh list, shared by every caller
function surface(meshes) { // port of build_local.surface_height: displayed terrain/sea triangles, highest where they overlap; 30 m buckets built per 480 m block on first use
  if (indexed.has(meshes)) return indexed.get(meshes);
  const S = 30, B = 480, blocks = new Map(), fine = new Map();
  for (const mesh of meshes) {
    mesh.updateWorldMatrix(true, false);
    const p = mesh.geometry.attributes.position.clone().applyMatrix4(mesh.matrixWorld).array, index = mesh.geometry.index?.array;
    for (let f = 0; f < (index ? index.length : p.length / 3); f += 3) {
      const t = [0, 1, 2].flatMap((c) => { const v = (index ? index[f + c] : f + c) * 3; return [p[v], p[v + 1], p[v + 2]]; });
      for (let x = Math.floor(Math.min(t[0], t[3], t[6]) / B); x <= Math.floor(Math.max(t[0], t[3], t[6]) / B); x++) for (let z = Math.floor(Math.min(t[2], t[5], t[8]) / B); z <= Math.floor(Math.max(t[2], t[5], t[8]) / B); z++) bucket(blocks, x * 4096 + z).push(t);
    }
  }
  const index = (bx, bz) => { // one block's triangles into its 30 m buckets
    const map = new Map(), clamp = (v, o) => Math.min(Math.max(v, o * B / S), (o + 1) * B / S - 1);
    for (const t of blocks.get(bx * 4096 + bz) || []) for (let x = clamp(Math.floor(Math.min(t[0], t[3], t[6]) / S), bx); x <= clamp(Math.floor(Math.max(t[0], t[3], t[6]) / S), bx); x++)
      for (let z = clamp(Math.floor(Math.min(t[2], t[5], t[8]) / S), bz); z <= clamp(Math.floor(Math.max(t[2], t[5], t[8]) / S), bz); z++) bucket(map, x * 4096 + z).push(t);
    return map;
  };
  return indexed.set(meshes, (x, z) => {
    const bx = Math.floor(x / B), bz = Math.floor(z / B), map = fine.get(bx * 4096 + bz) || fine.set(bx * 4096 + bz, index(bx, bz)).get(bx * 4096 + bz);
    let y = -Infinity;
    for (const [ax, ay, az, bx, by, bz, cx, cy, cz] of map.get(Math.floor(x / S) * 4096 + Math.floor(z / S)) || []) {
      const d = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz), u = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / d, v = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / d;
      if (u >= -1e-9 && v >= -1e-9 && u + v <= 1 + 1e-9) y = Math.max(y, u * ay + v * by + (1 - u - v) * cy);
    }
    return y;
  }).get(meshes);
}
function geometry(pos, shade, normal) { // shade = per-vertex grey (fake occlusion) multiplied by the instance colour; normal: true = up, array, else computed
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.Float32BufferAttribute(shade.flatMap((s) => [s, s, s]), 3));
  if (normal === true) g.setAttribute('normal', new THREE.Float32BufferAttribute(pos.map((_, i) => +(i % 3 === 1)), 3)); else if (normal) g.setAttribute('normal', new THREE.Float32BufferAttribute(normal, 3)); else g.computeVertexNormals();
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
function solid(...parts) { // [three.js geometry, shade(y)] parts merged, keeping each part's own (smooth) normals
  const pos = [], normal = [], shade = [];
  for (const [g, s] of parts) { const n = g.index ? g.toNonIndexed() : g, p = n.attributes.position.array; pos.push(...p); normal.push(...n.attributes.normal.array); for (let i = 1; i < p.length; i += 3) shade.push(s(p[i])); }
  return geometry(pos, shade, normal);
}
const trunk = (r0, r1, h) => [new THREE.CylinderGeometry(r1, r0, h, 4, 1, true).translate(0, h / 2, 0), () => .45];
const round = (sx, sy, sz, y) => { const g = new THREE.DodecahedronGeometry(1, 0); g.setAttribute('normal', g.attributes.position.clone()); return g.scale(sx, sy, sz).translate(0, y, 0); }; // sphere normals: smooth crown
// vegetation.py's unit-height templates: two-tier 8-sided cone (24 triangles), round broadleaf crown (44), low citrus crown (36)
const conifer = () => solid(trunk(.025, .02, .32), [new THREE.ConeGeometry(.25, .54, 8, 1, true).translate(0, .45, 0), (y) => .55 + .5 * y], [new THREE.ConeGeometry(.18, .55, 8, 1, true).rotateY(Math.PI / 8).translate(0, .725, 0), (y) => .55 + .5 * y]);
const broadleaf = () => solid(trunk(.03, .025, .45), [round(.45, .33, .45, .67), (y) => .5 + .5 * y]); // crown 15 % wider than vegetation.py's: closed canopy at 1 tree / 64 m2
const citrus = () => solid([round(.55, .5, .55, .5), (y) => .6 + .4 * y]);
function tunnel() { // unit-length (x -0.5..0.5) 7 m semicircular film tunnel, 12-segment arch with end walls (46 triangles)
  const pos = [], normal = [], arc = (k) => [3.5 * Math.sin(k * Math.PI / 12), 3.5 * Math.cos(k * Math.PI / 12)]; // [y, z]
  for (let k = 0; k < 12; k++) {
    const [y0, z0] = arc(k), [y1, z1] = arc(k + 1), n0 = [0, y0 / 3.5, z0 / 3.5], n1 = [0, y1 / 3.5, z1 / 3.5];
    pos.push(-.5, y0, z0, .5, y0, z0, .5, y1, z1, -.5, y0, z0, .5, y1, z1, -.5, y1, z1); normal.push(...n0, ...n0, ...n1, ...n0, ...n1, ...n1);
    if (k) for (const x of [-.5, .5]) { const [ya, za] = arc(k + (x > 0)), [yb, zb] = arc(k + (x < 0)); pos.push(x, 0, 3.5, x, ya, za, x, yb, zb); normal.push(x * 2, 0, 0, x * 2, 0, 0, x * 2, 0, 0); }
  }
  const shade = []; for (let i = 1; i < pos.length; i += 3) shade.push(.85 + .15 * pos[i] / 3.5);
  return geometry(pos, shade, normal);
}
const idHash = (text) => { let h = 0x811c9dc5; for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193); return (h >>> 0) % 0x3fffff; }; // FNV-1a
function tile(d, cadastral) { // one tile's (or the whole manifest block's) zones, parcels and wall segments, indexed
  const list = d.zones || [], parcels = cadastral ? d.parcels || [] : [], t = {list, zones:polygons(list.map((z) => z.rings)), parcels, parcel:polygons(parcels.map((p) => [p.ring])),
    byId:new Map(), houses:list.flatMap((z, i) => z.class === 'greenhouse' ? [i] : []), segs:[], wallCells:new Map()};
  parcels.forEach((p, i) => bucket(t.byId, p.id).push(i));
  for (const line of d.walls || []) for (let k = 1; k < line.length; k++) { // segment id from its own 0.1 m endpoints, not its index in this tile
    const [ax, az] = line[k - 1], [bx, bz] = line[k], id = (Math.imul(Math.round(ax * 10), 0x2545f491) ^ Math.imul(Math.round(az * 10), 0x9e3779b1) ^ Math.imul(Math.round(bx * 10), 0x85ebca6b) ^ Math.imul(Math.round(bz * 10), 0x27d4eb2d)) >>> 8;
    const s = t.segs.push([ax, az, bx, bz, id]) - 1;
    for (let x = Math.floor(Math.min(ax, bx) / CELL); x <= Math.floor(Math.max(ax, bx) / CELL); x++) for (let z = Math.floor(Math.min(az, bz) / CELL); z <= Math.floor(Math.max(az, bz) / CELL); z++) bucket(t.wallCells, x * 4096 + z).push(s);
  }
  return t;
}
function tiled(index, cadastral, arrived) { // /local/green/ tiles fetched on demand; at() answers only from the tile that holds the point
  const S = index.tile_size_m, [X0, Z0] = index.tiles[0]?.bounds || [0, 0], listed = new Map(), cache = new Map(); let want = new Set(); // tile grid origin: a tile corner, not index.bounds (the scene bbox)
  for (const t of index.tiles) listed.set(Math.round((t.bounds[0] - X0) / S) * 4096 + Math.round((t.bounds[1] - Z0) / S), t);
  const get = (k) => cache.get(k)?.tile || null, range = (a, b, o) => [Math.floor((a - o) / S), Math.floor((b - o) / S)];
  return {
    at: (x, z) => get(Math.floor((x - X0) / S) * 4096 + Math.floor((z - Z0) / S)),
    within(x0, z0, x1, z1) { const out = [], [i0, i1] = range(x0, x1, X0), [j0, j1] = range(z0, z1, Z0); for (let i = i0; i <= i1; i++) for (let j = j0; j <= j1; j++) { const t = get(i * 4096 + j); if (t) out.push(t); } return out; },
    request(tx, tz, R) { // listed tiles within R: fetch the missing, touch the cached, evict the least recently used beyond LRU
      const now = performance.now(), [i0, i1] = range(tx - R, tx + R, X0), [j0, j1] = range(tz - R, tz + R, Z0); want = new Set();
      for (let i = i0; i <= i1; i++) for (let j = j0; j <= j1; j++) { const x0 = X0 + i * S, z0 = Z0 + j * S; if (listed.has(i * 4096 + j) && Math.hypot(Math.max(x0 - tx, 0, tx - x0 - S), Math.max(z0 - tz, 0, tz - z0 - S)) < R) want.add(i * 4096 + j); }
      for (const k of want) {
        let e = cache.get(k); cache.delete(k);
        if (!e || e.failed < now - 30000) { // ponytail: a failed tile is retried 30 s later, on the next regeneration that wants it
          const entry = e = {};
          fetch(`/local/green/${encodeURIComponent(listed.get(k).id)}.json`, {signal:AbortSignal.timeout(20000)}).then((r) => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
            .then((d) => { entry.tile = tile(d, cadastral); }).then(() => { if (cache.get(k) === entry && want.has(k)) arrived(); }, () => { entry.failed = performance.now(); });
          // Sequence guard: a reply only lands in the entry it was fetched for, and only a tile the latest request still wants triggers a
          // regeneration, which always runs at the latest target; tiles are position-free, so an old camera's reply cannot overwrite a newer view.
        }
        cache.set(k, e);
      }
      for (const k of cache.keys()) if (cache.size > LRU && !want.has(k)) cache.delete(k);
    },
  };
}
export function groundcover(gc, surfaces, redraw) { // gc: the manifest groundcover block, or {index} from /local/green/index.json
  const seed = (gc.index || gc).seed | 0, crops = new Set(['field', 'other_crop']), woods = new Set(['conifer', 'broadleaf']), cadastral = (gc.index || gc).row_source === 'cadastral';
  const hash = (a, b, c) => { let h = Math.imul(a ^ seed, 0x27d4eb2d) ^ Math.imul(b ^ 0x5bd1e995, 0x165667b1) ^ Math.imul(c, 0x9e3779b1); h = Math.imul(h ^ h >>> 15, 0x85ebca6b); h = Math.imul(h ^ h >>> 13, 0xc2b2ae35); return ((h ^ h >>> 16) >>> 0) / 4294967296; };
  let dirty = false;
  const one = !gc.index && tile(gc, cadastral), src = one ? {at:() => one, within:() => [one], request() {}} : tiled(gc.index, cadastral, () => { dirty = true; redraw(); });
  const zoneAt = (x, z) => { const t = src.at(x, z), i = t ? t.zones.at(x, z) : -1; return i < 0 ? null : t.list[i]; }, zoneClass = (x, z) => zoneAt(x, z)?.class;
  // row_angle_deg: row direction atan2(dz, dx) in scene x/z. Parcels are ignored unless row_source is cadastral.
  const inParcel = (id, x, z) => { const t = src.at(x, z); return !!t && (t.byId.get(id) || []).some((i) => t.parcel.inside(i, x, z)); };
  const pal = (...hex) => hex.map((h) => new THREE.Color(h)), tint = (list, h, v) => { const c = list[Math.floor(h * list.length)]; return [c.r * v, c.g * v, c.b * v]; };
  const colors = {crop:pal('#4c7a2c', '#5b8a34', '#6a9440', '#447030', '#5f8f3a'), rice:pal('#b3a650', '#c2ae55', '#9ca047'), grass:pal('#6f8a3c', '#83934a', '#a09a5a'), wall:pal('#6a6760', '#5a5854', '#77726a', '#4f4d49'),
    conifer:pal('#3a5d3a', '#44683f', '#325333'), broadleaf:pal('#5f7f40', '#6d8c48', '#54743b'), citrus:pal('#3f7434', '#4b7f39'), greenhouse:pal('#ecf1ee', '#e2e9e6')}; // vegetation.py hues, lifted for lit sides
  const film = {side:THREE.DoubleSide, transparent:true, opacity:.6, depthWrite:false}, both = {side:THREE.DoubleSide};
  // name: [geometry, material, cap, radius m, deepest tier]; tiers by camera-target distance: 0 < 180 m, 1 < 600 m, 2 < 2 km, 3 beyond
  const kinds = {crop:[clump(), both, 15000, 50, 0], rice:[tuft(3, 1, .22, .03), {}, 25000, 50, 0], grass:[tuft(3, .45, .15, .06), {}, 20000, 80, 0], wall:[stones(), {}, 20000, 250, 1],
    conifer:[conifer(), both, 12000, 400, 2], broadleaf:[broadleaf(), both, 6000, 400, 2], citrus:[citrus(), {}, 10000, 250, 2], greenhouse:[tunnel(), film, 2000, 400, 2]};
  // Tier-2 types shrink about their base from 800 m camera distance to nothing at 2 km, so the tier cut never pops.
  // ponytail: from ~0.8-1.5 km the 400 m disc still reads as an island of 3D trees on the imagery; add a far LOD (impostors) if that matters.
  const fade = {value:1};
  const shrink = (s) => { s.uniforms.fade = fade; s.vertexShader = `uniform float fade;\n${s.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\ntransformed *= fade;')}`; };
  const meshes = Object.fromEntries(Object.entries(kinds).map(([name, [g, options, cap, , tier]]) => {
    const m = new THREE.InstancedMesh(g, new THREE.MeshLambertMaterial({vertexColors:true, ...options}), cap);
    if (tier === 2) m.material.onBeforeCompile = shrink;
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
  const near = (x0, z0) => src.within(x0, z0, x0 + T, z0 + T).flatMap((t) => t.zones.near(x0, z0).map((i) => t.list[i])); // zones around one 8 m tile
  const span = (x0, z0, c, s) => { const us = [], vs = []; for (const [x, z] of [[x0, z0], [x0 + T, z0], [x0, z0 + T], [x0 + T, z0 + T]]) { us.push(x * c + z * s); vs.push(z * c - x * s); } return [Math.min(...us), Math.max(...us), Math.min(...vs), Math.max(...vs)]; };
  const full = (kind, out) => out[kind].length >= kinds[kind][2]; // ponytail: tile-granular cap edge, hidden by the fade in write()
  function plants(tx, tz, out) { // row lattices anchored at the scene origin, rotated per parcel (else per 50 m cell)
    for (const [, x0, z0] of tiles(tx, tz, 50)) {
      const classes = near(x0, z0).map((z) => z.class);
      for (const [kind, du, dv, ok] of [['crop', .5, .8, (c) => crops.has(c)], ['rice', .4, .35, (c) => c === 'paddy']]) {
        if (!classes.some(ok) || full(kind, out)) continue;
        const ids = new Map(); // parcel id -> row angle, over every tile touching this 8 m tile
        for (const t of src.within(x0, z0, x0 + T, z0 + T)) for (const i of t.parcel.near(x0, z0)) { const b = t.parcel.boxes[i]; if (b[0] < x0 + T + 1 && b[2] > x0 - 1 && b[1] < z0 + T + 1 && b[3] > z0 - 1) ids.set(t.parcels[i].id, t.parcels[i].row_angle_deg); }
        const groups = [...ids].map(([id, a]) => ({id:idHash(String(id)), a, inner:(x, z) => inParcel(id, x, z)}));
        for (let cx = Math.floor(x0 / 50); cx <= Math.floor((x0 + T - 1e-6) / 50); cx++) for (let cz = Math.floor(z0 / 50); cz <= Math.floor((z0 + T - 1e-6) / 50); cz++)
          // ponytail: rows turn at 50 m cell lines through one field when there is no parcel; cadastral rows fix it.
          groups.push({id:-1 - (cx + 2048) * 4096 - cz - 2048, a:hash(cx, cz, 1) * 180, cell:[cx, cz], inner:(x, z) => ![...ids.keys()].some((id) => inParcel(id, x, z))});
        for (const g of groups) {
          const a = g.a * Math.PI / 180, c = Math.cos(a), s = Math.sin(a), look = hash(g.id, 7, 7), grow = .75 + .5 * hash(g.id, 8, 8), [u0, u1, v0, v1] = span(x0, z0, c, s);
          const good = (x, z) => g.inner(x, z) && ok(zoneClass(x, z));
          for (let i = Math.ceil(u0 / du); i * du <= u1; i++) for (let j = Math.ceil(v0 / dv); j * dv <= v1; j++) {
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
      if (full('grass', out) || !near(x0, z0).some((z) => z.class === 'grass')) continue;
      for (let i = Math.ceil(x0 / .7); i * .7 < x0 + T; i++) for (let j = Math.ceil(z0 / .7); j * .7 < z0 + T; j++) {
        const h = (k) => hash(i, j, k), x = (i + h(1) - .5) * .7, z = (j + h(2) - .5) * .7, d = Math.hypot(x - tx, z - tz);
        if (d >= 80 || zoneClass(x, z) !== 'grass') continue;
        const sc = .6 + .8 * h(3); out.grass.push([d, x, z, h(4) * 6.28, sc, sc * (.7 + .6 * h(6)), sc, ...tint(colors.grass, h(5), .8 + .4 * h(7))]);
      }
    }
  }
  function walls(tx, tz, out) { // ~1 m blocks along each segment, positions fixed by the segment alone; a block belongs to the tile that holds it
    for (const t of src.within(tx - 250, tz - 250, tx + 250, tz + 250)) {
      const seen = new Set();
      for (let x = Math.floor((tx - 250) / CELL); x <= Math.floor((tx + 250) / CELL); x++) for (let z = Math.floor((tz - 250) / CELL); z <= Math.floor((tz + 250) / CELL); z++) for (const s of t.wallCells.get(x * 4096 + z) || []) {
        if (seen.has(s)) continue; seen.add(s);
        const [ax, az, bx, bz, id] = t.segs[s], L = Math.hypot(bx - ax, bz - az), n = Math.max(1, Math.round(L)), yaw = Math.atan2(az - bz, bx - ax);
        for (let k = 0; k < n; k++) {
          const px = ax + (bx - ax) * (k + .5) / n, pz = az + (bz - az) * (k + .5) / n, d = Math.hypot(px - tx, pz - tz), h = (i) => hash(id, k, i);
          if (d < 250 && src.at(px, pz) === t) out.wall.push([d, px, pz, yaw + (h(1) - .5) * .12 + (h(7) > .5) * Math.PI, L / n * (.96 + .08 * h(2)), .85 + .4 * h(3), .4 + .1 * h(4), ...tint(colors.wall, h(5), .8 + .4 * h(6)), .15]);
        }
      }
    }
  }
  const G = 6, cand = (i, j) => [(i + .5 + (hash(i, j, 11) - .5) * .8) * G, (j + .5 + (hash(i, j, 12) - .5) * .8) * G, hash(i, j, 13)];
  function forest(tx, tz, R, out) { // one candidate per world 6 m cell (80 % jitter); it stands unless a neighbour within 5 m ranks higher (Matérn II): ~1 tree / 64 m2, >= 5 m apart
    const blocks = new Map(), wooded = (x, z) => { // 64 m block with any forest zone: skip the zone test elsewhere
      const k = cellKey(x, z), x0 = Math.floor(x / CELL) * CELL, z0 = Math.floor(z / CELL) * CELL;
      if (!blocks.has(k)) blocks.set(k, src.within(x0, z0, x0 + CELL, z0 + CELL).some((t) => t.zones.near(x0, z0).some((i) => woods.has(t.list[i].class))));
      return blocks.get(k);
    };
    for (let i = Math.floor((tx - R) / G); i * G < tx + R; i++) for (let j = Math.floor((tz - R) / G); j * G < tz + R; j++) {
      const [x, z, p] = cand(i, j), d = Math.hypot(x - tx, z - tz);
      if (d >= R || !wooded(x, z)) continue;
      let free = true;
      for (let a = -1; a <= 1 && free; a++) for (let b = -1; b <= 1 && free; b++) if (a || b) { const [nx, nz, q] = cand(i + a, j + b); free = !(q > p && (nx - x) ** 2 + (nz - z) ** 2 < 25); }
      const cls = free && zoneClass(x, z);
      if (!woods.has(cls)) continue; // ponytail: neighbours outside the forest still thin it, so stands run ~10 % sparse along their edges
      const h = (k) => hash(i, j, k), s = cls === 'conifer' ? 8 + 7 * h(14) : 6 + 6 * h(14);
      out[cls].push([d, x, z, h(15) * 6.28, s * (.9 + .2 * h(18)), s, s * (.9 + .2 * h(19)), ...tint(colors[cls], h(16), .8 + .4 * h(17)), .4]);
    }
  }
  function orchards(tx, tz, R, out) { // citrus rows along the zone's row_angle_deg, 5 m apart, every 4.2 m, 1.5 m inside the edge; lattice anchored at the origin
    for (const [, x0, z0] of tiles(tx, tz, R)) {
      if (full('citrus', out)) break;
      for (const deg of new Set(near(x0, z0).filter((z) => z.class === 'orchard').map((z) => z.row_angle_deg ?? 0))) { // ponytail: no row angle -> rows along x
        const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a), [u0, u1, v0, v1] = span(x0, z0, c, s);
        for (let i = Math.ceil(u0 / 4.2); i * 4.2 <= u1; i++) for (let j = Math.ceil(v0 / 5); j * 5 <= v1; j++) {
          const x = i * 4.2 * c - j * 5 * s, z = i * 4.2 * s + j * 5 * c, d = Math.hypot(x - tx, z - tz);
          if (x < x0 || x >= x0 + T || z < z0 || z >= z0 + T || d >= R) continue;
          const zone = zoneAt(x, z);
          if (zone?.class !== 'orchard' || (zone.row_angle_deg ?? 0) !== deg || [[1.5, 0], [-1.5, 0], [0, 1.5], [0, -1.5]].some(([ox, oz]) => zoneClass(x + ox, z + oz) !== 'orchard')) continue;
          const h = (k) => hash(i, j, Math.round(deg * 10) * 8 + k), sc = 2.5 + .5 * h(1), w = sc * (.9 + .2 * h(5));
          out.citrus.push([d, x + (h(2) - .5) * .3, z + (h(3) - .5) * .3, h(4) * 6.28, w, sc, w, ...tint(colors.citrus, h(6), .85 + .3 * h(7)), .2]);
        }
      }
    }
  }
  function tunnels(tx, tz, R, out) { // lines 8.5 m apart across row_angle_deg (anchored at the origin) sampled every 1 m: a tunnel is a run whose centre and both 3.2 m sides are greenhouse ground of that angle
    // ponytail: three sample lines, not a true 3.5 m inset, so a corner can clip a tunnel end; a run into a tile still loading ends there until it lands (650 m fetch vs 400 m radius).
    const lines = new Map(), seen = new Set();
    for (const t of src.within(tx - R, tz - R, tx + R, tz + R)) for (const i of t.houses) {
      const b = t.zones.boxes[i], deg = t.list[i].row_angle_deg ?? 0, a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
      if (b[0] > tx + R || b[2] < tx - R || b[1] > tz + R || b[3] < tz - R) continue;
      const us = [b[0] * c + b[1] * s, b[2] * c + b[1] * s, b[0] * c + b[3] * s, b[2] * c + b[3] * s], vs = [b[1] * c - b[0] * s, b[1] * c - b[2] * s, b[3] * c - b[0] * s, b[3] * c - b[2] * s];
      for (let k = Math.ceil(Math.min(...vs) / 8.5); k * 8.5 <= Math.max(...vs); k++) bucket(lines, `${deg} ${k}`).push([Math.min(...us), Math.max(...us)]);
    }
    for (const [key, spans] of lines) {
      const [deg, k] = key.split(' ').map(Number), a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a), v = k * 8.5;
      const inside = (u) => [0, 3.2, -3.2].every((w) => { const zone = zoneAt(u * c - (v + w) * s, u * s + (v + w) * c); return zone?.class === 'greenhouse' && (zone.row_angle_deg ?? 0) === deg; });
      for (const [u0, u1] of spans) for (let u = Math.ceil(u0); u <= u1; u++) {
        if (!inside(u)) continue;
        let lo = u, hi = u; while (inside(lo - 1)) lo--; while (inside(hi + 1)) hi++; // past the span too, so a tunnel never depends on which pieces were gathered
        u = hi; if (seen.has(`${key} ${lo}`) || hi - lo + 1 < 5) continue; seen.add(`${key} ${lo}`);
        const m = (lo + hi) / 2, x = m * c - v * s, z = m * s + v * c, d = Math.hypot(x - tx, z - tz), h = (n) => hash(lo, k, Math.round(deg * 10) * 8 + n);
        if (d < R) out.greenhouse.push([d, x, z, -a, hi - lo + 1, 1, 1, ...tint(colors.greenhouse, h(1), .92 + .08 * h(2)), .25, (hi - lo + 1) / 2]);
      }
    }
  }
  let height, last = {tier:-1}, timer = 0;
  function write(m, list, R, eye) { // reuse the capped buffers; items are [distance, x, z, yaw, sx, sy, sz, r, g, b, sink, half-length drape]
    if (list.length > m.instanceMatrix.count) R = list.sort((a, b) => a[0] - b[0])[(list.length = m.instanceMatrix.count) - 1][0];
    // ponytail: film tunnels sorted back to front from the regeneration camera only; orbiting without moving the target can mis-sort overlaps.
    if (eye) list.sort((a, b) => Math.hypot(b[1] - eye.x, b[2] - eye.z) - Math.hypot(a[1] - eye.x, a[2] - eye.z));
    let n = 0;
    for (const [d, x, z, yaw, sx, sy, sz, r, g, b, sink = .03, half] of list) {
      const f = Math.min(1, (R - d) / (R * .3)), c = Math.cos(yaw) * f, s = Math.sin(yaw) * f; // shrink toward the (cap) radius, no hard edge
      // half: a long tunnel is sheared to the terrain under its two ends instead of floating level from its middle
      const ends = half ? [height(x - Math.cos(yaw) * half, z + Math.sin(yaw) * half), height(x + Math.cos(yaw) * half, z - Math.sin(yaw) * half)] : null, y = ends ? Math.min(height(x, z), (ends[0] + ends[1]) / 2) : height(x, z);
      if (y === -Infinity || ends?.includes(-Infinity)) continue;
      m.instanceMatrix.array.set([c * sx, ends ? (ends[1] - ends[0]) * f : 0, -s * sx, 0, 0, sy * f, 0, 0, s * sz, 0, c * sz, 0, x, y - sink, z, 1], n * 16); m.instanceColor.array.set([r, g, b], n++ * 3);
    }
    m.count = n; m.visible = n > 0; m.instanceMatrix.needsUpdate = m.instanceColor.needsUpdate = true; m.computeBoundingSphere();
  }
  // ponytail: one synchronous task once the camera settles (the first also indexes the terrain); split per type over idle callbacks if the hitch shows.
  function regenerate(tx, tz, tier, eye) {
    const start = performance.now(), out = Object.fromEntries(Object.keys(kinds).map((k) => [k, []]));
    height ||= surface(surfaces); last = {x:tx, z:tz, tier}; dirty = false;
    if (tier < 3) src.request(tx, tz, 650); // the 400 m tree radius plus a 250 m margin
    if (tier === 0) { plants(tx, tz, out); grass(tx, tz, out); }
    if (tier < 2) walls(tx, tz, out);
    if (tier < 3) { forest(tx, tz, 400, out); orchards(tx, tz, 250, out); tunnels(tx, tz, 400, out); }
    for (const name in out) write(meshes[name], out[name], kinds[name][3], name === 'greenhouse' && eye);
    performance.measure('groundcover', {start, detail:Object.fromEntries(Object.entries(meshes).map(([k, m]) => [k, m.count]))});
    redraw();
  }
  return {group, update(camera, target) {
    const d = camera.position.distanceTo(target), tier = d < 180 ? 0 : d < 600 ? 1 : d < 2000 ? 2 : 3; // plants below 180 m, walls to 600 m, trees to 2 km
    fade.value = Math.min(1, (2000 - d) / 1200);
    for (const name in meshes) if (kinds[name][4] < tier) meshes[name].visible = false;
    if (!dirty && tier === last.tier && (tier === 3 || Math.hypot(target.x - last.x, target.z - last.z) < (tier ? 40 : 15))) return;
    clearTimeout(timer); timer = setTimeout(() => regenerate(target.x, target.z, tier, camera.position), 150);
  }};
}


export async function loadGrid(redraw = () => {}) {
  const response = await fetch('/local/manifest.json', {signal:AbortSignal.timeout(20000), cache:'no-store'});
  if (!response.ok) throw new Error('Local scene manifest unavailable');
  const data = await response.json();
  if (!Array.isArray(data.routes) || !Array.isArray(data.facilities) || !data.facilities.some((f) => f.kind === 'wind')) throw new Error('Invalid local scene manifest');
  // Scene-wide green tiles when /local/green/ has them; else the manifest's groundcover block; else no layer.
  const [gltf, green] = await Promise.all([new GLTFLoader().loadAsync('/local/scene.glb'), fetch('/local/green/index.json', {signal:AbortSignal.timeout(20000), cache:'no-store'})
    .then((r) => r.ok ? r.json() : null).then((index) => index?.schema_version === 1 && index.tile_size_m > 0 && Array.isArray(index.tiles) ? index : null, () => null)]);
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
  const visible = {wind:true, transmission:true, substation:true, terrain:true, pv:true, buildings:true, sea:true, roads:true};
  if (layers.get('vegetation').length) visible.vegetation = true; // baked vegetation_* nodes are optional: without them the toggle is disabled
  const cover = (green || data.groundcover) && groundcover(green ? {index:green} : data.groundcover, surfaces, redraw);
  if (cover) { object.add(cover.group); layers.get('groundcover').push(cover.group); visible.groundcover = true; }
  object.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(gltf.scene), obstacles = new Map();
  const terrainHeight = surface(surfaces, [bounds.min.x, bounds.min.z, bounds.max.x, bounds.max.z]);
  gltf.scene.traverse((mesh) => {
    if (!mesh.isMesh || surfaces.includes(mesh) || mesh.name.startsWith('groundcover_')) return;
    const box = new THREE.Box3().setFromObject(mesh);
    // Rotating rotor parts stay inside a conservative cached envelope.
    for (let node = mesh; node; node = node.parent) if (rotors.includes(node)) {
      box.expandByScalar(box.getSize(new THREE.Vector3()).length()); break;
    }
    const entry = {mesh, box};
    for (let x = Math.floor(box.min.x / CELL); x <= Math.floor(box.max.x / CELL); x++) for (let z = Math.floor(box.min.z / CELL); z <= Math.floor(box.max.z / CELL); z++) bucket(obstacles, x * 4096 + z).push(entry);
  });
  const raycaster = new THREE.Raycaster(), downRay = new THREE.Raycaster(undefined, new THREE.Vector3(0, -1, 0));
  const shown = (node) => {
    for (; node; node = node.parent) if (!node.visible || (node.userData.facility && visible[node.userData.facility.layer] === false)) return false;
    return true;
  };
  const [longitude, latitude] = data.coordinateFrame.origin_lon_lat;
  // UTM52 meridian convergence at this small scene's origin; input wind is 16-point true-north bearing.
  const northOffset = -Math.atan(Math.tan(THREE.MathUtils.degToRad(longitude - 129)) * Math.sin(THREE.MathUtils.degToRad(latitude)));
  const up = new THREE.Vector3(0, 1, 0);
  return {
    object, data, records, pickables, visible, rotors, bounds,
    groundHeight(x, z) {
      if (!Number.isFinite(x) || !Number.isFinite(z)) return 0;
      let y = terrainHeight(x, z); if (!Number.isFinite(y)) y = 0;
      const candidates = (obstacles.get(cellKey(x, z)) || []).filter(({mesh, box}) => shown(mesh) && x >= box.min.x && x <= box.max.x && z >= box.min.z && z <= box.max.z && box.max.y > y).sort((a, b) => b.box.max.y - a.box.max.y);
      downRay.ray.origin.set(x, Math.max(bounds.max.y + 1, ...candidates.map(({box}) => box.max.y + 1)), z);
      for (const {mesh, box} of candidates) {
        if (box.max.y <= y) continue;
        const hit = downRay.intersectObject(mesh, false)[0];
        if (hit && Number.isFinite(hit.point.y)) y = Math.max(y, hit.point.y);
      }
      return y;
    },
    pick(camera, ndc, {firstOnly = false} = {}) {
      if (!Number.isFinite(ndc.x) || !Number.isFinite(ndc.y)) return [];
      raycaster.camera = camera; raycaster.layers.mask = camera.layers.mask;
      raycaster.setFromCamera(ndc, camera); raycaster.params.Points.threshold = 3;
      const found = [], seen = new Set();
      for (const hit of raycaster.intersectObjects(pickables.filter(shown), true)) {
        if (!shown(hit.object)) continue;
        const material = Array.isArray(hit.object.material) ? hit.object.material[hit.face?.materialIndex || 0] : hit.object.material;
        if (material?.visible === false) continue;
        let node = hit.object; while (node && !node.userData.facility) node = node.parent;
        const record = node?.userData.facility;
        if (!record || seen.has(record.id)) continue;
        found.push(record); seen.add(record.id);
        if (firstOnly) break;
      }
      return found;
    },
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
