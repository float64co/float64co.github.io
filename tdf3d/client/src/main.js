import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { generateCity, PITCH, SEED } from './city.js';
import { buildWorld } from './world.js';
import { createGarage } from './garage.js';
import { createConsole } from './console.js';
import { createCommands } from './commands.js';
import { createVariables } from './variables.js';
import { createFps } from './fps.js';
import { createPerf } from './perf.js';
import { createTraffic } from './traffic.js';
import { createCamera } from './camera.js';
import { createCombat } from './combat.js';
import { createIntro } from './intro.js';
import { carjack } from './carjack.js';
import { createRadio } from './radio.js';
import { createEnding } from './ending.js';
import { MAX_LEVEL } from './wanted.js';
import { createClock } from './clock.js';
import { createSky } from './sky.js';
import { createHeadlights } from './headlights.js';
import { PLAYER_HP } from './weapons.js';

const STEP = 1 / 60;
const hud = document.getElementById('hud');
const wantedStars = document.getElementById('wanted');
const ammoCount = document.getElementById('ammo');
const radioPanel = document.getElementById('radio');
const radioStation = document.getElementById('radio-station');
const radioTrack = document.getElementById('radio-track');

await RAPIER.init();
const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
physics.timestep = STEP;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
document.body.append(renderer.domElement);

const scene = new THREE.Scene();
const sky = createSky(scene);
const headlights = createHeadlights(scene);
const clock = createClock();

const camera = new THREE.PerspectiveCamera(65, innerWidth / innerHeight, 1);
let drawDistance;
const view = {
  get: () => drawDistance,
  set(metres) {
    drawDistance = metres;
  },
};
view.set(1600);
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

const world = buildWorld(scene, physics, generateCity());
// x = 0 and z = 0 are road centre lines (even block count).
const traffic = createTraffic(scene, physics, SEED);
traffic.squad.enabled = true; // var_squad=0 sends them home
const garage = createGarage(scene, physics, { x: 0, y: 1.5, z: PITCH / 2 }, 'car',
  (spot) => traffic.clearNear(spot));

const fps = createFps(document.getElementById('fps'));
// No sound where there is nothing to play it with.
const radio = createRadio(globalThis.Audio ? new Audio()
  : { paused: true, play() {}, pause() {}, addEventListener() {} });
fetch('music/tracks.json').then((reply) => reply.json()).then((list) => radio.load(list)).catch(() => {});
const perf = createPerf(document.getElementById('perf'), renderer);
// How sharp to draw: 1 is the usual, lower is quicker for the graphics card.
const USUAL = Math.min(devicePixelRatio, 2);
let sharpness = 1;
const resolution = {
  get: () => sharpness,
  set(value) {
    sharpness = value;
    renderer.setPixelRatio(USUAL * value);
    renderer.setSize(innerWidth, innerHeight);
  },
};
const keys = new Set();
const chase = createCamera(camera, physics);
const combat = createCombat(scene, camera, physics, traffic);
const crosshair = document.getElementById('crosshair');
const player = { hp: PLAYER_HP };
const walking = () => (garage.current.name === 'on foot' ? garage.current : null);
let scrolled = 0;
let notice = '';
let noticeUntil = 0;
const ending = createEnding({
  words: document.getElementById('verdict'), screen: renderer.domElement, garage, traffic, player,
});
// Click the game to capture the pointer, Esc to let it go. It stays captured
// from foot to vehicle and back. Only the console borrows it.
const canvas = renderer.domElement;
// Browsers refuse for a moment after Esc; another click will do it.
const captureMouse = () => canvas.requestPointerLock()?.catch(() => {});
let borrowed = false; // the console took the pointer and should give it back
const devConsole = createConsole(
  createCommands({ garage }), createVariables({ garage, view, fps, traffic, clock, perf, resolution, radio }), (open) => {
  keys.clear();
  if (open) {
    borrowed = Boolean(document.pointerLockElement);
    if (borrowed) document.exitPointerLock();
  } else if (borrowed) captureMouse();
  });
