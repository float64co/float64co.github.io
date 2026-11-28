import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { createVariables } from '../src/variables.js';
import { MEMBERS } from '../src/squad.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

function setup(vehicle = 'car') {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  traffic.setRadius(200);
  traffic.set('cars', false);
  traffic.squad.enabled = true;
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 }, vehicle);
  const step = (input = {}) => {
    garage.drive({ throttle: 0, strafe: 0, turn: 0, steer: 0, brake: false, ...input }, DT);
    world.update([garage.current, ...garage.parked.values(), ...traffic.police.cars()].map((v) => v.body.translation()));
    traffic.update(garage, DT);
    physics.step();
    garage.sync();
    traffic.draw(garage.current.body.translation());
  };
  const run = (seconds, input) => {
    for (let i = 0; i < Math.round(seconds * 60); i++) step(input);
  };
  run(1.5);
  const squad = traffic.squad.members;
  const at = () => garage.current.body.translation();
  const apart = (m) => Math.hypot(m.actor.x - at().x, m.actor.z - at().z);
  return { physics, traffic, garage, step, run, squad, at, apart };
}

test('three of them, by name', () => {
  assert.deepEqual(MEMBERS.map((m) => m.name), ['Saif Keyes', 'Fleur Gardner', 'Jade Fields']);
  assert.equal(new Set(MEMBERS.map((m) => m.person.skin)).size, 3);
  assert.equal(new Set(MEMBERS.map((m) => m.top)).size, 3);
});

test('they get in with you, get out with you, follow on foot and keep up', () => {
  const r = setup();
  r.run(3);
  assert.ok(r.squad.every((m) => m.aboard && !m.actor.figure.visible), 'not in the car');
  assert.equal(r.garage.toggle(), null);
  r.run(2);
  assert.ok(r.squad.every((m) => !m.aboard && m.actor.figure.visible));
  assert.ok(r.squad.every((m) => r.apart(m) < 6), `${r.squad.map(r.apart)}`);
  // Walk, then run.
  for (const run of [false, true]) {
    r.run(5, { throttle: 1, run });
    assert.ok(r.squad.every((m) => r.apart(m) < 8), `${run}: ${r.squad.map(r.apart)}`);
  }
  r.run(2);
  // Never in each other, or in you.
  const all = [...r.squad.map((m) => m.actor), r.at()];
  for (const [i, a] of all.entries())
    for (const b of all.slice(i + 1)) assert.ok(Math.hypot(a.x - b.x, a.z - b.z) > 0.5, 'standing inside');
  // Far away at once: they catch up.
  r.garage.current.body.setTranslation({ x: 240, y: 0.9, z: 30 }, true);
  r.run(1);
  assert.ok(r.squad.every((m) => r.apart(m) < 8));
});

test('they do not get on a motorbike, but stay with it', () => {
  const r = setup('motorbike');
  r.run(6, { throttle: 0.5 });
  assert.ok(r.squad.every((m) => !m.aboard));
  r.run(4, { brake: true });
  assert.ok(r.squad.every((m) => r.apart(m) < 50));
});

test('they leave police who have done nothing alone, and go home when told', () => {
  const r = setup();
  assert.equal(r.garage.toggle(), null);
  r.run(2);
  assert.equal(r.traffic.wanted.level, 0);
  const variables = createVariables({ garage: r.garage, traffic: r.traffic });
  assert.equal(variables.var_squad.get(), 1);
  assert.ok(variables.var_squad.set('0'));
  r.run(1);
  assert.ok(r.squad.every((m) => !m.actor));
  assert.ok(variables.var_squad.set('1'));
  r.run(1);
  assert.ok(r.squad.every((m) => m.actor));
});

