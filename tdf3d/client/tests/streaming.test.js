import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH, SIZE } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGroundVehicle } from '../src/ground.js';

await RAPIER.init();
const city = generateCity();

function setup() {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = 1 / 60;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  return { physics, scene, world, fixed: physics.colliders.len() };
}

const solidAt = (physics, b) => {
  physics.step();
  return physics.intersectionWithShape(
    { x: b.x, y: 3, z: b.z }, { x: 0, y: 0, z: 0, w: 1 }, new RAPIER.Ball(0.5),
  ) !== null;
};
const buildingNear = (x, z) => city
  .flatMap((b) => b.buildings)
  .sort((a, b) => Math.hypot(a.x - x, a.z - z) - Math.hypot(b.x - x, b.z - z))[0];

test('colliders follow the positions given and stay bounded', () => {
  const { physics, world, fixed } = setup();
  const here = buildingNear(0, 0);
  const far = buildingNear(SIZE / 3, SIZE / 3);
  world.update([{ x: 0, z: 0 }]);
  assert.ok(solidAt(physics, here) && !solidAt(physics, far));
  assert.ok(physics.colliders.len() - fixed <= 25 * 4);

  world.update([{ x: far.x, z: far.z }]);
  assert.ok(solidAt(physics, far) && !solidAt(physics, here));

  // A parked vehicle keeps its own surroundings solid.
  world.update([{ x: far.x, z: far.z }, { x: 0, z: 0 }]);
  assert.ok(solidAt(physics, far) && solidAt(physics, here));
  assert.ok(physics.colliders.len() - fixed <= 50 * 4);

  world.update([]);
  assert.equal(physics.colliders.len(), fixed);
});

test('a car driven at a distant building is stopped by it', () => {
  const { physics, scene, world } = setup();
  const target = buildingNear(900, 900);
  const start = target.z - target.d / 2 - 15;
  const car = createGroundVehicle('car', scene, physics, { x: target.x, y: 1.5, z: start });
  for (let i = 0; i < 300; i++) {
    car.drive({ throttle: 1, steer: 0, brake: false }, 1 / 60);
    world.update([car.body.translation()]);
    physics.step();
  }
  assert.ok(car.body.translation().z < target.z - target.d / 2, 'drove through the wall');
});
