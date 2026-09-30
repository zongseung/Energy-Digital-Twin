import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as THREE from '../renderers/mock/node_modules/three/build/three.module.js';
import {GLTFLoader} from '../renderers/mock/node_modules/three/examples/jsm/loaders/GLTFLoader.js';

const source = (await readFile(new URL('../renderers/twin/grid.js', import.meta.url), 'utf8'))
  .replaceAll("from 'three'", `from '${new URL('../renderers/mock/node_modules/three/build/three.module.js', import.meta.url)}'`)
  .replace(/from 'three\/addons\/([^']+)'/g, (_, path) => `from '${new URL(`../renderers/mock/node_modules/three/examples/jsm/${path}`, import.meta.url)}'`);
const {loadGrid} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const scene = new THREE.Group();
function box(name, x, y, z, size = 2) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(size, size, size), new THREE.MeshBasicMaterial());
  mesh.name = name; mesh.position.set(x, y, z); return mesh;
}
const terrain = new THREE.Mesh(new THREE.PlaneGeometry(100, 100).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial());
terrain.name = 'terrain_landcover_test'; terrain.position.y = 1; scene.add(terrain);
const wind = new THREE.Group(); wind.name = 'wind'; wind.add(box('turbine', 0, 3, 0)); scene.add(wind);
const pv = new THREE.Group(); pv.name = 'pv'; pv.add(box('panel', 0, 7, 0)); scene.add(pv);
const building = new THREE.Group(); building.name = 'building_group'; building.add(box('building_roof', 0, 11, 0)); scene.add(building);
const distant = box('distant', 40, 100, 40); scene.add(distant);
let distantRays = 0; distant.raycast = () => { distantRays++; };
const data = {
  routes: [{id:'route:1',kind:'cable',paths:[[[20, 3, -5], [20, 3, 5]]]}],
  facilities: ['wind', 'pv'].map((kind) => ({id:kind,node:kind,kind,position:[0, kind === 'wind' ? 3 : 7, 0],coordinates:[126,33]})),
  coordinateFrame:{origin_lon_lat:[126,33]}, cameras:{wind:{position:[0,30,0],target:[0,0,0]}},
  buildings:{records:[{id:'building:1',node:'building_group',position:[0,11,0],walls_m:2,height_status:'provider',roof_rule:'flat_parapet'}]},
};
const originalFetch = globalThis.fetch, originalLoad = GLTFLoader.prototype.loadAsync;
globalThis.fetch = async () => ({ok:true,json:async () => structuredClone(data)});
GLTFLoader.prototype.loadAsync = async () => ({scene});
try {
  const grid = await loadGrid();
  grid.resize(800,800);
  assert.ok(grid.bounds instanceof THREE.Box3, 'grid exposes scene bounds');
  assert.equal(grid.groundHeight(0,0),12,'highest roof blocks camera');
  assert.equal(grid.groundHeight(10,10),1,'terrain elevation is preserved');
  assert.equal(grid.groundHeight(500,500),0,'uncovered coordinates remain finite');
  assert.equal(grid.groundHeight(NaN,0),0,'nonfinite input cannot poison camera');
  assert.equal(distantRays,0,'spatially distant meshes are not raycast');
  const camera = new THREE.PerspectiveCamera(50,1,.1,1000); camera.position.set(0,30,0); camera.lookAt(0,0,0); camera.updateMatrixWorld();
  for (const node of grid.pickables) if (node.isPoints) node.visible = false;
  assert.deepEqual(grid.pick(camera,{x:0,y:0}).map((r) => r.id),['building:1','pv','wind']);
  assert.deepEqual(grid.pick(camera,{x:0,y:0},{firstOnly:true}).map((r) => r.id),['building:1']);
  building.visible = false;
  assert.equal(grid.groundHeight(0,0),8,'hidden ancestor removes roof collision');
  assert.deepEqual(grid.pick(camera,{x:0,y:0}).map((r) => r.id),['pv','wind']);
  grid.setLayer('pv',false);
  assert.deepEqual(grid.pick(camera,{x:0,y:0}).map((r) => r.id),['wind']);
  wind.children[0].visible = false;
  assert.deepEqual(grid.pick(camera,{x:0,y:0}),[],'hidden descendant cannot be selected');
  camera.position.set(20,30,0); camera.lookAt(20,3,0); camera.updateMatrixWorld(); grid.resize(800,800);
  assert.deepEqual(grid.pick(camera,{x:0,y:0}).map((r) => r.id),['route:1'],'Line2 receives a camera');
  console.log('PASS map grid: bounds, roof/terrain/fallback heights, spatial culling, hidden ancestors/children/layers, ordered unique picking, firstOnly, Line2');
} finally { globalThis.fetch = originalFetch; GLTFLoader.prototype.loadAsync = originalLoad; }
