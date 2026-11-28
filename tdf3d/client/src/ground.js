// Car, motorbike and bicycle: all use Rapier's ray-cast vehicle controller.
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { material, part, makeBody } from './parts.js';
import { headGeometry, headMaterial } from './head.js';
import { createCarBody } from './carbody.js';
import { createCondition } from './damage.js';
import { AIR_TUNES } from './air.js';

// A car in flight handles as the helicopter does.
const FLIGHT = AIR_TUNES.helicopter;
const HOVER = 1.15;  // metres: as low as the middle of a flying car goes, wheels clear
const CEILING = 6000;
const FOLD = 0.1;    // how much of the way the wheels turn each frame

// Handling values can be changed live with the console's `set` command.
const SPECS = {
  car: {
    tune: { engine: 3500, brake: 60, steer: 0.7, falloff: 40, grip: 3, top: 45 },
    mass: 1200, inertia: [1700, 1900, 600], size: [2, 0.8, 4],
    track: 0.9, axle: 1.4, radius: 0.35, stiffness: 28, drag: 4, camera: { back: 10, up: 5 },
  },
  motorbike: {
    tune: { engine: 1100, brake: 12, steer: 0.55, falloff: 25, grip: 3, top: 55 },
    mass: 250, inertia: [130, 140, 60], size: [0.5, 0.5, 1.8],
    track: 0.35, axle: 0.75, radius: 0.35, stiffness: 35, drag: 1, camera: { back: 7, up: 3 },
  },
  bicycle: {
    tune: { engine: 140, brake: 3, steer: 0.6, falloff: 20, grip: 3, top: 12 },
    mass: 90, inertia: [40, 45, 20], size: [0.3, 0.4, 1.5],
    track: 0.3, axle: 0.6, radius: 0.35, stiffness: 40, drag: 0.3, camera: { back: 5, up: 2.5 },
  },
};

export const GROUND = Object.keys(SPECS);

// A car's rear tyres let go a little under the handbrake, which brings the tail
// round. Faded out at high speed, where it would only make the car slide further.
const HANDBRAKE = { grip: 0.7, full: 20, none: 28 }; // speeds in m/s
export const GROUND_TUNES = Object.fromEntries(GROUND.map((name) => [name, SPECS[name].tune]));

function look(name, group, colours = {}) {
  if (name === 'car') {
    const shell = createCarBody(colours);
    group.add(shell.group);
    return shell;
  }
  const frame = material(name === 'motorbike' ? 0xf4a000 : 0x1565c0);
  const [w, h, d] = name === 'motorbike' ? [0.4, 0.4, 1.3] : [0.08, 0.08, 1.2];
  part(group, new THREE.BoxGeometry(w, h, d), frame, 0, 0, 0);
  part(group, new THREE.BoxGeometry(0.7, 0.07, 0.07), material(0x222222), 0, 0.55, 0.55); // handlebar
  part(group, new THREE.BoxGeometry(0.08, 0.6, 0.08), frame, 0, 0.25, 0.55);
  // Rider.
  part(group, new THREE.BoxGeometry(0.45, 0.7, 0.3), material(0x37474f), 0, 0.75, -0.2).rotation.x = 0.4;
  part(group, headGeometry({ skin: 0xffcc99, hair: 0x5a3b1c }), headMaterial(), 0, 1.28, -0.04);
}

