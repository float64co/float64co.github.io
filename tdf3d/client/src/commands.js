// Console commands: actions. Settings are in variables.js.
import { SIZE } from './city.js';

const number = (text) => (text?.trim() ? Number(text) : NaN);

export function createCommands({ garage }) {
  const commands = {
    tp: {
      usage: 'tp x z             move to a position',
      run([x, z]) {
        const px = number(x);
        const pz = number(z);
        const limit = SIZE / 2 - 5;
        if (!(Math.abs(px) <= limit && Math.abs(pz) <= limit))
          return `x and z must be within ±${limit}`;
        const { body } = garage.current;
        body.setTranslation({ x: px, y: Math.max(body.translation().y, 100), z: pz }, true);
        body.setLinvel({ x: 0, y: 0, z: 0 }, true);
        garage.reset(); // lands on the nearest road
        return `moved to the road nearest ${px} ${pz}`;
      },
    },
    pos: {
      usage: 'pos                show position',
      run() {
        const p = garage.current.body.translation();
        return `x ${p.x.toFixed(1)}  z ${p.z.toFixed(1)}  height ${p.y.toFixed(1)}`;
      },
    },
    reset: {
      usage: 'reset              upright on the nearest road',
      run() {
        garage.reset();
        return 'ok';
      },
    },
  };
  return commands;
}
