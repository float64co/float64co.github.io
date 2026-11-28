// Draws the street lamps around the player. At night their heads shine and each
// throws a patch of light on the ground: far too many to give each a real light.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { PITCH } from './city.js';
import { LAMP_PARTS, lampPlace, lampKey, hasLamp } from './poles.js';

const SHINING = 0xffe9a8;
const UNLIT = 0x8d9096;
const BROKEN = 0x222222;
const POOL = 7.5; // metres: radius of the patch of light

// A disc, bright in the middle and fading to nothing at the edge.
function pool() {
  const geometry = new THREE.CircleGeometry(POOL, 20).rotateX(-Math.PI / 2);
  const { count } = geometry.attributes.position; // the first vertex is the centre
  const colours = new Float32Array(count * 3);
  colours.set([0.38, 0.29, 0.13]);
  geometry.setAttribute('color', new THREE.BufferAttribute(colours, 3));
  return geometry.translate(0, 0.12, 0); // just above the pavements
}

export function createLamps(scene, poles, reach) {
  const most = Math.ceil(Math.PI * (reach / PITCH + 2) ** 2) * 4;
  const box = ({ half: [w, h, d], x = 0, y }) => new THREE.BoxGeometry(w * 2, h * 2, d * 2).translate(x, y, 0);
  function instances(geometry, material) {
    const mesh = new THREE.InstancedMesh(geometry, material, most);
    mesh.frustumCulled = false;
    mesh.count = 0;
    scene.add(mesh);
    return mesh;
  }
  const { base, shaft, arm, head } = LAMP_PARTS;
  const posts = instances(mergeGeometries([box(base), box(shaft), box(arm)]),
    new THREE.MeshLambertMaterial({ color: 0x3c4248 }));
  const heads = instances(box(head), new THREE.MeshBasicMaterial());
  const pools = instances(pool(), new THREE.MeshBasicMaterial({
    vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  }));

  const matrix = new THREE.Matrix4();
  const position = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  const straight = new THREE.Quaternion();
  const up = new THREE.Vector3(0, 1, 0);
  const unit = new THREE.Vector3(1, 1, 1);
  const colour = new THREE.Color();
  let drawn = '';

  function finish(mesh, count) {
    mesh.count = count;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }

  return {
    posts, heads, pools,
    draw(focus, radius, night) {
      // They only move when knocked down, so mostly there is nothing to redo.
      const key = `${Math.round(focus.x / 10)},${Math.round(focus.z / 10)},${radius},${night}`;
      if (key === drawn && !poles.down.size) return;
      drawn = poles.down.size ? '' : key;

      const ci = Math.round(focus.x / PITCH);
      const cj = Math.round(focus.z / PITCH);
      const span = Math.ceil(radius / PITCH) + 1;
      let n = 0;
      let lit = 0;
      for (let i = ci - span; i <= ci + span; i++)
        for (let j = cj - span; j <= cj + span; j++)
          for (let k = 0; k < 4; k++) {
            if (!hasLamp(i, j, k)) continue;
            const place = lampPlace(i, j, k);
            if (n >= most || Math.hypot(place.x - focus.x, place.z - focus.z) > radius) continue;
            const fallen = poles.down.get(lampKey(i, j, k));
            if (fallen) {
              position.copy(fallen.position);
              rotation.copy(fallen.rotation);
            } else {
              position.set(place.x, 0, place.z);
              rotation.setFromAxisAngle(up, place.yaw);
            }
            matrix.compose(position, rotation, unit);
            posts.setMatrixAt(n, matrix);
            heads.setMatrixAt(n, matrix);
            heads.setColorAt(n, colour.set(fallen ? BROKEN : night ? SHINING : UNLIT));
            n++;
            if (!night || fallen) continue;
            position.set(place.x + Math.cos(place.yaw) * head.x, 0, place.z - Math.sin(place.yaw) * head.x);
            pools.setMatrixAt(lit++, matrix.compose(position, straight, unit));
          }
      finish(posts, n);
      finish(heads, n);
      finish(pools, lit);
    },
  };
}
