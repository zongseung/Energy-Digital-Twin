import assert from 'node:assert/strict';
import {shouldApply, kstDay} from '../renderers/twin/playback.mjs';

const A = '2026-09-28T00:00:00Z', A_KST = '2026-09-28T09:00:00+09:00', B = '2026-09-28T00:05:00Z';
assert.equal(shouldApply('latest', null, A, true, 0, 0), true);
assert.equal(shouldApply('latest', A, A, false, 1, 1), false); // late history reply after returning to latest
assert.equal(shouldApply('history', A, A_KST, false, 3, 3), true); // same instant, other offset
assert.equal(shouldApply('history', B, A, false, 3, 3), false); // A arrives after B was chosen
assert.equal(shouldApply('history', A, A, false, 4, 3), false); // stale request for the same time
assert.equal(shouldApply('history', A, A, true, 3, 3), false); // live WS during history
assert.equal(shouldApply('history', 'bad', 'bad', false, 3, 3), false);
assert.equal(shouldApply('scenario', A, A, true, 3, 3), false);
assert.equal(shouldApply('scenario', A, A, false, 3, 3), false);
assert.deepEqual(kstDay('2026-09-30'), {start:'2026-09-30T00:00:00+09:00', end:'2026-10-01T00:00:00+09:00'});
assert.deepEqual(kstDay('2026-12-31'), {start:'2026-12-31T00:00:00+09:00', end:'2027-01-01T00:00:00+09:00'});
assert.equal(kstDay('2026-02-30'), null);
assert.equal(kstDay(''), null);
console.log('playback: PASS');
