import test from 'node:test';
import assert from 'node:assert/strict';
import { PITCH, ROAD, generateCity } from '../src/city.js';
import { createTrafficSim as createSim, limitsFor, LIMITS as FIXED } from '../src/traffic-sim.js';
import { signal } from '../src/signals.js';

const focus = { x: 0, z: 30 };
const TEST_RADIUS = 220;
const LIMITS = { ...FIXED, cars: limitsFor('cars', TEST_RADIUS).count, peds: limitsFor('peds', TEST_RADIUS).count };
function createTrafficSim(seed) {
  const sim = createSim(seed);
  sim.setRadius(TEST_RADIUS);
  return sim;
}
const DT = 1 / 60;
const offRoad = (v) => Math.abs(v - Math.round(v / PITCH) * PITCH);
const distance = (a, b) => Math.hypot(a.x - b.x, a.z - b.z);

test('crossing directions are never both open', () => {
  for (let t = 0; t < 200; t += 0.25)
    for (const [i, j] of [[0, 0], [1, 0], [-3, 2]])
      assert.ok(signal(i, j, 0, t).colour === 'red' || signal(i, j, 1, t).colour === 'red');
});

test('ten simulated minutes: cars keep to roads, apart and moving', () => {
  const sim = createTrafficSim();
  let samples = 0;
  let moving = 0;
  let overlaps = 0;
  let worst = Infinity;
  let longest = 0;
  const travelled = new Set();
  for (let step = 0; step < 600 * 60; step++) {
    sim.step(DT, focus);
    if (step % 30) continue;
    for (const [n, car] of sim.cars.entries()) {
      samples++;
      if (car.speed > 1) moving++;
      longest = Math.max(longest, car.waited);
      if (car.turn !== 'straight') travelled.add(car.turn);
      assert.ok(Math.min(offRoad(car.x), offRoad(car.z)) <= ROAD / 2, `off road ${car.x} ${car.z}`);
      for (const other of sim.cars.slice(n + 1)) {
        const d = distance(car, other);
        worst = Math.min(worst, d);
        if (d < 2) overlaps++;
      }
    }
  }
  const result = { cars: sim.cars.length, moving: moving / samples, overlaps, worst, longest, stuck: sim.stats.stuck };
  console.log(result);
  assert.ok(result.cars > LIMITS.cars * 0.8);
  assert.ok(result.moving > 0.4, 'gridlock');
  assert.equal(result.overlaps, 0);
  assert.equal(result.stuck, 0);
});

test('pedestrians stay out of buildings and cross only on a light', () => {
  const sim = createTrafficSim();
  const buildings = generateCity().flatMap((b) => b.buildings)
    .filter((b) => Math.abs(b.x) < 250 && Math.abs(b.z) < 280);
  let onRoad = 0;
  for (let step = 0; step < 120 * 60; step++) {
    sim.step(DT, focus);
    if (step % 60) continue;
    for (const ped of sim.peds) {
      assert.ok(!buildings.some((b) => Math.abs(ped.x - b.x) < b.w / 2 && Math.abs(ped.z - b.z) < b.d / 2));
      if (ped.onRoad) onRoad++;
    }
  }
  assert.ok(sim.peds.length > LIMITS.peds * 0.8);
  assert.ok(onRoad > 0, 'nobody ever crossed');
});

test('cars and pedestrians never occupy the same space, and nobody gets stuck', () => {
  for (const seed of [1, 2, 3]) {
    const sim = createTrafficSim(seed);
    let hits = 0;
    for (let step = 0; step < 300 * 60; step++) {
      sim.step(DT, focus);
      if (step % 15 || step < 600) continue;
      for (const car of sim.cars)
        for (const ped of sim.peds) {
          const dx = ped.x - car.x;
          const dz = ped.z - car.z;
          if (Math.abs(dx * car.hx + dz * car.hz) < 2 && Math.abs(dz * car.hx - dx * car.hz) < 1) hits++;
        }
    }
    assert.equal(hits, 0, `seed ${seed}`);
    assert.equal(sim.stats.stuck, 0);
  }
});

test('cars stop for something in their lane and people step round a standing one', () => {
  const sim = createTrafficSim();
  sim.set('peds', false);
  sim.step(DT, focus);
  const car = sim.cars.find((c) => !c.path && c.s < 15);
  const block = { x: car.x + car.hx * 18, z: car.z + car.hz * 18, r: 1.8, speed: 0 };
  let closest = Infinity;
  for (let step = 0; step < 15 * 60; step++) {
    sim.step(DT, focus, [block]);
    closest = Math.min(closest, distance(car, block));
  }
  assert.ok(closest > 3.5, `drove into it: ${closest}`);

  sim.set('peds', true);
  sim.step(DT, focus);
  // Someone nearby, as the player's vehicles are.
  const ped = sim.peds.find((p) => distance(p, focus) < 100);
  const standing = { x: ped.x, z: ped.z, r: 1.8, speed: 0 };
  sim.step(DT, focus, [standing]);
  assert.ok(distance(ped, standing) > 2, 'did not step aside');
});

