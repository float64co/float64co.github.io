// An officer leaning out of a flying police car's side window, reaching round
// the windscreen to shoot. Part of the car's model, with an arm that aims.
import * as THREE from 'three';
import { material, part } from './parts.js';
import { createGun } from './guns.js';
import { headGeometry, headMaterial, personOf, variantOf, HEAD_SIZE } from './head.js';

const ARM_LENGTH = 0.55;
let hired = 0;

// Someone other than an officer: { person, top, cap: false }. seat: how far
// back along the car they lean out. fixed: the only side their seat lets them
// use (1 left, -1 right), or null to lean out of whichever faces the target.
export function createGunner(car, look = null, seat = 0.35, fixed = null) {
  const variant = look ? null : variantOf(hired++);
  const person = look?.person ?? personOf(variant);
  const group = new THREE.Group();
  const navy = material(look?.top ?? 0x16245c);
  const skin = material(person.skin);
  const dark = material(0x101010);
  // Head and shoulders out of the window, leaning forwards.
  const body = new THREE.Group();
  group.add(body);
  part(body, new THREE.BoxGeometry(0.34, 0.55, 0.28), navy, 0, 0, 0).rotation.x = 0.3;
  const head = part(body, headGeometry(person), headMaterial(), 0.06, 0.45, 0.1);
  if (look?.cap ?? true) part(head, new THREE.CylinderGeometry(0.14, 0.155, 0.09, 12), dark, 0, HEAD_SIZE.h - 0.02, 0);
  // The shoulder: everything under it points at the target.
  const arm = new THREE.Group();
  group.add(arm);
  part(arm, new THREE.BoxGeometry(0.12, 0.12, ARM_LENGTH), navy, 0, 0, ARM_LENGTH / 2);
  part(arm, new THREE.BoxGeometry(0.1, 0.1, 0.1), skin, 0, 0, ARM_LENGTH);
  let gun = null;
  let weapon = null;
  car.mesh.children[0].add(group);

  const aimAt = new THREE.Vector3();
  const centre = new THREE.Vector3();
  const along = new THREE.Vector3();
  return {
    group,
    arm,
    head,
    variant,
    // Where a ray meets them: { distance, head, point } or null.
    hit(from, dir, reach) {
      car.mesh.position.copy(car.body.translation());
      car.mesh.quaternion.copy(car.body.rotation());
      car.mesh.updateWorldMatrix(true, true);
      let best = null;
      // The head, and two balls for the chest and shoulders.
      for (const [part, offset, radius, isHead] of [[head, 0, 0.18, true], [body, 0.12, 0.25, false], [body, -0.15, 0.25, false]]) {
        part.localToWorld(centre.set(0, offset, 0));
        along.set(centre.x - from.x, centre.y - from.y, centre.z - from.z);
        const ahead = along.x * dir.x + along.y * dir.y + along.z * dir.z;
        const miss = along.lengthSq() - ahead * ahead;
        if (ahead < 0 || miss > radius * radius) continue;
        const distance = ahead - Math.sqrt(radius * radius - miss);
        if (distance <= reach && distance < (best?.distance ?? Infinity))
          best = { distance, head: isHead, point: centre.clone() };
      }
      return best;
    },
    // Lean out on the side facing `target` (world) and point `name` at it.
    // Returns where the end of the barrel then is.
    aim(target, name) {
      if (name !== weapon) {
        gun?.removeFromParent();
        weapon = name;
        gun = createGun(name);
        gun.position.set(0, 0, ARM_LENGTH + 0.03);
        arm.add(gun);
      }
      // The car may not have been drawn since it last moved.
      car.mesh.position.copy(car.body.translation());
      car.mesh.quaternion.copy(car.body.rotation());
      car.mesh.updateWorldMatrix(true, true);
      aimAt.copy(target);
      const side = fixed ?? (Math.sign(car.mesh.worldToLocal(aimAt.clone()).x) || -1);
      body.position.set(side * 1.08, 0.72, seat);
      body.rotation.z = -side * 0.35; // leaning out
      arm.position.set(side * 1.3, 0.88, seat + 0.15);
      group.updateWorldMatrix(true, true);
      arm.lookAt(aimAt);
      arm.updateWorldMatrix(true, true);
      return gun.localToWorld(gun.userData.muzzle.clone());
    },
    remove() {
      group.removeFromParent();
    },
  };
}
