// NPC cars and pedestrians on the road grid. The simulation is C++ compiled to
// WebAssembly (native/traffic.cpp); this module gives it a JavaScript face.
// Junction (i, j) is at (i * PITCH, j * PITCH). Traffic keeps to the left.
// After editing the C++, run `npm run wasm`.
import { BLOCKS, PITCH, ROAD, SEED } from './city.js';
import { GREEN, AMBER, CLEAR } from './signals.js';
import { PED_HP, HEADSHOT } from './weapons.js';
import binary from './wasm/traffic-wasm.js';

// One radius around the player for cars, people and traffic lights (metres).
export const RADIUS = { default: 450, min: 100, max: 600 };
// Counts grow with the area, so a bigger radius is just as crowded.
const DENSITY = { cars: { count: 180, at: 300 }, peds: { count: 270, at: 150 } };
const NEAR_RATIO = 0.23; // arrivals appear at least this far out
const KEEP_RATIO = 1.23; // and are removed beyond this

export function limitsFor(kind, radius) {
  const { count, at } = DENSITY[kind];
  return {
    count: Math.round(count * (radius / at) ** 2),
    near: radius * NEAR_RATIO,
    max: radius,
    keep: radius * KEEP_RATIO,
  };
}

// Counts at the default radius.
export const LIMITS = {
  cars: limitsFor('cars', RADIUS.default).count,
  peds: limitsFor('peds', RADIUS.default).count,
  fallen: 40,
};
export const LANE = 3.5;            // lane centre, metres left of the road centre line
export const BOX = ROAD / 2 + 4;    // junction half-size: stop lines clear the crosswalks
export const POLE = 7.6;            // traffic lights, metres from the road centre lines
// Street lamps: metres from the road centre line, and from the junction.
export const LAMP = { across: 7.4, along: 20 };

const module = await WebAssembly.compile(Uint8Array.from(atob(binary), (c) => c.charCodeAt(0)));

const KINDS = { cars: 0, peds: 1 };
const TURNS = ['straight', 'right', 'left'];

// Field order matches the structs in traffic.cpp.
const CAR_FIELDS = ['id', 'x', 'z', 'hx', 'hz', 's', 'speed', 'cruise', 'waited', 'shade', 'drift',
  'i', 'j', 'dx', 'dz', 'nx', 'nz', 'turn', 'path', 'cleared'];
const PED_FIELDS = ['id', 'x', 'z', 'ox', 'oz', 'hx', 'hz', 's', 'pace', 'stride', 'shade',
  'onRoad', 'walking', 'hp', 'panic', 'tx', 'tz'];
const FALLEN_FIELDS = ['uid', 'slot', 'x', 'z', 'vx', 'vz', 'age', 'stays'];
const OBSTACLE_FIELDS = ['x', 'z', 'r', 'speed', 'vehicle', 'hx', 'hz', 'halfL', 'halfW', 'vx', 'vz'];

const LOOSE_FIELDS = ['ax', 'az', 'bx', 'bz', 'avx', 'avy', 'avz', 'bvx', 'bvy', 'bvz'];

// A view of one row of numbers, with a property for each name.
function rowClass(fields, special = {}) {
  class Row {
    constructor(data) { this.data = data; }
  }
  fields.forEach((name, index) => {
    Object.defineProperty(Row.prototype, name, {
      get() { return this.data[index]; },
      set(value) { this.data[index] = value; },
      configurable: true, // some are replaced below
      enumerable: true,
    });
  });
  for (const [name, property] of Object.entries(special))
    Object.defineProperty(Row.prototype, name, { enumerable: true, ...property });
  return Row;
}

const at = (name) => CAR_FIELDS.indexOf(name);
// A direction as [x, z]. Adding 0 turns -0 into 0.
const direction = (x, z) => ({
  get() { return [this.data[x] + 0, this.data[z] + 0]; },
  set([a, b]) { this.data[x] = a; this.data[z] = b; },
});
const flag = (index) => ({
  get() { return this.data[index] !== 0; },
  set(value) { this.data[index] = value ? 1 : 0; },
});

