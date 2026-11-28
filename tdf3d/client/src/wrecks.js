// NPC cars follow rails until something is about to hit them. They then become
// real cars (same mass, suspension and tyres as the player's) so that Rapier
// works out the momentum, spin and skid. Afterwards the driver tries to get
// back to the lane it was using, and rejoins the traffic from there.
import { createGroundVehicle } from './ground.js';
import { steerBack, laneState, canRejoin } from './recovery.js';

export const MAX_WRECKS = 20;
const HALF_LENGTH = 2;
const HALF_WIDTH = 1;
const REACTION = 0.7;  // seconds before the NPC driver gets on the brakes
const BRAKE = 15;
const COMPOSE = 2.5;   // seconds after a hit before the driver sets off again
const SHUNT = 6;       // m/s: faster than a driver manoeuvres
const JOLT = 1.5;      // m/s change within one step: it has been hit again
const GIVE_UP = 60;    // seconds of trying before the driver stays put
const KEEP = 150;      // at least this far from the player before a wreck is cleared
const UNSEEN = 60;     // a car that gave up is cleared sooner
const NOWHERE = { add() {}, remove() {} }; // wrecks are drawn with the other NPC cars

// Do two rectangles on the ground overlap? Separating axis test.
export function overlaps(a, b, margin = 0) {
  const dx = b.x - a.x;
  const dz = b.z - a.z;
  const extent = (box, x, z) => box.halfL * Math.abs(box.hx * x + box.hz * z)
    + box.halfW * Math.abs(box.hx * z - box.hz * x);
  return [[a.hx, a.hz], [-a.hz, a.hx], [b.hx, b.hz], [-b.hz, b.hx]]
    .every(([x, z]) => Math.abs(dx * x + dz * z) <= extent(a, x, z) + extent(b, x, z) + margin);
}

