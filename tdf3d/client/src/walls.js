// The city's buildings as footprints, for people on foot to walk round.
import { block, PITCH, SIZE, BLOCKS } from './city.js';

const cache = new Map();
function blockWalls(i, j) {
  const key = i * BLOCKS + j;
  if (!cache.has(key)) {
    cache.set(key, block(i, j).buildings.map((b) => ({ x: b.x, z: b.z, hx: 0, hz: 1, halfL: b.d / 2, halfW: b.w / 2 })));
  }
  return cache.get(key);
}

// Those that might lie between two points, with `room` metres to either side.
export function wallsBetween(a, b, room = 8) {
  const cell = (v) => Math.max(0, Math.min(BLOCKS - 1, Math.floor((v + SIZE / 2) / PITCH)));
  const [x0, x1] = [Math.min(a.x, b.x) - room, Math.max(a.x, b.x) + room];
  const [z0, z1] = [Math.min(a.z, b.z) - room, Math.max(a.z, b.z) + room];
  const found = [];
  for (let i = cell(x0); i <= cell(x1); i++)
    for (let j = cell(z0); j <= cell(z1); j++)
      for (const w of blockWalls(i, j))
        if (w.x + w.halfW > x0 && w.x - w.halfW < x1 && w.z + w.halfL > z0 && w.z - w.halfL < z1) found.push(w);
  return found;
}
