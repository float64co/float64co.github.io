import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { PITCH, ROAD, SIZE, SITE_COLOURS } from './city.js';
import { addWindows } from './windows.js';

const CHUNK = 5; // blocks per side of one instanced chunk
const LAND = 60000; // ground reaches far past the city, for aircraft
// Blocks each side of a vehicle or player that are solid: at least 120 m.
const SOLID = 2;

// Building colliders exist only around the given positions.
function createStreamer(physics, blocks) {
  const index = (v) => Math.floor((v + SIZE / 2) / PITCH);
  const byCell = new Map(blocks.map((b) => [`${index(b.x)},${index(b.z)}`, b]));
  const loaded = new Map(); // cell -> colliders
  let last = '';

  return function update(positions) {
    const cells = positions.map((p) => [index(p.x), index(p.z)]);
    const signature = cells.join(';');
    if (signature === last) return; // nobody changed block
    last = signature;

    const wanted = new Set();
    for (const [i, j] of cells)
      for (let a = i - SOLID; a <= i + SOLID; a++)
        for (let b = j - SOLID; b <= j + SOLID; b++)
          if (byCell.has(`${a},${b}`)) wanted.add(`${a},${b}`);

    for (const [cell, colliders] of loaded) {
      if (wanted.has(cell)) continue;
      for (const collider of colliders) physics.removeCollider(collider, false);
      loaded.delete(cell);
    }
    for (const cell of wanted) {
      if (loaded.has(cell)) continue;
      loaded.set(cell, byCell.get(cell).buildings.map((b) => physics.createCollider(
        RAPIER.ColliderDesc.cuboid(b.w / 2, b.h / 2, b.d / 2).setTranslation(b.x, b.h / 2, b.z),
      )));
    }
  };
}

// What marks a building out, on its front and roof. Unlit, so it shows at night.
function sign({ x, z, w, h, d, site }) {
  const group = new THREE.Group();
  group.position.set(x, 0, z);
  const materials = new Map();
  const slab = (width, height, depth, colour, px, py, pz) => {
    if (!materials.has(colour)) materials.set(colour, new THREE.MeshBasicMaterial({ color: colour }));
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), materials.get(colour));
    mesh.position.set(px, py, pz);
    group.add(mesh);
  };
  const front = -d / 2 - 0.15;
  slab(6, 3.2, 0.2, 0x20242c, 0, 1.6, front); // the entrance
  if (site === 'hospital') {
    // A red cross over the door, and another seen from the air.
    slab(7, 2.2, 0.2, 0xe01020, 0, h - 5, front);
    slab(2.2, 7, 0.25, 0xe01020, 0, h - 5, front);
    slab(10, 0.2, 3, 0xe01020, 0, h + 0.1, 0);
    slab(3, 0.25, 10, 0xe01020, 0, h + 0.1, 0);
  } else {
    // A blue and white chequered band, and a blue lamp over the door.
    const squares = Math.floor(w / 1.5);
    for (let n = 0; n < squares; n++) {
      for (let row = 0; row < 2; row++) {
        slab(w / squares, 0.8, 0.2, (n + row) % 2 ? 0x1b4fd8 : 0xf4f4f4,
          -w / 2 + (n + 0.5) * (w / squares), 4.4 + row * 0.8, front);
      }
    }
    slab(1, 1.2, 1, 0x2f7bff, 0, 7.2, front);
  }
  return group;
}

export function buildWorld(scene, physics, blocks) {
  const ground = new THREE.Mesh(
    // Subdivided: two huge triangles lose depth precision and cover the pavements.
    new THREE.PlaneGeometry(LAND, LAND, 256, 256).rotateX(-Math.PI / 2).translate(0, -0.02, 0),
    new THREE.MeshLambertMaterial({
      color: 0x333338, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 4,
    }),
  );
  scene.add(ground);
  physics.createCollider(
    RAPIER.ColliderDesc.cuboid(LAND / 2, 1, LAND / 2).setTranslation(0, -1, 0),
  );

  // Low invisible walls keep ground vehicles in; aircraft fly over them.
  for (const [x, z, hx, hz] of [
    [0, SIZE / 2 + 1, SIZE / 2, 1], [0, -SIZE / 2 - 1, SIZE / 2, 1],
    [SIZE / 2 + 1, 0, 1, SIZE / 2], [-SIZE / 2 - 1, 0, 1, SIZE / 2],
  ]) {
    physics.createCollider(RAPIER.ColliderDesc.cuboid(hx, 10, hz).setTranslation(x, 10, z));
  }

  // Group by chunk so three.js can frustum-cull whole areas.
  const chunks = new Map();
  for (const b of blocks) {
    const key = `${Math.floor((b.x + SIZE / 2) / PITCH / CHUNK)},${Math.floor((b.z + SIZE / 2) / PITCH / CHUNK)}`;
    if (!chunks.has(key)) chunks.set(key, []);
    chunks.get(key).push(b);
  }

  const box = new THREE.BoxGeometry(1, 1, 1);
  const material = new THREE.MeshLambertMaterial();
  const walls = new THREE.MeshLambertMaterial();
  const setNight = addWindows(walls);
  const matrix = new THREE.Matrix4();
  const color = new THREE.Color();

  for (const list of chunks.values()) {
    // Pavements and parks are flat, visual only.
    const slabs = new THREE.InstancedMesh(box, material, list.length);
    list.forEach((b, n) => {
      matrix.makeScale(PITCH - ROAD, 0.1, PITCH - ROAD).setPosition(b.x, 0.05, b.z);
      slabs.setMatrixAt(n, matrix);
      slabs.setColorAt(n, color.set(b.park ? 0x4c8c4a : b.site ? 0xb8b6ae : 0x8a8a86));
    });
    scene.add(slabs);

    const buildings = list.flatMap((b) => b.buildings);
    if (!buildings.length) continue;
    const mesh = new THREE.InstancedMesh(box, walls, buildings.length);
    buildings.forEach((b, n) => {
      matrix.makeScale(b.w, b.h, b.d).setPosition(b.x, b.h / 2, b.z);
      mesh.setMatrixAt(n, matrix);
      if (b.site) mesh.setColorAt(n, color.set(SITE_COLOURS[b.site]));
      else mesh.setColorAt(n, color.setHSL(0.05 + b.shade * 0.1, 0.25, 0.35 + b.shade * 0.3));
    });
    scene.add(mesh);
  }

  for (const b of blocks) if (b.site) scene.add(sign(b.buildings[0]));

  return { update: createStreamer(physics, blocks), setNight };
}
