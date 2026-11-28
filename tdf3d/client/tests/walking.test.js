import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { createWalker } from '../src/foot.js';

await RAPIER.init();

// Velocity after one step, for a walker that starts facing +z (so +x is its left).
function setup() {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const walker = createWalker('on foot', new THREE.Scene(), physics, { x: 0, y: 1, z: 0 });
  const move = (input) => {
    walker.drive(input, 1 / 60);
    const v = walker.body.linvel();
    return [v.x, v.z].map((n) => Math.round(n * 100) / 100 + 0);
  };
  return { walker, move };
}

test('strafing moves sideways without turning', () => {
  const { walker, move } = setup();
  assert.deepEqual(move({ strafe: 1 }), [2.5, 0]);
  assert.deepEqual(move({ strafe: -1, run: true }), [-7, 0]);
  assert.equal(walker.view().yaw, 0);
  const [x, z] = move({ throttle: 1, strafe: 1, run: true });
  assert.ok(Math.abs(Math.hypot(x, z) - 7) < 0.02, 'diagonal is no faster');
});

test('the mouse turns the walker and pitch is limited', () => {
  const { walker, move } = setup();
  walker.look(Math.PI / 2 / walker.tune.mouse, 0); // a quarter turn to the right
  assert.deepEqual(move({ throttle: 1 }), [-2.5, 0]);
  walker.look(0, -100000);
  assert.equal(walker.view().pitch, 1.4);
  walker.look(0, 100000);
  assert.equal(walker.view().pitch, -1.4);
});

test('arrow turning still works', () => {
  const { walker, move } = setup();
  move({ turn: 1 });
  assert.ok(walker.view().yaw > 0);
});

const bodyYaw = (walker) => {
  const q = walker.body.rotation();
  return Math.atan2(2 * q.w * q.y, 1 - 2 * q.y * q.y);
};

test('third person: turns to walk the way the key points, then faces the camera', () => {
  // Camera faces +z, so left is +x.
  for (const [input, want] of [
    [{ throttle: 1 }, 0], [{ strafe: 1 }, Math.PI / 2], [{ strafe: -1 }, -Math.PI / 2],
    [{ throttle: -1 }, Math.PI], [{ throttle: 1, strafe: 1 }, Math.PI / 4],
  ]) {
    const { walker, move } = setup();
    let v;
    for (let i = 0; i < 60; i++) v = move({ ...input, thirdPerson: true });
    assert.ok(Math.cos(bodyYaw(walker) - want) > 0.999, `body ${bodyYaw(walker)} wanted ${want}`);
    assert.ok(Math.abs(v[0] - Math.sin(want) * 2.5) < 0.02 && Math.abs(v[1] - Math.cos(want) * 2.5) < 0.02);
    assert.equal(walker.view().yaw, 0, 'camera is not turned by walking');

    const first = move({ thirdPerson: true });
    assert.deepEqual(first, [0, 0], 'turns on the spot');
    for (let i = 0; i < 60; i++) move({ thirdPerson: true });
    assert.ok(Math.cos(bodyYaw(walker)) > 0.999, 'faces the camera again');
  }
});

test('third person turn is gradual, first person is immediate', () => {
  const { walker, move } = setup();
  move({ strafe: 1, thirdPerson: true });
  assert.ok(bodyYaw(walker) > 0 && bodyYaw(walker) < 0.5);
  move({ strafe: 1 });
  assert.ok(Math.abs(bodyYaw(walker)) < 1e-6, 'first person keeps facing the camera');
});
