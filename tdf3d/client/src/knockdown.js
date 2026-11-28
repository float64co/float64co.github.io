// Fast vehicles knock the player over when on foot.
export const FAST = 5;         // m/s (11 mph): slower than this they only shove
const PER_SPEED = 5;           // HP lost for each m/s above SAFE
const SAFE = 4;

export const damageAt = (speed) => Math.max(0, (speed - SAFE) * PER_SPEED);

// strikers: footprints with velocities. Knocks the walker over if one is driving
// into them. Tells the walker what is close, so they don't get up inside it.
// Returns the damage done.
export function runDown(walker, strikers, overlaps, dt) {
  const p = walker.body.translation();
  const box = { x: p.x, z: p.z, hx: 0, hz: 1, halfL: 0.35, halfW: 0.35 };
  const near = strikers.filter((s) => !(s.y > 2.5) && Math.abs(s.x - p.x) < 12 && Math.abs(s.z - p.z) < 12);
  walker.clearOf = near;
  if (walker.down || p.y > 2.5) return 0;
  const by = near.find((s) => Math.hypot(s.vx, s.vz) > FAST
    && s.vx * (p.x - s.x) + s.vz * (p.z - s.z) > 0 // coming towards, not leaving
    && (overlaps(s, box, 0.15)
      || overlaps({ ...s, x: s.x + s.vx * dt * 2, z: s.z + s.vz * dt * 2 }, box, 0.15)));
  if (!by) return 0;
  walker.knock(by.vx, by.vz);
  return damageAt(Math.hypot(by.vx, by.vz));
}
