import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createCamera } from '../src/camera.js';

const physics = { castRay: () => null };
const make = (name) => ({ name, mesh: new THREE.Group(), camera: { back: 5, up: 2 } });

test('V gives first person on foot only, and it is remembered', () => {
  const camera = new THREE.PerspectiveCamera();
  const view = createCamera(camera, physics);
  const car = make('car');
  const walker = make('on foot');
  walker.mesh.position.set(3, 1, 4);
  const looking = { yaw: Math.PI / 2, pitch: 0 }; // facing +x
  walker.view = () => looking;

  assert.equal(view.toggle(car), false);
  view.update(walker, 0.1);
  assert.ok(walker.mesh.visible && camera.position.distanceTo(walker.mesh.position) > 2);

  assert.equal(view.toggle(walker), true);
  view.update(walker, 0.1);
  assert.deepEqual(camera.position.toArray(), [3, 1.7, 4]);
  assert.ok(camera.getWorldDirection(new THREE.Vector3()).x > 0.99);
  assert.ok(!walker.mesh.visible && camera.near < 0.5);
  looking.pitch = 1;
  view.update(walker, 0.1);
  assert.ok(camera.getWorldDirection(new THREE.Vector3()).y > 0.8, 'looks up');
  looking.pitch = 0;

  view.update(car, 0.1);
  assert.ok(car.mesh.visible && camera.near === 1, 'driving uses the chase view');
  view.update(walker, 0.1);
  assert.ok(!walker.mesh.visible, 'first person comes back on foot');

  view.toggle(walker);
  view.update(walker, 0.1);
  assert.ok(walker.mesh.visible);
});

test('in a vehicle, looking back swings the camera round and straight back again', async () => {
  const THREE = await import('three');
  const RAPIER = (await import('@dimforge/rapier3d-compat')).default;
  const { createCamera } = await import('../src/camera.js');
  await RAPIER.init();
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const camera = new THREE.PerspectiveCamera();
  const chase = createCamera(camera, physics);
  const mesh = new THREE.Object3D();
  mesh.position.set(10, 1, 20);
  mesh.rotation.y = 0.7;
  const car = { name: 'car', mesh, camera: { back: 10, up: 5 } };
  const ahead = new THREE.Vector3(Math.sin(0.7), 0, Math.cos(0.7));
  const along = () => camera.position.clone().sub(mesh.position).setY(0).dot(ahead);
  const facing = () => camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize().dot(ahead);
  for (let i = 0; i < 300; i++) chase.update(car, 1 / 60);
  const usual = camera.position.clone();
  assert.ok(along() < -9 && facing() > 0.99);
  chase.update(car, 1 / 60, false, true);
  assert.ok(along() > 9 && facing() < -0.99, `${along()}, ${facing()}`);
  assert.ok(Math.abs(camera.position.y - usual.y) < 1e-6);
  chase.update(car, 1 / 60);
  assert.ok(camera.position.distanceTo(usual) < 1e-6, 'did not snap back');
});
