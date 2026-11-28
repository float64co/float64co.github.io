// When things happen in the intro, and where. Pure functions.
//
// The word is a fixed object lying on the fractal, and there is one camera. So
// the two always move and grow together.
export const TIMES = {
  text: 3.4,   // the word starts to show
  out: 5.2,    // it is at reading size
  dive: 6.6,   // the camera heads for the "o"
  through: 8.7,
  end: 9,
};
// A point on the edge of the Mandelbrot set, in the "^" shaped valley that
// opens downwards: the mirror image of "seahorse valley". The word lies on it.
export const SPOT = { x: -0.743643887, y: -0.131825904 };
const FIRST = 1.35; // half the height of the view, at the start

const clamp = (v) => Math.max(0, Math.min(1, v));

// A smooth rising curve through [time, value, slope] points.
function curve(points, t) {
  if (t <= points[0][0]) return points[0][1];
  for (let n = 1; n < points.length; n++) {
    const [t1, v1, m1] = points[n];
    if (t > t1) continue;
    const [t0, v0, m0] = points[n - 1];
    const w = t1 - t0;
    const u = (t - t0) / w;
    return (2 * u ** 3 - 3 * u * u + 1) * v0 + (u ** 3 - 2 * u * u + u) * w * m0
      + (-2 * u ** 3 + 3 * u * u) * v1 + (u ** 3 - u * u) * w * m1;
  }
  return points[points.length - 1][1];
}

// layout: { width, height } of the screen; { middle, wide } of the word;
// { centre, radius } of the hole in the "o". Units of the word are the font's.
const readingScale = ({ width, height, word }) => Math.min(width * 0.55, height * 1.8) / word.wide;

// How far the camera has zoomed in, as a natural logarithm. It slows right down
// while the word is being read, then rushes through the "o".
const SHOW = 3.9; // all of this is fractal alone
const OUT = 5.1;
const DIVE = 5.45;
export function depth(t, layout) {
  // Far enough that the hole covers the whole screen.
  let more = 3.4;
  if (layout) {
    const last = (Math.hypot(layout.width, layout.height) / 2 / layout.hole.radius) * 1.1;
    more = Math.log(last / (readingScale(layout) * Math.exp(DIVE - OUT)));
  }
  return curve([
    [0, 0, 0.6], [TIMES.text, SHOW, 1.2], [TIMES.out, OUT, 0.35],
    [TIMES.dive, DIVE, 0.3], [TIMES.through, DIVE + more, 1.5],
  ], t);
}
const halfAt = (t, layout) => FIRST * Math.exp(-depth(t, layout));

// Closing in on the spot, sliding up the valley as it goes.
function approach(t, layout) {
  const half = halfAt(t, layout);
  const away = half / FIRST;
  return {
    x: SPOT.x + (-0.6 - SPOT.x) * away,
    y: SPOT.y + (0 - SPOT.y) * away - half * 0.35 * (1 - clamp(t / TIMES.end)),
    half,
  };
}

// Where a point of the word (in font units) lies in the complex plane.
function onPlane(point, layout) {
  // Complex units per font unit: constant, as the word is part of the scene.
  const size = (readingScale(layout) * 2 * halfAt(TIMES.out, layout)) / layout.height;
  return {
    x: SPOT.x + (point.x - layout.word.middle.x) * size,
    y: SPOT.y - (point.y - layout.word.middle.y) * size, // the font's y goes down
    size,
  };
}

// The camera: the centre of the view and half its height.
export function fractalView(t, layout) {
  if (!layout || t <= TIMES.dive) return approach(t, layout);
  // Zoom about the hole in the "o", so that it comes to the middle of the screen.
  const from = approach(TIMES.dive, layout);
  const to = onPlane(layout.hole.centre, layout);
  const half = halfAt(t, layout);
  const last = halfAt(TIMES.through, layout);
  const share = (half - last) / (from.half - last);
  return { x: to.x + (from.x - to.x) * share, y: to.y + (from.y - to.y) * share, half };
}

// How the word's font units map to the screen, as the camera sees them:
// screen = (x, y) + scale * point. null before the word appears.
export function wordView(t, layout) {
  if (t < TIMES.text) return null;
  const view = fractalView(t, layout);
  const origin = onPlane({ x: 0, y: 0 }, layout);
  const pixels = layout.height / (2 * view.half); // per complex unit
  return {
    scale: origin.size * pixels,
    x: layout.width / 2 + (origin.x - view.x) * pixels,
    y: layout.height / 2 - (origin.y - view.y) * pixels,
    alpha: clamp((t - TIMES.text) / 0.6),
    hole: clamp((t - TIMES.dive) / 0.5), // how far the hole has opened
  };
}

// The hole in a glyph: flood out from the middle of its bitmap over the pixels
// that are not ink. alpha(x, y) is 0 to 255. Returns { mask, centre, radius }.
export function findHole(alpha, width, height) {
  let [x0, y0, x1, y1] = [width, height, 0, 0];
  for (let y = 0; y < height; y++)
    for (let x = 0; x < width; x++) {
      if (alpha(x, y) < 128) continue;
      x0 = Math.min(x0, x); y0 = Math.min(y0, y);
      x1 = Math.max(x1, x); y1 = Math.max(y1, y);
    }
  const centre = { x: Math.round((x0 + x1) / 2), y: Math.round((y0 + y1) / 2) };
  const mask = new Uint8Array(width * height); // how much to cut out of each pixel
  const seen = new Uint8Array(width * height);
  const stack = [centre.y * width + centre.x];
  let radius = Infinity;
  while (stack.length) {
    const at = stack.pop();
    if (seen[at]) continue;
    seen[at] = 1;
    const x = at % width;
    const y = (at - x) / width;
    const ink = alpha(x, y);
    mask[at] = 255 - ink;
    if (ink >= 128 || x <= 0 || y <= 0 || x >= width - 1 || y >= height - 1) {
      radius = Math.min(radius, Math.hypot(x - centre.x, y - centre.y));
      continue;
    }
    stack.push(at - 1, at + 1, at - width, at + width);
  }
  return { mask, centre, radius };
}
