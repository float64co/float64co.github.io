// Headlights and tail lights.
import * as THREE from 'three';

const WHITE = 0xfff4d0;
const RED = 0xff2a20;
// Where the lamps sit on each kind of vehicle: across, up, front, back.
const MOUNTS = {
  car: { across: [-0.65, 0.65], up: 0.05, front: 2.02, back: -2.02 },
  motorbike: { across: [0], up: 0.35, front: 0.75, back: -0.75 },
};

const glow = (color) => new THREE.MeshBasicMaterial({ color });
const lamp = () => new THREE.BoxGeometry(0.35, 0.2, 0.06);

// Lamps for an NPC car, modelled like its body: from the ground, centred.
export function npcLamps() {
  const pair = (z) => {
    const left = lamp().translate(-0.65, 0.8, z).toNonIndexed();
    const right = lamp().translate(0.65, 0.8, z).toNonIndexed();
    const both = new THREE.BufferGeometry();
    for (const name of ['position', 'normal']) {
      const values = [...left.attributes[name].array, ...right.attributes[name].array];
      both.setAttribute(name, new THREE.Float32BufferAttribute(values, 3));
    }
    return both;
  };
  return { front: pair(2.02), tail: pair(-2.02), white: glow(WHITE), red: glow(RED) };
}

// The patch of light on the road ahead of an NPC car. Far too many cars to
// give each a real light, so it is painted on: bright near, fading away.
export function beam() {
  const geometry = new THREE.BufferGeometry();
  const near = [[-0.9, 2.1], [0.9, 2.1]];
  const far = [[-2.6, 13], [2.6, 13]];
  const corners = [near[0], far[0], near[1], near[1], far[0], far[1]];
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(
    corners.flatMap(([x, z]) => [x, 0.13, z]), 3)); // just above the pavements
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(
    corners.flatMap(([, z]) => (z < 5 ? [0.28, 0.25, 0.17] : [0, 0, 0])), 3));
  const material = new THREE.MeshBasicMaterial({
    vertexColors: true, transparent: true, blending: THREE.AdditiveBlending,
    depthWrite: false, side: THREE.DoubleSide, fog: false,
  });
  return { geometry, material };
}

// The player's own: lamps, and one real light that shows up what's ahead.
export function createHeadlights(scene) {
  const group = new THREE.Group();
  const light = new THREE.SpotLight(WHITE, 0, 70, 0.55, 0.5, 1);
  scene.add(group, light, light.target);
  const lamps = Object.fromEntries(Object.entries(MOUNTS).map(([name, m]) => {
    const set = new THREE.Group();
    for (const x of m.across) {
      for (const [z, color] of [[m.front, WHITE], [m.back, RED]]) {
        const mesh = new THREE.Mesh(lamp(), glow(color));
        mesh.position.set(x, m.up, z);
        set.add(mesh);
      }
    }
    group.add(set);
    return [name, set];
  }));

  return {
    light,
    group,
    // On for the car or motorbike being driven, at night.
    update(vehicle, night) {
      const mount = night ? MOUNTS[vehicle.name] : null;
      group.visible = Boolean(mount);
      // Dimmed, not removed: changing the number of lights recompiles shaders.
      light.intensity = mount ? 45 : 0;
      if (!mount) return;
      for (const [name, set] of Object.entries(lamps)) set.visible = name === vehicle.name;
      const { position, quaternion } = vehicle.mesh;
      group.position.copy(position);
      group.quaternion.copy(quaternion);
      light.position.set(0, mount.up + 0.3, mount.front).applyQuaternion(quaternion).add(position);
      light.target.position.set(0, -1, mount.front + 15).applyQuaternion(quaternion).add(position);
    },
  };
}
