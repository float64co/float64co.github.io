import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { createGarage } from '../src/garage.js';
import { buildWorld } from '../src/world.js';
import { generateCity, PITCH } from '../src/city.js';
import { createTraffic } from '../src/traffic.js';
import { createTrafficSim, LANE } from '../src/traffic-sim.js';
import { lampPlace, lampKey } from '../src/poles.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

function setup(spawn) {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  traffic.set('cars', false);
  traffic.setRadius(150);
  const garage = createGarage(scene, physics, spawn);
  const step = () => {
    garage.drive({ throttle: 0, steer: 0, brake: false }, DT);
    world.update([garage.current.body.translation()]);
    traffic.update(garage, DT);
    physics.step();
  };
  return { physics, traffic, garage, step };
}

test('lamps stand on both sides of every road, clear of the lanes', () => {
  for (let n = 0; n < 4; n++) {
    const { x, z, yaw } = lampPlace(2, -3, n);
    const off = n < 2 ? x - 2 * PITCH : z + 3 * PITCH; // from the road centre line
    assert.ok(Math.abs(off) > LANE + 1 + 1 && Math.abs(off) < 8, `${n}: ${off}`);
    // The arm reaches back towards the road.
    const reach = n < 2 ? Math.cos(yaw) : -Math.sin(yaw);
    assert.ok(reach * off < -Math.abs(off) * 0.99, `${n}: arm points away`);
  }
});

test('a lamp stops a gentle bump and falls to a hard one', () => {
  const spot = lampPlace(0, 0, 0);
  for (const [speed, falls] of [[3, false], [15, true]]) {
    const r = setup({ x: spot.x, y: 0.8, z: spot.z - 10 });
    for (let i = 0; i < 60; i++) r.step();
    const { body } = r.garage.current;
    body.setTranslation({ x: spot.x, y: body.translation().y, z: spot.z - 6 }, true);
    body.setLinvel({ x: 0, y: 0, z: speed }, true);
    let furthest = -Infinity;
    for (let i = 0; i < 180; i++) {
      r.step();
      furthest = Math.max(furthest, body.translation().z);
    }
    assert.equal(r.traffic.poles.down.has(lampKey(0, 0, 0)), falls, `at ${speed} m/s`);
    if (!falls) assert.ok(furthest < spot.z - 1.9, 'went through it');
  }
});

test('lamps light up at night and a broken one goes out', () => {
  const r = setup({ x: 0, y: 0.8, z: 30 });
  const { lamps, poles } = r.traffic;
  const focus = { x: 0, y: 0, z: 30 };
  r.step();
  r.traffic.draw(focus, false);
  assert.ok(lamps.posts.count > 20);
  assert.equal(lamps.pools.count, 0);
  r.traffic.draw(focus, true);
  assert.equal(lamps.pools.count, lamps.posts.count);
  poles.down.set(lampKey(0, 0, 0), {
    kind: 'lamp', position: { x: 7, y: 0, z: 20 }, rotation: { x: 0, y: 0, z: 0, w: 1 },
  });
  r.traffic.draw(focus, true);
  assert.equal(lamps.pools.count, lamps.posts.count - 1);
});

test('people cannot walk through a standing lamp', () => {
  for (const n of [0, 1, 2, 3]) {
    const spot = lampPlace(1, 1, n);
    for (const down of [false, true]) {
      const sim = createTrafficSim(3);
      sim.set('cars', false);
      const focus = { x: spot.x, z: spot.z };
      for (let i = 0; i < 60; i++) sim.step(DT, focus);
      sim.setPole(spot.x, spot.z, down);
      // Someone close enough to the player to be updated every step.
      const away = (o) => Math.hypot(o.x - spot.x, o.z - spot.z);
      const ped = [...sim.peds].sort((a, b) => away(a) - away(b))[0];
      assert.ok(away(ped) < 100);
      for (const other of sim.peds) if (other !== ped) other.x += 5000;
      sim.enabled.peds = false;
      // Stand them still, almost where the lamp is.
      Object.assign(ped, { pace: 0, s: 5 });
      sim.step(DT, focus);
      const path = { x: ped.x - ped.ox, z: ped.z - ped.oz };
      Object.assign(ped, { ox: spot.x + 0.1 - path.x, oz: spot.z - path.z });
      sim.step(DT, focus);
      // Drifting back to the path moves them a little, so compare with the radius.
      assert.ok(down ? away(ped) < 0.2 : away(ped) > 0.5 - 1e-6, `lamp ${n}, down ${down}: ${away(ped)}`);
    }
  }
});
