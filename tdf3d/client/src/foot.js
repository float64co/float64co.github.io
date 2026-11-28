// The player on foot: an upright capsule whose horizontal velocity we set directly.
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { material, part } from './parts.js';
import { ARM, FOREARM, elbowBend, kneeBend } from './people.js';
import { createGun } from './guns.js';
import { PUNCH } from './weapons.js';
import { SUIT } from './suit.js';
import { headGeometry, headMaterial } from './head.js';
import { createRagdoll } from './ragdoll.js';
import { pushOut } from './officers.js';

export const WALK_TUNE = { walk: 2.5, run: 7, turn: 2.8, mouse: 0.0025 }; // mouse: radians per pixel

const TURN_MOVING = 12;  // third person: radians per second towards the way you're going
const TURN_STILL = 6;    // and back towards the camera once you stop
const AIMING = 1;        // seconds the gun stays up after a shot
export const DOWN = 3;   // seconds on the ground after being knocked over

// Turn `from` towards `to` the short way round, by at most `step`.
function approach(from, to, step) {
  const gap = Math.atan2(Math.sin(to - from), Math.cos(to - from));
  return from + Math.max(-step, Math.min(step, gap));
}

export function createWalker(name, scene, physics, spawn) {
  const tune = { ...WALK_TUNE };
  let yaw = spawn.yaw ?? 0; // where the camera looks
  let facing = yaw;         // where the body points
  let pitch = 0; // where the head is looking; positive is up
  const body = physics.createRigidBody(
    RAPIER.RigidBodyDesc.dynamic().setTranslation(spawn.x, spawn.y, spawn.z)
      .lockRotations().setCanSleep(false),
  );
  physics.createCollider(RAPIER.ColliderDesc.capsule(0.5, 0.35).setMass(80).setFriction(0), body);

  const mesh = new THREE.Group(); // origin at the capsule centre, feet at -0.85
  const upright = new THREE.Group(); // hidden while knocked down
  mesh.add(upright);
  const shirt = material(0x37474f);
  const skin = material(0xffcc99);
  part(upright, new THREE.BoxGeometry(0.5, 0.65, 0.3), shirt, 0, 0.15, 0);
  const face = headGeometry({ skin: 0xffcc99, hair: 0x5a3b1c });
  const faceMaterial = headMaterial();
  part(upright, face, faceMaterial, 0, 0.7, 0);
  const limb = (x, y, length, width, mat) => {
    const geometry = new THREE.BoxGeometry(width, length, width).translate(0, -length / 2, 0);
    return part(upright, geometry, mat, x, y, 0);
  };
  const jeans = material(0x1a237e);
  const legs = [-0.14, 0.14].map((x) => limb(x, -0.18, 0.335, 0.18, jeans));
  const below = new THREE.BoxGeometry(0.162, 0.335, 0.162).translate(0, -0.335 / 2, 0);
  const shins = legs.map((leg) => part(leg, below, jeans, 0, -0.335, 0)); // hinged at the knee
  const arms = [-0.34, 0.34].map((x) => limb(x, 0.45, ARM.h, ARM.w, shirt));
  const hanging = new THREE.BoxGeometry(FOREARM.w, FOREARM.h, FOREARM.w).translate(0, -FOREARM.h / 2, 0);
  const forearms = arms.map((arm) => part(arm, hanging, skin, 0, -ARM.h, 0));
  scene.add(mesh);

  // The right hand is at the end of the forearm on the -x side.
  let gun = null;
  let weapon = null;
  let aiming = 0;
  let punching = 0; // seconds left of a punch
  let fist = 1;     // which arm throws it: they take turns
  // How far the gun is turned from where the camera looks, to point at the target.
  const off = { yaw: 0, pitch: 0 };

  // While knocked down the body is switched off and follows a ragdoll.
  let doll = null;
  let lying = 0;
  const fallen = new THREE.Group();
  scene.add(fallen);
  const limp = (width, height, mat, drop) => part(fallen,
    new THREE.BoxGeometry(width, height, width).translate(0, -drop, 0), mat, 0, 0, 0);
  function createLimp() {
    return {
      torso: part(fallen, new THREE.BoxGeometry(0.5, 0.65, 0.3), shirt, 0, 0, 0),
      head: part(fallen, face, faceMaterial, 0, 0, 0),
      legs: [0, 1].map(() => limp(0.18, 0.35, jeans, 0.175)),
      shins: [0, 1].map(() => limp(0.162, 0.35, jeans, 0.175)),
      arms: [0, 1].map(() => limp(ARM.w, ARM.h, shirt, ARM.h / 2)),
      forearms: [0, 1].map(() => limp(FOREARM.w, FOREARM.h, skin, FOREARM.h / 2)),
    };
  }
  let limbs = null;

  function standUp(clearOf) {
    // Beside anything that has come to rest on top, rather than inside it.
    const { x, z } = doll.torso.translation();
    const p = { x, z };
    pushOut(p, clearOf);
    doll.dispose();
    doll = null;
    fallen.visible = false;
    upright.visible = true;
    body.setEnabled(true);
    body.setTranslation({ x: p.x, y: 0.9, z: p.z }, true);
    body.setLinvel({ x: 0, y: 0, z: 0 }, true);
  }

  const rotation = new THREE.Quaternion();
  let stride = 0;
  let swing = 0;
  let gait = 0;  // how much the knees are working, 0 to 1
  let backwards = 1;

  return {
    name: 'on foot', body, mesh, tune,
    get down() { return Boolean(doll); },
    get downFor() { return doll ? lying : 0; },
    clearOf: [], // vehicle footprints nearby
    // Set false to stay on the ground (no health left).
    able: true,
    // Thrown over by something moving at (vx, vz).
    knock(vx, vz) {
      if (doll) return;
      const p = body.translation();
      const speed = Math.hypot(vx, vz);
      const scale = Math.min(1.1, 30 / (speed || 1));
      body.setLinvel({ x: 0, y: 0, z: 0 }, true);
      body.setEnabled(false);
      doll = createRagdoll(physics, { x: p.x, z: p.z, yaw: facing },
        { x: vx * scale, y: Math.min(2 + speed * 0.15, 7), z: vz * scale });
      lying = 0;
      aiming = 0;
      limbs ??= createLimp();
      upright.visible = false;
      fallen.visible = true;
    },
    // The ragdoll's bodies, for anything that needs to tell them apart.
    get ragdoll() { return doll; },
    // Show a weapon in the hand, or none.
    hold(name) {
      if (name === weapon) return;
      weapon = name;
      gun?.removeFromParent();
      gun = null;
      if (!name) return;
      gun = createGun(name);
      gun.position.set(0, -FOREARM.h, 0);
      gun.rotation.x = Math.PI / 2; // barrel continues the line of the forearm
      forearms[0].add(gun);
    },
    // Raise the gun for a moment, pointing at `target` if given, or else the way
    // the camera looks. Takes effect at once, so the muzzle is where it will show.
    aim(target) {
      aiming = AIMING;
      off.yaw = 0;
      off.pitch = 0;
      if (target) {
        const p = body.translation();
        // From the right shoulder, as near as matters.
        const x = p.x - Math.cos(yaw) * ARM.x;
        const z = p.z + Math.sin(yaw) * ARM.x;
        const flat = Math.hypot(target.x - x, target.z - z);
        if (flat > 1) {
          off.yaw = Math.atan2(target.x - x, target.z - z) - yaw;
          off.pitch = Math.atan2(target.y - (p.y + 0.45), flat) - pitch;
        }
      }
      facing = yaw + off.yaw;
      body.setRotation(rotation.setFromAxisAngle({ x: 0, y: 1, z: 0 }, facing), true);
      this.sync();
    },
    // Throw a punch, towards `target` if given.
    punch(target) {
      this.aim(target);
      aiming = PUNCH.show;
      punching = PUNCH.show;
      fist = 1 - fist;
      this.sync();
    },
    // Where the end of the gun is now, or null if there is none.
    muzzle(out) {
      if (!gun) return null;
      mesh.updateWorldMatrix(true, true);
      return gun.localToWorld(out.copy(gun.userData.muzzle));
    },
    camera: { back: 4.5, up: 2.2 },
    help: 'Click for mouse look · WASD move · ←/→ turn · Shift run',
    // Mouse movement in pixels: right turns right, up looks up.
    look(dx, dy) {
      yaw -= dx * tune.mouse;
      pitch = Math.max(-1.4, Math.min(1.4, pitch - dy * tune.mouse));
    },
    view: () => ({ yaw, pitch }),
    // strafe and turn are positive to the left. In third person the body turns
    // the way it walks; otherwise it faces the camera and can strafe.
    drive({ throttle = 0, strafe = 0, turn = 0, run, thirdPerson = false, suit = false }, dt) {
      yaw += turn * tune.turn * dt;
      if (doll) {
        lying += dt;
        body.setTranslation(doll.torso.translation(), true);
        if (lying >= DOWN && this.able) standUp(this.clearOf);
        return;
      }
      aiming = Math.max(0, aiming - dt);
      punching = Math.max(0, punching - dt);
      if (aiming) thirdPerson = false; // face the target and strafe
      // Going diagonally is no faster than going straight.
      const boost = suit ? (run ? SUIT.run : SUIT.walk) : 1;
      const top = ((run ? tune.run : tune.walk) * boost) / Math.max(1, Math.hypot(throttle, strafe));
      const speed = Math.hypot(throttle, strafe) * top;
      let heading; // direction of travel
      if (thirdPerson) {
        if (speed) facing = approach(facing, yaw + Math.atan2(strafe, throttle), TURN_MOVING * dt);
        else facing = approach(facing, yaw, TURN_STILL * dt);
        heading = facing;
      } else {
        facing = yaw + (aiming ? off.yaw : 0);
        heading = yaw + Math.atan2(strafe, throttle);
      }
      body.setLinvel({
        x: Math.sin(heading) * speed, y: body.linvel().y, z: Math.cos(heading) * speed,
      }, true);
      const pace = speed * (!thirdPerson && throttle < 0 ? -1 : 1);
      body.setRotation(rotation.setFromAxisAngle({ x: 0, y: 1, z: 0 }, facing), true);
      stride += pace * dt * (suit ? SUIT.stride : 3);
      swing = Math.sin(stride) * Math.min(Math.abs(pace) / 5, 0.9);
      gait = Math.min(Math.abs(pace) / 2, 1);
      backwards = pace < 0 ? -1 : 1;
    },
    sync() {
      mesh.position.copy(body.translation());
      mesh.quaternion.copy(body.rotation());
      if (doll) {
        const copy = (to, from) => {
          to.position.copy(from.translation());
          to.quaternion.copy(from.rotation());
        };
        copy(limbs.torso, doll.torso);
        copy(limbs.head, doll.head);
        for (const k of [0, 1]) {
          copy(limbs.legs[k], doll.legs[k]);
          copy(limbs.shins[k], doll.shins[k]);
          copy(limbs.arms[k], doll.arms[k]);
          copy(limbs.forearms[k], doll.forearms[k]);
        }
        return;
      }
      legs.forEach((leg, i) => {
        const sign = i ? -1 : 1;
        leg.rotation.x = sign * swing;
        shins[i].rotation.x = kneeBend(stride, sign * backwards, gait);
      });
      arms.forEach((arm, i) => {
        arm.rotation.x = i ? swing : -swing;
        forearms[i].rotation.x = elbowBend(arm.rotation.x);
        if (i === fist && punching) {
          // Out and back again.
          const out = Math.sin((punching / PUNCH.show) * Math.PI);
          arm.rotation.x = -(Math.PI / 2) * (0.35 + 0.65 * out);
          forearms[i].rotation.x = -1.6 * (1 - out);
          return;
        }
        if (i === 0 && gun && aiming) {
          arm.rotation.x = -Math.PI / 2 - pitch - off.pitch; // point at the target
          forearms[i].rotation.x = 0;
        }
      });
    },
    speed() {
      const v = body.linvel();
      return Math.hypot(v.x, v.z);
    },
    dispose() {
      doll?.dispose();
      doll = null;
      scene.remove(fallen);
      physics.removeRigidBody(body);
      scene.remove(mesh);
    },
  };
}
