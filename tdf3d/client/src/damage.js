// What has happened to a car: its health, its dents and whether it is on fire.
// Plain data, so that it can follow a car from one form to another.
import * as THREE from 'three';

export const CAR_HP = 100;
export const SMOKING = 40;      // HP below which the engine smokes
export const BURN_TIME = 12;    // seconds of flames before it is burnt out
export const BURN_HURT = 6;     // HP a second to anyone sitting in it
const KNOCK = 1.2;              // m/s of sudden change in one step: more is a collision
const PER_SPEED = 2.5;          // HP for each m/s beyond that: hard crashes add up
const SHOT = 0.4;               // share of a bullet's damage that a car takes
const MOST_DENTS = 40;
const SAFE_FALL = 9;            // m/s down: about a 4 m drop, which the springs take
const PER_FALL = 3;             // HP for each m/s faster than that
const STOPPED = -2;             // m/s: no longer falling to speak of
const HALF = { x: 1, y: 0.4, z: 2 }; // the car's body

export function createCondition() {
  return { hp: CAR_HP, dents: [], version: 0, burning: 0, burnt: false };
}
export const onFire = (c) => c.hp <= 0 && !c.burnt;

function dent(condition, at, push, depth, radius) {
  const dents = condition.dents;
  const dentSeed = condition.version + 1;
  dents.push({ at, push, depth, radius, seed: dentSeed });
  if (dents.length > MOST_DENTS) dents.shift();
  condition.version++;
}
function hurt(condition, amount) {
  condition.hp = Math.max(0, condition.hp - amount);
}

const turn = new THREE.Quaternion();
const local = new THREE.Vector3();

// The car's velocity changed by `change` (world axes) in one step. If that is too
// sudden to be driving, it hit something on the side the change came from.
export function collide(condition, rotation, change) {
  const hard = Math.hypot(change.x, change.z) - KNOCK;
  if (hard <= 0) return 0;
  // Which way the car was pushed, in its own frame.
  local.set(change.x, 0, change.z).normalize().applyQuaternion(turn.copy(rotation).invert());
  const reach = 1 / Math.max(Math.abs(local.x) / HALF.x, Math.abs(local.z) / HALF.z);
  const at = { x: -local.x * reach, y: 0.1, z: -local.z * reach };
  dent(condition, at, { x: local.x, y: -0.15, z: local.z },
    Math.min(0.55, 0.08 + hard * 0.05), Math.min(1.9, 0.9 + hard * 0.12));
  const amount = hard * PER_SPEED;
  hurt(condition, amount);
  return amount;
}

// Coming to a stop after dropping at `speed` m/s. Whichever side is underneath
// takes it, front and back.
export function land(condition, rotation, speed) {
  const hard = speed - SAFE_FALL;
  if (hard <= 0) return 0;
  const down = new THREE.Vector3(0, -1, 0).applyQuaternion(turn.copy(rotation).invert());
  const reach = 1 / Math.max(Math.abs(down.x) / HALF.x, Math.abs(down.y) / HALF.y, Math.abs(down.z) / HALF.z);
  for (const end of [1.5, -1.5]) {
    dent(condition, { x: down.x * reach, y: down.y * reach, z: down.z * reach + end },
      { x: -down.x, y: -down.y, z: -down.z },
      Math.min(0.45, 0.1 + hard * 0.015), Math.min(1.8, 1 + hard * 0.04));
  }
  const amount = hard * PER_FALL;
  hurt(condition, amount);
  return amount;
}

// A bullet striking at `point` travelling along `dir` (both in the car's frame).
export function shot(condition, point, dir, damage) {
  const keep = (v, half) => Math.max(-half, Math.min(half, v));
  dent(condition, { x: keep(point.x, HALF.x), y: point.y, z: keep(point.z, HALF.z) },
    { x: dir.x, y: dir.y, z: dir.z }, 0.07, 0.35);
  hurt(condition, damage * SHOT);
}

// Watch a physics car for collisions. Call once per step.
export function watch(vehicle) {
  const v = vehicle.body.linvel();
  const before = vehicle.lastVelocity;
  vehicle.lastVelocity = { x: v.x, z: v.z };
  // The fastest it has fallen since it was last at rest. Flying it down is not falling.
  if (vehicle.flying) vehicle.fastestFall = 0;
  else if (v.y > STOPPED) {
    if (vehicle.fastestFall) land(vehicle.condition, vehicle.body.rotation(), vehicle.fastestFall);
    vehicle.fastestFall = 0;
  } else vehicle.fastestFall = Math.max(vehicle.fastestFall ?? 0, -v.y);
  if (before) collide(vehicle.condition, vehicle.body.rotation(), { x: v.x - before.x, z: v.z - before.z });
}

// Let a fire run its course. Call once per step.
export function burn(condition, dt) {
  if (!onFire(condition)) return;
  condition.burning += dt;
  if (condition.burning >= BURN_TIME) {
    condition.burnt = true;
    condition.version++;
  }
}
