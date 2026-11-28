// Police cars: real physics cars driven towards the player along the roads.
// Each carries an officer, who gets out and arrests a stopped player by hand.
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { PITCH, SIZE, rng } from './city.js';
import { createGroundVehicle } from './ground.js';
import { createOfficer, nearestOn, separate, sideDoor, beside, pushOut, OFFICER } from './officers.js';
import { createGunner } from './gunner.js';
import { wallsBetween } from './walls.js';
import { POLICE_GUNS, DRAW, COVER, gunFor, hitChance } from './police-guns.js';

const FOR_LEVEL = [0, 1, 2, 3, 4, 6]; // cars sent for each number of stars
export const POLICE = { cars: 6, speed: 28, maxCars: 10 };
const SPAWN = [80, 140];  // metres away that new ones set off from
const GONE = 240;         // further than this they are replaced
const SIGHT = 60;         // metres at which they can see you
const AIRBORNE = 8;       // metres up: higher than this you are flying, and so are they
const STAND_OFF = 9;      // metres: how close a flying police car comes
const REACH_UP = 20;      // and no higher than this
const ARREST = 1.2;       // metres from you, or your vehicle, to take hold
const HELD = 3;           // seconds an officer must keep hold
const PULL = 1.5;         // seconds at the door to drag someone out of a vehicle
const SLOW = 2;           // m/s: slow enough to be approached on foot
const FLEEING = 4;        // m/s: too fast to chase on foot
const GET_OUT = 12;       // metres: park and walk from here
const STUCK_OUT = 35;     // or from here, if the car can get no closer
const FAR_ON_FOOT = 45;   // further than this an officer goes back to the car
const OPEN_FIRE = 20;     // metres: armed officers get out to shoot someone on foot
const TRACE = 0.06;       // seconds a shot's tracer shows
const HOSTILE = 4;        // seconds someone counts as attacking after the last of it
const FORGET = 150;       // metres: someone lying further off than this is cleared away
const MOST_HUSKS = 8;     // burnt police cars kept about; the oldest goes first
export const LIVERY = { body: 0x15181d, cabin: 0xf2f2f2 };
const ROAD_HALF = 8;      // this close to a centre line counts as on that road

const line = (v) => Math.round(v / PITCH) * PITCH;

// Where to steer for, to get from p to goal along the roads. `turn` is set when
// that is a corner to slow down for, and not the goal itself.
export function waypoint(p, goal) {
  const x = line(p.x);
  const z = line(p.z);
  const onV = Math.abs(p.x - x) < ROAD_HALF; // a road running along z
  const onH = Math.abs(p.z - z) < ROAD_HALF; // a road running along x
  const gx = line(goal.x);
  const gz = line(goal.z);
  const goalV = Math.abs(goal.x - gx) <= Math.abs(goal.z - gz);
  const sameV = onV && x === gx && Math.abs(goal.x - gx) < ROAD_HALF + 2;
  const sameH = onH && z === gz && Math.abs(goal.z - gz) < ROAD_HALF + 2;
  if (sameV || sameH) return { x: goal.x, z: goal.z, turn: null };
  if (!onV && !onH) {
    return Math.abs(p.x - x) < Math.abs(p.z - z)
      ? { x, z: p.z, turn: null } : { x: p.x, z, turn: null };
  }
  // Along x or z, aiming a little way ahead on the centre line so as not to drift.
  const along = (corner, axis) => {
    const ahead = (from, to) => from + Math.max(-14, Math.min(14, to - from));
    return axis === 'x'
      ? { x: ahead(p.x, corner.x), z: corner.z, turn: corner }
      : { x: corner.x, z: ahead(p.z, corner.z), turn: corner };
  };
  // Get on to a road that crosses the goal's, then on to the goal's own.
  if (goalV) return onH ? along({ x: gx, z }, 'x') : along({ x, z: gz }, 'z');
  return onV ? along({ x, z: gz }, 'z') : along({ x: gx, z }, 'x');
}

// The nearest point of a road to someone who is well off all of them, or null.
export function kerbside(p) {
  const dx = Math.abs(p.x - line(p.x));
  const dz = Math.abs(p.z - line(p.z));
  if (Math.min(dx, dz) < ROAD_HALF + 1) return null;
  return dx < dz ? { x: line(p.x), z: p.z } : { x: p.x, z: line(p.z) };
}

