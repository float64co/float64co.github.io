// Draws the traffic simulation and gives NPC cars solid bodies.
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { PITCH, BLOCKS } from './city.js';
import { createTrafficSim, limitsFor, LIMITS, RADIUS as REACH } from './traffic-sim.js';
import { signal } from './signals.js';
import { createRagdoll } from './ragdoll.js';
import { isRobot, robotGeometries, robotMaterial } from './robot.js';
import { headGeometry, headMaterial, personOf, variantOf, VARIANTS } from './head.js';
import { createLamps } from './lamps.js';
import { npcLamps, beam } from './headlights.js';
import { rollWheels, RADIUS as WHEEL_RADIUS } from './wheels.js';
import { createWrecks, overlaps, MAX_WRECKS } from './wrecks.js';
import { createWanted } from './wanted.js';
import { createSquad } from './squad.js';
import { createAirTraffic } from './air-traffic.js';
import { createPolice, LIVERY } from './police.js';
import { createCarBody } from './carbody.js';
import { createSmoke } from './smoke.js';
import { createSparkTrails } from './spark-trails.js';
import { createCondition, watch, burn, shot, onFire, SMOKING, BURN_HURT } from './damage.js';
import { runDown } from './knockdown.js';
import { nearestOn } from './officers.js';
import { createSuit, SUIT } from './suit.js';
import { createPoles, polePlace, poleKey, APPROACHES, PARTS } from './poles.js';
import { TORSO, HEAD, LEG, ARM, FOREARM, THIGH, elbowBend, kneeBend } from './people.js';

const RADIUS = { car: 1.8, motorbike: 0.8, bicycle: 0.7, helicopter: 2, spaceplane: 4, 'on foot': 0.4 };
const LIGHT = { green: 0x00e676, amber: 0xffc400, red: 0xff1744 };
// Half length and half width of each vehicle's footprint.
const FOOTPRINT = {
  car: [2, 1], motorbike: [0.9, 0.35], bicycle: [0.8, 0.3], helicopter: [2, 1], spaceplane: [4, 1.5],
};
const WHEELS = [[-0.9, 1.4], [0.9, 1.4], [-0.9, -1.4], [0.9, -1.4]]; // across, along
const DARK = 0x222222; // a light that has been knocked down
const FACES = 40;      // metres: nearer than this, people get detailed heads

