// A police officer on foot: walks round vehicles, can be run over or shot, and
// is otherwise a model with no solid body, only something a bullet can hit.
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { material } from './parts.js';
import { createRagdoll } from './ragdoll.js';
import { createGun } from './guns.js';
import { headGeometry, headMaterial, personOf, variantOf, HEAD_SIZE } from './head.js';
import { TORSO, HEAD, LEG, ARM, FOREARM, THIGH, elbowBend, kneeBend } from './people.js';

export const OFFICER = { hp: 100, speed: 4.8, headshot: 3 };
const BODY = 0.35;     // metres: how close to a vehicle's side they can be
const DOWN = 7;        // seconds on the ground before getting up
const GROUP = 0x0002;  // thin things the camera ignores (see poles.js)
const CLIMB = 1.2;     // metres up when clambering over a car

// Where (x, z) is in a footprint's own frame, and the way back.
const local = (f, x, z) => [(x - f.x) * f.hx + (z - f.z) * f.hz, (x - f.x) * f.hz - (z - f.z) * f.hx];
const world = (f, along, across) => ({
  x: f.x + f.hx * along + f.hz * across,
  z: f.z + f.hz * along - f.hx * across,
});

// Where to stand to open a door: the side of a footprint nearer to (x, z), and
// whether someone there is alongside rather than off an end.
export function sideDoor(f, x, z) {
  const [along, across] = local(f, x, z);
  const side = across < 0 ? -1 : 1;
  return { ...world(f, 0, side * (f.halfW + BODY + 0.1)), side, alongside: Math.abs(along) <= f.halfL };
}
export const beside = (f, side, out) => world(f, 0, side * (f.halfW + out));

// The point of a footprint nearest to (x, z).
export function nearestOn(f, x, z) {
  const [along, across] = local(f, x, z);
  const keep = (v, limit) => Math.max(-limit, Math.min(limit, v));
  return world(f, keep(along, f.halfL), keep(across, f.halfW));
}

// Does the straight line from a to b pass through the footprint, grown by `by`?
function crosses(f, a, b, by) {
  const from = local(f, a.x, a.z);
  const to = local(f, b.x, b.z);
  let enter = 0;
  let leave = 1;
  for (const [k, half] of [[0, f.halfL + by], [1, f.halfW + by]]) {
    const step = to[k] - from[k];
    if (Math.abs(step) < 1e-9) {
      if (Math.abs(from[k]) >= half) return false;
      continue;
    }
    const first = (-half - from[k]) / step;
    const second = (half - from[k]) / step;
    enter = Math.max(enter, Math.min(first, second));
    leave = Math.min(leave, Math.max(first, second));
  }
  return enter < leave;
}

const contains = (f, p, by) => {
  const [along, across] = local(f, p.x, p.z);
  return Math.abs(along) < f.halfL + by && Math.abs(across) < f.halfW + by;
};

// Where to head for, from `at` to `goal`: the first corner of the shortest way
// round all the vehicles between them (Dijkstra over their corners). null if
// they are walled off.
export function around(at, goal, boxes) {
  const near = boxes.filter((f) => Math.hypot(f.x - at.x, f.z - at.z) < 20 + f.halfL + f.halfW);
  const margin = BODY - 0.05;
  // A box that either end is already touching cannot be avoided.
  const clear = (a, b) => near.every((f) => contains(f, a, margin) || contains(f, b, margin)
    || !crosses(f, a, b, margin));
  if (clear(at, goal)) return goal;
  const corners = near.flatMap((f) => [[1, 1], [1, -1], [-1, 1], [-1, -1]]
    .map(([a, b]) => world(f, a * (f.halfL + BODY + 0.2), b * (f.halfW + BODY + 0.2))))
    // Not buried in another vehicle, nor already underfoot.
    .filter((c) => !near.some((f) => contains(f, c, margin)) && Math.hypot(c.x - at.x, c.z - at.z) > 0.15);
  const nodes = [at, goal, ...corners].map((point) => ({ point, cost: Infinity, first: null, done: false }));
  nodes[0].cost = 0;
  for (;;) {
    let best = null;
    for (const node of nodes) if (!node.done && node.cost < (best?.cost ?? Infinity)) best = node;
    if (!best || best === nodes[1]) break;
    best.done = true;
    for (const next of nodes) {
      if (next.done) continue;
      const cost = best.cost + Math.hypot(next.point.x - best.point.x, next.point.z - best.point.z);
      if (cost >= next.cost || !clear(best.point, next.point)) continue;
      next.cost = cost;
      next.first = best === nodes[0] ? next.point : best.first;
    }
  }
  return nodes[1].first;
}

