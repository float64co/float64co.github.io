import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { airCars, HEIGHT, SPACING } from '../src/air-traffic.js';
import { overlaps } from '../src/wrecks.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

test('flying cars keep to their layer, move steadily and never run into each other', () => {
  const focus = { x: 0, z: 0 };
  const box = (c) => ({ x: c.x, z: c.z, hx: c.hx, hz: c.hz, halfL: 2, halfW: 1 });
  let seen = 0;
  for (let time = 0; time < 120; time += 0.5) {
    const cars = airCars(time, focus, 300);
    seen += cars.length;
    for (const [i, a] of cars.entries()) {
      assert.ok(a.y === HEIGHT.x || a.y === HEIGHT.z);
      for (const b of cars.slice(i + 1))
        if (a.y === b.y) assert.ok(!overlaps(box(a), box(b)), `${a.id} and ${b.id} at ${time}`);
    }
    if (time) continue;
    const later = new Map(airCars(1, focus, 320).map((c) => [c.id, c]));
    for (const a of cars) {
      const b = later.get(a.id);
      assert.ok(Math.abs(Math.hypot(b.x - a.x, b.z - a.z) - a.speed) < 1e-6);
    }
    // Left-hand traffic: heading +z, the lane is at +x of its road.
    for (const c of cars.filter((o) => o.hz === 1)) assert.ok(((c.x % PITCH) + PITCH) % PITCH < PITCH / 2);
  }
  assert.ok(seen / 240 > 40, `only ${seen / 240} about`);
  assert.ok(SPACING > 20);
});

function setup() {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  traffic.set('peds', false);
  traffic.setRadius(250);
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 });
  const step = (input = { throttle: 0, steer: 0, brake: false }) => {
    garage.drive(input, DT);
    world.update([garage.current, ...traffic.wrecks.map((w) => w.car), ...traffic.police.cars()]
      .map((v) => v.body.translation()));
    traffic.update(garage, DT);
    physics.step();
  };
  for (let i = 0; i < 90; i++) step();
  return { physics, traffic, garage, step };
}

test('flying into one, or shooting it, drops it out of the sky', () => {
  for (const how of ['ram', 'shoot']) {
    const r = setup();
    r.traffic.wanted.immune = true;
    const target = r.traffic.air.cars.find((c) => Math.hypot(c.x, c.z) < 70);
    assert.ok(target, 'nothing up there');
    if (how === 'ram') {
      const car = r.garage.current;
      car.setFlying(true);
      car.body.setTranslation({ x: target.x + target.hx * 4.3, y: target.y, z: target.z + target.hz * 4.3 }, true);
      for (let i = 0; i < 30 && r.traffic.air.cars.some((c) => c.id === target.id); i++) r.step();
    } else {
      const from = { x: target.x, y: target.y - 20, z: target.z };
      assert.ok(Math.abs(r.traffic.sight(from, { x: 0, y: 1, z: 0 }, 60) - 19.6) < 0.1);
      r.traffic.shoot(from, { x: 0, y: 1, z: 0 }, 60, null, 25);
    }
    assert.ok(!r.traffic.air.cars.some((c) => c.id === target.id), `${how}: still flying`);
    const wreck = r.traffic.wrecks.at(-1);
    assert.equal(wreck.route, null);
    for (let i = 0; i < 8 * 60; i++) r.step();
    assert.ok(wreck.car.body.translation().y < 20, 'did not fall');
    assert.ok(wreck.car.condition.hp < 60, `fall did no harm: ${wreck.car.condition.hp}`);
    assert.ok(!r.traffic.air.cars.some((c) => c.id === target.id), 'came back');
  }
});

for (const vehicle of ['car', 'helicopter']) {
  test(`police take to the air after a flying ${vehicle}, stay with it, and land after`, () => {
    const r = setup();
    r.traffic.police.settings.cars = 2;
    r.traffic.police.settings.guns = false;
    if (vehicle === 'helicopter') r.garage.switch('helicopter');
    for (let i = 0; i < 60; i++) r.step();
    r.garage.current.setFlying?.(true);
    r.traffic.wanted.set(3);
    const p = () => r.garage.current.body.translation();
    const keep = () => { if (!r.traffic.wanted.level) r.traffic.wanted.set(3); };
    // Up to 90 m, then cruise along gently.
    for (let i = 0; i < 6 * 60; i++) {
      keep();
      r.step({ throttle: 0, steer: 0, brake: p().y < 90 });
    }
    for (let i = 0; i < 20 * 60; i++) {
      keep();
      r.step({ throttle: 0.4, steer: 0, brake: false });
    }
    const gaps = r.traffic.police.units.map((u) => {
      const q = u.car.body.translation();
      return { flying: u.car.flying, high: q.y, gap: Math.hypot(q.x - p().x, q.y - p().y, q.z - p().z) };
    });
    console.log(vehicle, gaps.map((g) => `${g.flying} y ${g.high.toFixed(0)} gap ${g.gap.toFixed(0)}`));
    assert.ok(gaps.some((g) => g.flying && g.high > 60 && g.gap < 60), 'not following');
    assert.equal(r.garage.current.name, vehicle);

    // Come back down to the road and stop; so do they.
    const road = Math.round(p().x / PITCH) * PITCH;
    r.garage.current.body.setTranslation({ x: road, y: 1.2, z: p().z }, true);
    r.garage.current.body.setLinvel({ x: 0, y: 0, z: 0 }, true);
    r.garage.current.setFlying?.(false);
    let landed = false;
    for (let i = 0; i < 40 * 60 && !landed; i++) {
      keep();
      r.step({ throttle: 0, steer: 0, brake: vehicle === 'car', descend: false });
      r.traffic.takeBusted();
      landed = r.traffic.police.units.some((u) => !u.car.flying && u.car.body.translation().y < 3);
    }
    assert.ok(landed, 'stayed up');
  });
}

