import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {registerHooks} from 'node:module';
registerHooks({resolve(specifier, context, next) {
  if (specifier === 'three') return next(new URL('../renderers/mock/node_modules/three/build/three.module.js', import.meta.url).href, context);
  return next(specifier, context);
}});
const THREE = await import('three');
const {OrbitControls} = await import('../renderers/mock/node_modules/three/examples/jsm/controls/OrbitControls.js');
assert.ok(existsSync(new URL('../renderers/twin/navigation.mjs', import.meta.url)), 'navigation module must exist');
const {createNavigation} = await import('../renderers/twin/navigation.mjs');
// Only DOM delivery is stubbed; all camera, projection and controls calculations are real.
class Surface {
  listeners = new Map(); style = {}; clientWidth = 1000; clientHeight = 800; hidden = false;
  addEventListener(type, fn, options) {
    const list = this.listeners.get(type) || [];
    list.push({fn, capture:options === true || !!options?.capture}); this.listeners.set(type, list);
  }
  removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item.fn !== fn)); }
  dispatch(type, fields = {}) {
    const event = {type, pointerId:1, pointerType:'mouse', button:0, clientX:500, clientY:400,
      pageX:500, pageY:400, deltaMode:0, ctrlKey:false, metaKey:false, shiftKey:false,
      preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() { this.stopped = true; }, ...fields};
    for (const {fn} of [...(this.listeners.get(type) || [])].sort((a,b) => b.capture - a.capture)) {
      fn(event); if (event.stopped) break;
    }
    return event;
  }
  getRootNode() { return this.ownerDocument; }
  getBoundingClientRect() { return {left:0, top:0, width:1000, height:800}; }
  setPointerCapture() {} releasePointerCapture() {}
}
const doc = new Surface(), canvas = new Surface(); canvas.ownerDocument = doc;
const camera = new THREE.PerspectiveCamera(48, 1.25, .5, 70000); camera.position.set(0, 300, 400);
const controls = new OrbitControls(camera, canvas);
const media = new Surface(); media.matches = false;
const nav = createNavigation({camera, controls, canvas, bounds:new THREE.Box3(new THREE.Vector3(-1000,0,-1000),new THREE.Vector3(1000,100,1000)), groundHeight:(x,z) => Math.abs(x) < 20 && Math.abs(z) < 20 ? 120 : 30, reducedMotion:media});
function settle() { for (let i=0; i<500; i++) if (!nav.update(1/60)) return i; assert.fail('navigation never became idle'); }
function drag(fields = {}) {
  canvas.dispatch('pointerdown', fields);
  canvas.dispatch('pointermove', {...fields,clientX:600,pageX:600});
  canvas.dispatch('pointerup', {...fields,clientX:600,pageX:600});
  settle();
}
const startTarget = controls.target.clone(); drag();
assert.ok(controls.target.distanceTo(startTarget)>1, 'left drag must pan');
const panTarget = controls.target.clone(); const beforeAngle=controls.getAzimuthalAngle();
drag({shiftKey:true});
assert.ok(controls.target.distanceTo(panTarget)<1e-6, 'Shift drag must preserve pan target');
assert.ok(Math.abs(controls.getAzimuthalAngle()-beforeAngle)>.01, 'Shift drag must rotate');
console.log('PASS left pan / Shift rotate / damping reaches idle');
const beforeWheel = camera.position.clone();
for (const modifier of ['ctrlKey','metaKey']) {
  const e=canvas.dispatch('wheel',{[modifier]:true,deltaY:-100});
  assert.ok(!e.defaultPrevented,'browser zoom default must survive');
  assert.deepEqual(camera.position.toArray(),beforeWheel.toArray());
}
const distance=controls.getDistance(); canvas.dispatch('wheel',{deltaY:-100}); settle();
assert.ok(controls.getDistance()<distance,'ordinary wheel must zoom');
console.log('PASS Ctrl/Meta wheel bypass / ordinary wheel zoom');
const view={position:[400,300,400],target:[100,40,100],fov:42};
nav.moveTo(view); assert.equal(nav.update(.1),true);
assert.ok(camera.position.distanceTo(new THREE.Vector3(...view.position))>1,'focus must animate');
settle(); assert.ok(camera.position.distanceTo(new THREE.Vector3(...view.position))<1e-5);
assert.equal(camera.fov,42);
nav.moveTo({position:[-400,300,400],target:[-100,40,100]}); nav.update(.1);
canvas.dispatch('pointerdown'); const interrupted=camera.position.clone(); canvas.dispatch('pointerup'); settle();
assert.ok(camera.position.distanceTo(interrupted)<1e-5,'input must cancel focus');
console.log('PASS 350ms focus / input interruption');
nav.moveTo(view); nav.update(.1); doc.hidden=true; doc.dispatch('visibilitychange');
assert.equal(nav.update(.1),false); const hiddenPosition=camera.position.clone(); doc.hidden=false; settle();
assert.ok(camera.position.distanceTo(hiddenPosition)<1e-5,'hidden tab must cancel focus');
media.matches=true; media.dispatch('change');
nav.moveTo(view); assert.ok(camera.position.distanceTo(new THREE.Vector3(...view.position))<1e-5);
assert.equal(controls.enableDamping,false);
console.log('PASS hidden tab cancellation / live reduced motion');
nav.moveTo({position:[90000,-100,90000],target:[90000,0,90000]}); settle();
assert.ok(Math.abs(controls.target.x)<=1400 && Math.abs(controls.target.z)<=1400,'target must stay near scene');
assert.ok(camera.position.y>=36,'terrain clearance must hold');
nav.moveTo({position:[0,5,0],target:[0,0,-40]}); settle();
assert.ok(camera.position.y>=126,'obstacle clearance must hold');
nav.zoom(.00001); settle(); assert.ok(controls.getDistance()>=19.999);
nav.zoom(100000); settle(); assert.ok(controls.getDistance()<=controls.maxDistance+.001);
nav.north(); settle(); assert.ok(Math.abs(controls.getAzimuthalAngle())<1e-6);
nav.rotate(); settle(); assert.ok(Math.abs(controls.getAzimuthalAngle()-Math.PI/6)<1e-6);
assert.ok(camera.position.toArray().every(Number.isFinite));
console.log('PASS bounds / ground and obstacle clearance / zoom limits / north / rotate');
nav.dispose(); controls.dispose();
assert.equal([...canvas.listeners.values()].flat().length,0);
assert.equal([...doc.listeners.values()].flat().length,0);
assert.equal([...media.listeners.values()].flat().length,0);
console.log('PASS listener disposal');

