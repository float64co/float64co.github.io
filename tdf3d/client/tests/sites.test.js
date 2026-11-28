import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, nearestSite, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

test('there are nine hospitals and nine police stations, one of each near the start', () => {
  for (const kind of ['hospital', 'police']) {
    const blocks = city.filter((b) => b.site === kind);
    assert.equal(blocks.length, 9);
    for (const b of blocks) assert.equal(b.buildings.length, 1);
    const near = nearestSite(kind, 0, 30);
    assert.ok(Math.hypot(near.x, near.z - 30) < 200);
    // From anywhere, the one chosen is the closest of them.
    for (const [x, z] of [[2000, -1500], [-900, 900], [0, 0]]) {
      const best = Math.min(...blocks.map((b) => Math.hypot(b.x - x, b.z - z)));
      const found = nearestSite(kind, x, z);
      assert.equal(Math.hypot(found.x - x, found.z - z), best);
    }
  }
});

test('arriving puts you on foot outside, clear of walls, with a car you can get into', () => {
  for (const kind of ['hospital', 'police']) {
    const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
    physics.timestep = DT;
    const scene = new THREE.Scene();
    const world = buildWorld(scene, physics, city);
    const cleared = [];
    const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 }, 'car', (s) => cleared.push(s));
    const { door, car, z } = nearestSite(kind, 0, 30);
    garage.arrive(door, car);
    assert.deepEqual(cleared, [door, car]);
    for (let i = 0; i < 180; i++) {
      garage.drive({ throttle: 0, strafe: 0, turn: 0 }, DT);
      world.update([garage.current, ...garage.parked.values()].map((v) => v.body.translation()));
      physics.step();
    }
    assert.equal(garage.current.name, 'on foot');
    for (const [vehicle, spot] of [[garage.current, door], [garage.parked.get('car'), car]]) {
      const p = vehicle.body.translation();
      assert.ok(Math.hypot(p.x - spot.x, p.z - spot.z) < 0.5, `${kind}: pushed to ${p.x}, ${p.z}`);
      assert.ok(p.y > 0 && p.y < 2);
    }
    // The building is behind them, and they face away from it.
    const back = physics.castRay(new RAPIER.Ray({ x: door.x, y: 1, z: door.z }, { x: 0, y: 0, z: 1 }),
      30, true, RAPIER.QueryFilterFlags.EXCLUDE_DYNAMIC);
    assert.ok(back && back.timeOfImpact > 5 && z > door.z);
    // Walk over to the car and get in.
    const p = garage.parked.get('car').body.translation();
    garage.current.body.setTranslation({ x: p.x - 3.5, y: 0.9, z: p.z }, true);
    assert.equal(garage.toggle(), null);
    assert.equal(garage.current.name, 'car');
  }
});
