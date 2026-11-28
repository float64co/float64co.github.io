import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import { createTraffic } from '../src/traffic.js';
import { isRobot, robotGeometries, SHARE } from '../src/robot.js';
import { LEG, THIGH, TORSO, HEAD } from '../src/people.js';

await RAPIER.init();

test('five in every thirty are robots, always the same ones', () => {
  let robots = 0;
  for (let id = 0; id < 30000; id++) robots += isRobot(id) ? 1 : 0;
  assert.ok(Math.abs(robots / 30000 - 5 / 30) < 0.01, `${robots}`);
  assert.deepEqual(SHARE, { robots: 5, of: 30 });
  assert.equal(isRobot(99), isRobot(99));
});

const triangles = (parts) => Object.values(parts)
  .reduce((sum, g) => sum + g.attributes.position.count / 3, 0);

test('a robot stands on the ground with its head attached, near or far', () => {
  for (const detail of [true, false]) {
    const parts = robotGeometries({ detail });
    for (const g of Object.values(parts)) g.computeBoundingBox();
    const { torso, head, shin } = parts;
    assert.ok(Math.abs(LEG.y - THIGH + shin.boundingBox.min.y) < 0.03, 'feet off the ground');
    assert.ok(HEAD.y + head.boundingBox.min.y <= TORSO.y + torso.boundingBox.max.y + 1e-6, 'floating head');
    // The face is at the front of the head, and the light in front of the glass.
    assert.ok(parts.eyes.boundingBox.max.z >= head.boundingBox.max.z);
  }
  // There are hundreds of them: far ones must be cheap.
  assert.ok(triangles(robotGeometries({ detail: false })) < 250);
  assert.ok(triangles(robotGeometries()) > 1500);
});

test('every person on foot is drawn once, as one sort or the other, up or down', () => {
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  physics.createCollider(RAPIER.ColliderDesc.cuboid(500, 1, 500).setTranslation(0, -1, 0));
  const traffic = createTraffic(new THREE.Scene(), physics);
  traffic.set('cars', false);
  traffic.sim.step(1 / 60, { x: 0, z: 0 });
  const { humans, robots, farRobots } = traffic.bodies;
  const check = () => {
    traffic.draw({ x: 0, y: 0, z: 0 });
    const everyone = [...traffic.sim.peds, ...traffic.sim.fallen.map((f) => f.ped)];
    const steel = everyone.filter((p) => isRobot(p.id)).length;
    for (const part of ['torsos', 'heads', 'eyes'])
      assert.equal(robots[part].count + farRobots[part].count, steel, part);
    for (const part of ['thighs', 'shins'])
      assert.equal(robots[part].count + farRobots[part].count, steel * 2);
    assert.ok(robots.torsos.count > 0 && farRobots.torsos.count > robots.torsos.count);
    assert.equal(humans.torsos.count, everyone.length - steel);
    assert.equal(humans.forearms.count, (everyone.length - steel) * 2);
    return { steel, all: everyone.length };
  };
  const standing = check();
  assert.ok(Math.abs(standing.steel / standing.all - 1 / 6) < 0.03, `${standing.steel} of ${standing.all}`);
  // Shoot one robot and one person down.
  const down = [traffic.sim.peds.find((p) => isRobot(p.id)), traffic.sim.peds.find((p) => !isRobot(p.id))];
  for (const ped of down) traffic.sim.damage(ped, 500);
  traffic.sim.step(1 / 60, { x: 0, z: 0 });
  assert.equal(check().all, standing.all);
});