const CarView = rowClass(CAR_FIELDS, {
  dir: direction(at('dx'), at('dz')),
  next: direction(at('nx'), at('nz')),
  turn: {
    get() { return TURNS[this.data[at('turn')]]; },
    set(name) { this.data[at('turn')] = TURNS.indexOf(name); },
  },
  path: flag(at('path')), // true while crossing a junction
  cleared: flag(at('cleared')),
});
const PedView = rowClass(PED_FIELDS, {
  onRoad: flag(PED_FIELDS.indexOf('onRoad')),
  walking: flag(PED_FIELDS.indexOf('walking')),
});
const FallenView = rowClass(FALLEN_FIELDS);

export function createTrafficSim(seed = SEED) {
  // Each simulation has its own memory.
  const wasm = new WebAssembly.Instance(module).exports;
  wasm.init((seed ^ 0x5eed) >>> 0, PITCH, BLOCKS / 2 - 1, LANE, BOX, PED_HP,
    GREEN, AMBER, CLEAR, LIMITS.fallen, POLE, LAMP.across, LAMP.along);

  // The C++ uses only fixed memory, so these views stay valid.
  const doubles = (pointer, length) => new Float64Array(wasm.memory.buffer, pointer, length);
  const ints = (pointer, length) => new Int32Array(wasm.memory.buffer, pointer, length);
  const carStride = wasm.car_stride();
  const pedStride = wasm.ped_stride();
  const fallenStride = wasm.fallen_stride();
  const carRow = (slot) => doubles(wasm.car_data() + slot * carStride * 8, carStride);
  const pedRow = (slot) => doubles(wasm.ped_data() + slot * pedStride * 8, pedStride);
  const fallenRow = (n) => doubles(wasm.fallen_data() + n * fallenStride * 8, fallenStride);
  const carList = ints(wasm.car_list(), 2048);
  const pedList = ints(wasm.ped_list(), 8192);
  const rayData = doubles(wasm.ray_data(), 2);
  const obstacleData = doubles(wasm.obstacle_data(), wasm.obstacle_capacity() * OBSTACLE_FIELDS.length);

  const looseData = doubles(wasm.loose_data(), wasm.loose_capacity() * LOOSE_FIELDS.length);

  // Stable arrays of stable objects, so callers can hold on to them.
  const cars = [];
  const peds = [];
  const fallen = [];
  let known = { cars: new Map(), peds: new Map(), fallen: new Map() }; // id -> object
  let seen = -1;

  // Bring the JavaScript lists up to date with the C++ ones.
  function sync() {
    if (wasm.list_revision() === seen) return;
    seen = wasm.list_revision();
    const next = { cars: new Map(), peds: new Map(), fallen: new Map() };

    function wrap(kind, View, data) {
      const id = data[0];
      let object = known[kind].get(id);
      if (!object) object = new View(data);
      else object.data = data; // may have been detached or moved
      next[kind].set(id, object);
      return object;
    }

    cars.length = wasm.cars_length();
    for (let n = 0; n < cars.length; n++) cars[n] = wrap('cars', CarView, carRow(carList[n]));
    peds.length = wasm.peds_length();
    for (let n = 0; n < peds.length; n++) peds[n] = wrap('peds', PedView, pedRow(pedList[n]));
    fallen.length = wasm.fallen_length();
    for (let n = 0; n < fallen.length; n++) {
      // Rows move as the list changes, so each entry points at its current one.
      const data = fallenRow(n);
      const entry = wrap('fallen', FallenView, data);
      const row = pedRow(data[1]);
      const id = row[0];
      entry.ped ??= next.peds.get(id) ?? known.peds.get(id) ?? new PedView(row);
      entry.ped.data = row;
      next.peds.set(id, entry.ped);
      fallen[n] = entry;
    }

    // Anything gone keeps its last values, as its slot is about to be reused.
    for (const kind of ['cars', 'peds', 'fallen'])
      for (const [id, object] of known[kind])
        if (!next[kind].has(id)) object.data = object.data.slice();
    known = next;
    wasm.flush();
  }

  let radius = RADIUS.default;
  function applyLimits(metres) {
    for (const [kind, index] of Object.entries(KINDS)) {
      const { count, near, max, keep } = limitsFor(kind, metres);
      wasm.set_limits(index, count, near, max, keep);
    }
  }
  applyLimits(radius);

  const flagFor = (kind) => ({
    get: () => wasm.get_enabled(KINDS[kind]) !== 0,
    set: (on) => wasm.set_enabled_flag(KINDS[kind], on ? 1 : 0),
    enumerable: true,
  });
  const enabled = Object.defineProperties({}, { cars: flagFor('cars'), peds: flagFor('peds') });

  return {
    cars,
    peds,
    fallen, // { ped, x, z, vx, vz, age }: knocked over
    enabled,
    stats: {
      get stuck() { return wasm.stat_stuck(); },
      get hits() { return wasm.stat_hits(); },
    },
    get time() { return wasm.sim_time(); },
    get radius() { return radius; },
    // Change how far out everything lives. Extra ones, furthest first, go at once.
    setRadius(metres) {
      radius = metres;
      applyLimits(metres);
      wasm.trim_to_limits();
      sync();
    },
    set(kind, on) {
      wasm.set_enabled(KINDS[kind], on ? 1 : 0);
      sync();
    },
    // The traffic light at (x, z) has been knocked down, or stood up again.
    setPole(x, z, down) { wasm.set_pole(x, z, down ? 1 : 0); },
    // How many cars are out of the simulation but still count towards the total.
    set crashed(count) { wasm.set_crashed(count); },
    // A recovered car rejoins the traffic on the segment starting at (i, j).
    // It keeps its planned turn if it is still heading for the same junction.
    returnCar(route, { i, j, s, offset }, speed, shade) {
      const done = wasm.return_car(route.i, route.j, ...route.dir, ...route.next,
        TURNS.indexOf(route.turn), route.replan ? 1 : 0, route.cruise, i, j, s, offset, speed, shade);
      sync();
      return done !== 0;
    },
    // A driver thrown out of their car, who stays where they fall.
    dropDriver({ x, z, hx, hz, vx, vz, shade }) {
      const done = wasm.drop_driver(x, z, hx, hz, vx, vz, shade);
      sync();
      return done !== 0;
    },
    // Something frightening at (x, z): people within `reach` of it run away.
    alarm(x, z, reach) { wasm.alarm(x, z, reach); },
    // The nearest standing person on a ray: { ped, distance, head } or null.
    raycast(from, dir, reach) {
      const slot = wasm.raycast(from.x, from.y, from.z, dir.x, dir.y, dir.z, reach);
      if (slot < 0) return null;
      const ped = known.peds.get(pedRow(slot)[0]);
      return { ped, distance: rayData[0], head: rayData[1] !== 0 };
    },
    // Hurt someone, standing or down. At no HP they drop and stay down.
    damage(ped, amount, { head = false, vx = 0, vz = 0 } = {}) {
      const hp = wasm.damage(ped.id, amount * (head ? HEADSHOT : 1), vx, vz);
      sync();
      return hp < 0 ? ped.hp : hp;
    },
    // Hand a car over to someone else (it has been crashed into).
    takeCar(car) {
      wasm.take_car(car.id);
      sync();
    },
    // Make room where the player is about to appear.
    clearNear(position, distance = 10) {
      wasm.clear_near(position.x, position.z, distance);
      sync();
    },
    // focus: the player. obstacles: things on the ground, { x, z, r, speed }.
    // Vehicles also give { vehicle: true, hx, hz, halfL, halfW, vx, vz }.
    // poles: knocked down lights, { ax, az, bx, bz, avx, avy, avz, bvx, bvy, bvz }.
    step(dt, focus, obstacles = [], poles = []) {
      const loose = Math.min(poles.length, wasm.loose_capacity());
      for (let n = 0; n < loose; n++) {
        LOOSE_FIELDS.forEach((name, k) => {
          looseData[n * LOOSE_FIELDS.length + k] = poles[n][name] ?? 0;
        });
      }
      const count = Math.min(obstacles.length, wasm.obstacle_capacity());
      for (let n = 0; n < count; n++) {
        const o = obstacles[n];
        OBSTACLE_FIELDS.forEach((name, k) => {
          obstacleData[n * OBSTACLE_FIELDS.length + k] = Number(o[name] ?? 0);
        });
      }
      wasm.step(dt, focus.x, focus.z, count, loose);
      sync();
    },
  };
}
