import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {loadGrid} from '/twin/grid.js?v=map-first-20260930';
import {createNavigation} from '/twin/navigation.mjs?v=map-first-20260930';
import {estimateWind} from '/twin/wind-estimate.mjs';
import {shouldApply, kstDay} from '/twin/playback.mjs';

const el = (id) => document.getElementById(id);
const metricKeys = ['demand_mw', 'supply_capacity_mw', 'wind_mw', 'solar_mw', 'renewable_total_mw'];
const layers = ['wind', 'transmission', 'substation', 'pv', 'terrain', 'buildings', 'sea', 'roads', 'vegetation', 'groundcover', 'harbours'];
const sceneControls = ['inspect', 'north', 'overview', 'wind-view', 'terrain-relief', 'terrain-view', 'pv-view', 'buildings-view', 'sea-view', 'harbour-view', 'pitch-view', 'rotate', 'zoom-in', 'zoom-out', 'rotor-demo'];
const kindNames = {wind:'풍력',transmission:'송전',hvdc:'HVDC',cable:'케이블',substation:'변전소',pv:'태양광',building:'건물'};
const markers = new Map(), reducedMotion = matchMedia('(prefers-reduced-motion:reduce)');
let renderer, scene, camera, controls, selected, grid, navigation, panelTool = null, returnFocus = null;
let textFacilities = [];
let demoFrame = 0, previousTime = 0, drawing = false, socket, reconnectTimer, contextLost = false;
let windData = null, windSocket, windReconnectTimer, windMessageTimer, windExpiryTimer, windFetchFailed = false;
let rotorRPM = new Map();
let mode = 'latest', seq = 0, genSeq = 0, times = [], lastLive = null, selectedAt = null, playTimer, scenario = null, dayNote = '';

