// The opening: a zoom along the edge of the Mandelbrot set, out of which comes
// "Float64", and then through the "o" into the game.
import { TIMES, fractalView, wordView, findHole } from './intro-timeline.js';

const FONT = 'italic 700 1000px "IBM Plex Sans"';
const WORD = 'Float64';
const BLUE = '#0f62fe';
const DETAIL = 960; // the fractal is worked out at most this many pixels wide

const VERTEX = 'attribute vec2 corner; void main() { gl_Position = vec4(corner, 0.0, 1.0); }';
const FRAGMENT = `
precision highp float;
uniform vec2 centre;
uniform vec2 size;
uniform float halfHeight;
uniform float limit;
vec3 shade(float t) {
  // Navy, through blue, to white, and round again.
  float k = fract(t) * 4.0;
  vec3 navy = vec3(0.0, 0.03, 0.15), blue = vec3(0.06, 0.38, 1.0);
  vec3 pale = vec3(0.55, 0.8, 1.0), white = vec3(1.0);
  if (k < 1.0) return mix(navy, blue, k);
  if (k < 2.0) return mix(blue, pale, k - 1.0);
  if (k < 3.0) return mix(pale, white, k - 2.0);
  return mix(white, navy, k - 3.0);
}
void main() {
  vec2 c = centre + (gl_FragCoord.xy * 2.0 - size) / size.y * halfHeight;
  vec2 z = vec2(0.0);
  float count = 0.0;
  for (int n = 0; n < 600; n++) {
    if (float(n) >= limit || dot(z, z) > 64.0) break;
    z = vec2(z.x * z.x - z.y * z.y, 2.0 * z.x * z.y) + c;
    count += 1.0;
  }
  if (dot(z, z) <= 64.0) { gl_FragColor = vec4(0.0, 0.01, 0.06, 1.0); return; }
  float smoothed = count + 1.0 - log2(log2(dot(z, z)) * 0.5);
  gl_FragColor = vec4(shade(sqrt(max(smoothed, 0.0)) * 0.35), 1.0);
}`;

function createFractal(canvas) {
  const gl = canvas.getContext('webgl');
  if (!gl) return null;
  const program = gl.createProgram();
  for (const [type, source] of [[gl.VERTEX_SHADER, VERTEX], [gl.FRAGMENT_SHADER, FRAGMENT]]) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    gl.attachShader(program, shader);
  }
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  gl.useProgram(program);
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  const uniform = (name) => gl.getUniformLocation(program, name);
  const [centre, size, half, limit] = ['centre', 'size', 'halfHeight', 'limit'].map(uniform);

  return (view) => {
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform2f(centre, view.x, view.y);
    gl.uniform2f(size, canvas.width, canvas.height);
    gl.uniform1f(half, view.half);
    gl.uniform1f(limit, Math.min(600, 120 + 110 * Math.log10(1.35 / view.half)));
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
}

// Measure the word and cut out the hole in its "o".
function measure(createCanvas) {
  const canvas = createCanvas();
  canvas.width = 900;
  canvas.height = 1100;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  context.font = FONT;
  const whole = context.measureText(WORD);
  const before = context.measureText(WORD.slice(0, 2)).width; // up to the "o"
  const origin = { x: 150, y: 800 };
  context.fillText('o', origin.x, origin.y);
  const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
  const hole = findHole((x, y) => data[(y * canvas.width + x) * 4 + 3], canvas.width, canvas.height);

  const cut = context.createImageData(canvas.width, canvas.height);
  for (let n = 0; n < hole.mask.length; n++) cut.data[n * 4 + 3] = hole.mask[n];
  context.putImageData(cut, 0, 0);

  const top = -whole.actualBoundingBoxAscent;
  const bottom = whole.actualBoundingBoxDescent;
  return {
    word: { wide: whole.width, top, bottom, middle: { x: whole.width / 2, y: (top + bottom) / 2 } },
    hole: {
      centre: { x: before + hole.centre.x - origin.x, y: hole.centre.y - origin.y },
      radius: hole.radius,
      image: canvas,
      at: { x: before - origin.x, y: -origin.y }, // where the cut-out goes
    },
  };
}

// Covers the page until it is over, or skipped by a click or a key. Returns null
// where it cannot be drawn.
export function createIntro(page = document) {
  const canvas = page.createElement('canvas');
  const context = canvas.getContext?.('2d');
  if (!context) return null;
  const small = page.createElement('canvas');
  const fractal = createFractal(small);
  canvas.id = 'intro';
  page.body.append(canvas);

  let time = 0;
  let layout = null; // set once the font has arrived
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    canvas.remove();
  };
  for (const type of ['pointerdown', 'keydown']) addEventListener(type, finish, { once: true });
  const ready = () => { layout = measure(() => page.createElement('canvas')); };
  page.fonts.load(FONT, WORD).then(ready, ready);

  function draw() {
    const width = Math.round(innerWidth * Math.min(devicePixelRatio, 2));
    const height = Math.round(innerHeight * Math.min(devicePixelRatio, 2));
    if (canvas.width !== width || canvas.height !== height) {
      Object.assign(canvas, { width, height });
      const shrink = Math.min(1, DETAIL / width);
      Object.assign(small, { width: Math.round(width * shrink), height: Math.round(height * shrink) });
    }
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.globalCompositeOperation = 'source-over';
    context.globalAlpha = 1;
    const scene = layout && { width, height, ...layout };
    if (fractal) {
      fractal(fractalView(time, scene));
      context.drawImage(small, 0, 0, width, height);
    } else {
      context.fillStyle = '#00081f';
      context.fillRect(0, 0, width, height);
    }
    const view = scene && wordView(time, scene);
    if (!view) return;
    const { word, hole } = layout;
    context.setTransform(view.scale, 0, 0, view.scale, view.x, view.y);
    context.globalAlpha = view.alpha;
    // White on blue.
    context.fillStyle = BLUE;
    context.fillRect(-220, word.top - 180, word.wide + 440, word.bottom - word.top + 360);
    context.fillStyle = '#fff';
    context.font = FONT;
    context.fillText(WORD, 0, 0);
    if (view.hole) {
      // Open the middle of the "o" onto the game behind.
      context.globalCompositeOperation = 'destination-out';
      context.globalAlpha = view.hole;
      context.drawImage(hole.image, hole.at.x, hole.at.y);
    }
  }

  return {
    get done() { return done; },
    // Whether anything behind can be seen yet.
    get revealing() { return done || time > TIMES.dive; },
    // dt in seconds. Long hitches don't skip ahead.
    frame(dt) {
      if (done) return;
      // Wait for the font, but not for ever.
      if (!layout && time >= TIMES.text) time = TIMES.text;
      time += Math.min(dt, 0.05);
      if (time >= TIMES.end) finish();
      else draw();
    },
    skip: finish,
  };
}
