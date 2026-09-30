import assert from 'node:assert/strict';
import {estimateWind} from '../renderers/twin/wind-estimate.mjs';

for (const speed of [NaN, Infinity, -1, undefined]) assert.equal(estimateWind(speed), null);
assert.deepEqual(estimateWind(2.9), {powerKW:0, rpm:0, condition:'below'});
assert.equal(estimateWind(3).powerKW, 0);
assert.ok(estimateWind(3).rpm > 0); // visual cut-in heuristic, not measured motion
assert.ok(estimateWind(8).powerKW > 0 && estimateWind(8).powerKW < 3000);
assert.deepEqual(estimateWind(13), {powerKW:3000, rpm:15.7, condition:'available'});
assert.deepEqual(estimateWind(24.9), {powerKW:3000, rpm:15.7, condition:'available'});
assert.deepEqual(estimateWind(25), {powerKW:0, rpm:0, condition:'above'});
