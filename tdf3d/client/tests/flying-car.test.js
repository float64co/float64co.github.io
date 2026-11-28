import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { createGroundVehicle } from '../src/ground.js';
import { createTraffic } from '../src/traffic.js';
import { createGarage } from '../src/garage.js';

await RAPIER.init();
const DT = 1 / 60;
const idle = { throttle: 0, steer: 0, brake: false, descend: false };

function setup(name = 'car') {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  physics.createCollider(RAPIER.ColliderDesc.cuboid(5000, 1, 5000).setTranslation(0, -1, 0));
  const scene = new THREE.Scene();
  const car = createGroundVehicle(name, scene, physics, { x: 0, y: 0.9, z: 0 });
  const run = (seconds, input = {}) => {
    for (let i = 0; i < Math.round(seconds * 60); i++) {
      car.drive({ ...idle, ...input }, DT);
      physics.step();
      car.sync();
    }
  };
  run(2);
  const upright = () => new THREE.Vector3(0, 1, 0).applyQuaternion(car.body.rotation()).y;
  return { physics, scene, car, run, upright, at: () => car.body.translation() };
}

test('it climbs, hovers, flies, turns and descends like the helicopter', () => {
  const r = setup();
  assert.ok(r.car.setFlying(true));
  r.run(2, { brake: true }); // Space
  assert.ok(r.at().y > 20, `${r.at().y}`);
  r.run(2);
  const y = r.at().y;
  r.run(3);
  assert.ok(Math.abs(r.at().y - y) < 0.1, 'did not hover');
  r.run(4, { throttle: 1 });
  assert.ok(r.at().z > 80 && Math.abs(r.at().x) < 1);
  r.run(1, { steer: 1 });
  r.run(3, { throttle: 1 });
  assert.ok(r.at().x > 40, 'did not turn left');
  assert.ok(r.upright() > 0.999);
  r.run(8, { descend: true });
  assert.ok(r.at().y > 1 && r.at().y < 1.35, `rests at ${r.at().y}`);
});

test('switched off in mid-air it falls and lands on its wheels', () => {
  const r = setup();
  r.car.setFlying(true);
  r.run(3, { brake: true, throttle: 0.3 });
  const high = r.at().y;
  assert.ok(high > 30);
  assert.ok(!r.car.setFlying(false));
  r.run(1);
  assert.ok(r.at().y < high - 3, 'not falling');
  r.run(8);
  assert.ok(r.at().y > 0.3 && r.at().y < 1.3, `${r.at().y}`);
  assert.ok(r.upright() > 0.9, 'landed on its side');
  const z = r.at().z;
  r.run(3, { throttle: 1 });
  assert.ok(r.at().z - z > 5, 'cannot drive afterwards');
});

test('the wheels turn hubcaps down in flight, and back afterwards', () => {
  const r = setup();
  const facing = () => {
    r.car.mesh.updateWorldMatrix(true, true);
    const found = [];
    r.car.mesh.traverse((o) => {
      // A hubcap is a child of its wheel, offset along the axle.
      if (!o.isMesh || !o.parent.isMesh || Math.abs(Math.abs(o.position.x) - 0.185) > 1e-6) return;
      found.push(o.getWorldPosition(new THREE.Vector3()).sub(o.parent.getWorldPosition(new THREE.Vector3())).normalize().y);
    });
    return found;
  };
  assert.equal(facing().length, 4);
  assert.ok(facing().every((y) => Math.abs(y) < 0.1));
  r.car.setFlying(true);
  r.run(2);
  assert.ok(facing().every((y) => y < -0.99), `${facing()}`);
  r.run(2, { throttle: 1, steer: 1 });
  assert.ok(facing().every((y) => y < -0.9), 'moved from flat');
  assert.ok(r.car.hubs().every((h) => h.y < r.at().y - 0.2 && h.y > 0), `${r.car.hubs().map((h) => h.y)}`);
  r.car.setFlying(false);
  r.run(3);
  assert.ok(facing().every((y) => Math.abs(y) < 0.1));
});

test('only cars fly, a wreck cannot, and parking brings it down', () => {
  assert.equal(setup('motorbike').car.setFlying(true), false);
  const r = setup();
  r.car.condition.hp = 0;
  assert.equal(r.car.setFlying(true), false);
  r.car.condition.hp = 100;
  r.car.setFlying(true);
  r.run(2, { brake: true });
  r.car.condition.hp = 0; // shot out of the sky
  r.run(1);
  assert.ok(!r.car.flying);
  r.car.condition.hp = 100;
  r.car.setFlying(true);
  r.car.park(DT);
  assert.ok(!r.car.flying);
});

