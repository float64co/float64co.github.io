import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { headGeometry, HEAD_SIZE, NECK, personOf, variantOf, VARIANTS } from '../src/head.js';
import { TORSO, HEAD } from '../src/people.js';

test('a head with a neck that meets the shoulders', () => {
  for (const detail of [true, false]) {
    const geometry = headGeometry({ detail });
    geometry.computeBoundingBox();
    const { min, max } = geometry.boundingBox;
    // The neck reaches down into the top of the body: no floating head.
    assert.ok(HEAD.y + min.y <= TORSO.y + TORSO.h / 2, `gap under the neck: ${HEAD.y + min.y}`);
    // Taller than it is wide, as heads are, with no bigger crown than before.
    assert.ok(HEAD_SIZE.h > HEAD_SIZE.w && max.y < HEAD.r);
    assert.ok(min.y <= NECK.bottom + 1e-6);
    // The back and front differ: there is a face towards +z.
    if (detail) assert.ok(max.z > -min.z, 'no nose');
    const { count } = geometry.attributes.position;
    assert.equal(geometry.attributes.color.count, count);
    assert.equal(geometry.attributes.normal.count, count);
  }
});

test('distant heads are much cheaper', () => {
  const triangles = (g) => g.attributes.position.count / 3;
  const near = triangles(headGeometry());
  const far = triangles(headGeometry({ detail: false }));
  console.log({ near, far, before: triangles(new THREE.SphereGeometry(HEAD.r, 8, 6).toNonIndexed()) });
  assert.ok(far <= 80, `${far}`);
});

test('the crowd is a mix of different people', () => {
  const shapes = new Set();
  const skins = new Set();
  const hair = new Set();
  for (let v = 0; v < VARIANTS; v++) {
    const person = personOf(v);
    skins.add(person.skin);
    hair.add(person.hair);
    const geometry = headGeometry(person);
    shapes.add(`${geometry.attributes.position.count} ${[...geometry.attributes.position.array]
      .reduce((a, b) => a + b, 0).toFixed(3)}`);
    // Whatever the hair, still on the neck and within the old head's width.
    geometry.computeBoundingBox();
    assert.ok(geometry.boundingBox.max.x < HEAD.r && geometry.boundingBox.max.y < HEAD.r);
    assert.ok(HEAD.y + geometry.boundingBox.min.y <= TORSO.y + TORSO.h / 2);
    assert.ok(headGeometry({ ...person, detail: false }).attributes.position.count / 3 <= 90);
  }
  assert.equal(shapes.size, VARIANTS);
  assert.ok(skins.size >= 8 && hair.size >= 8);
  const counts = new Array(VARIANTS).fill(0);
  for (let id = 0; id < 5000; id++) counts[variantOf(id)]++;
  assert.ok(Math.min(...counts) > 350, `${counts}`);
  assert.equal(variantOf(1234), variantOf(1234));
});

test('police officers are as mixed as the crowd, hands matching faces', async () => {
  const { createOfficer } = await import('../src/officers.js');
  const RAPIER = (await import('@dimforge/rapier3d-compat')).default;
  await RAPIER.init();
  const physics = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
  const scene = new THREE.Scene();
  const officers = Array.from({ length: 30 }, () => createOfficer(scene, physics, { x: 0, z: 0 }));
  assert.ok(new Set(officers.map((o) => o.variant)).size >= 7);
  for (const officer of officers) {
    const skin = new THREE.Color(personOf(officer.variant).skin).getHex();
    assert.ok(officer.figure.children.some((m) => m.material.color.getHex() === skin), 'forearms do not match');
  }
  for (const officer of officers) officer.dispose();
});
