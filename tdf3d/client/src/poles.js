// Physics for traffic lights and street lamps. A standing one is solid. Anything
// hitting it with enough momentum breaks it off, and it becomes a loose body
// that topples. Colliders exist only near things that could hit them.
import RAPIER from '@dimforge/rapier3d-compat';
import { PITCH, BLOCKS } from './city.js';
import { overlaps } from './wrecks.js';
import { POLE, LAMP } from './traffic-sim.js';

export const APPROACHES = [[1, 0], [-1, 0], [0, 1], [0, -1]];
const NEAR = 40;     // metres around vehicles and people that poles are solid
export const BREAK = 4500; // kg m/s: a car at 9 mph, a motorbike at 40, never a bicycle
const EDGE = BLOCKS / 2 - 1; // outermost junction traffic uses
const HEAD_HEIGHT = 1.8;

// Poles are thin, so the camera ignores them rather than jumping in front.
const GROUP = 0x0002;
export const CAMERA_GROUPS = (0xffff << 16) | (0xffff & ~GROUP);

// Boxes making up a traffic light, from the ground up: half sizes, place, mass.
export const PARTS = {
  base: { half: [0.25, 0.05, 0.25], y: 0.05, mass: 30 },
  shaft: { half: [0.075, 2, 0.075], y: 2, mass: 40 },
  head: { half: [0.225, 0.45, 0.225], y: 4.3, mass: 15 },
};
// And a street lamp, whose arm reaches out along +x, over the road.
export const LAMP_PARTS = {
  base: { half: [0.25, 0.05, 0.25], y: 0.05, mass: 30 },
  shaft: { half: [0.08, 3, 0.08], y: 3, mass: 55 },
  arm: { half: [0.7, 0.05, 0.05], x: 0.7, y: 5.95, mass: 6 },
  head: { half: [0.35, 0.1, 0.2], x: 1.4, y: 5.9, mass: 8 },
};
const KINDS = {
  light: { parts: PARTS, height: PARTS.head.y + PARTS.head.half[1] },
  lamp: { parts: LAMP_PARTS, height: 6 },
};

// Where the light for one approach to junction (i, j) stands:
// the near-side corner on the driver's left.
export function polePlace(i, j, [dx, dz]) {
  return { x: i * PITCH - (dx - dz) * POLE, z: j * PITCH - (dz + dx) * POLE };
}
export const poleKey = (i, j, k) => `${i},${j},${k}`;

// Four lamps belong to junction (i, j): two on the road towards +z and two on
// the road towards +x, one on each side. yaw turns the arm over the road.
export function lampPlace(i, j, n) {
  const far = n % 2;
  const across = far ? -LAMP.across : LAMP.across;
  const along = far ? PITCH - LAMP.along : LAMP.along;
  return n < 2
    ? { x: i * PITCH + across, z: j * PITCH + along, yaw: far ? 0 : Math.PI }
    : { x: i * PITCH + along, z: j * PITCH + across, yaw: far ? -Math.PI / 2 : Math.PI / 2 };
}
export const lampKey = (i, j, n) => `lamp ${i},${j},${n}`;
// Is there such a lamp? Roads stop at the outermost junctions.
export const hasLamp = (i, j, n) => Math.abs(i) <= EDGE && Math.abs(j) <= EDGE
  && (n < 2 ? j : i) < EDGE;

const turned = (yaw) => ({ x: 0, y: Math.sin(yaw / 2), z: 0, w: Math.cos(yaw / 2) });