export function createTraffic(scene, physics, seed) {
  const sim = createTrafficSim(seed);
  // Instance buffers are sized for the largest radius.
  const most = { cars: limitsFor('cars', REACH.max).count, peds: limitsFor('peds', REACH.max).count };
  const mostLights = Math.ceil(Math.PI * (REACH.max / PITCH + 1) ** 2) * APPROACHES.length;
  const bodies = new Map(); // car id -> kinematic body
  const ragdolls = new Map(); // fallen entry -> ragdoll
  const wrecks = createWrecks(physics, sim);
  const poles = createPoles(physics, (x, z, down) => sim.setPole(x, z, down));
  const wanted = createWanted();
  const suit = createSuit();
  const squad = createSquad(scene, physics, seed);

  // The player's friends, after the police have had their turn.
  function companions(garage, dt) {
    const { current } = garage;
    const p = current.body.translation();
    const v = current.body.linvel();
    const q = current.body.rotation();
    const size = FOOTPRINT[current.name];
    const player = {
      vehicle: current, body: current.body, x: p.x, y: p.y, z: p.z, vx: v.x, vz: v.z,
      yaw: Math.atan2(2 * (q.x * q.z + q.w * q.y), 1 - 2 * (q.x * q.x + q.y * q.y)),
      footprint: size ? wrecks.footprint(current.body, ...size) : null,
    };
    const boxes = [
      ...[...garage.parked.values()].filter((o) => FOOTPRINT[o.name]).map((o) => wrecks.footprint(o.body, ...FOOTPRINT[o.name])),
      ...[...police.cars(), ...wrecks.list.map((w) => w.car)].map((car) => wrecks.footprint(car.body)),
      ...(player.footprint ? [player.footprint] : []),
    ].filter((box) => !(box.y > 3));
    // What could run them down: anything but what the player is driving.
    const strikers = [
      ...sim.cars.map((car) => ({
        x: car.x, z: car.z, hx: car.hx, hz: car.hz, halfL: 2, halfW: 1,
        vx: car.hx * car.speed, vz: car.hz * car.speed,
      })),
      ...[...police.cars(), ...wrecks.list.map((w) => w.car)].map((car) => wrecks.footprint(car.body)),
    ];
    const mine = current.condition;
    const result = squad.update(player, police.attackers(), boxes, dt, {
      strikers, overlaps, calm: !wanted.level, burning: mine && onFire(mine) ? BURN_HURT : 0,
    });
    for (let n = 0; n < result.kills; n++) wanted.report('squadKill');
    for (const shot of result.shots) sim.alarm(shot.x, shot.z, 40);
  }
  const police = createPolice(scene, physics, { footprint: wrecks.footprint, overlaps }, seed);
  let busted = false;
  let damage = 0; // done to the player and not yet collected
  let drawnAt = null;

  // Where the rest of the player is while they lie on the ground.
  function sprawl(current) {
    const doll = current.down ? current.ragdoll : null;
    if (!doll) return null;
    return [doll.torso, doll.head, ...doll.legs, ...doll.shins].map((part) => {
      const { x, z } = part.translation();
      return { x, z };
    });
  }

  // Run the police for one step.
  function patrol(garage, dt) {
    const { current } = garage;
    const p = current.body.translation();
    const v = current.body.linvel();
    const size = FOOTPRINT[current.name];
    const player = {
      x: p.x, y: p.y, z: p.z, vx: v.x, vz: v.z, body: current.body, down: Boolean(current.down), suit: suit.on,
      parts: sprawl(current) ?? undefined,
      footprint: size ? wrecks.footprint(current.body, ...size) : null,
    };
    const near = (o) => Math.abs(o.x - p.x) < 260 && Math.abs(o.z - p.z) < 260;
    const others = [
      ...sim.cars.filter(near),
      // Officers brake for people in the road.
      ...sim.peds.filter((ped) => ped.onRoad && near(ped)),
      ...wrecks.list.map(({ car }) => car.body.translation()),
      ...[...garage.parked.values()].map((parked) => parked.body.translation()),
    ];
    const moving = (body, size) => wrecks.footprint(body, ...size);
    const parked = [...garage.parked.values()].filter((o) => FOOTPRINT[o.name])
      .map((o) => moving(o.body, FOOTPRINT[o.name]));
    const crashed = wrecks.list.map(({ car }) => wrecks.footprint(car.body));
    const result = police.update(player, wanted.level, others, [...parked, ...crashed], dt, squad.targets());
    if (result.rammed) wanted.report('ram');
    if (!suit.on) damage += result.damage; // bullets don't get through the suit
    // Rounds that reach the driver go through the car to get there.
    if (result.damage && current.condition) {
      const side = (result.shots.length % 2) * 2 - 1;
      shot(current.condition, { x: side, y: 0.2, z: (wanted.level % 3) * 0.5 - 0.5 }, { x: -side, y: 0, z: 0 }, result.damage);
    }
    for (const shot of result.shots) sim.alarm(shot.x, shot.z, 40);
    // Running an officer over counts against you; anyone else's doing doesn't.
    if (player.footprint && police.strike([player.footprint], overlaps, dt)) wanted.report('hitOfficer');
    police.strike(crashed, overlaps, dt);
    wanted.update(dt, result.seen);
    // Dragged out of the vehicle and dumped on the road.
    if (result.pulled && garage.eject(result.pulled.spot)) {
      const { spot, away } = result.pulled;
      garage.current.knock((away.x - spot.x) * 2, (away.z - spot.z) * 2);
    }
    // The police stay where they are: the arrest is there to be seen.
    if (result.busted) busted = true;
  }

  // One instanced mesh per part, so draw calls don't grow with the crowd.
  function instances(geometry, count, mat = new THREE.MeshLambertMaterial()) {
    const mesh = new THREE.InstancedMesh(geometry, mat, count);
    mesh.frustumCulled = false; // instances move; a cached bounding sphere would be wrong
    mesh.count = 0;
    scene.add(mesh);
    return mesh;
  }
  const box = (w, h, d, y = 0, z = 0) => new THREE.BoxGeometry(w, h, d).translate(0, y, z);
  const dark = new THREE.MeshLambertMaterial({ color: 0x151515 });

  const shells = instances(box(2, 0.8, 4, 0.75), most.cars + MAX_WRECKS);
  const cabins = instances(box(1.7, 0.65, 2, 1.45, -0.3), most.cars + MAX_WRECKS);
  // Same size and placing as the wheels on the player's car.
  const wheels = instances(
    new THREE.CylinderGeometry(WHEEL_RADIUS, WHEEL_RADIUS, 0.35, 12).rotateZ(Math.PI / 2),
    (most.cars + MAX_WRECKS) * 4, dark,
  );
  // A bar across each face, so that the spin can be seen.
  const spokes = instances(box(0.37, 0.55, 0.09), (most.cars + MAX_WRECKS) * 4,
    new THREE.MeshLambertMaterial({ color: 0x9e9e9e }));
  // Headlights, only drawn at night.
  const lamp = npcLamps();
  const headlamps = instances(lamp.front, most.cars + MAX_WRECKS, lamp.white);
  const taillamps = instances(lamp.tail, most.cars + MAX_WRECKS, lamp.red);
  const light = beam();
  const beams = instances(light.geometry, most.cars + MAX_WRECKS, light.material);
  // Flying cars. One that is hit drops out of the layer into the wrecks.
  const air = createAirTraffic(scene, (car) => wrecks.addFalling(car), overlaps);
  const crumpled = new Map(); // condition -> the dented body drawn for it
  const smoke = createSmoke(scene);
  const trails = createSparkTrails(scene);
  const rolling = new WeakMap(); // NPC car -> wheel state
  const turn = new THREE.Euler(0, 0, 0, 'YXZ'); // steer, then spin about the axle
  const local = new THREE.Quaternion();

  // Wheel and spoke, given the car's rotation (in `rotation`).
  function putWheel(index, x, y, z, spin, steer) {
    local.setFromEuler(turn.set(spin, steer, 0)).premultiply(rotation);
    put(wheels, index, x, y, z, local);
    put(spokes, index, x, y, z, local);
  }
  const people = most.peds + LIMITS.fallen;
  const bare = new THREE.MeshLambertMaterial(); // tinted with each person's skin
  const torsos = instances(box(TORSO.w, TORSO.h, TORSO.d), people);
  // Faces only up close: there can be thousands of people.
  // One mesh for each sort of person, near and far.
  const facing = headMaterial();
  const crowd = (detail) => Array.from({ length: VARIANTS },
    (_, v) => instances(headGeometry({ ...personOf(v), detail }), people, facing));
  const heads = crowd(true);
  const farHeads = crowd(false);
  const placed = { near: new Array(VARIANTS), far: new Array(VARIANTS) }; // this frame
  function putHead(focus, id, x, y, z, q) {
    const v = variantOf(id);
    if (Math.hypot(x - focus.x, z - focus.z) < FACES) put(heads[v], placed.near[v]++, x, y, z, q);
    else put(farHeads[v], placed.far[v]++, x, y, z, q);
  }
  const skinColour = new THREE.Color();
  const skinOf = (id) => skinColour.set(personOf(variantOf(id)).skin);
  // Limbs are modelled hanging from their joint, so they swing about it.
  const thighs = instances(box(LEG.w, THIGH, LEG.w, -THIGH / 2), people * 2, dark);
  const shins = instances(box(LEG.w * 0.9, THIGH, LEG.w * 0.9, -THIGH / 2), people * 2, dark);
  const arms = instances(box(ARM.w, ARM.h, ARM.w, -ARM.h / 2), people * 2);
  const forearms = instances(box(FOREARM.w, FOREARM.h, FOREARM.w, -FOREARM.h / 2), people * 2, bare);
  // The robots among them have parts of their own, in near and far versions.
  const metal = robotMaterial();
  const glow = new THREE.MeshBasicMaterial({ vertexColors: true }); // lit up at night too
  const machine = (detail) => {
    const shapes = robotGeometries({ detail });
    return {
      torsos: instances(shapes.torso, people, metal),
      heads: instances(shapes.head, people, metal),
      eyes: instances(shapes.eyes, people, glow),
      thighs: instances(shapes.thigh, people * 2, metal),
      shins: instances(shapes.shin, people * 2, metal),
      arms: instances(shapes.arm, people * 2, metal),
      forearms: instances(shapes.forearm, people * 2, metal),
    };
  };
  const robots = machine(true);
  const farRobots = machine(false);
  const humans = { torsos, thighs, shins, arms, forearms };
  const kinds = [humans, robots, farRobots];
  // Which set of parts someone at (x, z) is made from.
  const bodyFor = (focus, id, x, z) => {
    if (!isRobot(id)) return humans;
    return Math.hypot(x - focus.x, z - focus.z) < FACES ? robots : farRobots;
  };
  const elbow = new THREE.Quaternion();
  const drop = new THREE.Vector3();
  // Drawn the same size as the colliders.
  const solid = ({ half: [w, h, d], y }) => box(w * 2, h * 2, d * 2, y);
  const posts = instances(mergeGeometries([solid(PARTS.base), solid(PARTS.shaft)]), mostLights, dark);
  const lamps = instances(solid(PARTS.head), mostLights, new THREE.MeshBasicMaterial());

  const lamplight = createLamps(scene, poles, REACH.max);

  const matrix = new THREE.Matrix4();
  const position = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  const swing = new THREE.Quaternion();
  const unit = new THREE.Vector3(1, 1, 1);
  const up = new THREE.Vector3(0, 1, 0);
  const side = new THREE.Vector3(1, 0, 0);
  const colour = new THREE.Color();

  function put(mesh, index, x, y, z, q) {
    mesh.setMatrixAt(index, matrix.compose(position.set(x, y, z), q, unit));
  }
  function finish(mesh, count) {
    mesh.count = count;
    // Upload only the part in use.
    mesh.instanceMatrix.clearUpdateRanges();
    mesh.instanceMatrix.addUpdateRange(0, Math.max(1, count) * 16);
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }

  function syncBodies() {
    const alive = new Set();
    for (const car of sim.cars) {
      alive.add(car.id);
      const q = rotation.setFromAxisAngle(up, Math.atan2(car.hx, car.hz));
      let body = bodies.get(car.id);
      if (!body) {
        body = physics.createRigidBody(RAPIER.RigidBodyDesc.kinematicPositionBased()
          .setTranslation(car.x, 0.8, car.z).setRotation(q));
        physics.createCollider(RAPIER.ColliderDesc.cuboid(1, 0.4, 2), body);
        bodies.set(car.id, body);
      }
      body.setNextKinematicTranslation({ x: car.x, y: 0.8, z: car.z });
      body.setNextKinematicRotation(q);
    }
    for (const [id, body] of bodies) {
      if (alive.has(id)) continue;
      physics.removeRigidBody(body);
      bodies.delete(id);
    }
  }

  // Keep one ragdoll for each fallen person, and tell the simulation where it is.
  function syncRagdolls() {
    for (const entry of sim.fallen) {
      let doll = ragdolls.get(entry);
      if (!doll) {
        const { ped, vx, vz } = entry;
        const speed = Math.hypot(vx, vz);
        const scale = Math.min(1.1, 30 / (speed || 1));
        doll = createRagdoll(physics, { x: ped.x, z: ped.z, yaw: Math.atan2(ped.hx, ped.hz) },
          { x: vx * scale, y: Math.min(2 + speed * 0.15, 7), z: vz * scale });
        ragdolls.set(entry, doll);
      } else {
        const p = doll.torso.translation();
        entry.x = p.x;
        entry.z = p.z;
      }
    }
    const down = new Set(sim.fallen);
    for (const [entry, doll] of ragdolls) {
      if (down.has(entry)) continue;
      doll.dispose();
      ragdolls.delete(entry);
    }
  }

  const lit = () => sim.enabled.cars || sim.enabled.peds;

  function updatePoles(garage, focus, dt) {
    const bodies = [
      ...police.cars().map((car) => [car.body, FOOTPRINT.car]),
      ...[garage.current, ...garage.parked.values()].map((v) => [v.body, FOOTPRINT[v.name]]),
      ...wrecks.list.map(({ car }) => [car.body, FOOTPRINT.car]),
    ];
    const vehicles = bodies.filter(([, size]) => size).map(([body, size]) => {
      const footprint = wrecks.footprint(body, ...size);
      return { footprint, momentum: body.mass() * Math.hypot(footprint.vx, footprint.vz) };
    });
    // There are no traffic lights while the traffic is switched off.
    const positions = bodies.map(([body]) => body.translation());
    poles.update(positions, vehicles, focus, sim.radius, dt, lit());
  }

  // Anything fast that is about to hit the player on foot.
  function strikePlayer(garage, dt) {
    const walker = garage.current;
    if (walker.name !== 'on foot') return;
    const strikers = [
      ...sim.cars.map((car) => ({
        x: car.x, z: car.z, hx: car.hx, hz: car.hz, halfL: 2, halfW: 1,
        vx: car.hx * car.speed, vz: car.hz * car.speed,
      })),
      ...[...police.cars(), ...wrecks.list.map((w) => w.car)].map((car) => wrecks.footprint(car.body)),
      ...[...garage.parked.values()].filter((v) => FOOTPRINT[v.name])
        .map((v) => wrecks.footprint(v.body, ...FOOTPRINT[v.name])),
    ];
    damage += runDown(walker, strikers, overlaps, dt);
  }

  // Every car there is a physics body for.
  const solidCars = (garage) => [
    ...[garage.current, ...garage.parked.values()].filter((v) => v.condition),
    ...police.cars(),
    ...wrecks.list.map((entry) => entry.car),
  ];

  // Collisions and fires.
  function damageCars(garage, dt) {
    for (const car of solidCars(garage)) {
      watch(car);
      burn(car.condition, dt);
    }
    for (const [id, condition] of wrecks.scarred) {
      if (!sim.cars.some((c) => c.id === id)) wrecks.scarred.delete(id);
      else burn(condition, dt);
    }
    // A driver whose crashed car catches fire gets thrown clear.
    for (const { car, shade } of wrecks.list) {
      if (car.condition.hp > 0 || car.condition.empty) continue;
      car.condition.empty = true;
      const f = wrecks.footprint(car.body);
      sim.dropDriver({
        x: f.x - f.hz * 1.9, z: f.z + f.hx * 1.9, hx: f.hx, hz: f.hz,
        vx: -f.hz * 2, vz: f.hx * 2, shade: (shade + 0.37) % 1,
      });
      syncRagdolls();
    }
    driver = garage;
    const mine = garage.current.condition;
    if (mine && onFire(mine)) damage += BURN_HURT * dt;
  }
  let driver = null; // the garage, once known
  // Asked afresh each time: a car may have been removed since the last step, and
  // touching a removed body crashes the physics engine.
  const cars = () => (driver ? solidCars(driver) : []);

  // Which car a bullet struck, if any, and damage it there.
  function shootCar(from, dir, reach, collider, amount) {
    const handle = collider.parent()?.handle;
    let car = cars().find((c) => c.body.handle === handle);
    if (!car) {
      // One still on rails is knocked out of the traffic by it.
      const id = [...bodies].find(([, body]) => body.handle === handle)?.[0];
      const npc = sim.cars.find((c) => c.id === id);
      if (!npc) return;
      wrecks.wreck(npc, 1 / 60);
      car = wrecks.list.at(-1).car;
      syncBodies();
    }
    const turn = new THREE.Quaternion().copy(car.body.rotation()).invert();
    const p = car.body.translation();
    const point = new THREE.Vector3(from.x + dir.x * reach - p.x, from.y + dir.y * reach - p.y,
      from.z + dir.z * reach - p.z).applyQuaternion(turn);
    shot(car.condition, point, new THREE.Vector3(dir.x, dir.y, dir.z).applyQuaternion(turn), amount);
  }

  const used = new Set(); // conditions drawn this frame
  function dented(condition, shade, position, quaternion) {
    let body = crumpled.get(condition);
    if (!body) {
      body = createCarBody({
        body: new THREE.Color().setHSL(shade, 0.55, 0.5).getHex(),
        cabin: new THREE.Color().setHSL(shade, 0.45, 0.35).getHex(),
      });
      crumpled.set(condition, body);
      scene.add(body.group);
    }
    body.update(condition);
    body.group.position.copy(position);
    body.group.quaternion.copy(quaternion);
    used.add(condition);
  }

  // Where smoke and flames come from: the engine of each badly damaged car.
  function fumes() {
    const hood = new THREE.Vector3();
    const from = (condition, position, quaternion) => {
      if (condition.hp >= SMOKING) return [];
      hood.set(0, 0.5, 1.4).applyQuaternion(quaternion).add(position);
      return [{
        x: hood.x, y: hood.y, z: hood.z,
        smoke: condition.burnt ? 2 : onFire(condition) ? 14 : 5,
        flame: onFire(condition) ? 40 : 0,
      }];
    };
    return [
      ...cars().flatMap((car) => from(car.condition, car.body.translation(), car.body.rotation())),
      ...[...wrecks.scarred].flatMap(([id, condition]) => {
        const car = sim.cars.find((c) => c.id === id);
        return car ? from(condition, new THREE.Vector3(car.x, 0.75, car.z),
          new THREE.Quaternion().setFromAxisAngle(up, Math.atan2(car.hx, car.hz))) : [];
      }),
    ];
  }

  // How far along a ray the first thing is, without harming it.
  function sight(from, dir, reach, ignore) {
    const solid = physics.castRay(new RAPIER.Ray(from, dir), reach, true,
      undefined, undefined, undefined, ignore);
    const upTo = solid ? solid.timeOfImpact : reach;
    const standing = sim.raycast(from, dir, upTo)?.distance ?? upTo;
    const near = police.sightGunner(from, dir, standing) ?? standing;
    return air.raycast(from, dir, near)?.distance ?? near;
  }

  // The nearest person standing within arm's reach of `from`, within `arc` of
  // the heading `yaw`: { x, z, distance, hit(damage) -> their HP } or null.
  function inReach(from, yaw, reach, arc) {
    const measure = (o) => {
      const distance = Math.hypot(o.x - from.x, o.z - from.z);
      const turn = Math.atan2(o.x - from.x, o.z - from.z) - yaw;
      return distance <= reach && Math.abs(Math.atan2(Math.sin(turn), Math.cos(turn))) <= arc ? distance : null;
    };
    // With the suit on, one blow sends anyone flying.
    const push = (o) => {
      const d = Math.hypot(o.x - from.x, o.z - from.z) || 1;
      const speed = suit.on ? SUIT.punch : 3;
      return [((o.x - from.x) / d) * speed, ((o.z - from.z) / d) * speed];
    };
    const blow = (amount) => (suit.on ? Infinity : amount);
    const found = [];
    for (const ped of sim.peds) {
      const distance = measure(ped);
      if (distance === null) continue;
      found.push({
        x: ped.x, z: ped.z, distance,
        hit(amount) {
          const [vx, vz] = push(ped);
          const hp = sim.damage(ped, Math.min(blow(amount), 1e6), { vx, vz });
          sim.alarm(from.x, from.z, 12);
          wanted.report(hp ? 'punch' : 'kill');
          syncRagdolls();
          return hp;
        },
      });
    }
    for (const officer of police.officers()) {
      const distance = officer.down ? null : measure(officer);
      if (distance === null) continue;
      found.push({
        x: officer.x, z: officer.z, distance,
        hit(amount) {
          const hp = officer.hurt(blow(amount), ...push(officer));
          wanted.report('hitOfficer');
          return hp;
        },
      });
    }
    if (suit.on) found.push(...vehiclesInReach(measure, from));
    return found.sort((a, b) => a.distance - b.distance)[0] ?? null;
  }

  // With the suit on, vehicles can be punched too. measure() gives the distance
  // to a point if it is within reach and in front.
  function vehiclesInReach(measure, from) {
    const found = [];
    const consider = (footprint, send) => {
      const side = nearestOn(footprint, from.x, from.z);
      const distance = measure(side);
      if (distance !== null) found.push({ x: side.x, z: side.z, distance, hit: () => send(side) });
    };
    // Away from the puncher, up, and turning end over end.
    const fling = (body, side) => {
      const p = body.translation();
      const d = Math.hypot(p.x - from.x, p.z - from.z) || 1;
      const [x, z] = [(p.x - from.x) / d, (p.z - from.z) / d];
      const { away, up: rise, tumble } = SUIT.fling;
      body.setLinvel({ x: x * away, y: rise, z: z * away }, true);
      // Off-centre blows spin it as well.
      const off = Math.sign((side.x - p.x) * z - (side.z - p.z) * x) || 1;
      body.setAngvel({ x: z * tumble, y: off * tumble * 0.7, z: -x * tumble }, true);
    };
    // A car notices the blow as any other collision, and is dented by it.
    const strike = (car, side, crime) => {
      const v = car.body.linvel();
      if (car.condition) car.lastVelocity = { x: v.x, z: v.z };
      fling(car.body, side);
      if (crime) wanted.report(crime);
      return undefined; // nobody's HP to report
    };
    const parked = driver ? [...driver.parked.values()] : [];
    for (const car of parked) consider(wrecks.footprint(car.body, ...FOOTPRINT[car.name]), (side) => strike(car, side));
    for (const car of police.cars()) consider(wrecks.footprint(car.body), (side) => strike(car, side, 'ram'));
    for (const { car } of wrecks.list) consider(wrecks.footprint(car.body), (side) => strike(car, side));
    for (const npc of sim.cars) {
      consider({ x: npc.x, z: npc.z, hx: npc.hx, hz: npc.hz, halfL: 2, halfW: 1 }, (side) => {
        // Off its rails first, so that there is something to throw.
        wrecks.wreck(npc, 1 / 60);
        syncBodies();
        return strike(wrecks.list.at(-1).car, side, 'punch');
      });
    }
    return found;
  }

  // A shot along a ray that is stopped at `reach` by `collider` (if any).
  function shoot(from, dir, reach, collider, amount) {
    wanted.report('gunfire');
    sim.alarm(from.x, from.z, 50);
    // An officer leaning out of a car is hit before the car is.
    const gunner = police.shootGunner(from, dir, reach, amount);
    if (gunner) {
      wanted.report('shootPolice');
      return gunner;
    }
    // A flying car in the way is brought down.
    const flier = air.raycast(from, dir, reach);
    if (flier) {
      const { car } = air.drop(flier.car);
      const turn = new THREE.Quaternion().copy(car.body.rotation()).invert();
      const at = new THREE.Vector3(from.x + dir.x * flier.distance - flier.car.x,
        from.y + dir.y * flier.distance - flier.car.y, from.z + dir.z * flier.distance - flier.car.z);
      shot(car.condition, at.applyQuaternion(turn), new THREE.Vector3(dir.x, dir.y, dir.z).applyQuaternion(turn), amount);
      wanted.report('punch');
      return { hp: undefined, distance: flier.distance };
    }
    const standing = sim.raycast(from, dir, reach);
    if (standing) {
      const hp = sim.damage(standing.ped, amount, { head: standing.head, vx: dir.x * 2, vz: dir.z * 2 });
      wanted.report(hp ? 'hurt' : 'kill');
      syncRagdolls();
      return { hp, distance: standing.distance };
    }
    if (!collider) return null;
    if (police.owns(collider)) wanted.report('shootPolice');
    shootCar(from, dir, reach, collider, amount);
    const officer = police.shoot(collider, from.y + dir.y * reach, amount, dir.x * 2, dir.z * 2);
    if (officer) {
      wanted.report('shootPolice');
      return { hp: officer.hp, distance: reach };
    }
    // Or someone already on the ground: their ragdoll stopped the shot.
    for (const [entry, doll] of ragdolls) {
      const bodies = [doll.torso, ...doll.arms, ...doll.forearms, ...doll.legs, ...doll.shins];
      const struck = bodies.find((body) => body.handle === collider.parent()?.handle);
      if (!struck) continue;
      struck.applyImpulse({ x: dir.x * 8, y: dir.y * 8, z: dir.z * 8 }, true);
      return { hp: sim.damage(entry.ped, amount), distance: reach };
    }
    return null;
  }

  return {
    sim,
    shoot,
    sight,
    inReach,
    suit,
    squad,
    ragdolls,
    wrecks: wrecks.list,
    poles,
    lamps: lamplight,
    bodies: { humans, robots, farRobots }, // the instanced parts of everyone on foot
    wanted,
    police,
    // Every car the player could take: { footprint, take() }. take() removes it
    // from the world, leaves its driver on the road, and returns what is needed to
    // put the player in it: { spot, colours, police }.
    carsToTake() {
      const paint = (shade) => ({
        body: new THREE.Color().setHSL(shade, 0.55, 0.5).getHex(),
        cabin: new THREE.Color().setHSL(shade, 0.45, 0.35).getHex(),
      });
      const spotFor = (f) => ({ x: f.x, y: 0.8, z: f.z, yaw: Math.atan2(f.hx, f.hz) });
      // The driver comes out of the right-hand door, as traffic keeps left.
      const eject = (f, shade) => {
        const out = { x: -f.hz, z: f.hx };
        sim.dropDriver({
          x: f.x + out.x * 1.7, z: f.z + out.z * 1.7, hx: f.hx, hz: f.hz,
          vx: out.x * 2.5, vz: out.z * 2.5, shade: (shade + 0.37) % 1,
        });
        syncRagdolls();
      };
      const civilian = (footprint, shade, remove, condition) => ({
        footprint,
        take() {
          remove();
          eject(footprint, shade);
          wanted.report('jack');
          return { spot: spotFor(footprint), colours: paint(shade), police: false, condition };
        },
      });
      return [
        ...sim.cars.map((car) => civilian(
          { x: car.x, z: car.z, hx: car.hx, hz: car.hz, halfL: 2, halfW: 1, vx: car.hx * car.speed, vz: car.hz * car.speed },
          car.shade,
          () => {
            wrecks.scarred.delete(car.id);
            sim.takeCar(car);
            syncBodies();
          },
          wrecks.scarred.get(car.id),
        )),
        ...wrecks.list.filter((entry) => entry.car.condition.hp > 0).map((entry) => civilian(
          wrecks.footprint(entry.car.body), entry.shade, () => wrecks.remove(entry), entry.car.condition)),
        ...police.units.map((unit) => {
          const footprint = wrecks.footprint(unit.car.body);
          return {
            footprint,
            take() {
              const { condition } = unit.car;
              police.hijack(unit, footprint);
              wanted.report('jackPolice');
              return { spot: spotFor(footprint), colours: LIVERY, police: true, condition };
            },
          };
        }),
      ];
    },
    // How much the player has been hurt since last asked.
    takeDamage() {
      const was = damage;
      damage = 0;
      return was;
    },
    // The player is gone: call off the search. The police then leave in their
    // own time, as they do when they lose someone.
    standDown() {
      wanted.clear();
      busted = false;
      damage = 0;
      squad.revive();
    },
    // True once, after the player has been arrested.
    takeBusted() {
      const was = busted;
      busted = false;
      return was;
    },
    // Call once per physics step, before physics.step().
    update(garage, dt) {
      // Aircraft overhead are not in anyone's way.
      const obstacles = [garage.current, ...garage.parked.values()]
        .filter((v) => v.body.translation().y < 4)
        .map((v) => {
          const p = v.body.translation();
          const s = v.body.linvel();
          const obstacle = { x: p.x, z: p.z, r: RADIUS[v.name], speed: Math.hypot(s.x, s.z) };
          const size = FOOTPRINT[v.name];
          if (!size) return obstacle; // on foot
          const q = v.body.rotation();
          const hx = 2 * (q.x * q.z + q.w * q.y);
          const hz = 1 - 2 * (q.x * q.x + q.y * q.y);
          const flat = Math.hypot(hx, hz) || 1;
          return Object.assign(obstacle, {
            vehicle: true, hx: hx / flat, hz: hz / flat, halfL: size[0], halfW: size[1], vx: s.x, vz: s.z,
          });
        });
      // People walk round all of a player who is down, not just their middle.
      for (const part of sprawl(garage.current) ?? []) obstacles.push({ ...part, r: 0.15, speed: 0 });
      // Cars stop for officers and friends on foot, and people walk round them.
      for (const o of [...police.officers(), ...squad.afoot()]) if (!o.down) obstacles.push({ x: o.x, z: o.z, r: 0.4, speed: 0 });
      // Police and crashed cars are in the way too, and can knock people over.
      // Not ones overhead.
      for (const car of [...police.cars(), ...wrecks.list.map((w) => w.car)].filter((c) => c.body.translation().y < 4)) {
        const f = wrecks.footprint(car.body);
        obstacles.push({ ...f, r: RADIUS.car, speed: Math.hypot(f.vx, f.vz), vehicle: true });
      }
      const p = garage.current.body.translation();
      sim.step(dt, { x: p.x, z: p.z }, obstacles, poles.segments());
      syncBodies();
      // Anyone newly down beside the player's moving vehicle was run over by it.
      const velocity = garage.current.body.linvel();
      if (FOOTPRINT[garage.current.name] && Math.hypot(velocity.x, velocity.z) > 1.5 && p.y < 4) {
        for (const entry of sim.fallen)
          if (!ragdolls.has(entry) && Math.hypot(entry.x - p.x, entry.z - p.z) < 5) wanted.report('runover');
      }
      strikePlayer(garage, dt);
      patrol(garage, dt);
      companions(garage, dt);
      const up = [garage.current.body, ...police.cars().map((car) => car.body)]
        .map((body) => wrecks.footprint(body));
      air.update(sim.time, p, sim.radius, up, dt, sim.enabled.cars);
      damageCars(garage, dt);
      const strikers = [garage.current, ...garage.parked.values()]
        .filter((v) => FOOTPRINT[v.name])
        .map((v) => wrecks.footprint(v.body, ...FOOTPRINT[v.name]))
        .concat(police.cars().map((car) => wrecks.footprint(car.body)));
      // A recovering driver also gives way to people.
      const people = [...sim.peds.filter((ped) => ped.onRoad), ...sim.fallen]
        .map((o) => ({ x: o.x, z: o.z, r: 0.5 }));
      if (garage.current.name === 'on foot') people.push({ x: p.x, z: p.z, r: 0.5, own: true });
      for (const o of police.officers()) people.push({ x: o.x, z: o.z, r: 0.5 });
      // A recovering driver also steers clear of standing lights.
      for (const pole of poles.standing.values()) people.push({ x: pole.x, z: pole.z, r: 0.3 });
      wrecks.update(strikers, p, dt, people);
      syncBodies(); // drop the rails bodies of cars that just became wrecks
      syncRagdolls();
      updatePoles(garage, p, dt);
    },
    clearNear(spot) {
      sim.clearNear(spot);
      wrecks.clearNear(spot);
      police.clearNear(spot);
      squad.regroup();
      syncBodies();
      syncRagdolls();
    },
    set(kind, on) {
      sim.set(kind, on);
      if (kind === 'cars' && !on) wrecks.clear();
      syncBodies();
      syncRagdolls();
    },
    isOn: (kind) => sim.enabled[kind],
    radius: () => sim.radius,
    setRadius(metres) {
      sim.setRadius(metres);
      syncBodies();
      syncRagdolls();
    },
    smoke,
    trails,
    crumpled,
    air,
    // Call once per rendered frame.
    draw(focus, night = false) {
      const now = performance.now();
      const frame = drawnAt === null ? 0 : Math.min(0.1, (now - drawnAt) / 1000);
      police.draw(frame, night);
      squad.draw();
      drawnAt = now;
      used.clear();
      air.draw();
      // Sparks under everything that is flying, the player's and police included.
      trails.draw(now / 1000, [
        ...cars().filter((car) => car.flying).flatMap((car) => {
          const { x: vx, z: vz } = car.body.linvel();
          return car.hubs().map((at) => ({ x: at.x, y: at.y, z: at.z, vx, vz }));
        }),
        ...air.wheels(focus),
      ]);
      sim.cars.forEach((car, n) => {
        rotation.setFromAxisAngle(up, Math.atan2(car.hx, car.hz));
        put(shells, n, car.x, 0, car.z, rotation);
        put(cabins, n, car.x, 0, car.z, rotation);
        if (night) for (const mesh of [headlamps, taillamps, beams]) put(mesh, n, car.x, 0, car.z, rotation);
        const damaged = wrecks.scarred.get(car.id);
        if (damaged) {
          // Its own body takes the place of the plain one.
          for (const mesh of [shells, cabins]) put(mesh, n, car.x, -100, car.z, rotation);
          dented(damaged, car.shade, new THREE.Vector3(car.x, 0.75, car.z), rotation);
        }
        shells.setColorAt(n, colour.setHSL(car.shade, 0.55, 0.5));
        cabins.setColorAt(n, colour.setHSL(car.shade, 0.45, 0.35));
        if (!rolling.has(car)) rolling.set(car, {});
        const { spin, steer } = rollWheels(rolling.get(car), car.x, car.z, Math.atan2(car.hx, car.hz));
        WHEELS.forEach(([across, along], k) => putWheel(n * 4 + k,
          car.x + car.hz * across + car.hx * along, WHEEL_RADIUS,
          car.z - car.hx * across + car.hz * along, spin, along > 0 ? steer : 0));
      });
      let total = sim.cars.length;
      for (const entry of wrecks.list) {
        const { car, shade } = entry;
        const p = car.body.translation();
        rotation.copy(car.body.rotation());
        // The instanced parts are modelled from the ground; the body's origin is 0.75 up.
        const at = (along) => new THREE.Vector3(0, -0.75, along).applyQuaternion(rotation).add(p);
        const base = at(0);
        for (const mesh of [shells, cabins]) put(mesh, total, base.x, -100, base.z, rotation);
        dented(entry.car.condition, shade, p, rotation);
        if (night) {
          put(headlamps, total, base.x, base.y, base.z, rotation);
          put(taillamps, total, base.x, base.y, base.z, rotation);
          // The patch of light only makes sense under a car that is the right way up.
          const level = 1 - 2 * (rotation.x ** 2 + rotation.z ** 2) > 0.95 && base.y < 0.4;
          put(beams, total, base.x, level ? 0 : -50, base.z, rotation);
        }
        shells.setColorAt(total, colour.setHSL(shade, 0.55, 0.5));
        cabins.setColorAt(total, colour.setHSL(shade, 0.45, 0.35));
        // A crashed car is a physics car, so its wheels come from the simulation.
        WHEELS.forEach(([across, along], k) => {
          // WHEELS and the physics car list left and right in the same order.
          const { y, spin, steer } = car.wheel(k);
          const hub = new THREE.Vector3(across, y, along).applyQuaternion(rotation).add(p);
          putWheel(total * 4 + k, hub.x, hub.y, hub.z, spin, steer);
        });
        total++;
      }
      finish(shells, total);
      for (const [condition, body] of crumpled) {
        if (used.has(condition)) continue;
        body.dispose();
        crumpled.delete(condition);
      }
      smoke.update(frame, fumes());
      for (const mesh of [headlamps, taillamps, beams]) finish(mesh, night ? total : 0);
      finish(cabins, total);
      finish(wheels, total * 4);
      finish(spokes, total * 4);

      placed.near.fill(0);
      placed.far.fill(0);
      for (const kind of kinds) kind.count = 0; // placed so far this frame
      sim.peds.forEach((ped) => {
        const body = bodyFor(focus, ped.id, ped.x, ped.z);
        const robot = body !== humans;
        const n = body.count++;
        rotation.setFromAxisAngle(up, Math.atan2(ped.hx, ped.hz));
        put(body.torsos, n, ped.x, TORSO.y, ped.z, rotation);
        if (robot) for (const mesh of [body.heads, body.eyes]) put(mesh, n, ped.x, HEAD.y, ped.z, rotation);
        else {
          putHead(focus, ped.id, ped.x, HEAD.y, ped.z, rotation);
          skinOf(ped.id);
          colour.setHSL(ped.shade, 0.6, 0.45);
          torsos.setColorAt(n, colour);
          for (const k of [0, 1]) {
            forearms.setColorAt(n * 2 + k, skinColour);
            arms.setColorAt(n * 2 + k, colour);
          }
        }
        const angle = ped.walking ? Math.sin(ped.stride) * 0.5 : 0;
        for (const [k, sign] of [[0, -1], [1, 1]]) {
          // The thigh from the hip, then the shin from wherever the knee is.
          swing.setFromAxisAngle(side, angle * sign).premultiply(rotation);
          const hip = sign * LEG.x;
          put(body.thighs, n * 2 + k, ped.x + ped.hz * hip, LEG.y, ped.z - ped.hx * hip, swing);
          drop.set(0, -THIGH, 0).applyQuaternion(swing);
          swing.multiply(elbow.setFromAxisAngle(side, kneeBend(ped.stride, sign, ped.walking ? 1 : 0)));
          put(body.shins, n * 2 + k, ped.x + ped.hz * hip + drop.x, LEG.y + drop.y,
            ped.z - ped.hx * hip + drop.z, swing);
          // Arms swing opposite to the leg on the same side.
          swing.setFromAxisAngle(side, -angle * sign).premultiply(rotation);
          const shoulder = sign * ARM.x;
          put(body.arms, n * 2 + k, ped.x + ped.hz * shoulder, ARM.y, ped.z - ped.hx * shoulder, swing);
          // `swing` still holds the upper arm's rotation.
          drop.set(0, -ARM.h, 0).applyQuaternion(swing);
          const across = sign * ARM.x;
          swing.multiply(elbow.setFromAxisAngle(side, elbowBend(angle * -sign)));
          put(body.forearms, n * 2 + k, ped.x + ped.hz * across + drop.x, ARM.y + drop.y,
            ped.z - ped.hx * across + drop.z, swing);
        }
      });

      for (const [entry, doll] of ragdolls) {
        const lies = doll.torso.translation();
        const body = bodyFor(focus, entry.ped.id, lies.x, lies.z);
        const robot = body !== humans;
        const n = body.count++;
        const copy = (mesh, index, from) => {
          const p = from.translation();
          put(mesh, index, p.x, p.y, p.z, swing.copy(from.rotation()));
        };
        copy(body.torsos, n, doll.torso);
        const at = doll.head.translation();
        swing.copy(doll.head.rotation());
        if (robot) for (const mesh of [body.heads, body.eyes]) put(mesh, n, at.x, at.y, at.z, swing);
        else {
          putHead(focus, entry.ped.id, at.x, at.y, at.z, swing);
          skinOf(entry.ped.id);
          colour.setHSL(entry.ped.shade, 0.6, 0.45);
          torsos.setColorAt(n, colour);
          for (const k of [0, 1]) {
            forearms.setColorAt(n * 2 + k, skinColour);
            arms.setColorAt(n * 2 + k, colour);
          }
        }
        for (const k of [0, 1]) {
          copy(body.thighs, n * 2 + k, doll.legs[k]);
          copy(body.shins, n * 2 + k, doll.shins[k]);
          copy(body.arms, n * 2 + k, doll.arms[k]);
          copy(body.forearms, n * 2 + k, doll.forearms[k]);
        }
      }
      for (const body of kinds) {
        const { count } = body;
        finish(body.torsos, count);
        finish(body.thighs, count * 2);
        finish(body.shins, count * 2);
        finish(body.arms, count * 2);
        finish(body.forearms, count * 2);
        if (body === humans) continue;
        finish(body.heads, count);
        finish(body.eyes, count);
      }
      heads.forEach((mesh, v) => finish(mesh, placed.near[v]));
      farHeads.forEach((mesh, v) => finish(mesh, placed.far[v]));

      // Lights at every junction within the traffic radius.
      let n = 0;
      if (lit()) {
        const ci = Math.round(focus.x / PITCH);
        const cj = Math.round(focus.z / PITCH);
        rotation.identity();
        const span = Math.ceil(sim.radius / PITCH);
        const edge = BLOCKS / 2 - 1; // outermost junction traffic uses
        for (let i = Math.max(-edge, ci - span); i <= Math.min(edge, ci + span); i++)
          for (let j = Math.max(-edge, cj - span); j <= Math.min(edge, cj + span); j++) {
            if (Math.hypot(i * PITCH - focus.x, j * PITCH - focus.z) > sim.radius) continue;
            APPROACHES.forEach((approach, k) => {
              const fallen = poles.down.get(poleKey(i, j, k));
              if (fallen) {
                const { x, y, z } = fallen.position;
                swing.copy(fallen.rotation);
                put(posts, n, x, y, z, swing);
                put(lamps, n, x, y, z, swing);
                lamps.setColorAt(n, colour.set(DARK));
              } else {
                const { x, z } = polePlace(i, j, approach);
                put(posts, n, x, 0, z, rotation);
                put(lamps, n, x, 0, z, rotation);
                lamps.setColorAt(n, colour.set(LIGHT[signal(i, j, approach[0] ? 0 : 1, sim.time).colour]));
              }
              n++;
            });
          }
      }
      finish(posts, n);
      finish(lamps, n);
      lamplight.draw(focus, sim.radius, night);
    },
  };
}
