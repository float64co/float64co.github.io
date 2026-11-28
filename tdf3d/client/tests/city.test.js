import test from 'node:test';
import assert from 'node:assert/strict';
import { generateCity, BLOCKS, PITCH, ROAD } from '../src/city.js';

test('same seed gives the same city', () => {
  assert.deepEqual(generateCity(7), generateCity(7));
});

test('different seed gives a different city', () => {
  assert.notDeepEqual(generateCity(7), generateCity(8));
});

test('buildings stay off the roads', () => {
  const city = generateCity();
  assert.equal(city.length, BLOCKS * BLOCKS);
  const half = (PITCH - ROAD) / 2;
  for (const block of city)
    for (const b of block.buildings) {
      assert.ok(Math.abs(b.x - block.x) + b.w / 2 <= half);
      assert.ok(Math.abs(b.z - block.z) + b.d / 2 <= half);
    }
});
