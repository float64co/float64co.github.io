import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';

export const material = (color) => new THREE.MeshLambertMaterial({ color });

export function part(group, geometry, mat, x, y, z) {
  const mesh = new THREE.Mesh(geometry, mat);
  mesh.position.set(x, y, z);
  group.add(mesh);
  return mesh;
}

// A box-shaped dynamic body. massY lowers the centre of mass to resist rolling.
export function makeBody(physics, spawn, [w, h, d], mass, [ix, iy, iz], massY) {
  const body = physics.createRigidBody(
    RAPIER.RigidBodyDesc.dynamic()
      .setTranslation(spawn.x, spawn.y, spawn.z)
      .setRotation({ x: 0, y: Math.sin((spawn.yaw ?? 0) / 2), z: 0, w: Math.cos((spawn.yaw ?? 0) / 2) })
      .setCanSleep(false)
      .setCcdEnabled(true),
  );
  physics.createCollider(
    RAPIER.ColliderDesc.cuboid(w / 2, h / 2, d / 2).setMassProperties(
      mass, { x: 0, y: massY, z: 0 }, { x: ix, y: iy, z: iz }, { x: 0, y: 0, z: 0, w: 1 },
    ),
    body,
  );
  return body;
}
