// Times the CPU side of the game, with no browser and no graphics: `npm run bench`.
// What the graphics card does with the result is not measured here; see var_perf.
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH, SEED } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { createCamera } from '../src/camera.js';
import { createSky } from '../src/sky.js';
import { createHeadlights } from '../src/headlights.js';

const STEP = 1 / 60;
const FRAMES = 600;
await RAPIER.init();
const city = generateCity();

const spent = new Map(); // section -> ms in the current frame
function timed(object, method, section) {
  const original = object[method].bind(object);
  object[method] = (...args) => {
    const start = performance.now();
    const result = original(...args);
    spent.set(section, (spent.get(section) ?? 0) + performance.now() - start);
    return result;
  };
}

// What three.js would be asked to draw from here, allowing for frustum culling.
function drawn(scene, camera) {
  scene.updateMatrixWorld();
  camera.updateMatrixWorld();
  const frustum = new THREE.Frustum().setFromProjectionMatrix(
    new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
  let calls = 0;
  let triangles = 0;
  scene.traverseVisible((o) => {
    if (!(o.isMesh || o.isLine) || (o.isInstancedMesh && !o.count)) return;
    if (o.frustumCulled && !frustum.intersectsObject(o)) return;
    const { geometry } = o;
    calls++;
    triangles += ((geometry.index?.count ?? geometry.attributes.position.count) / 3) * (o.isInstancedMesh ? o.count : 1);
  });
  return { calls, triangles: Math.round(triangles) };
}

function run(name, { radius = 450, night = false, stars = 0, driving = false, steps = 2 } = {}) {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = STEP;
  const scene = new THREE.Scene();
  const sky = createSky(scene);
  const headlights = createHeadlights(scene);
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics, SEED);
  traffic.setRadius(radius);
  traffic.wanted.immune = !stars;
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 });
  const camera = new THREE.PerspectiveCamera(65, 16 / 9, 1, 1700);
  const chase = createCamera(camera, physics);

  timed(physics, 'step', 'physics (Rapier)');
  timed(traffic.sim, 'step', 'cars and people (C++)');
  timed(traffic.poles, 'update', 'poles and lamps: physics');
  timed(traffic.police, 'update', 'police');
  timed(world, 'update', 'building colliders');
  timed(traffic, 'update', 'traffic.update, all of it');
  timed(traffic.lamps, 'draw', 'draw: street lamps');
  timed(traffic, 'draw', 'draw: instances, all of it');

  let ticks = 0;
  const frame = () => {
    spent.clear();
    for (let n = 0; n < steps; n++) {
      // Drive up and down the road, or sit still.
      const throttle = driving ? (Math.floor(ticks++ / 600) % 2 ? -1 : 1) : 0;
      garage.drive({ throttle, steer: 0, brake: !driving }, STEP);
      world.update([garage.current, ...garage.parked.values(),
        ...[...traffic.wrecks, ...traffic.police.units].map((w) => w.car)].map((v) => v.body.translation()));
      traffic.update(garage, STEP);
      if (stars) {
        // Keep them coming: no arrest, no death.
        traffic.takeDamage();
        if (traffic.takeBusted() || !traffic.wanted.level) traffic.wanted.set(stars);
      }
      physics.step();
    }
    const start = performance.now();
    garage.sync();
    chase.update(garage.current, steps * STEP);
    camera.updateProjectionMatrix();
    sky.update(night ? 23 : 12, camera, 0);
    world.setNight(night);
    headlights.update(garage.current, night);
    traffic.draw(garage.current.mesh.position, night);
    scene.updateMatrixWorld(); // renderer.render does this
    spent.set('main thread, without rendering', performance.now() - start);
  };

  if (stars) traffic.wanted.set(stars);
  for (let n = 0; n < 600; n++) frame(); // let the city fill and the code warm up
  const samples = [];
  for (let n = 0; n < FRAMES; n++) {
    const start = performance.now();
    frame();
    spent.set('TOTAL', performance.now() - start);
    samples.push(new Map(spent));
  }
  const sections = [...new Set(samples.flatMap((s) => [...s.keys()]))];
  console.log(`\n== ${name} (radius ${radius}, ${steps} physics steps a frame)`);
  console.log(`   cars ${traffic.sim.cars.length}, people ${traffic.sim.peds.length}, `
    + `bodies ${physics.bodies.len()}, colliders ${physics.colliders.len()}, police ${traffic.police.units.length}, `
    + `wrecks ${traffic.wrecks.length}`);
  const view = drawn(scene, camera);
  console.log(`   to draw: ${view.calls} draw calls, ${(view.triangles / 1e6).toFixed(2)}M triangles`);
  for (const section of sections) {
    const values = samples.map((s) => s.get(section) ?? 0).sort((a, b) => a - b);
    const mean = values.reduce((a, b) => a + b) / values.length;
    console.log(`   ${section.padEnd(32)} mean ${mean.toFixed(2).padStart(6)} ms   95% ${values[Math.floor(values.length * 0.95)].toFixed(2).padStart(6)} ms`);
  }
}

run('Parked, day');
run('Parked, night', { night: true });
run('Driving, night', { night: true, driving: true });
run('Five stars, night', { night: true, stars: 5 });
run('Parked, day, small radius', { radius: 250 });
run('Parked, day, large radius', { radius: 600 });
