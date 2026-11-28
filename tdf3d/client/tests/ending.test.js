import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { nearestSite, PITCH } from '../src/city.js';
import { createGarage } from '../src/garage.js';
import { createEnding, ENDING } from '../src/ending.js';

await RAPIER.init();
const DT = 1 / 60;

function setup() {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.createCollider(RAPIER.ColliderDesc.cuboid(500, 1, 500).setTranslation(0, -1, 0));
  const garage = createGarage(new THREE.Scene(), physics, { x: 0, y: 1.5, z: PITCH / 2 });
  const words = { style: {}, hidden: true, textContent: '' };
  const screen = { style: {} };
  const player = { hp: 100 };
  let stoodDown = 0;
  const traffic = { standDown: () => { stoodDown++; } };
  const ending = createEnding({ words, screen, garage, traffic, player });
  const run = (seconds, what = { damage: 0, busted: false }) => {
    for (let i = 0; i < Math.round(seconds * 60); i++) {
      garage.drive({ throttle: 0, strafe: 0, turn: 0, brake: true }, DT);
      physics.step();
      ending.step(DT, what);
    }
  };
  const grey = () => Number(/grayscale\((.*)\)/.exec(screen.style.filter)?.[1] ?? 0);
  return { garage, words, screen, player, ending, run, grey, stoodDown: () => stoodDown };
}

for (const [label, colour, site, what, onFoot] of [
  ['WASTED', '#e5141f', 'hospital', { damage: 200, busted: false }, true],
  ['WASTED', '#e5141f', 'hospital', { damage: 200, busted: false }, false],
  ['BUSTED', '#1f6bff', 'police', { damage: 0, busted: true }, false],
]) {
  test(`${label} ${onFoot ? 'on foot' : 'in a car'}: big words, fading colour, then the ${site}`, () => {
    const r = setup();
    r.run(2);
    if (onFoot) assert.equal(r.garage.toggle(), null);
    r.run(1);
    const before = { ...r.garage.current.body.translation() };
    r.run(DT);
    assert.ok(r.words.hidden && !r.ending.active);
    r.run(DT, what);
    assert.equal(r.words.textContent, label);
    assert.equal(r.words.style.color, colour);
    assert.ok(!r.words.hidden);
    assert.ok(r.grey() < 0.1);
    if (onFoot) assert.ok(r.garage.current.down, 'did not fall');
    r.run(ENDING.drain / 2, what); // more of the same changes nothing
    assert.ok(r.grey() > 0.3 && r.grey() < 0.7, `${r.grey()}`);
    r.run(ENDING.drain);
    assert.equal(r.grey(), 1);
    assert.equal(r.stoodDown(), 0, 'respawned early');
    assert.ok(!onFoot || r.garage.current.down, 'got up');
    r.run(ENDING.respawn);
    assert.ok(!r.ending.active && r.words.hidden);
    assert.equal(r.screen.style.filter, '');
    assert.equal(r.stoodDown(), 1);
    assert.equal(r.player.hp, 100);
    assert.equal(r.garage.current.name, 'on foot');
    assert.ok(!r.garage.current.down);
    const p = r.garage.current.body.translation();
    const { door } = nearestSite(site, before.x, before.z);
    assert.ok(Math.hypot(p.x - door.x, p.z - door.z) < 1, `at ${p.x}, ${p.z}`);
  });
}
