import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { damageAt, FAST } from '../src/knockdown.js';
import { DOWN } from '../src/foot.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

test('faster hurts more, and the fastest kills', () => {
  assert.ok(damageAt(FAST) < 10);
  assert.ok(damageAt(10) > 20 && damageAt(10) < 50);
  assert.ok(damageAt(25) >= 100);
});

function setup() {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  traffic.set('cars', false);
  traffic.set('peds', false);
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 });
  let damage = 0;
  const step = (input = {}) => {
    garage.drive({ throttle: 0, strafe: 0, turn: 0, brake: true, ...input }, DT);
    world.update([garage.current, ...garage.parked.values()].map((v) => v.body.translation()));
    traffic.update(garage, DT);
    physics.step();
    garage.sync();
    damage += traffic.takeDamage();
  };
  for (let i = 0; i < 60; i++) step();
  assert.equal(garage.toggle(), null);
  for (let i = 0; i < 30; i++) step();
  return { physics, traffic, garage, step, hurt: () => damage };
}

// Send the player's own parked car at them.
function runInto(r, speed) {
  const walker = r.garage.current;
  const p = walker.body.translation();
  const car = r.garage.parked.get('car').body;
  const q = car.rotation();
  const yaw = Math.atan2(2 * (q.x * q.z + q.w * q.y), 1 - 2 * (q.x * q.x + q.y * q.y));
  car.setTranslation({ x: p.x - Math.sin(yaw) * 5, y: car.translation().y, z: p.z - Math.cos(yaw) * 5 }, true);
  const send = () => car.setLinvel({ x: Math.sin(yaw) * speed, y: 0, z: Math.cos(yaw) * speed }, true);
  for (let i = 0; i < 90 && !walker.down; i++) {
    send();
    r.step();
  }
  car.setLinvel({ x: 0, y: 0, z: 0 }, true);
  return walker;
}

test('a fast car knocks you down, hurts, and you get up again', () => {
  const r = setup();
  const bodies = r.physics.bodies.len();
  const walker = runInto(r, 12);
  assert.ok(walker.down, 'still standing');
  assert.ok(r.hurt() > 20 && r.hurt() < 60, `${r.hurt()}`);
  assert.equal(r.physics.bodies.len(), bodies + 9);
  const lost = r.hurt();
  // Move it well clear, and try to walk while on the ground.
  r.garage.parked.get('car').body.setTranslation({ x: 60, y: 0.8, z: 30 }, true);
  for (let i = 0; i < (DOWN - 0.5) * 60; i++) r.step({ throttle: 1 });
  assert.ok(walker.down, 'got up early');
  for (let i = 0; i < 60; i++) r.step();
  assert.ok(!walker.down, 'did not get up');
  assert.equal(r.physics.bodies.len(), bodies);
  assert.equal(r.hurt(), lost, 'hurt more than once');
  const p = walker.body.translation();
  assert.ok(p.y > 0.7 && p.y < 1.2, `height ${p.y}`);
  const before = { x: p.x, z: p.z };
  for (let i = 0; i < 60; i++) r.step({ throttle: 1 });
  const after = walker.body.translation();
  assert.ok(Math.hypot(after.x - before.x, after.z - before.z) > 1, 'cannot walk');
});

test('with no health left you stay down; a slow car only shoves', () => {
  const r = setup();
  runInto(r, 3);
  assert.ok(!r.garage.current.down);
  assert.equal(r.hurt(), 0);
  r.garage.current.able = false;
  const walker = runInto(r, 14);
  for (let i = 0; i < (DOWN + 2) * 60; i++) r.step();
  assert.ok(walker.down);
  // Replacing them leaves nothing behind.
  const bodies = r.physics.bodies.len();
  r.garage.reset();
  assert.equal(r.physics.bodies.len(), bodies - 9);
});

test('traffic can knock you down too', () => {
  const r = setup();
  r.traffic.set('cars', true);
  for (let i = 0; i < 60; i++) r.step();
  const walker = r.garage.current;
  const npc = r.traffic.sim.cars.find((c) => !c.path && c.speed > 7 && c.s < 12);
  // Step out right in front of it.
  walker.body.setTranslation({ x: npc.x + npc.hx * 3.5, y: 0.9, z: npc.z + npc.hz * 3.5 }, true);
  for (let i = 0; i < 60 && !walker.down; i++) r.step();
  assert.ok(walker.down);
  assert.ok(r.hurt() > 0);
});

test('people keep off the whole of a player lying in the road', () => {
  const r = setup();
  r.traffic.set('peds', true);
  const walker = r.garage.current;
  walker.able = false;
  walker.knock(6, 0);
  for (let i = 0; i < 180; i++) r.step();
  const parts = [walker.ragdoll.head, ...walker.ragdoll.shins].map((b) => b.translation());
  // Someone standing on their head and feet is moved off.
  for (const part of parts) {
    const ped = r.traffic.sim.peds[0];
    for (let i = 0; i < 2; i++) {
      Object.assign(ped, { pace: 0, panic: 0, ox: part.x - (ped.x - ped.ox), oz: part.z - (ped.z - ped.oz), x: part.x, z: part.z });
      r.step();
    }
    const gap = Math.hypot(ped.x - part.x, ped.z - part.z);
    assert.ok(gap > 0.4, `still on them: ${gap}`);
  }
});
