import { mockProfile, timeLabel } from './model.js';

const $ = id => document.getElementById(id);
const labels = { substation: '변전소', power_plant: '발전 레코드', pv_facility: '태양광' };
const state = { scene: null, selected: null, matches: [], shown: 60, view: null };
const kindLabel = f => f.source_properties.plant_source === 'wind' ? '풍력 발전 레코드' : labels[f.kind];
const explorer = $('explorer');
const mobile = matchMedia('(max-width: 767px)');
explorer.open = !mobile.matches;
mobile.addEventListener('change', () => { explorer.open = !mobile.matches; });

function updateTime() {
  const slot = Number($('time').value), values = mockProfile(slot);
  $('time-value').value = `${timeLabel(slot)} KST`;
  $('time').setAttribute('aria-valuetext', `${timeLabel(slot)}, 합성 데이터`);
  for (const key of ['demand', 'wind', 'solar']) $(key).textContent = values[key].toLocaleString('ko-KR');
}
$('time').addEventListener('input', updateTime);
updateTime();

function select(facility) {
  state.selected = facility;
  $('selected-name').textContent = facility ? facility.name : '시설을 선택하세요';
  $('selection-guide').hidden = Boolean(facility);
  $('selected-details').replaceChildren();
  $('focus').disabled = !facility || !state.view;
  if (facility) {
    const props = facility.source_properties;
    const fields = [['종류', kindLabel(facility)], ['원천 ID', facility.id],
      ['경도 · 위도', facility.coordinates.map(n => n.toFixed(5)).join(' · ')],
      ['자료 출처', props.source_table || '원천 GIS'],
      ['자료 기준일', props.data_date || props.base_year || '원천에 미기재'],
      ['시설별 현재 출력', '자료 없음']];
    for (const [key, value] of fields) {
      const dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = key; dd.textContent = value;
      $('selected-details').append(dt, dd);
    }
  }
  state.view?.select(facility);
  for (const button of $('facilities').querySelectorAll('button')) {
    button.setAttribute('aria-pressed', String(button.dataset.id === facility?.id));
  }
}

function list() {
  const query = $('search').value.trim().toLocaleLowerCase();
  const layers = new Set([...$('layers').querySelectorAll('input:checked')].map(i => i.value));
  state.matches = (state.scene?.facilities || []).filter(f => layers.has(f.kind) &&
    `${f.name} ${f.id} ${kindLabel(f)}`.toLocaleLowerCase().includes(query));
  if (state.selected && !state.matches.some(f => f.id === state.selected.id)) select(null);
  state.view?.filter(new Set(state.matches.map(f => f.id)), layers.has('lines'));
  $('facilities').replaceChildren();
  for (const f of state.matches.slice(0, state.shown)) {
    const li = document.createElement('li'), button = document.createElement('button');
    const name = document.createElement('strong'), caption = document.createElement('small');
    button.type = 'button'; button.className = 'facility'; button.dataset.id = f.id;
    button.setAttribute('aria-pressed', String(state.selected?.id === f.id));
    name.textContent = f.name; caption.textContent = kindLabel(f);
    button.append(name, caption); button.addEventListener('click', () => select(f));
    li.append(button); $('facilities').append(li);
  }
  $('result-count').textContent = state.matches.length ? `${state.matches.length.toLocaleString('ko-KR')}개 중 ${Math.min(state.shown, state.matches.length)}개 표시` : '조건에 맞는 시설이 없습니다';
  $('more').hidden = state.matches.length <= state.shown;
  $('clear').hidden = Boolean(state.matches.length);
}
$('search').addEventListener('input', () => { state.shown = 60; list(); });
$('layers').addEventListener('change', () => { state.shown = 60; list(); });
$('more').addEventListener('click', () => { state.shown += 60; list(); });
$('clear').addEventListener('click', () => {
  $('search').value = '';
  for (const input of $('layers').querySelectorAll('input')) input.checked = true;
  list(); $('search').focus();
});

