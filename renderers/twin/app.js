import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {loadGrid} from '/twin/grid.js';
import {estimateWind} from '/twin/wind-estimate.mjs';
import {shouldApply, kstDay} from '/twin/playback.mjs';

const el = (id) => document.getElementById(id);
const metricKeys = ['demand_mw', 'supply_capacity_mw', 'wind_mw', 'solar_mw', 'renewable_total_mw'];
const layers = ['wind', 'transmission', 'substation', 'pv', 'terrain', 'buildings', 'sea'];
const sceneControls = ['inspect', 'overview', 'wind-view', 'terrain-relief', 'terrain-view', 'pv-view', 'buildings-view', 'sea-view', 'rotate', 'zoom-in', 'zoom-out', 'rotor-demo'];
let renderer, scene, camera, controls, selected, grid;
let demoFrame = 0, previousTime = 0, socket, reconnectTimer, contextLost = false;
let windData = null, windSocket, windReconnectTimer, windMessageTimer, windExpiryTimer, windFetchFailed = false;
let rotorRPM = new Map();
let mode = 'latest', seq = 0, times = [], lastLive = null, selectedAt = null, playTimer, scenario = null, dayNote = '';
function status(id, message, failed = false) {
  el(id).textContent = message;
  el(id).classList.toggle('error', failed);
  el(id).setAttribute('role', failed ? 'alert' : 'status');
}
async function getJson(url) {
  const response = await fetch(url, {signal:AbortSignal.timeout(20000), cache:'no-store'});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
function render() {
  if (renderer && !contextLost && !document.hidden) { grid?.update(camera); renderer.render(scene, camera); }
}
function cameraAt(view, scale = 1) {
  controls.target.fromArray(view.target);
  camera.position.fromArray(view.position).sub(controls.target).multiplyScalar(Math.max(1, 650 / el('viewport').clientWidth) * scale).add(controls.target);
  camera.fov = view.fov || 48; camera.updateProjectionMatrix(); controls.update(); render();
}
function inspect() {
  if (selected && controls && !contextLost) cameraAt(selected.view, selected.kind === 'wind' ? 1.35 : 1);
}
function revealSelection() {
  if (matchMedia('(min-width:768px) and (min-height:601px)').matches) document.querySelector('.sidebar').scrollTop = 0;
  else el('viewport').scrollIntoView({block:'start'});
}
function choose(record, focus = true) {
  selected = record; document.body.dataset.selected = record.id;
  el('selected-name').textContent = record.name;
  el('scene-title').textContent = `${record.name} · 실제 위치 / 설비 외형 추정`;
  status('selection-status', `선택됨 · ${record.id}`);
  el('facility-detail').replaceChildren();
  for (const [label, value] of record.rows) {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = label; dd.textContent = value; el('facility-detail').append(dt, dd);
  }
  for (const button of el('facilities').querySelectorAll('button')) {
    const active = button.dataset.id === record.id;
    button.setAttribute('aria-pressed', String(active));
    button.querySelector('small').textContent = active ? '선택됨' : button.dataset.tag;
  }
  grid.highlight(record);
  showWind();
  if (focus) { inspect(); revealSelection(); } else render();
}
function populate() {
  const records = grid.records.filter((r) => grid.visible[r.layer]);
  el('facilities').replaceChildren();
  for (const record of records) {
    const li = document.createElement('li'), button = document.createElement('button');
    const name = document.createElement('strong'), tag = document.createElement('small');
    button.className = 'facility'; button.dataset.id = record.id;
    button.setAttribute('aria-pressed', 'false'); name.textContent = record.name;
    button.dataset.tag = ({wind:'풍력',transmission:'송전',hvdc:'HVDC',cable:'케이블',substation:'변전소',pv:'PV'})[record.kind];
    tag.textContent = button.dataset.tag; button.append(name, tag); button.onclick = () => choose(record);
    li.append(button); el('facilities').append(li);
  }
  el('facility-count').textContent = `· ${records.length}개`;
  if (selected && records.some((r) => r.id === selected.id)) choose(selected, false);
  else if (records.length) choose(records.find((r) => r.kind === 'wind') || records[0], false);
  else {
    selected = null; delete document.body.dataset.selected;
    el('selected-name').textContent = el('scene-title').textContent = '표시할 시설 없음';
    status('selection-status', '풍력·송전·변전소·태양광 레이어를 켜세요.');
    el('facility-detail').replaceChildren(); grid.highlight(null);
  }
}
async function startScene() {
  try {
    renderer = new THREE.WebGLRenderer({antialias:true});
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = .9;
    scene = new THREE.Scene(); scene.background = new THREE.Color('#829fab');
    camera = new THREE.PerspectiveCamera(48, 1, .5, 70000);
    const pmrem = new THREE.PMREMGenerator(renderer), environment = new RoomEnvironment();
    scene.environment = pmrem.fromScene(environment, .04).texture; scene.environmentIntensity = .3;
    environment.dispose(); pmrem.dispose();
    scene.add(new THREE.HemisphereLight(0xdceeff, 0x475354, .7));
    const sun = new THREE.DirectionalLight(0xfff4dd, 2); sun.position.set(1000, 1500, 600); scene.add(sun);
    el('viewport').append(renderer.domElement);
    renderer.domElement.setAttribute('aria-label', '탐라–한림 실제 영상·지형과 전력시설 3D. 목록과 시점 버튼으로도 조작할 수 있습니다.');
    renderer.domElement.addEventListener('webglcontextlost', (event) => {
      event.preventDefault(); contextLost = true; stopDemo(); el('rotor-demo').checked = false; controls.enabled = false;
      for (const id of sceneControls) el(id).disabled = true;
      document.body.dataset.ready = 'false'; el('map-status').hidden = false;
      showWind();
      status('map-status', '3D 연결이 중단되었습니다. 새로고침하세요. 시설 목록과 관측값은 계속 확인할 수 있습니다.', true);
    });
    controls = new OrbitControls(camera, renderer.domElement);
    controls.minDistance = 20; controls.maxDistance = 50000; controls.maxPolarAngle = Math.PI * .49;
    controls.addEventListener('change', render);
    const resize = () => {
      const {clientWidth:w, clientHeight:h} = el('viewport');
      renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); grid?.resize(w, h); render();
    };
    new ResizeObserver(resize).observe(el('viewport')); resize();
    grid = await loadGrid();
    el('buildings-count').textContent = ` · ${grid.data.buildings.count.toLocaleString('ko-KR')}동 + 랜드마크 ${grid.data.landmarks?.count || 0}곳`; el('buildings-count').hidden = false;
    populate();
    showWind();
    if (contextLost) return;
    scene.add(grid.object); grid.resize(el('viewport').clientWidth, el('viewport').clientHeight);
    const raycaster = new THREE.Raycaster(); let down;
    renderer.domElement.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
    renderer.domElement.addEventListener('pointerup', (e) => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
      const box = renderer.domElement.getBoundingClientRect();
      raycaster.setFromCamera(new THREE.Vector2((e.clientX - box.left) / box.width * 2 - 1, -(e.clientY - box.top) / box.height * 2 + 1), camera);
      raycaster.params.Points.threshold = Math.max(3, camera.position.distanceTo(controls.target) / 180);
      let hit = raycaster.intersectObjects(grid.pickables.filter((object) => object.visible), true)[0]?.object;
      while (hit && !hit.userData.facility) hit = hit.parent;
      if (hit) { choose(hit.userData.facility, false); revealSelection(); }
    });
    for (const id of sceneControls) el(id).disabled = false;
    for (const layer of layers) el(`layer-${layer}`).disabled = false;
    el('map-status').hidden = true; document.body.dataset.ready = 'true'; cameraAt(grid.data.cameras.array);
  } catch (error) {
    console.error('Local scene failed', error); document.body.dataset.ready = 'false';
    el('map-status').hidden = false;
    status('map-status', '통합 지형·시설을 불러오지 못했습니다. 페이지를 새로고침하세요. 지역 관측은 별도로 확인할 수 있습니다.', true);
  }
}
function stopDemo() { cancelAnimationFrame(demoFrame); demoFrame = 0; previousTime = 0; }
function syncDemo() {
  if (!grid || !grid.visible.wind || !el('rotor-demo').checked || document.hidden || contextLost || !grid.rotors.some((rotor) => (rotorRPM.get(rotor.userData.facilityId) || 0) > 0)) { stopDemo(); return; }
  if (!demoFrame) demoFrame = requestAnimationFrame(animate);
}
function animate(time) {
  demoFrame = 0;
  if (!grid?.visible.wind || !el('rotor-demo').checked || document.hidden || contextLost) { stopDemo(); return; }
  const delta = previousTime ? Math.min((time - previousTime) / 1000, .1) : 0; previousTime = time;
  for (const rotor of grid.rotors) rotor.rotateX(delta * (rotorRPM.get(rotor.userData.facilityId) || 0) * Math.PI * 2 / 60);
  render(); syncDemo();
}
function enableLayer(layer) {
  if (!grid.visible[layer]) { grid.setLayer(layer, true); el(`layer-${layer}`).checked = true; populate(); }
  if (layer === 'wind') { el('rotor-demo').disabled = contextLost; showWind(); }
}
el('rotor-demo').onchange = syncDemo;
el('inspect').onclick = inspect;
el('overview').onclick = () => { enableLayer('terrain'); cameraAt(grid.data.cameras.overview); el('scene-title').textContent = '탐라–한림 일부 · 실제 영상 + DSM / 시설 통합'; };
el('wind-view').onclick = () => { enableLayer('wind'); enableLayer('terrain'); enableLayer('sea'); cameraAt(grid.data.cameras.array); el('scene-title').textContent = '탐라 풍력과 주변 실제 지형'; };
el('buildings-view').onclick = () => { enableLayer('terrain'); cameraAt(grid.data.cameras.buildings); el('scene-title').textContent = '신창 마을 · 윤곽·지붕영상 실제 / 형태·외벽 추정'; };
el('sea-view').onclick = () => { enableLayer('sea'); enableLayer('wind'); enableLayer('terrain'); cameraAt(grid.data.cameras.sea); el('scene-title').textContent = '풍력·바다 · 기존 VWorld 영상 / 조위·수심 미반영'; };
el('terrain-relief').onclick = () => { enableLayer('terrain'); cameraAt(grid.data.cameras.terrain); el('scene-title').textContent = `실제 DSM 고도 · 구역 최고 약 ${Math.round(grid.data.terrain.height_range_m[1])} m / 수직 배율 1:1`; };
el('terrain-view').onclick = () => { enableLayer('transmission'); enableLayer('terrain'); choose(grid.records.find((r) => r.id === 'hub:power_line:3596')); };
el('pv-view').onclick = () => { enableLayer('pv'); enableLayer('terrain'); choose(grid.records.find((r) => r.kind === 'pv')); };
for (const layer of layers) el(`layer-${layer}`).onchange = (event) => {
  grid.setLayer(layer, event.target.checked);
  if (layer === 'wind') { stopDemo(); el('rotor-demo').checked = false; el('rotor-demo').disabled = !event.target.checked || contextLost; }
  populate(); showWind(); render();
};
el('rotate').onclick = () => { camera.position.sub(controls.target).applyAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 6).add(controls.target); controls.update(); render(); };
for (const [id, factor] of [['zoom-in', .8], ['zoom-out', 1.25]]) el(id).onclick = () => {
  const offset = camera.position.clone().sub(controls.target);
  offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, controls.minDistance, controls.maxDistance));
  camera.position.copy(controls.target).add(offset); controls.update(); render();
};

