import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { gunFor, hitChance } from '../src/police-guns.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

test('which gun, and how good a shot', () => {
  assert.deepEqual([0, 1, 2, 3, 4, 5].map(gunFor), [null, null, null, 'Glock 17', 'BRN-180', 'BRN-180']);
  assert.ok(hitChance('Glock 17', 3, 0) > hitChance('Glock 17', 30, 0));
  assert.ok(hitChance('Glock 17', 10, 0) > hitChance('Glock 17', 10, 7));
  assert.equal(hitChance('Glock 17', 40, 0), 0);
});

// A player on foot with the police after them.
function setup(stars, { guns = true } = {}) {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  traffic.setRadius(200);
  // No traffic, so that any damage is the police's doing.
  traffic.set('cars', false);
  traffic.police.settings.guns = guns;
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 });
  let ticks = 0;
  let running = false;
  const step = () => {
    // Running means jogging up and down the road rather than getting away.
    const throttle = running ? (Math.floor(ticks++ / 180) % 2 ? -1 : 1) : 0;
    garage.drive({ throttle, strafe: 0, turn: 0, run: true, brake: true }, DT);
    world.update([garage.current, ...[...traffic.wrecks, ...traffic.police.units].map((w) => w.car)]
      .map((v) => v.body.translation()));
    traffic.update(garage, DT);
    physics.step();
    traffic.draw(garage.current.body.translation());
  };
  for (let i = 0; i < 60; i++) step();
  assert.equal(garage.toggle(), null);
  traffic.wanted.set(stars);
  return { physics, scene, traffic, garage, step, run() { running = true; } };
}

// Run until arrested or `damage` has been done.
function play(r, enough = 100) {
  let damage = 0;
  let busted = false;
  let armed = new Set();
  let seconds = 0;
  while (!busted && damage < enough && seconds < 60) {
    r.step();
    damage += r.traffic.takeDamage();
    busted = r.traffic.takeBusted();
    for (const o of r.traffic.police.officers()) armed.add(o.weapon);
    seconds += DT;
  }
  return { damage, busted, armed: [...armed], seconds };
}

for (const [stars, weapon] of [[2, null], [3, 'Glock 17'], [5, 'BRN-180']]) {
  test(`at ${stars} stars officers carry ${weapon ?? 'nothing'}`, () => {
    const r = setup(stars);
    r.run();
    const { damage, armed, seconds, busted } = play(r);
    console.log({ stars, damage, busted, seconds: seconds.toFixed(1) });
    // Unarmed officers can't catch a runner, so there may be none out to look at.
    if (weapon) assert.deepEqual(armed, [weapon]);
    else assert.ok(armed.every((w) => w === null));
    if (weapon) assert.ok(damage > 0, 'never hit');
    else assert.equal(damage, 0);
  });
}

test('someone who gives up may be arrested rather than shot dead', () => {
  const { busted, damage } = play(setup(3));
  assert.ok(busted);
  assert.ok(damage < 50, `${damage}`);
});

test('guns can be switched off', () => {
  const { damage, armed } = play(setup(5, { guns: false }));
  assert.equal(damage, 0);
  assert.deepEqual(armed, [null]);
});

test('no shooting through a wall, and no instant death', () => {
  const r = setup(5);
  const p = r.garage.current.body.translation();
  // Box the player in with something solid.
  for (const [x, z, hx, hz] of [[0, 3, 3.2, 0.1], [0, -3, 3.2, 0.1], [3, 0, 0.1, 3.2], [-3, 0, 0.1, 3.2]])
    r.physics.createCollider(RAPIER.ColliderDesc.cuboid(hx, 3, hz).setTranslation(p.x + x, 3, p.z + z));
  let damage = 0;
  for (let i = 0; i < 20 * 60; i++) {
    r.step();
    r.traffic.takeBusted();
    // Officers don't plan round walls; keep them outside this one.
    for (const o of r.traffic.police.officers())
      if (Math.abs(o.x - p.x) < 3.5 && Math.abs(o.z - p.z) < 3.5) o.x = p.x + 4;
    damage += r.traffic.takeDamage();
  }
  assert.equal(damage, 0);

  const open = play(setup(5), 100);
  assert.ok(open.busted || open.seconds > 4, `dead in ${open.seconds} s`);
});
