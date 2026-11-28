import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { createRagdoll } from '../src/ragdoll.js';
import { TORSO, HEAD, THIGH } from '../src/people.js';

await RAPIER.init();

for (const name of ['car', 'motorbike', 'bicycle']) {
  test(`riding a ${name} into someone gives a ragdoll that holds together`, () => {
    const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
    physics.timestep = 1 / 60;
    const scene = new THREE.Scene();
    const world = buildWorld(scene, physics, generateCity());
    const traffic = createTraffic(scene, physics);
    traffic.set('cars', false);
    traffic.wanted.immune = true; // police would run into the story
    const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 }, name);
    const step = (throttle) => {
      garage.drive({ throttle, steer: 0, brake: false }, 1 / 60);
      world.update([garage.current.body.translation()]);
      traffic.update(garage, 1 / 60);
      physics.step();
      traffic.draw(garage.current.body.translation());
    };
    for (let i = 0; i < 120; i++) step(0);
    const bodies = physics.bodies.len();
    const joints = physics.impulseJoints.len();

    // Stand someone in the road ahead.
    const ped = traffic.sim.peds[0];
    // A fixed spot in the car's path; hold them there until they are hit.
    const start = garage.current.body.translation();
    const spot = { x: start.x, z: start.z + 14 };
    for (let i = 0; i < 360 && !traffic.ragdolls.size; i++) {
      if (traffic.sim.peds.includes(ped))
        Object.assign(ped, { ox: spot.x - (ped.x - ped.ox), oz: spot.z - (ped.z - ped.oz), ...spot });
      step(1);
    }
    assert.equal(traffic.ragdolls.size, 1);
    const [doll] = traffic.ragdolls.values();
    let furthest = 0;
    for (let i = 0; i < 240; i++) {
      step(0);
      const t = doll.torso.translation();
      assert.ok(t.y > -0.2 && t.y < 12, `torso at height ${t.y}`);
      for (const part of [doll.head, ...doll.arms, ...doll.forearms, ...doll.legs, ...doll.shins]) {
        const p = part.translation();
        furthest = Math.max(furthest, Math.hypot(p.x - t.x, p.y - t.y, p.z - t.z));
      }
    }
    assert.ok(furthest < 1, `came apart: ${furthest}`);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(garage.current.body.rotation()).y;
    assert.ok(up > 0.8, `vehicle tipped: ${up}`);

    for (let i = 0; i < 600; i++) step(0);
    // Leave them behind: anyone who did not get up is then cleared away.
    garage.current.body.setTranslation({ x: 600, y: 1.5, z: PITCH / 2 }, true);
    for (let i = 0; i < 5; i++) step(0);
    assert.equal(traffic.ragdolls.size, 0);
    assert.equal(physics.bodies.len(), bodies);
    assert.equal(physics.impulseJoints.len(), joints);
  });
}

// How deep the head sphere is inside the torso box, in metres.
function headDepth(doll) {
  const t = doll.torso.translation();
  const h = doll.head.translation();
  const local = new THREE.Vector3(h.x - t.x, h.y - t.y, h.z - t.z)
    .applyQuaternion(new THREE.Quaternion().copy(doll.torso.rotation()).invert());
  const half = new THREE.Vector3(TORSO.w / 2, TORSO.h / 2, TORSO.d / 2);
  const nearest = local.clone().clamp(half.clone().negate(), half);
  return HEAD.r - local.distanceTo(nearest);
}

test('thrown hard in any direction: head stays out of the torso, elbows mostly bend only forwards', () => {
  let deepest = 0;
  let elbows = 0;
  let outOfRange = 0;
  let knees = 0;
  let wrongKnees = 0;
  let apart = 0;
  let gapped = 0;
  let settled = 0; // the worst gap in the last second
  let mostBent = 0;
  // How far the lower part of a limb is turned about the hinge from the upper.
  const hinge = (upper, lower) => {
    const bend = new THREE.Quaternion().copy(upper.rotation()).invert()
      .multiply(new THREE.Quaternion().copy(lower.rotation()));
    const x = 2 * Math.atan2(bend.x, bend.w);
    return Math.atan2(Math.sin(x), Math.cos(x));
  };
  for (let n = 0; n < 48; n++) {
    const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
    physics.timestep = 1 / 60;
    physics.createCollider(RAPIER.ColliderDesc.cuboid(500, 1, 500).setTranslation(0, -1, 0));
    const angle = (n / 16) * Math.PI * 2;
    const speed = [5, 14, 30][n % 3];
    const doll = createRagdoll(physics, { x: 0, z: 0, yaw: 0 },
      { x: Math.sin(angle) * speed, y: 2 + (n % 5), z: Math.cos(angle) * speed });
    for (let i = 0; i < 300; i++) {
      physics.step();
      deepest = Math.max(deepest, headDepth(doll));
      doll.legs.forEach((leg, k) => {
        const angle = hinge(leg, doll.shins[k]);
        knees++;
        if (angle < -0.3 || angle > 2.7) wrongKnees++;
        mostBent = Math.max(mostBent, angle);
        // The shin stays on the end of the thigh.
        const end = new THREE.Vector3(0, -THIGH, 0).applyQuaternion(new THREE.Quaternion().copy(leg.rotation()))
          .add(leg.translation());
        const gap = end.distanceTo(doll.shins[k].translation());
        apart = Math.max(apart, gap);
        if (gap > 0.05) gapped++;
        if (i >= 240) settled = Math.max(settled, gap);
      });
      doll.arms.forEach((arm, k) => {
        const bend = new THREE.Quaternion().copy(arm.rotation()).invert()
          .multiply(new THREE.Quaternion().copy(doll.forearms[k].rotation()));
        const x = 2 * Math.atan2(bend.x, bend.w);
        const wrapped = Math.atan2(Math.sin(x), Math.cos(x));
        elbows++;
        if (wrapped > 0.3 || wrapped < -2.7) outOfRange++;
      });
    }
  }
  console.log({ deepest, outOfRange: outOfRange / elbows, wrongKnees: wrongKnees / knees, apart, mostBent });
  assert.ok(deepest < 0.05, `head sank ${deepest} m into the torso`);
  // Joint limits give a little under impact; without them this is over half.
  assert.ok(outOfRange / elbows < 0.05, 'elbows are bending the wrong way');
  assert.ok(wrongKnees / knees < 0.05, 'knees are bending the wrong way');
  assert.ok(mostBent > 1, 'knees never folded');
  // Joints stretch for an instant on the hardest impacts, as the elbows do too.
  assert.ok(apart < 0.4 && gapped / knees < 0.02, `legs came apart: ${apart}, ${gapped / knees} of the time`);
  assert.ok(settled < 0.06, `left apart: ${settled}`);
});