function error(message) {
  $('map-status').hidden = false;
  $('map-status').className = 'error';
  $('map-status').setAttribute('role', 'alert');
  $('map-status').textContent = message;
  const retry = document.createElement('button');
  retry.textContent = '다시 시도'; retry.addEventListener('click', () => location.reload());
  $('map-status').append(document.createElement('br'), retry);
}

async function createView(data) {
  const THREE = await import('three');
  const { OrbitControls } = await import('three/addons/controls/OrbitControls.js');
  const styles = getComputedStyle(document.documentElement);
  const color = token => new THREE.Color(styles.getPropertyValue(token).trim());
  const map = $('map');
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.domElement.tabIndex = 0;
  renderer.domElement.setAttribute('aria-label', '제주 3D 지도. 드래그로 회전, 휠로 확대. 시설은 목록에서도 선택할 수 있습니다.');
  map.prepend(renderer.domElement);
  const scene = new THREE.Scene(); scene.background = color('--ocean');
  const camera = new THREE.PerspectiveCamera(42, 1, .1, 1000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = false;
  controls.minDistance = 2; controls.maxDistance = 220; controls.maxPolarAngle = Math.PI * .48;
  controls.listenToKeyEvents(renderer.domElement);
  let queued = false, available = true;
  let overview = true;
  controls.addEventListener('start', () => { overview = false; });
  function draw() {
    if (queued || document.hidden || !available) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; if (available) renderer.render(scene, camera); });
  }
  controls.addEventListener('change', draw);
  document.addEventListener('visibilitychange', draw);
  scene.add(new THREE.HemisphereLight(color('--text'), color('--terrain-low'), 2.5));
  const sun = new THREE.DirectionalLight(color('--text'), 3);
  sun.position.set(-30, 80, 30); scene.add(sun);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(data.terrain.positions, 3));
  geometry.setIndex(data.terrain.indices); geometry.computeVertexNormals();
  const low = color('--terrain-low'), high = color('--terrain-high'), colors = [];
  for (let i = 1; i < data.terrain.positions.length; i += 3) {
    const c = low.clone().lerp(high, Math.min(1, data.terrain.positions[i] / 5.5));
    colors.push(c.r, c.g, c.b);
  }
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  scene.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: .95 })));

  function addLines(paths, token, lift) {
    const points = [];
    for (const path of paths) for (let i = 1; i < path.length; i++) {
      for (const p of [path[i - 1], path[i]]) points.push(p[0], p[1] + lift, p[2]);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
    const mesh = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: color(token) }));
    scene.add(mesh); return mesh;
  }
  addLines(data.coast, '--accent', .08);
  const lines = addLines(data.lines.map(l => l.points), '--line', .12);
  const markers = [], matrix = new THREE.Matrix4(), scale = new THREE.Vector3(), pos = new THREE.Vector3();
  for (const [kind, token] of [['substation', '--substation'], ['power_plant', '--plant'], ['pv_facility', '--pv']]) {
    const entries = data.facilities.filter(f => f.kind === kind);
    const shape = kind === 'substation' ? new THREE.OctahedronGeometry(.42) : new THREE.BoxGeometry(.23, .3, .23);
    const mesh = new THREE.InstancedMesh(shape, new THREE.MeshStandardMaterial({ color: color(token), roughness: .6 }), entries.length);
    mesh.userData.facilities = entries;
    entries.forEach((f, i) => {
      mesh.setMatrixAt(i, matrix.makeTranslation(f.position[0], f.position[1] + .28, f.position[2]));
    });
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    mesh.computeBoundingSphere(); markers.push(mesh); scene.add(mesh);
  }
  const ring = new THREE.Mesh(new THREE.RingGeometry(.65, .85, 40), new THREE.MeshBasicMaterial({ color: color('--accent'), side: THREE.DoubleSide, depthTest: false }));
  ring.rotation.x = -Math.PI / 2; ring.visible = false; ring.renderOrder = 1; scene.add(ring);
  const ray = new THREE.Raycaster(), pointer = new THREE.Vector2();
  let pressed;
  renderer.domElement.addEventListener('pointerdown', e => { pressed = [e.clientX, e.clientY]; });
  renderer.domElement.addEventListener('pointerup', e => {
    if (!pressed || Math.hypot(e.clientX - pressed[0], e.clientY - pressed[1]) > 5) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1);
    ray.setFromCamera(pointer, camera);
    const hit = ray.intersectObjects(markers).find(h => state.matches.some(f => f.id === h.object.userData.facilities[h.instanceId].id));
    if (hit) select(hit.object.userData.facilities[hit.instanceId]);
  });
  const reset = () => {
    overview = true;
    controls.target.set(0, 0, 0);
    camera.position.set(20, 66, 78).multiplyScalar(Math.max(1.2, 1.2 / camera.aspect));
    controls.update(); draw();
  };
  $('reset').addEventListener('click', reset);
  for (const [id, factor] of [['zoom-in', .75], ['zoom-out', 1.25]]) $(id).addEventListener('click', () => {
    overview = false;
    const offset = camera.position.clone().sub(controls.target);
    offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, controls.minDistance, controls.maxDistance));
    camera.position.copy(controls.target).add(offset); controls.update(); draw();
  });
  $('rotate').addEventListener('click', () => {
    overview = false;
    const offset = camera.position.clone().sub(controls.target).applyAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 8);
    camera.position.copy(controls.target).add(offset); controls.update(); draw();
  });
  $('focus').addEventListener('click', () => {
    if (!state.selected || !available) return;
    overview = false;
    controls.target.fromArray(state.selected.position);
    camera.position.copy(controls.target).add(new THREE.Vector3(4, 8, 10)); controls.update(); draw();
  });
  const observer = new ResizeObserver(() => {
    if (!map.clientWidth || !map.clientHeight) return;
    camera.aspect = map.clientWidth / map.clientHeight; camera.updateProjectionMatrix();
    renderer.setSize(map.clientWidth, map.clientHeight, false);
    if (overview) reset(); else draw();
  });
  observer.observe(map);
  renderer.domElement.addEventListener('webglcontextlost', event => {
    event.preventDefault(); available = false; controls.enabled = false;
    for (const b of document.querySelectorAll('.map-controls button,#focus')) b.disabled = true;
    state.view = null;
    error('3D 그래픽 연결이 끊겼습니다. 목록과 시간축은 계속 사용할 수 있습니다.');
  });
  for (const button of document.querySelectorAll('.map-controls button')) button.disabled = false;
  reset();
  return {
    select(f) { ring.visible = Boolean(f); if (f) ring.position.set(f.position[0], f.position[1] + .5, f.position[2]); draw(); },
    filter(ids, showLines) {
      lines.visible = showLines;
      for (const mesh of markers) {
        mesh.userData.facilities.forEach((f, i) => {
          pos.set(f.position[0], f.position[1] + .28, f.position[2]);
          scale.setScalar(ids.has(f.id) ? 1 : 0);
          mesh.setMatrixAt(i, matrix.compose(pos, mesh.quaternion, scale));
        });
        mesh.instanceMatrix.needsUpdate = true;
      }
      draw();
    },
  };
}

try {
  const response = await fetch('/scene.json');
  if (!response.ok) throw new Error('Scene request failed');
  const data = await response.json();
  if (data.schema_version !== 1 || data.data_kind !== 'mock_geometry' || !data.facilities?.length || !data.terrain?.indices?.length) throw new Error('Invalid scene');
  state.scene = data;
  $('total-count').textContent = data.facilities.length.toLocaleString('ko-KR');
  $('provenance').textContent = `원천 ${data.metadata.source_commit} · ${data.metadata.source_generated_at}`;
  list();
  try {
    state.view = await createView(data);
    list(); $('map-status').hidden = true;
    document.body.dataset.ready = 'true';
  } catch (failure) {
    console.error(failure);
    error('이 브라우저에서 3D 지도를 표시할 수 없습니다. 시설 목록은 사용할 수 있습니다.');
  }
} catch (failure) {
  console.error(failure);
  error('장면 데이터를 불러오지 못했습니다. 자료 준비와 서버 연결을 확인하세요.');
  $('result-count').textContent = '시설 자료를 불러오지 못했습니다';
}
