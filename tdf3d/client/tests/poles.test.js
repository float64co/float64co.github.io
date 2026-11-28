import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { polePlace, poleKey, CAMERA_GROUPS } from '../src/poles.js';
import { createTrafficSim } from '../src/traffic-sim.js';
import { PITCH } from '../src/city.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;
// The light on the (+z) approach to junction (0, 0).
const APPROACH = [0, 1];
const KEY = poleKey(0, 0, 2);
const spot = polePlace(0, 0, APPROACH);

function setup() {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  // No moving traffic to get in the way. Pedestrians keep the lights switched on.
  traffic.set('cars', false);
  traffic.setRadius(100);
  const garage = createGarage(scene, physics, { x: spot.x, y: 0.8, z: spot.z - 12 });
  const step = (input = { throttle: 0, steer: 0, brake: false }) => {
    garage.drive(input, DT);
    world.update([garage.current.body.translation()]);
    traffic.update(garage, DT);
    physics.step();
  };
  return { physics, scene, traffic, garage, step };
}

// Send the player's vehicle straight at the light.
function ram({ garage, step }, speed, seconds = 3) {
  for (let i = 0; i < 60; i++) step({ throttle: 0, steer: 0, brake: true });
  const { body } = garage.current;
  body.setTranslation({ x: spot.x, y: body.translation().y, z: spot.z - 6 }, true);
  body.setLinvel({ x: 0, y: 0, z: speed }, true);
  let furthest = -Infinity;
  for (let i = 0; i < seconds * 60; i++) {
    step();
    furthest = Math.max(furthest, body.translation().z);
  }
  return furthest;
}

test('a gentle bump stops the car and leaves the light standing', () => {
  const r = setup();
  const furthest = ram(r, 3);
  assert.ok(!r.traffic.poles.down.has(KEY));
  assert.ok(r.traffic.poles.standing.has(KEY));
  assert.ok(furthest < spot.z - 1.9, `car went through: ${furthest - spot.z}`);
});

test('a hard hit knocks the light down and costs the car some speed', () => {
  const r = setup();
  ram(r, 15, 4);
  const fallen = r.traffic.poles.down.get(KEY);
  assert.ok(fallen, 'still standing');
  const up = new THREE.Vector3(0, 1, 0).applyQuaternion(new THREE.Quaternion().copy(fallen.rotation));
  assert.ok(up.y < 0.5, `not toppled: ${up.y}`);
  assert.ok(fallen.position.y > -0.5, 'fell through the ground');
});

test('a bicycle cannot knock a light down', () => {
  const r = setup();
  r.garage.switch('bicycle');
  ram(r, 12);
  assert.equal(r.traffic.poles.down.size, 0);
});

test('lights stand up again once far away, and go when traffic is off', () => {
  const r = setup();
  ram(r, 15);
  assert.ok(r.traffic.poles.down.size);
  r.garage.current.body.setTranslation({ x: 600, y: 0.8, z: 30 }, true);
  r.step();
  assert.equal(r.traffic.poles.down.size, 0);
  const bodies = r.physics.bodies.len();
  assert.equal(bodies, 1);
  r.traffic.set('peds', false);
  r.step();
  const kinds = new Set([...r.traffic.poles.standing.values()].map((p) => p.kind));
  assert.deepEqual([...kinds], ['lamp']);
});

test('the camera ray passes through lights', () => {
  const r = setup();
  r.step();
  r.physics.step();
  const ray = new RAPIER.Ray({ x: spot.x, y: 2, z: spot.z - 3 }, { x: 0, y: 0, z: 1 });
  assert.ok(r.physics.castRay(ray, 6, true), 'bullets should hit it');
  assert.equal(r.physics.castRay(ray, 6, true, undefined, CAMERA_GROUPS), null);
});

// One person on the crossing beside the light, walking in -z towards it.
function walker() {
  const sim = createTrafficSim(3);
  sim.set('cars', false);
  const focus = { x: spot.x, z: spot.z };
  for (let i = 0; i < 60; i++) sim.step(DT, focus);
  const [ped] = sim.peds;
  for (const other of sim.peds) if (other !== ped) other.x += 5000;
  sim.enabled.peds = false;
  const path = 8.5;
  ped.data.set([0, 0, -1, -1, 0, -1, -1, 1, 1], ped.data.length - 9);
  const start = spot.z + 6;
  Object.assign(ped, { s: path - start, pace: 1.5, ox: 0, oz: 0, x: path, z: start });
  return { sim, ped, focus, path };
}
const still = (ax, az, bx, bz) => ({ ax, az, bx, bz, avx: 0, avy: 0, avz: 0, bvx: 0, bvy: 0, bvz: 0 });
function gap(p, o) {
  const dx = o.bx - o.ax;
  const dz = o.bz - o.az;
  const u = Math.max(0, Math.min(1, ((p.x - o.ax) * dx + (p.z - o.az) * dz) / (dx * dx + dz * dz)));
  return Math.hypot(p.x - o.ax - dx * u, p.z - o.az - dz * u);
}

