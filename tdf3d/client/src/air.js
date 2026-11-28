// Arcade flight: we set the body's velocity directly and let Rapier handle collisions.
import * as THREE from 'three';
import { material, part, makeBody } from './parts.js';

const CEILING = 6000;
export const AIR_TUNES = {
  helicopter: { top: 40, climb: 18, turn: 1.4 },
  spaceplane: { top: 250, thrust: 30, takeoff: 35, turn: 0.8 },
};
const { clamp } = THREE.MathUtils;

function flyer(name, scene, physics, spawn, { size, mass, tune, camera, help, build, control }) {
  const body = makeBody(physics, spawn, size, mass, [mass, mass, mass], 0);
  body.setEnabledRotations(false, false, false, true); // attitude is set by us
  const mesh = new THREE.Group();
  const model = new THREE.Group(); // visual bank and pitch
  mesh.add(model);
  const animate = build(model);
  scene.add(mesh);

  const state = { yaw: spawn.yaw ?? 0, pitch: 0, bank: 0, power: 0, parked: false };
  const rotation = new THREE.Quaternion();
  const forward = new THREE.Vector3();

  return {
    name, body, mesh, tune, camera, help,
    // Engine off: gravity holds it where it was left.
    park() {
      Object.assign(state, { parked: true, pitch: 0, bank: 0, power: 0 });
      body.setGravityScale(1, true);
      body.setLinvel({ x: 0, y: body.linvel().y, z: 0 }, true);
      body.setRotation(rotation.setFromAxisAngle({ x: 0, y: 1, z: 0 }, state.yaw), true);
    },
    drive(input, dt) {
      state.parked = false;
      forward.set(Math.sin(state.yaw), 0, Math.cos(state.yaw));
      control({ input, dt, body, state, tune, forward });
      if (body.translation().y > CEILING && body.linvel().y > 0)
        body.setLinvel({ ...body.linvel(), y: 0 }, true);
      body.setRotation(rotation.setFromAxisAngle({ x: 0, y: 1, z: 0 }, state.yaw), true);
    },
    sync() {
      mesh.position.copy(body.translation());
      mesh.quaternion.copy(body.rotation());
      model.rotation.set(-state.pitch, 0, state.bank, 'YXZ');
      animate?.(state);
    },
    speed() {
      const v = body.linvel();
      return Math.hypot(v.x, v.y, v.z);
    },
    dispose() {
      physics.removeRigidBody(body);
      scene.remove(mesh);
    },
  };
}

export function createHelicopter(name, scene, physics, spawn) {
  return flyer(name, scene, physics, spawn, {
    size: [2, 1.6, 4], mass: 1500,
    tune: { ...AIR_TUNES.helicopter },
    camera: { back: 16, up: 6 },
    help: 'Space up · Shift down · W/S forward/back · A/D turn',
    build(model) {
      const paint = material(0x2e7d32);
      part(model, new THREE.BoxGeometry(1.8, 1.5, 3), paint, 0, 0, 0.3);
      part(model, new THREE.BoxGeometry(0.35, 0.35, 3.5), paint, 0, 0.3, -2.8);
      part(model, new THREE.BoxGeometry(0.1, 1.2, 0.5), paint, 0, 0.7, -4.4);
      const dark = material(0x222222);
      for (const x of [-0.8, 0.8]) part(model, new THREE.BoxGeometry(0.12, 0.12, 3), dark, x, -0.74, 0.3);
      const rotor = part(model, new THREE.BoxGeometry(8, 0.06, 0.3), dark, 0, 1.0, 0.3);
      let spin = 0;
      return (state) => { if (!state.parked) rotor.rotation.y = spin += 0.5; };
    },
    control({ input, dt, body, state, tune, forward }) {
      state.yaw += input.steer * tune.turn * dt;
      const lift = (input.brake ? 1 : 0) - (input.descend ? 1 : 0);
      const v = body.linvel();
      const ease = 1 - Math.exp(-1.5 * dt);
      const wanted = input.throttle * tune.top;
      body.setGravityScale(0, true);
      body.setLinvel({
        x: v.x + (forward.x * wanted - v.x) * ease,
        y: v.y + (lift * tune.climb - v.y) * (1 - Math.exp(-4 * dt)),
        z: v.z + (forward.z * wanted - v.z) * ease,
      }, true);
      state.pitch += (-input.throttle * 0.2 - state.pitch) * ease * 2;
      state.bank += (-input.steer * 0.2 - state.bank) * ease * 2;
    },
  });
}

export function createSpaceplane(name, scene, physics, spawn) {
  return flyer(name, scene, physics, spawn, {
    size: [3, 1.2, 8], mass: 3000,
    tune: { ...AIR_TUNES.spaceplane },
    camera: { back: 22, up: 6 },
    help: 'W/S throttle · A/D turn · Space nose up · Shift nose down',
    build(model) {
      const white = material(0xeeeeee);
      const dark = material(0x263238);
      part(model, new THREE.CylinderGeometry(0.7, 0.8, 7, 12).rotateX(Math.PI / 2), white, 0, 0, -0.5);
      part(model, new THREE.ConeGeometry(0.7, 2, 12).rotateX(Math.PI / 2), dark, 0, 0, 4);
      part(model, new THREE.BoxGeometry(8, 0.15, 2.2), white, 0, -0.3, -2);
      part(model, new THREE.BoxGeometry(0.15, 1.8, 1.4), dark, 0, 1.3, -3.2);
      const flame = part(model, new THREE.ConeGeometry(0.5, 3, 10).rotateX(-Math.PI / 2),
        new THREE.MeshBasicMaterial({ color: 0xff8f00 }), 0, 0, -5.5);
      return (state) => { flame.visible = state.power > 0.05; flame.scale.z = state.power; };
    },
    control({ input, dt, body, state, tune, forward }) {
      state.power = clamp(state.power + input.throttle * dt * 0.7, 0, 1);
      const v = body.linvel();
      // Read speed back from the body, so hitting something really slows the plane.
      const cos = Math.cos(state.pitch);
      let speed = Math.max(0, v.x * forward.x * cos + v.y * Math.sin(state.pitch) + v.z * forward.z * cos);
      speed += clamp(state.power * tune.top - speed, -tune.thrust * dt, tune.thrust * dt);

      const grounded = body.translation().y < 1;
      const flying = speed >= tune.takeoff;
      const nose = (input.brake ? 1 : 0) - (input.descend ? 1 : 0);
      state.pitch = clamp(state.pitch + nose * 0.8 * dt, grounded ? 0 : -1.2, flying ? 1.2 : 0);
      state.yaw += input.steer * tune.turn * dt * clamp(speed / 20, 0, 1);
      state.bank = flying && !grounded ? state.bank + (-input.steer * 0.6 - state.bank) * 3 * dt : 0;

      const level = speed * Math.cos(state.pitch);
      body.setGravityScale(flying ? 0 : 1, true); // stall below take-off speed
      body.setLinvel({
        x: Math.sin(state.yaw) * level,
        y: flying ? speed * Math.sin(state.pitch) : v.y,
        z: Math.cos(state.yaw) * level,
      }, true);
    },
  });
}
