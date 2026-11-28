import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH } from '../src/city.js';
import { buildWorld } from '../src/world.js';
import { createGarage } from '../src/garage.js';
import { createTraffic } from '../src/traffic.js';
import { createTrafficSim } from '../src/traffic-sim.js';
import { createVariables } from '../src/variables.js';
import { createWanted, escapeTime } from '../src/wanted.js';
import { waypoint } from '../src/police.js';

await RAPIER.init();
const city = generateCity();
const DT = 1 / 60;

test('crimes add stars, hiding clears them, immunity stops them', () => {
  const wanted = createWanted();
  for (let i = 0; i < 100; i++) wanted.report('gunfire');
  assert.equal(wanted.level, 1, 'noise alone is one star');
  wanted.report('kill');
  assert.equal(wanted.level, 2);
  for (let i = 0; i < 20; i++) wanted.report('kill');
  assert.equal(wanted.level, 5);

  wanted.update(escapeTime(5) - 1, false);
  assert.equal(wanted.level, 5);
  assert.ok(wanted.evading <= 1);
  wanted.update(0.5, true); // spotted again: the count starts over
  assert.equal(wanted.evading, null);
  wanted.update(escapeTime(5) + 1, false);
  assert.equal(wanted.level, 0);

  wanted.immune = true;
  wanted.report('kill');
  assert.equal(wanted.level, 0);
  assert.ok(!wanted.set(3));
  wanted.immune = false;
  assert.ok(wanted.set(3));
  assert.equal(wanted.level, 3);
});

test('waypoints keep to the roads', () => {
  // Wherever they start from, following the corners gets there along roads.
  const goal = { x: 3 * PITCH, z: 2.5 * PITCH };
  for (const start of [{ x: 0, z: 30 }, { x: 90, z: 0 }, { x: -60, z: -150 }, { x: 180, z: -90 }]) {
    const at = { ...start };
    let steps = 0;
    while (Math.hypot(at.x - goal.x, at.z - goal.z) > 3 && steps++ < 1000) {
      const to = waypoint(at, goal);
      const length = Math.hypot(to.x - at.x, to.z - at.z);
      at.x += ((to.x - at.x) / length) * Math.min(2, length);
      at.z += ((to.z - at.z) / length) * Math.min(2, length);
      const off = (v) => Math.abs(v - Math.round(v / PITCH) * PITCH);
      assert.ok(Math.min(off(at.x), off(at.z)) < 8, `left the road at ${at.x}, ${at.z}`);
    }
    assert.ok(steps < 1000, `never arrived from ${start.x}, ${start.z}`);
  }
});

function setup(vehicle = 'car', spawn = { x: 0, y: 1.5, z: PITCH / 2 }) {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.timestep = DT;
  const scene = new THREE.Scene();
  const world = buildWorld(scene, physics, city);
  const traffic = createTraffic(scene, physics);
  traffic.setRadius(200);
  const garage = createGarage(scene, physics, spawn, vehicle);
  const step = (input = { throttle: 0, steer: 0, brake: true }) => {
    garage.drive(input, DT);
    world.update([garage.current, ...[...traffic.wrecks, ...traffic.police.units].map((w) => w.car)]
      .map((v) => v.body.translation()));
    traffic.update(garage, DT);
    physics.step();
  };
  for (let i = 0; i < 60; i++) step();
  return { physics, scene, traffic, garage, step };
}

for (const [vehicle, stars] of [['car', 1], ['on foot', 2], ['car', 4]]) {
  test(`police drive through traffic and bust a player ${vehicle === 'car' ? 'sitting in a car' : vehicle} (${stars} stars)`, () => {
    // Mid-block on a road, so it takes at least one corner or a straight run.
    const r = setup(vehicle === 'car' ? 'car' : 'car');
    if (vehicle === 'on foot') assert.equal(r.garage.toggle(), null);
    assert.ok(r.traffic.wanted.set(stars));
    let seconds = 0;
    let most = 0;
    let busted = false;
    let arresting = false;
    while (!busted && seconds < 90) {
      arresting ||= r.traffic.police.officers().some((o) => o.reaching);
      r.step();
      seconds += DT;
      most = Math.max(most, r.traffic.police.units.length);
      busted = r.traffic.takeBusted();
    }
    console.log({ stars, vehicle, seconds: seconds.toFixed(1), most });
    assert.ok(busted, 'never caught');
    assert.ok(arresting, 'busted without an officer on foot');
    assert.ok(most >= 1 && most <= 6);
    // Nobody vanishes at the moment of arrest, with the officer still there.
    assert.ok(r.traffic.police.units.length >= 1);
    assert.ok(r.traffic.police.officers().some((o) => o.reaching));
    for (let i = 0; i < 4 * 60; i++) r.step();
    assert.ok(r.traffic.police.officers().some((o) => o.reaching), 'let go during the sequence');
    // Once called off, they go.
    r.traffic.standDown();
    assert.equal(r.traffic.wanted.level, 0);
    for (let i = 0; i < 20 * 60; i++) r.step();
    assert.equal(r.traffic.police.units.length, 0);
  });
}

