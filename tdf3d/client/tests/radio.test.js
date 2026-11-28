import test from 'node:test';
import assert from 'node:assert/strict';
import { createRadio, addressOf, titleOf } from '../src/radio.js';

function player() {
  const listeners = {};
  const audio = {
    paused: true, src: '', volume: 1, refuse: false, played: [],
    play() {
      if (audio.refuse) return Promise.reject(new Error('not allowed'));
      audio.paused = false;
      audio.played.push(audio.src);
      return Promise.resolve();
    },
    pause() { audio.paused = true; },
    addEventListener: (type, fn) => { listeners[type] = fn; },
    end() { audio.paused = true; listeners.ended(); },
    fail() { listeners.error(); },
  };
  return audio;
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
const tracks = ['a.mp3', "Where's Your Head At.mp3", 'c #3.mp3', 'd (bootleg).mp3'];

test('file names make safe addresses and readable titles', () => {
  assert.equal(addressOf('synth/Night #1?.mp3'), 'music/synth/Night%20%231%3F.mp3');
  assert.equal(titleOf('synth/Toaster-sized Fusion Reactor.mp3'), 'Toaster-sized Fusion Reactor');
});

test('plays only while listening and keeps its place', async () => {
  const audio = player();
  const radio = createRadio(audio);
  radio.update(true); // nothing loaded yet
  assert.ok(audio.paused);
  radio.load(tracks);
  radio.update(false);
  assert.ok(audio.paused);
  assert.equal(radio.title, null);
  radio.update(true);
  await settle();
  assert.ok(!audio.paused && radio.title);
  const src = audio.src;
  radio.update(false);
  assert.ok(audio.paused);
  radio.update(true);
  await settle();
  assert.equal(audio.src, src);
  radio.on = false;
  radio.update(true);
  assert.ok(audio.paused);
  radio.volume = 0.2;
  assert.equal(audio.volume, 0.2);
});

test('every track plays once before any repeats, and none twice running', async () => {
  const audio = player();
  const radio = createRadio(audio);
  radio.load(tracks);
  radio.update(true);
  const heard = [];
  for (let i = 0; i < 40; i++) {
    await settle();
    heard.push(audio.src);
    audio.end();
  }
  for (let i = 0; i + 4 <= heard.length; i += 4) assert.equal(new Set(heard.slice(i, i + 4)).size, 4);
  heard.forEach((src, i) => assert.notEqual(src, heard[i - 1]));
  const before = audio.src;
  radio.next();
  assert.notEqual(audio.src, before);
});

test('tries again after the browser refuses, and gives up on broken files', async () => {
  const audio = player();
  const radio = createRadio(audio);
  radio.load(tracks);
  audio.refuse = true;
  radio.update(true);
  await settle();
  assert.ok(audio.paused);
  audio.refuse = false;
  radio.update(true);
  await settle();
  assert.ok(!audio.paused);
  const tried = new Set();
  for (let i = 0; i < 20; i++) {
    audio.fail();
    tried.add(audio.src);
  }
  assert.ok(tried.size <= 4);
  const stuck = audio.src;
  audio.fail();
  assert.equal(audio.src, stuck);
});

const stations = ['dnb/one.mp3', 'dnb/two.mp3', 'dnb/three.mp3', 'dubstep/four.mp3', 'dubstep/five.mp3'];

test('folders are named stations, and skipping stays on the station', async () => {
  const audio = player();
  const radio = createRadio(audio);
  radio.load(stations);
  radio.update(true);
  await settle();
  assert.deepEqual(radio.stations, ['dnb', 'dubstep']);
  assert.equal(radio.station, 'Amen FM');
  for (let i = 0; i < 12; i++) {
    radio.next();
    assert.match(audio.src, /^music\/dnb\//);
  }
  radio.nextStation();
  assert.equal(radio.station, 'Wobble FM');
  for (let i = 0; i < 8; i++) {
    assert.match(audio.src, /^music\/dubstep\//);
    const before = audio.src;
    radio.next();
    assert.notEqual(audio.src, before);
  }
  radio.nextStation();
  assert.equal(radio.station, 'Amen FM');
});

test('going back to a station carries on where you left it', async () => {
  const audio = player();
  const radio = createRadio(audio);
  radio.load(stations);
  radio.update(true);
  await settle();
  const left = audio.src;
  audio.currentTime = 42;
  assert.ok(radio.tune('dubstep'));
  audio.currentTime = 7;
  assert.ok(radio.tune('dnb'));
  assert.equal(audio.src, left);
  assert.equal(audio.currentTime, 42);
  assert.ok(!radio.tune('jazz'));
  radio.next();
  assert.notEqual(audio.src, left);
});
