// Owns the player (in a vehicle or on foot) and any vehicles left parked.
import RAPIER from '@dimforge/rapier3d-compat';
import { PITCH, SIZE } from './city.js';
import { GROUND, GROUND_TUNES, createGroundVehicle } from './ground.js';
import { AIR_TUNES, createHelicopter, createSpaceplane } from './air.js';
import { WALK_TUNE, createWalker } from './foot.js';
import * as THREE from 'three';

const MAKERS = {
  ...Object.fromEntries(GROUND.map((name) => [name, createGroundVehicle])),
  helicopter: createHelicopter,
  spaceplane: createSpaceplane,
};

export const VEHICLES = Object.keys(MAKERS);
export const TUNES = { ...GROUND_TUNES, ...AIR_TUNES, 'on foot': WALK_TUNE };
const REACH = 4.5; // metres from a vehicle's centre to get in
const LEFT_BEHIND = 250; // metres: a stolen car further off than this is gone

// Closest road centre line to (x, z), facing along that road.
export function roadSpot(x, z, yaw = 0) {
  const limit = SIZE / 2 - PITCH;
  const keep = (v) => Math.max(-limit, Math.min(limit, v));
  const line = (v) => Math.round(v / PITCH) * PITCH;
  x = keep(x);
  z = keep(z);
  if (Math.abs(x - line(x)) <= Math.abs(z - line(z)))
    return { x: line(x), y: 1.5, z, yaw: Math.cos(yaw) < 0 ? Math.PI : 0 };
  return { x, y: 1.5, z: line(z), yaw: Math.sin(yaw) < 0 ? -Math.PI / 2 : Math.PI / 2 };
}

const heading = (body) => {
  const q = body.rotation();
  return Math.atan2(2 * (q.x * q.z + q.w * q.y), 1 - 2 * (q.x * q.x + q.y * q.y));
};
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);

