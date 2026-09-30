import {MOUSE, TOUCH, Vector3, MathUtils} from 'three';

export function createNavigation({camera, controls, canvas, bounds, groundHeight, reducedMotion = false}) {
  const document = canvas.ownerDocument;
  const size = bounds.getSize(new Vector3());
  const margin = Math.max(size.x, size.z) * .15;
  const reduced = () => typeof reducedMotion === 'boolean' ? reducedMotion : reducedMotion.matches;
  let transition = null, active = false, disposed = false;
  Object.assign(controls, {
    enableDamping:!reduced(), dampingFactor:.08, screenSpacePanning:false, zoomToCursor:true,
    minDistance:20, maxDistance:Math.max(1000, size.length() * 3),
    minPolarAngle:.02, maxPolarAngle:Math.PI * .49,
    minAzimuthAngle:-Infinity, maxAzimuthAngle:Infinity,
    mouseButtons:{LEFT:MOUSE.PAN, MIDDLE:MOUSE.DOLLY, RIGHT:MOUSE.ROTATE},
    touches:{ONE:TOUCH.PAN, TWO:TOUCH.DOLLY_PAN},
  });
  function constrain() {
    const target = controls.target.clone();
    controls.target.x = MathUtils.clamp(target.x, bounds.min.x - margin, bounds.max.x + margin);
    controls.target.z = MathUtils.clamp(target.z, bounds.min.z - margin, bounds.max.z + margin);
    camera.position.add(controls.target.clone().sub(target));
    const height = groundHeight(camera.position.x, camera.position.z);
    camera.position.y = Math.max(camera.position.y, (Number.isFinite(height) ? height : 0) + 6);
    camera.lookAt(controls.target);
  }
  function cancel() {
    transition = null;
    // Flush OrbitControls inertia through its public API, preserving the visible pose.
    const position = camera.position.clone(), target = controls.target.clone();
    controls.enableDamping = false; controls.update();
    camera.position.copy(position); controls.target.copy(target); controls.update();
    controls.enableDamping = !reduced(); active = false;
  }
  function changed() { active = true; }
  function browserZoom(event) {
    if (event.ctrlKey || event.metaKey) event.stopImmediatePropagation();
  }
  function visibility() { if (document.hidden) cancel(); }
  function motionChanged() { cancel(); }
  controls.addEventListener('start', cancel);
  controls.addEventListener('change', changed);
  canvas.addEventListener('wheel', browserZoom, {capture:true, passive:true});
  document.addEventListener('visibilitychange', visibility);
  if (typeof reducedMotion !== 'boolean') reducedMotion.addEventListener('change', motionChanged);

  function moveTo(view, scale = 1) {
    cancel();
    const target = new Vector3().fromArray(view.target);
    const position = new Vector3().fromArray(view.position).sub(target).multiplyScalar(scale).add(target);
    transition = {position, target, fov:view.fov || 48, from:camera.position.clone(),
      fromTarget:controls.target.clone(), fromFov:camera.fov, elapsed:0};
    active = true;
    if (reduced()) update(.35);
    // Wake the renderer's existing change listener; navigation never owns a RAF.
    controls.dispatchEvent({type:'change'});
  }
  function update(deltaSeconds) {
    if (disposed || document.hidden || !controls.enabled) return false;
    if (transition) {
      transition.elapsed += Math.max(0, Number.isFinite(deltaSeconds) ? deltaSeconds : 0);
      const t = Math.min(1, transition.elapsed / .35), eased = t * t * (3 - 2 * t);
      camera.position.lerpVectors(transition.from, transition.position, eased);
      controls.target.lerpVectors(transition.fromTarget, transition.target, eased);
      camera.fov = MathUtils.lerp(transition.fromFov, transition.fov, eased); camera.updateProjectionMatrix();
      controls.update(); constrain();
      if (t === 1) transition = null;
      active = !!transition;
      return active;
    }
    if (!active) return false;
    active = controls.update(deltaSeconds);
    constrain();
    return active;
  }
  function zoom(factor) {
    cancel();
    const offset = camera.position.clone().sub(controls.target);
    offset.setLength(MathUtils.clamp(offset.length() * factor, controls.minDistance, controls.maxDistance));
    camera.position.copy(controls.target).add(offset); controls.update(); constrain();
    active = true; controls.dispatchEvent({type:'change'});
  }
  function heading(angle) {
    const offset = camera.position.clone().sub(controls.target);
    offset.applyAxisAngle(camera.up, angle - controls.getAzimuthalAngle());
    moveTo({position:offset.add(controls.target).toArray(), target:controls.target.toArray(), fov:camera.fov});
  }
  function dispose() {
    cancel(); disposed = true;
    controls.removeEventListener('start', cancel); controls.removeEventListener('change', changed);
    canvas.removeEventListener('wheel', browserZoom, true);
    document.removeEventListener('visibilitychange', visibility);
    if (typeof reducedMotion !== 'boolean') reducedMotion.removeEventListener('change', motionChanged);
  }
  return {moveTo, zoom, north:() => heading(0), rotate:() => heading(controls.getAzimuthalAngle() + Math.PI / 6), cancel, update, dispose};
}
