// A pedestrian as nine jointed Rapier bodies: torso (with the head), two
// upper arms, two forearms, two thighs (`legs`) and two shins.
import RAPIER from '@dimforge/rapier3d-compat';
import { TORSO, HEAD, LEG, ARM, FOREARM, THIGH } from './people.js';

// y: how far off the ground their feet are.
export function createRagdoll(physics, { x, z, yaw, altitude = 0 }, velocity) {
  const cos = Math.cos(yaw);
  const sin = Math.sin(yaw);
  const rotation = { x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) };

  // side is the offset to the person's side; y is the height of the body origin.
  function bodyAt(side, y, shape, mass) {
    const body = physics.createRigidBody(RAPIER.RigidBodyDesc.dynamic()
      .setTranslation(x + side * cos, y + altitude, z - side * sin)
      .setRotation(rotation)
      .setLinvel(velocity.x, velocity.y, velocity.z)
      .setLinearDamping(0.2)
      .setAngularDamping(0.6));
    physics.createCollider(shape.setMass(mass), body);
    return body;
  }
  const limb = (side, y, { w, h }, mass) => bodyAt(side, y,
    RAPIER.ColliderDesc.cuboid(w / 2, h / 2, w / 2).setTranslation(0, -h / 2, 0), mass);

  const torso = bodyAt(0, TORSO.y, RAPIER.ColliderDesc.cuboid(TORSO.w / 2, TORSO.h / 2, TORSO.d / 2), 30);
  // The head is part of the torso's body, so no impact can push one into the
  // other. (A separate head on a joint did, even with the joint limited.)
  const up = HEAD.y - TORSO.y;
  physics.createCollider(RAPIER.ColliderDesc.ball(HEAD.r).setTranslation(0, up, 0).setMass(5), torso);
  // Reads like a body, for drawing.
  const head = {
    rotation: () => torso.rotation(),
    translation() {
      const p = torso.translation();
      const { x, y, z, w } = torso.rotation();
      return {
        x: p.x + 2 * (x * y - w * z) * up,
        y: p.y + (1 - 2 * (x * x + z * z)) * up,
        z: p.z + 2 * (y * z + w * x) * up,
      };
    },
  };
  const arms = [-1, 1].map((s) => limb(s * ARM.x, ARM.y, ARM, 2.5));
  const forearms = [-1, 1].map((s) => limb(s * ARM.x, ARM.y - ARM.h, FOREARM, 1.5));
  const legs = [-1, 1].map((s) => limb(s * LEG.x, LEG.y, { w: LEG.w, h: THIGH }, 5.5));
  const shins = [-1, 1].map((s) => limb(s * LEG.x, LEG.y - THIGH, { w: LEG.w * 0.9, h: THIGH }, 3.5));

  // anchor is on the torso; own is on the joined part.
  function join(part, anchor, own = { x: 0, y: 0, z: 0 }) {
    const joint = physics.createImpulseJoint(RAPIER.JointData.spherical(anchor, own), torso, part, true);
    joint.setContactsEnabled(false);
  }
  arms.forEach((arm, i) => join(arm, { x: (i ? 1 : -1) * ARM.x, y: ARM.y - TORSO.y, z: 0 }));
  legs.forEach((leg, i) => join(leg, { x: (i ? 1 : -1) * LEG.x, y: LEG.y - TORSO.y, z: 0 }));
  // Elbows are hinges that only bend forwards.
  arms.forEach((arm, i) => {
    const elbow = physics.createImpulseJoint(RAPIER.JointData.revolute(
      { x: 0, y: -ARM.h, z: 0 }, { x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 0 },
    ), arm, forearms[i], true);
    elbow.setContactsEnabled(false);
    elbow.setLimits(-2.4, 0);
  });
  // Knees likewise, folding the other way.
  legs.forEach((leg, i) => {
    const knee = physics.createImpulseJoint(RAPIER.JointData.revolute(
      { x: 0, y: -THIGH, z: 0 }, { x: 0, y: 0, z: 0 }, { x: 1, y: 0, z: 0 },
    ), leg, shins[i], true);
    knee.setContactsEnabled(false);
    knee.setLimits(0, 2.4);
  });
  // A little tumble so no two falls look alike.
  torso.setAngvel({ x: velocity.z * 0.4, y: 0, z: -velocity.x * 0.4 }, true);

  const bodies = [torso, ...arms, ...forearms, ...legs, ...shins];
  return {
    torso, head, arms, forearms, legs, shins,
    // Removing a body also removes its joints.
    dispose() {
      for (const body of bodies) physics.removeRigidBody(body);
    },
  };
}
