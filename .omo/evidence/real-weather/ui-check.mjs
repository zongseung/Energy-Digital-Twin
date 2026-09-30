import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {estimateWind} from '../../../renderers/twin/wind-estimate.mjs';
const app = readFileSync('renderers/twin/app.js','utf8');
const nodes = new Map();
const element = () => ({textContent:'', dataset:{}, checked:true, append(){}, replaceChildren(){}, setAttribute(){}});
const el = id => { if (!nodes.has(id)) nodes.set(id,element()); return nodes.get(id); };
const now = Date.parse('2026-09-30T05:30:00Z');
const context = vm.createContext({Date:class extends Date { static now(){return now;} },Number,Math,Map,String,console,el,
  THREE:{MathUtils:{degToRad:n=>n*Math.PI/180,clamp:(n,a,b)=>Math.min(b,Math.max(a,n))}},
  document:{body:{dataset:{}},hidden:false,createElement:element}, clearTimeout(){},setTimeout(){},
  windExpiryTimer:null,windFetchFailed:false,selected:{coordinates:[126.25809,33.39268],kind:'pv',name:'PV'},
  grid:{data:{facilities:[{id:'turbine',kind:'wind',coordinates:[126.25809,33.39268]}]},visible:{wind:true},setWindDirections(){}},estimateWind,contextLost:false,rotorRPM:new Map(),syncDemo(){},render(){},status(id,message){el(id).textContent=message;}
});
vm.runInContext(app.slice(app.indexOf('const kst ='),app.indexOf('const kstDate =')) + app.slice(app.indexOf('function stationDistance('),app.indexOf('function connectWind(')),context);
const row = {station:{id:779,name:'한림',latitude:33.39268,longitude:126.25809},observed_at:'2026-09-30T05:29:00Z',received_at:'2026-09-30T05:30:00Z',status:'fresh',weather_status:'fresh',speed_m_s:2,direction_from_deg:0,direction_label:'북',average_window_minutes:10,directional_resolution_deg:22.5,temperature_c:-2.5,relative_humidity_percent:82,precipitation_1h_mm:0};
function display(changes={},failed=false){context.windData={stations:[{...row,...changes}]};context.windFetchFailed=failed;context.showWind();}
display();
assert.match(el('weather-values').textContent,/-2.5 °C/);
assert.match(el('weather-values').textContent,/82 %/);
assert.match(el('weather-values').textContent,/60분 강수 0 mm/);
assert.equal(el('weather-temperature').textContent,'-2.5 °C');
assert.equal(el('weather-humidity').textContent,'82 %');
assert.equal(el('weather-rain').textContent,'0 mm');
assert.match(el('weather-status').textContent,/최근/);
assert.equal(context.rotorRPM.size,1);
display({relative_humidity_percent:101,precipitation_1h_mm:null});
assert.match(el('weather-values').textContent,/습도 —/);
assert.match(el('weather-values').textContent,/60분 강수 —/);
display({},true);
assert.match(el('weather-status').textContent,/지연/);
assert.equal(context.document.body.dataset.wind,'stale');
display({status:'unavailable',speed_m_s:null,direction_from_deg:null});
assert.match(el('weather-values').textContent,/-2.5 °C/);
assert.match(el('weather-status').textContent,/최근/);
assert.match(el('wind-nearest-note').textContent,/바람 결측/);
assert.equal(context.document.body.dataset.wind,'unavailable');
assert.equal(context.rotorRPM.size,0);
context.windData={stations:[{...row,station:{...row.station,id:1},status:'unavailable',weather_status:'unavailable',speed_m_s:null,temperature_c:null,relative_humidity_percent:null,precipitation_1h_mm:null},{...row,station:{...row.station,id:2,latitude:33.4},status:'unavailable',speed_m_s:null}]};
context.showWind();
assert.match(el('wind-station').textContent,/\(2\)/);
assert.equal(context.rotorRPM.size,0);
display({observed_at:'2026-09-30T04:00:00Z'});
assert.match(el('weather-status').textContent,/지연/);
display({temperature_c:null,relative_humidity_percent:null,precipitation_1h_mm:null,weather_status:'unavailable'});
assert.match(el('weather-status').textContent,/없음/);
console.log('PASS: actual showWind renders same-station weather, zero rain, null fields, stale/disconnected values and weather-only fallback without rotor estimates');
