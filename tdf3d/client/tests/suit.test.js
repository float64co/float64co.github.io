import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { createCombat } from '../src/combat.js';
import { createWalker } from '../src/foot.js';
import { createArsenal, PUNCH } from '../src/weapons.js';
import { createVariables } from '../src/variables.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

test('the suit is off until var_suit=1, and bad values change nothing', () => {
  const traffic = { suit: { on: false } };
  const { var_suit: suit } = createVariables({ garage: { names: [] }, traffic });
  assert.equal(suit.get(), 0);
  assert.ok(suit.set('1'));
  assert.equal(traffic.suit.on, true);
  assert.ok(!suit.set('2'));
  assert.ok(suit.set('0'));
  assert.equal(traffic.suit.on, false);
});

test('a bottomless clip never empties or reloads, and fills up when put on', () => {
  const arsenal = createArsenal();
  arsenal.cycle(1);
  for (let i = 0; i < 17; i++) {
    arsenal.step(1);
    arsenal.fire();
  }
  assert.ok(arsenal.reloading);
  arsenal.bottomless = true;
  assert.ok(!arsenal.reloading);
  for (const direction of [0, 1]) {
    if (direction) arsenal.cycle(direction);
    let fired = 0;
    for (let i = 0; i < 400; i++) {
      arsenal.step(0.2);
      if (arsenal.fire()) fired++;
      assert.ok(!arsenal.reloading);
      assert.equal(arsenal.ammo, arsenal.weapon.magazine);
    }
    assert.ok(fired > 390, `${arsenal.weapon.name}: ${fired}`);
    assert.ok(!arsenal.reload());
  }
  arsenal.bottomless = false;
  arsenal.step(1);
  arsenal.fire();
  assert.equal(arsenal.ammo, arsenal.weapon.magazine - 1);
});

test('walking and running are much faster in the suit', () => {
  const speed = (suit, run) => {
    const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
    const walker = createWalker('on foot', new THREE.Scene(), physics, { x: 0, y: 1, z: 0 });
    walker.drive({ throttle: 1, run, suit }, DT);
    return walker.speed();
  };
  assert.ok(Math.abs(speed(true, false) - 2 * speed(false, false)) < 1e-5);
  assert.ok(Math.abs(speed(true, true) - 3 * speed(false, true)) < 1e-5);
});

test('one punch in the suit throws someone far; without it they only lose some HP', () => {
  const thrown = (suit) => {
    const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
    physics.createCollider(RAPIER.ColliderDesc.cuboid(500, 1, 500).setTranslation(0, -1, 0));
    const scene = new THREE.Scene();
    const traffic = createTraffic(scene, physics);
    traffic.suit.on = suit;
    traffic.set('cars', false);
    traffic.sim.step(DT, { x: 0, z: 0 });
    const [ped] = traffic.sim.peds;
    for (const other of traffic.sim.peds) if (other !== ped) other.x += 5000;
    traffic.sim.enabled.peds = false;
    Object.assign(ped, { x: 0, z: 1.2 });
    const walker = createWalker('on foot', scene, physics, { x: 0, y: 0.85, z: 0 });
    const combat = createCombat(scene, new THREE.PerspectiveCamera(), physics, traffic);
    combat.update(PUNCH.interval, walker, false);
    const { hp } = combat.punch(walker);
    const [doll] = traffic.ragdolls.values();
    if (!doll) return { hp };
    let high = 0;
    for (let i = 0; i < 180; i++) {
      physics.step();
      high = Math.max(high, doll.torso.translation().y);
    }
    return { hp, far: doll.torso.translation().z - 1.2, high };
  };
  assert.deepEqual(thrown(false), { hp: 80 });
  const hit = thrown(true);
  console.log(hit);
  assert.equal(hit.hp, 0);
  assert.ok(hit.far > 10, `only ${hit.far} m`);
  assert.ok(hit.high > 1.5);
});

// The police after a player who sits or stands still for a minute.
function siege(vehicle, stars) {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  traffic.setRadius(200);
  traffic.set('cars', false);
  traffic.suit.on = true;
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 });
  const step = () => {
    garage.drive({ throttle: 0, strafe: 0, turn: 0, brake: true, suit: true }, DT);
    world.update([garage.current, ...traffic.police.cars()].map((v) => v.body.translation()));
    traffic.update(garage, DT);
    physics.step();
  };
  for (let i = 0; i < 60; i++) step();
  if (vehicle === 'on foot') assert.equal(garage.toggle(), null);
  traffic.wanted.set(stars);
  let damage = 0; // other than from sitting in a car that is on fire
  let burnt = 0;
  let busted = false;
  let reached = false;
  let shots = false;
  for (let i = 0; i < 60 * 60; i++) {
    step();
    const burning = garage.current.condition?.hp <= 0 && !garage.current.condition.burnt;
    const taken = traffic.takeDamage();
    if (burning) burnt += taken;
    else damage += taken;
    busted ||= traffic.takeBusted();
    reached ||= traffic.police.officers().length > 0;
    shots ||= traffic.police.officers().some((o) => o.aiming);
    assert.equal(traffic.police.arrest, 0);
    assert.equal(traffic.police.pulling, 0);
  }
  return { damage, burnt, busted, reached, shots, name: garage.current.name };
}