// Controls for one police car. pose and player: { x, z, hx, hz, speed }.
// others: [{ x, z }] to avoid driving into. memory persists between steps.
export function chase(pose, player, others, top, memory, dt) {
  const away = Math.hypot(player.x - pose.x, player.z - pose.z);
  const lead = Math.min(0.5, away / 60);
  // Someone on foot away from any road is got to by parking at the nearest point
  // of one; the officer walks from there.
  const parkAt = player.footprint ? null : kerbside(player);
  const goal = parkAt ?? { x: player.x + player.vx * lead, z: player.z + player.vz * lead };
  const target = away < 16 && !parkAt ? { ...goal, turn: null } : waypoint(pose, goal);
  const tx = target.x - pose.x;
  const tz = target.z - pose.z;
  // Positive angle: the target is to the left.
  const angle = Math.atan2(tx * pose.hz - tz * pose.hx, tx * pose.hx + tz * pose.hz);
  let steer = Math.max(-1, Math.min(1, angle * 2));

  let want = top;
  if (target.turn) {
    const to = Math.hypot(target.turn.x - pose.x, target.turn.z - pose.z);
    want = Math.min(want, 7 + Math.max(0, to - 8) * 0.6);
  }
  if (Math.abs(angle) > 0.5) want = Math.min(want, 8);
  // Pull up beside someone who has stopped, instead of ramming them. Someone on
  // foot is approached slower still, so as not to run them down.
  if (parkAt) {
    const to = Math.hypot(parkAt.x - pose.x, parkAt.z - pose.z);
    want = Math.min(want, Math.max(0, to - 4) * 0.45);
  } else if (!player.footprint) want = Math.min(want, Math.max(0, away - 6) * 0.45);
  else if (Math.hypot(player.vx, player.vz) < 3) want = Math.min(want, Math.max(0, away - 5) * 0.9);
  // Slow for anything else in the way.
  for (const o of others) {
    const dx = o.x - pose.x;
    const dz = o.z - pose.z;
    const ahead = dx * pose.hx + dz * pose.hz;
    const across = dz * pose.hx - dx * pose.hz; // positive: to the right
    if (ahead > 0 && ahead < 5 + Math.max(0, pose.speed) * 0.6 && Math.abs(across) < 2.2) {
      want = Math.min(want, Math.max(3, ahead - 4));
      steer = Math.max(-1, Math.min(1, steer + (across < 0 ? -0.5 : 0.5)));
    }
  }

  if (memory.reversing > 0) {
    memory.reversing -= dt;
    return { throttle: pose.speed > -5 ? -0.8 : 0, steer: -steer, brake: false };
  }
  memory.stalled = want > 2 && Math.abs(pose.speed) < 1 ? (memory.stalled ?? 0) + dt : 0;
  if (memory.stalled > 1.5) {
    memory.stalled = 0;
    memory.reversing = 1.3;
  }
  if (want < 0.5) return { throttle: 0, steer, brake: true };
  return { throttle: pose.speed < want ? 1 : 0, steer, brake: pose.speed > want + 2 };
}

// Controls for a police car in flight, making for (and holding off) the target.
// target: { x, y, z, vx, vz, stand }: stand is how close to come, for a place
// to be reached rather than a person. blocked: something solid is just ahead.
export function soar(pose, target, blocked) {
  const dx = target.x - pose.x;
  const dz = target.z - pose.z;
  const away = Math.hypot(dx, dz);
  const angle = Math.atan2(dx * pose.hz - dz * pose.hx, dx * pose.hx + dz * pose.hz);
  const speed = Math.hypot(target.vx, target.vz);
  // Hold back only from something that is itself hanging about.
  const close = away < (target.stand ?? STAND_OFF) && speed < 5;
  const climb = blocked ? 1 : target.y - pose.y;
  return {
    throttle: close || Math.abs(angle) > 1.3 ? 0 : Math.min(1, away / 25 + speed / 40),
    steer: Math.max(-1, Math.min(1, angle * 2.5)),
    brake: climb > 0.7,    // Space: up
    descend: climb < -0.7, // Shift: down
  };
}