for (const vehicle of ['on foot', 'car']) {
  test(`${vehicle}: they shoot back at police who attack, and their kills make you more wanted`, () => {
    const r = setup();
    r.traffic.police.settings.cars = 3;
    r.run(3);
    if (vehicle === 'on foot') assert.equal(r.garage.toggle(), null);
    r.traffic.wanted.set(3);
    let killed = 0;
    let leaned = false;
    let armed = false;
    let most = 3;
    const seen = new Set();
    for (let i = 0; i < 90 * 60; i++) {
      r.step({ brake: true });
      r.traffic.takeDamage();
      r.traffic.takeBusted();
      most = Math.max(most, r.traffic.wanted.level);
      if (!r.traffic.wanted.level) r.traffic.wanted.set(3);
      if (r.garage.current.condition) r.garage.current.condition.hp = 100;
      for (const o of r.traffic.police.officers()) if (o.dead && !seen.has(o)) { seen.add(o); killed++; }
      leaned ||= r.squad.some((m) => m.gunner);
      armed ||= r.squad.some((m) => !m.aboard && m.actor.weapon === 'BRN-180');
      if (killed >= 2) break;
    }
    console.log(vehicle, { killed, most, leaned, armed });
    assert.ok(killed >= 2, `killed ${killed}`);
    assert.ok(most >= 4, `wanted only ${most}`);
    assert.equal(vehicle === 'car' ? leaned : armed, true);
  });
}

test('a name tag floats over each of them while they can be seen', async () => {
  const { createNameTag } = await import('../src/name-tag.js');
  const r = setup();
  r.run(3); // aboard: nothing to label
  assert.ok(r.squad.every((m) => !m.tag.visible));
  assert.equal(r.garage.toggle(), null);
  r.run(2);
  for (const m of r.squad) {
    assert.ok(m.tag.visible);
    assert.equal(m.tag.name, m.name.split(' ')[0]);
    assert.ok(Math.hypot(m.tag.position.x - m.actor.x, m.tag.position.z - m.actor.z) < 1e-6);
    assert.ok(m.tag.position.y > 1.8 && m.tag.position.y < 2.3);
  }
  r.traffic.squad.enabled = false;
  assert.ok(r.squad.every((m) => !m.tag.visible));
  // Where there is a canvas, the name is written on it.
  const written = [];
  const context = { strokeText: (t) => written.push(t), fillText: (t) => written.push(t) };
  const tag = createNameTag('Jade Fields', { getContext: () => context });
  assert.deepEqual(written, ['Jade Fields', 'Jade Fields']);
  assert.ok(tag.material.map);
});

// How far inside any building a point is (0 if outside).
function inside(p) {
  let deep = 0;
  for (const b of city.flatMap((c) => (Math.abs(c.x - p.x) < 60 && Math.abs(c.z - p.z) < 60 ? c.buildings : [])))
    deep = Math.max(deep, Math.min(b.w / 2 - Math.abs(p.x - b.x), b.d / 2 - Math.abs(p.z - b.z)));
  return deep;
}

test('they go round the corners of buildings, never through them', () => {
  const r = setup();
  r.traffic.set('peds', false);
  assert.equal(r.garage.toggle(), null);
  r.run(2);
  // A building near the start, and a lap of it the player walks just outside.
  const b = city.flatMap((c) => c.buildings).filter((o) => !o.site)
    .sort((p, q) => Math.hypot(p.x, p.z) - Math.hypot(q.x, q.z))[0];
  const out = 1.2;
  const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1], [1, -1]]
    .map(([sx, sz]) => ({ x: b.x + sx * (b.w / 2 + out), z: b.z + sz * (b.d / 2 + out) }));
  const body = () => r.garage.current.body;
  body().setTranslation({ x: corners[0].x, y: 0.9, z: corners[0].z }, true);
  r.run(8); // time to get over there
  let deepest = 0;
  let furthest = 0;
  for (const corner of corners.slice(1)) {
    for (let i = 0; i < 900; i++) {
      const p = body().translation();
      const [dx, dz] = [corner.x - p.x, corner.z - p.z];
      const left = Math.hypot(dx, dz);
      if (left < 0.2) break;
      r.garage.drive({}, DT);
      const speed = Math.min(7, left / DT);
      body().setLinvel({ x: (dx / left) * speed, y: 0, z: (dz / left) * speed }, true);
      r.traffic.update(r.garage, DT);
      r.physics.step();
      for (const m of r.squad) {
        deepest = Math.max(deepest, inside(m.actor));
        furthest = Math.max(furthest, r.apart(m));
      }
    }
  }
  console.log({ building: [b.w.toFixed(0), b.d.toFixed(0)], deepest, furthest });
  assert.ok(deepest < 0.05, `walked ${deepest} m into a building`);
  assert.ok(furthest < 20, `lost: ${furthest}`);
  r.run(4);
  assert.ok(r.squad.every((m) => r.apart(m) < 6), `${r.squad.map(r.apart)}`);

  // Left on the far side of it, they find their way round rather than waiting.
  const [one] = r.squad;
  Object.assign(one.actor, { x: b.x - b.w / 2 - 1, z: b.z });
  body().setTranslation({ x: b.x + b.w / 2 + 2, y: 0.9, z: b.z }, true);
  for (let i = 0; i < 8 * 60; i++) {
    r.step();
    assert.ok(inside(one.actor) < 0.05);
  }
  assert.ok(r.apart(one) < 6, `stuck ${r.apart(one)} m away`);
});

