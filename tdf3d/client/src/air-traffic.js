// A layer of flying cars over the roads. Each follows its lane for ever, so
// where it is depends only on the time. One that gets hit drops out of the layer
// and becomes an ordinary falling car.
import * as THREE from 'three';
import { PITCH, SIZE } from './city.js';

// Heights of the lanes running along z and along x: one layer, but crossing
// traffic passes over and under.
export const HEIGHT = { z: 45, x: 50 };
export const SPACING = 96;   // metres between cars in a lane
export const SPEED = 18;     // m/s, give or take 15% from lane to lane
const EVERY = 2;             // a lane in each direction over every other road
const ACROSS = 3.5;          // metres left of the road's centre line
const LIMIT = SIZE / 2 - PITCH;

// A fraction from 0 to 1 that is always the same for the same numbers.
const hash = (a, b, c) => {
  const n = Math.sin(a * 127.1 + b * 311.7 + c * 74.7) * 43758.5453;
  return n - Math.floor(n);
};

// Every flying car within `radius` of the focus: { id, x, y, z, hx, hz, speed, shade }.
export function airCars(time, focus, radius) {
  const found = [];
  for (const axis of ['z', 'x']) {
    // The roads that lanes run over, and how far along them.
    const [across, along] = axis === 'z' ? [focus.x, focus.z] : [focus.z, focus.x];
    const first = Math.ceil((across - radius) / (PITCH * EVERY));
    const last = Math.floor((across + radius) / (PITCH * EVERY));
    for (let road = first; road <= last; road++) {
      const centre = road * PITCH * EVERY;
      if (Math.abs(centre) > LIMIT) continue;
      for (const way of [1, -1]) {
        const seed = [axis === 'z' ? 1 : 2, road, way];
        const speed = SPEED * (0.85 + 0.3 * hash(...seed));
        const moved = hash(way, road, seed[0]) * SPACING + way * speed * time;
        const from = Math.ceil((along - radius - moved) / SPACING);
        const to = Math.floor((along + radius - moved) / SPACING);
        for (let n = from; n <= to; n++) {
          const u = moved + n * SPACING;
          // Keep to the left of the way of travel.
          const side = centre + (axis === 'z' ? way : -way) * ACROSS;
          const x = axis === 'z' ? side : u;
          const z = axis === 'z' ? u : side;
          if (Math.abs(u) > LIMIT || Math.hypot(x - focus.x, z - focus.z) > radius) continue;
          found.push({
            id: `${axis} ${road} ${way} ${n}`,
            x, y: HEIGHT[axis], z,
            hx: axis === 'x' ? way : 0, hz: axis === 'z' ? way : 0,
            speed,
            shade: hash(n, road * 2 + way, seed[0]),
          });
        }
      }
    }
  }
  return found;
}

const footprintOf = (c) => ({
  x: c.x, z: c.z, hx: c.hx, hz: c.hz, halfL: 2, halfW: 1, vx: c.hx * c.speed, vz: c.hz * c.speed,
});

