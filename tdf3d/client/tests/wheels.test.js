import test from 'node:test';
import assert from 'node:assert/strict';
import { rollWheels, RADIUS } from '../src/wheels.js';

test('wheels roll with distance, backwards in reverse', () => {
  const state = {};
  rollWheels(state, 0, 0, 0);
  rollWheels(state, 0, 1, 0);
  assert.ok(Math.abs(state.spin - 1 / RADIUS) < 1e-9 && state.steer === 0);
  rollWheels(state, 0, 0, 0);
  assert.ok(Math.abs(state.spin) < 1e-9);
});

test('front wheels point into the turn and hold when stopped', () => {
  for (const side of [1, -1]) {
    const state = {};
    rollWheels(state, 0, 0, 0);
    rollWheels(state, 0, 0.2, 0.02 * side); // positive yaw is a left turn
    assert.equal(Math.sign(state.steer), side);
    const held = state.steer;
    rollWheels(state, 0, 0.2, 0.02 * side);
    assert.equal(state.steer, held);
  }
});

test('slow creeping still adds up, and teleports do not spin the wheels', () => {
  const state = {};
  rollWheels(state, 0, 0, 0);
  for (let i = 1; i <= 100; i++) rollWheels(state, 0, i * 0.004, 0);
  assert.ok(Math.abs(state.spin - 0.4 / RADIUS) < 0.02);
  const before = state.spin;
  rollWheels(state, 0, 500, 0);
  assert.equal(state.spin, before);
});
