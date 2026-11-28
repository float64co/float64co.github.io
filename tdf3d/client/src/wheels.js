// Wheel spin and steering for a car on rails, worked out from how it has moved.
export const RADIUS = 0.4;
const WHEELBASE = 2.8;
const LOCK = 0.6; // radians

// state: {} on first use. Positive steer is left, like the player's car.
export function rollWheels(state, x, z, yaw) {
  if (state.x === undefined) Object.assign(state, { x, z, yaw, spin: 0, steer: 0 });
  const distance = (x - state.x) * Math.sin(yaw) + (z - state.z) * Math.cos(yaw);
  // A big jump is a teleport, not driving. A tiny one says nothing about steering.
  if (Math.abs(distance) < 5) {
    // Keep the old reference point until there is enough movement to measure.
    if (Math.abs(distance) <= 0.005) return state;
    state.spin = (state.spin + distance / RADIUS) % (Math.PI * 2);
    const turned = Math.atan2(Math.sin(yaw - state.yaw), Math.cos(yaw - state.yaw));
    state.steer = Math.max(-LOCK, Math.min(LOCK, Math.atan(WHEELBASE * turned / distance)));
  }
  Object.assign(state, { x, z, yaw });
  return state;
}