export function createGarage(scene, physics, spawn, first = 'car', onSpawn = () => {}) {
  let current = MAKERS[first](first, scene, physics, spawn);
  const parked = new Map(); // name -> vehicle, at most one of each
  const onFoot = () => current.name === 'on foot';
  const overrides = {}; // type -> tuning the player has changed
  const tuned = (vehicle) => {
    Object.assign(vehicle.tune, overrides[vehicle.name]);
    return vehicle;
  };

  // Which type a setting refers to: the one in use if it has it, otherwise the
  // only type that does. null if that is ambiguous.
  function owner(key) {
    if (key in current.tune) return current.name;
    const types = Object.keys(TUNES).filter((name) => key in TUNES[name]);
    return types.length === 1 ? types[0] : null;
  }

  // There can be several cars about, once some have been stolen.
  function park(vehicle) {
    let key = vehicle.name;
    for (let n = 2; parked.has(key); n++) key = `${vehicle.name}#${n}`;
    parked.set(key, vehicle);
  }

  // Replace whatever the player is using with a fresh one on the nearest road.
  function respawn(name) {
    const spot = roadSpot(current.body.translation().x, current.body.translation().z, heading(current.body));
    current.dispose();
    parked.get(name)?.dispose();
    parked.delete(name);
    // Don't appear inside a parked vehicle.
    while ([...parked.values()].some((v) => distance(v.body.translation(), { ...spot, y: v.body.translation().y }) < 9)) {
      spot.x += Math.sin(spot.yaw) * 10;
      spot.z += Math.cos(spot.yaw) * 10;
    }
    onSpawn(spot);
    current = tuned(name === 'on foot'
      ? createWalker(name, scene, physics, spot)
      : MAKERS[name](name, scene, physics, spot));
  }

  // A clear place to stand beside the vehicle: left, right, behind, then on top.
  function doorstep() {
    const p = current.body.translation();
    const yaw = heading(current.body);
    const shape = new RAPIER.Capsule(0.5, 0.35);
    const side = current.name === 'spaceplane' || current.name === 'helicopter' ? 2.4 : 1.8;
    for (const [angle, reach] of [[Math.PI / 2, side], [-Math.PI / 2, side], [Math.PI, 4.2]]) {
      const spot = {
        x: p.x + Math.sin(yaw + angle) * reach,
        y: p.y + 0.4,
        z: p.z + Math.cos(yaw + angle) * reach,
      };
      if (!physics.intersectionWithShape(spot, { x: 0, y: 0, z: 0, w: 1 }, shape))
        return { ...spot, yaw };
    }
    return { x: p.x, y: p.y + 2.5, z: p.z, yaw };
  }

  // Get out, or get in the nearest parked vehicle. Returns a message if it can't.
  function toggle() {
    const p = current.body.translation();
    if (onFoot()) {
      const [nearest] = [...parked]
        .filter(([, v]) => !v.condition || v.condition.hp > 0)
        .map(([key, v]) => [distance(p, v.body.translation()), v, key])
        .filter(([d]) => d <= REACH)
        .sort((a, b) => a[0] - b[0]);
      if (!nearest) return 'No vehicle close enough';
      current.dispose();
      current = nearest[1];
      parked.delete(nearest[2]);
      return null;
    }
    const v = current.body.linvel();
    const ground = physics.castRay(
      new RAPIER.Ray(p, { x: 0, y: -1, z: 0 }), 2, true, undefined, undefined, undefined, current.body,
    );
    if (Math.hypot(v.x, v.y, v.z) > 2) return 'Stop before getting out';
    if (!ground) return 'Land before getting out';
    const spot = doorstep();
    park(current);
    current = tuned(createWalker('on foot', scene, physics, spot));
    return null;
  }

  return {
    get current() { return current; },
    parked,
    names: VEHICLES,
    // undefined if the setting does not apply to anything unambiguous.
    getTune(key) {
      const name = owner(key);
      if (!name) return undefined;
      return name === current.name ? current.tune[key] : overrides[name]?.[key] ?? TUNES[name][key];
    },
    // Returns the type changed, or null. The value is remembered for that type.
    setTune(key, value) {
      const name = owner(key);
      if (!name) return null;
      (overrides[name] ??= {})[key] = value;
      for (const vehicle of [current, ...parked.values()]) if (vehicle.name === name) tuned(vehicle);
      return name;
    },
    switch: respawn,
    toggle,
    // Dragged out of whatever they are in, to stand at `spot`. The vehicle stays.
    eject(spot) {
      if (onFoot()) return false;
      const { y } = current.body.translation();
      const yaw = heading(current.body);
      park(current);
      current = tuned(createWalker('on foot', scene, physics, { x: spot.x, y: Math.max(0.9, y + 0.1), z: spot.z, yaw }));
      return true;
    },
    // From on foot, straight into a car that was somebody else's.
    hijack(spot, colours, police, condition) {
      current.dispose();
      current = tuned(MAKERS.car('car', scene, physics, spot, colours));
      current.stolen = true;
      if (condition) current.condition = condition; // as damaged as it was
      if (!police) return;
      for (const [x, colour] of [[-0.35, 0xff1030], [0.35, 0x2060ff]]) {
        const bar = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.18, 0.35), new THREE.MeshBasicMaterial({ color: colour }));
        bar.position.set(x, 1.2, -0.3);
        current.mesh.add(bar);
      }
    },
    // Put the player on foot at `door`, with a car waiting at `kerb`. Whatever
    // they were using is gone.
    arrive(door, kerb) {
      current.dispose();
      parked.get('car')?.dispose();
      parked.delete('car');
      onSpawn(door);
      onSpawn(kerb);
      current = tuned(createWalker('on foot', scene, physics, door));
      parked.set('car', tuned(MAKERS.car('car', scene, physics, kerb)));
    },
    // Upright, on the nearest road.
    reset: () => respawn(current.name),
    drive(input, dt) {
      current.drive(input, dt);
      const at = current.body.translation();
      for (const [key, vehicle] of parked) {
        const gone = distance(at, vehicle.body.translation());
        if ((vehicle.stolen && gone > LEFT_BEHIND) || (vehicle.condition?.burnt && gone > 150)) {
          vehicle.dispose();
          parked.delete(key);
        } else vehicle.park(dt);
      }
    },
    sync() {
      current.sync();
      for (const vehicle of parked.values()) vehicle.sync();
    },
  };
}