function showState(data) {
  if (!data?.observed_at || !Number.isFinite(Date.parse(data.observed_at))) throw new Error('Invalid observation');
  for (const key of metricKeys) el(key).textContent = Number.isFinite(data[key]) ? `${data[key].toLocaleString('ko-KR', {maximumFractionDigits:2})} MW` : '—';
  const flags = Array.isArray(data.quality_flags) ? data.quality_flags : [];
  const delayed = flags.includes('source_delayed');
  status('state-status', `${delayed ? '원천 갱신 지연 · ' : ''}${new Date(data.observed_at).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'})} KST 관측`);
  el('state-source').textContent = `출처: ${data.source || '—'} · 품질: ${flags.join(', ') || '표시 없음'} · 제주 집계 / 개별 시설 출력 아님`;
}
function blank(message, failed = true) {
  for (const key of metricKeys) el(key).textContent = '—';
  status('state-status', message, failed);
}
function live(data) {
  lastLive = data;
  if (shouldApply(mode, null, data?.observed_at, true, 0, 0)) showState(data);
}
async function refreshState() {
  el('refresh').disabled = true;
  try { live(await getJson('/api/v1/jeju/state')); }
  catch { if (mode === 'latest') blank('수급 자료를 읽지 못했습니다. 다시 읽기로 재시도하세요.'); }
  finally { el('refresh').disabled = false; }
}
function connectState() {
  clearTimeout(reconnectTimer);
  if (document.hidden || socket?.readyState <= 1) return;
  const connection = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/v1/jeju/ws`);
  socket = connection;
  connection.onmessage = (event) => {
    if (socket !== connection) return;
    try {
      const envelope = JSON.parse(event.data);
      if (envelope.type === 'snapshot' && envelope.data) live(envelope.data);
      else if (envelope.type === 'status' && mode === 'latest') status('state-status', '원천 연결 확인 중 · 표시 값은 마지막 수신 관측입니다.', true);
    } catch { if (mode === 'latest') status('state-status', '관측 메시지를 읽지 못했습니다. 다시 읽기로 확인하세요.', true); }
  };
  connection.onclose = () => {
    if (document.hidden || socket !== connection) return;
    if (mode === 'latest') status('state-status', '갱신 연결 재시도 중 · 표시 값은 마지막 수신 관측입니다.', true);
    reconnectTimer = setTimeout(connectState, 5000);
  };
}
const kst = (time) => new Date(time).toLocaleString('ko-KR', {timeZone:'Asia/Seoul', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false});
const kstDate = (days = 0) => new Date(Date.now() + 9 * 3600000 + days * 86400000).toISOString().slice(0, 10);
const mw = (value) => Number.isFinite(value) ? `${value.toLocaleString('ko-KR', {maximumFractionDigits:1})} MW` : '—';
function stop() { clearInterval(playTimer); playTimer = 0; el('play').textContent = '재생'; }
function setRange(count) { const range = el('history-time'); range.max = Math.max(count - 1, 0); range.value = range.max; range.disabled = el('play').disabled = !count; }
function showPoint() {
  const point = scenario?.points[el('history-time').value], s = point?.scenario;
  el('history-label').textContent = point ? `${kst(point.observed_at)} KST · ${dayNote}` : dayNote || '—';
  el('sim-net').textContent = point ? `${mw(point.baseline.net_load_mw)} → ${mw(s.net_load_mw)}` : '—';
  el('sim-before').textContent = mw(s?.residual_before_ess_mw); el('sim-after').textContent = mw(s?.residual_after_ess_mw);
  el('sim-ess').textContent = s ? `${mw(s.charge_mw)} / ${mw(s.discharge_mw)}` : '—';
  el('sim-soc').textContent = Number.isFinite(s?.soc_percent) ? `${s.soc_percent.toFixed(1)} %` : '—';
}
async function pick() {
  if (mode === 'scenario') return showPoint();
  const index = Number(el('history-time').value), mine = ++seq;
  selectedAt = times[index]; if (!selectedAt) return;
  el('history-label').textContent = `${kst(selectedAt)} KST · ${index + 1}/${times.length} · ${dayNote}`;
  try {
    const data = await getJson(`/api/v1/jeju/state?at=${encodeURIComponent(selectedAt)}`);
    if (shouldApply(mode, selectedAt, data.observed_at, false, seq, mine)) showState(data);
  } catch (error) { if (mode === 'history' && mine === seq) blank(error.message === 'HTTP 404' ? `${kst(selectedAt)} KST · 해당 시각 관측 없음` : '과거 관측을 읽지 못했습니다.'); }
}
async function loadDay() {
  const mine = ++seq, date = el('history-date').value, day = kstDay(date);
  stop(); times = []; scenario = null; dayNote = ''; setRange(0); showPoint();
  blank(day ? '관측 시각 불러오는 중…' : '날짜를 고르세요.', !day);
  if (!day) return;
  try {
    const list = await getJson(`/api/v1/jeju/timeline?start=${encodeURIComponent(day.start)}&end=${encodeURIComponent(day.end)}`);
    if (mine !== seq) return;
    times = list; dayNote = !list.length ? '자료 없음' : date === kstDate() ? `진행 중 · ${list.length}개 시각` : `${list.length}개 시각 · 누락 ${288 - list.length}`;
    if (mode === 'scenario') { showPoint(); status('state-status', list.length ? '배율·가정을 정하고 실행하세요.' : `${date} 자료 없음`, !list.length); return; }
    setRange(list.length);
    if (list.length) pick(); else { el('history-label').textContent = dayNote; blank(`${date} 자료 없음`); }
  } catch { if (mine === seq) status('state-status', '관측 시각을 읽지 못했습니다.', true); }
}
el('play').onclick = () => { // ponytail: each step supersedes the last /state?at, so replies slower than 1 s show nothing while playing; prefetch the day if that matters
  if (playTimer) return stop();
  const range = el('history-time');
  if (+range.value >= +range.max) { range.value = 0; pick(); }
  el('play').textContent = '정지';
  playTimer = setInterval(() => { if (+range.value >= +range.max) return stop(); range.value = +range.value + 1; pick(); }, 1000);
};
el('history-time').onchange = pick;
el('history-date').value = kstDate(-1); el('history-date').max = kstDate();
el('history-date').onchange = loadDay;
el('assume').onchange = (event) => { el('assumptions').disabled = !event.target.checked; };
for (const radio of document.querySelectorAll('[name=mode]')) radio.onchange = () => {
  mode = radio.value; seq += 1; stop();
  el('state-kind').textContent = {latest:'실제 관측 · 최신', history:'실제 관측 · 과거 KST 시각', scenario:'시뮬레이션 · 실측 아님'}[mode];
  el('time-controls').hidden = mode === 'latest'; el('scenario-form').hidden = el('scenario-metrics').hidden = mode !== 'scenario'; el('metrics').hidden = mode === 'scenario';
  el('state-source').textContent = mode === 'scenario' ? '제주 집계 관측에 배율과 사용자 가정을 적용한 계산 · 실측·예측 아님' : '지역 집계이며 개별 시설의 실측 출력이 아닙니다.';
  if (mode !== 'latest') return loadDay();
  refreshState();
  if (lastLive) showState(lastLive);
};
el('scenario-form').onsubmit = async (event) => {
  event.preventDefault();
  if (!times.length) await loadDay();
  if (!times.length || mode !== 'scenario') return;
  const mine = ++seq, value = (id) => Number(el(id).value), start = times[0], end = new Date(Date.parse(times.at(-1)) + 300000).toISOString();
  const body = {run_id:`web-${Date.now()}`, start, end, scales:{demand:value('scale-demand'), wind:value('scale-wind'), solar:value('scale-solar')}};
  if (el('assume').checked) {
    const hvdc = [1, 2, 3].map((n) => {
      const power = value(`hvdc-${n}-mw`), available = n < 3 || el('hvdc-3-available').checked;
      return {id:`hvdc-${n}`, power_mw:available ? power : 0, available, min_mw:Math.min(0, power), max_mw:Math.max(0, power)};
    });
    body.dispatch = [];
    for (let time = Date.parse(start); time < Date.parse(end); time += 300000) body.dispatch.push({observed_at:new Date(time).toISOString(), nonrenewable_mw:value('g-mw'), hvdc});
    body.ess = {capacity_mwh:value('ess-mwh'), charge_limit_mw:value('ess-mw'), discharge_limit_mw:value('ess-mw'), charge_efficiency:.9, discharge_efficiency:.9, initial_mwh:value('ess-mwh') * value('ess-soc') / 100, min_mwh:0, max_mwh:value('ess-mwh')};
  }
  stop(); scenario = null; setRange(0); showPoint(); status('state-status', '시뮬레이션 계산 중…');
  try {
    const response = await fetch('/api/v1/jeju/simulate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), signal:AbortSignal.timeout(60000)});
    const result = response.ok ? await response.json() : null;
    if (mine !== seq || mode !== 'scenario') return;
    if (!result) return status('state-status', response.status === 429 ? '다른 계산 진행 중 · 잠시 후 재시도' : `시뮬레이션 실패 · HTTP ${response.status}`, true);
    const missing = result.missing_intervals || [];
    scenario = result; setRange(result.points.length); showPoint();
    status('state-status', `시뮬레이션 ${result.status}${missing.length ? ` · 누락 ${missing.length}개: ${missing.slice(0, 3).map(kst).join(', ')} KST` : ''}`, result.status === 'incomplete');
    el('state-source').textContent = `시뮬레이션 · 실측 아님 · 모델 ${result.model_version} · 원천 ${result.input?.source_version || '—'} · 입력 G·HVDC·ESS는 사용자 가정`;
  } catch { if (mine === seq) status('state-status', '시뮬레이션 응답을 읽지 못했습니다.', true); }
};
function stationDistance(coordinates, station) {
  if (!coordinates) return Infinity;
  const [lon, lat] = coordinates.map(THREE.MathUtils.degToRad);
  const otherLat = THREE.MathUtils.degToRad(station.latitude);
  const a = Math.sin((otherLat - lat) / 2) ** 2 + Math.cos(lat) * Math.cos(otherLat) * Math.sin((THREE.MathUtils.degToRad(station.longitude) - lon) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(THREE.MathUtils.clamp(a, 0, 1)));
}
function closestWind(readings, coordinates) {
  const sorted = readings.map((r) => ({...r, distance:stationDistance(coordinates, r.station)})).sort((a, b) => a.distance - b.distance || a.station.id - b.station.id);
  return {sorted, selected:sorted.find((r) => r.fresh) || sorted[0]};
}
function showWind() {
  clearTimeout(windExpiryTimer);
  const now = Date.now();
  const readings = (Array.isArray(windData?.stations) ? windData.stations : []).filter((r) =>
    Number.isInteger(r.station?.id) && Number.isFinite(r.station.latitude) && Math.abs(r.station.latitude) <= 90 && Number.isFinite(r.station.longitude) && Math.abs(r.station.longitude) <= 180
  ).map((data) => {
    const observed = Date.parse(data.observed_at), received = Date.parse(data.received_at);
    const directional = Number.isFinite(data.direction_from_deg) && data.direction_from_deg >= 0 && data.direction_from_deg < 360;
    const valid = Number.isFinite(observed) && Number.isFinite(received) && Number.isFinite(data.speed_m_s) && data.speed_m_s >= 0 &&
      (data.speed_m_s === 0 || directional) && data.average_window_minutes === 10 && data.directional_resolution_deg === 22.5;
    const expires = Math.min(observed + 900000, received + 150000);
    const fresh = valid && !windFetchFailed && data.status === 'fresh' && now < expires && observed <= now + 60000 && received <= now + 60000;
    return {...data, observed, valid, fresh, expires, directional:fresh && data.speed_m_s > 0 && directional};
  });
  const coordinates = selected?.coordinates || grid?.data.coordinateFrame.origin_lon_lat;
  const {sorted, selected:data} = closestWind(readings, coordinates);
  el('wind-station').textContent = data ? `${data.station.name}(${data.station.id}) · ${Number.isFinite(data.distance) ? data.distance.toFixed(2) + ' km' : '거리 미확인'}` : '제주 관측 바람';
  el('wind-selection').textContent = selected?.coordinates ? `기준 시설: ${selected.name}` : '기준: 장면 중심 좌표';
  el('wind-speed').textContent = data?.valid ? `${data.speed_m_s.toLocaleString('ko-KR', {maximumFractionDigits:1})} m/s` : '—';
  el('wind-direction').textContent = data?.valid ? data.speed_m_s === 0 ? '정온 · 풍향 없음' : `${data.direction_label} · 약 ${data.direction_from_deg}°에서` : '—';
  el('wind-time').textContent = data?.valid ? `${new Date(data.observed).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'})} KST 관측` : '관측 시각 —';
  el('wind-nearest-note').textContent = data && data !== sorted[0] ? '가장 가까운 관측소의 자료가 유효하지 않아 다음 관측소를 사용합니다.' : '';
  status('wind-status', data?.fresh ? '최근 관측 · 연결됨 / 최신값 자동 반영' : data?.valid ? '갱신 지연 · 마지막 관측 표시' : '유효 관측 없음 · 자동 재시도', !data?.fresh);
  const directions = new Map();
  const estimates = new Map();
  for (const facility of grid?.data.facilities.filter((f) => f.kind === 'wind') || []) {
    const reading = closestWind(readings, facility.coordinates).selected;
    if (reading?.directional) directions.set(facility.id, reading.direction_from_deg);
    if (reading?.fresh) estimates.set(facility.id, estimateWind(reading.speed_m_s));
  }
  rotorRPM = new Map([...estimates].map(([id, estimate]) => [id, estimate?.rpm || 0]));
  const estimate = selected?.kind === 'wind' ? estimates.get(selected.id) : null;
  el('wind-estimate').hidden = selected?.kind !== 'wind';
  el('wind-estimate-power').textContent = estimate ? `${Math.round(estimate.powerKW).toLocaleString('ko-KR')} kW` : '—';
  el('wind-estimate-rpm').textContent = estimate ? `${estimate.rpm.toFixed(1)} RPM` : '—';
  el('wind-estimate-status').textContent = !estimate ? '최근 유효 관측 없음 · 추정 보류' : estimate.condition === 'below' ? '가정한 시동 풍속 3 m/s 미만 · 가능 출력 0' : estimate.condition === 'above' ? '가정한 정지 풍속 25 m/s 이상 · 가능 출력 0' : '가정 범위 내 가능 출력 · 실제 발전량 아님';
  syncDemo();
  const ready = !!grid && !contextLost && grid.visible.wind && directions.size > 0;
  el('wind-follow').disabled = !ready;
  const follow = ready && el('wind-follow').checked;
  grid?.setWindDirections(follow ? directions : new Map());
  el('wind-follow-status').textContent = follow ? `${directions.size}기 방향 시연 · 시설별 인근 관측 / 실제 터빈 방향·RPM 아님` : '방향 시연 정지 · 기본 추정 방향 표시';
  document.body.dataset.wind = data?.fresh ? data.speed_m_s === 0 ? 'calm' : 'fresh' : data?.valid ? 'stale' : 'unavailable';
  document.body.dataset.windFollowing = String(follow);
  document.body.dataset.windFollowingCount = String(follow ? directions.size : 0);
  el('wind-station-count').textContent = `제주 전체 ${readings.length}개 관측소 · 유효 ${readings.filter((r) => r.fresh).length}개`;
  el('wind-stations').replaceChildren();
  for (const reading of sorted) {
    const row = document.createElement('tr'); row.dataset.stationId = String(reading.station.id);
    row.setAttribute('aria-current', String(reading === data));
    const cells = [
      [`${reading.station.name}(${reading.station.id})`, Number.isFinite(reading.distance) ? `${reading.distance.toFixed(2)} km` : '거리 미확인'],
      [reading.valid ? `${reading.speed_m_s.toLocaleString('ko-KR')} m/s · ${reading.speed_m_s === 0 ? '정온' : reading.direction_label}` : '—', reading.fresh ? '최근 관측' : reading.valid ? '지연' : '결측'],
      reading.valid ? [new Date(reading.observed).toLocaleDateString('ko-KR', {timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit'}), new Date(reading.observed).toLocaleTimeString('ko-KR', {timeZone:'Asia/Seoul',hour12:false,hour:'2-digit',minute:'2-digit'})] : ['—'],
    ];
    for (const values of cells) {
      const cell = document.createElement('td'); cell.textContent = values[0];
      if (values.length > 1) { const detail = document.createElement('span'); detail.textContent = values[1]; cell.append(detail); }
      row.append(cell);
    }
    el('wind-stations').append(row);
  }
  const expiries = readings.filter((r) => r.fresh).map((r) => r.expires);
  if (expiries.length && !document.hidden) windExpiryTimer = setTimeout(showWind, Math.min(...expiries) - now + 1);
  render();
}
function connectWind() {
  clearTimeout(windReconnectTimer);
  if (document.hidden || windSocket?.readyState <= 1) return;
  const connection = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/v1/jeju/wind/ws`);
  windSocket = connection;
  const deadline = (milliseconds) => {
    clearTimeout(windMessageTimer);
    windMessageTimer = setTimeout(() => {
      if (windSocket !== connection) return;
      windFetchFailed = true; showWind(); connection.close();
    }, milliseconds);
  };
  deadline(15000);
  connection.onmessage = (event) => {
    if (windSocket !== connection || document.hidden) return;
    try {
      const data = JSON.parse(event.data);
      if (!Array.isArray(data?.stations) || !Number.isInteger(data.station_count) || data.station_count !== data.stations.length || data.stations.some((r) => !r || typeof r.station !== 'object' || !r.station)) throw new Error('Invalid wind snapshot');
      windData = data; windFetchFailed = false; showWind(); deadline(90000);
    } catch { windFetchFailed = true; showWind(); connection.close(); }
  };
  connection.onerror = () => {
    if (windSocket !== connection) return;
    windFetchFailed = true; showWind(); connection.close();
  };
  connection.onclose = () => {
    if (windSocket !== connection) return;
    clearTimeout(windMessageTimer);
    windFetchFailed = true; showWind();
    if (!document.hidden) windReconnectTimer = setTimeout(connectWind, 5000);
  };
}
function disconnectWind() {
  clearTimeout(windReconnectTimer); clearTimeout(windMessageTimer); clearTimeout(windExpiryTimer);
  const previous = windSocket; windSocket = null; previous?.close();
  windFetchFailed = true; showWind();
}
function refreshWind() { disconnectWind(); connectWind(); }
el('wind-follow').onchange = showWind;
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { stopDemo(); stop(); clearTimeout(reconnectTimer); disconnectWind(); socket?.close(); }
  else { showWind(); connectWind(); render(); refreshState(); if (!socket || socket.readyState > 1) connectState(); }
});
el('refresh').onclick = () => { refreshState(); refreshWind(); };
startScene(); refreshState(); connectState(); connectWind();