test('police are limited, and go when switched off', () => {
  const r = setup();
  const variables = createVariables({ garage: r.garage, traffic: r.traffic });
  assert.ok(variables.var_police_cars.set('2'));
  assert.ok(variables.var_wanted.set('5'));
  // Keep moving out of reach of an arrest.
  for (let i = 0; i < 8 * 60; i++) r.step({ throttle: 0, steer: 0, brake: false });
  assert.equal(r.traffic.police.units.length, 2);
  assert.ok(variables.var_police.set('0'));
  assert.equal(r.traffic.police.units.length, 0);
  assert.equal(variables.var_wanted.get(), 0);
  assert.ok(!variables.var_wanted.set('3'), 'no police to want you');
  r.traffic.wanted.report('kill');
  assert.equal(r.traffic.wanted.level, 0);
  assert.ok(variables.var_police.set('1'));
  assert.ok(variables.var_immune.set('1'));
  assert.ok(!variables.var_wanted.set('3'));
  for (const bad of ['9', '-1', 'x', '']) assert.ok(!variables.var_wanted.set(bad), bad);
});

test('firing makes you wanted and people run away from it', () => {
  const r = setup();
  const p = r.garage.current.body.translation();
  const away = (o) => Math.hypot(o.x - p.x, o.z - p.z);
  r.traffic.police.settings.cars = 0;
  const near = r.traffic.sim.peds.filter((ped) => away(ped) < 45);
  assert.ok(near.length > 3, `${near.length} people`);
  const before = near.map(away);
  r.traffic.shoot({ x: p.x, y: 1.5, z: p.z }, { x: 0, y: 1, z: 0 }, 50, null, 25);
  assert.equal(r.traffic.wanted.level, 1);
  for (let i = 0; i < 4 * 60; i++) r.step();
  const gained = near.map((ped, n) => away(ped) - before[n]);
  const fled = gained.filter((d) => d > 5).length;
  assert.ok(fled > near.length * 0.7, `${fled} of ${near.length} ran`);
});

test('people jump out of the way of a fast car if they have room', () => {
  const sim = createTrafficSim(3);
  sim.set('cars', false);
  const focus = { x: 0, z: 0 };
  for (let i = 0; i < 60; i++) sim.step(DT, focus);
  const ped = [...sim.peds].sort((a, b) => Math.hypot(a.x, a.z) - Math.hypot(b.x, b.z))[0];
  for (const other of sim.peds) if (other !== ped) other.x += 5000;
  sim.enabled.peds = false;
  ped.pace = 0;
  sim.step(DT, focus);
  // A car 20 m off at 15 m/s, passing 0.8 m to one side of them.
  const car = {
    x: ped.x - 20, z: ped.z - 0.8, r: 1.8, speed: 15, vehicle: true,
    hx: 1, hz: 0, halfL: 2, halfW: 1, vx: 15, vz: 0,
  };
  for (let i = 0; i < 100; i++) {
    sim.step(DT, focus, [car]);
    car.x += 15 * DT;
  }
  assert.equal(sim.fallen.length, 0, 'was hit');
});

test('hitting someone with a car brings the police', () => {
  const r = setup();
  const [ped] = r.traffic.sim.peds;
  const { body } = r.garage.current;
  const p = body.translation();
  body.setLinvel({ x: 0, y: 0, z: 8 }, true);
  for (let i = 0; i < 60 && !r.traffic.wanted.level; i++) {
    const at = { x: p.x, z: body.translation().z + 2.5 };
    Object.assign(ped, { panic: 0, ox: at.x - (ped.x - ped.ox), oz: at.z - (ped.z - ped.oz), ...at });
    r.step({ throttle: 1, steer: 0, brake: false });
  }
  assert.ok(r.traffic.wanted.level >= 1);
});

