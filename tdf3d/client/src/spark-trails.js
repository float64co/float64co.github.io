// The blue sparks under the wheels of every flying car: the player's, police and
// traffic alike. Each spark's place is worked out from the time, so there is
// nothing to keep track of and any number of cars can have them.
import * as THREE from 'three';

export const TRAIL = {
  each: 7,      // sparks to a wheel
  life: 0.25,   // seconds for one to travel the length of its trail
  drop: 0.7,    // metres it falls
  back: 1.8,    // metres it streams behind, once moving
  full: 8,      // m/s at which it streams that far
  size: 0.08,
};

// 0 to 1, the same for the same numbers.
const hash = (a, b) => {
  const n = Math.sin(a * 127.1 + b * 311.7) * 43758.5453;
  return n - Math.floor(n);
};

// Where the sparks of one wheel are. wheel: { x, y, z, vx, vz }. n tells wheels apart.
export function sparksOf(wheel, time, n, visit) {
  const speed = Math.hypot(wheel.vx, wheel.vz);
  // The same length for everyone, however fast they are going.
  const stream = speed > 0 ? (TRAIL.back * Math.min(1, speed / TRAIL.full)) / speed : 0;
  for (let k = 0; k < TRAIL.each; k++) {
    const turns = time / TRAIL.life + k / TRAIL.each + hash(n, 1);
    const age = turns % 1;
    const which = Math.floor(turns) * TRAIL.each + k; // a new spark each time round
    const spread = 0.35 * age;
    visit(
      wheel.x - wheel.vx * stream * age + (hash(which, n + 2) - 0.5) * spread,
      wheel.y - 0.05 - TRAIL.drop * age,
      wheel.z - wheel.vz * stream * age + (hash(which, n + 3) - 0.5) * spread,
      TRAIL.size * (1 - 0.75 * age),
    );
  }
}

export function createSparkTrails(scene, most = 4000) {
  const material = new THREE.MeshBasicMaterial({
    color: 0x3aa8ff, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const mesh = new THREE.InstancedMesh(new THREE.OctahedronGeometry(1, 0), material, most * TRAIL.each);
  mesh.frustumCulled = false;
  mesh.count = 0;
  scene.add(mesh);
  const matrix = new THREE.Matrix4();

  return {
    mesh,
    // wheels: every wheel in flight just now.
    draw(time, wheels) {
      let count = 0;
      wheels.slice(0, most).forEach((wheel, n) => sparksOf(wheel, time, n, (x, y, z, size) => {
        mesh.setMatrixAt(count++, matrix.makeScale(size, size, size).setPosition(x, y, z));
      }));
      mesh.count = count;
      mesh.instanceMatrix.clearUpdateRanges();
      mesh.instanceMatrix.addUpdateRange(0, Math.max(1, count) * 16);
      mesh.instanceMatrix.needsUpdate = true;
    },
  };
}
