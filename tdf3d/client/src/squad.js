// Three friends who follow the player about, ride along, and shoot back at
// anyone who attacks. Bullets and vehicles can hurt them. In vapour suits they
// are immune to bullets, run faster, never reload and punch people flying.
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { rng } from './city.js';
import { wallsBetween } from './walls.js';
import { PLAYER_HP, PUNCH } from './weapons.js';
import { SUIT } from './suit.js';
import { runDown } from './knockdown.js';
import { headGeometry } from './head.js';
import { createOfficer, separate, sideDoor, pushOut } from './officers.js';
import { createGunner } from './gunner.js';
import { createNameTag } from './name-tag.js';

// Seats as in a British car: you drive from the front right, so nobody shoots
// from there. side is 1 for the left of the car and -1 for the right; seat is
// how far forward of its middle.
const FRONT = 0.35;
const BACK = -0.85;
const ROUND_THE_CORNER = 0.6; // metres past their own side's middle line they can still aim

export const MEMBERS = [
  {
    name: 'Saif Keyes',
    person: { skin: 0xb27c55, hair: 0x14100c, style: 'short', nose: 1.1, jaw: 0.85, width: 1.02 },
    top: 0x14574f,
    place: [-1.5, -1.6], // across (left is positive) and behind
    side: 1,             // front left, beside you
    seat: FRONT,
  },
  {
    name: 'Fleur Gardner',
    person: { skin: 0xeac19c, hair: 0x3a2114, style: 'long', width: 0.94, jaw: 1.2, nose: 0.9, eyes: 0x4b6a43 },
    top: 0x7a1f35,
    place: [1.5, -1.6],
    side: -1,            // back right, behind you
    seat: BACK,
  },
  {
    name: 'Jade Fields',
    person: { skin: 0xd2a077, hair: 0x120b08, style: 'bun', width: 0.95, jaw: 1.12, nose: 0.95 },
    top: 0xc98a12,
    place: [0, -2.8],
    side: 1,             // back left
    seat: BACK,
  },
];
export const SQUAD_GUN = { name: 'BRN-180', damage: 12, interval: 0.12, accuracy: 0.5, range: 60, magazine: 30, reload: 2.2 };
const RUN = 9;        // m/s without suits: a little quicker than you, to catch up
const SUIT_RUN = 24;
const CHARGE = 12;    // metres: in suits, they go for anyone this close
const FORGET = 150;   // metres: a body further off than this is cleared away
const HEAL = 5;       // HP a second while nobody is after the player
const RIDES = ['car', 'helicopter', 'spaceplane']; // what they can get into
const BOARDING = 2;   // seconds before they are aboard whether or not they got there
const LOST = 45;      // metres behind: they catch up at once
const TRACE = 0.05;

// A faint glow in their own colour wherever something stands in front of them.
// Drawn after the world but before the figure itself, so only what is hidden shows.
function showThroughWalls(figure, colour) {
  const glow = new THREE.MeshBasicMaterial({
    color: new THREE.Color(colour).multiplyScalar(1.6), blending: THREE.AdditiveBlending,
    depthFunc: THREE.GreaterDepth, depthWrite: false, fog: false,
  });
  const meshes = [];
  figure.traverse((o) => { if (o.isMesh) meshes.push(o); });
  for (const mesh of meshes) {
    const ghost = new THREE.Mesh(mesh.geometry, glow);
    ghost.name = 'through walls';
    ghost.renderOrder = 1;
    mesh.renderOrder = 2;
    mesh.add(ghost);
  }
}

