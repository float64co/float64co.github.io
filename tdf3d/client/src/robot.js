// Some of the people in the street are humanoid robots: smooth white shells over
// dark joints, and a glass face with a strip of light. They behave like anyone
// else. Parts are the same size and joints as a person's, so walking, being
// knocked over and being shot all work the same way.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { TORSO, LEG, ARM, FOREARM, THIGH } from './people.js';

export const SHARE = { robots: 5, of: 30 }; // this many in every so many are robots
export const isRobot = (id) => (Math.imul(id + 7, 1597334677) >>> 12) % SHARE.of < SHARE.robots;

const SHELL = 0xe6e8ea;  // white composite
const METAL = 0x70777e;  // brushed aluminium
const BLACK = 0x17191c;  // actuators and joints
const GLASS = 0x05070a;
const LIGHT = 0x7fe9ff;

function painted(geometry, colour) {
  const part = geometry.index ? geometry.toNonIndexed() : geometry;
  const { r, g, b } = new THREE.Color(colour);
  const colours = new Float32Array(part.attributes.position.count * 3);
  for (let n = 0; n < colours.length; n += 3) colours.set([r, g, b], n);
  part.setAttribute('color', new THREE.BufferAttribute(colours, 3));
  return part;
}
const box = (w, h, d, colour, x = 0, y = 0, z = 0) => painted(new THREE.BoxGeometry(w, h, d).translate(x, y, z), colour);
// An upright, possibly tapering, round bar.
const bar = (top, bottom, length, colour, x, y, z, sides = 12) => painted(
  new THREE.CylinderGeometry(top, bottom, length, sides).translate(x, y, z), colour);
// A hinge, lying across the body.
const hinge = (r, width, colour, x, y, z = 0, sides = 12) => painted(
  new THREE.CylinderGeometry(r, r, width, sides).rotateZ(Math.PI / 2).translate(x, y, z), colour);
const egg = (rx, ry, rz, colour, x, y, z, across = 16, up = 12) => painted(
  new THREE.SphereGeometry(1, across, up).scale(rx, ry, rz).translate(x, y, z), colour);
// A solid of revolution, flattened front to back. profile: [radius, height].
const shell = (profile, depth, colour, sides = 18) => painted(new THREE.LatheGeometry(
  profile.map(([r, y]) => new THREE.Vector2(r, y)), sides).scale(1, 1, depth), colour);

const arc = Math.PI * 0.8; // how far the face wraps round
// Part of the front of a head-shaped surface, facing +z.
const face = (rx, ry, rz, colour, from, span, across = 14, up = 8) => painted(
  new THREE.SphereGeometry(1, across, up, (Math.PI - arc) / 2, arc, from, span).scale(rx, ry, rz), colour);

