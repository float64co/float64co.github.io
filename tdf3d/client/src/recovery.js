// The driver of a crashed car: steer and reverse back onto the lane it was using.
// Pure functions over plain data, apart from reading the pose handed in.
import { BLOCKS, PITCH } from './city.js';
import { LANE, BOX } from './traffic-sim.js';

const STRAIGHT = PITCH - 2 * BOX;
const MAX = BLOCKS / 2 - 1;
const CRUISE = 4.5;     // m/s while manoeuvring
const LOOKAHEAD = 7;
const PATIENCE = 2.5;   // seconds blocked before backing off
const BACKING = 1.6;    // seconds spent reversing

// Where the car is relative to its old direction of travel.
export function laneState(route, pose) {
  const [dx, dz] = route.dir;
  const [sx, sz] = [dz, -dx]; // the driver's left, where the lane is
  // The road does not change: a car travelling along x keeps its j, along z its i.
  const centre = dx ? route.j * PITCH : route.i * PITCH;
  const line = centre + (dx ? sz : sx) * LANE;
  const along = pose.x * dx + pose.z * dz;
  const k = Math.max(-MAX, Math.min(MAX - 1, Math.floor(along / PITCH)));
  const junction = k * (dx || dz);
  return {
    i: dx ? junction : route.i,
    j: dx ? route.j : junction,
    s: along - k * PITCH - BOX,
    offset: (dx ? pose.z : pose.x) - line,
    aligned: pose.hx * dx + pose.hz * dz,
    target: dx
      ? { x: (along + LOOKAHEAD) * dx, z: line }
      : { x: line, z: (along + LOOKAHEAD) * dz },
  };
}

// Close enough to the lane to carry on as ordinary traffic?
export function canRejoin(state) {
  return Math.abs(state.offset) < 0.8 && state.aligned > 0.98 && state.s >= 0 && state.s <= STRAIGHT - 3;
}

// Is anything within `reach` of the front (way = 1) or back (way = -1) bumper?
function blocked(pose, way, reach, others) {
  return others.some((o) => {
    const dx = o.x - pose.x;
    const dz = o.z - pose.z;
    const ahead = (dx * pose.hx + dz * pose.hz) * way;
    const across = Math.abs(dz * pose.hx - dx * pose.hz);
    return ahead > 0 && ahead < 2 + o.r + reach && across < 1 + o.r * 0.6;
  });
}

// pose: { x, z, hx, hz, speed (signed, forwards positive) }; others: [{ x, z, r }].
// memory keeps the driver's state between steps. Returns inputs for the vehicle.
export function steerBack(route, pose, others, memory, dt) {
  const { target } = laneState(route, pose);
  const tx = target.x - pose.x;
  const tz = target.z - pose.z;
  // Positive angle: the target is to the left.
  const angle = Math.atan2(tx * pose.hz - tz * pose.hx, tx * pose.hx + tz * pose.hz);
  const steer = Math.max(-1, Math.min(1, angle * 1.8));
  const stop = { throttle: 0, steer, brake: true };

  if (memory.reversing > 0) {
    memory.reversing -= dt;
    if (blocked(pose, -1, 1.2, others)) return stop;
    // Opposite lock swings the nose towards the target while backing up.
    return { throttle: pose.speed > -3 ? -0.4 : 0, steer: -steer, brake: false };
  }

  const waiting = blocked(pose, 1, 1.5 + Math.max(0, pose.speed) * 0.6, others);
  const stalled = waiting || Math.abs(pose.speed) < 0.4;
  memory.stalled = stalled ? (memory.stalled ?? 0) + dt : 0;
  if (memory.stalled > PATIENCE) {
    memory.stalled = 0;
    memory.reversing = BACKING;
  }
  if (waiting) return stop;
  return { throttle: pose.speed < CRUISE ? 0.45 : 0, steer, brake: pose.speed > CRUISE + 2 };
}