for (const [vehicle, stars] of [['on foot', 5], ['car', 2], ['car', 4]]) {
  test(`in the suit, ${vehicle} at ${stars} stars: never arrested and never hurt by bullets`, () => {
    const result = siege(vehicle, stars);
    assert.ok(result.reached, 'nobody came');
    assert.equal(result.busted, false);
    assert.equal(result.damage, 0);
    assert.equal(result.name, vehicle, 'dragged out');
    if (stars >= 3) assert.ok(result.shots, 'they should still try');
  });
}

// The player on foot beside a car, facing it.
function facing(kind, suit = true) {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  traffic.setRadius(200);
  traffic.set('peds', false);
  traffic.suit.on = suit;
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 }, kind === 'motorbike' ? 'motorbike' : 'car');
  const combat = createCombat(scene, new THREE.PerspectiveCamera(), physics, traffic);
  const step = () => {
    garage.drive({ throttle: 0, strafe: 0, turn: 0, brake: true }, DT);
    world.update([garage.current, ...garage.parked.values(), ...traffic.wrecks.map((w) => w.car)]
      .map((v) => v.body.translation()));
    traffic.update(garage, DT);
    physics.step();
  };
  for (let i = 0; i < 90; i++) step();
  assert.equal(garage.toggle(), null);
  for (let i = 0; i < 60; i++) step();
  const walker = garage.current;
  // Stand `gap` from a point on the car's side and turn to face it.
  const stand = (x, z, hx, hz, gap) => {
    walker.body.setTranslation({ x: x + hz * gap, y: 0.9, z: z - hx * gap }, true);
    walker.look(-(Math.atan2(-hz, hx) - walker.view().yaw) / walker.tune.mouse, 0);
  };
  const punch = () => {
    combat.update(1, walker, false);
    return combat.punch(walker);
  };
  return { physics, traffic, garage, walker, step, stand, punch };
}

function flight(r, body) {
  const from = { ...body.translation() };
  let high = 0;
  for (let i = 0; i < 240; i++) {
    r.step();
    high = Math.max(high, body.translation().y);
  }
  const p = body.translation();
  return { far: Math.hypot(p.x - from.x, p.z - from.z), high };
}

for (const kind of ['car', 'motorbike']) {
  test(`a suit punch sends a parked ${kind} flying`, () => {
    const r = facing(kind);
    const [vehicle] = r.garage.parked.values();
    const p = vehicle.body.translation();
    const q = vehicle.body.rotation();
    const hz = 1 - 2 * (q.x * q.x + q.y * q.y);
    const hx = 2 * (q.x * q.z + q.w * q.y);
    r.stand(p.x, p.z, hx, hz, kind === 'car' ? 2.2 : 1.5);
    r.punch();
    const went = flight(r, vehicle.body);
    console.log(kind, went, vehicle.condition?.hp);
    assert.ok(went.far > 12 && went.high > 2, JSON.stringify(went));
    if (kind === 'car') assert.ok(vehicle.condition.hp < 80 && vehicle.condition.dents.length, 'unmarked');
  });
}

test('traffic can be punched off the road, but only with the suit on', () => {
  for (const suit of [false, true]) {
    const r = facing('car', suit);
    const p = r.walker.body.translation();
    const npc = r.traffic.sim.cars.find((c) => !c.path && Math.hypot(c.x - p.x, c.z - p.z) < 120);
    Object.assign(npc, { cruise: 0, speed: 0 });
    r.stand(npc.x, npc.z, npc.hx, npc.hz, 2.2);
    r.punch();
    if (!suit) {
      assert.ok(r.traffic.sim.cars.includes(npc));
      assert.equal(r.traffic.wrecks.length, 0);
      continue;
    }
    assert.ok(!r.traffic.sim.cars.includes(npc));
    const { body } = r.traffic.wrecks.at(-1).car;
    assert.ok(flight(r, body).far > 12);
    assert.ok(r.traffic.wanted.level >= 1);
  }
});
