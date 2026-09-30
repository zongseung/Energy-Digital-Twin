'use strict';
const $ = (id) => document.getElementById(id);
const bounds = [126.155, 33.325, 126.19, 33.36];
const metricKeys = ['demand_mw', 'supply_capacity_mw', 'wind_mw', 'solar_mw', 'renewable_total_mw'];
let viewer;
let facilities = [];
let selectedId;
let buildings;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function status(id, text, failed = false) {
  const node = $(id);
  node.textContent = text;
  node.classList.toggle('error', failed);
  node.setAttribute('role', failed ? 'alert' : 'status');
}
async function getJson(url) {
  const response = await fetch(url, {signal: AbortSignal.timeout(20000)});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
function kind(feature) {
  const p = feature.properties;
  if (p.power_type === 'plant' && p.plant_source === 'wind') return '풍력단지 위치';
  if (p.plant_method === 'wind_turbine') return '개별 풍력 터빈';
  return p.facility_kind || p.power_type || '시설 위치';
}
function name(feature) { return feature.properties.name || feature.properties.name_en || `${kind(feature)} · ${feature.properties.source_id ?? feature.id}`; }
function focusAt(longitude, latitude, range = 1500, heading = 135) {
  if (!viewer) return;
  const C = window.Cesium;
  viewer.camera.lookAt(C.Cartesian3.fromDegrees(longitude, latitude, 0), new C.HeadingPitchRange(C.Math.toRadians(heading), C.Math.toRadians(-35), range));
  viewer.camera.lookAtTransform(C.Matrix4.IDENTITY);
}
function select(feature) {
  selectedId = feature.id;
  $('selected-title').textContent = name(feature);
  $('selection-status').textContent = `${kind(feature)} 원천 위치 선택됨 · 실제 3D 모델 식별 여부는 미확인`;
  const details = $('selected-details');
  details.replaceChildren();
  const p = feature.properties;
  const values = [['원천 ID', feature.id], ['원천 테이블', p.source_table], ['종류', kind(feature)], ['경도 · 위도', feature.geometry.coordinates.join(', ')], ['원천 출력 속성', p.plant_output], ['품질 표시', JSON.stringify(p.quality_flags || [])]];
  for (const [label, value] of values) {
    const dt = document.createElement('dt'); dt.textContent = label;
    const dd = document.createElement('dd'); dd.textContent = value == null ? '—' : String(value);
    details.append(dt, dd);
  }
  $('properties').textContent = JSON.stringify(p, null, 2);
  renderFacilities();
  $('assets').querySelector('[aria-pressed="true"]')?.focus({preventScroll: true});
  focusAt(...feature.geometry.coordinates.slice(0, 2), 900);
}
function renderFacilities() {
  const query = $('search').value.trim().toLocaleLowerCase();
  const visible = facilities.filter((f) => `${name(f)} ${f.id} ${kind(f)}`.toLocaleLowerCase().includes(query));
  $('assets').replaceChildren();
  for (const feature of visible) {
    const li = document.createElement('li');
    const button = document.createElement('button'); button.className = 'facility'; button.type = 'button';
    button.setAttribute('aria-pressed', String(selectedId === feature.id));
    const title = document.createElement('strong'); title.textContent = name(feature);
    const subtitle = document.createElement('small'); subtitle.textContent = `${selectedId === feature.id ? '선택됨 · ' : ''}${kind(feature)} · ${feature.id}`;
    button.append(title, subtitle); button.addEventListener('click', () => select(feature)); li.append(button); $('assets').append(li);
  }
  status('assets-status', visible.length ? `${visible.length}개 / 신창 구역 시설 위치 ${facilities.length}개` : '조건에 맞는 시설이 없습니다.');
}
async function loadFacilities() {
  try {
    const data = await getJson('/api/v1/jeju/assets');
    if (!Array.isArray(data.features)) throw new Error('invalid features');
    facilities = data.features.filter((f) => {
      if (f.geometry?.type !== 'Point' || !f.properties || f.id == null) return false;
      const [x, y] = f.geometry.coordinates;
      return Number.isFinite(x) && Number.isFinite(y) && x >= bounds[0] && x <= bounds[2] && y >= bounds[1] && y <= bounds[3];
    });
    $('assets-source').textContent = `시설 출처: ${data.source || '—'} · 생성 시각: ${data.generated_at || '—'}`;
    renderFacilities();
  } catch {
    status('assets-status', '시설 API를 불러오지 못했습니다. 지도를 계속 탐색할 수 있습니다.', true);
  }
}
async function refreshState() {
  $('refresh').disabled = true;
  status('state-status', '최신 집계 불러오는 중');
  try {
    const data = await getJson('/api/v1/jeju/state');
    if (!data || typeof data !== 'object' || !data.observed_at) throw new Error('invalid state');
    for (const key of metricKeys) $(key).textContent = Number.isFinite(data[key]) ? `${data[key].toLocaleString('ko-KR', {maximumFractionDigits: 2})} MW` : '—';
    const time = new Date(data.observed_at);
    status('state-status', `${data.quality_flags?.includes('source_delayed') ? '원천 갱신 지연 · ' : ''}관측 시각 ${Number.isNaN(time.getTime()) ? data.observed_at : time.toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'})} KST`);
    $('state-source').textContent = `출처: ${data.source || '—'} · 품질 표시: ${JSON.stringify(data.quality_flags || [])} · 지역 집계이며 개별 시설 출력이 아닙니다.`;
  } catch {
    for (const key of metricKeys) $(key).textContent = '—';
    $('state-source').textContent = '';
    status('state-status', '수급 API를 불러오지 못했습니다. 새로고침으로 다시 시도하세요.', true);
  } finally { $('refresh').disabled = false; }
}
async function loadBuildings() {
  try {
    const C = window.Cesium;
    const data = await getJson('/site/buildings.geojson');
    const source = await C.GeoJsonDataSource.load(data, {clampToGround: true});
    let count = 0;
    for (const entity of source.entities.values) {
      const height = entity.properties?.height_m?.getValue();
      if (!entity.polygon || !Number.isFinite(height) || height <= 0) { entity.show = false; continue; }
      entity.polygon.height = 0;
      entity.polygon.heightReference = C.HeightReference.CLAMP_TO_GROUND;
      entity.polygon.extrudedHeight = height;
      entity.polygon.extrudedHeightReference = C.HeightReference.RELATIVE_TO_GROUND;
      entity.polygon.material = C.Color.fromCssColorString(getComputedStyle(document.documentElement).getPropertyValue('--muted').trim()).withAlpha(0.85);
      entity.polygon.outline = false;
      count++;
    }
    buildings = await viewer.dataSources.add(source);
    buildings.show = $('building-layer').checked;
    status('building-status', `${count}개 건물 윤곽·제공 높이로 구성한 개략 외형 · 외벽 실사 텍스처 아님`);
  } catch {
    status('building-status', '높이 건물 레이어를 불러오지 못했습니다. VWorld 배경은 유지됩니다.', true);
  }
}
async function startMap() {
  let stage = 'sdk-ready';
  try {
    const deadline = Date.now() + 30000;
    while (!window.vw?.Map) {
      if (window.vworldLoadFailed || Date.now() > deadline) throw new Error('SDK unavailable');
      await delay(100);
    }
    stage = 'construct';
    const map = new window.vw.Map();
    stage = 'set-option';
    map.setOption({mapId: 'map', initPosition: new window.vw.CameraPosition(new window.vw.CoordZ(126.17217, 33.3430267, 1800), new window.vw.Direction(0, -60, 0)), logo: true, navigation: true});
    stage = 'set-map-id';
    map.setMapId('map');
    stage = 'start';
    map.start();
    stage = 'wait-scene';
    await delay(5000);
    while (!window.ws3d?.viewer?.scene?.globe || !window.Cesium || !window.ws3d.viewer.scene.globe.tilesLoaded) {
      if (Date.now() > deadline) throw new Error('scene unavailable');
      await delay(200);
    }
    viewer = window.ws3d.viewer;
    stage = 'focus';
    focusAt(126.177, 33.344, 2200, 135);
    $('overview').disabled = false; $('coast').disabled = false;
    $('map-status').hidden = true;
    document.body.dataset.ready = 'true';
    loadBuildings();
  } catch (error) {
    document.body.dataset.mapFailureStage = stage;
    document.body.dataset.mapFailureType = error instanceof TypeError ? 'TypeError' : error instanceof ReferenceError ? 'ReferenceError' : 'Error';
    status('map-status', 'VWorld 3D 장면을 표시하지 못했습니다. SDK 연결·키의 등록 도메인·WebGL 지원을 확인하세요. 시설 목록과 최신 집계는 계속 사용할 수 있습니다.', true);
    document.body.dataset.ready = 'false';
  }
}
$('overview').addEventListener('click', () => focusAt(126.177, 33.344, 2200, 135));
$('coast').addEventListener('click', () => focusAt(126.17217, 33.3430267, 1300, 135));
$('building-layer').addEventListener('change', () => { if (buildings) buildings.show = $('building-layer').checked; });
$('search').addEventListener('input', renderFacilities);
$('refresh').addEventListener('click', refreshState);
loadFacilities(); refreshState(); startMap();
