// Proportions shared by walking pedestrians and their ragdolls.
// Heights are above the ground; limbs hang from their joint.
export const TORSO = { w: 0.44, h: 0.65, d: 0.28, y: 1.05 };
export const HEAD = { r: 0.19, y: 1.6 };
export const LEG = { w: 0.17, h: 0.7, x: 0.12, y: 0.72 };
export const ARM = { w: 0.12, h: 0.31, x: 0.3, y: 1.35 };  // upper arm, from the shoulder
export const FOREARM = { w: 0.1, h: 0.3 };                  // hangs from the elbow

// A leg is a thigh and a shin of equal length, hinged at the knee.
export const THIGH = LEG.h / 2;
// How far the knee is bent (positive folds the shin back). `stride` is the
// phase of the walk and `sign` the way this leg swings, its hip being turned by
// sin(stride) * sign. The knee folds while the leg is coming forward and stays
// nearly straight while it carries the weight. `amount` is 0 when standing.
export const kneeBend = (stride, sign, amount = 1) =>
  amount * (0.06 + 1.1 * Math.max(0, -Math.cos(stride) * sign) ** 1.5);

// How far the elbow is bent (negative is forwards) for a shoulder swung by `angle`.
export const elbowBend = (angle) => -(0.2 + Math.max(0, -angle) * 0.9);