export function createSquad(scene, physics, seed = 1) {
  const random = rng(seed ^ 0x5ca1);
  const members = MEMBERS.map((who) => ({
    ...who, actor: null, aboard: false, boarding: 0, cooldown: 0, gunner: null, car: null,
    hp: PLAYER_HP, rounds: SQUAD_GUN.magazine, reloading: 0, punch: 0,
    tag: createNameTag(who.name.split(' ')[0]),
  }));
  for (const member of members) scene.add(member.tag);
  let suits = false;
  const tracers = [];
  const streak = new THREE.LineBasicMaterial({ color: 0xbfe8ff });
  let enabled = false;
  let lost = true; // put them beside the player on the next step

  function stow(member) {
    member.gunner?.remove();
    member.gunner = null;
    member.car = null;
  }

  function make(member, x, z) {
    member.actor = createOfficer(scene, physics, { x, z }, {
      look: { face: headGeometry(member.person), skin: member.person.skin, top: member.top, bottom: 0x15161a, cap: false },
      solid: false,
    });
    showThroughWalls(member.actor.figure, member.top);
  }

  // Where someone stands relative to a player facing `yaw`.
  const clearOfWalls = (spot) => {
    pushOut(spot, wallsBetween(spot, spot, 2));
    return spot;
  };
  const placeBy = (p, yaw, [across, behind]) => ({
    x: p.x + Math.cos(yaw) * across + Math.sin(yaw) * behind,
    z: p.z - Math.sin(yaw) * across + Math.cos(yaw) * behind,
  });

  // Can a bullet get there? Walls and cars stop it; the player's own does not.
  function inSight(from, to, ignore) {
    const dir = new THREE.Vector3(to.x - from.x, to.y - from.y, to.z - from.z);
    const length = dir.length();
    return !physics.castRay(new RAPIER.Ray(from, dir.normalize()), length - 0.3, true,
      RAPIER.QueryFilterFlags.EXCLUDE_SENSORS, undefined, undefined, ignore);
  }

  function fire(member, from, target, ignore, result, dt) {
    member.cooldown -= dt;
    if (member.cooldown > 0) return;
    member.cooldown += SQUAD_GUN.interval * (0.9 + random() * 0.2);
    const distance = Math.hypot(target.x - from.x, target.y - from.y, target.z - from.z);
    const hit = random() < SQUAD_GUN.accuracy * (1 - 0.5 * (distance / SQUAD_GUN.range));
    const wide = hit ? 0 : 0.8 + random();
    const dir = new THREE.Vector3(target.x - from.x, target.y - from.y, target.z - from.z).normalize();
    const end = new THREE.Vector3(target.x + dir.z * wide, target.y, target.z - dir.x * wide);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(from.x, from.y, from.z), end]), streak);
    line.frustumCulled = false;
    scene.add(line);
    tracers.push({ line, left: TRACE });
    result.shots.push({ x: from.x, z: from.z, name: member.name });
    if (hit && target.hurt(SQUAD_GUN.damage, dir) === 0) {
      result.kills++;
      target.dead = true;
    }
  }

  // Fire, unless the magazine is empty. In suits it never is.
  function shoot(member, from, target, ignore, result, dt) {
    if (member.reloading > 0) return;
    const before = result.shots.length;
    fire(member, from, target, ignore, result, dt);
    if (suits || result.shots.length === before) return;
    if (--member.rounds <= 0) member.reloading = SQUAD_GUN.reload;
  }

  // Someone in the squad is hit. Returns their HP.
  function wound(member, amount, vx = 0, vz = 0) {
    if (member.hp <= 0) return 0;
    member.hp = Math.max(0, member.hp - amount);
    member.actor.hp = member.hp;
    if (!member.hp) member.actor.knock(vx, vz);
    return member.hp;
  }

  // Hit while leaning out: killed, they tumble out of the window.
  function shotAt(member, amount, player) {
    if (suits || member.hp <= 0) return member.hp;
    member.hp = Math.max(0, member.hp - amount);
    member.actor.hp = member.hp;
    if (member.hp) return member.hp;
    const f = player.footprint;
    // Their own side of the car. The footprint's left is (hz, -hx).
    const door = { x: f.x + f.hz * member.side * (f.halfW + 0.6), z: f.z - f.hx * member.side * (f.halfW + 0.6) };
    stow(member);
    member.aboard = false;
    Object.assign(member.actor, { x: door.x, z: door.z, altitude: Math.max(0, player.y - 0.9) });
    member.actor.knock(player.vx, player.vz);
    member.actor.altitude = 0;
    member.actor.figure.visible = true;
    return 0;
  }

  // The nearest living attacker that can be seen from `from`.
  function pick(from, attackers, ignore) {
    return attackers
      .filter((a) => !a.dead)
      .map((a) => [Math.hypot(a.x - from.x, a.y - from.y, a.z - from.z), a])
      .filter(([d, a]) => d <= SQUAD_GUN.range && inSight(from, a, ignore))
      .sort((a, b) => a[0] - b[0])[0]?.[1] ?? null;
  }

  function remove(member) {
    stow(member);
    member.tag.visible = false;
    member.actor?.dispose();
    member.actor = null;
    member.aboard = false;
  }
  function restore(member) {
    remove(member);
    Object.assign(member, { hp: PLAYER_HP, rounds: SQUAD_GUN.magazine, reloading: 0 });
  }
  function dismiss() {
    for (const member of members) restore(member);
    lost = true;
  }

  // Where the police can shoot at them: { x, y, z, vx, vz, hurt(amount) }.
  let exposed = [];

  return {
    members,
    get enabled() { return enabled; },
    set enabled(value) {
      enabled = value;
      if (!value) dismiss();
    },
    // Come back to the player, wherever that now is.
    regroup() { lost = true; },
    // All of them, well again: for when the player starts over.
    revive() { dismiss(); },
    get suits() { return suits; },
    set suits(value) {
      suits = value;
      if (value) for (const member of members) Object.assign(member, { rounds: SQUAD_GUN.magazine, reloading: 0 });
    },
    targets: () => exposed,
    // Those on their feet, for others to walk and drive round.
    afoot: () => members.filter((m) => m.actor && !m.aboard && !m.actor.down).map((m) => m.actor),
    // player: { vehicle, footprint (or null on foot), x, y, z, yaw, vx, vz }.
    // attackers: as from police.attackers(). boxes: vehicle footprints.
    // threats: { strikers: moving vehicle footprints, overlaps, burning, calm }.
    // Returns { kills, shots }. Call before physics.step().
    update(player, attackers, boxes, dt, threats = {}) {
      const result = { kills: 0, shots: [] };
      for (let n = tracers.length - 1; n >= 0; n--) {
        tracers[n].left -= dt;
        if (tracers[n].left > 0) continue;
        scene.remove(tracers[n].line);
        tracers[n].line.geometry.dispose();
        tracers.splice(n, 1);
      }
      if (!enabled) return result;

      const { vehicle } = player;
      const ride = RIDES.includes(vehicle.name) && !vehicle.condition?.burnt;
      const speed = Math.hypot(player.vx, player.vz);
      exposed = [];
      for (const member of members) {
        if (!member.actor) {
          if (member.hp <= 0) continue; // left behind, until the player starts over
          make(member, player.x, player.z);
          member.actor.hp = member.hp;
        }
        const { actor } = member;
        member.punch = Math.max(0, member.punch - dt);
        if (member.reloading > 0) {
          member.reloading -= dt;
          if (member.reloading <= 0) member.rounds = SQUAD_GUN.magazine;
        }
        if (threats.calm && member.hp > 0) member.hp = Math.min(PLAYER_HP, member.hp + HEAL * dt);
        if (member.hp > 0) actor.hp = member.hp;
        // Down: dead for good, or getting up again shortly.
        actor.step(dt);
        if (actor.down) {
          stow(member);
          member.aboard = false;
          actor.figure.visible = true;
          if (member.hp <= 0 && Math.hypot(actor.x - player.x, actor.z - player.z) > FORGET) remove(member);
          continue;
        }
        if (lost) {
          stow(member);
          member.aboard = false;
          Object.assign(actor, clearOfWalls(placeBy(player, player.yaw, member.place)));
          member.boarding = 0;
        }

        if (member.aboard && !ride) {
          // Out they get, beside where the player is now.
          stow(member);
          member.aboard = false;
          Object.assign(actor, clearOfWalls(placeBy(player, player.yaw, [member.place[0] * 1.6, member.place[1]])));
        }
        if (!member.aboard && ride) {
          const door = sideDoor(player.footprint, actor.x, actor.z);
          member.boarding += dt;
          actor.speed = suits ? SUIT_RUN : 9;
          actor.walk(door, boxes, dt, wallsBetween(actor, door));
          if (Math.hypot(actor.x - door.x, actor.z - door.z) < 1.2 || member.boarding > BOARDING
            || speed > 6 || player.y > 3) member.aboard = true;
        }
        actor.figure.visible = !member.aboard;

        if (member.aboard) {
          member.boarding = 0;
          Object.assign(actor, { x: player.x, z: player.z, moving: 0 });
          if (member.car !== vehicle) stow(member);
          if (threats.burning && !suits) shotAt(member, threats.burning * dt, player);
          if (member.hp <= 0) continue;
          // From their own window, only at those who are shooting, and only on
          // that side: nobody aims across the inside of the car.
          const f = player.footprint;
          const from = { x: player.x + f.hz * member.side, y: player.y + 0.9, z: player.z - f.hx * member.side };
          const inverse = new THREE.Quaternion().copy(player.body.rotation()).invert();
          const across = (a) => new THREE.Vector3(a.x - player.x, a.y - player.y, a.z - player.z)
            .applyQuaternion(inverse).x * member.side;
          const target = pick(from, attackers.filter((a) => a.shooting && across(a) > -ROUND_THE_CORNER), player.body);
          if (!target) {
            stow(member);
            continue;
          }
          if (!member.gunner) {
            member.gunner = createGunner(vehicle, { person: member.person, top: member.top, cap: false },
              member.seat, member.side);
            member.car = vehicle;
          }
          const muzzle = member.gunner.aim(new THREE.Vector3(target.x, target.y, target.z), SQUAD_GUN.name);
          shoot(member, muzzle, target, player.body, result, dt);
          exposed.push({
            x: muzzle.x, y: muzzle.y, z: muzzle.z, vx: player.vx, vz: player.vz,
            hurt: (amount) => shotAt(member, amount, player),
          });
          continue;
        }

        // Run down by something fast: not by the player's own.
        if (threats.strikers && !ride) {
          const body = { body: { translation: () => ({ x: actor.x, y: 0.9, z: actor.z }) }, down: false, knock: () => {} };
          let by = null;
          body.knock = (vx, vz) => { by = [vx, vz]; };
          const damage = runDown(body, threats.strikers, threats.overlaps, dt);
          if (by) {
            if (!suits) member.hp = Math.max(0, member.hp - damage);
            actor.hp = member.hp;
            actor.knock(...by);
            continue;
          }
        }
        // In suits, anyone close enough is gone for with bare hands.
        const prey = suits && !ride
          ? attackers.filter((a) => !a.dead && a.y < 3 && Math.hypot(a.x - actor.x, a.z - actor.z) < CHARGE)
            .sort((a, b) => Math.hypot(a.x - actor.x, a.z - actor.z) - Math.hypot(b.x - actor.x, b.z - actor.z))[0]
          : null;
        if (prey) {
          const away = Math.hypot(prey.x - actor.x, prey.z - actor.z);
          actor.equip(null);
          actor.aiming = false;
          actor.speed = SUIT_RUN;
          if (away > PUNCH.reach * 0.8) actor.walk(prey, boxes, dt, wallsBetween(actor, prey));
          else actor.face(prey);
          actor.reaching = member.punch > PUNCH.interval - PUNCH.show;
          if (away <= PUNCH.reach && member.punch === 0) {
            member.punch = PUNCH.interval;
            actor.reaching = true;
            const dir = { x: (prey.x - actor.x) / (away || 1), z: (prey.z - actor.z) / (away || 1) };
            if (prey.hurt(Infinity, dir, SUIT.punch) === 0) {
              result.kills++;
              prey.dead = true;
            }
          }
        } else if (!ride) {
          actor.reaching = false;
          member.boarding = 0;
          // Fallen behind, they make for you; close by, for their usual place.
          const behind = Math.hypot(player.x - actor.x, player.z - actor.z) > 6;
          const goal = behind ? { x: player.x, z: player.z } : clearOfWalls(placeBy(player, player.yaw, member.place));
          const gap = Math.hypot(goal.x - actor.x, goal.z - actor.z);
          if (Math.hypot(player.x - actor.x, player.z - actor.z) > LOST) Object.assign(actor, goal);
          // Faster the further behind they are.
          actor.speed = Math.min(suits ? SUIT_RUN : RUN, Math.max(3, speed + gap * 1.5));
          if (gap > (speed > 0.5 ? 0.2 : 0.8)) actor.walk(goal, boxes, dt, wallsBetween(actor, goal));
          else actor.moving = 0;
        }
        exposed.push({
          x: actor.x, y: 1.1, z: actor.z, vx: 0, vz: 0,
          hurt: (amount) => (suits ? member.hp : wound(member, amount)),
        });
        if (prey) continue;
        const from = { x: actor.x, y: 1.35, z: actor.z };
        const target = ride ? null : pick(from, attackers, player.body);
        actor.equip(target ? SQUAD_GUN.name : null);
        actor.aiming = Boolean(target);
        if (target) {
          actor.yaw = Math.atan2(target.x - actor.x, target.z - actor.z);
          shoot(member, { x: actor.x + Math.sin(actor.yaw) * 0.6, y: 1.35, z: actor.z + Math.cos(actor.yaw) * 0.6 },
            target, player.body, result, dt);
        }
      }
      lost = false;
      const walking = members.filter((m) => m.actor && !m.aboard).map((m) => m.actor);
      // Making room for each other must not push anyone into a wall either.
      separate(walking, [...boxes, ...walking.flatMap((actor) => wallsBetween(actor, actor, 2))],
        player.footprint ? [] : [player]);
      return result;
    },
    draw() {
      for (const member of members) {
        const { actor, tag, gunner } = member;
        if (actor && !member.aboard) {
          actor.draw();
          tag.position.set(actor.x, actor.down ? 0.7 : 2.05 + actor.lift, actor.z);
          tag.visible = true;
        } else if (gunner) {
          // Over whoever is leaning out of the window.
          gunner.head.getWorldPosition(tag.position).y += 0.4;
          tag.visible = true;
        } else tag.visible = false;
      }
    },
  };
}