test('only a moving vehicle knocks people over, and they get up again', () => {
  const sim = createTrafficSim();
  sim.set('cars', false);
  sim.step(DT, focus);
  // Someone near enough not to be forgotten while they are down.
  const ped = sim.peds.find((p) => distance(p, focus) < 100);
  const at = () => ({ x: ped.x, z: ped.z, r: 1.8, hx: 0, hz: 1, halfL: 2, halfW: 1, vx: 0, vz: 10 });
  for (const harmless of [{ ...at(), vehicle: true, speed: 0.5 }, { ...at(), speed: 7 }]) {
    sim.step(DT, focus, [harmless]);
    assert.equal(sim.fallen.length, 0);
  }
  sim.step(DT, focus, [{ ...at(), vehicle: true, speed: 10 }]);
  assert.ok(sim.fallen.some((f) => f.ped === ped) && !sim.peds.includes(ped));
  for (let step = 0; step < 7 * 60; step++) sim.step(DT, focus);
  assert.equal(sim.fallen.length, 0);
  assert.ok(sim.peds.includes(ped), 'did not get up');
});

test('people walk round each other', () => {
  const sim = createTrafficSim();
  sim.set('cars', false);
  let pairs = 0;
  let close = 0;
  let through = 0;
  for (let step = 0; step < 180 * 60; step++) {
    sim.step(DT, focus);
    if (step % 20 || step < 600) continue;
    for (const [n, a] of sim.peds.entries())
      for (const b of sim.peds.slice(n + 1)) {
        if (distance(a, focus) > 110) continue; // dodging is only simulated nearby
        const d = distance(a, b);
        if (d < 2) pairs++;
        if (d < 0.45) close++;
        if (d < 0.2) through++;
      }
  }
  console.log({ pairs, close, through });
  assert.ok(pairs > 1000, 'nobody ever met');
  assert.equal(through, 0);
  assert.ok(close / pairs < 0.002);
});

test('same seed and inputs give the same traffic', () => {
  const play = () => {
    const sim = createTrafficSim(5);
    for (let step = 0; step < 600; step++) sim.step(DT, focus);
    return [sim.cars.map((c) => [c.x, c.z]), sim.peds.map((p) => [p.x, p.z])];
  };
  assert.deepEqual(play(), play());
});

test('each kind of traffic can be switched off and on', () => {
  const sim = createTrafficSim();
  sim.step(DT, focus);
  sim.set('cars', false);
  assert.equal(sim.cars.length, 0);
  assert.ok(sim.peds.length > 0);
  sim.set('peds', false);
  sim.step(DT, focus);
  assert.deepEqual([sim.cars.length, sim.peds.length], [0, 0]);
  sim.set('cars', true);
  sim.step(DT, focus);
  assert.ok(sim.cars.length > 0 && sim.peds.length === 0);
});

test('changing the radius resizes the population around the player', () => {
  const sim = createTrafficSim();
  const run = (seconds) => { for (let i = 0; i < seconds * 60; i++) sim.step(DT, focus); };
  const furthest = (list) => Math.max(...list.map((o) => distance(o, focus)));
  run(1);
  const before = sim.cars.length;
  sim.setRadius(150);
  assert.ok(sim.cars.length <= limitsFor('cars', 150).count && sim.cars.length < before);
  assert.ok(sim.peds.length <= limitsFor('peds', 150).count);
  run(5);
  assert.ok(furthest(sim.cars) <= limitsFor('cars', 150).keep + 1);
  assert.ok(furthest(sim.peds) <= limitsFor('peds', 150).keep + 1);
  sim.setRadius(600);
  run(20);
  assert.ok(sim.cars.length > limitsFor('cars', 600).count * 0.8, `only ${sim.cars.length}`);
  assert.ok(furthest(sim.cars) > 450 && furthest(sim.peds) > 450);
  assert.ok(sim.peds.length > limitsFor('peds', 600).count * 0.8, `only ${sim.peds.length} people`);
});

test('cars keep to the left of the road', () => {
  const sim = createTrafficSim();
  sim.step(DT, focus);
  for (const car of sim.cars.filter((c) => !c.path)) {
    // Signed distance from the road centre line towards the driver's left.
    const [dx, dz] = car.dir;
    const across = dx ? car.z - car.j * PITCH : car.x - car.i * PITCH;
    assert.ok(Math.abs(across * (dx ? -dx : dz) - 3.5) < 1e-9, `${across} heading ${car.dir}`);
  }
});