export function createPolice(scene, physics, { footprint, overlaps }, seed = 1) {
  const random = rng(seed ^ 0x911);
  const units = []; // { car, memory, flipped, rammed, idle }
  const strays = []; // { officer }: those whose car has been taken or wrecked
  const husks = []; // police cars that have burnt, with nobody in them
  const settings = { enabled: true, cars: POLICE.cars, speed: POLICE.speed, guns: true };
  let wait = 0;
  let clock = 0;
  const tracers = []; // { line, left }
  const streak = new THREE.LineBasicMaterial({ color: 0xffe082 });

  const flat = { red: new THREE.MeshBasicMaterial(), blue: new THREE.MeshBasicMaterial() };
  const lamp = {
    white: new THREE.MeshBasicMaterial({ color: 0xfff4d0 }),
    tail: new THREE.MeshBasicMaterial({ color: 0xff2a20 }),
  };
  const bar = new THREE.BoxGeometry(0.6, 0.18, 0.35);
  const bulb = new THREE.BoxGeometry(0.35, 0.2, 0.06);

  function add(spot) {
    const car = createGroundVehicle('car', scene, physics, spot, LIVERY);
    const place = (geometry, material, x, y, z) => {
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(x, y, z);
      car.mesh.add(mesh);
      return mesh;
    };
    place(bar, flat.red, -0.35, 1.2, -0.3);
    place(bar, flat.blue, 0.35, 1.2, -0.3);
    const lamps = [-0.65, 0.65].flatMap((x) => [
      place(bulb, lamp.white, x, 0.05, 2.02), place(bulb, lamp.tail, x, 0.05, -2.02),
    ]);
    car.tune.top = settings.speed;
    car.sync();
    // officer is null while they are in the car.
    units.push({ car, lamps, officer: null, memory: {}, flipped: 0, rammed: 0, idle: 0, still: 0 });
  }
  function remove(index) {
    units[index].officer?.dispose();
    units[index].car.dispose();
    units.splice(index, 1);
  }
  const clear = () => {
    while (units.length) remove(units.length - 1);
    for (const { officer } of strays.splice(0)) officer.dispose();
    for (const car of husks.splice(0)) car.dispose();
  };

  function trace(from, to) {
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([from, to]), streak);
    line.frustumCulled = false;
    scene.add(line);
    tracers.push({ line, left: TRACE });
  }
  function fade(dt) {
    for (let n = tracers.length - 1; n >= 0; n--) {
      tracers[n].left -= dt;
      if (tracers[n].left > 0) continue;
      scene.remove(tracers[n].line);
      tracers[n].line.geometry.dispose();
      tracers.splice(n, 1);
    }
  }

  // Can a bullet get from the officer to the player? Walls and vehicles stop it.
  function inSight(from, to, body) {
    const dir = { x: to.x - from.x, y: to.y - from.y, z: to.z - from.z };
    const length = Math.hypot(dir.x, dir.y, dir.z);
    for (const k of ['x', 'y', 'z']) dir[k] /= length;
    return !physics.castRay(new RAPIER.Ray(from, dir), length, true,
      RAPIER.QueryFilterFlags.EXCLUDE_SENSORS, undefined, undefined, body);
  }

  let friends = []; // others who can be shot at, from the player's side

  // Aim at the nearest of the player's side and fire when ready. Returns the
  // damage done to the player.
  function shootAt(officer, player, dt) {
    const gun = officer.weapon;
    const from = { x: officer.x, y: 1.35 + officer.lift, z: officer.z };
    const gapTo = (o) => Math.hypot(o.x - from.x, o.y - from.y, o.z - from.z);
    const friend = friends.filter((o) => gapTo(o) < gapTo(player)).sort((a, b) => gapTo(a) - gapTo(b))
      .find((o) => inSight(from, o, player.body));
    const to = friend ? { x: friend.x, y: friend.y, z: friend.z }
      : { x: player.x, y: player.y + (player.footprint ? 0.5 : 0.3), z: player.z };
    const distance = Math.hypot(to.x - from.x, to.y - from.y, to.z - from.z);
    officer.aiming = Boolean(gun) && distance <= POLICE_GUNS[gun].range && inSight(from, to, player.body);
    if (!officer.aiming) {
      officer.cooldown = Math.max(officer.cooldown, DRAW);
      return 0;
    }
    officer.yaw = Math.atan2(to.x - from.x, to.z - from.z);
    officer.hostile = HOSTILE; // taking aim at someone is an attack on them
    officer.shooting = true;
    officer.cooldown -= dt;
    if (officer.cooldown > 0) return 0;
    officer.cooldown += POLICE_GUNS[gun].interval * (0.8 + random() * 0.4);
    const hit = random() < hitChance(gun, distance, Math.hypot(player.vx, player.vz));
    const wide = hit ? 0 : 1 + random();
    const side = random() < 0.5 ? -1 : 1;
    trace(
      new THREE.Vector3(from.x + Math.sin(officer.yaw) * 0.6, from.y, from.z + Math.cos(officer.yaw) * 0.6),
      new THREE.Vector3(to.x + Math.cos(officer.yaw) * wide * side, to.y, to.z - Math.sin(officer.yaw) * wide * side),
    );
    fired.push({ x: officer.x, z: officer.z });
    if (friend) {
      if (hit) friend.hurt(POLICE_GUNS[gun].damage);
      return 0;
    }
    return hit ? POLICE_GUNS[gun].damage * (player.footprint ? COVER : 1) : 0;
  }
  const fired = []; // where shots came from during this step
  let pulled = null; // set when an officer drags the player out
  const officers = () => [...units, ...strays].map((u) => u.officer).filter(Boolean);

  // Beside the driver's door, on whichever side is nearer to (x, z).
  function door(f, x, z) {
    return [1.6, -1.6]
      .map((side) => ({ x: f.x + f.hz * side, z: f.z - f.hx * side }))
      .sort((a, b) => Math.hypot(a.x - x, a.z - z) - Math.hypot(b.x - x, b.z - z))[0];
  }

  // Shooting from a car in flight. Returns the damage done.
  function gunnery(unit, f, player, level, dt) {
    const weapon = settings.guns && !unit.car.condition?.burnt ? gunFor(level) : null;
    const away = Math.hypot(player.x - f.x, player.y - f.y, player.z - f.z);
    if (!weapon || player.down || unit.gunnerHp <= 0 || away > POLICE_GUNS[weapon].range) {
      stow(unit);
      return 0;
    }
    if (!unit.gunner) {
      unit.gunner = createGunner(unit.car);
      unit.shooter = { cooldown: DRAW, lift: 0 };
    }
    const target = new THREE.Vector3(player.x, player.y + (player.footprint ? 0.5 : 0.3), player.z);
    const muzzle = unit.gunner.aim(target, weapon);
    // The same shooting as on foot, from where the barrel is. What matters to
    // the aim is how the two are moving relative to each other.
    Object.assign(unit.shooter, { weapon, x: muzzle.x, z: muzzle.z, lift: muzzle.y - 1.35 });
    return shootAt(unit.shooter, { ...player, vx: player.vx - f.vx, vz: player.vz - f.vz }, dt);
  }
  // The nearest leaning officer on a ray, as { unit, distance, head, point }.
  function onRay(from, dir, reach) {
    let best = null;
    for (const unit of units) {
      const found = unit.gunner?.hit(from, dir, best?.distance ?? reach);
      if (found) best = { unit, ...found };
    }
    return best;
  }
  // Hurt a leaning officer. Killed, they fall out; whoever is at the wheel
  // flies on. dir: which way the blow was going. Returns their HP.
  function wound(unit, amount, point, dir) {
    unit.gunnerHp = Math.max(0, (unit.gunnerHp ?? OFFICER.hp) - amount);
    if (unit.gunnerHp) return unit.gunnerHp;
    const officer = createOfficer(scene, physics,
      { x: point.x, z: point.z, altitude: Math.max(0, point.y - 1.2), yaw: Math.atan2(dir.x, dir.z) });
    const v = unit.car.body.linvel();
    officer.hurt(Infinity, v.x + dir.x * 3, v.z + dir.z * 3);
    officer.draw();
    strays.push({ officer });
    if (strays.length > MOST_HUSKS) strays.shift().officer.dispose();
    stow(unit);
    return 0;
  }
  function stow(unit) {
    unit.gunner?.remove();
    unit.gunner = null;
  }

  // An officer who is out of the car: arrest, shoot, give chase, or go back.
  // Returns the damage they did.
  function onFoot(unit, f, player, level, boxes, dt) {
    const { officer } = unit;
    officer.step(dt);
    officer.reaching = false;
    officer.aiming = false;
    if (officer.down) return 0;
    officer.equip(settings.guns ? gunFor(level) : null);
    if (player.down) {
      // Wait for them to get up before doing anything else.
      officer.held = 0;
      officer.moving = 0;
      return 0;
    }
    const hold = player.footprint ? nearestOn(player.footprint, officer.x, officer.z) : player;
    const gap = Math.hypot(hold.x - officer.x, hold.z - officer.z) - (player.footprint ? 0 : 0.35);
    const speed = Math.hypot(player.vx, player.vz);
    const away = Math.hypot(player.x - officer.x, player.z - officer.z);
    // Armed officers stay out against someone running, and shoot at a vehicle
    // going past close by, before giving chase again.
    const escaping = speed > FLEEING && (!officer.weapon || (player.footprint && away > 25));
    if (!level || escaping || away > FAR_ON_FOOT || player.y > 4) {
      officer.held = 0;
      officer.pulling = 0;
      if (!f) {
        officer.moving = 0; // no car to go back to
        return 0;
      }
      const seat = door(f, officer.x, officer.z);
      officer.walk(seat, boxes, dt, wallsBetween(officer, seat));
      if (Math.hypot(seat.x - officer.x, seat.z - officer.z) < 0.5) {
        officer.dispose();
        unit.officer = null;
      }
      return 0;
    }
    // Nobody can lay hands on the wearer of a vapour suit; they can still shoot.
    if (player.suit) {
      officer.held = 0;
      officer.pulling = 0;
      if (gap > ARREST) officer.walk(hold, boxes, dt, wallsBetween(officer, hold));
      else officer.face(player);
      return shootAt(officer, player, dt);
    }
    // Someone in a vehicle has to be got at through its side.
    const entry = player.footprint && sideDoor(player.footprint, officer.x, officer.z);
    if (gap > ARREST || (entry && !entry.alongside)) {
      officer.held = 0;
      officer.pulling = 0;
      // Walk to the door, or to just short of someone on foot, unless it is a
      // vehicle driving off.
      const goal = entry || hold;
      if (!(player.footprint && speed > FLEEING)) officer.walk(goal, boxes, dt, wallsBetween(officer, goal));
      else officer.moving = 0;
      return shootAt(officer, player, dt);
    }
    officer.face(player);
    officer.reaching = true;
    officer.hostile = HOSTILE;
    officer.shooting = false;
    if (entry) {
      // No arrest until they are out.
      officer.held = 0;
      officer.pulling = speed < SLOW ? officer.pulling + dt : 0;
      if (officer.pulling >= PULL) {
        officer.pulling = 0;
        // Out of that side, and away from the vehicle.
        pulled = { spot: beside(player.footprint, entry.side, 0.9), away: beside(player.footprint, entry.side, 1.9) };
      }
      return 0;
    }
    officer.pulling = 0;
    officer.held = speed < SLOW ? officer.held + dt : 0;
    return 0;
  }


  // A stretch of road out of the player's immediate surroundings, clear of cars.
  function findSpot(player, others) {
    for (let attempt = 0; attempt < 8; attempt++) {
      const distance = SPAWN[0] + random() * (SPAWN[1] - SPAWN[0]);
      const sign = random() < 0.5 ? -1 : 1;
      const vertical = random() < 0.5;
      const x = vertical ? line(player.x) : player.x + sign * distance;
      const z = vertical ? player.z + sign * distance : line(player.z);
      if (Math.max(Math.abs(x), Math.abs(z)) > SIZE / 2 - PITCH) continue;
      if (others.some((o) => Math.hypot(o.x - x, o.z - z) < 9)) continue;
      const yaw = vertical ? (sign > 0 ? Math.PI : 0) : (sign > 0 ? -Math.PI / 2 : Math.PI / 2);
      return { x, y: 0.8, z, yaw };
    }
    return null;
  }

  return {
    units,
    husks,
    // Every police car there is a body of.
    cars: () => [...units.map((u) => u.car), ...husks],
    settings,
    clear,
    officers,
    // How close to being arrested, from 0 to 1.
    get arrest() { return Math.min(1, Math.max(0, ...officers().map((o) => o.held)) / HELD); },
    // How close to being dragged out of a vehicle, from 0 to 1.
    get pulling() { return Math.min(1, Math.max(0, ...officers().map((o) => o.pulling)) / PULL); },
    // player: { x, y, z, vx, vz, footprint (or null on foot) }. others: [{ x, z }].
    // vehicles: footprints of everything an officer has to walk round.
    // player.body is the player's rigid body, which does not shield them.
    // player.parts, if given, are points along them when they are on the ground.
    // Call before physics.step(). Returns { seen, rammed, busted, damage, shots,
    // pulled }: pulled is { spot, away } if the player is being dragged out.
    update(player, level, others, vehicles, dt, allies = []) {
      friends = allies;
      const result = { seen: false, rammed: false, busted: false, damage: 0, shots: fired };
      fired.length = 0;
      pulled = null;
      for (const one of [...officers(), ...units.map((u) => u.shooter).filter(Boolean)])
        if (one.hostile > 0) one.hostile -= dt;
      const wanted = settings.enabled ? Math.min(FOR_LEVEL[level], settings.cars) : 0;
      wait -= dt;
      // The officer abandons a car that catches fire.
      for (let n = units.length - 1; n >= 0; n--) {
        const unit = units[n];
        if (unit.car.condition.hp > 0) continue;
        const f = footprint(unit.car.body);
        // One shot out of the sky has nobody getting out of it.
        const officer = unit.officer
          ?? (f.y < 3 ? createOfficer(scene, physics, door(f, player.x, player.z)) : null);
        if (officer) strays.push({ officer });
        stow(unit);
        husks.push(unit.car);
        units.splice(n, 1);
        if (husks.length > MOST_HUSKS) husks.shift().dispose();
        // Likewise the officers left on foot.
        if (strays.length > MOST_HUSKS) strays.shift().officer.dispose();
      }
      for (let n = husks.length - 1; n >= 0; n--) {
        const p = husks[n].body.translation();
        husks[n].drive({ throttle: 0, steer: 0, brake: true }, dt);
        if (Math.hypot(p.x - player.x, p.z - player.z) <= FORGET) continue;
        husks[n].dispose();
        husks.splice(n, 1);
      }
      const manned = () => units.filter((u) => !u.officer?.dead).length;
      for (let n = units.length - 1; n >= 0; n--) {
        const unit = units[n];
        const { car } = unit;
        const p = car.body.translation();
        const away = Math.hypot(p.x - player.x, p.z - player.z);
        const lying = unit.officer?.down;
        // Nobody on the ground is cleared away until the player has left them.
        if (lying && settings.enabled && Math.hypot(unit.officer.x - player.x, unit.officer.z - player.z) <= FORGET)
          continue;
        const q = car.body.rotation();
        unit.flipped = 1 - 2 * (q.x * q.x + q.z * q.z) < 0.4 ? unit.flipped + dt : 0;
        unit.idle = level ? 0 : unit.idle + dt;
        if (!settings.enabled || away > GONE || p.y < -10 || unit.flipped > 3
          || (!level && (away > 50 || unit.idle > 6)) || (level && !unit.officer?.dead && manned() > wanted))
          remove(n);
      }
      // A car whose officer is dead is no further use, and another is sent.
      if (manned() < wanted && wait <= 0) {
        const spot = findSpot(player, [...others, ...units.map((u) => u.car.body.translation())]);
        if (spot) {
          add(spot);
          wait = 2;
        }
      }

      for (let n = strays.length - 1; n >= 0; n--) {
        const { officer } = strays[n];
        const away = Math.hypot(officer.x - player.x, officer.z - player.z);
        if (away > FORGET || (!level && !officer.down && away > 50)) {
          officer.dispose();
          strays.splice(n, 1);
        }
      }
      const boxes = [
        ...vehicles,
        ...(player.footprint ? [player.footprint] : []),
        ...units.map((u) => footprint(u.car.body)),
      ];
      for (const unit of units) {
        const { car } = unit;
        const f = footprint(car.body);
        const pose = { x: f.x, z: f.z, hx: f.hx, hz: f.hz, speed: f.vx * f.hx + f.vz * f.hz };
        const away = Math.hypot(f.x - player.x, f.z - player.z);
        car.tune.top = settings.speed;
        const { officer } = unit;
        const eyes = officer ?? f;
        const up = !officer && player.y > AIRBORNE;
        if (level && !officer?.dead && (player.y < REACH_UP || car.flying)
          && Math.hypot(eyes.x - player.x, car.flying ? f.y - player.y : 0, eyes.z - player.z) < SIGHT)
          result.seen = true;

        // Take off after someone in the air, and come down when they do.
        if (level && up) car.setFlying(true);
        if (car.flying) {
          const road = Math.min(Math.abs(f.x - line(f.x)), Math.abs(f.z - line(f.z)));
          let target = player;
          if (!level || !up) {
            // Land, on a road rather than a roof.
            const alongZ = Math.abs(f.x - line(f.x)) < Math.abs(f.z - line(f.z));
            const beside = Math.hypot(player.x - f.x, player.z - f.z);
            const here = { x: f.x, y: -5, z: f.z, vx: 0, vz: 0 }; // straight down
            if (road >= 5) {
              target = { x: alongZ ? line(f.x) : f.x, y: f.y, z: alongZ ? f.z : line(f.z), vx: 0, vz: 0, stand: 1 };
            } else if (beside < 8 && level) {
              // Not on top of them: a little way along the road.
              const way = (alongZ ? f.z - player.z : f.x - player.x) < 0 ? -1 : 1;
              target = { x: alongZ ? f.x : f.x + way * 12, y: f.y, z: alongZ ? f.z + way * 12 : f.z, vx: 0, vz: 0, stand: 1 };
            } else target = beside < 30 || !level ? here : { ...player, y: f.y };
            // Near enough the ground and clear of them: cut the engines.
            if (road < 5 && f.y < 3 && (beside >= 6 || !level)) car.setFlying(false);
          }
          if (car.flying) {
            const ahead = physics.castRay(new RAPIER.Ray({ x: f.x + f.hx * 2.5, y: f.y, z: f.z + f.hz * 2.5 },
              { x: f.hx, y: 0, z: f.hz }), 25, true,
            RAPIER.QueryFilterFlags.EXCLUDE_DYNAMIC | RAPIER.QueryFilterFlags.EXCLUDE_SENSORS);
            car.drive(soar({ ...pose, y: f.y }, target, Boolean(ahead)), dt);
            if (level && !officer) result.damage += gunnery(unit, f, player, level, dt);
            else stow(unit);
            continue;
          }
        }

        stow(unit); // nobody leans out of a car on the ground
        // Stop, get out and walk once there is somebody to walk up to.
        unit.still = Math.abs(pose.speed) < 1 ? unit.still + dt : 0;
        const slow = Math.hypot(player.vx, player.vz) < SLOW && player.y < 4;
        const parkedUp = slow && f.y < 3 && Math.abs(pose.speed) < 1.5
          && (away < GET_OUT || (away < STUCK_OUT && unit.still > 2.5));
        // Someone running can be shot at, so armed officers get out for that too.
        const gunning = settings.guns && gunFor(level) && !player.footprint && player.y < 4
          && away < OPEN_FIRE && Math.abs(pose.speed) < 6;
        if (level && !officer && (parkedUp || gunning)) {
          const spot = door(f, player.x, player.z);
          pushOut(spot, wallsBetween(spot, spot, 2)); // not into a wall beside the car
          unit.officer = createOfficer(scene, physics, { ...spot, yaw: Math.atan2(f.hx, f.hz) });
          unit.officer.cooldown = DRAW;
        }
        if (unit.officer) result.damage += onFoot(unit, f, player, level, boxes, dt);
        if (unit.officer || !level) {
          car.drive({ throttle: 0, steer: 0, brake: true }, dt);
        } else {
          const obstacles = [
            ...others,
            ...units.filter((u) => u !== unit).map((u) => u.car.body.translation()),
            ...officers(),
          ];
          car.drive(chase(pose, player, obstacles, settings.speed, unit.memory, dt), dt);
        }

        // Did the player drive into them?
        unit.rammed -= dt;
        const mine = player.footprint;
        if (mine && unit.rammed <= 0 && Math.hypot(player.vx - f.vx, player.vz - f.vz) > 5
          && Math.hypot(player.vx, player.vz) > Math.hypot(f.vx, f.vz) && overlaps(mine, f, 0.4)) {
          result.rammed = true;
          unit.rammed = 3;
        }
      }

      for (const stray of strays) result.damage += onFoot(stray, null, player, level, boxes, dt);
      // On foot, the player takes up room; lying down, along their whole length.
      // Making room must not push anyone into a wall either.
      separate(officers(), [...boxes, ...officers().flatMap((o) => wallsBetween(o, o, 2))],
        player.footprint ? [] : player.parts ?? [player]);
      result.busted = officers().some((o) => o.held >= HELD);
      result.pulled = pulled;
      return result;
    },
    // The player has taken this unit's car. Whoever was driving is thrown out.
    // Call from outside update().
    hijack(unit, at) {
      const index = units.indexOf(unit);
      if (index < 0) return;
      const f = footprint(unit.car.body);
      let { officer } = unit;
      if (!officer) {
        // They were at the wheel: out of the driver's door and on to the road.
        officer = createOfficer(scene, physics, { ...door(f, at.x, at.z), yaw: Math.atan2(f.hx, f.hz) });
        officer.knock((officer.x - f.x) * 1.5, (officer.z - f.z) * 1.5);
        officer.draw();
      }
      unit.car.dispose();
      units.splice(index, 1);
      strays.push({ officer });
    },
    // How far along a ray a leaning officer is, or null.
    sightGunner: (from, dir, reach) => onRay(from, dir, reach)?.distance ?? null,
    // A shot at them. Returns { hp, distance }, or null for a miss. Killed, they
    // fall out of the window; whoever is at the wheel flies on.
    shootGunner(from, dir, reach, amount) {
      const found = onRay(from, dir, reach);
      if (!found) return null;
      const { unit, distance, head, point } = found;
      return { hp: wound(unit, amount * (head ? OFFICER.headshot : 1), point, dir), distance };
    },
    // Everyone who has lately aimed at the player or laid hands on them:
    // { x, y, z, shooting, hurt(amount, dir) -> their HP }.
    attackers() {
      const afoot = officers().filter((o) => !o.dead && !o.down && o.hostile > 0).map((o) => ({
        x: o.x, y: 1.2 + o.lift, z: o.z, shooting: Boolean(o.shooting),
        hurt: (amount, dir, speed = 3) => o.hurt(amount, dir.x * speed, dir.z * speed),
      }));
      const aloft = units.filter((u) => u.gunner && u.shooter.hostile > 0).map((u) => {
        u.car.mesh.updateWorldMatrix(true, true);
        const at = u.gunner.head.getWorldPosition(new THREE.Vector3());
        return {
          x: at.x, y: at.y - 0.15, z: at.z, shooting: true,
          hurt: (amount, dir) => wound(u, amount, at, dir),
        };
      });
      return [...afoot, ...aloft];
    },
    // Is this collider part of a police car?
    owns: (collider) => units.some((u) => u.car.body.handle === collider?.parent()?.handle),
    // A shot that struck `collider` at height y. Returns { hp } if it was an officer.
    shoot(collider, y, amount, vx, vz) {
      const officer = officers().find((o) => o.owns(collider));
      if (!officer) return null;
      const head = !officer.down && y > 1.4;
      return { hp: officer.hurt(amount * (head ? OFFICER.headshot : 1), vx, vz) };
    },
    // Knock down any officer that one of these is driving into.
    // strikers: footprints with velocities. Returns true if one was.
    strike(strikers, overlaps, dt) {
      let hit = false;
      for (const officer of officers()) {
        if (officer.down) continue;
        const box = { x: officer.x, z: officer.z, hx: 0, hz: 1, halfL: 0.25, halfW: 0.25 };
        const by = strikers.find((s) => !(s.y > 2.5) && Math.hypot(s.vx, s.vz) > 1.5
          && (overlaps(s, box, 0.15)
            || overlaps({ ...s, x: s.x + s.vx * dt * 2, z: s.z + s.vz * dt * 2 }, box, 0.15)));
        if (!by) continue;
        officer.knock(by.vx, by.vz);
        hit = true;
      }
      return hit;
    },
    // Call once per rendered frame.
    draw(dt, night) {
      clock += dt;
      fade(dt);
      const phase = Math.floor(clock * 4) % 2;
      flat.red.color.set(phase ? 0xff1030 : 0x40060e);
      flat.blue.color.set(phase ? 0x06103a : 0x2060ff);
      for (const { officer } of strays) officer.draw();
      for (const car of husks) car.sync();
      for (const unit of units) {
        unit.officer?.draw();
        unit.car.sync();
        for (const mesh of unit.lamps) mesh.visible = night;
      }
    },
    clearNear(position, radius = 10) {
      for (let n = units.length - 1; n >= 0; n--) {
        const p = units[n].car.body.translation();
        if (Math.hypot(p.x - position.x, p.z - position.z) < radius) remove(n);
      }
    },
  };
}
