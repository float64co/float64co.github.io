// NPC cars and pedestrians on the road grid. No three.js or Rapier, no Math.random.
// Junction (i, j) is at (i * PITCH, j * PITCH). Traffic keeps to the left.
import { BLOCKS, PITCH, ROAD, SEED, rng } from './city.js';
import { signal, CLEAR } from './signals.js';
import { PED_HP, HEADSHOT } from './weapons.js';

// One radius around the player for cars, people and traffic lights (metres).
export const RADIUS = { default: 450, min: 100, max: 600 };
// Counts grow with the area, so a bigger radius is just as crowded.
const DENSITY = { cars: { count: 180, at: 300 }, peds: { count: 270, at: 150 } };
const NEAR_RATIO = 0.23; // arrivals appear at least this far out
const KEEP_RATIO = 1.23; // and are removed beyond this
const DETAIL = 120;      // metres: further off, people update less often and don't dodge each other
const SKIP = 4;

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
  fallen: 16,
};
export const LANE = 3.5;            // lane centre, metres left of the road centre line
export const BOX = ROAD / 2 + 4;    // junction half-size: stop lines clear the crosswalks
const STRAIGHT = PITCH - 2 * BOX;   // lane length between junctions
const MAX = BLOCKS / 2 - 1;         // outermost junction traffic uses
const PAVE = PITCH / 2 - 8.5;       // pavement path, from the block centre
const ACCEL = 3;
const DECEL = 6;
const NOSE = 2;                     // half a car's length
const BUMP = 1.5;                   // m/s: a vehicle moving faster than this knocks people over
const DOWN = 6;                     // seconds before they get up again
const DEAD = 20;                    // seconds before a body is cleared away
const PERSONAL = 0.7;               // metres between two people
const SIDESTEP = 0.9;               // m/s
const STARVED = 30;                 // seconds waiting before a car gets priority
const PATIENCE = 100;               // seconds stuck before a distant car is removed; over two light cycles

const inside = (i, j) => Math.abs(i) <= MAX && Math.abs(j) <= MAX;
const right = ([dx, dz]) => [-dz || 0, dx];
const left = ([dx, dz]) => [dz, -dx || 0];
const dist = (a, b) => Math.hypot(a.x - b.x, a.z - b.z);

// Point on the lane leaving junction (i, j) in direction d, s metres past its box.
function lanePoint(i, j, d, along) {
  const side = left(d);
  return { x: i * PITCH + d[0] * along + side[0] * LANE, z: j * PITCH + d[1] * along + side[1] * LANE };
}

// Buckets things by position, so each one only checks its neighbours.
const CELL = 32; // metres; at least as far as a car looks ahead
const NEAR = 4;  // fine cells for things only a few metres apart
const cellKey = (x, z, cell) => (Math.floor(x / cell) + 4000) * 16000 + Math.floor(z / cell) + 4000;

function gridOf(list, cell = CELL) {
  const grid = new Map();
  for (const item of list) {
    const key = cellKey(item.x, item.z, cell);
    if (!grid.has(key)) grid.set(key, []);
    grid.get(key).push(item);
  }
  return grid;
}

// Calls visit for everything in the 3x3 cells around (x, z).
function around(grid, x, z, visit, cell = CELL) {
  for (let a = -1; a <= 1; a++)
    for (let b = -1; b <= 1; b++) {
      const bucket = grid.get(cellKey(x + a * cell, z + b * cell, cell));
      if (bucket) for (const item of bucket) visit(item);
    }
}