function detailed() {
  const torso = mergeGeometries([
    // Chest, broad at the shoulders.
    shell([[0.001, 0.02], [0.13, 0.03], [0.17, 0.12], [0.215, 0.23], [0.2, 0.3], [0.1, 0.34], [0.001, 0.35]], 0.68, SHELL),
    box(0.13, 0.008, 0.012, BLACK, 0, 0.255, 0.134),        // panel line across the chest
    // A ribbed black waist.
    ...[0.015, -0.025, -0.065, -0.105].map((y) => shell(
      [[0.001, y - 0.017], [0.09, y - 0.017], [0.108, y], [0.09, y + 0.017], [0.001, y + 0.017]], 0.75, BLACK, 14)),
    // Pelvis.
    shell([[0.001, -0.3], [0.09, -0.29], [0.165, -0.22], [0.15, -0.14], [0.08, -0.12], [0.001, -0.12]], 0.7, SHELL),
    egg(0.115, 0.125, 0.045, METAL, 0, 0.18, -0.105),       // battery, set into the back
    box(0.09, 0.012, 0.01, BLACK, 0, 0.21, -0.149),
    hinge(0.05, 0.46, BLACK, 0, 0.3),                       // shoulder axle
  ]);
  const head = mergeGeometries([
    egg(0.112, 0.138, 0.128, SHELL, 0, 0.012, -0.004, 20, 14), // helmet
    face(0.114, 0.14, 0.133, GLASS, Math.PI * 0.27, Math.PI * 0.46).translate(0, 0.012, -0.002),
    hinge(0.038, 0.022, METAL, 0.112, 0.005, -0.01),           // ears
    hinge(0.038, 0.022, METAL, -0.112, 0.005, -0.01),
    bar(0.04, 0.048, 0.13, BLACK, 0, -0.17, -0.015),           // neck
    bar(0.01, 0.01, 0.14, METAL, 0.042, -0.17, 0.012, 6),      // with two struts
    bar(0.01, 0.01, 0.14, METAL, -0.042, -0.17, 0.012, 6),
  ]);
  // A thin band of light across the glass.
  const eyes = face(0.1155, 0.1415, 0.1348, LIGHT, Math.PI * 0.46, Math.PI * 0.035, 14, 1).translate(0, 0.012, -0.002);
  const thigh = mergeGeometries([
    hinge(0.05, 0.1, BLACK, 0, 0),                         // hip
    bar(0.075, 0.052, 0.27, SHELL, 0, -0.175, 0.004),      // thigh
    bar(0.018, 0.018, 0.26, METAL, 0, -0.17, -0.064, 8),   // the strut behind it
  ]);
  const shin = mergeGeometries([ // hanging from the knee, down to the sole
    hinge(0.052, 0.13, BLACK, 0, -0.35),                   // knee
    egg(0.045, 0.055, 0.03, SHELL, 0, -0.35, 0.045, 10, 8),
    bar(0.062, 0.036, 0.27, SHELL, 0, -0.51, -0.004),      // shin
    bar(0.015, 0.015, 0.22, METAL, 0, -0.52, -0.058, 8),
    hinge(0.032, 0.1, BLACK, 0, -0.655),                   // ankle
    box(0.12, 0.036, 0.2, METAL, 0, -LEG.h + 0.03, 0.035), // foot
    hinge(0.022, 0.12, SHELL, 0, -LEG.h + 0.032, 0.14, 10),
    box(0.125, 0.012, 0.23, BLACK, 0, -LEG.h + 0.006, 0.05), // sole
  ]).translate(0, THIGH, 0);
  const arm = mergeGeometries([
    egg(0.058, 0.062, 0.062, SHELL, 0, -0.008, 0),
    bar(0.028, 0.028, ARM.h, BLACK, 0, -ARM.h / 2, 0),
    bar(0.052, 0.038, ARM.h * 0.62, SHELL, 0, -ARM.h * 0.56, 0),
  ]);
  const forearm = mergeGeometries([
    hinge(0.037, 0.088, BLACK, 0, 0),                      // elbow
    bar(0.046, 0.029, 0.19, SHELL, 0, -0.13, 0),
    bar(0.021, 0.021, 0.035, BLACK, 0, -0.235, 0, 8),      // wrist
    box(0.036, 0.05, 0.07, METAL, 0, -0.27, 0),            // palm
    ...[-0.022, 0, 0.022].map((z) => box(0.017, 0.05, 0.016, BLACK, 0, -FOREARM.h - 0.018, z)),
    box(0.016, 0.04, 0.016, BLACK, 0, -0.28, 0.048),       // thumb
  ]);
  return { torso, head, eyes, thigh, shin, arm, forearm };
}

// The same outline and colours for those too far off to make out.
function simple() {
  const torso = mergeGeometries([
    box(0.4, 0.31, 0.27, SHELL, 0, 0.17, 0), box(0.18, 0.15, 0.15, BLACK, 0, -0.05, 0),
    box(0.3, 0.17, 0.2, SHELL, 0, -0.21, 0),
  ]);
  const head = mergeGeometries([
    egg(0.112, 0.138, 0.128, SHELL, 0, 0.012, -0.004, 8, 5),
    box(0.15, 0.09, 0.02, GLASS, 0, 0.015, 0.12), box(0.07, 0.14, 0.07, BLACK, 0, -0.17, -0.015),
  ]);
  const eyes = box(0.13, 0.016, 0.006, LIGHT, 0, 0.022, 0.134);
  const thigh = box(0.12, THIGH, 0.11, SHELL, 0, -THIGH / 2, 0);
  const shin = mergeGeometries([
    box(0.09, 0.3, 0.09, SHELL, 0, -0.15, 0),
    box(0.12, 0.05, 0.22, BLACK, 0, -THIGH + 0.025, 0.045),
  ]);
  const arm = box(0.1, ARM.h, 0.1, SHELL, 0, -ARM.h / 2, 0);
  const forearm = mergeGeometries([
    box(0.075, 0.22, 0.075, SHELL, 0, -0.11, 0), box(0.04, 0.09, 0.07, BLACK, 0, -FOREARM.h + 0.03, 0),
  ]);
  return { torso, head, eyes, thigh, shin, arm, forearm };
}

export const robotGeometries = ({ detail = true } = {}) => (detail ? detailed() : simple());
// Glossy, so that the shells catch the light.
export const robotMaterial = () => new THREE.MeshPhongMaterial({ vertexColors: true, specular: 0x555555, shininess: 60 });