// Fly up with the police after you and keep still for a while.
function airChase(stars, { guns = true } = {}) {
  const r = setup();
  r.traffic.set('cars', false);
  r.traffic.police.settings.cars = 2;
  r.traffic.police.settings.guns = guns;
  r.garage.current.setFlying(true);
  r.traffic.wanted.set(stars);
  let damage = 0;
  let leaning = 0;
  let checked = 0;
  for (let i = 0; i < 25 * 60; i++) {
    if (!r.traffic.wanted.level) r.traffic.wanted.set(stars);
    const car = r.garage.current;
    car.condition.hp = 100; // keep it airworthy
    r.step({ throttle: 0, steer: 0, brake: car.body.translation().y < 30 });
    damage += r.traffic.takeDamage();
    const p = car.body.translation();
    for (const unit of r.traffic.police.units) {
      if (!unit.gunner) continue;
      leaning++;
      assert.ok(unit.car.flying, 'leaning out of a car on the ground');
      if (i % 30) continue;
      checked++;
      // Out beyond the side of the car, pointing at the player.
      unit.car.mesh.updateWorldMatrix(true, true);
      const at = unit.gunner.arm.getWorldPosition(new THREE.Vector3());
      assert.ok(Math.abs(unit.car.mesh.worldToLocal(at.clone()).x) > 1.1, 'inside the bodywork');
      const along = unit.gunner.arm.getWorldDirection(new THREE.Vector3());
      const wanted = new THREE.Vector3(p.x, p.y + 0.5, p.z).sub(at).normalize();
      assert.ok(along.dot(wanted) > 0.98, `aim off: ${along.dot(wanted)}`);
    }
  }
  return { damage, leaning, checked, r };
}

test('from three stars, officers shoot at you from their flying cars', () => {
  for (const stars of [3, 5]) {
    const { damage, leaning, checked } = airChase(stars);
    console.log({ stars, damage, leaning, checked });
    assert.ok(leaning > 100 && checked > 3, 'nobody leaned out');
    assert.ok(damage > 0, 'never hit');
  }
});

test('no shooting from the air at two stars, with guns off, or in the suit', () => {
  for (const [stars, options] of [[2, {}], [5, { guns: false }]]) {
    const { damage, leaning } = airChase(stars, options);
    assert.equal(leaning, 0);
    assert.equal(damage, 0);
  }
});

test('a leaning officer can be shot without harming the car, and falls when killed', () => {
  const { r } = airChase(3);
  const unit = r.traffic.police.units.find((u) => u.gunner);
  assert.ok(unit, 'nobody leaning');
  const car = unit.car.condition.hp;
  const at = () => unit.gunner.head.getWorldPosition(new THREE.Vector3());
  // Fire from just beyond them, straight at the head, with nothing in between.
  const aim = () => {
    unit.gunner.hit({ x: 0, y: 0, z: 0 }, { x: 0, y: 1, z: 0 }, 0); // brings the pose up to date
    const head = at();
    const out = head.clone().sub(unit.car.mesh.position).setY(0).normalize();
    const from = head.clone().addScaledVector(out, 4);
    return { from, dir: head.clone().sub(from).normalize() };
  };
  r.traffic.wanted.set(1); // no steps are taken, so they stay where they are
  const { from, dir } = aim();
  const distance = r.traffic.sight(from, dir, 20);
  assert.ok(Math.abs(distance - 3.82) < 0.05, `${distance}`);
  // A glancing body hit first: aim lower.
  const chest = from.clone();
  chest.y -= 0.45;
  const low = r.traffic.shoot(chest, dir, 20, null, 25);
  assert.equal(low?.hp, 75);
  assert.equal(unit.car.condition.hp, car, 'the car took it');
  assert.ok(r.traffic.wanted.level >= 2, 'not minded');

  const strays = r.traffic.police.officers().length;
  const shot = aim();
  const head = r.traffic.shoot(shot.from, shot.dir, 20, null, 25);
  assert.equal(head.hp, 0, 'a head shot on top should do it');
  assert.equal(unit.gunner, null);
  assert.equal(unit.car.condition.hp, car);
  assert.equal(r.traffic.police.officers().length, strays + 1);
  const body = r.traffic.police.officers().at(-1);
  assert.ok(body.dead && body.down);
  const height = r.physics.bodies.len();
  for (let i = 0; i < 8 * 60; i++) r.step({ throttle: 0, steer: 0, brake: false });
  assert.ok(r.physics.bodies.len() <= height + 2);
  assert.ok(unit.gunner === null, 'someone else leaned out of the same car');
});

test('misses go past them', () => {
  const { r } = airChase(3);
  const unit = r.traffic.police.units.find((u) => u.gunner);
  const head = unit.gunner.head.getWorldPosition(new THREE.Vector3());
  assert.equal(unit.gunner.hit({ x: head.x, y: head.y + 2, z: head.z - 5 }, { x: 0, y: 0, z: 1 }, 20), null);
});