export const APART = BODY * 2; // metres between the middles of two officers

// Stop officers standing in each other, or in the player. One who has hold of
// the player stays put and the others give way; nobody moves the player.
// boxes: vehicles nobody is to be pushed into. solid: [{ x, z }] of the player.
export function separate(officers, boxes, solid = []) {
  const standing = officers.filter((o) => !o.down);
  // A few passes, as moving one apart can push them into another.
  for (let pass = 0; pass < 4; pass++) {
    for (let a = 0; a < standing.length; a++)
      for (let b = a + 1; b < standing.length; b++) {
        const [one, two] = [standing[a], standing[b]];
        let dx = two.x - one.x;
        let dz = two.z - one.z;
        let d = Math.hypot(dx, dz);
        if (d >= APART) continue;
        // Exactly on top of each other: any direction will do.
        if (d < 1e-4) [dx, dz, d] = [1, 0, 1];
        const overlap = APART - Math.hypot(two.x - one.x, two.z - one.z);
        const share = one.held > 0 === two.held > 0 ? 0.5 : one.held > 0 ? 0 : 1; // of it that `one` takes
        one.x -= (dx / d) * overlap * share;
        one.z -= (dz / d) * overlap * share;
        two.x += (dx / d) * overlap * (1 - share);
        two.z += (dz / d) * overlap * (1 - share);
      }
    for (const officer of standing) {
      for (const body of solid) {
        const dx = officer.x - body.x;
        const dz = officer.z - body.z;
        const d = Math.hypot(dx, dz);
        if (d >= APART) continue;
        if (d < 1e-4) officer.x += APART;
        else {
          officer.x = body.x + (dx / d) * APART;
          officer.z = body.z + (dz / d) * APART;
        }
      }
      if (!officer.lift) pushOut(officer, boxes);
    }
  }
  for (const officer of standing) officer.settle();
}

// Push someone out of any footprint they are inside, by the nearest side.
export function pushOut(at, boxes) {
  for (const f of boxes) {
    const [along, across] = local(f, at.x, at.z);
    const spareL = f.halfL + BODY - Math.abs(along);
    const spareW = f.halfW + BODY - Math.abs(across);
    if (spareL <= 0 || spareW <= 0) continue;
    Object.assign(at, spareL < spareW
      ? world(f, Math.sign(along || 1) * (f.halfL + BODY), across)
      : world(f, along, Math.sign(across || 1) * (f.halfW + BODY)));
  }
}

// The same mix of people as in the crowd, built once each.
const faces = new Map(); // variant -> geometry
const faceOf = (variant) => {
  if (!faces.has(variant)) faces.set(variant, headGeometry(personOf(variant)));
  return faces.get(variant);
};
let recruits = 0;

// look: { face: geometry, skin, top, bottom, cap }.
function createFigure(scene, look) {
  const group = new THREE.Group();
  const navy = material(look.top);
  const dark = material(look.bottom);
  const skin = material(look.skin);
  const add = (geometry, mat) => {
    const mesh = new THREE.Mesh(geometry, mat);
    group.add(mesh);
    return mesh;
  };
  const hanging = ({ w, h }) => new THREE.BoxGeometry(w, h, w).translate(0, -h / 2, 0);
  const figure = {
    group,
    torso: add(new THREE.BoxGeometry(TORSO.w, TORSO.h, TORSO.d), navy),
    head: add(look.face, headMaterial()),
    thighs: [0, 1].map(() => add(hanging({ w: LEG.w, h: THIGH }), dark)),
    shins: [0, 1].map(() => add(hanging({ w: LEG.w * 0.9, h: THIGH }), dark)),
    arms: [0, 1].map(() => add(hanging(ARM), navy)),
    forearms: [0, 1].map(() => add(hanging(FOREARM), skin)),
  };
  if (look.cap) {
    // A cap, which stays on the head.
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.155, 0.09, 12), dark);
    cap.position.y = HEAD_SIZE.h - 0.02;
    figure.head.add(cap);
  }
  scene.add(group);
  return figure;
}