export function createGroundVehicle(name, scene, physics, spawn, colours) {
  const spec = SPECS[name];
  const tune = { ...spec.tune };
  const twoWheeled = name !== 'car';
  const body = makeBody(physics, spawn, spec.size, spec.mass, spec.inertia, -spec.size[1] / 2);

  const vehicle = physics.createVehicleController(body);
  vehicle.indexUpAxis = 1;
  vehicle.setIndexForwardAxis = 2; // Rapier's JS API really does name the setter this way

  // Two-wheelers get four narrow physics wheels but only show two.
  const places = [[-1, 1], [1, 1], [-1, -1], [1, -1]].map(([sx, sz]) => [sx * spec.track, sz * spec.axle]);
  const mesh = new THREE.Group();
  const model = new THREE.Group(); // leans independently of the physics body
  mesh.add(model);
  const shell = look(name, model, colours);
  // Only cars can be damaged.
  const condition = name === 'car' ? createCondition() : null;

  const thickness = name === 'car' ? 0.35 : name === 'motorbike' ? 0.18 : 0.06;
  const tyre = new THREE.CylinderGeometry(0.4, 0.4, thickness, 16).rotateZ(Math.PI / 2);
  const rubber = material(0x111111);
  places.forEach(([x, z], i) => {
    vehicle.addWheel({ x, y: -0.2, z }, { x: 0, y: -1, z: 0 }, { x: -1, y: 0, z: 0 }, 0.35, spec.radius);
    vehicle.setWheelSuspensionStiffness(i, spec.stiffness);
    vehicle.setWheelSuspensionCompression(i, 3);
    vehicle.setWheelSuspensionRelaxation(i, 4);
  });
  const hub = new THREE.CylinderGeometry(0.19, 0.19, 0.03, 12).rotateZ(Math.PI / 2);
  const chrome = material(0xc9ced4);
  const wheels = (twoWheeled ? [[0, spec.axle, 0], [0, -spec.axle, 2]] : places.map(([x, z], i) => [x, z, i]))
    .map(([x, z, index]) => {
      const wheel = part(model, tyre, rubber, x, 0, z);
      wheel.rotation.order = 'YXZ';
      // A hubcap on the outside of each of a car's wheels.
      const cap = name === 'car' ? part(wheel, hub, chrome, Math.sign(x) * (thickness / 2 + 0.01), 0, 0) : null;
      return { wheel, cap, x, z, index };
    });
  scene.add(mesh);

  const forward = new THREE.Vector3();
  const right = new THREE.Vector3();
  const quaternion = new THREE.Quaternion();
  let lean = 0;
  // Flight, which only cars can do.
  let flying = false;
  let folded = 0; // how far the wheels have turned flat, 0 to 1
  let yaw = 0;
  let pitch = 0;
  let bank = 0;
  const upright = new THREE.Quaternion();
  const up = new THREE.Vector3(0, 1, 0);

  function setFlying(on) {
    if (on === flying || (on && !(api.condition?.hp > 0))) return flying;
    flying = on;
    // Gravity comes back the moment it stops, wherever that is.
    body.setGravityScale(on ? 0 : 1, true);
    if (on) {
      const q = body.rotation();
      yaw = Math.atan2(2 * (q.x * q.z + q.w * q.y), 1 - 2 * (q.x * q.x + q.y * q.y));
    } else {
      pitch = 0;
      bank = 0;
      body.setAngvel({ x: 0, y: 0, z: 0 }, true);
      // Straight into a fall, not carrying on upwards.
      const v = body.linvel();
      body.setLinvel({ x: v.x, y: Math.min(0, v.y), z: v.z }, true);
    }
    return flying;
  }

  // Space up, Shift (descend) down, W/S forward and back, A/D turn.
  function fly({ throttle, steer, brake, descend }, dt) {
    yaw += steer * FLIGHT.turn * dt;
    const lift = (brake ? 1 : 0) - (descend ? 1 : 0);
    const v = body.linvel();
    const { y } = body.translation();
    const ease = 1 - Math.exp(-1.5 * dt);
    const wanted = throttle * FLIGHT.top;
    let rise = lift * FLIGHT.climb;
    // Never lower than a hover, nor higher than the other aircraft.
    if (y < HOVER) rise = Math.max(rise, (HOVER - y) * 4);
    if (y > CEILING) rise = Math.min(rise, 0);
    body.setLinvel({
      x: v.x + (Math.sin(yaw) * wanted - v.x) * ease,
      y: v.y + (rise - v.y) * (1 - Math.exp(-4 * dt)),
      z: v.z + (Math.cos(yaw) * wanted - v.z) * ease,
    }, true);
    body.setRotation(upright.setFromAxisAngle(up, yaw), true);
    body.setAngvel({ x: 0, y: 0, z: 0 }, true);
    pitch += (-throttle * 0.2 - pitch) * ease * 2;
    bank += (-steer * 0.2 - bank) * ease * 2;
  }

  function drive(input, dt) {
    // A wrecked engine cannot keep anything up.
    if (flying && !(api.condition.hp > 0)) setFlying(false);
    if (flying) return fly(input, dt);
    let { throttle } = input;
    const { steer, brake, handbrake } = input;
    const speed = vehicle.currentVehicleSpeed();
    if (api.condition?.hp <= 0) throttle = 0; // the engine is wrecked
    const force = Math.abs(speed) < tune.top ? throttle * tune.engine : 0;
    const angle = steer * tune.steer / (1 + Math.abs(speed) / tune.falloff); // calmer at speed
    const fade = (Math.abs(speed) - HANDBRAKE.full) / (HANDBRAKE.none - HANDBRAKE.full);
    const loose = handbrake && name === 'car'
      ? HANDBRAKE.grip + (1 - HANDBRAKE.grip) * Math.max(0, Math.min(1, fade)) : 1;
    for (let i = 0; i < 4; i++) {
      vehicle.setWheelFrictionSlip(i, tune.grip * (i < 2 ? 1 : loose));
      vehicle.setWheelBrake(i, brake ? tune.brake : throttle ? 0 : spec.drag);
      if (i < 2) vehicle.setWheelSteering(i, angle);
      else vehicle.setWheelEngineForce(i, force);
    }
    vehicle.updateVehicle(dt);

    if (twoWheeled) {
      // Balance aid: steer the roll rate towards level.
      quaternion.copy(body.rotation());
      forward.set(0, 0, 1).applyQuaternion(quaternion);
      right.set(1, 0, 0).applyQuaternion(quaternion);
      const spin = new THREE.Vector3().copy(body.angvel());
      const correction = -8 * Math.asin(THREE.MathUtils.clamp(right.y, -1, 1)) - spin.dot(forward);
      body.setAngvel(spin.addScaledVector(forward, correction), true);
    }
  }

  function sync() {
    if (api.condition) shell.update(api.condition);
    folded += ((flying ? 1 : 0) - folded) * FOLD;
    if (Math.abs(folded - (flying ? 1 : 0)) < 0.01) folded = flying ? 1 : 0;
    // The model banks and tips in flight, as the helicopter does.
    if (name === 'car') model.rotation.set(-pitch, 0, bank, 'YXZ');
    mesh.position.copy(body.translation());
    mesh.quaternion.copy(body.rotation());
    if (twoWheeled) {
      // Visual lean into corners. Positive yaw rate is a left turn.
      const wanted = -Math.atan(vehicle.currentVehicleSpeed() * body.angvel().y / 9.81);
      lean += (THREE.MathUtils.clamp(wanted, -0.7, 0.7) - lean) * 0.15;
      model.rotation.z = lean;
    }
    for (const { wheel, x, z, index } of wheels) {
      const hung = -0.2 - (vehicle.wheelSuspensionLength(index) ?? 0.35);
      // In flight each swings out and lies flat, hubcap to the ground.
      wheel.position.set(x * (1 + 0.2 * folded), hung + (-0.55 - hung) * folded, z);
      wheel.rotation.set(
        (vehicle.wheelRotation(index) ?? 0) * (1 - folded),
        (vehicle.wheelSteering(index) ?? 0) * (1 - folded),
        -Math.sign(x) * (Math.PI / 2) * folded,
      );
    }
  }

  const api = {
    name, body, mesh, tune, drive, sync, condition,
    get flying() { return flying; },
    setFlying,
    // Where the hubcaps are in the world.
    hubs() {
      mesh.updateWorldMatrix(true, true);
      return wheels.filter((w) => w.cap).map((w) => w.cap.getWorldPosition(new THREE.Vector3()));
    },
    // Left standing, it comes down.
    park(dt) {
      setFlying(false);
      drive({ throttle: 0, steer: 0, brake: true }, dt);
    },
    camera: spec.camera,
    get help() {
      if (flying) return 'Flying: Space up · Shift down · W/S forward/back · A/D turn · G drop';
      return `W/S drive · A/D steer · Space brake${name === 'car' ? ' · G fly' : ''}`;
    },
    speed: () => vehicle.currentVehicleSpeed(),
    // Where wheel i (front left/right, rear left/right) is, for drawing it elsewhere.
    wheel: (i) => ({
      y: -0.2 - (vehicle.wheelSuspensionLength(i) ?? 0.35),
      spin: vehicle.wheelRotation(i) ?? 0,
      steer: vehicle.wheelSteering(i) ?? 0,
    }),
    dispose() {
      physics.removeVehicleController(vehicle);
      physics.removeRigidBody(body);
      scene.remove(mesh);
    },
  };
  return api;
}
