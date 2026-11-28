// Boots src/main.js with a stand-in page and renderer, and runs some frames.
// Other tests never load main.js, so mistakes there would go unnoticed.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, rm } from 'node:fs/promises';

function element() {
  const listeners = {};
  const children = new Map();
  return {
    listeners, hidden: true, textContent: '', value: '', scrollTop: 0, scrollHeight: 0, style: {},
    addEventListener: (type, fn) => (listeners[type] ??= []).push(fn),
    querySelector(selector) {
      if (!children.has(selector)) children.set(selector, element());
      return children.get(selector);
    },
    append() {}, replaceChildren() {}, focus() {}, blur() {}, scrollIntoView() {},
    requestPointerLock() {},
  };
}

test('the game starts, runs frames and handles getting out, switching and firing', async () => {
  const page = new Map();
  const listeners = {};
  const canvas = element();
  let loop;
  Object.assign(globalThis, {
    Audio: class {
      paused = true;
      play() { this.paused = false; return Promise.resolve(); }
      pause() { this.paused = true; }
      addEventListener() {}
    },
    fetch: async () => ({ json: async () => ['dnb/Night City.mp3'] }),
    innerWidth: 800, innerHeight: 600, devicePixelRatio: 1,
    addEventListener: (type, fn) => (listeners[type] ??= []).push(fn),
    document: {
      getElementById(id) {
        if (!page.has(id)) page.set(id, element());
        return page.get(id);
      },
      createElement: element,
      body: { append() {} },
      pointerLockElement: null,
      exitPointerLock() { this.releases = (this.releases ?? 0) + 1; },
    },
    testRenderer: {
      domElement: canvas, setPixelRatio() {}, setSize() {}, render() {},
      setAnimationLoop: (fn) => { loop = fn; },
    },
  });

  // The only change: there is no WebGL here.
  const source = await readFile(new URL('../src/main.js', import.meta.url), 'utf8');
  const stubbed = source.replace(/new THREE\.WebGLRenderer\([^)]*\)/, 'globalThis.testRenderer');
  assert.notEqual(stubbed, source);
  const copy = new URL('../src/main.under-test.js', import.meta.url);
  await writeFile(copy, stubbed);
  try {
    await import(copy.href);
  } finally {
    await rm(copy);
  }

  let now = performance.now();
  const frames = (n) => { for (let i = 0; i < n; i++) loop(now += 1000 / 30); };
  const send = (target, type, event = {}) => {
    for (const fn of target[type] ?? []) fn({ preventDefault() {}, ...event });
  };
  const hud = () => page.get('hud').textContent;

  frames(60);
  assert.match(hud(), /HP 100[\s\S]*car/);
  assert.ok(!page.get('radio').hidden);
  assert.equal(page.get('radio-station').textContent, 'Amen FM');
  assert.equal(page.get('radio-track').textContent, '♪ Night City');
  assert.doesNotMatch(hud(), /Night City|Amen FM/);
  send(listeners, 'keydown', { code: 'KeyF' });
  send(listeners, 'keyup', { code: 'KeyF' });
  frames(10);
  assert.match(hud(), /unarmed[\s\S]*on foot/);
  // The radio has its own place, and is not heard on foot.
  assert.ok(page.get('radio').hidden);
  // Unarmed, and in a car, there is no count.
  assert.ok(page.get('ammo').hidden);
  // Getting back into the car keeps hold of the mouse.
  document.pointerLockElement = canvas;
  for (const getting of [/car/, /on foot/]) {
    send(listeners, 'keyup', { code: 'KeyF' });
    send(listeners, 'keydown', { code: 'KeyF' });
    frames(10);
    assert.match(hud(), getting);
  }
  send(listeners, 'keyup', { code: 'KeyF' });
  assert.equal(document.releases ?? 0, 0);

  // In a car, a click captures it too, and the console only borrows it.
  send(listeners, 'keydown', { code: 'KeyF' });
  send(listeners, 'keyup', { code: 'KeyF' });
  frames(10);
  assert.match(hud(), /car/);
  let requests = 0;
  canvas.requestPointerLock = () => { requests++; };
  document.pointerLockElement = null;
  send(canvas.listeners, 'click');
  assert.equal(requests, 1, 'clicking in a car did not capture');
  document.pointerLockElement = canvas;
  const backquote = () => send(listeners, 'keydown', { code: 'Backquote' });
  backquote();
  assert.equal(document.releases, 1, 'the console needs the pointer');
  document.pointerLockElement = null;
  backquote();
  assert.equal(requests, 2, 'not given back');
  send(listeners, 'keydown', { code: 'KeyF' });
  send(listeners, 'keyup', { code: 'KeyF' });
  frames(10);
  assert.match(hud(), /on foot/);
  document.pointerLockElement = null;
  send(listeners, 'wheel', { deltaY: 100 });
  frames(10);
  assert.match(hud(), /Glock 17 17\/17/);
  const ammo = () => page.get('ammo');
  assert.equal(ammo().textContent, '17 / 17');
  assert.ok(!ammo().hidden);
  document.pointerLockElement = canvas;
  send(canvas.listeners, 'mousedown', { button: 0 });
  send(listeners, 'keydown', { code: 'KeyW' });
  send(listeners, 'keydown', { code: 'KeyV' });
  frames(10);
  assert.match(hud(), /Glock 17 16\/17/);
  assert.equal(ammo().textContent, '16 / 17');
  send(listeners, 'keydown', { code: 'KeyR' });
  send(listeners, 'keyup', { code: 'KeyR' });
  frames(2);
  assert.match(hud(), /Glock 17 reloading[\s\S]*on foot/);
  frames(60);
  assert.equal(ammo().textContent, '17 / 17');
  send(canvas.listeners, 'mousedown', { button: 0 });
  send(listeners, 'mouseup', { button: 0 });
  frames(2);
  // Firing earns a star, shown on its own rather than in the HUD.
  assert.equal(page.get('wanted').textContent, '★☆☆☆☆');
  assert.doesNotMatch(hud(), /★/);

  // Holding the button: the Glock stays at one shot, the BRN-180 keeps going.
  frames(30);
  assert.match(hud(), /Glock 17 16\/17/);
  send(listeners, 'mouseup', { button: 0 });
  await new Promise((resolve) => setTimeout(resolve, 200));
  send(listeners, 'wheel', { deltaY: 100 });
  frames(10);
  assert.match(hud(), /BRN-180 30\/30/);
  send(canvas.listeners, 'mousedown', { button: 0 });
  frames(29); // with the first shot, one second at 10 rounds a second
  const left = Number(/BRN-180 (\d+)\/30/.exec(hud())[1]);
  assert.ok(left >= 19 && left <= 21, `${left} left after a second`);
  assert.equal(ammo().textContent, `${left} / 30`);
  send(listeners, 'mouseup', { button: 0 });
  frames(1);
  const after = hud().match(/BRN-180 \d+/)[0];
  frames(30);
  assert.ok(hud().includes(after), 'still firing after release');
});