canvas.addEventListener('click', () => {
  if (!devConsole.isOpen() && !document.pointerLockElement) captureMouse();
});
// Scroll to change weapon, on foot. Trackpads send bursts, so limit the rate.
addEventListener('wheel', (e) => {
  if (devConsole.isOpen() || !walking() || !e.deltaY) return;
  if (performance.now() - scrolled < 150) return;
  scrolled = performance.now();
  combat.arsenal.cycle(e.deltaY);
  trigger = false; // a new weapon needs a new pull
});
// Left click fires once the mouse is captured; automatic weapons keep firing
// for as long as it is held.
let trigger = false;
const release = () => { trigger = false; };
addEventListener('mouseup', (e) => { if (e.button === 0) release(); });
addEventListener('blur', release);
addEventListener('pointerlockchange', release);
const canShoot = () => document.pointerLockElement === canvas && walking() && !walking().down
  && !ending.active && !devConsole.isOpen();
// With a gun out this fires it; bare handed it throws a punch.
function shoot() {
  const hit = combat.arsenal.weapon.magazine ? combat.fire(walking()) : combat.punch(walking());
  if (hit?.hp === undefined) return;
  notice = hit.hp ? `Target HP ${hit.hp}` : 'Target down';
  noticeUntil = performance.now() + 2000;
}
canvas.addEventListener('mousedown', (e) => {
  if (e.button !== 0 || !canShoot()) return;
  trigger = true;
  shoot();
});
addEventListener('mousemove', (e) => {
  if (document.pointerLockElement === canvas) garage.current.look?.(e.movementX, e.movementY);
});
addEventListener('keydown', (e) => {
  if (devConsole.isOpen() || e.code === 'Backquote') return;
  keys.add(e.code);
  if (e.code === 'KeyR' && !e.repeat && walking() && !walking().down && !ending.active) combat.arsenal.reload();
  if (e.code === 'KeyV' && !e.repeat) chase.toggle(garage.current);
  // Any car can take to the air, and drop out of it again.
  if (e.code === 'KeyG' && !e.repeat && !ending.active) {
    const car = garage.current;
    car.setFlying?.(!car.flying);
  }
  if (e.code === 'KeyN' && !e.repeat && !walking()) radio.next();
  if (e.code === 'KeyB' && !e.repeat && !walking()) radio.nextStation();
  if (e.code === 'KeyM' && !e.repeat) radio.on = !radio.on;
  if (e.code === 'KeyF' && !e.repeat && !garage.current.down && !ending.active) {
    notice = carjack(garage, traffic) ? '' : garage.toggle() ?? '';
    noticeUntil = performance.now() + 2000;
  }
  if (e.code === 'Space' || e.code.startsWith('Arrow')) e.preventDefault();
});
addEventListener('keyup', (e) => keys.delete(e.code));
addEventListener('blur', () => keys.clear());
const held = (...codes) => (codes.some((c) => keys.has(c)) ? 1 : 0);

let last = performance.now();
let pending = 0;
const intro = createIntro();
let warmed = false; // the first frame of the game is drawn behind the intro

