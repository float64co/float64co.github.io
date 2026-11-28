import test from 'node:test';
import assert from 'node:assert/strict';
import { createFps } from '../src/fps.js';

test('fps toggles and averages frame times', () => {
  const element = { hidden: true, textContent: '' };
  const fps = createFps(element);
  assert.equal(fps.toggle(), true);
  for (let i = 0; i <= 60; i++) fps.frame(i * 20);
  assert.equal(element.textContent, '50 FPS');
  assert.equal(fps.toggle(), false);
  assert.equal(element.hidden, true);
});
