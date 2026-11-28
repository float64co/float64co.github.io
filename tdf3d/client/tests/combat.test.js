import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { createArsenal, PED_HP } from '../src/weapons.js';
import { createTraffic } from '../src/traffic.js';
import { createCombat } from '../src/combat.js';
import { createWalker } from '../src/foot.js';

await RAPIER.init();
const DT = 1 / 60;

test('scrolling cycles weapons; ammo runs down and reloads itself', () => {
  const arsenal = createArsenal();
  assert.equal(arsenal.fire(), null, 'unarmed');
  const names = [1, 1, 1, -1].map((d) => { arsenal.cycle(d * 100); return arsenal.weapon.name; });
  assert.deepEqual(names, ['Glock 17', 'BRN-180', 'unarmed', 'BRN-180']);
  arsenal.cycle(-1);
  let fired = 0;
  for (let i = 0; i < 17 * 20; i++) {
    arsenal.step(DT);
    if (arsenal.fire()) fired++;
    if (arsenal.reloading) break;
  }
  assert.deepEqual([fired, arsenal.ammo], [17, 0]);
  assert.equal(arsenal.fire(), null);
  arsenal.step(2);
  assert.equal(arsenal.ammo, 17);
});

test('a part-used magazine can be reloaded early', () => {
  const arsenal = createArsenal();
  assert.ok(!arsenal.reload(), 'unarmed');
  arsenal.cycle(1);
  assert.ok(!arsenal.reload(), 'already full');
  arsenal.step(1);
  assert.ok(arsenal.fire());
  assert.ok(arsenal.reload());
  assert.ok(!arsenal.reload(), 'already reloading');
  arsenal.step(0.5);
  assert.equal(arsenal.fire(), null, 'fired while reloading');
  assert.equal(arsenal.ammo, 16);
  arsenal.step(2);
  assert.equal(arsenal.ammo, 17);
});

// One person standing 10 m in front of an armed player.
function range() {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.createCollider(RAPIER.ColliderDesc.cuboid(500, 1, 500).setTranslation(0, -1, 0));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera();
  const traffic = createTraffic(scene, physics);
  traffic.set('cars', false);
  traffic.sim.step(DT, { x: 0, z: 0 });
  const ped = traffic.sim.peds[0];
  for (const other of traffic.sim.peds) if (other !== ped) other.x += 5000;
  traffic.sim.enabled.peds = false;
  Object.assign(ped, { x: 0, z: 10 });
  const walker = createWalker('on foot', scene, physics, { x: 0, y: 0.85, z: 0 });
  const combat = createCombat(scene, camera, physics, traffic);
  combat.arsenal.cycle(1);
  const shoot = (height) => {
    combat.update(0.5, walker, true);
    camera.position.set(0, 1.5, 0);
    camera.lookAt(0, height, 10);
    physics.step();
    return combat.fire(walker);
  };
  return { physics, traffic, ped, shoot };
}

test('body shots wear HP down; at zero they drop and stay down', () => {
  const { traffic, ped, shoot } = range();
  assert.equal(ped.hp, PED_HP);
  assert.deepEqual([1, 2, 3].map(() => shoot(1).hp), [75, 50, 25]);
  assert.equal(traffic.sim.fallen.length, 0);
  assert.equal(shoot(1).hp, 0);
  assert.equal(traffic.sim.fallen.length, 1);
  assert.equal(traffic.ragdolls.size, 1);
  for (let i = 0; i < 10 * 60; i++) traffic.sim.step(DT, { x: 0, z: 0 });
  assert.ok(!traffic.sim.peds.includes(ped), 'got up');
  for (let i = 0; i < 60 * 60; i++) traffic.sim.step(DT, { x: 0, z: 0 });
  assert.equal(traffic.sim.fallen.length, 1, 'cleared away on a timer');
  traffic.sim.step(DT, { x: 200, z: 0 });
  assert.equal(traffic.sim.fallen.length, 0, 'not cleared away');
});

