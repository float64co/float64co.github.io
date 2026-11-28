import test from 'node:test';
import assert from 'node:assert/strict';
import { createPerf } from '../src/perf.js';

test('averages each section over the frames, and does nothing while off', () => {
  let clock = 0;
  const element = { hidden: true, textContent: '' };
  const renderer = { info: { render: { calls: 12, triangles: 34000 } }, domElement: { width: 800, height: 600 } };
  const perf = createPerf(element, renderer, () => clock);
  const work = (ms) => () => { clock += ms; return ms; };
  perf.frame();
  assert.equal(perf.time('a', work(3)), 3);
  assert.equal(element.textContent, '');

  perf.set(true);
  for (let n = 0; n < 40; n++) {
    perf.frame();
    perf.time('simulate', work(4));
    perf.time('simulate', work(2)); // twice in a frame
    perf.render(work(3));
    clock += 11; // waiting for the screen
  }
  assert.match(element.textContent, /frame 20\.0 ms \(worst 20\)/);
  assert.match(element.textContent, /simulate +6\.0/);
  assert.match(element.textContent, /submit drawing +3\.0/);
  assert.match(element.textContent, /idle\/waiting +11\.0/);
  assert.match(element.textContent, /graphics card +n\/a/);
  assert.match(element.textContent, /draw calls 12\ntriangles 34k\npixels 800x600/);
  perf.set(false);
  assert.ok(element.hidden);
});