function status(id, message, failed = false) {
  el(id).textContent = message;
  el(id).classList.toggle('error', failed);
  el(id).setAttribute('role', failed ? 'alert' : 'status');
}
async function getJson(url) {
  const response = await fetch(url, {signal:AbortSignal.timeout(20000), cache:'no-store'});
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.json();
}
function render() {
  if (!demoFrame && !drawing && renderer && !contextLost && !document.hidden) demoFrame = requestAnimationFrame(animate);
}
function cameraAt(view, scale = 1) {
  if (!view || !navigation || contextLost) return;
  navigation.moveTo(view, Math.max(1, 650 / el('viewport').clientWidth) * scale);
  el('view-menu').open = false;
  render();
}
function inspect() {
  if (selected && navigation && !contextLost) cameraAt(selected.view, selected.kind === 'wind' ? 1.35 : 1);
}
function setSheetHeight(percent) {
  const height = THREE.MathUtils.clamp(percent, 40, 70);
  document.body.style.setProperty('--sheet-height', height + '%');
  el('sheet-toggle').setAttribute('aria-expanded', String(height > 55));
  el('sheet-toggle').textContent = height > 55 ? '축소하기' : '펼치기';
}
function openPanel(tool, origin = document.activeElement) {
  if (el('info-panel').hidden) returnFocus = origin;
  panelTool = tool; document.body.dataset.panel = tool;
  el('info-panel').hidden = false;
  for (const section of el('panel-body').querySelectorAll('[data-panel]')) section.hidden = section.dataset.panel !== tool;
  el('panel-title').textContent = {detail:'선택 시설',facilities:'시설 탐색',layers:'표시 레이어',weather:'현재 기상',analysis:'지역 분석',sources:'자료 출처',help:'지도 도움말'}[tool];
  el('panel-back').hidden = !selected || tool === 'detail';
  for (const button of document.querySelectorAll('[data-tool]')) button.setAttribute('aria-expanded', String(button.dataset.tool === tool));
  (tool === 'detail' ? el('detail-weather') : el('weather-home')).append(document.querySelector('.wind-observation'));
  el('panel-body').scrollTop = 0;
  el('view-menu').open = false; el('pick-candidates').hidden = true;
  el('panel-title').focus({preventScroll:true});
  render();
}
function closePanel(clear = true) {
  ++genSeq;
  el('info-panel').hidden = true; panelTool = null; document.body.dataset.panel = '';
  for (const button of document.querySelectorAll('[data-tool]')) button.setAttribute('aria-expanded','false');
  el('weather-home').append(document.querySelector('.wind-observation'));
  el('pick-candidates').hidden = true;
  setSheetHeight(40);
  if (clear) {
    if (selected?.kind === 'building') { markers.get(selected.id)?.button.remove(); markers.delete(selected.id); }
    selected = null; delete document.body.dataset.selected;
    grid?.highlight(null);
    el('scene-title').textContent = '탐라–한림 통합 구역';
    el('scene-note').textContent = '시설을 클릭해 정보를 확인하세요';
    showWind(); populate();
  }
  const target = returnFocus?.isConnected && returnFocus.getClientRects().length ? returnFocus : el('viewport');
  target.focus({preventScroll:true}); render();
}
function pointOf(record) { return new THREE.Vector3().fromArray(record.view.target); }
function updateMarkers() {
  if (!grid || !camera) return;
  camera.updateMatrixWorld();
  for (const {button,record} of markers.values()) {
    const p = pointOf(record).project(camera), active = selected?.id === record.id;
    button.hidden = !grid.visible[record.layer] || p.z < -1 || p.z > 1 || Math.abs(p.x) > 1 || Math.abs(p.y) > 1;
    button.style.transform = 'translate(' + ((p.x + 1) / 2 * el('viewport').clientWidth - 22) + 'px,' + ((1 - p.y) / 2 * el('viewport').clientHeight - 22) + 'px)';
    button.setAttribute('aria-pressed', String(active));
  }
  el('compass-arrow').style.transform = 'rotate(' + (-controls.getAzimuthalAngle() * 180 / Math.PI) + 'deg)';
}
function marker(record) {
  const button = document.createElement('button'), glyph = document.createElement('span'), label = document.createElement('span');
  button.className = 'map-marker'; button.dataset.id = record.id;
  button.setAttribute('aria-label', record.name + ' · ' + kindNames[record.kind] + ' · 상세 보기');
  button.setAttribute('aria-pressed','false');
  glyph.className = 'marker-glyph ' + ({wind:'wind',pv:'pv',substation:'station'}[record.kind] || 'transmission');
  const paths = {wind:'M12 13v9M12 13 5 7m7 6 8-4m-8 4 1-10',pv:'m6 4-3 12h18L18 4H6Zm6 0v12M5 10h14M12 16v5m-5 0h10',substation:'M4 5h16M7 5v6m10-6v6M4 19h16M7 15v4m10-4v4M4 11h6v4H4Zm10 0h6v4h-6Z',building:'M4 21V8l8-5 8 5v13H4Zm5 0v-7h6v7',transmission:'M3 18h5V7h8V3m0 4h5M3 15v6m18-17v6'};
  glyph.innerHTML = '<svg viewBox="0 0 24 24"><path d="' + (paths[record.kind] || paths.transmission) + '"/></svg>';
  glyph.setAttribute('aria-hidden','true');
  label.className = 'marker-label'; label.textContent = record.name;
  button.append(glyph,label); button.onclick = event => {
    const overlaps = event.detail ? [...markers.values()].filter(({button}) => {
      if (button.hidden) return false;
      const box = button.getBoundingClientRect();
      return event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
    }).map(item => item.record) : [record];
    if (overlaps.length > 1) showCandidates(overlaps); else choose(record);
  };
  button.ondblclick = () => {choose(record);inspect();};
  el('map-markers').append(button); markers.set(record.id,{button,record});
}
function keepSelectionVisible() {
  if (!selected || !navigation || contextLost || el('info-panel').hidden) return;
  const box = el('viewport').getBoundingClientRect(), panel = el('info-panel').getBoundingClientRect();
  const world = pointOf(selected), p = world.clone().project(camera);
  const x = box.left + (p.x + 1) * box.width / 2, y = box.top + (1 - p.y) * box.height / 2;
  if (p.z < -1 || p.z > 1 || x < panel.left || x > panel.right || y < panel.top || y > panel.bottom) return;
  const target = p.clone();
  if (innerWidth < 768) target.y = 1 - 2 * ((panel.top - box.top) / 2) / box.height;
  else target.x = 2 * ((panel.left - box.left) / 2) / box.width - 1;
  const raycaster = new THREE.Raycaster(), plane = new THREE.Plane(new THREE.Vector3(0,1,0), -world.y);
  raycaster.setFromCamera(new THREE.Vector2(target.x,target.y),camera);
  const destination = raycaster.ray.intersectPlane(plane,new THREE.Vector3());
  if (!destination) return;
  const shift = world.sub(destination);
  if (!Number.isFinite(shift.length()) || shift.length() > grid.bounds.getSize(new THREE.Vector3()).length()) return;
  navigation.cancel(); controls.target.add(shift); camera.position.add(shift); controls.update(); render();
}
function choose(record, focus = true) {
  if (!record || (grid && !grid.visible[record.layer])) return;
  if (selected?.kind === 'building' && selected.id !== record.id) { markers.get(selected.id)?.button.remove(); markers.delete(selected.id); }
  selected = record; document.body.dataset.selected = record.id;
  el('selected-name').textContent = record.name;
  el('scene-title').textContent = record.name;
  el('scene-note').textContent = '선택 시설 · 실제 위치 / 형상 추정';
  status('selection-status', kindNames[record.kind] + ' · ' + record.id);
  el('operation-summary').hidden = record.kind === 'building';
  el('rotor-demo-control').hidden = record.kind !== 'wind';
  el('facility-detail').replaceChildren();
  for (const [label,value] of record.rows) {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = label; dd.textContent = value; el('facility-detail').append(dt,dd);
  }
  showGeneration();
  for (const button of el('facilities').querySelectorAll('button')) button.setAttribute('aria-pressed',String(button.dataset.id === record.id));
  if (grid && !markers.has(record.id)) marker(record);
  grid?.highlight(record); showWind();
  if (focus) { setSheetHeight(40); openPanel('detail'); requestAnimationFrame(keepSelectionVisible); }
  render();
}
function showCandidates(records) {
  el('pick-list').replaceChildren();
  for (const record of records) {
    const button = document.createElement('button'); button.textContent = record.name + ' · ' + kindNames[record.kind];
    button.onclick = () => choose(record); el('pick-list').append(button);
  }
  el('pick-candidates').hidden = false; el('pick-list').firstElementChild.focus({preventScroll:true});
}
function populate() {
  if (!grid && !textFacilities.length) return;
  const search = el('facility-search').value.trim().toLocaleLowerCase('ko-KR'), filter = el('facility-filter').value;
  const records = (grid?.records || textFacilities).filter(r => (!grid || grid.visible[r.layer]) && (!filter || r.layer === filter) && (r.name + ' ' + r.id).toLocaleLowerCase('ko-KR').includes(search));
  el('facilities').replaceChildren();
  for (const record of records) {
    const li = document.createElement('li'), button = document.createElement('button'), name = document.createElement('strong'), tag = document.createElement('small');
    button.className = 'facility'; button.dataset.id = record.id; button.setAttribute('aria-pressed',String(selected?.id === record.id));
    name.textContent = record.name; tag.textContent = kindNames[record.kind]; button.append(name,tag);
    button.onclick = () => choose(record); li.append(button); el('facilities').append(li);
  }
  el('facility-count').textContent = records.length + '개 시설 · 표시 레이어 기준';
  el('facility-empty').hidden = !!records.length; el('facility-reset').hidden = !!records.length && !search && !filter;
  for (const record of grid?.records || []) if (!markers.has(record.id)) marker(record);
  if (selected && grid && !grid.visible[selected.layer]) closePanel();
  render();
}
for (const button of document.querySelectorAll('[data-tool]')) button.onclick = () => {
  if (panelTool === button.dataset.tool) closePanel(false);
  else openPanel(button.dataset.tool,button);
};
el('panel-close').onclick = () => closePanel();
el('panel-back').onclick = () => { if (selected) openPanel('detail'); };
el('facility-search').oninput = populate; el('facility-filter').onchange = populate;
el('facility-reset').onclick = () => {el('facility-search').value='';el('facility-filter').value='';populate();};
el('sheet-toggle').onclick = () => setSheetHeight(el('sheet-toggle').getAttribute('aria-expanded') === 'true' ? 40 : 70);
let sheetDrag = null, sheetMoved = false;
el('sheet-toggle').addEventListener('pointerdown',event => {
  sheetDrag = {id:event.pointerId,y:event.clientY,height:el('info-panel').clientHeight}; sheetMoved=false;
  el('sheet-toggle').setPointerCapture(event.pointerId);
});
el('sheet-toggle').addEventListener('pointermove',event => {
  if (!sheetDrag || sheetDrag.id !== event.pointerId) return;
  const delta = sheetDrag.y - event.clientY;
  if (Math.abs(delta)>5) sheetMoved=true;
  if (sheetMoved) setSheetHeight((sheetDrag.height + delta) / el('viewport').clientHeight * 100);
});
el('sheet-toggle').addEventListener('click',event => {if(sheetMoved){event.stopImmediatePropagation();sheetMoved=false;}},true);
el('sheet-toggle').addEventListener('pointerup',()=>{sheetDrag=null;});
el('sheet-toggle').addEventListener('pointercancel',()=>{sheetDrag=null;sheetMoved=false;});
document.addEventListener('keydown',event => {
  if (event.key !== 'Escape') return;
  if (!el('pick-candidates').hidden) el('pick-candidates').hidden=true;
  else if (el('view-menu').open) el('view-menu').open=false;
  else if (!el('info-panel').hidden) closePanel();
});

