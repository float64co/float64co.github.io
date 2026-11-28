// Firing: what the shot hits, what that does, and the brief effects.
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { createGun } from './guns.js';
import { WEAPONS, PUNCH, createArsenal } from './weapons.js';
import { material, part } from './parts.js';

const FLASH = 0.05; // seconds a tracer stays visible

export function createCombat(scene, camera, physics, traffic) {
  const arsenal = createArsenal();
  const direction = new THREE.Vector3();
  const origin = new THREE.Vector3();
  const muzzle = new THREE.Vector3();
  const target = new THREE.Vector3();
  let first = false; // first person, as of the last frame

  // First person models ride on the camera, which must be in the scene.
  scene.add(camera);
  const held = new Map(WEAPONS.filter((w) => w.magazine).map((w) => {
    const gun = createGun(w.name);
    gun.position.set(0.18, -0.16, -0.55);
    gun.rotation.y = Math.PI; // the camera looks along -z
    gun.visible = false;
    camera.add(gun);
    return [w.name, gun];
  }));

  // Fists for the first person view, one each side.
  const fists = [1, -1].map((side) => {
    const hand = new THREE.Group();
    part(hand, new THREE.BoxGeometry(0.09, 0.09, 0.1), material(0xffcc99), 0, 0, 0);
    part(hand, new THREE.BoxGeometry(0.1, 0.1, 0.3), material(0x37474f), 0, 0, 0.2);
    hand.userData.side = side;
    hand.visible = false;
    camera.add(hand);
    return hand;
  });
  let wait = 0;     // until the next punch
  let punching = 0;
  let thrown = 0;

  const tracer = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
    new THREE.LineBasicMaterial({ color: 0xffe082 }),
  );
  tracer.visible = false;
  tracer.frustumCulled = false;
  scene.add(tracer);
  let flash = 0;

  return {
    arsenal,
    // The walker fires from the end of the gun at whatever is in the centre of
    // the screen. Returns what was hit: { hp } for a person, {} for anything
    // else, or null if no round was fired.
    fire(walker) {
      const weapon = arsenal.fire();
      if (!weapon) return null;
      // What the reticule is on. Look from level with the shooter, so that
      // nothing between them and the camera counts.
      camera.getWorldDirection(direction);
      const chest = walker.body.translation();
      const skip = Math.max(0, muzzle.copy(chest).sub(camera.position).dot(direction));
      origin.copy(camera.position).addScaledVector(direction, skip);
      target.copy(origin).addScaledVector(direction, traffic.sight(origin, direction, weapon.range, walker.body));

      // Bring the gun up, then fire from its muzzle towards that point.
      walker.aim(target);
      const model = first ? held.get(weapon.name) : null;
      if (model) {
        camera.updateWorldMatrix(true, true);
        model.localToWorld(muzzle.copy(model.userData.muzzle));
      } else if (!walker.muzzle?.(muzzle)) muzzle.set(chest.x, chest.y + 0.45, chest.z);
      // Pressed against a wall, the barrel can poke through it. Such a round
      // starts on this side, so nobody can shoot through walls that way.
      const out = new THREE.Vector3().copy(muzzle).sub(chest);
      const arm = out.length();
      const wall = physics.castRay(new RAPIER.Ray(chest, out.normalize()), arm, true,
        RAPIER.QueryFilterFlags.EXCLUDE_SENSORS, undefined, undefined, walker.body);
      if (wall) muzzle.copy(chest).addScaledVector(out, Math.max(0, wall.timeOfImpact - 0.05));
      direction.copy(target).sub(muzzle);
      const far = direction.length();
      direction.normalize();
      // A little past the point, so that a surface exactly there is struck.
      const length = Math.min(weapon.range, far + 0.5);
      const solid = physics.castRay(new RAPIER.Ray(muzzle, direction), length, true,
        undefined, undefined, undefined, walker.body);
      const reach = solid ? solid.timeOfImpact : length;
      const hit = traffic.shoot(muzzle, direction, reach, solid?.collider, weapon.damage);

      const end = muzzle.clone().addScaledVector(direction, hit?.distance ?? (solid ? reach : far));
      tracer.geometry.setFromPoints([muzzle.clone(), end]);
      tracer.visible = true;
      flash = FLASH;
      return hit ? { hp: hit.hp } : {};
    },
    tracer,
    fists,
    // Bare hands, when no weapon is out. Returns what was hit: { hp } for a
    // person, {} for thin air, or null if a punch was not thrown.
    punch(walker) {
      if (arsenal.weapon.magazine || wait > 0) return null;
      wait = PUNCH.interval;
      punching = PUNCH.show;
      thrown++;
      const view = walker.view().yaw;
      const target = traffic.inReach(walker.body.translation(), view, PUNCH.reach, PUNCH.arc);
      const p = walker.body.translation();
      walker.punch(target ? { x: target.x, y: p.y + 0.45, z: target.z } : undefined);
      return target ? { hp: target.hit(PUNCH.damage) } : {};
    },
    // Call once per frame. walker is null when the player is in a vehicle.
    update(dt, walker, firstPerson) {
      first = firstPerson;
      arsenal.step(dt);
      wait = Math.max(0, wait - dt);
      punching = Math.max(0, punching - dt);
      fists.forEach((hand, i) => {
        hand.visible = Boolean(walker) && firstPerson && punching > 0 && thrown % 2 === i;
        const out = Math.sin((punching / PUNCH.show) * Math.PI);
        hand.position.set(hand.userData.side * (0.22 - 0.14 * out), -0.2 + 0.08 * out, -0.45 - 0.4 * out);
      });
      flash -= dt;
      if (flash <= 0) tracer.visible = false;
      for (const [name, gun] of held)
        gun.visible = Boolean(walker) && firstPerson && arsenal.weapon.name === name;
      walker?.hold(arsenal.weapon.magazine ? arsenal.weapon.name : null);
    },
  };
}
