import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createClock, isNight } from '../src/clock.js';
import { createSky, moonDirection } from '../src/sky.js';
import { createHeadlights } from '../src/headlights.js';
import { addWindows } from '../src/windows.js';
import { createVariables } from '../src/variables.js';

test('night runs from 6pm to 6am', () => {
  for (const hour of [18, 21, 0, 3, 5.99]) assert.ok(isNight(hour), `${hour}`);
  for (const hour of [6, 12, 17.99]) assert.ok(!isNight(hour), `${hour}`);
});

test('the clock follows the system time unless set', () => {
  let date = new Date(2026, 0, 1, 17, 59, 30);
  const clock = createClock(() => date);
  assert.equal(clock.label(), '17:59');
  assert.ok(!clock.night());
  date = new Date(2026, 0, 1, 18, 0, 0);
  assert.ok(clock.night());
  const variables = createVariables({ garage: { names: [] }, clock });
  assert.ok(variables.var_time.set('9.5'));
  assert.equal(clock.label(), '09:30');
  assert.ok(!clock.night());
  for (const bad of ['25', '-1', 'noon', '']) assert.ok(!variables.var_time.set(bad), bad);
  assert.ok(variables.var_time.set('system'));
  assert.equal(variables.var_time.get(), 'system');
  assert.equal(clock.label(), '18:00');
});

test('the sky is dark with a moon at night, and light without one by day', () => {
  const scene = new THREE.Scene();
  const sky = createSky(scene);
  const camera = new THREE.PerspectiveCamera(65, 1, 1, 2000);
  camera.position.set(100, 5, -40);
  const brightness = () => scene.background.r + scene.background.g + scene.background.b;
  sky.update(12, camera, 0);
  const day = brightness();
  assert.ok(!sky.moon.visible);
  for (const hour of [18, 21, 0, 5.9]) {
    sky.update(hour, camera, 0);
    assert.ok(brightness() < day / 10);
    assert.ok(sky.moon.visible);
    // Above the horizon, and inside the part of the world that gets drawn.
    assert.ok(sky.moon.position.y > camera.position.y + 100);
    assert.ok(sky.moon.position.distanceTo(camera.position) + sky.moon.scale.x < camera.far);
  }
  assert.ok(moonDirection(0).y > moonDirection(18).y, 'highest around midnight');
});

test('headlights shine from a car or motorbike at night only', () => {
  const lights = createHeadlights(new THREE.Scene());
  const mesh = new THREE.Object3D();
  mesh.position.set(10, 0.8, 20);
  mesh.rotation.y = Math.PI / 2; // facing +x
  for (const [name, night, on] of [['car', true, true], ['motorbike', true, true], ['car', false, false],
    ['on foot', true, false], ['helicopter', true, false]]) {
    lights.update({ name, mesh }, night);
    assert.equal(lights.light.intensity > 0, on, `${name} ${night}`);
    assert.equal(lights.group.visible, on);
  }
  lights.update({ name: 'car', mesh }, true);
  assert.ok(lights.light.target.position.x > lights.light.position.x + 10, 'not pointing forwards');
});

test('the window shader finds its places in three.js', () => {
  const material = new THREE.MeshLambertMaterial();
  const set = addWindows(material);
  const { vertexShader, fragmentShader } = THREE.ShaderLib.lambert;
  const shader = { uniforms: {}, vertexShader, fragmentShader };
  material.onBeforeCompile(shader);
  assert.match(shader.vertexShader, /vWindowPoint = \(modelMatrix/);
  assert.match(shader.fragmentShader, /totalEmissiveRadiance \+= warm/);
  set(true);
  assert.equal(shader.uniforms.windowsLit.value, 1);
});