test('their glow only shows where they are hidden, and their tags always show', () => {
  const r = setup();
  assert.equal(r.garage.toggle(), null);
  r.run(2);
  for (const m of r.squad) {
    let ghosts = 0;
    let meshes = 0;
    m.actor.figure.traverse((o) => {
      if (!o.isMesh) return;
      if (o.name !== 'through walls') {
        meshes++;
        return;
      }
      ghosts++;
      assert.equal(o.material.depthFunc, THREE.GreaterDepth);
      assert.ok(!o.material.depthWrite && !o.material.transparent);
      assert.ok(o.renderOrder < o.parent.renderOrder, 'drawn over their own body');
    });
    assert.ok(ghosts > 8 && ghosts === meshes, `${ghosts} of ${meshes}`);
    assert.equal(m.tag.material.depthTest, false);
  }
});

// The player on foot with the squad about them, and no police.
function standing() {
  const r = setup();
  r.traffic.set('peds', false);
  assert.equal(r.garage.toggle(), null);
  r.run(2);
  return r;
}

test('they have HP, can be shot down, stay down and come back when you start over', () => {
  const r = standing();
  assert.ok(r.squad.every((m) => m.hp === 100));
  const targets = r.traffic.squad.targets();
  assert.equal(targets.length, 3);
  assert.equal(targets[0].hurt(30), 70);
  assert.equal(r.squad[0].hp, 70);
  // Nobody after you: wounds heal.
  r.run(2);
  assert.ok(r.squad[0].hp > 79 && r.squad[0].hp < 82, `${r.squad[0].hp}`);
  r.traffic.squad.targets()[0].hurt(500);
  r.run(15);
  assert.equal(r.squad[0].hp, 0);
  assert.ok(r.squad[0].actor.down, 'got up');
  assert.equal(r.traffic.squad.afoot().length, 2);
  assert.equal(r.traffic.squad.targets().length, 2);
  // Left far behind, the body is cleared away, and they do not reappear.
  r.garage.current.body.setTranslation({ x: 300, y: 0.9, z: 30 }, true);
  r.run(1);
  assert.equal(r.squad[0].actor, null);
  r.traffic.standDown();
  r.run(1);
  assert.ok(r.squad.every((m) => m.hp === 100 && m.actor && !m.actor.down));
});

test('police shoot at them as well as you, and suits stop the bullets', () => {
  for (const suits of [false, true]) {
    const r = standing();
    r.traffic.squad.suits = suits;
    r.traffic.police.settings.cars = 6;
    r.traffic.wanted.set(5);
    let lowest = 100;
    for (let i = 0; i < 40 * 60; i++) {
      r.step();
      r.traffic.takeDamage();
      r.traffic.takeBusted();
      if (!r.traffic.wanted.level) r.traffic.wanted.set(5);
      lowest = Math.min(lowest, ...r.squad.map((m) => m.hp));
    }
    console.log({ suits, lowest });
    if (suits) assert.equal(lowest, 100);
    else assert.ok(lowest < 100, 'never hit');
  }
});

test('a fast car knocks one over and hurts them; they get up if they can', () => {
  const r = standing();
  const [one] = r.squad;
  r.traffic.set('cars', true);
  r.run(1);
  const npc = r.traffic.sim.cars.find((c) => !c.path && c.speed > 7 && c.s < 12 && Math.hypot(c.x, c.z) < 120);
  assert.ok(npc, 'no car coming');
  for (let i = 0; i < 90 && !one.actor.down; i++) {
    if (!one.actor.down && i === 0) Object.assign(one.actor, { x: npc.x + npc.hx * 2.6, z: npc.z + npc.hz * 2.6 });
    r.step();
  }
  assert.ok(one.actor.down, 'not knocked over');
  assert.ok(one.hp < 100 && one.hp > 0, `${one.hp}`);
  r.traffic.set('cars', false);
  r.run(9);
  assert.ok(!one.actor.down, 'stayed down');
});

