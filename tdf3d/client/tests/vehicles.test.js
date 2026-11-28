import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage, roadSpot, VEHICLES } from '../src/garage.js';

await RAPIER.init();

function setup(name) {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = 1 / 60;
  const scene = new THREE.Scene();
  buildWorld(scene, physics, generateCity());
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 }, name);
  const run = (seconds, input = {}) => {
    let minUp = 1;
    for (let i = 0; i < seconds * 60; i++) {
      garage.current.drive({ throttle: 0, steer: 0, brake: false, descend: false, ...input }, 1 / 60);
      physics.step();
      const up = new THREE.Vector3(0, 1, 0).applyQuaternion(garage.current.body.rotation()).y;
      minUp = Math.min(minUp, up);
    }
    garage.current.sync();
    return { ...garage.current.body.translation(), speed: garage.current.speed(), minUp };
  };
  run(1);
  return { garage, physics, scene, run };
}

for (const name of ['car', 'motorbike', 'bicycle']) {
  test(`${name} accelerates and stays upright through a hard turn`, () => {
    const { run } = setup(name);
    const straight = run(3, { throttle: 1 });
    assert.ok(straight.speed > 4, `speed ${straight.speed}`);
    const turned = run(4, { throttle: 1, steer: 1 });
    console.log(name, { top: straight.speed, minUp: turned.minUp, height: turned.y });
    assert.ok(turned.minUp > 0.85, `leaned to ${turned.minUp}`);
  });
}

test('helicopter climbs, hovers and lands', () => {
  const { run } = setup('helicopter');
  const high = run(4, { brake: true });
  assert.ok(high.y > 40);
  const settled = run(2);
  assert.ok(Math.abs(run(3).y - settled.y) < 0.5);
  const down = run(10, { descend: true });
  assert.ok(down.y > 0.5 && down.y < 1.5, `height ${down.y}`);
});

test('spaceplane needs speed to take off, then climbs', () => {
  const { run } = setup('spaceplane');
  assert.ok(run(1, { brake: true }).y < 1.5);
  const rolling = run(3, { throttle: 1 });
  assert.ok(rolling.speed > 35 && rolling.y < 1.5, JSON.stringify(rolling));
  const climbing = run(5, { throttle: 1, brake: true });
  console.log('spaceplane', climbing);
  assert.ok(climbing.y > 200);
});

test('switching leaves exactly one vehicle, on a road', () => {
  const { garage, physics, scene, run } = setup('car');
  const bodies = physics.bodies.len();
  const objects = scene.children.length;
  for (const name of [...VEHICLES, 'car']) {
    garage.switch(name);
    run(1);
    assert.equal(garage.current.name, name);
    assert.equal(physics.bodies.len(), bodies);
    assert.equal(scene.children.length, objects);
  }
  const spot = roadSpot(31, 500);
  assert.ok(spot.x % PITCH === 0 || spot.z % PITCH === 0);
});

for (const name of VEHICLES) {
  test(`get out of the ${name}, walk, run and get back in`, () => {
    const { garage, physics, run } = setup(name);
    run(1);
    const vehicle = garage.current;
    const left = { ...vehicle.body.translation() };
    assert.equal(garage.toggle(), null);
    assert.equal(garage.current.name, 'on foot');
    const step = (seconds, input = {}) => {
      for (let i = 0; i < seconds * 60; i++) {
        garage.drive({ throttle: 0, steer: 0, brake: false, descend: false, run: false, ...input }, 1 / 60);
        physics.step();
      }
      return { ...garage.current.body.translation(), speed: garage.current.speed() };
    };
    const stood = step(2);
    assert.ok(stood.y > 0.7 && stood.y < 1, `standing height ${stood.y}`);
    const still = vehicle.body.translation();
    assert.ok(Math.hypot(still.x - left.x, still.z - left.z) < 0.5, 'parked vehicle moved');
    assert.equal(garage.toggle(), null, 'should be able to get straight back in');
    assert.equal(garage.current, vehicle);
    assert.equal(garage.parked.size, 0);
    assert.equal(garage.toggle(), null);
    assert.ok(Math.abs(step(1, { throttle: 1 }).speed - 2.5) < 0.1);
    assert.ok(Math.abs(step(3, { throttle: 1, run: true }).speed - 7) < 0.1);
    assert.equal(garage.toggle(), 'No vehicle close enough');
  });
}

test('cannot get out while moving or hovering', () => {
  const car = setup('car');
  car.run(2, { throttle: 1 });
  assert.equal(car.garage.toggle(), 'Stop before getting out');
  const heli = setup('helicopter');
  heli.run(3, { brake: true });
  heli.run(3);
  assert.equal(heli.garage.toggle(), 'Land before getting out');
});
