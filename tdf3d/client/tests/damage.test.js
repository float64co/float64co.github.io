import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { createCarBody } from '../src/carbody.js';
import { createCondition, collide, shot, burn, onFire, BURN_TIME, CAR_HP } from '../src/damage.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;
const level = { x: 0, y: 0, z: 0, w: 1 };

test('only sudden changes of speed are collisions, and they dent the side that hit', () => {
  const c = createCondition();
  collide(c, level, { x: 0, y: 0, z: -0.3 }); // hard braking
  assert.equal(c.hp, CAR_HP);
  assert.equal(c.version, 0);
  collide(c, level, { x: 0, y: 0, z: -9 }); // stopped dead from ahead
  assert.ok(c.hp < 90 && c.hp > 60, `${c.hp}`);
  assert.ok(c.dents[0].at.z > 1.9, 'not at the front');
  assert.ok(c.dents[0].push.z < -0.9, 'not pushed in');
  // Facing -x, a push towards -x comes from behind.
  const turned = { x: 0, y: -Math.SQRT1_2, z: 0, w: Math.SQRT1_2 };
  collide(c, turned, { x: -9, y: 0, z: 0 });
  assert.ok(c.dents[1].at.z < -1.9, `${c.dents[1].at.z}`);
});

const extent = (body) => {
  body.group.updateMatrixWorld();
  return new THREE.Box3().setFromObject(body.group, true);
};

test('a crash crumples the body inwards without tearing or turning it inside out', () => {
  const body = createCarBody();
  const whole = extent(body);
  const c = createCondition();
  collide(c, level, { x: 0, y: 0, z: -12 });
  body.update(c);
  const front = extent(body);
  assert.ok(front.max.z < whole.max.z + 0.05);
  assert.ok(Math.abs(front.min.z - whole.min.z) < 1e-6, 'the back moved');
  // The nose is pushed in.
  const mesh = body.group.children[0].geometry.attributes.position;
  let nose = 0;
  let pushed = 0;
  const intact = createCarBody().group.children[0].geometry.attributes.position;
  for (let n = 0; n < mesh.count; n++) {
    if (intact.getZ(n) < 1.99) continue;
    nose++;
    if (mesh.getZ(n) < 1.85) pushed++;
  }
  assert.ok(pushed > nose * 0.3, `${pushed} of ${nose}`);
  // Vertices that shared a place still do.
  const places = new Map();
  for (let n = 0; n < mesh.count; n++) {
    const key = `${intact.getX(n).toFixed(3)} ${intact.getY(n).toFixed(3)} ${intact.getZ(n).toFixed(3)}`;
    const now = [mesh.getX(n), mesh.getY(n), mesh.getZ(n)];
    if (!places.has(key)) places.set(key, now);
    places.get(key).forEach((v, k) => assert.ok(Math.abs(v - now[k]) < 1e-5, 'split at an edge'));
  }
  // However many hits, nothing passes through the middle.
  for (let i = 0; i < 60; i++) collide(c, level, { x: 0, y: 0, z: -20 });
  body.update(c);
  for (let n = 0; n < mesh.count; n++) {
    if (intact.getZ(n) > 1.99) assert.ok(mesh.getZ(n) > 0.2);
  }
});