test('head shots do more, misses and walls do nothing', () => {
  const { physics, ped, shoot } = range();
  assert.deepEqual(shoot(4), {}, 'over their head');
  assert.equal(shoot(1.6).hp, 25);
  physics.createCollider(RAPIER.ColliderDesc.cuboid(2, 3, 0.2).setTranslation(0, 3, 5));
  assert.deepEqual(shoot(1), {});
  assert.equal(ped.hp, 25);
});

for (const firstPerson of [true, false]) {
  test(`${firstPerson ? 'first' : 'third'} person: the round leaves the gun and goes to the reticule`, async () => {
    const { createCamera } = await import('../src/camera.js');
    const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
    physics.createCollider(RAPIER.ColliderDesc.cuboid(500, 1, 500).setTranslation(0, -1, 0));
    // A wall to shoot at.
    physics.createCollider(RAPIER.ColliderDesc.cuboid(30, 15, 0.5).setTranslation(0, 15, 20));
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(65, 16 / 9, 0.3, 500);
    const traffic = createTraffic(scene, physics);
    traffic.set('cars', false);
    traffic.set('peds', false);
    const walker = createWalker('on foot', scene, physics, { x: 0, y: 0.85, z: 0 });
    const combat = createCombat(scene, camera, physics, traffic);
    const chase = createCamera(camera, physics);
    if (firstPerson) chase.toggle(walker);
    combat.arsenal.cycle(1);
    for (const [turn, tilt] of [[0, 0], [0.4, 0.25], [-0.5, -0.1]]) {
      walker.drive({ throttle: 0 }, DT);
      const view = walker.view();
      walker.look((view.yaw - turn) / walker.tune.mouse, (view.pitch - tilt) / walker.tune.mouse);
      physics.step();
      walker.drive({ throttle: 0 }, DT);
      walker.sync();
      combat.update(0.5, walker, firstPerson);
      chase.update(walker, DT, true);
      assert.ok(combat.fire(walker));
      const points = combat.tracer.geometry.attributes.position;
      const start = new THREE.Vector3().fromBufferAttribute(points, 0);
      const end = new THREE.Vector3().fromBufferAttribute(points, 1);
      // It ends where the middle of the screen is.
      const seen = end.clone().project(camera);
      assert.ok(Math.hypot(seen.x, seen.y) < 0.02, `${turn},${tilt}: lands ${seen.x}, ${seen.y} off centre`);
      // It starts at the end of the gun that is showing, not the middle of the player.
      let muzzle;
      if (firstPerson) {
        const gun = camera.children.find((c) => c.visible);
        muzzle = gun.localToWorld(gun.userData.muzzle.clone());
      } else muzzle = walker.muzzle(new THREE.Vector3());
      assert.ok(start.distanceTo(muzzle) < 1e-3, `starts ${start.distanceTo(muzzle)} from the muzzle`);
      const chest = walker.body.translation();
      assert.ok(Math.hypot(start.x - chest.x, start.z - chest.z) > 0.2, 'from the middle of the player');
      // And the arm points that way.
      if (!firstPerson) {
        const hand = walker.mesh.position;
        const along = start.clone().sub(hand).setY(0).normalize();
        const aim = end.clone().sub(start).setY(0).normalize();
        assert.ok(along.dot(aim) > 0.7, 'gun not towards the target');
      }
      combat.update(1, walker, firstPerson);
    }
  });
}

test('a barrel poked through a thin wall does not shoot from the other side', () => {
  const { physics, traffic, ped, shoot } = range();
  // A sheet between the chest (z = 0) and the end of the gun.
  physics.createCollider(RAPIER.ColliderDesc.cuboid(2, 3, 0.02).setTranslation(0, 3, 0.4));
  assert.deepEqual(shoot(1), {});
  assert.equal(ped.hp, PED_HP);
  assert.equal(traffic.sim.fallen.length, 0);
});
