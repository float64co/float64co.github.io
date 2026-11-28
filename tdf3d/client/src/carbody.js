// A car's shell and cabin, finely divided so that they can be crumpled.
import * as THREE from 'three';

const CHARRED = 0x1c1a18;

// How much each place gives: broad folds, with a little grain. The same for any
// vertices that share a place, so that edges don't split.
const rough = (x, y, z, seed) => {
  const fold = Math.sin(x * 4.3 + seed * 1.7) * Math.sin(y * 6.1 + seed * 2.3) + Math.sin(z * 5.2 + seed * 3.1);
  const n = Math.sin(x * 127.1 + y * 311.7 + z * 74.7 + seed * 19.19) * 43758.5453;
  return 0.95 + 0.18 * fold + 0.2 * (n - Math.floor(n) - 0.5);
};

export function createCarBody({ body = 0xd62828, cabin } = {}) {
  const group = new THREE.Group(); // origin at the middle of the lower shell
  const paint = (colour) => new THREE.MeshLambertMaterial({ color: colour, flatShading: true });
  const lower = paint(body);
  const upper = cabin === undefined ? lower : paint(cabin);
  const parts = [
    [new THREE.BoxGeometry(2, 0.8, 4, 8, 3, 16), lower, [0, 0, 0]],
    [new THREE.BoxGeometry(1.7, 0.7, 2, 7, 3, 8), upper, [0, 0.75, -0.3]],
  ].map(([geometry, material, [x, y, z]]) => {
    geometry.translate(x, y, z);
    group.add(new THREE.Mesh(geometry, material));
    return { geometry, rest: geometry.attributes.position.array.slice() };
  });
  let version = 0;

  function crumple(dents) {
    for (const { geometry, rest } of parts) {
      const at = geometry.attributes.position.array;
      for (let n = 0; n < at.length; n += 3) {
        let x = rest[n];
        let y = rest[n + 1];
        let z = rest[n + 2];
        for (const d of dents) {
          const away = Math.hypot(rest[n] - d.at.x, rest[n + 1] - d.at.y, rest[n + 2] - d.at.z);
          if (away >= d.radius) continue;
          const much = d.depth * (1 - (away / d.radius) ** 2) * rough(rest[n], rest[n + 1], rest[n + 2], d.seed);
          x += d.push.x * much;
          y += d.push.y * much;
          z += d.push.z * much;
        }
        // However battered, no panel passes through the middle of the car.
        const hold = (value, from) => (from > 0.3 ? Math.max(0.2 * from, value)
          : from < -0.3 ? Math.min(0.2 * from, value) : value);
        at[n] = hold(x, rest[n]);
        at[n + 1] = Math.max(-0.45, y);
        at[n + 2] = hold(z, rest[n + 2]);
      }
      geometry.attributes.position.needsUpdate = true;
      geometry.computeVertexNormals();
      geometry.computeBoundingSphere();
    }
  }

  return {
    group,
    // Bring the shape and colour up to date with a car's condition.
    update(condition) {
      if (condition.version === version) return;
      version = condition.version;
      crumple(condition.dents);
      if (condition.burnt) for (const material of [lower, upper]) material.color.set(CHARRED);
    },
    dispose() {
      group.removeFromParent();
      for (const { geometry } of parts) geometry.dispose();
    },
  };
}
