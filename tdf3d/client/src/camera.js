// Chase camera, plus a first person view that is available on foot.
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { CAMERA_GROUPS } from './poles.js';

const EYE = 0.7;         // metres above the walker's centre
const NEAR_FIRST = 0.3;  // walls and cars get much closer in first person
const NEAR_CHASE = 1;    // a larger near plane keeps distant ground from flickering
const TILT = -0.25;      // the third person view on foot looks slightly down
const SHOULDER = 0.6;    // metres to the right when armed, so you aren't in the way

// Unit vector for a heading and an upward angle.
const aim = (out, { yaw, pitch }) => out.set(
  Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch),
);

export function createCamera(camera, physics) {
  const behind = new THREE.Vector3(0, 5, -10); // camera position relative to the player
  const wanted = new THREE.Vector3();
  const eye = new THREE.Vector3();
  const reach = new THREE.Vector3();
  let firstPerson = false; // remembered while driving

  return {
    get firstPerson() { return firstPerson; },
    // Only on foot; returns whether anything changed.
    toggle(vehicle) {
      if (vehicle.name !== 'on foot') return false;
      firstPerson = !firstPerson;
      return true;
    },
    // back: look behind, from a vehicle.
    update(vehicle, frame, armed = false, back = false) {
      const { position, quaternion } = vehicle.mesh;
      const looking = vehicle.view?.(); // only the player on foot can look around
      const first = firstPerson && vehicle.name === 'on foot' && !vehicle.down;
      vehicle.mesh.visible = !first; // don't look out from inside your own head
      camera.near = first ? NEAR_FIRST : NEAR_CHASE;
      if (first) {
        camera.position.copy(position).y += EYE;
        camera.lookAt(eye.copy(camera.position).add(aim(reach, looking)));
        return;
      }
      if (looking) {
        // Orbit with the mouse, without smoothing so it feels direct.
        const pitch = Math.max(-1.4, Math.min(1.4, looking.pitch + TILT));
        aim(wanted, { yaw: looking.yaw, pitch });
        camera.position.copy(position).addScaledVector(wanted, -vehicle.camera.back).y += 1;
        camera.position.y = Math.max(0.3, camera.position.y); // stay above ground
        behind.copy(camera.position).sub(position); // carries over on getting in
      } else {
        // Smoothing the offset (not the position) avoids lag at high speed.
        wanted.set(0, 0, 1).applyQuaternion(quaternion).setY(0).normalize()
          .multiplyScalar(-vehicle.camera.back).setY(vehicle.camera.up);
        behind.lerp(wanted, 1 - Math.exp(-5 * frame));
        camera.position.copy(position).add(behind);
        // Swung round to the front, looking back past the vehicle. At once, and
        // the usual view is there again as soon as the key is let go.
        if (back) camera.position.set(position.x - behind.x, camera.position.y, position.z - behind.z);
      }
      // Pull the camera in rather than let a building come between it and the player.
      // Over the right shoulder when armed.
      const shift = looking && armed ? SHOULDER : 0;
      const sideX = shift ? -Math.cos(looking.yaw) * shift : 0;
      const sideZ = shift ? Math.sin(looking.yaw) * shift : 0;
      camera.position.x += sideX;
      camera.position.z += sideZ;
      eye.copy(position).y += 1;
      reach.copy(camera.position).sub(eye);
      const span = reach.length();
      const hit = physics.castRay(new RAPIER.Ray(eye, reach.normalize()), span, true,
        RAPIER.QueryFilterFlags.EXCLUDE_DYNAMIC | RAPIER.QueryFilterFlags.EXCLUDE_KINEMATIC,
        CAMERA_GROUPS);
      if (hit) camera.position.copy(eye).addScaledVector(reach, Math.max(0.5, hit.timeOfImpact - 0.3));
      if (looking) {
        // Look along the aim, so the centre of the screen is where shots go.
        camera.lookAt(position.x + sideX, position.y + 1, position.z + sideZ);
      } else camera.lookAt(position.x, position.y + 1, position.z);
    },
  };
}