test('shooting a police car raises the level', () => {
  const r = setup();
  r.traffic.wanted.set(1);
  for (let i = 0; i < 60 && !r.traffic.police.units.length; i++) r.step();
  const [unit] = r.traffic.police.units;
  r.physics.step();
  const p = unit.car.body.translation();
  const from = { x: p.x, y: p.y + 5, z: p.z };
  const hit = r.physics.castRay(new RAPIER.Ray(from, { x: 0, y: -1, z: 0 }), 10, true);
  assert.ok(r.traffic.police.owns(hit.collider));
  r.traffic.shoot(from, { x: 0, y: -1, z: 0 }, hit.timeOfImpact, hit.collider, 25);
  assert.equal(r.traffic.wanted.level, 2);
});

test('with no police able to see you, the wanted level runs out', () => {
  const r = setup('helicopter');
  r.traffic.police.settings.cars = 0; // none to follow
  r.traffic.wanted.set(2);
  const { body } = r.garage.current;
  let seconds = 0;
  while (r.traffic.wanted.level && seconds < 40) {
    body.setTranslation({ x: 0, y: 100, z: 30 }, true);
    body.setLinvel({ x: 0, y: 0, z: 0 }, true);
    r.step();
    seconds += DT;
  }
  assert.ok(Math.abs(seconds - escapeTime(2)) < 0.5, `took ${seconds}`);
});

// A car with its officer out and walking, near a player sitting still.
function approached(stars = 1) {
  const r = setup();
  r.traffic.wanted.set(stars);
  for (let i = 0; i < 60 * 60 && !r.traffic.police.officers().length; i++) r.step();
  assert.equal(r.traffic.police.officers().length, 1, 'nobody got out');
  return r;
}

test('a police car alone cannot arrest you', () => {
  const r = approached();
  // Keep the officer away: knock them down over and over.
  for (let i = 0; i < 20 * 60; i++) {
    for (const o of r.traffic.police.officers()) o.knock(0, 0);
    r.step();
    assert.ok(!r.traffic.takeBusted());
  }
  assert.equal(r.traffic.police.arrest, 0);
});

test('driving off sends the officer back to the car, which follows', () => {
  const r = approached();
  const [unit] = r.traffic.police.units;
  let seconds = 0;
  const { body } = r.garage.current;
  while (unit.officer && seconds < 20) {
    // Pretend to be on the move without going anywhere.
    body.setLinvel({ x: 0, y: 0, z: 8 }, true);
    body.setTranslation({ x: 0, y: 0.8, z: 30 }, true);
    r.step();
    seconds += DT;
  }
  assert.equal(unit.officer, null, 'still on foot');
  assert.ok(!r.traffic.takeBusted());
});

test('officers can be run over and get up again, or shot dead and replaced', () => {
  const r = approached();
  r.traffic.police.settings.cars = 1;
  const [officer] = r.traffic.police.officers();
  const bodies = r.physics.bodies.len();
  assert.ok(r.traffic.police.strike(
    [{ x: officer.x, z: officer.z, hx: 0, hz: 1, halfL: 2, halfW: 1, vx: 0, vz: 8 }],
    () => true, DT,
  ));
  assert.ok(officer.down);
  assert.equal(r.physics.bodies.len(), bodies + 9);
  for (let i = 0; i < 8 * 60; i++) officer.step(DT);
  assert.ok(!officer.down, 'stayed down');
  assert.equal(r.physics.bodies.len(), bodies);

  // Shoot them from above: a head shot is enough with the rifle.
  r.physics.step();
  const from = { x: officer.x, y: 5, z: officer.z };
  const down = { x: 0, y: -1, z: 0 };
  const hit = r.physics.castRay(new RAPIER.Ray(from, down), 10, true);
  const result = r.traffic.shoot(from, down, hit.timeOfImpact, hit.collider, 40);
  assert.equal(result.hp, 0);
  assert.ok(officer.dead && officer.down);
  assert.ok(r.traffic.wanted.level >= 2);
  for (let i = 0; i < 5 * 60; i++) r.step({ throttle: 0, steer: 0, brake: false });
  assert.equal(r.traffic.police.units.length, 2, 'no replacement sent');
});

