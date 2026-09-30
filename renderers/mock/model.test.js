import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mockProfile, timeLabel } from './model.js';

test('mock day is deterministic, bounded and never implies measured facility dispatch', () => {
  for (let i = 0; i < 288; i++) {
    const p = mockProfile(i);
    assert.equal(p.data_kind, 'mock');
    assert.ok([p.demand, p.wind, p.solar].every(v => Number.isFinite(v) && v >= 0));
    assert.equal(p.netLoad, p.demand - p.wind - p.solar);
    assert.deepEqual(p, mockProfile(i));
  }
  assert.equal(mockProfile(0).solar, 0);
  assert.equal(timeLabel(287), '23:55');
  for (const invalid of [-1, 288, .5, NaN, '3']) {
    assert.throws(() => mockProfile(invalid), RangeError);
    assert.throws(() => timeLabel(invalid), RangeError);
  }
});