export function createWrecks(physics, sim) {
  const list = []; // { car, route, shade, age, trying, memory, recovering }
  // Cars back in traffic that still carry damage: sim car id -> condition.
  const scarred = new Map();

  function remove(index) {
    list[index].car.dispose();
    list.splice(index, 1);
  }

  // A rectangle describing a car's footprint and motion.
  function footprint(body, halfL = HALF_LENGTH, halfW = HALF_WIDTH) {
    const p = body.translation();
    const q = body.rotation();
    const v = body.linvel();
    const hx = 2 * (q.x * q.z + q.w * q.y);
    const hz = 1 - 2 * (q.x * q.x + q.y * q.y);
    const flat = Math.hypot(hx, hz) || 1;
    return { x: p.x, y: p.y, z: p.z, hx: hx / flat, hz: hz / flat, halfL, halfW, vx: v.x, vz: v.z };
  }

  // What the driver was doing: mid-turn counts as already heading the new way.
  function routeOf(npc) {
    const { i, j, dir, next, turn } = npc;
    const cruise = npc.cruise || 10;
    if (!npc.path) return { i, j, dir, next, turn, cruise };
    // The corner junction fixes the new road; a fresh turn is planned on rejoining.
    return { i: i + dir[0], j: j + dir[1], dir: next, next, turn, cruise, replan: true };
  }

  function wreck(npc, dt) {
    const damage = scarred.get(npc.id);
    scarred.delete(npc.id);
    sim.takeCar(npc);
    if (list.length >= MAX_WRECKS) remove(0);
    const car = createGroundVehicle('car', NOWHERE, physics,
      { x: npc.x, y: 0.8, z: npc.z, yaw: Math.atan2(npc.hx, npc.hz) });
    car.body.setLinvel({ x: npc.hx * npc.speed, y: 0, z: npc.hz * npc.speed }, true);
    car.tune.brake = BRAKE;
    if (damage) car.condition = damage;
    car.drive({ throttle: 0, steer: 0, brake: false }, dt);
    list.push({ car, route: routeOf(npc), shade: npc.shade, age: 0, trying: 0, memory: {}, recovering: false });
  }

  // One step of a crashed car's life. Returns true once it is back in traffic.
  function drive(entry, others, dt) {
    const { car } = entry;
    // Knocked out of the sky: it has no lane to go back to.
    if (!entry.route) {
      car.drive({ throttle: 0, steer: 0, brake: true }, dt);
      return false;
    }
    const f = footprint(car.body);
    const speed = Math.hypot(f.vx, f.vz);
    const upright = 1 - 2 * (car.body.rotation().x ** 2 + car.body.rotation().z ** 2) > 0.8;
    entry.age += dt;
    // Burning or burnt: nobody is driving it anywhere.
    if (car.condition.hp <= 0) {
      entry.recovering = false;
      car.drive({ throttle: 0, steer: 0, brake: true }, dt);
      return false;
    }
    const jolt = entry.velocity ? Math.hypot(f.vx - entry.velocity.x, f.vz - entry.velocity.z) : 0;
    entry.velocity = { x: f.vx, z: f.vz };
    if (jolt > JOLT || (entry.recovering && speed > SHUNT))
      Object.assign(entry, { age: 0, trying: 0, memory: {}, recovering: false });
    if (!entry.recovering)
      entry.recovering = upright && entry.age > COMPOSE && speed < 0.5 && entry.trying < GIVE_UP;
    if (!entry.recovering || !upright || entry.trying >= GIVE_UP) {
      entry.recovering = false;
      car.drive({ throttle: 0, steer: 0, brake: entry.age > REACTION }, dt);
      return false;
    }

    entry.trying += dt;
    const { route } = entry;
    const pose = { x: f.x, z: f.z, hx: f.hx, hz: f.hz, speed: f.vx * f.hx + f.vz * f.hz };
    const state = laneState(route, pose);
    // Rejoin only where nothing of the player's is close enough to be touching.
    if (canRejoin(state) && pose.speed > -0.5 && !others.some((o) => o.own && Math.hypot(o.x - f.x, o.z - f.z) < o.r + 4)
      && sim.returnCar(route, state, Math.max(0, pose.speed), entry.shade)) {
      if (car.condition.version) scarred.set(sim.cars.at(-1).id, car.condition);
      return true;
    }
    car.drive(steerBack(route, pose, others, entry.memory, dt), dt);
    return false;
  }

  return {
    list,
    footprint,
    scarred,
    // A car out of the sky: { x, y, z, hx, hz, speed, shade }. It just falls.
    addFalling({ x, y, z, hx, hz, speed, shade }) {
      if (list.length >= MAX_WRECKS) remove(0);
      const car = createGroundVehicle('car', NOWHERE, physics, { x, y, z, yaw: Math.atan2(hx, hz) });
      car.body.setLinvel({ x: hx * speed, y: 0, z: hz * speed }, true);
      car.tune.brake = BRAKE;
      car.drive({ throttle: 0, steer: 0, brake: true }, 1 / 60);
      const entry = { car, route: null, shade, age: 0, trying: 0, memory: {}, recovering: false };
      list.push(entry);
      return entry;
    },
    // Turn a car on rails into a crashed one now.
    wreck: (npc, dt) => wreck(npc, dt),
    // strikers: footprints of the player's vehicles that can crash into things.
    // people: [{ x, z, r }] that a recovering driver must not run into.
    // Call before physics.step(), so the first contact is already a fair one.
    update(strikers, focus, dt, people = []) {
      for (let n = list.length - 1; n >= 0; n--) {
        const entry = list[n];
        const p = entry.car.body.translation();
        const away = Math.hypot(p.x - focus.x, p.z - focus.z);
        if (away > Math.max(KEEP, sim.radius / 2) || p.y < -10 || (away > UNSEEN && entry.trying >= GIVE_UP)) {
          remove(n);
          continue;
        }
        const others = [
          ...people,
          ...strikers.map((s) => ({ x: s.x, z: s.z, r: s.halfL * 0.8, own: true })),
          ...sim.cars.filter((c) => Math.abs(c.x - p.x) < 15 && Math.abs(c.z - p.z) < 15)
            .map((c) => ({ x: c.x, z: c.z, r: 1.5 })),
          ...list.filter((o) => o !== entry).map((o) => ({ ...o.car.body.translation(), r: 1.5 })),
        ];
        if (drive(entry, others, dt)) remove(n);
      }
      sim.crashed = list.length;
      // Moving wrecks can set off the next car along.
      // A driver manoeuvring carefully does not count.
      const moving = list.map((entry) => [entry, footprint(entry.car.body)])
        .filter(([entry, f]) => Math.hypot(f.vx, f.vz) > (entry.recovering ? SHUNT : 0.5))
        .map(([, f]) => f);
      const hit = sim.cars.filter((npc) => {
        const box = { x: npc.x, z: npc.z, hx: npc.hx, hz: npc.hz, halfL: HALF_LENGTH, halfW: HALF_WIDTH };
        return [...strikers, ...moving].some((s) => {
          if (s.y > 2.5 || Math.abs(s.x - npc.x) > 12 || Math.abs(s.z - npc.z) > 12) return false;
          // Now, or where it will be two steps from now.
          return overlaps(s, box, 0.15)
            || overlaps({ ...s, x: s.x + s.vx * dt * 2, z: s.z + s.vz * dt * 2 }, box, 0.15);
        });
      });
      for (const npc of hit) wreck(npc, dt);
    },
    remove(entry) {
      const index = list.indexOf(entry);
      if (index >= 0) remove(index);
    },
    clearNear(position, radius = 10) {
      for (let n = list.length - 1; n >= 0; n--) {
        const p = list[n].car.body.translation();
        if (Math.hypot(p.x - position.x, p.z - position.z) < radius) remove(n);
      }
    },
    clear() {
      scarred.clear();
      while (list.length) remove(list.length - 1);
      sim.crashed = 0;
    },
  };
}