test('they reload, except in suits', () => {
  for (const suits of [false, true]) {
    const r = standing();
    r.traffic.squad.suits = suits;
    // Something to shoot at for ever.
    r.traffic.police.attackers = () => [{
      x: r.squad[0].actor.x, y: 1.2, z: r.squad[0].actor.z + 25, shooting: true, hurt: () => 100,
    }];
    let reloads = 0;
    let before = false;
    for (let i = 0; i < 20 * 60; i++) {
      r.step();
      const now = r.squad[0].reloading > 0;
      if (now && !before) reloads++;
      before = now;
      assert.ok(r.squad[0].rounds > 0 || now);
    }
    if (suits) assert.equal(reloads, 0);
    else assert.ok(reloads >= 2, `${reloads}`);
  }
});

test('in suits they run much faster and send people flying with a punch', () => {
  const r = standing();
  const variables = createVariables({ garage: r.garage, traffic: r.traffic });
  assert.equal(variables.var_squad_suits.get(), 0);
  assert.ok(variables.var_squad_suits.set('1'));
  const [one] = r.squad;
  let thrown = null;
  let alive = true;
  const attacker = {
    x: one.actor.x, y: 1.2, z: one.actor.z + 9, shooting: false,
    hurt: (amount, dir, speed) => { thrown = { amount, speed }; alive = false; return 0; },
  };
  r.traffic.police.attackers = () => (alive ? [attacker] : []);
  let fastest = 0;
  for (let i = 0; i < 120 && alive; i++) {
    r.step();
    for (const m of r.squad) fastest = Math.max(fastest, m.actor.moving);
  }
  assert.ok(thrown, 'never punched');
  assert.equal(thrown.amount, Infinity);
  assert.ok(thrown.speed > 15);
  assert.ok(fastest > 15, `${fastest}`);
});

test('seats as in a British car: none leaning from the front right, two on the left', () => {
  const seats = MEMBERS.map((m) => [Math.sign(m.side), m.seat > 0 ? 'front' : 'back'].join(' '));
  assert.deepEqual(seats.sort(), ['-1 back', '1 back', '1 front']);
});

// Sit in the car with one attacker at (across, ahead) of it, in the car's frame.
function fromTheCar(across, ahead) {
  const r = setup();
  r.traffic.set('peds', false);
  r.run(4);
  assert.ok(r.squad.every((m) => m.aboard));
  const car = r.garage.current;
  const where = () => new THREE.Vector3(across, 0.4, ahead)
    .applyQuaternion(new THREE.Quaternion().copy(car.body.rotation())).add(car.body.translation());
  r.traffic.police.attackers = () => {
    const p = where();
    return [{ x: p.x, y: p.y, z: p.z, shooting: true, hurt: () => 100 }];
  };
  r.run(1);
  car.mesh.updateWorldMatrix(true, true);
  return Object.fromEntries(r.squad.filter((m) => m.gunner).map((m) => {
    const at = car.mesh.worldToLocal(m.gunner.arm.getWorldPosition(new THREE.Vector3()));
    return [m.name.split(' ')[0], [Math.sign(at.x), at.z > 0 ? 'front' : 'back']];
  }));
}

test('each leans out only on their own side', () => {
  // Left is +x. A threat to the left: the two on the left, front and back.
  assert.deepEqual(fromTheCar(15, 0), { Saif: [1, 'front'], Jade: [1, 'back'] });
  // To the right: only the one in the back; the driver's quarter has nobody.
  assert.deepEqual(fromTheCar(-15, 0), { Fleur: [-1, 'back'] });
  // Dead ahead or behind: anyone can reach round.
  for (const ahead of [20, -20]) assert.deepEqual(Object.keys(fromTheCar(0, ahead)).sort(), ['Fleur', 'Jade', 'Saif']);
});
