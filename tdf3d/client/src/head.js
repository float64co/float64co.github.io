// A human head and neck as one geometry, coloured by vertex, so that it still
// takes only one instanced draw. Centred on the head, facing +z.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

export const HEAD_SIZE = { w: 0.125, h: 0.155, d: 0.14 }; // half sizes of the skull
export const NECK = { bottom: -0.25, top: -0.08 };         // below the centre of the head

function painted(geometry, colour) {
  const part = geometry.index ? geometry.toNonIndexed() : geometry;
  const { r, g, b } = new THREE.Color(colour);
  const colours = new Float32Array(part.attributes.position.count * 3);
  for (let n = 0; n < colours.length; n += 3) colours.set([r, g, b], n);
  part.setAttribute('color', new THREE.BufferAttribute(colours, 3));
  return part;
}

// The people in the crowd. width and jaw shape the face; nose is its size.
// hair: 'short', 'long', 'bun' or 'bald'.
const PEOPLE = [
  { skin: 0xf1c7a5, hair: 0x2a1a10, style: 'short' },
  { skin: 0x8d5a3b, hair: 0x120c08, style: 'short', width: 1.04, nose: 1.2, beard: true },
  { skin: 0xf6d3bb, hair: 0xc9a23f, style: 'long', width: 0.94, jaw: 1.2, nose: 0.8, eyes: 0x2f5f8f },
  { skin: 0x5c3826, hair: 0x0c0806, style: 'bun', width: 0.96, nose: 1.1 },
  { skin: 0xd9a577, hair: 0x141010, style: 'long', jaw: 0.9, nose: 0.9 },
  { skin: 0xeab793, hair: 0x7a3216, style: 'bun', width: 0.95, jaw: 1.15, eyes: 0x3f6f4a },
  { skin: 0xc68a5e, hair: 0x241710, style: 'bald', width: 1.08, jaw: 0.7, nose: 1.25, beard: true },
  { skin: 0xf0c3a0, hair: 0xb9b6b0, style: 'short', width: 0.98, jaw: 1.1, nose: 1.15, eyes: 0x4a5560 },
  { skin: 0x7a4a30, hair: 0x1a100a, style: 'long', width: 0.97, nose: 1.05 },
  { skin: 0xe2b08a, hair: 0x4b2d18, style: 'short', width: 1.05, jaw: 0.8, beard: true, eyes: 0x2f5f8f },
];
export const VARIANTS = PEOPLE.length;
export const personOf = (variant) => PEOPLE[variant];
// Which of them someone is: the same every time for the same id, and mixed up.
export const variantOf = (id) => (Math.imul(id + 1, 2654435761) >>> 16) % VARIANTS;

// An egg: a round cranium narrowing to the jaw and chin. shape: { width, jaw };
// jaw above 1 gives a pointed chin, below 1 a square one. part limits it to a
// piece of the surface, as [phiStart, phiLength, thetaStart, thetaLength].
function skull(across, up, grow = 1, shape = {}, part = [0, Math.PI * 2, 0, Math.PI]) {
  const { width = 1, jaw = 1 } = shape;
  const geometry = new THREE.SphereGeometry(1, across, up, ...part);
  const at = geometry.attributes.position;
  for (let n = 0; n < at.count; n++) {
    const [x, y, z] = [at.getX(n), at.getY(n), at.getZ(n)];
    const low = Math.max(0, -y) ** 2;
    at.setXYZ(n, x * HEAD_SIZE.w * width * (1 - 0.38 * jaw * low) * grow, y * HEAD_SIZE.h * grow,
      z * HEAD_SIZE.d * (1 - (z < 0 ? 0.45 : 0.08) * low) * grow);
  }
  geometry.computeVertexNormals();
  return geometry;
}

const neck = (sides) => new THREE.CylinderGeometry(0.058, 0.068, NECK.top - NECK.bottom, sides, 1, true)
  .translate(0, (NECK.top + NECK.bottom) / 2, -0.012);