for (const [label, ax, dz1, bx, dz2] of [
  ['square across the way', -2, 0, 2.5, 0],
  ['at a slant', -2, 1.2, 2, -1.2],
  ['slanting the other way', -2, -1.2, 2, 1.2],
  ['lying along the way', 0.1, -2, 0, 2],
  ['mostly to one side', -4, 0, 0.3, 0],
]) {
  test(`people walk round a downed pole ${label}`, () => {
    const { sim, ped, focus, path } = walker();
    const middle = ped.z - 3.5;
    const pole = still(path + ax, middle + dz1, path + bx, middle + dz2);
    let closest = Infinity;
    let passed = false;
    for (let i = 0; i < 20 * 60 && !passed; i++) {
      sim.step(DT, focus, [], [pole]);
      closest = Math.min(closest, gap(ped, pole));
      passed = ped.z < middle - 2.5;
    }
    assert.ok(passed, `stuck at ${ped.x - path}, ${ped.z - middle}`);
    assert.ok(closest > 0.45 - 1e-6, `came within ${closest}`);
    assert.equal(sim.fallen.length, 0);
  });
}

test('a moving pole knocks people down, a still or slow one does not', () => {
  for (const [speed, down] of [[0, 0], [1, 0], [6, 1]]) {
    const { sim, ped, focus, path } = walker();
    const pole = { ...still(path - 1, ped.z, path + 1, ped.z), avx: speed, bvx: speed };
    sim.step(DT, focus, [], [pole]);
    assert.equal(sim.fallen.length, down, `at ${speed} m/s`);
  }
});

test('a light being knocked over takes out someone standing where it falls', () => {
  const r = setup();
  for (let i = 0; i < 60; i++) r.step({ throttle: 0, steer: 0, brake: true });
  const [ped] = r.traffic.sim.peds;
  // Pedestrians stay switched on, or there would be no lights.
  for (const other of r.traffic.sim.peds) if (other !== ped) other.x += 5000;
  // Stand them just beyond the light, where the car will throw it.
  Object.assign(ped, { x: spot.x, z: spot.z + 2.5, pace: 0 });
  ped.ox = 0;
  const { body } = r.garage.current;
  body.setTranslation({ x: spot.x, y: body.translation().y, z: spot.z - 6 }, true);
  body.setLinvel({ x: 0, y: 0, z: 15 }, true);
  const hold = { s: ped.s };
  for (let i = 0; i < 90 && !r.traffic.sim.fallen.some((f) => f.ped === ped); i++) {
    // Keep them there until the pole arrives.
    Object.assign(ped, { panic: 0, s: hold.s, ox: ped.ox + spot.x - ped.x, oz: ped.oz + spot.z + 2.5 - ped.z });
    r.step();
  }
  assert.ok(r.traffic.poles.down.has(KEY), 'the light did not break off');
  assert.ok(r.traffic.sim.fallen.some((f) => f.ped === ped), 'still standing');
  // It was the pole that got them: the car had not arrived.
  assert.ok(r.garage.current.body.translation().z < spot.z, 'run over instead');
});

// Walk one person along the pavement past the light, displaced `across` metres
// from the path (towards the light), and report how close they come to it.
function walkPast(across, down = false) {
  const sim = createTrafficSim(3);
  sim.set('cars', false);
  const focus = { x: spot.x, z: spot.z };
  for (let i = 0; i < 60; i++) sim.step(DT, focus);
  const [ped] = sim.peds;
  for (const other of sim.peds) if (other !== ped) other.x += 5000;
  sim.enabled.peds = false;
  if (down) sim.setPole(spot.x, spot.z, true);
  // Put them on the crossing beside the light, walking in -z towards it.
  const path = 8.5;
  ped.data.set([0, 0, -1, -1, 0, -1, -1, 1, 1], ped.data.length - 9);
  const start = spot.z + 6;
  Object.assign(ped, { s: path - start, pace: 1.5, ox: spot.x - path, oz: 0 });
  ped.ox *= across / Math.abs(ped.ox);
  Object.assign(ped, { x: path + ped.ox, z: start });
  let closest = Infinity;
  let passed = false;
  const aside = ped.ox;
  for (let i = 0; i < 5 * 60; i++) {
    // Hold them off course until they are nearly there, or they drift back first.
    if (ped.z > spot.z + (down ? 0.05 : 1)) ped.ox = aside;
    sim.step(DT, focus);
    closest = Math.min(closest, Math.hypot(ped.x - spot.x, ped.z - spot.z));
    passed ||= ped.z < spot.z - 0.5;
  }
  return { closest, passed };
}

test('people walk round a standing light, not through it', () => {
  // The light is 0.9 m from the path: try to walk straight at it and either side.
  // 0.9 is dead centre. The others pass it on one side or the other.
  for (let across = 0.5; across <= 1.31; across += 0.05) {
    const { closest, passed } = walkPast(across);
    assert.ok(closest > 0.5 - 1e-6, `${across}: came within ${closest}`);
    assert.ok(closest < 1, `${across}: never went near it (${closest})`);
    assert.ok(passed, `${across}: stuck`);
  }
});

test('people can walk where a light has been knocked down', () => {
  assert.ok(walkPast(0.9, true).closest < 0.2);
});

test('knocking a light down tells the simulation', () => {
  const r = setup();
  const told = [];
  r.traffic.sim.setPole = (x, z, down) => told.push([x, z, down]);
  ram(r, 15);
  assert.deepEqual(told[0], [spot.x, spot.z, true]);
});
