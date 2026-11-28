import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { kneeBend, THIGH, LEG } from '../src/people.js';
import { createTraffic } from '../src/traffic.js';
import { createWalker } from '../src/foot.js';

await RAPIER.init();

test('knees fold only backwards, most on the leg coming forward, and not when standing', () => {
  for (let stride = 0; stride < 7; stride += 0.1) {
    for (const sign of [-1, 1]) {
      const bend = kneeBend(stride, sign);
      assert.ok(bend > 0 && bend < 1.3, `${bend}`);
      assert.equal(kneeBend(stride, sign, 0), 0);
      // The hip is turned by sin(stride) * sign; falling means coming forward.
      const forward = Math.cos(stride) * sign < -0.7;
      const back = Math.cos(stride) * sign > 0.2;
      if (forward) assert.ok(bend > 0.6);
      if (back) assert.ok(bend < 0.1, 'bent while carrying the weight');
    }
  }
});

// Where a person's knee and foot are, from the two instanced parts.
function joints(body, k) {
  const read = (mesh) => {
    const m = new THREE.Matrix4();
    mesh.getMatrixAt(k, m);
    return m;
  };
  const down = (m) => new THREE.Vector3(0, -THIGH, 0).applyMatrix4(m);
  const hip = new THREE.Vector3().setFromMatrixPosition(read(body.thighs));
  const knee = down(read(body.thighs));
  const top = new THREE.Vector3().setFromMatrixPosition(read(body.shins));
  return { hip, knee, top, foot: down(read(body.shins)) };
}

test('walking people have joined, bending legs with feet that never sink', () => {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const traffic = createTraffic(new THREE.Scene(), physics);
  traffic.set('cars', false);
  traffic.sim.step(1 / 60, { x: 0, z: 0 });
  for (const ped of traffic.sim.peds) ped.walking = true;
  let bent = 0;
  for (let n = 0; n < 20; n++) {
    for (const ped of traffic.sim.peds) ped.stride = n * 0.33;
    traffic.draw({ x: 0, y: 0, z: 0 });
    for (const body of Object.values(traffic.bodies)) {
      for (const k of [0, 1]) {
        const { hip, knee, top, foot } = joints(body, k);
        assert.ok(knee.distanceTo(top) < 1e-3, `shin came off the knee: ${knee.distanceTo(top)}`); // 32-bit matrices
        assert.ok(foot.y > -1e-3, `foot below ground: ${foot.y}`);
        if (hip.distanceTo(foot) < LEG.h - 0.03) bent++;
      }
    }
  }
  assert.ok(bent > 20, 'knees never bent');
});

test('the player bends their knees walking, and stands straight', () => {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const walker = createWalker('on foot', new THREE.Scene(), physics, { x: 0, y: 1, z: 0 });
  const knees = () => {
    const found = [];
    walker.mesh.traverse((o) => {
      if (o.isMesh && o.parent.isMesh && Math.abs(o.position.y + 0.335) < 1e-6) found.push(o.rotation.x);
    });
    return found;
  };
  let most = 0;
  for (let i = 0; i < 120; i++) {
    walker.drive({ throttle: 1 }, 1 / 60);
    walker.sync();
    assert.equal(knees().length, 2);
    most = Math.max(most, ...knees());
  }
  assert.ok(most > 0.7, `${most}`);
  walker.drive({ throttle: 0 }, 1 / 60);
  walker.sync();
  assert.deepEqual(knees(), [0, 0]);
});