async function startScene() {
  try {
    renderer = new THREE.WebGLRenderer({antialias:true});
    renderer.setPixelRatio(Math.min(devicePixelRatio,2)); renderer.toneMapping=THREE.ACESFilmicToneMapping; renderer.toneMappingExposure=.9;
    scene=new THREE.Scene(); scene.background=new THREE.Color(getComputedStyle(document.body).getPropertyValue('--map-bg').trim());
    camera=new THREE.PerspectiveCamera(48,1,.5,70000);
    const pmrem=new THREE.PMREMGenerator(renderer), environment=new RoomEnvironment();
    scene.environment=pmrem.fromScene(environment,.04).texture; scene.environmentIntensity=.3; environment.dispose();pmrem.dispose();
    scene.add(new THREE.HemisphereLight(0xdceeff,0x475354,.7));
    const sun=new THREE.DirectionalLight(0xfff4dd,2);sun.position.set(1000,1500,600);scene.add(sun);
    el('viewport').append(renderer.domElement);renderer.domElement.setAttribute('aria-label','탐라–한림 3D 지도');
    renderer.domElement.addEventListener('webglcontextlost',event => {
      event.preventDefault();contextLost=true;stopDemo();navigation?.cancel();el('rotor-demo').checked=false;controls.enabled=false;
      for(const id of sceneControls)el(id).disabled=true;
      document.body.dataset.ready='false';el('map-status').hidden=false;showWind();
      status('map-status','3D 연결이 중단되었습니다. 새로고침하세요. 목록·기상 정보는 계속 확인할 수 있습니다.',true);
    });
    controls=new OrbitControls(camera,renderer.domElement);
    controls.addEventListener('change',render);
    const resize=()=>{const {clientWidth:w,clientHeight:h}=el('viewport');if(!w||!h)return;renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();grid?.resize(w,h);render();};
    new ResizeObserver(resize).observe(el('viewport'));resize();
    grid=await loadGrid(render);
    scene.add(grid.object);
    el('buildings-count').textContent=' · '+grid.data.buildings.count.toLocaleString('ko-KR')+'동';el('buildings-count').hidden=false;
    const initial=grid.data.cameras.array;controls.target.fromArray(initial.target);
    camera.position.fromArray(initial.position).sub(controls.target).multiplyScalar(Math.max(1,650/el('viewport').clientWidth)).add(controls.target);
    camera.fov=initial.fov||48;camera.updateProjectionMatrix();controls.update();
    navigation=createNavigation({camera,controls,canvas:renderer.domElement,bounds:grid.bounds,groundHeight:grid.groundHeight,reducedMotion});
    grid.resize(el('viewport').clientWidth,el('viewport').clientHeight);populate();showWind();
    if(contextLost)return;
    const pointers=new Set();let down=null,dragged=false;
    const at=event => {const box=renderer.domElement.getBoundingClientRect();return new THREE.Vector2((event.clientX-box.left)/box.width*2-1,-(event.clientY-box.top)/box.height*2+1);};
    renderer.domElement.addEventListener('pointerdown',event => {
      pointers.add(event.pointerId);
      if(pointers.size>1||event.button!==0){down=null;return;}
      down={id:event.pointerId,x:event.clientX,y:event.clientY};dragged=false;
    });
    renderer.domElement.addEventListener('pointermove',event => {if(down&&Math.hypot(event.clientX-down.x,event.clientY-down.y)>5)dragged=true;});
    renderer.domElement.addEventListener('pointercancel',event => {pointers.delete(event.pointerId);down=null;});
    renderer.domElement.addEventListener('pointerup',event => {
      pointers.delete(event.pointerId);
      const candidate=down;down=null;
      if(!candidate||candidate.id!==event.pointerId||dragged||event.button!==0||Math.hypot(event.clientX-candidate.x,event.clientY-candidate.y)>5)return;
      const hits=grid.pick(camera,at(event));
      if(!hits.length){closePanel();return;}
      if(hits.length===1){choose(hits[0]);return;}
      showCandidates(hits);
    });
    renderer.domElement.addEventListener('dblclick',event => {const hit=grid.pick(camera,at(event),{firstOnly:true})[0];if(hit){choose(hit);inspect();}});
    el('pick-close').onclick=()=>{el('pick-candidates').hidden=true;el('viewport').focus({preventScroll:true});};
    for(const id of sceneControls)el(id).disabled=false;
    for(const layer of layers)Object.assign(el('layer-'+layer),{disabled:!(layer in grid.visible),checked:!!grid.visible[layer]});
    el('map-status').hidden=true;document.body.dataset.ready='true';render();
  } catch(error) {
    console.error('Local scene failed',error);document.body.dataset.ready='false';el('map-status').hidden=false;
    for (const id of sceneControls) el(id).disabled = true;
    status('map-status','통합 지형·시설을 불러오지 못했습니다. 새로고침하세요. 시설 목록·기상은 별도로 확인할 수 있습니다.',true);
    if (!grid) {
      try {
        const data = await getJson('/local/manifest.json');
        textFacilities = [...data.facilities.filter(f => f.kind !== 'line'), ...data.routes].map(f => ({
          ...f, layer:data.routes.includes(f) ? 'transmission' : f.kind,
          name:f.name || f.source_properties?.name || f.source_properties?.name_en || `시설 ${f.id.split(':').at(-1)}`,
          rows:[['원천 ID',f.id], ['좌표',f.coordinates?.map(v => Number(v).toFixed(6)).join(', ') || '경로 자료'], ['지도 상태','3D 표시 불가 · 목록 정보만 표시']],
        }));
        populate();
      } catch {
        el('facility-empty').hidden = false;
        el('facility-empty').textContent = '시설 목록을 가져오지 못했습니다. 새로고침하세요.';
      }
    }
  }
}
function stopDemo(){cancelAnimationFrame(demoFrame);demoFrame=0;previousTime=0;}
function demoActive(){return !!grid&&grid.visible.wind&&el('rotor-demo').checked&&grid.rotors.some(rotor=>(rotorRPM.get(rotor.userData.facilityId)||0)>0);}
function syncDemo(){render();}
function animate(time){
  demoFrame=0;if(document.hidden||contextLost)return;
  drawing=true;
  const delta=previousTime?Math.min((time-previousTime)/1000,.1):1/60;previousTime=time;
  const moving=navigation?.update(delta)||false, demo=demoActive();
  if(demo)for(const rotor of grid.rotors)rotor.rotateX(delta*(rotorRPM.get(rotor.userData.facilityId)||0)*Math.PI*2/60);
  grid?.update(camera,controls.target);renderer.render(scene,camera);updateMarkers();
  drawing=false;
  if(moving||demo)render();else previousTime=0;
}
function enableLayer(layer){
  if(!grid.visible[layer]){grid.setLayer(layer,true);el('layer-'+layer).checked=true;populate();}
  if(layer==='wind'){el('rotor-demo').disabled=contextLost;showWind();}
}
el('rotor-demo').onchange=syncDemo;el('inspect').onclick=inspect;
el('north').onclick=()=>{navigation.north();render();};
el('rotate').onclick=()=>{navigation.rotate();render();};
for(const [id,factor]of [['zoom-in',.8],['zoom-out',1.25]])el(id).onclick=()=>{navigation.zoom(factor);render();};
el('overview').onclick=()=>{enableLayer('terrain');cameraAt(grid.data.cameras.overview);};
el('wind-view').onclick=()=>{enableLayer('wind');enableLayer('terrain');enableLayer('sea');cameraAt(grid.data.cameras.array);};
el('buildings-view').onclick=()=>{enableLayer('terrain');cameraAt(grid.data.cameras.buildings);};
el('sea-view').onclick=()=>{enableLayer('sea');enableLayer('wind');enableLayer('terrain');cameraAt(grid.data.cameras.sea);};
el('harbour-view').onclick=()=>{enableLayer('harbours');enableLayer('terrain');enableLayer('sea');cameraAt(grid.data.cameras.hallim_harbour);};
el('pitch-view').onclick=()=>{enableLayer('harbours');enableLayer('terrain');cameraAt(grid.data.cameras.sports_pitch);};
el('terrain-relief').onclick=()=>{enableLayer('terrain');cameraAt(grid.data.cameras.terrain);};
el('terrain-view').onclick=()=>{enableLayer('transmission');enableLayer('terrain');choose(grid.records.find(r=>r.id==='hub:power_line:3596'));inspect();};
el('pv-view').onclick=()=>{enableLayer('pv');enableLayer('terrain');choose(grid.records.find(r=>r.kind==='pv'));inspect();};
for(const layer of layers)el('layer-'+layer).onchange=event=>{
  grid.setLayer(layer,event.target.checked);
  if(layer==='wind'){el('rotor-demo').checked=false;el('rotor-demo').disabled=!event.target.checked||contextLost;}
  populate();showWind();render();
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
  showGeneration();
}
async function pick() {
  if (mode === 'scenario') return showPoint();
  const index = Number(el('history-time').value), mine = ++seq;
  selectedAt = times[index]; if (!selectedAt) return;
  showGeneration(); // ponytail: every 5-min playback step refetches its hour; cache per hour if the bridge load matters
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
  el('view-mode').textContent = {latest:'최신 보기',history:'과거 · 제주 집계',scenario:'시나리오 · 제주 집계'}[mode];
  el('weather-scope').textContent = mode === 'latest' ? '현재 기상 · 시설 현장 계측 아님' : '현재 기상 · 과거 제주 집계와 별도 자료';
  el('state-kind').textContent = {latest:'실제 관측 · 최신', history:'실제 관측 · 과거 KST 시각', scenario:'시뮬레이션 · 실측 아님'}[mode];
  el('time-controls').hidden = mode === 'latest'; el('scenario-form').hidden = el('scenario-metrics').hidden = mode !== 'scenario'; el('metrics').hidden = mode === 'scenario';
  el('state-source').textContent = mode === 'scenario' ? '제주 집계 관측에 배율과 사용자 가정을 적용한 계산 · 실측·예측 아님' : '지역 집계이며 개별 시설의 실측 출력이 아닙니다.';
  if (mode !== 'latest') return loadDay();
  refreshState(); showGeneration();
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
// Measured hourly farm totals (manifest.generation.facility_plants): the latest hour, or the hour holding the history/scenario time.
// ponytail: HTTP on selection and time change only; the bridge's /api/v1/jeju/pv/ws would keep the latest value live.
function generation(value, note) { el('generation-value').textContent = value; el('generation-note').textContent = note; }
async function showGeneration() {
  const plant = grid?.data.generation?.facility_plants?.[selected?.id], mine = ++genSeq;
  el('generation').hidden = !plant;
  if (!plant) return;
  const latest = mode === 'latest', at = latest ? 0 : Date.parse((mode === 'history' ? times.length && selectedAt : scenario?.points[el('history-time').value]?.observed_at) || '');
  if (!Number.isFinite(at)) return generation('—', '시각 선택 대기');
  const hour = Math.floor(at / 3600000) * 3600000, rfc = (t) => new Date(t + 9 * 3600000).toISOString().slice(0, 19) + '+09:00';
  const kstHour = (t) => new Date(t).toLocaleString('ko-KR', {timeZone:'Asia/Seoul', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}); // with the year: the latest value can be years old
  const card = el('generation'), query = plant + ':' + (latest ? 'latest' : hour), same = card.dataset.query === query;
  const previousValue = same ? el('generation-value').textContent : '—', previousNote = same ? card.dataset.note || '' : '';
  card.dataset.query = query; card.dataset.state = 'loading';
  if (!same) card.dataset.note = '';
  generation(previousValue, previousNote ? previousNote + ' · 갱신 중…' : '불러오는 중…');
  try {
    const response = await fetch(`${grid.data.generation.api}?plant_id=${plant}${latest ? '' : `&start=${encodeURIComponent(rfc(hour))}&end=${encodeURIComponent(rfc(hour + 3600000))}`}`, {signal:AbortSignal.timeout(20000), cache:'no-store'});
    const body = await response.json().catch(() => null);
    if (mine !== genSeq) return; // sequence guard: a reply for an older selection, mode or time never overwrites a newer one
    if (response.status === 404 && body?.error === 'plant_not_found') { card.dataset.state = 'unavailable'; return generation('—', '브릿지에 해당 단지가 공개되지 않았습니다'); }
    if (!response.ok || !body) throw new Error('HTTP ' + response.status);
    if (body.schema_version !== 1 || body.energy_unit !== 'kWh' || body.interval_seconds !== 3600 || body.source_timezone !== 'Asia/Seoul' || body.timestamp_convention !== 'interval_start' || !Array.isArray(body.plants)) throw new Error('Generation contract');
    const farm = body.plants.find((p) => p.plant?.plant_id === plant), list = farm?.observations;
    if (farm?.plant?.fuel_type !== (selected.kind === 'wind' ? 'wind' : 'solar') || !Array.isArray(list)) throw new Error('Generation facility');
    const obs = latest ? list.at(-1) : list.find((o) => Date.parse(o.interval_start) === hour);
    if (!obs) { card.dataset.state = 'missing'; card.dataset.note = latest ? '최신 1시간값 없음' : `${kstHour(hour)} KST 원천 라벨의 1시간값 없음`; return generation('—', card.dataset.note); }
    const observed = Date.parse(obs.interval_start);
    if (typeof obs.interval_start !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(obs.interval_start) || !Number.isFinite(observed) || (obs.gen_kwh !== null && !Number.isFinite(obs.gen_kwh))) throw new Error('Generation observation');
    const flags = [...(obs.quality_flags || []), ...(farm.quality_flags || [])];
    card.dataset.state = obs.gen_kwh === null ? 'missing' : observed > Date.now() ? 'future' : Date.now() - observed > 48 * 3600000 ? 'historical' : 'available';
    card.dataset.note = [latest ? '원천 최신 1시간값' : '선택 시각이 속한 1시간값', `${kstHour(obs.interval_start)} KST 원천 라벨`,
      Date.now() - observed > 48 * 3600000 ? '과거 실적 · 실시간 계측 아님' : observed > Date.now() ? '미래 시각 · 원천 확인 필요' : '',
      obs.gen_kwh === null ? '발전량 결측' : '', `품질: ${flags.join(', ') || '표시 없음'}`, farm.plant?.data_quality_note].filter(Boolean).join(' · ');
    const kwh = obs.gen_kwh === null ? '—' : `${obs.gen_kwh.toLocaleString('ko-KR')} kWh`;
    // An old "latest" value must not read as current output: put its hour in the headline itself.
    generation(card.dataset.state === 'historical' ? `${kwh} · ${kstHour(obs.interval_start)} 과거값` : kwh, card.dataset.note);
  } catch { if (mine === genSeq) { card.dataset.state = 'disconnected'; generation(previousValue, [previousNote, '발전량 연결 지연 · 시설을 다시 선택하면 재시도합니다'].filter(Boolean).join(' · ')); } }
}
function stationDistance(coordinates, station) {
  if (!coordinates) return Infinity;
  const [lon, lat] = coordinates.map(THREE.MathUtils.degToRad);
  const otherLat = THREE.MathUtils.degToRad(station.latitude);
  const a = Math.sin((otherLat - lat) / 2) ** 2 + Math.cos(lat) * Math.cos(otherLat) * Math.sin((THREE.MathUtils.degToRad(station.longitude) - lon) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(THREE.MathUtils.clamp(a, 0, 1)));
}
function closestWind(readings, coordinates) {
  const sorted = readings.map((r) => ({...r, distance:stationDistance(coordinates, r.station)})).sort((a, b) => a.distance - b.distance || a.station.id - b.station.id);
  return {sorted, selected:sorted.find((r) => r.fresh) || sorted.find((r) => r.weatherFresh) || sorted[0]};
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
    const temperature_c = Number.isFinite(data.temperature_c) && data.temperature_c >= -90 && data.temperature_c <= 60 ? data.temperature_c : null;
    const relative_humidity_percent = Number.isFinite(data.relative_humidity_percent) && data.relative_humidity_percent >= 0 && data.relative_humidity_percent <= 100 ? data.relative_humidity_percent : null;
    const precipitation_1h_mm = Number.isFinite(data.precipitation_1h_mm) && data.precipitation_1h_mm >= 0 ? data.precipitation_1h_mm : null;
    const weatherValid = Number.isFinite(observed) && Number.isFinite(received) && [temperature_c, relative_humidity_percent, precipitation_1h_mm].some(Number.isFinite);
    const weatherFresh = weatherValid && !windFetchFailed && data.weather_status === 'fresh' && now < expires && observed <= now + 60000 && received <= now + 60000;
    return {...data, temperature_c, relative_humidity_percent, precipitation_1h_mm, weatherValid, weatherFresh, observed, valid, fresh, expires, directional:fresh && data.speed_m_s > 0 && directional};
  });
  const coordinates = selected?.coordinates || grid?.data.coordinateFrame.origin_lon_lat;
  const {sorted, selected:data} = closestWind(readings, coordinates);
  el('wind-station').textContent = data ? `${data.station.name}(${data.station.id}) · ${Number.isFinite(data.distance) ? data.distance.toFixed(2) + ' km' : '거리 미확인'}` : '제주 관측 바람';
  el('wind-selection').textContent = selected?.coordinates ? `기준 시설: ${selected.name}` : '기준: 장면 중심 좌표';
  el('wind-speed').textContent = data?.valid ? `${data.speed_m_s.toLocaleString('ko-KR', {maximumFractionDigits:1})} m/s` : '—';
  el('wind-direction').textContent = data?.valid ? data.speed_m_s === 0 ? '정온 · 풍향 없음' : `${data.direction_label} · 약 ${data.direction_from_deg}°에서` : '—';
  el('wind-time').textContent = data?.valid || data?.weatherValid ? `${new Date(data.observed).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'})} KST 관측` : '관측 시각 —';
  el('wind-nearest-note').textContent = data?.weatherFresh && !data?.fresh ? '바람 결측·지연 · 기상 관측이 유효한 가까운 관측소 표시' : data && data !== sorted[0] ? '가장 가까운 관측소의 자료가 유효하지 않아 다음 관측소를 사용합니다.' : '';
  el('weather-values').textContent = [['기온', data?.temperature_c, '°C'], ['습도', data?.relative_humidity_percent, '%'], ['이전 60분 강수', data?.precipitation_1h_mm, 'mm']].map(([label, value, unit]) => `${label} ${data?.weatherValid && Number.isFinite(value) ? value.toLocaleString('ko-KR', {maximumFractionDigits:1}) + ' ' + unit : '—'}`).join(' · ');
  for (const [id,value,unit] of [['weather-temperature',data?.temperature_c,'°C'],['weather-humidity',data?.relative_humidity_percent,'%'],['weather-rain',data?.precipitation_1h_mm,'mm']]) el(id).textContent = data?.weatherValid && Number.isFinite(value) ? value.toLocaleString('ko-KR',{maximumFractionDigits:1}) + ' ' + unit : '—';
  el('map-data-status').textContent = (data?.weatherFresh || data?.fresh ? '기상 최근 관측' : data?.weatherValid || data?.valid ? '기상 갱신 지연' : '기상 연결 확인 중') + (data?.weatherValid || data?.valid ? ' · ' + kst(data.observed_at) + ' KST' : '');
  status('weather-status', data?.weatherFresh ? '기상 최근 관측 · 위 관측소·시각 기준' : data?.weatherValid ? '기상 갱신 지연 · 마지막 관측 표시' : '유효 기상 관측 없음 · 결측은 —', !data?.weatherFresh);
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
  // ponytail: onshore turbines reuse the Tamra 3 MW curve scaled to their own rated power; per-model curves if the shape matters
  el('wind-estimate-power').textContent = estimate ? `${Math.round(estimate.powerKW * (selected.rated_power_kw || 3000) / 3000).toLocaleString('ko-KR')} kW` : '—';
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
      [reading.weatherValid && Number.isFinite(reading.temperature_c) ? `${reading.temperature_c.toLocaleString('ko-KR', {maximumFractionDigits:1})} °C` : '—'],
      [reading.weatherValid && Number.isFinite(reading.relative_humidity_percent) ? `${reading.relative_humidity_percent.toLocaleString('ko-KR', {maximumFractionDigits:1})} %` : '—'],
      [reading.weatherValid && Number.isFinite(reading.precipitation_1h_mm) ? `${reading.precipitation_1h_mm.toLocaleString('ko-KR', {maximumFractionDigits:1})} mm` : '—'],
      ['—'],
      reading.valid || reading.weatherValid ? [new Date(reading.observed).toLocaleDateString('ko-KR', {timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit'}), `${new Date(reading.observed).toLocaleTimeString('ko-KR', {timeZone:'Asia/Seoul',hour12:false,hour:'2-digit',minute:'2-digit'})}${reading.weatherValid ? reading.weatherFresh ? ' · 기상 최근' : ' · 기상 지연' : ''}`] : ['—'],
    ];
    for (const values of cells) {
      const cell = document.createElement('td'); cell.textContent = values[0];
      if (values.length > 1) { const detail = document.createElement('span'); detail.textContent = values[1]; cell.append(detail); }
      row.append(cell);
    }
    el('wind-stations').append(row);
  }
  const expiries = readings.filter((r) => r.fresh || r.weatherFresh).map((r) => r.expires);
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
  if (document.hidden) { navigation?.cancel(); stopDemo(); stop(); clearTimeout(reconnectTimer); disconnectWind(); socket?.close(); }
  else { showWind(); connectWind(); render(); refreshState(); if (!socket || socket.readyState > 1) connectState(); }
});
el('refresh').onclick = () => { refreshState(); refreshWind(); };
startScene(); refreshState(); connectState(); connectWind();