// knock(car) is called for one that has been hit, and takes it over.
export function createAirTraffic(scene, knock, overlaps, most = 700) {
  const gone = new Set(); // ids no longer in the layer
  let cars = [];

  function instances(geometry, material, count) {
    const mesh = new THREE.InstancedMesh(geometry, material, count);
    mesh.frustumCulled = false;
    mesh.count = 0;
    scene.add(mesh);
    return mesh;
  }
  // The same shapes as other cars, with the wheels already folded flat. Plain
  // boxes: one that gets damaged becomes a car of its own.
  const shell = new THREE.BoxGeometry(2, 0.8, 4);
  const cabin = new THREE.BoxGeometry(1.7, 0.7, 2).translate(0, 0.75, -0.3);
  const shells = instances(shell, new THREE.MeshLambertMaterial(), most);
  const cabins = instances(cabin, new THREE.MeshLambertMaterial(), most);
  const tyre = new THREE.CylinderGeometry(0.4, 0.4, 0.35, 12);
  const tyres = instances(tyre, new THREE.MeshLambertMaterial({ color: 0x151515 }), most * 4);
  const hubs = instances(new THREE.CylinderGeometry(0.19, 0.19, 0.04, 10),
    new THREE.MeshLambertMaterial({ color: 0xc9ced4 }), most * 4);
  const WHEELS = [[-1.08, 1.4], [1.08, 1.4], [-1.08, -1.4], [1.08, -1.4]];

  const matrix = new THREE.Matrix4();
  const position = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  const unit = new THREE.Vector3(1, 1, 1);
  const up = new THREE.Vector3(0, 1, 0);
  const colour = new THREE.Color();
  const put = (mesh, n, x, y, z) => mesh.setMatrixAt(n, matrix.compose(position.set(x, y, z), rotation, unit));

  function drop(car) {
    gone.add(car.id);
    cars = cars.filter((c) => c !== car);
    return knock(car);
  }

  // The first car on a ray, as { car, distance }.
  function raycast(from, dir, reach) {
    let best = null;
    for (const car of cars) {
      // Into the car's frame, where it is a box.
      const local = (x, y, z) => [x * car.hz - z * car.hx, y, x * car.hx + z * car.hz];
      const origin = local(from.x - car.x, from.y - car.y, from.z - car.z);
      const way = local(dir.x, dir.y, dir.z);
      let enter = 0;
      let leave = best?.distance ?? reach;
      [[-1, 1], [-0.4, 1.1], [-2, 2]].forEach(([low, high], k) => {
        const a = (low - origin[k]) / way[k];
        const b = (high - origin[k]) / way[k];
        enter = Math.max(enter, Math.min(a, b));
        leave = Math.min(leave, Math.max(a, b));
      });
      if (enter <= leave) best = { car, distance: enter };
    }
    return best;
  }

  return {
    get cars() { return cars; },
    raycast,
    drop,
    // Move on, and knock out anything a striker is about to hit. strikers:
    // footprints with y and velocities, of things that may be up there.
    update(time, focus, radius, strikers, dt, enabled = true) {
      cars = enabled ? airCars(time, focus, radius).filter((c) => !gone.has(c.id)).slice(0, most) : [];
      const high = strikers.filter((s) => s.y > 30);
      if (!high.length) return;
      for (const car of [...cars]) {
        const box = footprintOf(car);
        const hit = high.some((s) => Math.abs(s.y - car.y) < 1.6 && Math.abs(s.x - car.x) < 12
          && Math.abs(s.z - car.z) < 12
          && (overlaps(s, box, 0.15) || overlaps({
            ...s, x: s.x + (s.vx - box.vx) * dt * 2, z: s.z + (s.vz - box.vz) * dt * 2,
          }, box, 0.15)));
        if (hit) drop(car);
      }
    },
    // The wheels of the cars near enough for their sparks to be seen.
    wheels(focus, reach = 250) {
      return cars.filter((c) => Math.hypot(c.x - focus.x, c.z - focus.z) < reach)
        .flatMap((c) => WHEELS.map(([across, along]) => ({
          x: c.x + c.hz * across + c.hx * along, y: c.y - 0.75, z: c.z - c.hx * across + c.hz * along,
          vx: c.hx * c.speed, vz: c.hz * c.speed,
        })));
    },
    draw() {
      cars.forEach((car, n) => {
        rotation.setFromAxisAngle(up, Math.atan2(car.hx, car.hz));
        put(shells, n, car.x, car.y, car.z);
        put(cabins, n, car.x, car.y, car.z);
        shells.setColorAt(n, colour.setHSL(car.shade, 0.55, 0.5));
        cabins.setColorAt(n, colour.setHSL(car.shade, 0.45, 0.35));
        WHEELS.forEach(([across, along], k) => {
          const x = car.x + car.hz * across + car.hx * along;
          const z = car.z - car.hx * across + car.hz * along;
          put(tyres, n * 4 + k, x, car.y - 0.55, z);
          put(hubs, n * 4 + k, x, car.y - 0.74, z);
        });
      });
      for (const [mesh, each] of [[shells, 1], [cabins, 1], [tyres, 4], [hubs, 4]]) {
        mesh.count = cars.length * each;
        mesh.instanceMatrix.needsUpdate = true;
        if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      }
    },
    meshes: { shells, cabins, tyres, hubs },
  };
}
