import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { createTraffic } from '../src/traffic.js';
import { createCombat } from '../src/combat.js';
import { createWalker } from '../src/foot.js';
import { PUNCH, PED_HP } from '../src/weapons.js';

await RAPIER.init();
const DT = 1 / 60;

// The player at the origin facing +z, and one person at `at`.
function setup(at = { x: 0, z: 1.2 }) {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.createCollider(RAPIER.ColliderDesc.cuboid(500, 1, 500).setTranslation(0, -1, 0));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera();
  const traffic = createTraffic(scene, physics);
  traffic.set('cars', false);
  traffic.sim.step(DT, { x: 0, z: 0 });
  const [ped] = traffic.sim.peds;
  for (const other of traffic.sim.peds) if (other !== ped) other.x += 5000;
  traffic.sim.enabled.peds = false;
  const place = () => Object.assign(ped, { x: at.x, z: at.z });
  place();
  const walker = createWalker('on foot', scene, physics, { x: 0, y: 0.85, z: 0 });
  const combat = createCombat(scene, camera, physics, traffic);
  const swing = (first = false) => {
    place();
    combat.update(PUNCH.interval, walker, first);
    return combat.punch(walker);
  };
  return { traffic, scene, physics, walker, combat, ped, swing, camera };
}

test('punches wear someone down until they drop, and make you wanted', () => {
  const r = setup();
  const hits = Math.ceil(PED_HP / PUNCH.damage);
  for (let n = 1; n < hits; n++) assert.equal(r.swing().hp, PED_HP - n * PUNCH.damage);
  assert.equal(r.traffic.sim.fallen.length, 0);
  assert.ok(r.traffic.wanted.level >= 1);
  assert.equal(r.swing().hp, 0);
  assert.equal(r.traffic.sim.fallen.length, 1);
  assert.equal(r.traffic.ragdolls.size, 1);
  assert.deepEqual(r.swing(), {}, 'nobody left standing');
});

test('only what is in reach and in front, no faster than a fist, and not while armed', () => {
  for (const at of [{ x: 0, z: PUNCH.reach + 0.3 }, { x: 0, z: -1 }, { x: 1.4, z: 0 }]) {
    const r = setup(at);
    assert.deepEqual(r.swing(), {}, JSON.stringify(at));
    assert.equal(r.ped.hp, PED_HP);
  }
  const r = setup();
  assert.ok(r.swing().hp);
  assert.equal(r.combat.punch(r.walker), null, 'too soon');
  r.combat.arsenal.cycle(1);
  r.combat.update(1, r.walker, false);
  assert.equal(r.combat.punch(r.walker), null, 'with a gun out');
});

test('police can be punched too, which they take badly', () => {
  const r = setup({ x: 30, z: 30 });
  r.traffic.police.settings.guns = false;
  r.traffic.wanted.set(1);
  const garage = { current: r.walker, parked: new Map(), eject: () => false };
  for (let i = 0; i < 60 * 60 && !r.traffic.police.officers().length; i++) {
    r.walker.drive({}, DT);
    r.traffic.update(garage, DT);
    r.physics.step();
  }
  const [officer] = r.traffic.police.officers();
  assert.ok(officer, 'no officer came');
  const p = r.walker.body.translation();
  Object.assign(officer, { x: p.x, z: p.z + 1 });
  r.traffic.wanted.set(1);
  r.walker.look(r.walker.view().yaw / r.walker.tune.mouse, 0); // face +z
  r.combat.update(1, r.walker, false);
  assert.equal(r.combat.punch(r.walker).hp, 100 - PUNCH.damage);
  assert.ok(r.traffic.wanted.level >= 2);
});

test('an arm goes out in both views', () => {
  const r = setup();
  const hands = () => r.combat.fists.filter((f) => f.visible).length;
  const arms = () => {
    r.walker.sync();
    return r.walker.mesh.children[0].children.filter((m) => Math.abs(m.position.y - 0.45) < 1e-6)
      .map((m) => m.rotation.x);
  };
  assert.ok(arms().every((x) => x > -0.5));
  r.swing(false);
  r.walker.drive({ thirdPerson: true }, PUNCH.show / 2);
  assert.ok(Math.min(...arms()) < -1.2, `${arms()}`);
  assert.equal(hands(), 0);
  r.swing(true);
  r.combat.update(PUNCH.show / 2, r.walker, true);
  assert.equal(hands(), 1);
  r.combat.update(1, r.walker, true);
  assert.equal(hands(), 0);
});