renderer.setAnimationLoop((now) => {
  fps.frame(now);
  perf.frame();
  let frame = Math.min((now - last) / 1000, 0.1);
  last = now;
  if (intro && !intro.done) {
    intro.frame(frame);
    // Until the game can be seen through it, leave the machine to the intro.
    if (warmed && !intro.revealing) return;
    warmed = true;
    frame = 0; // the world waits until the intro is over
  }
  pending += frame;
  // Fixed timestep: driving behaves the same at any frame rate.
  while (pending >= STEP) {
    perf.time('vehicle', () => garage.drive(ending.active ? { throttle: 0, strafe: 0, turn: 0, steer: 0, brake: true } : {
      throttle: held('KeyW', 'ArrowUp') - held('KeyS', 'ArrowDown'),
      steer: held('KeyA', 'ArrowLeft') - held('KeyD', 'ArrowRight'),
      // On foot, A/D step sideways and the arrows turn.
      strafe: held('KeyA') - held('KeyD'),
      turn: held('ArrowLeft') - held('ArrowRight'),
      thirdPerson: !chase.firstPerson,
      brake: keys.has('Space'),
      handbrake: keys.has('Space'),
      descend: held('ShiftLeft', 'ShiftRight') === 1,
      run: held('ShiftLeft', 'ShiftRight') === 1,
      suit: traffic.suit.on,
    }, STEP));
    perf.time('walls', () => world.update([garage.current, ...garage.parked.values(),
      ...traffic.wrecks.map((w) => w.car), ...traffic.police.cars()].map((v) => v.body.translation())));
    perf.time('traffic+police', () => traffic.update(garage, STEP));
    ending.step(STEP, { damage: traffic.takeDamage(), busted: traffic.takeBusted() });
    if (ending.active) trigger = false;
    // Once nobody is after you, you recover.
    if (!traffic.wanted.level && !ending.active) player.hp = Math.min(PLAYER_HP, player.hp + 5 * STEP);
    perf.time('physics', () => physics.step());
    pending -= STEP;
  }
  const vehicle = garage.current;
  garage.sync();
  const { position, quaternion } = vehicle.mesh;
  if (position.y < -10) garage.reset();

  combat.arsenal.bottomless = traffic.suit.on;
  const armed = Boolean(combat.arsenal.weapon.magazine);
  combat.update(frame, walking(), chase.firstPerson);
  chase.update(vehicle, frame, armed, !vehicle.look && held('KeyC') === 1);
  if (!canShoot()) trigger = false;
  else if (trigger && combat.arsenal.weapon.auto) shoot();
  crosshair.hidden = !(walking() && armed);

  // Higher up: see further, and the sky darkens towards space.
  const height = Math.max(0, position.y);
  scene.fog.far = view.get() + height * 1.5;
  scene.fog.near = scene.fog.far * 0.35;
  camera.far = scene.fog.far * 1.05;
  camera.updateProjectionMatrix();

  const hour = clock.hour();
  const night = clock.night();
  sky.update(hour, camera, height);
  world.setNight(night);
  headlights.update(vehicle, night);
  perf.time('prepare drawing', () => traffic.draw(position, night));
  perf.render(() => renderer.render(scene, camera));
  const altitude = height > 3 ? `   alt ${Math.round(height)} m` : '';
  const { weapon, ammo, reloading } = combat.arsenal;
  const { bottomless } = combat.arsenal;
  const carried = !walking() ? '' : weapon.magazine
    ? `   ${weapon.name} ${bottomless ? '∞' : reloading ? 'reloading' : `${ammo}/${weapon.magazine}`}`
    : '   unarmed';
  const { condition } = vehicle;
  const carState = !condition ? '' : condition.burnt ? ' BURNT OUT' : condition.hp <= 0 ? ' ON FIRE, get out!'
    : ` ${Math.ceil(condition.hp)}%`;
  const { level, evading } = traffic.wanted;
  // Rounds left in the magazine, out of how many it holds.
  ammoCount.hidden = !(walking() && weapon.magazine && !walking().down && !ending.active);
  ammoCount.textContent = ammoCount.hidden ? '' : bottomless ? '∞' : `${ammo} / ${weapon.magazine}`;
  ammoCount.style.opacity = reloading ? 0.45 : 1; // dimmed while reloading
  // Heard from any vehicle that is not a burnt out shell, once the intro is over.
  radio.update(!walking() && !vehicle.condition?.burnt && !(intro && !intro.done));
  const { title } = radio;
  radioPanel.hidden = !title;
  radioStation.textContent = title ? radio.station : '';
  radioTrack.textContent = title ? `♪ ${title}` : '';
  wantedStars.textContent = level ? '★'.repeat(level) + '☆'.repeat(MAX_LEVEL - level) : '';
  const status = !level ? ''
    : traffic.police.arrest ? `\nbeing arrested ${Math.round(traffic.police.arrest * 100)}%`
    : traffic.police.pulling ? `\nbeing pulled out ${Math.round(traffic.police.pulling * 100)}%`
      : evading === null ? '' : `\nlosing them ${Math.ceil(evading)}s`;
  hud.textContent = `${clock.label()}   HP ${Math.ceil(player.hp)}${traffic.suit.on ? '   VAPOUR SUIT' : ''}${carried}\n${vehicle.name}${carState}   ${Math.round(Math.abs(vehicle.speed()) * 2.236936)} mph${altitude}   seed ${SEED}\n`
    + `${vehicle.help}\nF get in/out${vehicle.name === 'on foot' ? ' · V view · scroll weapon · click fire/punch · R reload' : ' · hold C look back · N next track · B station · M radio'} · \` console (var_vehicle=<name>)`
    + (traffic.squad.enabled ? `\nSquad${traffic.squad.suits ? ' (suited)' : ''}: ${traffic.squad.members.map((m) =>
      `${m.name} ${m.hp > 0 ? Math.ceil(m.hp) : 'down'}${m.reloading > 0 ? ' reloading' : ''}`).join(' · ')}` : '')
    + status + (now < noticeUntil && notice ? `\n${notice}` : '');
});