test('every flying car has the same short blue trail under its wheels', async () => {
  const { sparksOf, TRAIL } = await import('../src/spark-trails.js');
  const shape = (vx) => {
    const points = [];
    for (let time = 0; time < 2; time += 0.013)
      sparksOf({ x: 0, y: 10, z: 0, vx, vz: 0 }, time, 1, (x, y, z) => points.push({ x, y }));
    return { back: -Math.min(...points.map((p) => p.x)), below: 10 - Math.min(...points.map((p) => p.y)), above: Math.max(...points.map((p) => p.y)) };
  };
  const [still, traffic, flatOut] = [shape(0), shape(18), shape(40)];
  assert.ok(still.back < 0.25);
  assert.ok(Math.abs(traffic.back - flatOut.back) < 0.1, `${traffic.back} and ${flatOut.back}`);
  assert.ok(flatOut.back < TRAIL.back + 0.25 && flatOut.below < 0.8 && flatOut.above < 10);

  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  physics.createCollider(RAPIER.ColliderDesc.cuboid(5000, 1, 5000).setTranslation(0, -1, 0));
  const traffic2 = createTraffic(new THREE.Scene(), physics);
  traffic2.set('peds', false);
  traffic2.setRadius(450);
  const garage = createGarage(new THREE.Scene(), physics, { x: 0, y: 1.5, z: 30 });
  const step = () => {
    garage.drive({ ...idle }, DT);
    traffic2.update(garage, DT);
    physics.step();
    garage.sync();
    traffic2.draw(garage.current.body.translation());
  };
  for (let i = 0; i < 60; i++) step();
  const wheels = () => traffic2.trails.mesh.count / TRAIL.each;
  const flying = traffic2.air.wheels(garage.current.body.translation()).length;
  assert.ok(flying > 100, `${flying}`);
  assert.equal(wheels(), flying, 'traffic overhead has none');
  garage.current.setFlying(true);
  step();
  assert.equal(wheels(), traffic2.air.wheels(garage.current.body.translation()).length + 4);
  assert.ok(traffic2.trails.mesh.material.color.b > traffic2.trails.mesh.material.color.r);
});

// Drop a car from `height` metres and report its HP once it has settled.
async function dropped(height, { flipped = false, flyDown = false } = {}) {
  const { watch } = await import('../src/damage.js');
  const r = setup();
  const step = (input = {}) => {
    r.car.drive({ ...idle, ...input }, DT);
    r.physics.step();
    watch(r.car);
  };
  for (let i = 0; i < 60; i++) step();
  r.car.setFlying(true);
  r.car.body.setTranslation({ x: 0, y: height, z: 0 }, true);
  for (let i = 0; i < 90; i++) step();
  assert.equal(r.car.condition.hp, 100, 'damaged while flying');
  if (flyDown) {
    for (let i = 0; i < 900; i++) step({ descend: true });
  } else {
    r.car.setFlying(false);
    if (flipped) r.car.body.setRotation({ x: 0, y: 0, z: 1, w: 0 }, true);
    for (let i = 0; i < 600; i++) step();
  }
  return r.car.condition;
}

test('the higher the drop, the more damage; small ones and flying down do none', async () => {
  const hp = [];
  for (const height of [3, 12, 30, 150]) hp.push((await dropped(height)).hp);
  console.log({ hp });
  assert.equal(hp[0], 100);
  assert.ok(hp[1] < 100 && hp[1] > 70, `${hp[1]}`);
  assert.ok(hp[2] < hp[1] - 15 && hp[2] > 20, `${hp[2]}`);
  assert.equal(hp[3], 0, 'survived 150 m');
  assert.equal((await dropped(60, { flyDown: true })).hp, 100);
});

test('whichever side lands is dented, once for each fall', async () => {
  const level = await dropped(30);
  assert.equal(level.dents.length, 2);
  assert.ok(level.dents.every((d) => d.at.y < 0 && d.push.y > 0.9), 'not underneath');
  const roof = await dropped(30, { flipped: true });
  assert.ok(roof.dents.slice(0, 2).every((d) => d.at.y > 0 && d.push.y < -0.9), 'not on the roof');
});