export function createPoles(physics, changed = () => {}) {
  const standing = new Map(); // key -> { x, z, i, j, colliders }
  const down = new Map();     // key -> { x, z, body, position, rotation }

  const collider = ({ half, mass }) => RAPIER.ColliderDesc.cuboid(...half)
    .setMass(mass).setCollisionGroups((GROUP << 16) | 0xffff);
  const parts = (pole) => Object.values(KINDS[pole.kind].parts);

  function load(pole) {
    pole.colliders = parts(pole).map((p) => {
      const out = p.x ?? 0;
      return physics.createCollider(collider(p)
        .setTranslation(pole.x + Math.cos(pole.yaw) * out, p.y, pole.z - Math.sin(pole.yaw) * out)
        .setRotation(turned(pole.yaw)));
    });
  }
  function unload(pole) {
    for (const c of pole.colliders) physics.removeCollider(c, true);
  }

  // A loose pole, at its last pose.
  function release(entry) {
    entry.body = physics.createRigidBody(RAPIER.RigidBodyDesc.dynamic()
      .setTranslation(entry.position.x, entry.position.y, entry.position.z)
      .setRotation(entry.rotation));
    for (const p of parts(entry)) {
      physics.createCollider(collider(p).setTranslation(p.x ?? 0, p.y, 0), entry.body);
    }
  }
  function stow(entry) {
    physics.removeRigidBody(entry.body);
    entry.body = null;
  }

  // The stretch of a downed light that is low enough to meet a person, as a
  // line on the ground with the velocity at each end; null if all of it is higher.
  function segment({ kind, body, position, rotation }) {
    const { x, y, z, w } = rotation;
    // The pole's own up direction.
    const up = [2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)];
    const centre = body?.worldCom();
    const linear = body?.linvel();
    const spin = body?.angvel();
    const point = (along) => {
      const p = [position.x + up[0] * along, position.y + up[1] * along, position.z + up[2] * along];
      if (!body) return { p, v: [0, 0, 0] };
      const r = [p[0] - centre.x, p[1] - centre.y, p[2] - centre.z];
      return {
        p,
        v: [
          linear.x + spin.y * r[2] - spin.z * r[1],
          linear.y + spin.z * r[0] - spin.x * r[2],
          linear.z + spin.x * r[1] - spin.y * r[0],
        ],
      };
    };
    let a = point(0);
    let b = point(KINDS[kind].height);
    if (a.p[1] > b.p[1]) [a, b] = [b, a]; // a is the lower end
    if (a.p[1] > HEAD_HEIGHT) return null;
    if (b.p[1] > HEAD_HEIGHT) {
      const t = (HEAD_HEIGHT - a.p[1]) / (b.p[1] - a.p[1]);
      const mix = (from, to) => from.map((value, n) => value + (to[n] - value) * t);
      b = { p: mix(a.p, b.p), v: mix(a.v, b.v) };
    }
    return {
      ax: a.p[0], az: a.p[2], bx: b.p[0], bz: b.p[2],
      avx: a.v[0], avy: a.v[1], avz: a.v[2], bvx: b.v[0], bvy: b.v[1], bvz: b.v[2],
    };
  }

  return {
    standing,
    down,
    // For the pedestrians.
    segments: () => [...down.values()].map(segment).filter(Boolean),
    // positions: everything that needs solid poles around it.
    // lit: whether there are traffic lights at all just now (lamps always are).
    // vehicles: [{ footprint, momentum }] that might break one off.
    // Poles knocked down are stood up again once further than `forget` from focus.
    // Call before physics.step().
    update(positions, vehicles, focus, forget, dt, lit = true) {
      const wanted = new Map();
      const want = (key, p, place) => {
        if (Math.abs(place.x - p.x) < NEAR && Math.abs(place.z - p.z) < NEAR) wanted.set(key, place);
      };
      for (const p of positions) {
        const ci = Math.round(p.x / PITCH);
        const cj = Math.round(p.z / PITCH);
        for (let i = Math.max(-EDGE, ci - 1); i <= Math.min(EDGE, ci + 1); i++)
          for (let j = Math.max(-EDGE, cj - 1); j <= Math.min(EDGE, cj + 1); j++)
          {
            if (lit) APPROACHES.forEach((approach, k) => want(poleKey(i, j, k), p,
              { kind: 'light', yaw: 0, ...polePlace(i, j, approach) }));
            for (let n = 0; n < 4; n++) {
              if (hasLamp(i, j, n)) want(lampKey(i, j, n), p, { kind: 'lamp', ...lampPlace(i, j, n) });
            }
          }
      }

      for (const [key, pole] of standing) {
        if (wanted.has(key)) continue;
        unload(pole);
        standing.delete(key);
      }
      for (const [key, place] of wanted) {
        if (standing.has(key) || down.has(key)) continue;
        load(place);
        standing.set(key, place);
      }

      // Break off anything about to be hit hard, before the contact happens.
      const hard = vehicles.filter((v) => v.momentum > BREAK);
      if (hard.length) {
        for (const [key, pole] of standing) {
          const box = { x: pole.x, z: pole.z, hx: 0, hz: 1, halfL: 0.25, halfW: 0.25 };
          const hit = hard.some(({ footprint: f }) => {
            if (f.y > 5 || Math.abs(f.x - pole.x) > 12 || Math.abs(f.z - pole.z) > 12) return false;
            return overlaps(f, box, 0.15)
              || overlaps({ ...f, x: f.x + f.vx * dt * 2, z: f.z + f.vz * dt * 2 }, box, 0.15);
          });
          if (!hit) continue;
          unload(pole);
          standing.delete(key);
          const entry = {
            kind: pole.kind, x: pole.x, z: pole.z, body: null,
            position: { x: pole.x, y: 0, z: pole.z }, rotation: turned(pole.yaw),
          };
          release(entry);
          down.set(key, entry);
          changed(entry.x, entry.z, true);
        }
      }

      for (const [key, entry] of down) {
        if (entry.body) {
          entry.position = entry.body.translation();
          entry.rotation = entry.body.rotation();
        }
        const near = positions.some((p) => Math.abs(entry.position.x - p.x) < NEAR
          && Math.abs(entry.position.z - p.z) < NEAR);
        // Far from everything, it keeps its pose without being simulated.
        if (entry.body && !near) stow(entry);
        else if (!entry.body && near) release(entry);
        if (Math.hypot(entry.x - focus.x, entry.z - focus.z) > forget) {
          if (entry.body) stow(entry);
          down.delete(key);
          changed(entry.x, entry.z, false);
        }
      }
    },
  };
}
