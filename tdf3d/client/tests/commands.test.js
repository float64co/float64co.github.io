import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { run, createCompletion } from '../src/console.js';
import { createCommands } from '../src/commands.js';
import { createVariables } from '../src/variables.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { createClock } from '../src/clock.js';

await RAPIER.init();

function setup() {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const scene = new THREE.Scene();
  const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: 30 });
  const traffic = createTraffic(scene, physics);
  let distance = 1600;
  let shown = false;
  const view = { get: () => distance, set: (n) => { distance = n; } };
  const fps = { isOn: () => shown, toggle: () => { shown = !shown; } };
  const variables = createVariables({
    garage, view, fps, traffic, clock: createClock(),
    perf: { isOn: () => false, set() {} }, radio: { on: true, volume: 0.6, folder: null, stations: [] }, resolution: { get: () => 1, set() {} },
  });
  const commands = createCommands({ garage });
  return { garage, traffic, variables, say: (line) => run(commands, variables, line) };
}

test('variables show and change settings', () => {
  const { say, garage, traffic } = setup();
  assert.equal(say('var_peds'), 'var_peds = 1');
  assert.equal(say('var_peds=0'), 'var_peds = 0');
  assert.equal(traffic.isOn('peds'), false);
  assert.equal(say('var_fps = 1'), 'var_fps = 1');
  assert.equal(say('var_view=900'), 'var_view = 900');
  assert.equal(say('var_radius=200'), 'var_radius = 200');
  assert.equal(traffic.radius(), 200);
  assert.equal(say('var_vehicle=bicycle'), 'var_vehicle = bicycle');
  assert.equal(garage.current.name, 'bicycle');
  assert.match(say('help'), /var_name=value/);
});

test('bad input changes nothing', () => {
  const { say, variables } = setup();
  const snapshot = () => Object.values(variables).map((v) => v.get());
  const before = snapshot();
  for (const bad of ['var_peds=2', 'var_peds=', 'var_view=5', 'var_radius=9999', 'var_radius=x',
    'var_vehicle=tank', 'var_engine=-1', 'var_engine=nope', 'var_nothing=1', 'var_toString=1', 'constructor'])
    assert.match(say(bad), /cannot set|unknown/, bad);
  assert.deepEqual(snapshot(), before);
});

test('tuning is remembered for each vehicle type', () => {
  const { say, garage } = setup();
  say('var_engine=5000');
  say('var_walk=4'); // only the walker has this, so it can be set from the car
  say('var_vehicle=bicycle');
  assert.equal(garage.current.tune.engine, 140);
  say('var_vehicle=car');
  assert.equal(garage.current.tune.engine, 5000);
  assert.equal(say('var_climb'), 'var_climb = 18');
  say('var_vehicle=spaceplane');
  assert.equal(say('var_engine'), 'var_engine = -', 'car, motorbike or bicycle: ambiguous');
  assert.equal(say('var_walk'), 'var_walk = 4');
});

test('tab completion lists matches and cycles both ways', () => {
  const menu = createCompletion(['var_cars', 'var_car_radius', 'var_peds', 'tp']);
  assert.ok(menu.open('var_'));
  assert.equal(menu.matches.length, 3);
  assert.deepEqual([menu.move(1), menu.move(1), menu.move(1), menu.move(1)],
    ['var_cars', 'var_car_radius', 'var_peds', 'var_cars']);
  assert.equal(menu.move(-1), 'var_peds');
  assert.ok(menu.open('var_car'));
  assert.equal(menu.move(-1), 'var_car_radius');
  assert.ok(!menu.open('zzz'));
});

test('tab completion matches any part of a name, prefixes first', () => {
  const menu = createCompletion(['var_cars', 'var_car_radius', 'var_ped_radius', 'reset', 'pos']);
  assert.ok(menu.open('radius'));
  assert.deepEqual(menu.matches, ['var_car_radius', 'var_ped_radius']);
  assert.ok(menu.open('RE'));
  assert.deepEqual(menu.matches, ['reset']);
  assert.ok(menu.open('s'));
  assert.deepEqual(menu.matches, ['var_cars', 'var_car_radius', 'var_ped_radius', 'reset', 'pos'].filter((n) => n.includes('s')));
  assert.ok(menu.open('p'));
  assert.deepEqual(menu.matches, ['pos', 'var_ped_radius']);
});
