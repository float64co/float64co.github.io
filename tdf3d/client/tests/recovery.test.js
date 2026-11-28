import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { laneState, canRejoin, steerBack } from '../src/recovery.js';
import { LANE, BOX } from '../src/traffic-sim.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

test('lane state is measured along the original direction of travel', () => {
  // Heading +z on the road x = 0: the lane is to the left, at x = +LANE.
  const route = { i: 0, j: 0, dir: [0, 1] };
  const state = laneState(route, { x: LANE, z: PITCH + BOX + 5, hx: 0, hz: 1 });
  assert.deepEqual([state.i, state.j, state.s, state.offset], [0, 1, 5, 0]);
  assert.ok(canRejoin(state));
  assert.ok(!canRejoin(laneState(route, { x: LANE, z: 20, hx: 0, hz: -1 })), 'facing backwards');
  assert.ok(!canRejoin(laneState(route, { x: -3, z: 20, hx: 0, hz: 1 })), 'wrong lane');
});

test('the driver stops for something ahead, then backs off', () => {
  const route = { i: 0, j: 0, dir: [0, 1] };
  const pose = { x: LANE, z: 20, hx: 0, hz: 1, speed: 0 };
  const person = [{ x: LANE, z: 23, r: 0.5 }];
  const memory = {};
  assert.equal(steerBack(route, pose, person, memory, DT).throttle, 0);
  let input;
  for (let i = 0; i < 3 * 60; i++) input = steerBack(route, pose, person, memory, DT);
  assert.ok(input.throttle < 0, 'did not reverse');
});

for (const [label, angle, speed] of [['rear-ended', 0, 14], ['side-swiped', 0.4, 12], ['hit head on', 3.14, 10]]) {
  test(`a car that is ${label} drives back into traffic going the same way`, () => {
    const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
    physics.timestep = DT;
    const scene = new THREE.Scene();
    const world = buildWorld(scene, physics, city);
    const traffic = createTraffic(scene, physics);
    traffic.set('peds', false);
    const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 });
    let braking = false;
    const step = () => {
      garage.drive({ throttle: 0, steer: 0, brake: braking }, DT);
      world.update([garage.current, ...traffic.wrecks.map((w) => w.car)].map((v) => v.body.translation()));
      traffic.update(garage, DT);
      physics.step();
    };
    for (let i = 0; i < 60; i++) step();
    const npc = traffic.sim.cars.find((c) => !c.path && c.s > 8 && c.s < 18);
    for (const other of [...traffic.sim.cars]) if (other !== npc) traffic.sim.takeCar(other);
    traffic.sim.enabled.cars = false;
    const dir = [...npc.dir];
    Object.assign(npc, { cruise: 0, speed: 0 }); // stand still to be hit
    step();
    const yaw = Math.atan2(npc.hx, npc.hz) + angle;
    const [x, z] = [Math.sin(yaw), Math.cos(yaw)];
    const { body } = garage.current;
    body.setTranslation({ x: npc.x - x * 5.5 + z * 0.8, y: 0.8, z: npc.z - z * 5.5 - x * 0.8 }, true);
    body.setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) }, true);
    body.setLinvel({ x: x * speed, y: 0, z: z * speed }, true);
    for (let i = 0; i < 90; i++) step();
    assert.equal(traffic.wrecks.length, 1);
    braking = true;
    // Get the player's car out of the way, onto a road.
    const p = body.translation();
    body.setTranslation({ x: Math.round(p.x / PITCH + 1) * PITCH, y: 0.8, z: (Math.round(p.z / PITCH) + 0.5) * PITCH }, true);
    body.setLinvel({ x: 0, y: 0, z: 0 }, true);
    for (let i = 0; i < 60 * 60 && traffic.wrecks.length; i++) step();
    assert.equal(traffic.wrecks.length, 0, 'still wrecked');
    assert.equal(traffic.sim.cars.length, 1);
    const [car] = traffic.sim.cars;
    assert.deepEqual(car.dir, dir);
    assert.equal(physics.bodies.len(), 2); // player and the car back on rails
    const before = { x: car.x, z: car.z };
    for (let i = 0; i < 40 * 60; i++) step();
    assert.ok(Math.hypot(car.x - before.x, car.z - before.z) > 20, 'did not drive on');
  });
}
