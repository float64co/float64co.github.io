import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { carjack } from '../src/carjack.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

function setup() {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  traffic.setRadius(200);
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 });
  const step = () => {
    garage.drive({ throttle: 0, strafe: 0, turn: 0, brake: true }, DT);
    world.update([garage.current, ...garage.parked.values(),
      ...[...traffic.wrecks, ...traffic.police.units].map((w) => w.car)].map((v) => v.body.translation()));
    traffic.update(garage, DT);
    physics.step();
    traffic.draw(garage.current.body.translation());
  };
  for (let i = 0; i < 60; i++) step();
  assert.equal(garage.toggle(), null);
  const walkTo = (x, z) => garage.current.body.setTranslation({ x, y: 0.9, z }, true);
  return { physics, traffic, garage, step, walkTo };
}

// Stop one NPC car and walk up to it.
function approach(r) {
  const p = r.garage.current.body.translation();
  const npc = r.traffic.sim.cars
    .filter((c) => !c.path && Math.hypot(c.x - p.x, c.z - p.z) < 120)[0];
  Object.assign(npc, { cruise: 0, speed: 0 });
  r.walkTo(npc.x + npc.hz * 2.2, npc.z - npc.hx * 2.2);
  return { npc, at: { x: npc.x, z: npc.z, shade: npc.shade } };
}

test('taking an NPC car puts you in it and leaves the driver lying in the road', () => {
  const r = setup();
  const { npc, at } = approach(r);
  assert.ok(carjack(r.garage, r.traffic));
  assert.equal(r.garage.current.name, 'car');
  const p = r.garage.current.body.translation();
  assert.ok(Math.hypot(p.x - at.x, p.z - at.z) < 0.5, 'not where the car was');
  assert.ok(!r.traffic.sim.cars.includes(npc));
  assert.equal(r.traffic.sim.fallen.length, 1);
  assert.equal(r.traffic.ragdolls.size, 1);
  assert.equal(r.traffic.wanted.level, 1);
  assert.equal(r.garage.parked.size, 1, 'your own car stays where you left it');

  // Still there a minute later: no timer.
  r.traffic.wanted.immune = true;
  const [driver] = r.traffic.sim.fallen;
  for (let i = 0; i < 60 * 60; i++) r.step();
  assert.ok(r.traffic.sim.fallen.includes(driver), 'cleared away on a timer');
  assert.ok(r.traffic.ragdolls.has(driver));
  // Move away and they are gone.
  r.garage.current.body.setTranslation({ x: 400, y: 0.9, z: PITCH / 2 }, true);
  r.step();
  assert.ok(!r.traffic.sim.fallen.includes(driver));
  assert.ok(!r.traffic.ragdolls.has(driver));
});

test('getting out of a stolen car leaves it there with your own, both usable', () => {
  const r = setup();
  r.traffic.wanted.immune = true;
  approach(r);
  assert.ok(carjack(r.garage, r.traffic));
  for (let i = 0; i < 120; i++) r.step();
  assert.equal(r.garage.toggle(), null);
  assert.equal(r.garage.parked.size, 2);
  assert.equal(r.garage.toggle(), null, 'back in');
  assert.equal(r.garage.parked.size, 1);
  assert.equal(r.garage.current.name, 'car');
  // Left far behind, a stolen car is forgotten; your own is not.
  assert.equal(r.garage.toggle(), null);
  r.walkTo(600, PITCH / 2);
  r.step();
  assert.equal(r.garage.parked.size, 1);
  assert.ok(![...r.garage.parked.values()][0].stolen);
});

test('nothing happens out of reach, or to a car going by fast', () => {
  const r = setup();
  const { npc } = approach(r);
  r.walkTo(npc.x + npc.hz * 6, npc.z - npc.hx * 6);
  assert.ok(!carjack(r.garage, r.traffic));
  r.walkTo(npc.x + npc.hz * 2.2, npc.z - npc.hx * 2.2);
  npc.speed = 15;
  assert.ok(!carjack(r.garage, r.traffic));
});

test('a police car can be taken, and its officer gets up and comes after you', () => {
  const r = setup();
  r.traffic.police.settings.cars = 1;
  r.traffic.police.settings.guns = false;
  r.traffic.wanted.set(1);
  for (let i = 0; i < 90 && !r.traffic.police.units.length; i++) r.step();
  const [unit] = r.traffic.police.units;
  const c = unit.car.body.translation();
  const q = unit.car.body.rotation();
  const yaw = Math.atan2(2 * (q.x * q.z + q.w * q.y), 1 - 2 * (q.x * q.x + q.y * q.y));
  unit.car.body.setLinvel({ x: 0, y: 0, z: 0 }, true);
  r.walkTo(c.x + Math.cos(yaw) * 2.2, c.z - Math.sin(yaw) * 2.2);
  assert.ok(carjack(r.garage, r.traffic));
  assert.equal(r.traffic.police.units.length, 0);
  const [officer] = r.traffic.police.officers();
  assert.ok(officer.down, 'driver not thrown out');
  assert.ok(r.traffic.wanted.level >= 2);
  for (let i = 0; i < 8 * 60; i++) r.step();
  assert.ok(r.traffic.police.officers().includes(officer));
  assert.ok(!officer.down, 'did not get up');
});

test('a dead officer lies there until you leave', () => {
  const r = setup();
  r.traffic.police.settings.cars = 1;
  r.traffic.police.settings.guns = false;
  r.traffic.wanted.set(1);
  for (let i = 0; i < 60 * 60 && !r.traffic.police.officers().length; i++) r.step();
  const [officer] = r.traffic.police.officers();
  officer.hurt(1000, 0, 0);
  r.traffic.wanted.clear();
  for (let i = 0; i < 60 * 60; i++) r.step();
  assert.ok(r.traffic.police.officers().includes(officer), 'cleared away on a timer');
  r.walkTo(500, PITCH / 2);
  r.step();
  assert.ok(!r.traffic.police.officers().includes(officer));
});
