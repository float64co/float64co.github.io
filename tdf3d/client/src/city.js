// Pure city generation: no three.js or Rapier, no Math.random.
export const SEED = 1337;
export const BLOCKS = 80;      // blocks per side
export const PITCH = 60;       // block + road, metres
export const ROAD = 14;
export const SIZE = BLOCKS * PITCH;

// mulberry32
export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), a | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Hospitals and police stations: nine of each, spread evenly. Each takes a
// whole block, with its building at the back and a forecourt facing -z.
const SITES = { hospital: [14, 41, 68], police: [11, 38, 65] };
export const SITE_COLOURS = { hospital: 0xf2f2f2, police: 0x44597a };
const BUILDINGS = { hospital: { w: 34, h: 16, d: 20 }, police: { w: 30, h: 11, d: 20 } };
const siteAt = (i, j) => Object.keys(SITES).find((kind) => SITES[kind].includes(i) && SITES[kind].includes(j));
const middle = (i) => (i + 0.5) * PITCH - SIZE / 2;

// The hospital or police station nearest to (x, z): where it is, where someone
// leaving it appears, and where a car waits for them.
export function nearestSite(kind, x, z) {
  const [cx, cz] = SITES[kind].flatMap((i) => SITES[kind].map((j) => [middle(i), middle(j)]))
    .sort((a, b) => Math.hypot(a[0] - x, a[1] - z) - Math.hypot(b[0] - x, b[1] - z))[0];
  return {
    x: cx,
    z: cz,
    door: { x: cx, y: 1, z: cz - 12, yaw: Math.PI },
    car: { x: cx + 9, y: 0.9, z: cz - 14, yaw: -Math.PI / 2 },
  };
}

// Each block has its own stream, so changing one never shifts the others.
export function block(i, j, seed = SEED) {
  const random = rng(seed ^ Math.imul(i + 1, 73856093) ^ Math.imul(j + 1, 19349663));
  const x = (i + 0.5) * PITCH - SIZE / 2;
  const z = (j + 0.5) * PITCH - SIZE / 2;
  const site = siteAt(i, j);
  const park = !site && random() < 0.08;
  const buildings = [];
  if (site) buildings.push({ x, z: z + 8, ...BUILDINGS[site], shade: 0.5, site });
  else if (!park) {
    const inner = PITCH - ROAD - 6;               // leave a pavement
    const n = random() < 0.35 ? 1 : 2;            // 1x1 or 2x2 lots
    const lot = inner / n;
    // Taller towards the centre.
    const downtown = Math.max(0, 1 - Math.hypot(x, z) / (SIZE / 2));
    for (let a = 0; a < n; a++) {
      for (let b = 0; b < n; b++) {
        buildings.push({
          x: x - inner / 2 + (a + 0.5) * lot,
          z: z - inner / 2 + (b + 0.5) * lot,
          w: lot * (0.65 + random() * 0.3),
          d: lot * (0.65 + random() * 0.3),
          h: 6 + random() * 14 + downtown ** 2 * random() * 80,
          shade: random(),
        });
      }
    }
  }
  return { x, z, park, site, buildings };
}

export function generateCity(seed = SEED) {
  const blocks = [];
  for (let i = 0; i < BLOCKS; i++)
    for (let j = 0; j < BLOCKS; j++) blocks.push(block(i, j, seed));
  return blocks;
}
