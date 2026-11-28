import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { overlaps } from '../src/wrecks.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

function setup(name = 'car') {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  traffic.set('peds', false);
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 }, name);
  const step = () => {
    garage.drive({ throttle: 0, steer: 0, brake: false }, DT);
    world.update([garage.current.body.translation()]);
    traffic.update(garage, DT);
    physics.step();
    traffic.draw(garage.current.body.translation());
  };
  for (let i = 0; i < 90; i++) step();
  return { physics, garage, traffic, step };
}

// Park one NPC car, then launch the player at it from `angle` radians off its heading.
function crash(name, speed, angle = 0) {
  const game = setup(name);
  const { garage, traffic, step } = game;
  const npc = traffic.sim.cars.find((c) => !c.path && c.s > 8 && c.s < 18);
  for (const other of [...traffic.sim.cars]) if (other !== npc) traffic.sim.takeCar(other);
  traffic.sim.enabled.cars = false; // no newcomers
  npc.cruise = 0;
  npc.speed = 0;
  step();
  const target = { x: npc.x, z: npc.z, hx: npc.hx, hz: npc.hz };
  const yaw = Math.atan2(npc.hx, npc.hz) + angle;
  const dir = { x: Math.sin(yaw), z: Math.cos(yaw) };
  const { body } = garage.current;
  body.setTranslation({ x: npc.x - dir.x * 7, y: 0.8, z: npc.z - dir.z * 7 }, true);
  body.setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) }, true);
  body.setLinvel({ x: dir.x * speed, y: 0, z: dir.z * speed }, true);
  const along = (b) => b.linvel().x * dir.x + b.linvel().z * dir.z;
  let before = speed;
  for (let i = 0; i < 90 && !traffic.wrecks.length; i++) {
    before = along(body);
    step();
  }
  assert.equal(traffic.wrecks.length, 1, 'never became a wreck');
  const wreck = traffic.wrecks[0].car.body;
  for (let i = 0; i < 12; i++) step();
  return { ...game, target, wreck, before, mine: along(body), theirs: along(wreck) };
}

test('rear-ending a car shares momentum with it', () => {
  const r = crash('car', 15);
  console.log('rear-end', { before: r.before, mine: r.mine, theirs: r.theirs });
  assert.ok(r.theirs > 4, 'target barely moved');
  assert.ok(r.mine < r.before - 4, 'striker barely slowed');
  assert.ok(r.mine > 1, 'striker stopped dead, like hitting a wall');
  assert.ok(Math.abs(r.mine + r.theirs - r.before) / r.before < 0.3, 'momentum not roughly kept');
});

test('a side impact shoves the car sideways', () => {
  const r = crash('car', 15, Math.PI / 2);
  for (let i = 0; i < 60; i++) r.step();
  const p = r.wreck.translation();
  const sideways = Math.abs((p.x - r.target.x) * -r.target.hz + (p.z - r.target.z) * r.target.hx);
  console.log('side impact moved', sideways);
  assert.ok(sideways > 0.7);
});

test('a bicycle barely moves a car', () => {
  const r = crash('bicycle', 8);
  assert.ok(r.theirs < 2, `car flew off at ${r.theirs}`);
});

test('traffic is left alone by a car that is not touching it', () => {
  const { traffic, step } = setup();
  for (let i = 0; i < 60 * 60; i++) step();
  assert.equal(traffic.wrecks.length, 0);
});

test('turning cars off removes wrecks and their bodies', () => {
  const r = crash('car', 15);
  r.traffic.set('cars', false);
  assert.equal(r.traffic.wrecks.length, 0);
  assert.equal(r.physics.bodies.len(), 1);
});

test('footprint overlap handles rotation', () => {
  const box = (x, z, hx = 0, hz = 1) => ({ x, z, hx, hz, halfL: 2, halfW: 1 });
  assert.ok(!overlaps(box(0, 0), box(2.2, 0)));
  assert.ok(overlaps(box(0, 0), box(2.2, 0, 1, 0)));
  assert.ok(!overlaps(box(0, 0), box(0, 4.2)));
  assert.ok(overlaps(box(0, 0), box(0, 3.9)));
});

test('traffic queuing behind a stopped player does not count as a crash', () => {
  const { garage, traffic, step } = setup();
  const lead = traffic.sim.cars.find((c) => !c.path && c.s > 15 && c.s < 22);
  const yaw = Math.atan2(lead.hx, lead.hz);
  const spot = { x: lead.x + lead.hx * 12, z: lead.z + lead.hz * 12 };
  traffic.clearNear(spot);
  garage.current.body.setTranslation({ x: spot.x, y: 0.8, z: spot.z }, true);
  garage.current.body.setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) }, true);
  let closest = Infinity;
  for (let i = 0; i < 20 * 60; i++) {
    step();
    const p = garage.current.body.translation();
    for (const c of traffic.sim.cars) closest = Math.min(closest, Math.hypot(c.x - p.x, c.z - p.z));
  }
  console.log('queue', { closest, wrecks: traffic.wrecks.length });
  assert.ok(closest < 8, 'nobody queued');
  assert.equal(traffic.wrecks.length, 0);
});

test('a struck car can set off the one in front', () => {
  const { garage, traffic, step } = setup();
  const [first, second] = traffic.sim.cars.filter((c) => !c.path && c.s > 8 && c.s < 18);
  for (const other of [...traffic.sim.cars]) if (other !== first && other !== second) traffic.sim.takeCar(other);
  traffic.sim.enabled.cars = false;
  // Stand the second car just ahead of the first.
  Object.assign(second, { i: first.i, j: first.j, dir: first.dir, next: first.next, turn: first.turn, s: first.s + 5 });
  for (const c of [first, second]) Object.assign(c, { cruise: 0, speed: 0 });
  step();
  const { hx, hz } = first;
  const yaw = Math.atan2(hx, hz);
  const { body } = garage.current;
  body.setTranslation({ x: first.x - hx * 7, y: 0.8, z: first.z - hz * 7 }, true);
  body.setRotation({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) }, true);
  body.setLinvel({ x: hx * 18, y: 0, z: hz * 18 }, true);
  for (let i = 0; i < 90; i++) step();
  assert.equal(traffic.wrecks.length, 2);
});