export function createTrafficSim(seed = SEED) {
  const random = rng(seed ^ 0x5eed);
  const pick = (list) => list[Math.floor(random() * list.length)];
  const cars = [];
  const peds = [];
  const fallen = []; // { ped, x, z, vx, vz, age }: knocked over
  const boxes = new Map(); // junction -> cars inside it
  const priority = new Map(); // junction -> car that has been kept out too long
  const enabled = { cars: true, peds: true };
  let radius = RADIUS.default;
  const limits = { cars: limitsFor('cars', radius), peds: limitsFor('peds', radius) };
  let tick = 0;
  let centre = { x: 0, z: 0 }; // where the player was last
  const stats = { stuck: 0, hits: 0 };
  let time = 0;
  let filled = { cars: false, peds: false };
  let nextId = 0;
  let crashed = 0; // cars out of the simulation that still count towards the total
  let carGrid = new Map();
  let pedGrid = new Map();
  let pedsClose = new Map();
  let carsClose = new Map();

  // ---- cars ----

  // Where to go at the far junction of this car's lane.
  function plan(car) {
    const [i, j] = [car.i + car.dir[0], car.j + car.dir[1]];
    const options = [
      [car.dir, 'straight', 6], [right(car.dir), 'right', 2], [left(car.dir), 'left', 2],
    ].filter(([d]) => inside(i + d[0], j + d[1]));
    let roll = random() * options.reduce((sum, o) => sum + o[2], 0);
    [car.next, car.turn] = options.find((o) => (roll -= o[2]) < 0) ?? options[0];
  }

  // The path through the junction: a quadratic curve from lane to lane.
  function curve(car) {
    const [i, j] = [car.i + car.dir[0], car.j + car.dir[1]];
    const a = lanePoint(i, j, car.dir, -BOX);
    const c = lanePoint(i, j, car.next, BOX);
    const r = left(car.dir);
    const r2 = left(car.next);
    const b = car.turn === 'straight'
      ? { x: (a.x + c.x) / 2, z: (a.z + c.z) / 2 }
      : { x: i * PITCH + (r[0] + r2[0]) * LANE, z: j * PITCH + (r[1] + r2[1]) * LANE };
    let length = 0;
    let previous = a;
    for (let n = 1; n <= 8; n++) {
      const point = bezier(a, b, c, n / 8);
      length += dist(previous, point);
      previous = point;
    }
    return { a, b, c, length, key: `${i},${j}` };
  }

  function bezier(a, b, c, t) {
    const u = 1 - t;
    return {
      x: u * u * a.x + 2 * u * t * b.x + t * t * c.x,
      z: u * u * a.z + 2 * u * t * b.z + t * t * c.z,
    };
  }

  function place(car) {
    if (!car.path) {
      Object.assign(car, lanePoint(car.i, car.j, car.dir, BOX + car.s));
      // A car that has just rejoined eases the rest of the way onto its lane.
      if (car.drift) {
        car.x += car.drift * Math.abs(car.dir[1]);
        car.z += car.drift * Math.abs(car.dir[0]);
      }
      car.hx = car.dir[0];
      car.hz = car.dir[1];
      return;
    }
    const { a, b, c, length } = car.path;
    const t = Math.min(1, car.s / length);
    Object.assign(car, bezier(a, b, c, t));
    const hx = (1 - t) * (b.x - a.x) + t * (c.x - b.x);
    const hz = (1 - t) * (b.z - a.z) + t * (c.z - b.z);
    const size = Math.hypot(hx, hz) || 1;
    car.hx = hx / size;
    car.hz = hz / size;
  }

  // Can two cars from these approaches be in a junction together?
  function compatible(car, other, from = other.dir) {
    const same = from[0] === car.dir[0] && from[1] === car.dir[1];
    const opposite = from[0] === -car.dir[0] && from[1] === -car.dir[1];
    return same || (opposite && other.turn !== 'right' && car.turn !== 'right');
  }

  // May this car enter the junction ahead?
  function mayEnter(car) {
    // Only the front of a queue may claim the junction.
    let queued = false;
    around(carGrid, car.x, car.z, (o) => {
      queued ||= o !== car && !o.path && !o.cleared && o.s > car.s
        && o.i === car.i && o.j === car.j && o.dir[0] === car.dir[0] && o.dir[1] === car.dir[1];
    });
    if (queued) return false;
    const [i, j] = [car.i + car.dir[0], car.j + car.dir[1]];
    if (signal(i, j, car.dir[0] ? 0 : 1, time).colour !== 'green') return false;
    // A car kept out too long goes first, ahead of anyone who would block it again.
    const key = `${i},${j}`;
    const first = priority.get(key);
    if (first && first !== car && !first.dir[0] === !car.dir[0] && !compatible(car, first)) return false;
    // Only share the junction with cars it cannot cross.
    for (const other of boxes.get(key) ?? []) {
      if (compatible(car, other.car, other.from)) continue;
      if (car.waited > STARVED && !first) priority.set(key, car);
      return false;
    }
    // Don't block the box: the exit must have room.
    const exit = lanePoint(i, j, car.next, BOX + 3);
    let full = false;
    around(carGrid, exit.x, exit.z, (other) => { full ||= other !== car && dist(other, exit) < 5; });
    return !full;
  }

  function reserve(car) {
    const key = `${car.i + car.dir[0]},${car.j + car.dir[1]}`;
    if (!boxes.has(key)) boxes.set(key, []);
    boxes.get(key).push({ car, from: car.dir });
    if (priority.get(key) === car) priority.delete(key);
    car.holds.push(key);
  }

  function release(car, key) {
    const list = boxes.get(key)?.filter((entry) => entry.car !== car);
    if (list?.length) boxes.set(key, list);
    else boxes.delete(key);
    car.holds = car.holds.filter((k) => k !== key);
  }

  function removeCar(index) {
    const car = cars[index];
    for (const key of [...car.holds]) release(car, key);
    for (const [key, waiting] of priority) if (waiting === car) priority.delete(key);
    cars.splice(index, 1);
  }

  // Free distance ahead of the car before something is in its way.
  function clearAhead(car, obstacles) {
    let free = Infinity;
    const check = (o, radius) => {
      const dx = o.x - car.x;
      const dz = o.z - car.z;
      const ahead = dx * car.hx + dz * car.hz;
      if (ahead <= 0 || ahead > 30) return;
      if (Math.abs(dz * car.hx - dx * car.hz) > radius + 1.1) return;
      free = Math.min(free, ahead - NOSE - radius - 1);
    };
    around(carGrid, car.x, car.z, (other) => {
      // Oncoming cars are in their own lane; don't brake for them.
      if (other !== car && other.hx * car.hx + other.hz * car.hz >= -0.7) check(other, 1.5);
    });
    around(pedGrid, car.x, car.z, (ped) => { if (ped.onRoad) check(ped, 0.4); });
    for (const body of fallen) check(body, 0.9);
    for (const obstacle of obstacles) check(obstacle, obstacle.r);
    return Math.max(0, free);
  }

  function stepCar(car, dt, obstacles) {
    const length = car.path ? car.path.length : STRAIGHT;
    let free = clearAhead(car, obstacles);

    if (!car.path && !car.cleared && car.s > STRAIGHT - 14) {
      const toLine = STRAIGHT - NOSE - car.s;
      if (toLine < 5 && mayEnter(car)) {
        car.cleared = true;
        reserve(car);
      } else free = Math.min(free, Math.max(0, toLine));
    }

    car.speed = Math.min(car.speed + ACCEL * dt, car.cruise, Math.sqrt(2 * DECEL * free));
    if (car.path) car.speed = Math.min(car.speed, 6);
    car.s += car.speed * dt;
    if (car.drift) car.drift = Math.sign(car.drift) * Math.max(0, Math.abs(car.drift) - car.speed * dt * 0.2);
    car.waited = car.speed < 0.3 ? car.waited + dt : 0;

    // Leaving a junction frees it once the tail is clear.
    if (!car.path && car.s > NOSE + 1)
      for (const key of [...car.holds]) if (!car.cleared) release(car, key);

    if (car.s >= length) {
      car.s -= length;
      if (car.path) {
        car.i += car.dir[0];
        car.j += car.dir[1];
        car.dir = car.next;
        car.path = null;
        car.cleared = false;
        plan(car);
      } else if (car.cleared) {
        car.path = curve(car);
        car.drift = 0;
      } else car.s = length; // held at the line
    }
    place(car);
  }

  function spawnCar(focus, obstacles, near) {
    const { max, keep } = limits.cars;
    const dir = pick([[1, 0], [-1, 0], [0, 1], [0, -1]]);
    const span = Math.ceil(max / PITCH);
    const around = (v) => Math.round(v / PITCH) + Math.floor(random() * (2 * span + 1)) - span;
    const car = {
      id: nextId++, i: around(focus.x), j: around(focus.z), dir, s: random() * (STRAIGHT - 16),
      speed: 0, cruise: 8 + random() * 5, path: null, cleared: false, holds: [], waited: 0,
      shade: random(),
    };
    if (!inside(car.i, car.j) || !inside(car.i + dir[0], car.j + dir[1])) return;
    place(car);
    const d = dist(car, focus);
    if (d < near || d > max || d > keep) return;
    if (cars.some((other) => dist(other, car) < 10)) return;
    if (obstacles.some((o) => dist(o, car) < 20)) return;
    plan(car);
    car.speed = car.cruise / 2;
    cars.push(car);
  }

  // ---- pedestrians ----

  // A node is a pavement corner: block (bi, bj) and which corner (sx, sz).
  const corner = ({ bi, bj, sx, sz }) => ({
    x: (bi + 0.5) * PITCH + sx * PAVE, z: (bj + 0.5) * PITCH + sz * PAVE,
  });
  const validBlock = (bi, bj) => bi >= -MAX && bi < MAX && bj >= -MAX && bj < MAX;
  const sameNode = (a, b) => a.bi === b.bi && a.bj === b.bj && a.sx === b.sx && a.sz === b.sz;

  function nextNode(node, previous) {
    const { bi, bj, sx, sz } = node;
    const options = [
      { bi, bj, sx: -sx, sz }, { bi, bj, sx, sz: -sz },
      { bi: bi + sx, bj, sx: -sx, sz, cross: 0 }, { bi, bj: bj + sz, sx, sz: -sz, cross: 1 },
    ].filter((n) => validBlock(n.bi, n.bj) && !(previous && sameNode(n, previous)));
    // Mostly stay on the pavement.
    const along = options.filter((n) => n.cross === undefined);
    return along.length && random() < 0.75 ? pick(along) : pick(options);
  }

  function stepPed(ped, dt, obstacles, far) {
    const a = corner(ped.from);
    const b = corner(ped.to);
    const length = dist(a, b);
    let walking = true;

    // At the kerb, wait for a light that leaves time to get across.
    if (ped.to.cross !== undefined && ped.s === 0) {
      const { bi, bj, sx, sz } = ped.from;
      const light = signal(bi + (sx > 0), bj + (sz > 0), ped.to.cross, time);
      walking = light.colour !== 'red' && light.left + CLEAR >= length / ped.pace;
    }
    if (walking) {
      ped.s += ped.pace * dt;
      ped.stride += ped.pace * dt * 3;
    }
    ped.walking = walking;

    const t = Math.min(1, ped.s / length);
    const path = { x: a.x + (b.x - a.x) * t, z: a.z + (b.z - a.z) * t };
    ped.hx = (b.x - a.x) / length;
    ped.hz = (b.z - a.z) / length;

    // Avoid each other: keep right to pass, and never stand in the same spot.
    let threatened = false;
    if (!far) around(pedsClose, path.x + ped.ox, path.z + ped.oz, (other) => {
      if (other === ped) return;
      let dx = path.x + ped.ox - other.x;
      let dz = path.z + ped.oz - other.z;
      const d = Math.hypot(dx, dz);
      if (d > 2.2) return;
      const ahead = -(dx * ped.hx + dz * ped.hz);
      const across = Math.abs(dz * ped.hx - dx * ped.hz);
      if (walking && ahead > 0 && across < 0.8) {
        ped.ox -= ped.hz * SIDESTEP * dt;
        ped.oz += ped.hx * SIDESTEP * dt;
        threatened = true;
      }
      if (d >= PERSONAL) return;
      // Too close: each moves half the overlap. Coincident pairs split by id.
      if (d < 1e-3) [dx, dz] = ped.id < other.id ? [1, 0] : [-1, 0];
      else [dx, dz] = [dx / d, dz / d];
      ped.ox += dx * (PERSONAL - d) / 2;
      ped.oz += dz * (PERSONAL - d) / 2;
      threatened = true;
    }, NEAR);
    // Step round the player on foot and standing vehicles.
    for (const o of obstacles) {
      if (o.vehicle && o.speed > BUMP) continue; // too quick to dodge
      const reach = o.r + 0.5;
      let dx = path.x + ped.ox - o.x;
      let dz = path.z + ped.oz - o.z;
      const d = Math.hypot(dx, dz);
      if (d >= reach) continue;
      if (d < 1e-3) [dx, dz] = [-ped.hz, ped.hx];
      else [dx, dz] = [dx / d, dz / d];
      ped.ox += dx * (reach - d);
      ped.oz += dz * (reach - d);
      threatened = true;
    }
    // Walk around cars rather than through them.
    around(carsClose, path.x + ped.ox, path.z + ped.oz, (car) => {
      const dx = path.x + ped.ox - car.x;
      const dz = path.z + ped.oz - car.z;
      const ahead = dx * car.hx + dz * car.hz;
      const across = dz * car.hx - dx * car.hz;
      const overAhead = NOSE + 0.35 - Math.abs(ahead);
      const overAcross = 1.35 - Math.abs(across);
      if (overAhead <= 0 || overAcross <= 0) return;
      // Leave by the nearest face.
      if (overAhead < overAcross) {
        ped.ox += car.hx * overAhead * (Math.sign(ahead) || 1);
        ped.oz += car.hz * overAhead * (Math.sign(ahead) || 1);
      } else {
        ped.ox -= car.hz * overAcross * (Math.sign(across) || 1);
        ped.oz += car.hx * overAcross * (Math.sign(across) || 1);
      }
      threatened = true;
    }, NEAR);
    // Drift back to the path, no faster than walking.
    const strayed = Math.hypot(ped.ox, ped.oz);
    if (!threatened && strayed > 0) {
      const back = Math.min(strayed * 0.8, ped.pace) * dt;
      ped.ox -= ped.ox / strayed * back;
      ped.oz -= ped.oz / strayed * back;
    }
    ped.x = path.x + ped.ox;
    ped.z = path.z + ped.oz;
    const crossing = ped.to.cross !== undefined && ped.s > 1 && ped.s < length - 1;
    ped.onRoad = crossing || Math.hypot(ped.ox, ped.oz) > 1;

    if (t === 1) {
      const previous = ped.from;
      ped.from = ped.to;
      ped.to = nextNode(ped.from, previous);
      ped.s = 0;
    }
  }

  function spawnPed(focus, near) {
    const { max } = limits.peds;
    const span = Math.ceil(max / PITCH);
    const around = (v) => Math.floor(v / PITCH) + Math.floor(random() * (2 * span + 1)) - span;
    const from = { bi: around(focus.x), bj: around(focus.z), sx: pick([-1, 1]), sz: pick([-1, 1]) };
    if (!validBlock(from.bi, from.bj)) return;
    // Start part-way along a pavement edge.
    const to = pick([{ ...from, sx: -from.sx }, { ...from, sz: -from.sz }]);
    const a = corner(from);
    const b = corner(to);
    const t = random() * 0.95;
    const x = a.x + (b.x - a.x) * t;
    const z = a.z + (b.z - a.z) * t;
    const d = Math.hypot(x - focus.x, z - focus.z);
    if (d < near || d > max) return;
    let crowded = false;
    around(pedsClose, x, z, (other) => { crowded ||= Math.hypot(other.x - x, other.z - z) < 1; }, NEAR);
    if (crowded) return;
    const ped = {
      id: nextId++, from, to, s: t * dist(a, b), x, z, ox: 0, oz: 0, hx: 1, hz: 0,
      hp: PED_HP, pace: 1.3 + random() * 0.5, stride: random() * 6, shade: random(), onRoad: false, walking: true,
    };
    peds.push(ped);
    const key = cellKey(x, z, NEAR);
    if (!pedsClose.has(key)) pedsClose.set(key, []);
    pedsClose.get(key).push(ped);
  }

  // ---- being knocked over ----

  // Is the pedestrian touching this moving vehicle?
  function struck(ped, o) {
    if (!o.vehicle || o.speed <= BUMP) return false;
    const dx = ped.x - o.x;
    const dz = ped.z - o.z;
    // A little early, more so at speed, so fast vehicles can't skip past.
    return Math.abs(dx * o.hx + dz * o.hz) < o.halfL + 0.3 + o.speed / 30
      && Math.abs(dz * o.hx - dx * o.hz) < o.halfW + 0.3;
  }

  function knockOver(index, o) {
    const [ped] = peds.splice(index, 1);
    if (fallen.length >= LIMITS.fallen) fallen.shift();
    fallen.push({ ped, x: ped.x, z: ped.z, vx: o.vx, vz: o.vz, age: 0 });
    stats.hits++;
  }

  // Distance along a ray (unit direction) to a standing person, and where it hit.
  function rayHit(ped, from, dir, reach) {
    let best = null;
    // Head: a sphere.
    const hx = ped.x - from.x;
    const hy = 1.6 - from.y;
    const hz = ped.z - from.z;
    const along = hx * dir.x + hy * dir.y + hz * dir.z;
    const miss = hx * hx + hy * hy + hz * hz - along * along;
    if (miss < 0.19 ** 2) best = { distance: along - Math.sqrt(0.19 ** 2 - miss), head: true };
    // Body and legs: an upright cylinder from the ground to the shoulders.
    const flat = dir.x ** 2 + dir.z ** 2;
    if (flat > 1e-9) {
      const middle = (hx * dir.x + hz * dir.z) / flat;
      const off = (hx - dir.x * middle) ** 2 + (hz - dir.z * middle) ** 2;
      if (off < 0.28 ** 2) {
        const distance = middle - Math.sqrt((0.28 ** 2 - off) / flat);
        const height = from.y + dir.y * distance;
        if (height >= 0 && height <= 1.4 && !(best && best.distance < distance))
          best = { distance, head: false };
      }
    }
    return best && best.distance > 0 && best.distance < reach ? best : null;
  }

  // Back on their feet where they landed, then they walk back to the path.
  function getUp(entry) {
    const { ped } = entry;
    const ox = entry.x - (ped.x - ped.ox);
    const oz = entry.z - (ped.z - ped.oz);
    if (Math.hypot(ox, oz) > 8) return; // thrown too far; someone new will spawn
    Object.assign(ped, { x: entry.x, z: entry.z, ox, oz, onRoad: true });
    peds.push(ped);
  }

  function stepFallen(dt, focus, obstacles) {
    for (let n = fallen.length - 1; n >= 0; n--) {
      const entry = fallen[n];
      entry.age += dt;
      const gone = dist(entry, focus) > limits.peds.keep;
      // Don't stand up underneath a vehicle.
      const covered = obstacles.some((o) => o.vehicle
        && Math.abs((entry.x - o.x) * o.hx + (entry.z - o.z) * o.hz) < o.halfL + 0.15
        && Math.abs((entry.z - o.z) * o.hx - (entry.x - o.x) * o.hz) < o.halfW + 0.15);
      const dead = entry.ped.hp <= 0;
      if (!gone && (dead ? entry.age < DEAD : entry.age < DOWN || covered)) continue;
      fallen.splice(n, 1);
      if (!gone && !dead) getUp(entry);
    }
  }

  // ---- both ----

  function populate(kind, list, focus, spawn, away = 0) {
    const { near, count } = limits[kind];
    // The first fill may be close to the player; later arrivals appear further off.
    const closest = filled[kind] ? near : 15;
    // Top up quickly if far short, as after the radius grows.
    const attempts = !filled[kind] ? count * 20 : Math.max(12, count - list.length);
    for (let n = 0; n < attempts && list.length + away < count; n++) spawn(closest);
    filled[kind] = true;
  }

  return {
    cars,
    peds,
    fallen,
    enabled,
    get radius() { return radius; },
    stats,
    // Change how far out everything lives. Extra ones, furthest first, go at once.
    setRadius(metres) {
      radius = metres;
      for (const [kind, list] of [['cars', cars], ['peds', peds]]) {
        limits[kind] = limitsFor(kind, metres);
        const { count, keep } = limits[kind];
        const nearest = [...list].sort((a, b) => dist(a, centre) - dist(b, centre));
        const stay = new Set(nearest.slice(0, count).filter((item) => dist(item, centre) <= keep));
        for (let n = list.length - 1; n >= 0; n--) {
          if (stay.has(list[n])) continue;
          if (kind === 'cars') removeCar(n);
          else list.splice(n, 1);
        }
      }
    },
    get time() { return time; },
    set(kind, on) {
      enabled[kind] = on;
      if (on) return;
      filled[kind] = false;
      if (kind === 'cars') while (cars.length) removeCar(cars.length - 1);
      else peds.length = fallen.length = 0;
    },
    set crashed(count) { crashed = count; },
    // A recovered car rejoins the traffic on the segment starting at (i, j).
    // It keeps its planned turn if it is still heading for the same junction.
    returnCar(route, { i, j, s, offset }, speed, shade) {
      if (!inside(i, j) || !inside(i + route.dir[0], j + route.dir[1])) return false;
      const car = {
        id: nextId++, i, j, dir: route.dir, s, speed, cruise: route.cruise, path: null,
        cleared: false, holds: [], waited: 0, shade, drift: offset,
        next: route.next, turn: route.turn,
      };
      place(car);
      if (cars.some((other) => dist(other, car) < 6)) return false;
      if (route.replan || i !== route.i || j !== route.j) plan(car);
      cars.push(car);
      return true;
    },
    // The nearest standing person on a ray: { ped, distance, head } or null.
    raycast(from, dir, reach) {
      let nearest = null;
      for (const ped of peds) {
        const hit = rayHit(ped, from, dir, nearest?.distance ?? reach);
        if (hit) nearest = { ped, ...hit };
      }
      return nearest;
    },
    // Hurt someone, standing or down. At no HP they drop and stay down.
    damage(ped, amount, { head = false, vx = 0, vz = 0 } = {}) {
      ped.hp = Math.max(0, ped.hp - amount * (head ? HEADSHOT : 1));
      const index = peds.indexOf(ped);
      if (ped.hp === 0 && index >= 0) knockOver(index, { vx, vz });
      return ped.hp;
    },
    // Hand a car over to someone else (it has been crashed into).
    takeCar(car) {
      const index = cars.indexOf(car);
      if (index >= 0) removeCar(index);
    },
    // Make room where the player is about to appear.
    clearNear(position, radius = 10) {
      for (let n = cars.length - 1; n >= 0; n--) if (dist(cars[n], position) < radius) removeCar(n);
    },
    // focus: the player. obstacles: things on the ground, { x, z, r, speed }.
    // Vehicles also give { vehicle: true, hx, hz, halfL, halfW, vx, vz }.
    step(dt, focus, obstacles = []) {
      time += dt;
      centre = focus;
      tick++;
      carGrid = gridOf(cars);
      pedGrid = gridOf(peds);
      pedsClose = gridOf(peds.filter((ped) => dist(ped, focus) <= DETAIL + 5), NEAR);
      carsClose = gridOf(cars, NEAR);
      for (let n = cars.length - 1; n >= 0; n--) {
        const car = cars[n];
        const away = dist(car, focus);
        const hopeless = car.waited > PATIENCE && away > 40;
        if (hopeless) stats.stuck++;
        if (away > limits.cars.keep || hopeless) removeCar(n);
        else stepCar(car, dt, obstacles);
      }
      for (let n = peds.length - 1; n >= 0; n--) {
        if (dist(peds[n], focus) > limits.peds.keep) {
          peds.splice(n, 1);
          continue;
        }
        // Far away, take every SKIP-th turn with a longer step.
        const far = dist(peds[n], focus) > DETAIL;
        if (far && (tick + peds[n].id) % SKIP) continue;
        stepPed(peds[n], far ? dt * SKIP : dt, obstacles, far);
        const hit = obstacles.find((o) => struck(peds[n], o));
        if (hit) knockOver(n, hit);
      }
      stepFallen(dt, focus, obstacles);
      if (enabled.cars) populate('cars', cars, focus, (near) => spawnCar(focus, obstacles, near), crashed);
      if (enabled.peds) populate('peds', peds, focus, (near) => spawnPed(focus, near), fallen.length);
    },
  };
}