// as: for someone who is not a police officer, { look, solid: false }.
export function createOfficer(scene, physics, spot, as = {}) {
  const variant = variantOf(recruits++);
  const figure = createFigure(scene, as.look
    ?? { face: faceOf(variant), skin: personOf(variant).skin, top: 0x16245c, bottom: 0x101010, cap: true });
  const target = physics.createCollider(RAPIER.ColliderDesc.capsule(0.55, 0.35)
    .setTranslation(spot.x, 0.9, spot.z).setSensor(true).setCollisionGroups((GROUP << 16) | 0xffff));
  if (as.solid === false) target.setEnabled(false); // bullets pass by
  let doll = null;
  let down = 0;
  let stride = 0;
  const swing = new THREE.Quaternion();
  const bend = new THREE.Quaternion();
  const turn = new THREE.Quaternion();
  const drop = new THREE.Vector3();
  const up = new THREE.Vector3(0, 1, 0);
  let gun = null;
  const side = new THREE.Vector3(1, 0, 0);

  const officer = {
    variant, // which sort of person, as numbered in head.js
    figure: figure.group,
    x: spot.x,
    z: spot.z,
    altitude: spot.altitude ?? 0, // off the ground, for one falling out of a car
    yaw: spot.yaw ?? 0,
    hp: OFFICER.hp,
    moving: 0,
    speed: OFFICER.speed,
    lift: 0,         // height, while climbing over something
    reaching: false, // arms out, making an arrest
    weapon: null,    // name of the gun in their right hand
    aiming: false,
    cooldown: 0,     // seconds until they can fire
    held: 0,         // seconds of arrest so far
    pulling: 0,      // seconds spent getting the player out of their vehicle
    get down() { return Boolean(doll); },
    get dead() { return officer.hp <= 0; },
    owns: (collider) => collider?.handle === target.handle
      || Boolean(doll && [doll.torso, ...doll.arms, ...doll.forearms, ...doll.legs, ...doll.shins]
        .some((body) => body.handle === collider?.parent()?.handle)),
    // Walk towards a point, going round vehicles, or over them if there is no way.
    // walls: footprints that are gone round, never over or through.
    walk(goal, boxes, dt, walls = []) {
      const way = around(officer, goal, walls.length ? [...boxes, ...walls] : boxes);
      const over = !way || officer.lift > 0 && boxes.some((f) => contains(f, officer, BODY - 0.05));
      // Over a car, but still round any wall.
      const next = over ? (walls.length && around(officer, goal, walls)) || goal : way;
      const dx = next.x - officer.x;
      const dz = next.z - officer.z;
      const length = Math.hypot(dx, dz);
      const step = Math.min(length, officer.speed * dt);
      if (length > 1e-6) {
        officer.x += (dx / length) * step;
        officer.z += (dz / length) * step;
        officer.yaw = Math.atan2(dx, dz);
      }
      officer.moving = step / dt;
      stride += step * 3;
      if (!over) pushOut(officer, boxes);
      pushOut(officer, walls);
      officer.lift = over && boxes.some((f) => contains(f, officer, 0)) ? CLIMB : over ? 0.01 : 0;
      target.setTranslation({ x: officer.x, y: 0.9, z: officer.z });
    },
    // Draw a gun, or put it away (null).
    equip(name) {
      if (name === officer.weapon) return;
      officer.weapon = name;
      gun?.removeFromParent();
      gun = null;
      if (!name) return;
      gun = createGun(name);
      gun.position.set(0, -FOREARM.h, 0);
      gun.rotation.x = Math.PI / 2; // the barrel continues the line of the forearm
      figure.forearms[0].add(gun);
    },
    // Bring what bullets hit up to date after being moved.
    settle() {
      target.setTranslation({ x: officer.x, y: 0.9, z: officer.z });
    },
    face(point) {
      officer.moving = 0;
      officer.yaw = Math.atan2(point.x - officer.x, point.z - officer.z);
    },
    knock(vx, vz) {
      if (doll) return;
      const speed = Math.hypot(vx, vz);
      const scale = Math.min(1.1, 30 / (speed || 1));
      doll = createRagdoll(physics, officer,
        { x: vx * scale, y: Math.min(2 + speed * 0.15, 7), z: vz * scale });
      down = 0;
      officer.held = 0;
      officer.pulling = 0;
      officer.reaching = false;
      officer.aiming = false;
      target.setEnabled(false);
    },
    hurt(amount, vx, vz) {
      officer.hp = Math.max(0, officer.hp - amount);
      if (!officer.hp) officer.knock(vx, vz);
      return officer.hp;
    },
    // Time on the ground. Anyone alive gets up after a while.
    step(dt) {
      if (!doll) return;
      down += dt;
      const p = doll.torso.translation();
      officer.x = p.x;
      officer.z = p.z;
      if (down < DOWN || officer.dead) return;
      doll.dispose();
      doll = null;
      target.setTranslation({ x: officer.x, y: 0.9, z: officer.z });
      target.setEnabled(true);
    },
    draw() {
      const { torso, head, thighs, shins, arms, forearms } = figure;
      if (doll) {
        const copy = (mesh, body) => {
          mesh.position.copy(body.translation());
          mesh.quaternion.copy(body.rotation());
        };
        copy(torso, doll.torso);
        copy(head, doll.head);
        for (const k of [0, 1]) {
          copy(thighs[k], doll.legs[k]);
          copy(shins[k], doll.shins[k]);
          copy(arms[k], doll.arms[k]);
          copy(forearms[k], doll.forearms[k]);
        }
        return;
      }
      turn.setFromAxisAngle(up, officer.yaw);
      const place = (mesh, across, y, q) => {
        mesh.position.set(officer.x + Math.cos(officer.yaw) * across, y + officer.lift,
          officer.z - Math.sin(officer.yaw) * across);
        mesh.quaternion.copy(q);
      };
      place(torso, 0, TORSO.y, turn);
      place(head, 0, HEAD.y, turn);
      const angle = officer.moving ? Math.sin(stride) * 0.6 : 0;
      for (const [k, sign] of [[0, -1], [1, 1]]) {
        place(thighs[k], sign * LEG.x, LEG.y, swing.setFromAxisAngle(side, angle * sign).premultiply(turn));
        drop.set(0, -THIGH, 0).applyQuaternion(swing);
        shins[k].position.copy(thighs[k].position).add(drop);
        shins[k].quaternion.copy(swing)
          .multiply(bend.setFromAxisAngle(side, kneeBend(stride, sign, officer.moving ? 1 : 0)));
        const pointing = officer.aiming && k === 0; // arms[0] is the right
        const raise = pointing ? -Math.PI / 2 : officer.reaching ? -1.4 : -angle * sign;
        place(arms[k], sign * ARM.x, ARM.y, swing.setFromAxisAngle(side, raise).premultiply(turn));
        drop.set(0, -ARM.h, 0).applyQuaternion(swing);
        swing.multiply(bend.setFromAxisAngle(side, pointing ? 0 : officer.reaching ? -0.2 : elbowBend(raise)));
        forearms[k].position.copy(arms[k].position).add(drop);
        forearms[k].quaternion.copy(swing);
      }
    },
    dispose() {
      doll?.dispose();
      physics.removeCollider(target, false);
      scene.remove(figure.group);
    },
  };
  return officer;
}
