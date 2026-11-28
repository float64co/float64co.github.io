// Smoke and flames from damaged cars: a pool of drifting, growing puffs.
import * as THREE from 'three';
import { rng } from './city.js';

const MOST = 500;

export function createSmoke(scene) {
  const random = rng(99);
  const puff = new THREE.IcosahedronGeometry(1, 0);
  const kinds = {
    smoke: { material: new THREE.MeshBasicMaterial({ color: 0x55565a, transparent: true, opacity: 0.45, depthWrite: false }), life: 2.2, rise: 2, size: [0.2, 0.9] },
    flame: { material: new THREE.MeshBasicMaterial({ color: 0xff7a18, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false }), life: 0.55, rise: 2.6, size: [0.45, 0.1] },
  };
  for (const kind of Object.values(kinds)) {
    kind.mesh = new THREE.InstancedMesh(puff, kind.material, MOST);
    kind.mesh.frustumCulled = false;
    kind.mesh.count = 0;
    scene.add(kind.mesh);
    kind.puffs = [];
    kind.owed = 0;
  }
  const matrix = new THREE.Matrix4();

  function emit(kind, at, rate, dt) {
    for (let due = rate * dt + random(); due >= 1 && kind.puffs.length < MOST; due--) {
      kind.puffs.push({
        x: at.x + (random() - 0.5) * (kind.spread ?? 0.8), y: at.y, z: at.z + (random() - 0.5) * (kind.spread ?? 0.8),
        drift: [(random() - 0.5) * (kind.scatter ?? 0.6), (random() - 0.5) * (kind.scatter ?? 0.6)], age: 0,
      });
    }
  }

  return {
    kinds,
    // sources: [{ x, y, z, smoke, flame }], the last two in puffs a second.
    update(dt, sources) {
      for (const source of sources) {
        if (source.smoke) emit(kinds.smoke, source, source.smoke, dt);
        if (source.flame) emit(kinds.flame, source, source.flame, dt);
      }
      for (const { mesh, puffs, life, rise, size } of Object.values(kinds)) {
        let n = 0;
        for (let i = puffs.length - 1; i >= 0; i--) {
          const p = puffs[i];
          p.age += dt;
          if (p.age >= life) {
            puffs.splice(i, 1);
            continue;
          }
          p.x += p.drift[0] * dt;
          p.y += rise * dt;
          p.z += p.drift[1] * dt;
          const s = size[0] + (size[1] - size[0]) * (p.age / life);
          mesh.setMatrixAt(n++, matrix.makeScale(s, s, s).setPosition(p.x, p.y, p.z));
        }
        mesh.count = n;
        mesh.instanceMatrix.needsUpdate = true;
      }
    },
  };
}