const box = (w, h, d, x, y, z) => new THREE.BoxGeometry(w, h, d).translate(x, y, z);
// A thin sheet seen from both sides: the same again, facing the other way.
function lined(sheet) {
  const inner = sheet.clone();
  const index = inner.index.array;
  for (let n = 0; n < index.length; n += 3) [index[n + 1], index[n + 2]] = [index[n + 2], index[n + 1]];
  const normals = inner.attributes.normal.array;
  for (let n = 0; n < normals.length; n++) normals[n] = -normals[n];
  return mergeGeometries([sheet, inner]);
}

// Hair covers the top, tipped back to leave a forehead. Long hair also falls
// behind the ears to the shoulders, and a bun sits at the back.
function hairParts(style, colour, shape, across, up) {
  if (style === 'bald') return [];
  const cap = skull(across, up, 1.07, shape, [0, Math.PI * 2, 0, Math.PI * 0.52])
    .rotateX(-0.42).translate(0, 0.004, -0.004);
  const parts = [painted(cap, colour)];
  const back = [Math.PI, Math.PI]; // the half facing -z
  if (style === 'long') {
    const fall = new THREE.CylinderGeometry(1, 1.12, 0.25, across, 1, true, Math.PI / 2, Math.PI)
      .scale(HEAD_SIZE.w * (shape.width ?? 1) * 1.1, 1, HEAD_SIZE.d * 0.95).translate(0, -0.09, -0.004);
    parts.push(painted(lined(fall), colour));
  } else {
    // Short hair still comes down further behind than in front.
    parts.push(painted(skull(across, up, 1.06, shape, [...back, Math.PI * 0.3, Math.PI * 0.36]), colour));
  }
  if (style === 'bun') {
    parts.push(painted(new THREE.SphereGeometry(0.062, across / 2 + 3, up).translate(0, 0.07, -0.155), colour));
  }
  return parts;
}

// detail false gives a much cheaper one, for people too far off to tell.
export function headGeometry({
  skin = 0xd7a77a, hair = 0x3a2618, style = 'short', width = 1, jaw = 1, nose = 1,
  eyes = 0x2a1a10, beard = false, detail = true,
} = {}) {
  const shape = { width, jaw };
  if (!detail) {
    // Just the outline and colours.
    const cap = style === 'bald' ? [] : [painted(skull(6, 2, 1.07, shape, [0, Math.PI * 2, 0, Math.PI * 0.52])
      .rotateX(-0.42), hair)];
    if (style === 'long') {
      cap.push(painted(lined(new THREE.CylinderGeometry(0.135, 0.15, 0.22, 4, 1, true, Math.PI / 2, Math.PI)
        .translate(0, -0.08, -0.01)), hair));
    }
    return mergeGeometries([painted(skull(6, 4, 1, shape), skin), painted(neck(4), skin), ...cap]);
  }
  const shade = new THREE.Color(skin).multiplyScalar(0.8);
  const parts = [
    painted(skull(14, 11, 1, shape), skin),
    painted(neck(10), skin),
    ...hairParts(style, hair, shape, 14, 6),
    // The nose: a wedge from between the eyes down to its tip.
    painted(new THREE.ConeGeometry(0.022 * nose, 0.065 * (0.5 + nose / 2), 4).rotateX(-0.28)
      .translate(0, -0.012, 0.137 + 0.003 * nose), shade),
    painted(box(0.052, 0.011, 0.012, 0, -0.068, 0.119), 0x8a4a44), // mouth
  ];
  if (beard) {
    // Over the chin and jaw, below the mouth.
    parts.push(painted(skull(14, 4, 1.05, shape, [0, Math.PI, Math.PI * 0.69, Math.PI * 0.31]), hair));
  }
  for (const side of [-1, 1]) {
    parts.push(
      painted(box(0.032, 0.017, 0.008, side * 0.043, 0.018, 0.134), 0xf2eee8), // eye
      painted(box(0.014, 0.015, 0.008, side * 0.043, 0.018, 0.139), eyes), // iris
      painted(box(0.042, 0.009, 0.012, side * 0.044, 0.044, 0.134), hair),     // brow
      painted(new THREE.SphereGeometry(1, 6, 5).scale(0.014, 0.034, 0.022)
        .translate(side * 0.125 * width, 0.002, -0.008), shade), // ear
    );
  }
  return mergeGeometries(parts);
}

export const headMaterial = () => new THREE.MeshLambertMaterial({ vertexColors: true });