test('officers walk round a vehicle in the way', async () => {
  const { around, createOfficer } = await import('../src/officers.js');
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const car = { x: 0, z: 0, hx: 0, hz: 1, halfL: 2, halfW: 1 };
  const officer = createOfficer(new THREE.Scene(), physics, { x: -5, z: 0.1 });
  const goal = { x: 5, z: 0 };
  assert.notDeepEqual(around(officer, goal, [car]), goal);
  for (let i = 0; i < 5 * 60 && Math.hypot(officer.x - 5, officer.z) > 0.2; i++) {
    officer.walk(goal, [car], DT);
    assert.ok(Math.abs(officer.x) > 1.3 || Math.abs(officer.z) > 2.3, `walked through at ${officer.x}, ${officer.z}`);
  }
  assert.ok(Math.hypot(officer.x - 5, officer.z) <= 0.2, `stuck at ${officer.x}, ${officer.z}`);
});

test('officers find a way out from between two vehicles', async () => {
  const { around, createOfficer } = await import('../src/officers.js');
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  // Two cars with a gap too narrow to squeeze through at one end.
  const cars = [
    { x: 0, z: 0, hx: 0, hz: 1, halfL: 2, halfW: 1 },
    { x: 2.2, z: -1.5, hx: 1, hz: 0, halfL: 2, halfW: 1 },
  ];
  const officer = createOfficer(new THREE.Scene(), physics, { x: 1.4, z: -3.1 });
  const goal = { x: 1.8, z: 1.2 };
  for (let i = 0; i < 6 * 60 && Math.hypot(officer.x - goal.x, officer.z - goal.z) > 0.3; i++)
    officer.walk(goal, cars, DT);
  assert.ok(Math.hypot(officer.x - goal.x, officer.z - goal.z) <= 0.3, `stuck at ${officer.x}, ${officer.z}`);
  assert.ok(around(officer, goal, cars));
});

test('officers climb over cars to someone walled in by them', async () => {
  const { createOfficer } = await import('../src/officers.js');
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const car = (x, z, hx, hz) => ({ x, z, hx, hz, halfL: 2, halfW: 1 });
  const ring = [car(0, 2, 1, 0), car(0, -2, 1, 0), car(2.5, 0, 0, 1), car(-2.5, 0, 0, 1)];
  const officer = createOfficer(new THREE.Scene(), physics, { x: 7, z: 0.2 });
  let highest = 0;
  for (let i = 0; i < 5 * 60 && Math.hypot(officer.x, officer.z) > 0.3; i++) {
    officer.walk({ x: 0, z: 0 }, ring, DT);
    highest = Math.max(highest, officer.lift);
  }
  assert.ok(Math.hypot(officer.x, officer.z) <= 0.3, `stuck at ${officer.x}, ${officer.z}`);
  assert.ok(highest > 1, 'walked through rather than over');
});

test('officers never stand inside each other, and one can still make the arrest', async () => {
  const { createOfficer, separate, APART } = await import('../src/officers.js');
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const scene = new THREE.Scene();
  const goal = { x: 0, z: 0 };
  // Six converge on one spot, two of them starting in exactly the same place.
  const officers = [[6, 0], [6, 0], [-6, 0.1], [0, 6], [0.2, -6], [4, 4]]
    .map(([x, z]) => createOfficer(scene, physics, { x, z }));
  const closest = () => Math.min(...officers.flatMap((a, i) => officers.slice(i + 1)
    .map((b) => Math.hypot(a.x - b.x, a.z - b.z))));
  let holder = null;
  for (let step = 0; step < 6 * 60; step++) {
    for (const o of officers) {
      // The first to arrive takes hold, and stays within reach.
      if (Math.hypot(o.x, o.z) < 1) holder ??= o;
      if (o === holder) o.held += DT;
      else o.walk(goal, [], DT);
    }
    const before = holder && { x: holder.x, z: holder.z };
    separate(officers, []);
    if (before) assert.deepEqual({ x: holder.x, z: holder.z }, before, 'the one arresting was shoved');
    if (step > 0) assert.ok(closest() > APART * 0.8, `step ${step}: ${closest()}`);
  }
  assert.ok(holder);
  assert.ok(officers.every((o) => Math.hypot(o.x, o.z) < 2.5), 'the rest gave up their approach');
});

