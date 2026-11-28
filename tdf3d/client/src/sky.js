// Sky colour, sunlight or moonlight, and the moon itself.
import * as THREE from 'three';
import { NIGHT_FROM, NIGHT_TO, isNight } from './clock.js';

const SPACE = new THREE.Color(0x02030a);
const LOOKS = {
  day: { sky: 0x9fc5e8, above: 0xffffff, below: 0x445566, ambient: 2, light: 0xffffff, direct: 1.5 },
  night: { sky: 0x0b1026, above: 0x8090c0, below: 0x202030, ambient: 0.5, light: 0xaabbff, direct: 0.3 },
};
const SUN = new THREE.Vector3(1, 2, 0.5).normalize();

// Where the moon is: it rises in the east at 6pm and sets in the west at 6am,
// staying high enough to be seen between buildings at either end.
export function moonDirection(hour, out = new THREE.Vector3()) {
  const length = (NIGHT_TO + 24 - NIGHT_FROM) % 24;
  const through = ((hour - NIGHT_FROM + 24) % 24) / length; // 0 to 1 over the night
  const elevation = 0.15 + 0.35 * Math.sin(Math.min(1, through) * Math.PI); // radians
  const bearing = Math.min(1, through) * Math.PI;
  return out.set(
    Math.cos(bearing) * Math.cos(elevation), Math.sin(elevation), Math.sin(bearing) * Math.cos(elevation),
  );
}

function createMoon() {
  const flat = (color) => new THREE.MeshBasicMaterial({ color, fog: false });
  const moon = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 16), flat(0xe8ecf5));
  // A few darker seas, so that it reads as the moon rather than a lamp.
  for (const [x, y, size] of [[-0.35, 0.3, 0.3], [0.25, -0.15, 0.22], [0.05, 0.5, 0.15], [-0.1, -0.45, 0.18]]) {
    const sea = new THREE.Mesh(new THREE.CircleGeometry(size, 16), flat(0xb9c0d0));
    sea.position.set(x, y, Math.sqrt(1 - x * x - y * y) + 0.02);
    sea.lookAt(sea.position.x * 2, sea.position.y * 2, sea.position.z * 2);
    moon.add(sea);
  }
  return moon;
}

export function createSky(scene) {
  scene.background = new THREE.Color();
  scene.fog = new THREE.Fog(0);
  const ambient = new THREE.HemisphereLight();
  const direct = new THREE.DirectionalLight();
  const moon = createMoon();
  scene.add(ambient, direct, moon);
  const toMoon = new THREE.Vector3();

  return {
    moon,
    // height: metres above the ground, where the sky fades towards space.
    // Call after the camera has moved and its far plane is set.
    update(hour, camera, height) {
      const night = isNight(hour);
      const look = night ? LOOKS.night : LOOKS.day;
      scene.background.set(look.sky).lerp(SPACE, THREE.MathUtils.smoothstep(height, 800, 4000));
      scene.fog.color.copy(scene.background);
      ambient.color.set(look.above);
      ambient.groundColor.set(look.below);
      ambient.intensity = look.ambient;
      direct.color.set(look.light);
      direct.intensity = look.direct;
      direct.position.copy(night ? moonDirection(hour, toMoon) : SUN);

      // The moon is infinitely far away: it keeps its place relative to the camera.
      moon.visible = night;
      if (!night) return;
      const distance = camera.far * 0.9;
      moon.position.copy(camera.position).addScaledVector(moonDirection(hour, toMoon), distance);
      moon.scale.setScalar(distance * 0.025);
      moon.lookAt(camera.position);
    },
  };
}