test('at no health it burns for a while, then is burnt out and black', () => {
  const c = createCondition();
  for (let i = 0; i < 20; i++) shot(c, { x: 1, y: 0, z: 0 }, { x: -1, y: 0, z: 0 }, 25);
  assert.equal(c.hp, 0);
  assert.ok(onFire(c) && !c.burnt);
  for (let t = 0; t < BURN_TIME - 0.5; t += DT) burn(c, DT);
  assert.ok(onFire(c));
  for (let t = 0; t < 1; t += DT) burn(c, DT);
  assert.ok(c.burnt && !onFire(c));
  const body = createCarBody();
  body.update(c);
  assert.ok(body.group.children[0].material.color.r < 0.05, 'not charred');
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
  let hurt = 0;
  const step = (input = { throttle: 0, steer: 0, brake: false }) => {
    garage.drive(input, DT);
    world.update([garage.current, ...garage.parked.values()].map((v) => v.body.translation()));
    traffic.update(garage, DT);
    physics.step();
    garage.sync();
    traffic.draw(garage.current.body.translation());
    hurt += traffic.takeDamage();
  };
  for (let i = 0; i < 90; i++) step();
  return { physics, scene, traffic, garage, step, hurt: () => hurt };
}

test('driving gently costs nothing; hitting a wall hurts the car', () => {
  const r = setup();
  const { condition } = r.garage.current;
  for (let i = 0; i < 200; i++) r.step({ throttle: 1, steer: 0, brake: false }); // down the road
  for (let i = 0; i < 90; i++) r.step({ throttle: 0, steer: 0, brake: true });
  assert.equal(condition.hp, CAR_HP, 'ordinary driving did damage');
  const p = r.garage.current.body.translation();
  const q = r.garage.current.body.rotation();
  const forward = new THREE.Vector3(0, 0, 1).applyQuaternion(new THREE.Quaternion().copy(q));
  r.physics.createCollider(RAPIER.ColliderDesc.cuboid(6, 3, 6)
    .setTranslation(p.x + forward.x * 12, 3, p.z + forward.z * 12));
  r.garage.current.body.setLinvel({ x: forward.x * 18, y: 0, z: forward.z * 18 }, true);
  for (let i = 0; i < 90; i++) r.step();
  assert.ok(condition.hp < 80 && condition.hp > 20, `${condition.hp}`);
  assert.ok(condition.dents.some((d) => d.at.z > 1.5), 'no dent at the front');
});

test('a burning car stops working and hurts whoever stays in it; get out and it burns out', () => {
  const r = setup();
  const car = r.garage.current;
  car.condition.hp = 0;
  const start = car.body.translation().z;
  for (let i = 0; i < 120; i++) r.step({ throttle: 1, steer: 0, brake: false });
  assert.ok(Math.abs(car.body.translation().z - start) < 1, 'the engine still runs');
  assert.ok(r.hurt() > 8 && r.hurt() < 16, `${r.hurt()}`);
  // Puffs come by the clock, not the step, so give them a moment of real time.
  const until = performance.now() + 300;
  while (performance.now() < until) r.traffic.draw(car.body.translation());
  assert.ok(r.traffic.smoke.kinds.flame.mesh.count > 5, `no flames: ${r.traffic.smoke.kinds.flame.mesh.count}`);
  assert.equal(r.garage.toggle(), null, 'could not get out');
  const lost = r.hurt();
  for (let i = 0; i < BURN_TIME * 60; i++) r.step();
  assert.ok(car.condition.burnt);
  assert.equal(r.hurt(), lost);
  assert.equal(r.garage.toggle(), 'No vehicle close enough', 'got back into a wreck');
});

test('shooting a car in traffic knocks it out of line and damages it', () => {
  const r = setup();
  r.traffic.set('cars', true);
  for (let i = 0; i < 30; i++) r.step();
  const npc = r.traffic.sim.cars.find((c) => Math.hypot(c.x, c.z) < 150);
  r.physics.step();
  const from = { x: npc.x, y: 5, z: npc.z };
  const down = { x: 0, y: -1, z: 0 };
  const hit = r.physics.castRay(new RAPIER.Ray(from, down), 10, true);
  r.traffic.shoot(from, down, hit.timeOfImpact, hit.collider, 40);
  assert.ok(!r.traffic.sim.cars.includes(npc));
  const wreck = r.traffic.wrecks.at(-1).car;
  assert.ok(wreck.condition.hp < CAR_HP);
  // Finish it: the driver is thrown clear and the car stays put.
  r.traffic.wanted.immune = true;
  wreck.condition.hp = 0;
  r.step();
  assert.equal(r.traffic.sim.fallen.length, 1);
  for (let i = 0; i < 60 * 60; i++) r.step();
  assert.ok(r.traffic.wrecks.some((w) => w.car === wreck), 'it drove off');
  assert.ok(wreck.condition.burnt);
  assert.equal(r.traffic.crumpled.size, 1);
});

test('burnt police cars are left behind with their officers on foot, but only so many', () => {
  const r = setup();
  r.traffic.police.settings.guns = false;
  r.traffic.wanted.set(5);
  let most = 0;
  for (let i = 0; i < 90 * 60; i++) {
    r.step();
    r.traffic.takeBusted();
    if (!r.traffic.wanted.level) r.traffic.wanted.set(5);
    if (r.garage.current.name === 'on foot') r.garage.current.able = false;
    if (i % 120 === 0) for (const unit of r.traffic.police.units) unit.car.condition.hp = 0;
    most = Math.max(most, r.traffic.police.husks.length);
  }
  assert.ok(most > 4 && most <= 8, `${most}`);
  assert.ok(r.traffic.police.officers().length <= 14);
});