const touchCanvas = new Surface(); touchCanvas.ownerDocument = doc;
const touchCamera = new THREE.PerspectiveCamera(48, 1.25, .5, 70000); touchCamera.position.set(0,300,400);
const touchControls = new OrbitControls(touchCamera,touchCanvas);
const touchNav = createNavigation({camera:touchCamera,controls:touchControls,canvas:touchCanvas,
  bounds:new THREE.Box3(new THREE.Vector3(-5000,0,-5000),new THREE.Vector3(5000,100,5000)),groundHeight:()=>0});
function touchSettle() { for(let i=0;i<500;i++) if(!touchNav.update(1/60)) return; assert.fail('touch/cursor damping did not settle'); }
const oldTarget = touchControls.target.clone();
touchCanvas.dispatch('pointerdown',{pointerType:'touch'});
touchCanvas.dispatch('pointermove',{pointerType:'touch',clientX:550,pageX:550});
touchCanvas.dispatch('pointerup',{pointerType:'touch',clientX:550,pageX:550}); touchSettle();
assert.ok(touchControls.target.distanceTo(oldTarget)>1,'one finger must pan');
const oldDistance=touchControls.getDistance();
touchCanvas.dispatch('pointerdown',{pointerType:'touch',pointerId:1,clientX:450,pageX:450});
touchCanvas.dispatch('pointerdown',{pointerType:'touch',pointerId:2,clientX:550,pageX:550});
touchCanvas.dispatch('pointermove',{pointerType:'touch',pointerId:2,clientX:650,pageX:650});
touchCanvas.dispatch('pointerup',{pointerType:'touch',pointerId:2,clientX:650,pageX:650});
touchCanvas.dispatch('pointerup',{pointerType:'touch',pointerId:1,clientX:450,pageX:450}); touchSettle();
assert.ok(touchControls.getDistance()<oldDistance,'two fingers must pinch zoom');
console.log('PASS touch pan / two-finger pinch');
touchCamera.updateMatrixWorld();
const ray = new THREE.Raycaster(), ndc = new THREE.Vector2(.4,.2);
ray.setFromCamera(ndc,touchCamera);
const anchor=ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0,1,0),-touchControls.target.y),new THREE.Vector3());
assert.ok(anchor);
touchCanvas.dispatch('wheel',{clientX:700,clientY:320,deltaY:-100}); touchSettle(); touchCamera.updateMatrixWorld();
const projected=anchor.clone().project(touchCamera);
assert.ok(Math.hypot(projected.x-.4,projected.y-.2)<.02,'ground anchor must remain near cursor');
touchNav.moveTo({position:[0,31,1000],target:[0,0,0]}); touchSettle();
for (let i=0;i<30;i++) { touchCanvas.dispatch('wheel',{clientX:800,clientY:1,deltaY:i%2?-100:100}); touchSettle(); }
assert.ok(touchCamera.position.toArray().every(Number.isFinite));
assert.ok(touchControls.target.toArray().every(Number.isFinite));
assert.ok(touchCamera.position.y>=6);
console.log('PASS cursor ground anchoring / repeated horizon zoom stays finite');
touchNav.dispose(); touchControls.dispose();
