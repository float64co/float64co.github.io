import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGroundVehicle } from '../src/ground.js';

await RAPIER.init();

function run(steer, seconds) {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = 1 / 60;
  const scene = new THREE.Scene();
  buildWorld(scene, physics, generateCity());
  const car = createGroundVehicle('car', scene, physics, { x: 0, y: 1.5, z: PITCH / 2 });
  const step = (input) => { car.drive(input, 1 / 60); physics.step(); };
  for (let i = 0; i < 60; i++) step({ throttle: 0, steer: 0, brake: false });
  const start = { ...car.body.translation() };
  for (let i = 0; i < seconds * 60; i++) step({ throttle: 1, steer, brake: false });
  const end = car.body.translation();
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(car.body.rotation());
  return { dx: end.x - start.x, dz: end.z - start.z, y: end.y, up: up.y, speed: car.speed() };
}

test('throttle drives forward (+z) and stays upright', () => {
  const r = run(0, 3);
  console.log(r);
  assert.ok(r.dz > 5 && Math.abs(r.dx) < 1 && r.speed > 5);
  assert.ok(r.up > 0.95 && r.y > 0.3 && r.y < 1.5);
});

test('positive steer turns left (+x) without rolling', () => {
  const r = run(1, 1.5);
  console.log(r);
  assert.ok(r.dx > 0.5 && r.up > 0.9);
});

// How far round the car gets from `speed` at full lock with the brake on.
function skid(speed, handbrake) {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = 1 / 60;
  physics.createCollider(RAPIER.ColliderDesc.cuboid(2000, 1, 2000).setTranslation(0, -1, 0));
  const car = createGroundVehicle('car', new THREE.Scene(), physics, { x: 0, y: 0.9, z: 0 });
  const step = (input) => { car.drive(input, 1 / 60); physics.step(); };
  for (let i = 0; i < 90; i++) step({ throttle: 0, steer: 0, brake: false });
  car.body.setLinvel({ x: 0, y: 0, z: speed }, true);
  for (let i = 0; i < 20; i++) step({ throttle: 0, steer: 0, brake: false });
  const start = { ...car.body.translation() };
  let turned = 0;
  let last = 0;
  let side = 0;
  for (let i = 0; i < 180; i++) {
    step({ throttle: 0, steer: 1, brake: true, handbrake });
    const q = car.body.rotation();
    const yaw = Math.atan2(2 * (q.x * q.z + q.w * q.y), 1 - 2 * (q.x * q.x + q.y * q.y));
    turned += Math.atan2(Math.sin(yaw - last), Math.cos(yaw - last));
    last = yaw;
    side = Math.max(side, Math.abs(car.body.translation().x - start.x));
    assert.ok(1 - 2 * (q.x * q.x + q.z * q.z) > 0.9, 'rolled');
    const v = car.body.linvel();
    if (Math.hypot(v.x, v.z) < 1) break;
  }
  return { turned: (turned * 180) / Math.PI, side };
}

test('the handbrake turns more tightly than braking alone', () => {
  for (const speed of [14, 20]) {
    const plain = skid(speed, false);
    const hand = skid(speed, true);
    console.log({ speed, plain, hand });
    assert.ok(hand.turned > plain.turned * 1.1, `at ${speed}`);
  }
  assert.ok(skid(20, true).side < skid(20, false).side * 0.85, 'no tighter');
  const fast = [skid(32, false), skid(32, true)];
  assert.ok(Math.abs(fast[1].turned - fast[0].turned) < 25, 'much changed at high speed');
});