test('officers cannot walk into the player, standing or down, but can still arrest', async () => {
  const { createOfficer, separate, APART } = await import('../src/officers.js');
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const officer = createOfficer(new THREE.Scene(), physics, { x: 5, z: 0 });
  const player = { x: 0, z: 0 };
  for (let i = 0; i < 180; i++) {
    officer.walk(player, [], DT); // straight at them
    separate([officer], [], [player]);
    assert.ok(Math.hypot(officer.x, officer.z) > APART - 1e-6);
  }
  assert.ok(Math.hypot(officer.x, officer.z) - 0.35 < 1.2, 'kept out of arresting reach');
  // The player walks into them, even while being held: they give way.
  officer.held = 1;
  const intruder = { x: officer.x - 0.1, z: officer.z };
  separate([officer], [], [intruder]);
  assert.ok(Math.hypot(officer.x - intruder.x, officer.z - intruder.z) > APART - 1e-6);
  const lying = [{ x: 10, z: 0 }, { x: 10.6, z: 0 }, { x: 11.2, z: 0 }];
  Object.assign(officer, { x: 10.7, z: 0.1, held: 0 });
  separate([officer], [], lying);
  for (const part of lying)
    assert.ok(Math.hypot(officer.x - part.x, officer.z - part.z) > APART * 0.8);
});

for (const vehicle of ['car', 'motorbike']) {
  test(`in a ${vehicle} you are dragged out first, and only then arrested`, () => {
    const r = setup(vehicle);
    r.traffic.police.settings.cars = 1;
    r.traffic.wanted.set(1);
    const log = [];
    let busted = false;
    for (let i = 0; i < 90 * 60 && !busted; i++) {
      r.step();
      const walking = r.garage.current.name === 'on foot';
      if (!walking) assert.equal(r.traffic.police.arrest, 0, 'arrested while still inside');
      const [officer] = r.traffic.police.officers();
      const state = walking ? (r.garage.current.down ? 'down' : 'up') : officer?.pulling ? 'pulling' : 'in';
      if (state !== log.at(-1)) log.push(state);
      busted = r.traffic.takeBusted();
    }
    assert.ok(busted, `never caught: ${log}`);
    // A sliding bike can make them lose their grip for a moment and try again.
    assert.deepEqual([...new Set(log)], ['in', 'pulling', 'down', 'up']);
    assert.equal(log.at(-1), 'up');
    // The vehicle is left behind, and the player is beside it.
    assert.equal(r.garage.parked.size, 1);
    const [left] = r.garage.parked.values();
    assert.equal(left.name, vehicle);
    const a = left.body.translation();
    const b = r.garage.current.body.translation();
    assert.ok(Math.hypot(a.x - b.x, a.z - b.z) < 6);
  });
}

test('driving off while they are at the door gets you away', () => {
  const r = setup();
  r.traffic.police.settings.cars = 1;
  r.traffic.wanted.set(1);
  for (let i = 0; i < 90 * 60 && !r.traffic.police.pulling; i++) r.step();
  assert.ok(r.traffic.police.pulling > 0);
  r.garage.current.body.setLinvel({ x: 0, y: 0, z: 8 }, true);
  r.step({ throttle: 1, steer: 0, brake: false });
  r.step({ throttle: 1, steer: 0, brake: false });
  assert.equal(r.traffic.police.pulling, 0);
  assert.equal(r.garage.current.name, 'car');
});

test('officers go round buildings to reach you, and arrest you behind one', () => {
  const r = setup();
  r.traffic.set('cars', false);
  r.traffic.set('peds', false);
  r.traffic.police.settings.cars = 2;
  assert.equal(r.garage.toggle(), null);
  const buildings = city.flatMap((c) => c.buildings);
  // Stand on the far side of a building from the road.
  const b = buildings.filter((o) => !o.site && o.x < -5 && o.z > 0)
    .sort((p, q) => Math.hypot(p.x, p.z - 30) - Math.hypot(q.x, q.z - 30))[0];
  r.garage.current.body.setTranslation({ x: b.x - b.w / 2 - 1.2, y: 0.9, z: b.z }, true);
  r.step();
  r.traffic.wanted.set(1);
  const inside = (o) => buildings.some((w) => w.w / 2 - Math.abs(o.x - w.x) > 0.05 && w.d / 2 - Math.abs(o.z - w.z) > 0.05);
  let busted = false;
  let sawOne = false;
  for (let i = 0; i < 90 * 60 && !busted; i++) {
    r.step();
    for (const o of r.traffic.police.officers()) {
      sawOne = true;
      assert.ok(!inside(o), `inside a building at ${o.x.toFixed(1)}, ${o.z.toFixed(1)}`);
    }
    busted = r.traffic.takeBusted();
  }
  assert.ok(sawOne, 'nobody got out');
  assert.ok(busted, 'never reached you');
});
