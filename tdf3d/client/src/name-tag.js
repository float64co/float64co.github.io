// A name floating over someone's head: white on nothing, always facing the camera.
import * as THREE from 'three';

const SIZE = { width: 512, height: 96 };
export const TAG_HEIGHT = 0.18; // metres tall in the world

// Where there is no canvas to write on, the tag is there but blank.
export function createNameTag(name, canvas = globalThis.document?.createElement('canvas')) {
  const context = canvas?.getContext?.('2d');
  // Never hidden: it can be read through walls.
  const material = new THREE.SpriteMaterial({ transparent: true, depthWrite: false, depthTest: false });
  if (context) {
    Object.assign(canvas, SIZE);
    context.font = 'bold 56px sans-serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.lineWidth = 10;
    context.lineJoin = 'round';
    context.strokeStyle = 'rgba(0, 0, 0, 0.8)'; // so that it reads against the sky
    context.strokeText(name, SIZE.width / 2, SIZE.height / 2);
    context.fillStyle = '#fff';
    context.fillText(name, SIZE.width / 2, SIZE.height / 2);
    material.map = new THREE.CanvasTexture(canvas);
    material.map.colorSpace = THREE.SRGBColorSpace;
  } else material.opacity = 0;
  const sprite = new THREE.Sprite(material);
  sprite.name = name;
  sprite.scale.set(TAG_HEIGHT * (SIZE.width / SIZE.height), TAG_HEIGHT, 1);
  sprite.visible = false;
  sprite.renderOrder = 3;
  return sprite;
}
