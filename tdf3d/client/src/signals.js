// Traffic lights, a pure function of time. axis 0 = east-west travel, 1 = north-south.
export const GREEN = 18; // includes the amber at the end
export const AMBER = 3;
export const CLEAR = 3;  // all red between phases
const HALF = GREEN + CLEAR;
const CYCLE = HALF * 2;

export function signal(i, j, axis, time) {
  // Neighbouring junctions run half a cycle apart.
  const shift = ((i + j) & 1) * HALF + axis * HALF;
  const t = (((time + shift) % CYCLE) + CYCLE) % CYCLE;
  if (t < GREEN - AMBER) return { colour: 'green', left: GREEN - t };
  if (t < GREEN) return { colour: 'amber', left: GREEN - t };
  return { colour: 'red', left: 0 };
}
