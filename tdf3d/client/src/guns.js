// Simple models. The barrel points along +z with the grip hanging down.
import * as THREE from 'three';
import { material, part } from './parts.js';

export function createGun(name) {
  const gun = new THREE.Group();
  const black = material(0x1b1b1b);
  const grey = material(0x4a4a4a);
  const box = (w, h, d, mat, y, z) => part(gun, new THREE.BoxGeometry(w, h, d), mat, 0, y, z);
  if (name === 'Glock 17') {
    box(0.04, 0.05, 0.2, black, 0.03, 0.04);              // slide
    box(0.035, 0.11, 0.05, grey, -0.04, -0.02).rotation.x = 0.25; // grip
    gun.userData.muzzle = new THREE.Vector3(0, 0.03, 0.15); // end of the barrel
  } else if (name === 'BRN-180') {
    gun.userData.muzzle = new THREE.Vector3(0, 0.025, 0.575);
    box(0.05, 0.08, 0.28, black, 0.02, 0.05);             // receiver
    box(0.045, 0.055, 0.25, grey, 0.025, 0.31);           // handguard
    box(0.02, 0.02, 0.14, black, 0.025, 0.5);             // barrel
    box(0.035, 0.14, 0.06, grey, -0.08, 0.1).rotation.x = -0.2;   // magazine
    box(0.035, 0.1, 0.045, black, -0.06, -0.06).rotation.x = 0.3; // grip
    box(0.04, 0.07, 0.2, black, -0.01, -0.2);             // stock
    box(0.02, 0.03, 0.1, black, 0.08, 0.05);              // sight
  }
  return gun;
}
