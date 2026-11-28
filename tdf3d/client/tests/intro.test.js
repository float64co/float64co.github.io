import test from 'node:test';
import assert from 'node:assert/strict';
import { TIMES, fractalView, wordView, findHole } from '../src/intro-timeline.js';

// Is c in the set, as far as `limit` iterations can tell?
function inside(x, y, limit = 2000) {
  let [a, b] = [0, 0];
  for (let n = 0; n < limit; n++) {
    [a, b] = [a * a - b * b + x, 2 * a * b + y];
    if (a * a + b * b > 4) return false;
  }
  return true;
}

const layout = {
  width: 1600, height: 900,
  word: { wide: 3500, middle: { x: 1750, y: -350 } },
  hole: { centre: { x: 1000, y: -260 }, radius: 100 },
};
const screen = (view, p) => ({ x: view.x + view.scale * p.x, y: view.y + view.scale * p.y });

test('the zoom goes steadily deeper, with the edge of the set in view until the dive', () => {
  let before = Infinity;
  for (let t = 0; t <= TIMES.through; t += 0.1) {
    const view = fractalView(t, layout);
    assert.ok(view.half < before, `at ${t}`);
    before = view.half;
  }
  for (let t = 0; t <= TIMES.dive; t += 0.5) {
    const view = fractalView(t, layout);
    let ins = 0;
    const n = 40;
    for (let i = 0; i < n; i++)
      for (let j = 0; j < n; j++)
        ins += inside(view.x + view.half * 1.7 * (2 * i / n - 1), view.y + view.half * (2 * j / n - 1), 600) ? 1 : 0;
    assert.ok(ins > 0 && ins < n * n * 0.9, `at ${t}: ${ins} of ${n * n} inside, no edge to see`);
  }
});

test('the word moves and grows exactly as the fractal does', () => {
  // Any spot on the word stays over the same point of the complex plane.
  const point = { x: 400, y: -600 };
  const under = (t) => {
    const view = fractalView(t, layout);
    const p = screen(wordView(t, layout), point);
    const unit = (2 * view.half) / layout.height;
    return [view.x + (p.x - layout.width / 2) * unit, view.y - (p.y - layout.height / 2) * unit];
  };
  const first = under(TIMES.text);
  for (let t = TIMES.text; t <= TIMES.through; t += 0.2)
    under(t).forEach((v, k) => assert.ok(Math.abs(v - first[k]) < 1e-9, `slipped at ${t}`));
  // And it lies below the real axis, in the valley that opens downwards.
  assert.ok(first[1] < -0.05);
});

test('the word comes out, then the camera goes through the hole in the o', () => {
  assert.equal(wordView(TIMES.text - 0.1, layout), null);
  const small = wordView(TIMES.text, layout);
  const reading = wordView(TIMES.out, layout);
  assert.ok(small.scale < reading.scale / 3);
  for (const t of [TIMES.out, TIMES.dive]) {
    const view = wordView(t, layout);
    const left = screen(view, { x: 0, y: 0 });
    const right = screen(view, { x: layout.word.wide, y: -700 });
    assert.ok(left.x > 0 && right.x < layout.width && right.y > 0 && left.y < layout.height,
      `not all on screen at ${t}`);
  }
  const end = wordView(TIMES.through, layout);
  const hole = screen(end, layout.hole.centre);
  assert.ok(Math.hypot(hole.x - 800, hole.y - 450) < 1, `hole at ${hole.x}, ${hole.y}`);
  assert.ok(end.scale * layout.hole.radius > Math.hypot(1600, 900) / 2, 'the hole fills the screen');
  assert.equal(end.hole, 1);
});

test('the hole of a ring is found, and nothing outside it', () => {
  const size = 101;
  const ring = (x, y) => {
    const d = Math.hypot(x - 50, y - 50);
    return d >= 20 && d <= 40 ? 255 : 0;
  };
  const { mask, centre, radius } = findHole(ring, size, size);
  assert.deepEqual(centre, { x: 50, y: 50 });
  assert.ok(Math.abs(radius - 20) < 1.5, `${radius}`);
  assert.equal(mask[50 * size + 50], 255);
  assert.equal(mask[50 * size + 5], 0, 'outside the ring');
  assert.equal(mask[50 * size + 20], 0, 'in the ink');
});
